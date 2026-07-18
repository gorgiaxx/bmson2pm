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
EDITION_PATTERN = re.compile(r"^edt([0-9]{3})([0-9]{3})$")
MAX_MIRROR_PACKAGES = 500


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


def parse_update_list(
    payload: bytes,
    *,
    allow_add: bool = False,
) -> tuple[int, list[UpdateOperation]]:
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
        action_token = fields[0] if fields else ""
        action = action_token.casefold()
        allowed_actions = {"r", "d"} | ({"a"} if allow_add else set())
        if action not in allowed_actions:
            allowed = "r、A 或 d" if allow_add else "r 或 d"
            raise Pm3OtaAuditError(
                f"update.lst 第 {line_number} 行操作必须是 {allowed}"
            )
        if action == "d" and action_token != "d":
            raise Pm3OtaAuditError(
                f"update.lst 第 {line_number} 行删除操作必须使用小写 d，以兼容 pcli"
            )
        if len(fields) < 2 or not fields[1]:
            raise Pm3OtaAuditError(f"update.lst 第 {line_number} 行缺少目标路径")
        path = _safe_relative_path(fields[1], line_number)
        lowered = path.casefold()
        if lowered in paths:
            raise Pm3OtaAuditError(f"update.lst 重复操作同一路径：{path}")
        paths.add(lowered)
        expected_md5: str | None = None
        if action in {"r", "a"}:
            if len(fields) != 3 or not MD5_PATTERN.fullmatch(fields[2]):
                raise Pm3OtaAuditError(
                    f"update.lst 第 {line_number} 行复制操作需要 32 位 MD5"
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
            and path.name.casefold() not in {"report.json", ".ds_store"}
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

    def audit_mirror(
        self,
        *,
        generation: int | None = None,
        installed_version: int | None = None,
        installed_edition: int | None = None,
        downloaded_version: int | None = None,
        downloaded_edition: int | None = None,
        verify_payloads: bool = False,
    ) -> dict[str, Any]:
        machine_cfg = self._read_version_config("machine.cfg", "Machine")
        update_cfg = self._read_version_config("update.cfg", "Update")
        generation = self._resolved_value(
            generation, machine_cfg, "generation", "generation"
        )
        installed_version = self._resolved_value(
            installed_version, machine_cfg, "version", "已安装 version"
        )
        installed_edition = self._resolved_value(
            installed_edition, machine_cfg, "edition", "已安装 edition"
        )
        downloaded_version = self._resolved_value(
            downloaded_version, update_cfg, "version", "目标 version"
        )
        downloaded_edition = self._resolved_value(
            downloaded_edition, update_cfg, "edition", "目标 edition"
        )
        watermarks = (
            generation,
            installed_version,
            installed_edition,
            downloaded_version,
            downloaded_edition,
        )
        if any(value < 0 or value > 999 for value in watermarks):
            raise Pm3OtaAuditError("generation、version 与 edition 必须位于 0..999")

        patch_name = self._patch_name(generation)
        try:
            mirror_root = self.workspace.resolve("mirror", "", expect="directory")
            patch_root = self.workspace.resolve(
                "mirror", patch_name, expect="directory"
            )
        except Pm3WorkspaceError as exc:
            raise Pm3OtaAuditError(f"本地 FTP 镜像不可用：{exc}") from exc

        try:
            entries = sorted(patch_root.iterdir(), key=lambda path: path.name.casefold())
        except OSError as exc:
            raise Pm3OtaAuditError(f"无法读取 FTP 镜像目录：{exc}") from exc
        package_dirs = [
            path for path in entries
            if path.is_dir()
            and (VERSION_PATTERN.fullmatch(path.name) or EDITION_PATTERN.fullmatch(path.name))
        ]
        unsafe_package_dirs = [path.name for path in package_dirs if path.is_symlink()]
        if unsafe_package_dirs:
            raise Pm3OtaAuditError(
                "FTP 镜像补丁目录不能是符号链接：" + ", ".join(unsafe_package_dirs)
            )
        if len(package_dirs) > MAX_MIRROR_PACKAGES:
            raise Pm3OtaAuditError("FTP 镜像补丁目录超过 500 个审计上限")
        if not package_dirs:
            raise Pm3OtaAuditError(f"{patch_name} 没有 verNNN 或 edtNNNNNN 补丁")

        packages = [
            self._audit_mirror_package(path, verify_payloads=verify_payloads)
            for path in package_dirs
        ]
        version_packages = {
            package["version"]: package
            for package in packages if package["kind"] == "version"
        }
        edition_packages = {
            (package["version"], package["edition"]): package
            for package in packages if package["kind"] == "edition"
        }
        errors = [
            f"{package['name']}：{message}"
            for package in packages for message in package["errors"]
        ]
        warnings = [
            f"{package['name']}：{message}"
            for package in packages for message in package["warnings"]
        ]

        edition_chains: list[dict[str, Any]] = []
        cumulative_breaks = 0
        edition_versions = sorted({version for version, _ in edition_packages})
        for version in edition_versions:
            chain = sorted(
                (
                    package for (package_version, _), package in edition_packages.items()
                    if package_version == version
                ),
                key=lambda package: package["edition"],
            )
            breaks: list[dict[str, Any]] = []
            for previous, current in zip(chain, chain[1:]):
                missing_paths = sorted(
                    set(previous["operation_paths"]) - set(current["operation_paths"])
                )
                current["cumulative"] = not missing_paths
                if missing_paths:
                    cumulative_breaks += 1
                    detail = {
                        "previous": previous["name"],
                        "current": current["name"],
                        "missing_count": len(missing_paths),
                        "missing_paths": missing_paths[:100],
                    }
                    breaks.append(detail)
                    errors.append(
                        f"{current['name']} 不是累计 edition，缺少 {len(missing_paths)} 个前版路径"
                    )
            edition_chains.append({
                "version": version,
                "editions": [package["edition"] for package in chain],
                "cumulative": not breaks,
                "breaks": breaks,
            })

        plan_errors: list[str] = []
        version_steps: list[str] = []
        edition_step: str | None = None
        missing_versions: list[int] = []
        if downloaded_version < installed_version:
            plan_errors.append("目标 version 低于已安装 version，PowerOn 不支持降级")
        else:
            for version in range(installed_version + 1, downloaded_version + 1):
                package = version_packages.get(version)
                if package is None:
                    missing_versions.append(version)
                else:
                    version_steps.append(package["name"])
                    package["planned"] = True
            base_edition = installed_edition if downloaded_version == installed_version else 0
            if downloaded_edition < base_edition:
                plan_errors.append("目标 edition 低于已安装 edition，PowerOn 不支持降级")
            elif downloaded_edition > base_edition:
                package = edition_packages.get((downloaded_version, downloaded_edition))
                if package is None:
                    plan_errors.append(
                        f"缺少目标 edition：edt{downloaded_version:03d}{downloaded_edition:03d}"
                    )
                else:
                    edition_step = package["name"]
                    package["planned"] = True
        if missing_versions:
            plan_errors.append(
                "缺少连续 version：" + ", ".join(
                    f"ver{version:03d}" for version in missing_versions
                )
            )
        errors.extend(plan_errors)

        version_numbers = sorted(version_packages)
        catalog_gaps = [
            version for version in range(version_numbers[0], version_numbers[-1] + 1)
            if version not in version_packages
        ] if version_numbers else []
        unknown_dirs = [
            path.name for path in entries
            if path.is_dir()
            and not VERSION_PATTERN.fullmatch(path.name)
            and not EDITION_PATTERN.fullmatch(path.name)
        ]
        if unknown_dirs:
            warnings.append(
                f"{patch_name} 有 {len(unknown_dirs)} 个非补丁子目录"
            )
        if catalog_gaps:
            warnings.append(
                "镜像 version 编号存在空档：" + ", ".join(
                    f"ver{version:03d}" for version in catalog_gaps
                )
            )
        if not verify_payloads:
            warnings.append("当前只检查 payload 存在性，尚未计算全部 MD5")
        warnings.extend([
            "镜像审计只读取本地目录，不连接 FTP、NetPatch 或 PM3 主机",
            "升级计划仅模拟 PowerOn 选择顺序，不修改 machine.cfg 或 update.cfg",
        ])

        counts = {
            "packages": len(packages),
            "versions": len(version_packages),
            "editions": len(edition_packages),
            "operations": sum(package["operation_count"] for package in packages),
            "replace": sum(package["counts"]["replace"] for package in packages),
            "add": sum(package["counts"]["add"] for package in packages),
            "delete": sum(package["counts"]["delete"] for package in packages),
            "verified_payloads": sum(
                package["counts"]["verified_payloads"] for package in packages
            ),
            "missing_payloads": sum(
                package["counts"]["missing_payloads"] for package in packages
            ),
            "md5_mismatches": sum(
                package["counts"]["md5_mismatches"] for package in packages
            ),
            "invalid_packages": sum(not package["valid"] for package in packages),
            "cumulative_breaks": cumulative_breaks,
        }
        public_packages = []
        for package in packages:
            public_package = {
                key: value for key, value in package.items()
                if key != "operation_paths"
            }
            public_packages.append(public_package)
        return {
            "valid": not errors,
            "read_only": True,
            "verify_payloads": verify_payloads,
            "integrity_verified": verify_payloads and not errors,
            "root": str(mirror_root),
            "patch_root": patch_name,
            "generation": generation,
            "installed": {
                "version": installed_version,
                "edition": installed_edition,
            },
            "downloaded": {
                "version": downloaded_version,
                "edition": downloaded_edition,
            },
            "config": {
                "machine": machine_cfg,
                "update": update_cfg,
            },
            "available": {
                "versions": version_numbers,
                "editions": {
                    str(version): sorted(
                        edition for package_version, edition in edition_packages
                        if package_version == version
                    )
                    for version in edition_versions
                },
                "catalog_version_gaps": catalog_gaps,
            },
            "plan": {
                "valid": not plan_errors,
                "version_steps": version_steps,
                "edition_step": edition_step,
                "steps": [*version_steps, *([edition_step] if edition_step else [])],
                "missing_versions": missing_versions,
                "errors": plan_errors,
            },
            "edition_chains": edition_chains,
            "counts": counts,
            "packages": public_packages,
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _audit_mirror_package(
        self,
        directory: Path,
        *,
        verify_payloads: bool,
    ) -> dict[str, Any]:
        version_match = VERSION_PATTERN.fullmatch(directory.name)
        edition_match = EDITION_PATTERN.fullmatch(directory.name)
        kind = "version" if version_match else "edition"
        version = int((version_match or edition_match).group(1))
        edition = int(edition_match.group(2)) if edition_match else 0
        errors: list[str] = []
        warnings: list[str] = []
        update_path = directory / "update.lst"
        if not update_path.is_file():
            return {
                "name": directory.name,
                "kind": kind,
                "version": version,
                "edition": edition,
                "timestamp": None,
                "activation_time": None,
                "due": None,
                "operation_count": 0,
                "counts": {
                    "replace": 0,
                    "add": 0,
                    "delete": 0,
                    "verified_payloads": 0,
                    "missing_payloads": 0,
                    "md5_mismatches": 0,
                    "unmanaged_files": 0,
                },
                "operation_paths": [],
                "mismatches": [],
                "missing_payloads": [],
                "unmanaged_files": [],
                "planned": False,
                "cumulative": None,
                "valid": False,
                "errors": ["缺少 update.lst"],
                "warnings": [],
            }
        if update_path.is_symlink():
            return {
                "name": directory.name,
                "kind": kind,
                "version": version,
                "edition": edition,
                "timestamp": None,
                "activation_time": None,
                "due": None,
                "operation_count": 0,
                "counts": {
                    "replace": 0,
                    "add": 0,
                    "delete": 0,
                    "verified_payloads": 0,
                    "missing_payloads": 0,
                    "md5_mismatches": 0,
                    "unmanaged_files": 0,
                },
                "operation_paths": [],
                "mismatches": [],
                "missing_payloads": [],
                "unmanaged_files": [],
                "planned": False,
                "cumulative": None,
                "valid": False,
                "errors": ["update.lst 不能是符号链接"],
                "warnings": [],
            }
        try:
            timestamp, operations = parse_update_list(
                update_path.read_bytes(), allow_add=True
            )
        except (OSError, Pm3OtaAuditError) as exc:
            return {
                "name": directory.name,
                "kind": kind,
                "version": version,
                "edition": edition,
                "timestamp": None,
                "activation_time": None,
                "due": None,
                "operation_count": 0,
                "counts": {
                    "replace": 0,
                    "add": 0,
                    "delete": 0,
                    "verified_payloads": 0,
                    "missing_payloads": 0,
                    "md5_mismatches": 0,
                    "unmanaged_files": 0,
                },
                "operation_paths": [],
                "mismatches": [],
                "missing_payloads": [],
                "unmanaged_files": [],
                "planned": False,
                "cumulative": None,
                "valid": False,
                "errors": [str(exc)],
                "warnings": [],
            }

        mismatches: list[dict[str, str]] = []
        missing_payloads: list[str] = []
        listed_payloads: set[str] = set()
        verified_payloads = 0
        for operation in operations:
            target = self._package_path(directory, operation.path)
            if operation.action in {"r", "a"}:
                listed_payloads.add(operation.path.casefold())
                if not target.is_file():
                    missing_payloads.append(operation.path)
                    errors.append(f"payload 缺失：{operation.path}")
                    continue
                if verify_payloads:
                    try:
                        actual_md5 = self._md5(target)
                    except OSError as exc:
                        errors.append(f"无法读取 payload：{operation.path}（{exc}）")
                        continue
                    if actual_md5 != operation.expected_md5:
                        mismatches.append({
                            "path": operation.path,
                            "expected_md5": operation.expected_md5 or "",
                            "actual_md5": actual_md5,
                        })
                        errors.append(f"MD5 不匹配：{operation.path}")
                    else:
                        verified_payloads += 1
            elif target.exists() or target.is_symlink():
                warnings.append(f"删除操作同时携带同名 payload：{operation.path}")

        unmanaged_files = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
            and path.name.casefold() not in {"update.lst", ".listing", ".ds_store"}
            and path.relative_to(directory).as_posix().casefold() not in listed_payloads
        )
        if unmanaged_files:
            warnings.append(f"有 {len(unmanaged_files)} 个未列入清单的 payload")
        try:
            activation_time = datetime.fromtimestamp(
                timestamp, tz=timezone.utc
            ).isoformat() if timestamp else None
        except (OverflowError, OSError, ValueError):
            activation_time = None
            warnings.append("清单时间戳无法转换为 UTC 时间")
        return {
            "name": directory.name,
            "kind": kind,
            "version": version,
            "edition": edition,
            "timestamp": timestamp,
            "activation_time": activation_time,
            "due": datetime.now(timezone.utc).timestamp() > timestamp,
            "operation_count": len(operations),
            "counts": {
                "replace": sum(operation.action == "r" for operation in operations),
                "add": sum(operation.action == "a" for operation in operations),
                "delete": sum(operation.action == "d" for operation in operations),
                "verified_payloads": verified_payloads,
                "missing_payloads": len(missing_payloads),
                "md5_mismatches": len(mismatches),
                "unmanaged_files": len(unmanaged_files),
            },
            "operation_paths": [operation.path for operation in operations],
            "mismatches": mismatches[:100],
            "missing_payloads": missing_payloads[:100],
            "unmanaged_files": unmanaged_files[:100],
            "planned": False,
            "cumulative": None,
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
        }

    def _read_version_config(
        self,
        filename: str,
        identifier: str,
    ) -> dict[str, int] | None:
        try:
            path = self.workspace.resolve("rewrite", filename, expect="file")
            text = path.read_text(encoding="ascii")
        except (Pm3WorkspaceError, OSError, UnicodeError):
            return None
        for line in text.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if not fields or fields[0].casefold() != identifier.casefold():
                continue
            try:
                values = [int(field) for field in fields[1:]]
            except ValueError:
                return None
            if identifier.casefold() == "machine" and len(values) >= 4:
                return {
                    "generation": values[0],
                    "version": values[1],
                    "edition": values[2],
                    "area": values[3],
                }
            if identifier.casefold() == "update" and len(values) >= 2:
                return {"version": values[0], "edition": values[1]}
        return None

    @staticmethod
    def _resolved_value(
        override: int | None,
        config: dict[str, int] | None,
        key: str,
        label: str,
    ) -> int:
        if override is not None:
            return override
        if config is None or key not in config:
            raise Pm3OtaAuditError(f"无法从本地配置读取{label}，请显式指定")
        return config[key]

    @staticmethod
    def _patch_name(generation: int) -> str:
        if generation == 1:
            return "patch"
        if generation == 3:
            return "patch_fa"
        return f"patch{generation:03d}"

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
