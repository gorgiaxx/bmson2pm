from __future__ import annotations

import csv
import hashlib
import json
import os
import re
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
from ..storage import ProjectAssetError, ProjectNotFoundError, ProjectStore
from .pm3_resources import PM3_MV_IDS
from .pm3_roms import Pm3RomBuildError, Pm3RomBuilder, Pm3RomSong
from .pm3_workspace import Pm3Workspace, Pm3WorkspaceError


class Pm3ExportError(ValueError):
    pass


PM3_RESOURCE_PROFILES = {"extracted-media-overlay", "squashfs-ota"}
PM3_VERSION_DIRECTORY = re.compile(r"^ver[0-9]{3}$")


class Pm3ExportService:
    """Builds deployable rewrite overlays without accepting arbitrary paths."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        workspace: Pm3Workspace | None = None,
        deploy_roots: dict[str, Path] | None = None,
        project_store: ProjectStore | None = None,
        rom_builder: Pm3RomBuilder | None = None,
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
        self.project_store = project_store
        self.rom_builder = rom_builder or Pm3RomBuilder(self.workspace)
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
        song_id: int | None = None,
        include_song_list: bool = False,
        include_resources: bool = False,
        mv_id: int = 0,
        resource_profile: str = "extracted-media-overlay",
    ) -> dict[str, Any]:
        self._validate_mv_id(mv_id)
        self._validate_resource_profile(resource_profile)
        built = self.adapter.build_with_report(
            project, difficulty, slot=slot, song_id=song_id,
        )
        files = [self._file_info(f"rewrite/script_download/{built.filename}", built.container)]
        resource_package = self._resource_package(
            project, built.song_id, mv_id, resource_profile
        )
        if include_resources and resource_package["complete"]:
            if resource_profile == "squashfs-ota":
                files.extend(self._planned_rom_files(resource_package))
            else:
                resource_artifacts = self._build_resource_artifacts(
                    project, built.song_id, mv_id, resource_package, resource_profile
                )
                files.extend(
                    self._file_info(relative, payload)
                    for relative, payload in resource_artifacts.items()
                )
        song_list_preview = None
        if include_song_list:
            song_list, song_list_plaintext, song_list_encoding, song_warnings = self._build_song_list(
                project, difficulty, built
            )
            files.append(self._file_info("rewrite/script_download/SongList.enc", song_list))
            song_list_preview = {
                "filename": "rewrite/script_download/SongList.enc",
                "encoding": song_list_encoding,
                "text": song_list_plaintext.decode(song_list_encoding, errors="replace"),
            }
        else:
            song_warnings = []
        manifest = self._build_update_list(
            files,
            activation_timestamp=0 if resource_profile == "squashfs-ota" else None,
        )
        files.append(self._file_info("update.lst", manifest))
        chart_preview = self.adapter.inspect(built.container, filename=built.filename)
        chart_preview.update({
            "root_id": "export-preview",
            "path": f"rewrite/script_download/{built.filename}",
        })
        return {
            "valid": not include_resources or resource_package["complete"],
            "filename": built.filename,
            "song_id": built.song_id,
            "slot": built.slot,
            "header": f"0x{built.header:08x}",
            "warnings": list(dict.fromkeys([
                *built.warnings,
                *song_warnings,
                *(resource_package["warnings"] if include_resources else []),
            ])),
            "stats": built.stats,
            "files": files,
            "target_version": project.metadata.version or (
                "PM3 SquashFS offline OTA"
                if resource_profile == "squashfs-ota"
                else "PM3 rewrite overlay"
            ),
            "resources": self._resources(project),
            "include_resources": include_resources,
            "mv_id": mv_id,
            "resource_profile": resource_profile,
            "resource_package": resource_package,
            "previews": {
                "chart": chart_preview,
                "update_list": {
                    "filename": "update.lst",
                    "encoding": "ascii",
                    "text": manifest.decode("ascii"),
                },
                "song_list": song_list_preview,
            },
        }

    def export(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        target_id: str = "staging",
        slot: int | None = None,
        song_id: int | None = None,
        include_song_list: bool = False,
        include_resources: bool = False,
        mv_id: int = 0,
        resource_profile: str = "extracted-media-overlay",
        fail_after_files: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._validate_mv_id(mv_id)
            self._validate_resource_profile(resource_profile)
            target = self._target(target_id)
            built = self.adapter.build_with_report(
                project, difficulty, slot=slot, song_id=song_id,
            )
            artifacts = {f"rewrite/script_download/{built.filename}": built.container}
            warnings = list(built.warnings)
            resource_package = self._resource_package(
                project, built.song_id, mv_id, resource_profile
            )
            if include_resources:
                artifacts.update(self._build_resource_artifacts(
                    project, built.song_id, mv_id, resource_package, resource_profile
                ))
                warnings.extend(resource_package["warnings"])
            if include_song_list:
                song_list, _, _, song_warnings = self._build_song_list(
                    project, difficulty, built
                )
                artifacts["rewrite/script_download/SongList.enc"] = song_list
                warnings.extend(song_warnings)
            artifacts["update.lst"] = self._build_update_list(
                [self._file_info(relative, payload) for relative, payload in artifacts.items()],
                activation_timestamp=0 if resource_profile == "squashfs-ota" else None,
            )
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
                    warnings, include_song_list, include_resources, mv_id,
                    resource_profile, resource_package,
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

    def version_candidates(self) -> list[dict[str, Any]]:
        if self.project_store is None:
            raise Pm3ExportError("PM3 多曲版本需要项目存储")
        candidates: list[dict[str, Any]] = []
        for summary in self.project_store.list():
            try:
                project = self.project_store.get(summary.id)
            except ProjectNotFoundError:
                continue
            background = self._resource_status(
                project, self._audio_resource_ref(project, "background")
            )
            preview = self._resource_status(
                project, self._audio_resource_ref(project, "preview")
            )
            difficulties = [
                {
                    "id": difficulty.value,
                    "label": chart.display_name,
                    "level": chart.level,
                    "notes": len(chart.notes),
                }
                for difficulty, chart in project.difficulties.items()
                if chart.notes
            ]
            candidates.append({
                "project_id": project.id,
                "title": project.metadata.title,
                "artist": project.metadata.artist,
                "song_id": self._project_song_id(project),
                "slot": self._project_slot(project),
                "mv_id": self._project_mv_id(project),
                "difficulties": difficulties,
                "audio_ready": bool(background["available"] and preview["available"]),
                "audio": {"background": background, "preview": preview},
                "updated_at": project.updated_at.isoformat(),
            })
        return candidates

    def preview_version(
        self,
        *,
        version_name: str,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prepared = self._prepare_version(version_name, entries)
        files = [
            self._file_info(relative, payload)
            for relative, payload in prepared["artifacts"].items()
        ]
        files.extend({
            "path": path,
            "size": 0,
            "md5": "PENDING",
            "sha256": "PENDING",
            "pending": True,
        } for path in prepared["rom"]["files"])
        update_list = self._build_update_list(files, activation_timestamp=0)
        files.append(self._file_info("update.lst", update_list))
        return {
            "valid": prepared["complete"],
            "version_name": version_name,
            "songs": prepared["songs"],
            "stats": prepared["stats"],
            "rom": prepared["rom"],
            "files": [
                {**item, "path": f"{version_name}/{item['path']}"}
                for item in files
            ],
            "warnings": prepared["warnings"],
            "previews": {
                "update_list": {
                    "filename": f"{version_name}/update.lst",
                    "encoding": "ascii",
                    "text": update_list.decode("ascii"),
                },
                "song_list": {
                    "filename": f"{version_name}/rewrite/script_download/SongList.enc",
                    "encoding": prepared["song_list_encoding"],
                    "text": prepared["song_list_plaintext"].decode(
                        prepared["song_list_encoding"], errors="replace"
                    ),
                },
            },
        }

    def export_version(
        self,
        *,
        version_name: str,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            prepared = self._prepare_version(version_name, entries)
            if not prepared["complete"]:
                missing = [
                    str(song["song_id"])
                    for song in prepared["songs"]
                    if not song["audio_ready"]
                ]
                detail = f"曲目 {', '.join(missing)} 缺少完整音频" if missing else "ROM 构建环境不完整"
                raise Pm3ExportError(f"PM3 多曲版本无法构建：{detail}")
            rom_songs = [
                Pm3RomSong(
                    song_id=song["song_id"],
                    mv_id=song["mv_id"],
                    background=self._read_resource(
                        song["project"], self._audio_resource_ref(song["project"], "background")
                    ),
                    preview=self._read_resource(
                        song["project"], self._audio_resource_ref(song["project"], "preview")
                    ),
                )
                for song in prepared["resource_songs"]
            ]
            try:
                rom_artifacts = self.rom_builder.build_many(rom_songs)
            except Pm3RomBuildError as exc:
                raise Pm3ExportError(str(exc)) from exc
            artifacts = {**prepared["artifacts"], **rom_artifacts}
            artifacts["update.lst"] = self._build_update_list(
                [self._file_info(relative, payload) for relative, payload in artifacts.items()],
                activation_timestamp=0,
            )
            export_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-version-{uuid4().hex[:8]}"
            temporary = self.exports_root / f".{export_id}.tmp"
            final = self.exports_root / export_id
            if temporary.exists():
                shutil.rmtree(temporary)
            version_root = temporary / version_name
            version_root.mkdir(parents=True)
            try:
                for relative, payload in artifacts.items():
                    destination = self._safe_path(version_root, relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                report = {
                    "export_id": export_id,
                    "kind": "pm3-version",
                    "status": "staged",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "filename": version_name,
                    "version_name": version_name,
                    "target_version": f"PM3 offline {version_name}",
                    "target": {
                        "id": "staging",
                        "label": "安全导出目录",
                        "kind": "staging",
                        "path": str(self.exports_root),
                    },
                    "songs": [
                        {key: value for key, value in song.items() if key != "project"}
                        for song in prepared["songs"]
                    ],
                    "stats": prepared["stats"],
                    "rom": prepared["rom"],
                    "resource_profile": "squashfs-ota",
                    "include_resources": True,
                    "include_song_list": True,
                    "files": [
                        {
                            **self._file_info(relative, payload),
                            "path": f"{version_name}/{relative}",
                        }
                        for relative, payload in artifacts.items()
                    ],
                    "warnings": prepared["warnings"],
                    "rollback_available": False,
                }
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

    def package_directory(self, export_id: str) -> Path:
        self.get_report(export_id)
        return self._export_directory(export_id)

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

    def _prepare_version(
        self,
        version_name: str,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._validate_version_name(version_name)
        if self.project_store is None:
            raise Pm3ExportError("PM3 多曲版本需要项目存储")
        if not entries:
            raise Pm3ExportError("PM3 多曲版本至少需要一张谱面")
        if len(entries) > 50:
            raise Pm3ExportError("PM3 多曲版本一次最多包含 50 张谱面")

        charts: list[tuple[SongProject, DifficultyId, Pm3BuildResult]] = []
        chart_summaries: list[dict[str, Any]] = []
        resource_by_song: dict[int, dict[str, Any]] = {}
        filenames: set[str] = set()
        warnings: list[str] = []
        artifacts: dict[str, bytes] = {}

        for raw in entries:
            project_id = str(raw.get("project_id", ""))
            try:
                project = self.project_store.get(project_id)
            except ProjectNotFoundError as exc:
                raise Pm3ExportError(f"多曲版本项目不存在：{project_id}") from exc
            raw_difficulty = raw.get("difficulty")
            try:
                difficulty = (
                    raw_difficulty
                    if isinstance(raw_difficulty, DifficultyId)
                    else DifficultyId(str(raw_difficulty or ""))
                )
            except ValueError as exc:
                raise Pm3ExportError(f"{project.metadata.title} 的难度无效") from exc
            try:
                song_id = int(raw.get("song_id"))
                slot = int(raw.get("slot", 0))
                mv_id = int(raw.get("mv_id", 0))
            except (TypeError, ValueError) as exc:
                raise Pm3ExportError(f"{project.metadata.title} 的 PM3 参数无效") from exc
            if song_id < 0 or song_id > 999:
                raise Pm3ExportError("PM3 曲目序号必须在 0..999")
            if slot < 0 or slot > 9:
                raise Pm3ExportError("PM3 Key slot 必须在 0..9")
            self._validate_mv_id(mv_id)
            built = self.adapter.build_with_report(
                project, difficulty, slot=slot, song_id=song_id,
            )
            relative = f"rewrite/script_download/{built.filename}"
            lowered = relative.lower()
            if lowered in filenames:
                raise Pm3ExportError(f"多曲版本包含重复谱面：{built.filename}")
            filenames.add(lowered)
            artifacts[relative] = built.container
            charts.append((project, difficulty, built))
            warnings.extend(built.warnings)

            resource_song = resource_by_song.get(song_id)
            if resource_song is None:
                package = self._resource_package(
                    project, song_id, mv_id, "squashfs-ota"
                )
                resource_song = {
                    "song_id": song_id,
                    "project_id": project.id,
                    "title": project.metadata.title,
                    "artist": project.metadata.artist,
                    "mv_id": mv_id,
                    "audio_ready": bool(
                        package["audio"]["background"]["available"]
                        and package["audio"]["preview"]["available"]
                    ),
                    "project": project,
                    "package": package,
                    "charts": [],
                }
                resource_by_song[song_id] = resource_song
            elif resource_song["project_id"] != project.id or resource_song["mv_id"] != mv_id:
                raise Pm3ExportError(
                    f"曲目序号 {song_id} 被不同项目或 MV 重复使用"
                )
            chart_summary = {
                "project_id": project.id,
                "song_id": song_id,
                "title": project.metadata.title,
                "artist": project.metadata.artist,
                "difficulty": difficulty.value,
                "difficulty_label": project.difficulties[difficulty].display_name,
                "level": project.difficulties[difficulty].level,
                "slot": built.slot,
                "mv_id": mv_id,
                "filename": built.filename,
                "note_objects": built.stats["note_objects"],
                "event_count": built.stats["event_count"],
            }
            chart_summaries.append(chart_summary)
            resource_song["charts"].append(chart_summary)

        resource_songs = list(resource_by_song.values())
        try:
            rom = self.rom_builder.inspect_many([
                song["song_id"] for song in resource_songs
            ])
        except Pm3RomBuildError as exc:
            raise Pm3ExportError(str(exc)) from exc
        song_list, song_list_plaintext, song_list_encoding, song_warnings = (
            self._build_song_list_many(charts)
        )
        artifacts["rewrite/script_download/SongList.enc"] = song_list
        warnings.extend(song_warnings)
        for song in resource_songs:
            package_warnings = song["package"]["warnings"]
            warnings.extend(
                warning for warning in package_warnings
                if warning.startswith("尚未准备")
            )
        if not rom["available"]:
            warnings.append(f"离线 ROM 构建环境不完整：{'、'.join(rom['missing'])}")
        warnings.extend([
            "多曲版本从同一只读原版基线一次性重建共享 ROM，不会互相覆盖歌曲资源",
            f"{version_name} 仅生成与 OTA 镜像同形的归档目录，不会连接 FTP 或修改 update.cfg",
            "ROM 会逐曲回读校验；尚未经过 PM3 真机动态验证",
        ])
        songs = [
            {key: value for key, value in song.items() if key not in {"project", "package"}}
            for song in resource_songs
        ]
        complete = bool(
            rom["available"] and all(song["audio_ready"] for song in resource_songs)
        )
        return {
            "complete": complete,
            "artifacts": artifacts,
            "charts": charts,
            "songs": songs,
            "resource_songs": resource_songs,
            "rom": rom,
            "song_list_plaintext": song_list_plaintext,
            "song_list_encoding": song_list_encoding,
            "warnings": list(dict.fromkeys(warnings)),
            "stats": {
                "song_count": len(resource_songs),
                "chart_count": len(charts),
                "bundle_count": len(rom["bundles"]),
                "bundles": rom["bundles"],
                "note_objects": sum(item["note_objects"] for item in chart_summaries),
                "event_count": sum(item["event_count"] for item in chart_summaries),
            },
        }

    @staticmethod
    def _project_song_id(project: SongProject) -> int | None:
        for value, pattern in (
            (project.metadata.game_song_id, r"([0-9]{1,3})"),
            (project.metadata.source_name, r"p([0-9]{1,3})"),
        ):
            if not value:
                continue
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                return min(999, int(match.group(1)))
        return None

    @staticmethod
    def _project_slot(project: SongProject) -> int:
        slot = project.game_specific_data.get("pm3_slot")
        return int(slot) if isinstance(slot, int) and 0 <= slot <= 9 else 0

    @staticmethod
    def _project_mv_id(project: SongProject) -> int:
        value = project.mv_configuration.get("pm3_mv_id")
        return int(value) if isinstance(value, int) and value in PM3_MV_IDS else 0

    @staticmethod
    def _validate_version_name(version_name: str) -> None:
        if PM3_VERSION_DIRECTORY.fullmatch(version_name) is None:
            raise Pm3ExportError("PM3 版本目录必须使用 verNNN 格式")

    def _build_song_list(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        built: Pm3BuildResult,
    ) -> tuple[bytes, bytes, str, list[str]]:
        return self._build_song_list_many([(project, difficulty, built)])

    def _build_song_list_many(
        self,
        charts: list[tuple[SongProject, DifficultyId, Pm3BuildResult]],
    ) -> tuple[bytes, bytes, str, list[str]]:
        if not charts:
            raise Pm3ExportError("SongList 至少需要一张谱面")
        try:
            source = self.workspace.load_song_list_source()
        except Pm3WorkspaceError as exc:
            raise Pm3ExportError(str(exc)) from exc
        encoding = str(source["encoding"]).replace("-replace", "")
        text = source["plaintext"].decode(encoding)
        lines = text.splitlines()
        rows = source["rows"]
        warnings = list(source.get("warnings", []))
        filenames: list[str] = []
        for project, difficulty, built in charts:
            filename = Path(built.filename).stem
            filenames.append(filename)
            matching = next(
                (row for row in rows if row.filename.lower() == filename.lower()), None
            )
            fields = self._song_list_fields(project, difficulty, built, matching)
            output = StringIO()
            csv.writer(output, lineterminator="").writerow(fields)
            new_line = output.getvalue()
            if matching:
                lines[matching.line_number - 1] = new_line
            else:
                insert_at = next(
                    (
                        index
                        for index, line in enumerate(lines)
                        if line.strip().strip("#- \t").casefold()
                        in {"q", "end", "file end"}
                    ),
                    len(lines),
                )
                lines.insert(insert_at, new_line)
            original = project.game_specific_data.get("pm3_total_note")
            current = built.stats["playable_events"]
            if original is not None and original != current:
                warnings.append(
                    f"{filename} 音符数已变化；SongList 的 TotalHit/MaxCombo 语义未完全解明，保留原字段值"
                )
        try:
            plaintext = ("\r\n".join(lines) + "\r\n").encode(encoding)
        except UnicodeEncodeError as exc:
            raise Pm3ExportError(f"SongList 含有 {encoding} 无法编码的字符：{exc}") from exc
        container = encrypt_song_list(plaintext, header=source["header"])
        try:
            verified_rows, verify_warnings, _ = parse_song_list(decrypt_song_list(container).plaintext)
        except (ValueError, UnicodeError) as exc:
            raise Pm3ExportError(f"SongList 加密后验证失败：{exc}") from exc
        verified_names = {row.filename.lower() for row in verified_rows}
        missing = [filename for filename in filenames if filename.lower() not in verified_names]
        if missing:
            raise Pm3ExportError(f"SongList 加密后未找到目标谱面记录：{', '.join(missing)}")
        warnings.extend(verify_warnings)
        return container, plaintext, encoding, warnings

    @staticmethod
    def _song_list_fields(
        project: SongProject,
        difficulty: DifficultyId,
        built: Pm3BuildResult,
        matching: Any,
    ) -> list[str]:
        filename = Path(built.filename).stem
        source_fields = project.game_specific_data.get("pm3_song_info_raw_fields")
        class_id = next(
            (key for key, value in DIFFICULTY_BY_CLASS.items() if value == difficulty), 2
        )
        if isinstance(source_fields, list) and len(source_fields) == 16:
            fields = [str(value) for value in source_fields]
        elif matching:
            fields = list(matching.raw_fields)
        else:
            fields = [
                "12000", "12000", "12000", "0", "0", "0",
                f"{built.song_id:03d}", project.metadata.title,
                project.metadata.artist, str(built.song_id), "0", "0", "0",
                str(class_id), str(project.difficulties[difficulty].level), filename,
            ]
        bpm_values = [
            round(project.timing.initial_bpm * 100),
            *[round(event.bpm * 100) for event in project.timing.bpm_events],
        ]
        fields[0] = str(round(project.timing.initial_bpm * 100))
        fields[1] = str(min(bpm_values))
        fields[2] = str(max(bpm_values))
        fields[3] = str(max(
            (event.tick for event in parse_chart_text(built.plaintext)[0].events),
            default=0,
        ))
        fields[6] = f"{built.song_id:03d}"
        fields[7] = project.metadata.title
        fields[8] = project.metadata.artist
        fields[9] = str(built.song_id)
        fields[13] = str(class_id)
        fields[14] = str(project.difficulties[difficulty].level)
        fields[15] = filename
        return fields

    @staticmethod
    def _build_update_list(
        files: list[dict[str, Any]],
        *,
        activation_timestamp: int | None = None,
    ) -> bytes:
        timestamp = (
            int(datetime.now(timezone.utc).timestamp())
            if activation_timestamp is None
            else max(0, activation_timestamp)
        )
        lines = [str(timestamp)]
        lines.extend(
            f"r, {item['path']}, {item['md5']}"
            for item in files
            if item["path"].startswith(("rewrite/", "ROMS/"))
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
        include_resources: bool,
        mv_id: int,
        resource_profile: str,
        resource_package: dict[str, Any],
    ) -> dict[str, Any]:
        created = datetime.now(timezone.utc).isoformat()
        return {
            "export_id": export_id,
            "status": "staged" if target["kind"] == "staging" else "publishing",
            "created_at": created,
            "project_id": project.id,
            "title": project.metadata.title,
            "difficulty": difficulty.value,
            "target_version": project.metadata.version or (
                "PM3 SquashFS offline OTA"
                if resource_profile == "squashfs-ota"
                else "PM3 rewrite overlay"
            ),
            "target": {key: str(value) for key, value in target.items() if key != "root"} | {"path": str(target["root"])},
            "filename": built.filename,
            "song_id": built.song_id,
            "slot": built.slot,
            "header": f"0x{built.header:08x}",
            "include_song_list": include_song_list,
            "include_resources": include_resources,
            "mv_id": mv_id,
            "resource_profile": resource_profile,
            "resource_package": resource_package,
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

    def _resource_package(
        self,
        project: SongProject,
        song_id: int,
        mv_id: int,
        resource_profile: str,
    ) -> dict[str, Any]:
        background_ref = self._audio_resource_ref(project, "background")
        preview_ref = self._audio_resource_ref(project, "preview")
        background = self._resource_status(project, background_ref)
        preview = self._resource_status(project, preview_ref)
        warnings = []
        if not background["available"]:
            warnings.append("尚未准备 PM3 主音乐；需要 Ogg Vorbis 44.1 kHz 双声道资源")
        if not preview["available"]:
            warnings.append("尚未准备 PM3 选歌试听；需要 PCM S16LE WAV 44.1 kHz 双声道资源")
        rom = None
        if resource_profile == "squashfs-ota":
            rom = self.rom_builder.inspect(song_id)
            if not rom["available"]:
                warnings.append(f"离线 ROM 构建环境不完整：{'、'.join(rom['missing'])}")
            warnings.extend([
                "离线包按 PowerOn update.lst 规则生成，但不会连接 FTP、修改版本配置或写入主机",
                "ROM 已做 SquashFS 回读校验；尚未经过 PM3 真机动态验证",
                "每个包以只读原版 ROM 为基线；多个独立自制歌曲包需要合并后重新构建",
            ])
        else:
            warnings.extend([
                "音频产物是 extracted-media overlay，不是原机 SquashFS ROM OTA 补丁",
                f"MV {mv_id} 仅生成 stage.lua 合并片段，尚未重建 lua_script.rom",
            ])
        audio_config = self._pm3_audio_config(project)
        return {
            "profile": resource_profile,
            "complete": bool(
                background["available"]
                and preview["available"]
                and (rom is None or rom["available"])
            ),
            "song_id": song_id,
            "audio": {
                "source_name": audio_config.get("source_name"),
                "duration": audio_config.get("duration"),
                "preview_start": audio_config.get("preview_start", project.metadata.preview_time),
                "preview_duration": audio_config.get("preview_duration"),
                "background": {
                    **background,
                    "output_path": f"media/sound/BG/BG_{song_id:03d}.ogg",
                },
                "preview": {
                    **preview,
                    "output_path": f"media/sound/preview/p{song_id:03d}.wav",
                },
            },
            "mv": {
                "id": mv_id,
                "available": mv_id in PM3_MV_IDS,
                "mapping": f"StageConfig.MV[{song_id}] = {mv_id}",
                "requires_lua_rom_rebuild": resource_profile != "squashfs-ota",
            },
            "rom": rom,
            "warnings": warnings,
        }

    def _build_resource_artifacts(
        self,
        project: SongProject,
        song_id: int,
        mv_id: int,
        package: dict[str, Any],
        resource_profile: str,
    ) -> dict[str, bytes]:
        if not package["complete"]:
            missing = [
                label for label, key in (("主音乐", "background"), ("选歌试听", "preview"))
                if not package["audio"][key]["available"]
            ]
            rom = package.get("rom")
            if isinstance(rom, dict) and not rom.get("available"):
                missing.append("ROM 构建环境")
            raise Pm3ExportError(f"PM3 完整歌曲资源不齐：{'、'.join(missing)}")
        background_ref = self._audio_resource_ref(project, "background")
        preview_ref = self._audio_resource_ref(project, "preview")
        background = self._read_resource(project, background_ref)
        preview = self._read_resource(project, preview_ref)
        if resource_profile == "squashfs-ota":
            try:
                artifacts = self.rom_builder.build(
                    song_id=song_id,
                    mv_id=mv_id,
                    background=background,
                    preview=preview,
                )
            except Pm3RomBuildError as exc:
                raise Pm3ExportError(str(exc)) from exc
            manifest = self._resource_manifest(
                package, artifacts, song_id=song_id, mv_id=mv_id,
                snippet_path=None,
            )
            artifacts["package/pm3-song.json"] = manifest
            return artifacts
        artifacts = {
            f"media/sound/BG/BG_{song_id:03d}.ogg": background,
            f"media/sound/preview/p{song_id:03d}.wav": preview,
        }
        snippet_path = f"package/stage-mv-{song_id:03d}.lua"
        artifacts[snippet_path] = (
            f"-- Merge into StageConfig.MV in media/lua_script/stage.lua\n"
            f"StageConfig.MV[{song_id}] = {mv_id}\n"
        ).encode("ascii")
        artifacts["package/pm3-song.json"] = self._resource_manifest(
            package, artifacts, song_id=song_id, mv_id=mv_id,
            snippet_path=snippet_path,
        )
        return artifacts

    def _resource_manifest(
        self,
        package: dict[str, Any],
        artifacts: dict[str, bytes],
        *,
        song_id: int,
        mv_id: int,
        snippet_path: str | None,
    ) -> bytes:
        manifest = {
            "schema_version": 1,
            "profile": package["profile"],
            "song_id": song_id,
            "audio": {
                "background": f"media/sound/BG/BG_{song_id:03d}.ogg",
                "preview": f"media/sound/preview/p{song_id:03d}.wav",
                "preview_start": package["audio"]["preview_start"],
                "preview_duration": package["audio"]["preview_duration"],
            },
            "mv": {
                "id": mv_id,
                "mapping": package["mv"]["mapping"],
                "snippet": snippet_path,
                "requires_lua_rom_rebuild": package["mv"]["requires_lua_rom_rebuild"],
            },
            "rom": package.get("rom"),
            "files": [self._file_info(relative, payload) for relative, payload in artifacts.items()],
        }
        return (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

    @staticmethod
    def _planned_rom_files(package: dict[str, Any]) -> list[dict[str, Any]]:
        rom = package.get("rom")
        if not isinstance(rom, dict):
            return []
        files = [
            {
                "path": path,
                "size": 0,
                "md5": "PENDING",
                "sha256": "PENDING",
                "pending": True,
            }
            for path in rom.get("files", [])
        ]
        files.append({
            "path": "package/pm3-song.json",
            "size": 0,
            "md5": "PENDING",
            "sha256": "PENDING",
            "pending": True,
        })
        return files

    def _audio_resource_ref(self, project: SongProject, role: str) -> dict[str, Any] | None:
        audio = self._pm3_audio_config(project)
        configured = audio.get(role)
        if isinstance(configured, dict):
            return configured
        preferred_roles = {
            "background": ("pm3-package-background", "background-audio"),
            "preview": ("pm3-package-preview", "preview-audio"),
        }[role]
        for preferred in preferred_roles:
            for item in reversed(project.source_files):
                if isinstance(item, dict) and item.get("role") == preferred:
                    return item
        return None

    @staticmethod
    def _pm3_audio_config(project: SongProject) -> dict[str, Any]:
        package = project.game_specific_data.get("pm3_package")
        if not isinstance(package, dict):
            return {}
        audio = package.get("audio")
        return audio if isinstance(audio, dict) else {}

    def _resource_status(
        self,
        project: SongProject,
        reference: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if reference is None:
            return {"available": False, "source": None, "size": None}
        try:
            path = self._resolve_resource(project, reference)
        except (Pm3ExportError, Pm3WorkspaceError, ProjectAssetError, ProjectNotFoundError, OSError):
            return {"available": False, "source": self._resource_label(reference), "size": None}
        return {
            "available": True,
            "source": self._resource_label(reference),
            "size": path.stat().st_size,
        }

    def _read_resource(
        self,
        project: SongProject,
        reference: dict[str, Any] | None,
    ) -> bytes:
        if reference is None:
            raise Pm3ExportError("PM3 音频资源引用缺失")
        path = self._resolve_resource(project, reference)
        if path.stat().st_size > 512 * 1024 * 1024:
            raise Pm3ExportError("PM3 单个音频资源不得超过 512 MB")
        return path.read_bytes()

    def _resolve_resource(self, project: SongProject, reference: dict[str, Any]) -> Path:
        project_id = reference.get("project_id")
        relative = reference.get("path")
        if not isinstance(relative, str) or not relative:
            raise Pm3ExportError("PM3 音频资源路径缺失")
        if project_id is not None:
            if project_id != project.id or self.project_store is None:
                raise Pm3ExportError("PM3 项目音频资源不属于当前项目")
            return self.project_store.asset_path(project.id, relative)
        root_id = reference.get("root_id")
        if isinstance(root_id, str):
            return self.workspace.resolve(root_id, relative, expect="file")
        raise Pm3ExportError("PM3 音频资源缺少受信来源")

    @staticmethod
    def _resource_label(reference: dict[str, Any]) -> str:
        if reference.get("project_id"):
            return f"project:{reference.get('path', '')}"
        return f"{reference.get('root_id', '?')}:{reference.get('path', '')}"

    @staticmethod
    def _validate_mv_id(mv_id: int) -> None:
        if mv_id not in PM3_MV_IDS:
            allowed = ", ".join(str(value) for value in PM3_MV_IDS)
            raise Pm3ExportError(f"PM3 MV ID 无效；可用值为 {allowed}")

    @staticmethod
    def _validate_resource_profile(resource_profile: str) -> None:
        if resource_profile not in PM3_RESOURCE_PROFILES:
            raise Pm3ExportError("PM3 资源模式无效")

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
                    compression = (
                        zipfile.ZIP_STORED
                        if path.suffix.casefold() == ".rom"
                        else zipfile.ZIP_DEFLATED
                    )
                    bundle.write(
                        path, path.relative_to(directory).as_posix(),
                        compress_type=compression,
                    )
        os.replace(temporary, archive)
        return archive

    @staticmethod
    def _write_report(directory: Path, report: dict[str, Any]) -> None:
        target = directory / "report.json"
        temporary = directory / ".report.json.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
