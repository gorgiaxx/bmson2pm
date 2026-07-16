from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from ..adapters.pm3_crypto import decrypt_chart, decrypt_song_list
from ..adapters.pm3_parser import parse_chart_text, parse_song_list
from .pm3_workspace import Pm3Workspace, Pm3WorkspaceError


MAX_UPDATE_LIST_BYTES = 2 * 1024 * 1024
MAX_UPDATE_OPERATIONS = 2000
MD5_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
VERSION_PATTERN = re.compile(r"^ver([0-9]{3})$")


class Pm3OtaAuditError(ValueError):
    pass


class Pm3ExportStore(Protocol):
    def get_report(self, export_id: str) -> dict[str, Any]: ...

    def package_directory(self, export_id: str) -> Path: ...


@dataclass(frozen=True)
class UpdateOperation:
    action: str
    path: str
    expected_md5: str | None
    line_number: int


def parse_update_list(payload: bytes) -> tuple[int, list[UpdateOperation]]:
    if len(payload) > MAX_UPDATE_LIST_BYTES:
        raise Pm3OtaAuditError("update.lst 超过 2 MB 审计上限")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Pm3OtaAuditError("update.lst 必须使用 ASCII 编码") from exc
    lines = text.splitlines()
    if not lines:
        raise Pm3OtaAuditError("update.lst 为空")
    try:
        timestamp = int(lines[0].strip())
    except ValueError as exc:
        raise Pm3OtaAuditError("update.lst 第一行必须是 Unix 时间戳") from exc
    if timestamp < 0:
        raise Pm3OtaAuditError("update.lst 时间戳不能为负数")

    operations: list[UpdateOperation] = []
    paths: set[str] = set()
    for line_number, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        action = fields[0].casefold() if fields else ""
        if action not in {"r", "d"}:
            raise Pm3OtaAuditError(f"update.lst 第 {line_number} 行操作必须是 r 或 d")
        if len(fields) < 2 or not fields[1]:
            raise Pm3OtaAuditError(f"update.lst 第 {line_number} 行缺少目标路径")
        path = _safe_relative_path(fields[1], line_number)
        lowered = path.casefold()
        if lowered in paths:
            raise Pm3OtaAuditError(f"update.lst 重复操作同一路径：{path}")
        paths.add(lowered)
        expected_md5: str | None = None
        if action == "r":
            if len(fields) != 3 or not MD5_PATTERN.fullmatch(fields[2]):
                raise Pm3OtaAuditError(
                    f"update.lst 第 {line_number} 行替换操作需要 32 位 MD5"
                )
            expected_md5 = fields[2].upper()
        elif len(fields) > 2 and any(fields[2:]):
            raise Pm3OtaAuditError(f"update.lst 第 {line_number} 行删除操作不应包含 MD5")
        operations.append(UpdateOperation(action, path, expected_md5, line_number))
        if len(operations) > MAX_UPDATE_OPERATIONS:
            raise Pm3OtaAuditError("update.lst 操作数量超过 2000 条审计上限")
    if not operations:
        raise Pm3OtaAuditError("update.lst 没有文件操作")
    return timestamp, operations


def _safe_relative_path(value: str, line_number: int) -> str:
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise Pm3OtaAuditError(f"update.lst 第 {line_number} 行路径越过补丁目录")
    return pure.as_posix()


class Pm3OtaAuditor:
    def __init__(self, exports: Pm3ExportStore, workspace: Pm3Workspace) -> None:
        self.exports = exports
        self.workspace = workspace

    def audit_export(self, export_id: str) -> dict[str, Any]:
        report = self.exports.get_report(export_id)
        package = self.exports.package_directory(export_id)
        patch_root = self._patch_root(package, report)
        update_path = patch_root / "update.lst"
        if not update_path.is_file():
            raise Pm3OtaAuditError("导出包缺少 update.lst")
        try:
            timestamp, operations = parse_update_list(update_path.read_bytes())
        except OSError as exc:
            raise Pm3OtaAuditError(f"无法读取 update.lst：{exc}") from exc

        errors: list[str] = []
        warnings: list[str] = []
        inspected: list[dict[str, Any]] = []
        song_list: dict[str, Any] | None = None
        listed_paths = {operation.path.casefold() for operation in operations}

        for operation in operations:
            baseline = self._baseline_status(operation.path)
            item: dict[str, Any] = {
                "line": operation.line_number,
                "action": operation.action,
                "path": operation.path,
                "expected_md5": operation.expected_md5,
                "actual_md5": None,
                "size": None,
                "verified": operation.action == "d",
                "format": self._format_name(operation.path),
                "format_valid": None,
                "baseline": baseline,
                "effect": self._effect(operation.action, baseline["status"]),
            }
            target = self._package_path(patch_root, operation.path)
            if operation.action == "r":
                if not target.is_file():
                    errors.append(f"清单文件缺失：{operation.path}")
                    item["format_valid"] = False
                else:
                    item["size"] = target.stat().st_size
                    actual_md5 = self._md5(target)
                    item["actual_md5"] = actual_md5
                    item["verified"] = actual_md5 == operation.expected_md5
                    if not item["verified"]:
                        errors.append(f"MD5 不匹配：{operation.path}")
                    format_valid, format_detail = self._validate_format(target, operation.path)
                    item["format_valid"] = format_valid
                    item["format_detail"] = format_detail
                    if not format_valid:
                        errors.append(f"文件格式回读失败：{operation.path}（{format_detail}）")
                    if operation.path.casefold().endswith("songlist.enc"):
                        try:
                            song_list = self._inspect_song_list(target)
                        except (ValueError, UnicodeError, OSError) as exc:
                            errors.append(f"SongList 回读失败：{exc}")
                            song_list = {"valid": False, "error": str(exc), "filenames": []}
            elif target.exists() or target.is_symlink():
                warnings.append(f"删除操作同时携带了同名文件：{operation.path}")
            inspected.append(item)

        unmanaged = sorted(
            path.relative_to(patch_root).as_posix()
            for path in patch_root.rglob("*")
            if path.is_file()
            and path.name != "update.lst"
            and path.relative_to(patch_root).as_posix().casefold() not in listed_paths
        )
        if unmanaged:
            warnings.append(
                f"补丁包含 {len(unmanaged)} 个不由 update.lst 安装的辅助文件"
            )
        if song_list and not song_list.get("valid", False):
            errors.append("SongList 的 FILE END 边界无效")

        counts = {
            "replace": sum(item["effect"] == "replace" for item in inspected),
            "create": sum(item["effect"] == "create" for item in inspected),
            "delete": sum(item["effect"] == "delete" for item in inspected),
            "noop": sum(item["effect"] == "noop" for item in inspected),
            "unknown": sum(item["effect"] == "unknown" for item in inspected),
            "verified": sum(bool(item["verified"]) for item in inspected),
        }
        rom_items = [item for item in inspected if item["path"].casefold().endswith(".rom")]
        warnings.extend([
            "模拟器只读取本地导出包和受信基线，不会复制、删除或挂载文件",
            "基线状态只判断目标是否存在，不执行大文件哈希或真机 PowerOn",
        ])
        return {
            "export_id": export_id,
            "version_name": str(report.get("version_name") or report.get("filename") or export_id),
            "kind": str(report.get("kind") or "pm3-song"),
            "created_at": report.get("created_at"),
            "valid": not errors,
            "read_only": True,
            "activation_timestamp": timestamp,
            "activation_time": (
                datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                if timestamp else None
            ),
            "operation_count": len(inspected),
            "counts": counts,
            "operations": inspected,
            "song_list": song_list,
            "rom": {
                "count": len(rom_items),
                "valid": all(item["format_valid"] is True for item in rom_items),
                "paths": [item["path"] for item in rom_items],
            },
            "unmanaged_files": unmanaged[:100],
            "unmanaged_count": len(unmanaged),
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def simulate_chain(self, export_ids: list[str]) -> dict[str, Any]:
        if not export_ids:
            raise Pm3OtaAuditError("版本链至少需要一个本地导出包")
        if len(export_ids) > 20:
            raise Pm3OtaAuditError("版本链一次最多模拟 20 个导出包")
        if len(set(export_ids)) != len(export_ids):
            raise Pm3OtaAuditError("版本链不能重复选择同一导出包")

        audits = [self.audit_export(export_id) for export_id in export_ids]
        virtual: dict[str, str | None] = {}
        transitions: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        previous_version: int | None = None
        previous_song_list: set[str] | None = None
        song_list_changes: list[dict[str, Any]] = []

        for order, audit in enumerate(audits, start=1):
            match = VERSION_PATTERN.fullmatch(audit["version_name"])
            version = int(match.group(1)) if match else None
            if version is not None and previous_version is not None and version <= previous_version:
                errors.append(
                    f"版本顺序不是严格递增：ver{previous_version:03d} → ver{version:03d}"
                )
            if version is not None:
                previous_version = version
            for operation in audit["operations"]:
                path = operation["path"]
                before = virtual.get(path, "BASELINE")
                after = operation["actual_md5"] if operation["action"] == "r" else None
                if before == after:
                    change = "unchanged"
                elif after is None:
                    change = "deleted"
                elif before is None:
                    change = "restored"
                elif before == "BASELINE":
                    change = operation["effect"]
                else:
                    change = "overridden"
                virtual[path] = after
                transitions.append({
                    "order": order,
                    "export_id": audit["export_id"],
                    "version_name": audit["version_name"],
                    "path": path,
                    "change": change,
                    "before_md5": before if isinstance(before, str) and before != "BASELINE" else None,
                    "after_md5": after,
                })
            current_song_list = set((audit.get("song_list") or {}).get("filenames", []))
            if current_song_list:
                added = sorted(current_song_list - (previous_song_list or set()))
                removed = sorted((previous_song_list or set()) - current_song_list)
                song_list_changes.append({
                    "export_id": audit["export_id"],
                    "version_name": audit["version_name"],
                    "added": added,
                    "removed": removed,
                })
                if previous_song_list is not None and removed:
                    warnings.append(
                        f"{audit['version_name']} 的 SongList 比上一包少 {len(removed)} 条记录"
                    )
                previous_song_list = current_song_list
            if not audit["valid"]:
                errors.append(f"{audit['version_name']} 未通过单包审计")

        return {
            "valid": not errors,
            "read_only": True,
            "export_ids": export_ids,
            "versions": [{
                "export_id": audit["export_id"],
                "version_name": audit["version_name"],
                "valid": audit["valid"],
                "operation_count": audit["operation_count"],
            } for audit in audits],
            "counts": {
                "versions": len(audits),
                "operations": len(transitions),
                "overrides": sum(item["change"] == "overridden" for item in transitions),
                "deletes": sum(item["change"] == "deleted" for item in transitions),
                "final_files": sum(value is not None for value in virtual.values()),
            },
            "transitions": transitions,
            "song_list_changes": song_list_changes,
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys([
                *warnings,
                "版本链仅在内存中模拟清单顺序，不修改 update.cfg 或任何游戏文件",
            ])),
        }

    @staticmethod
    def _patch_root(package: Path, report: dict[str, Any]) -> Path:
        version_name = report.get("version_name")
        if isinstance(version_name, str) and VERSION_PATTERN.fullmatch(version_name):
            candidate = package / version_name
            if candidate.is_dir():
                return candidate
        return package

    @staticmethod
    def _package_path(root: Path, relative: str) -> Path:
        candidate = root.joinpath(*PurePosixPath(relative).parts).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise Pm3OtaAuditError("补丁清单路径越过导出目录")
        return candidate

    def _baseline_status(self, relative: str) -> dict[str, Any]:
        pure = PurePosixPath(relative)
        if pure.parts[0].casefold() == "roms":
            root_id = "game"
            baseline_relative = pure.as_posix()
        elif pure.parts[0].casefold() == "rewrite" and len(pure.parts) > 1:
            root_id = "rewrite"
            baseline_relative = PurePosixPath(*pure.parts[1:]).as_posix()
        else:
            return {"status": "unavailable", "root_id": None, "path": None, "size": None}
        root = self.workspace.roots.get(root_id)
        if root is None or not root.is_dir():
            return {
                "status": "unavailable", "root_id": root_id,
                "path": baseline_relative, "size": None,
            }
        try:
            candidate = self.workspace.resolve(root_id, baseline_relative)
        except Pm3WorkspaceError:
            return {
                "status": "unavailable", "root_id": root_id,
                "path": baseline_relative, "size": None,
            }
        if not candidate.is_file():
            return {
                "status": "missing", "root_id": root_id,
                "path": baseline_relative, "size": None,
            }
        return {
            "status": "present", "root_id": root_id,
            "path": baseline_relative, "size": candidate.stat().st_size,
        }

    @staticmethod
    def _effect(action: str, baseline_status: str) -> str:
        if baseline_status == "unavailable":
            return "unknown"
        if action == "r":
            return "replace" if baseline_status == "present" else "create"
        return "delete" if baseline_status == "present" else "noop"

    @staticmethod
    def _md5(path: Path) -> str:
        digest = hashlib.md5()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    @staticmethod
    def _format_name(relative: str) -> str:
        lowered = relative.casefold()
        if lowered.endswith("songlist.enc"):
            return "song-list"
        if lowered.endswith(".enc"):
            return "chart"
        if lowered.endswith(".rom"):
            return "squashfs"
        return "binary"

    @staticmethod
    def _validate_format(path: Path, relative: str) -> tuple[bool, str]:
        lowered = relative.casefold()
        try:
            if lowered.endswith("songlist.enc"):
                rows, warnings, _ = parse_song_list(decrypt_song_list(path.read_bytes()).plaintext)
                return bool(rows), f"{len(rows)} rows, {len(warnings)} warnings"
            if lowered.endswith(".enc"):
                document, _ = parse_chart_text(decrypt_chart(path.read_bytes()).plaintext)
                return True, f"{len(document.events)} events"
            if lowered.endswith(".rom"):
                with path.open("rb") as stream:
                    return stream.read(4) == b"hsqs", "SquashFS magic"
            return True, "binary"
        except (ValueError, UnicodeError, OSError) as exc:
            return False, str(exc)

    @staticmethod
    def _inspect_song_list(path: Path) -> dict[str, Any]:
        plaintext = decrypt_song_list(path.read_bytes()).plaintext
        rows, warnings, encoding = parse_song_list(plaintext)
        lines = plaintext.decode(encoding.replace("-replace", ""), errors="replace").splitlines()
        end_line = next((
            index for index, line in enumerate(lines, start=1)
            if line.strip().strip("#- \t").casefold() in {"file end", "q", "end"}
        ), None)
        rows_after_end = [row.filename for row in rows if end_line and row.line_number > end_line]
        return {
            "valid": end_line is not None and not rows_after_end,
            "encoding": encoding,
            "row_count": len(rows),
            "file_end_line": end_line,
            "rows_after_end": rows_after_end,
            "filenames": [row.filename for row in rows],
            "warnings": warnings,
        }
