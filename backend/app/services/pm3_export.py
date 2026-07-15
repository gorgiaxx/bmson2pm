from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ..adapters.pm3 import Pm3Adapter, Pm3BuildResult, Pm3FormatError
from ..adapters.pm3_crypto import decrypt_chart, decrypt_song_list, encrypt_song_list
from ..adapters.pm3_parser import DIFFICULTY_BY_CLASS, parse_chart_text, parse_song_list
from ..models import DifficultyId, SongProject
from .pm3_workspace import Pm3Workspace, Pm3WorkspaceError


class Pm3ExportError(ValueError):
    pass


class Pm3ExportService:
    """Builds deployable rewrite overlays without accepting arbitrary paths."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        workspace: Pm3Workspace | None = None,
        deploy_roots: dict[str, Path] | None = None,
    ) -> None:
        configured = os.getenv("BMSON2PM_PM3_EXPORT_ROOT")
        self.root = (Path(configured) if configured else (
            root or Path(__file__).parents[2] / "data" / "pm3-exports"
        )).expanduser().resolve()
        self.exports_root = self.root / "exports"
        self.archives_root = self.root / "archives"
        self.exports_root.mkdir(parents=True, exist_ok=True)
        self.archives_root.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace or Pm3Workspace()
        self.adapter = Pm3Adapter()
        if deploy_roots is None:
            configured_deploy = os.getenv("BMSON2PM_PM3_DEPLOY_ROOT")
            deploy_roots = {"deploy": Path(configured_deploy)} if configured_deploy else {}
        self.deploy_roots = {
            target_id: path.expanduser().resolve() for target_id, path in deploy_roots.items()
        }
        self._lock = threading.RLock()

    def target_descriptors(self) -> list[dict[str, Any]]:
        targets = [{
            "id": "staging",
            "label": "安全导出目录",
            "kind": "staging",
            "path": str(self.exports_root),
            "backup": False,
        }]
        targets.extend({
            "id": target_id,
            "label": f"受控部署目录 · {target_id}",
            "kind": "deployment",
            "path": str(path),
            "backup": True,
        } for target_id, path in sorted(self.deploy_roots.items()))
        return targets

    def preview(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        slot: int | None = None,
        include_song_list: bool = False,
    ) -> dict[str, Any]:
        built = self.adapter.build_with_report(project, difficulty, slot=slot)
        files = [self._file_info(f"rewrite/script_download/{built.filename}", built.container)]
        if include_song_list:
            song_list, song_warnings = self._build_song_list(project, difficulty, built)
            files.append(self._file_info("rewrite/script_download/SongList.enc", song_list))
        else:
            song_warnings = []
        manifest = self._build_update_list(files)
        files.append(self._file_info("update.lst", manifest))
        return {
            "valid": True,
            "filename": built.filename,
            "slot": built.slot,
            "header": f"0x{built.header:08x}",
            "warnings": list(dict.fromkeys([*built.warnings, *song_warnings])),
            "stats": built.stats,
            "files": files,
            "target_version": project.metadata.version or "PM3 rewrite overlay",
            "resources": self._resources(project),
        }

    def export(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        target_id: str = "staging",
        slot: int | None = None,
        include_song_list: bool = False,
        fail_after_files: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            target = self._target(target_id)
            built = self.adapter.build_with_report(project, difficulty, slot=slot)
            artifacts = {f"rewrite/script_download/{built.filename}": built.container}
            warnings = list(built.warnings)
            if include_song_list:
                song_list, song_warnings = self._build_song_list(project, difficulty, built)
                artifacts["rewrite/script_download/SongList.enc"] = song_list
                warnings.extend(song_warnings)
            artifacts["update.lst"] = self._build_update_list([
                self._file_info(relative, payload) for relative, payload in artifacts.items()
            ])
            export_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            temporary = self.exports_root / f".{export_id}.tmp"
            final = self.exports_root / export_id
            if temporary.exists():
                shutil.rmtree(temporary)
            temporary.mkdir(parents=True)
            try:
                for relative, payload in artifacts.items():
                    destination = self._safe_path(temporary, relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                report = self._report(
                    export_id, project, difficulty, target, built, artifacts,
                    warnings, include_song_list,
                )
                if target["kind"] == "deployment":
                    deployment = self._deploy(
                        export_id, target["root"], artifacts,
                        fail_after_files=fail_after_files,
                    )
                    report.update(deployment)
                (temporary / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(final)
                archive = self._archive(export_id, final)
                report["archive"] = str(archive)
                self._write_report(final, report)
                return report
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise

    def list_reports(self) -> list[dict[str, Any]]:
        reports = []
        for path in self.exports_root.glob("*/report.json"):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(reports, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_report(self, export_id: str) -> dict[str, Any]:
        path = self._export_directory(export_id) / "report.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise Pm3ExportError("PM3 导出记录不存在") from exc
        except (OSError, ValueError) as exc:
            raise Pm3ExportError(f"PM3 导出报告损坏：{exc}") from exc

    def archive_path(self, export_id: str) -> Path:
        self.get_report(export_id)
        archive = self.archives_root / f"{export_id}.zip"
        if not archive.is_file():
            raise Pm3ExportError("PM3 导出压缩包不存在")
        return archive

    def rollback(self, export_id: str) -> dict[str, Any]:
        with self._lock:
            report = self.get_report(export_id)
            if not report.get("rollback_available"):
                raise Pm3ExportError("该导出没有可用回滚点")
            target_id = report.get("target", {}).get("id")
            target = self._target(str(target_id))
            if target["kind"] != "deployment":
                raise Pm3ExportError("安全导出目录不需要回滚")
            records = report.get("backup_records", [])
            for record in reversed(records):
                destination = self._safe_path(target["root"], record["path"])
                if record.get("existed"):
                    backup = self._safe_path(target["root"], record["backup_path"])
                    if not backup.is_file():
                        raise Pm3ExportError(f"回滚备份缺失：{record['path']}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, destination)
                elif destination.exists():
                    destination.unlink()
            report["status"] = "rolled_back"
            report["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            report["rollback_available"] = False
            directory = self._export_directory(export_id)
            self._write_report(directory, report)
            self._archive(export_id, directory)
            return report

    def _target(self, target_id: str) -> dict[str, Any]:
        if target_id == "staging":
            return {"id": "staging", "label": "安全导出目录", "kind": "staging", "root": self.exports_root}
        root = self.deploy_roots.get(target_id)
        if root is None:
            raise Pm3ExportError("导出目标不在服务器白名单中")
        return {"id": target_id, "label": f"受控部署目录 · {target_id}", "kind": "deployment", "root": root}

    def _deploy(
        self,
        export_id: str,
        root: Path,
        artifacts: dict[str, bytes],
        *,
        fail_after_files: int | None,
    ) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        backup_root = root / ".bmson2pm-backups" / export_id
        records: list[dict[str, Any]] = []
        installed: list[Path] = []
        try:
            for index, (relative, payload) in enumerate(artifacts.items(), start=1):
                destination = self._safe_path(root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                existed = destination.is_file()
                record: dict[str, Any] = {"path": relative, "existed": existed}
                if existed:
                    backup = self._safe_path(backup_root, relative)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
                    record["backup_path"] = backup.relative_to(root).as_posix()
                temporary = destination.with_name(f".{destination.name}.{export_id}.tmp")
                temporary.write_bytes(payload)
                os.replace(temporary, destination)
                installed.append(destination)
                records.append(record)
                if fail_after_files is not None and index >= fail_after_files:
                    raise OSError("injected PM3 deployment failure")
            for relative, expected in artifacts.items():
                actual = self._safe_path(root, relative).read_bytes()
                if actual != expected:
                    raise Pm3ExportError(f"发布后哈希校验失败：{relative}")
                if relative.lower().endswith(".enc") and not relative.lower().endswith("songlist.enc"):
                    parse_chart_text(decrypt_chart(actual).plaintext)
            return {
                "status": "published",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "rollback_available": True,
                "backup_records": records,
                "backup_directory": str(backup_root),
            }
        except Exception as exc:
            for record in reversed(records):
                destination = self._safe_path(root, record["path"])
                if record["existed"]:
                    backup = self._safe_path(root, record["backup_path"])
                    if backup.is_file():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(backup, destination)
                elif destination.exists():
                    destination.unlink()
            for destination in installed:
                temporary = destination.with_name(f".{destination.name}.{export_id}.tmp")
                temporary.unlink(missing_ok=True)
            raise Pm3ExportError(f"PM3 发布失败，已自动恢复：{exc}") from exc

    def _build_song_list(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        built: Pm3BuildResult,
    ) -> tuple[bytes, list[str]]:
        try:
            source = self.workspace.load_song_list_source()
        except Pm3WorkspaceError as exc:
            raise Pm3ExportError(str(exc)) from exc
        encoding = str(source["encoding"]).replace("-replace", "")
        text = source["plaintext"].decode(encoding)
        lines = text.splitlines()
        filename = Path(built.filename).stem
        rows = source["rows"]
        matching = next((row for row in rows if row.filename.lower() == filename.lower()), None)
        source_fields = project.game_specific_data.get("pm3_song_info_raw_fields")
        if isinstance(source_fields, list) and len(source_fields) == 16:
            fields = [str(value) for value in source_fields]
        elif matching:
            fields = list(matching.raw_fields)
        else:
            class_id = next((key for key, value in DIFFICULTY_BY_CLASS.items() if value == difficulty), 2)
            song_id = Pm3Adapter._numeric_song_id(project.metadata.game_song_id) or 0
            fields = ["12000", "12000", "12000", "0", "0", "0", f"{song_id:03d}", project.metadata.title, project.metadata.artist, str(song_id), "0", "0", "0", str(class_id), str(project.difficulties[difficulty].level), filename]
        bpm_values = [round(project.timing.initial_bpm * 100), *[round(event.bpm * 100) for event in project.timing.bpm_events]]
        fields[0] = str(round(project.timing.initial_bpm * 100))
        fields[1] = str(min(bpm_values))
        fields[2] = str(max(bpm_values))
        fields[3] = str(max((event.tick for event in parse_chart_text(built.plaintext)[0].events), default=0))
        fields[7] = project.metadata.title
        fields[8] = project.metadata.artist
        fields[14] = str(project.difficulties[difficulty].level)
        fields[15] = filename
        output = StringIO()
        writer = csv.writer(output, lineterminator="")
        writer.writerow(fields)
        new_line = output.getvalue()
        if matching:
            lines[matching.line_number - 1] = new_line
        else:
            insert_at = next((index for index, line in enumerate(lines) if line.strip().lower() in {"q", "end"}), len(lines))
            lines.insert(insert_at, new_line)
        try:
            plaintext = ("\r\n".join(lines) + "\r\n").encode(encoding)
        except UnicodeEncodeError as exc:
            raise Pm3ExportError(f"SongList 含有 {encoding} 无法编码的字符：{exc}") from exc
        warnings = list(source.get("warnings", []))
        original = project.game_specific_data.get("pm3_total_note")
        current = built.stats["playable_events"]
        if original is not None and original != current:
            warnings.append("音符数已变化；SongList 的 TotalHit/MaxCombo 语义未完全解明，保留原字段值")
        container = encrypt_song_list(plaintext, header=source["header"])
        try:
            verified_rows, verify_warnings, _ = parse_song_list(decrypt_song_list(container).plaintext)
        except (ValueError, UnicodeError) as exc:
            raise Pm3ExportError(f"SongList 加密后验证失败：{exc}") from exc
        if not any(row.filename.lower() == filename.lower() for row in verified_rows):
            raise Pm3ExportError("SongList 加密后未找到目标谱面记录")
        warnings.extend(verify_warnings)
        return container, warnings

    @staticmethod
    def _build_update_list(files: list[dict[str, Any]]) -> bytes:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        lines = [str(timestamp)]
        lines.extend(
            f"r, {item['path']}, {item['md5']}"
            for item in files if item["path"].startswith("rewrite/")
        )
        return ("\r\n".join(lines) + "\r\n").encode("ascii")

    @staticmethod
    def _file_info(relative: str, payload: bytes) -> dict[str, Any]:
        return {
            "path": relative,
            "size": len(payload),
            "md5": hashlib.md5(payload).hexdigest().upper(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def _report(
        self,
        export_id: str,
        project: SongProject,
        difficulty: DifficultyId,
        target: dict[str, Any],
        built: Pm3BuildResult,
        artifacts: dict[str, bytes],
        warnings: list[str],
        include_song_list: bool,
    ) -> dict[str, Any]:
        created = datetime.now(timezone.utc).isoformat()
        return {
            "export_id": export_id,
            "status": "staged" if target["kind"] == "staging" else "publishing",
            "created_at": created,
            "project_id": project.id,
            "title": project.metadata.title,
            "difficulty": difficulty.value,
            "target_version": project.metadata.version or "PM3 rewrite overlay",
            "target": {key: str(value) for key, value in target.items() if key != "root"} | {"path": str(target["root"])},
            "filename": built.filename,
            "slot": built.slot,
            "header": f"0x{built.header:08x}",
            "include_song_list": include_song_list,
            "files": [self._file_info(relative, payload) for relative, payload in artifacts.items()],
            "resources": self._resources(project),
            "warnings": list(dict.fromkeys(warnings)),
            "stats": built.stats,
            "round_trip": self.adapter.round_trip_project(project, difficulty),
            "rollback_available": False,
        }

    @staticmethod
    def _resources(project: SongProject) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for item in project.source_files:
            if not isinstance(item, dict):
                continue
            key = (item.get("role"), item.get("path"), item.get("filename"))
            if key in seen:
                continue
            seen.add(key)
            result.append({key: value for key, value in item.items() if key != "sha256" or value})
        return result

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise Pm3ExportError("导出文件路径无效")
        candidate = root.joinpath(*pure.parts).resolve()
        resolved_root = root.resolve()
        if not candidate.is_relative_to(resolved_root):
            raise Pm3ExportError("导出文件越过白名单目录")
        return candidate

    def _export_directory(self, export_id: str) -> Path:
        if not export_id or not all(char.isalnum() or char in "-_" for char in export_id):
            raise Pm3ExportError("PM3 导出 ID 无效")
        return self.exports_root / export_id

    def _archive(self, export_id: str, directory: Path) -> Path:
        archive = self.archives_root / f"{export_id}.zip"
        temporary = archive.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(directory).as_posix())
        os.replace(temporary, archive)
        return archive

    @staticmethod
    def _write_report(directory: Path, report: dict[str, Any]) -> None:
        target = directory / "report.json"
        temporary = directory / ".report.json.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
