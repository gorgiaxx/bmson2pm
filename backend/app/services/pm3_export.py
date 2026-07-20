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
from ..adapters.pm3_reservations import (
    PM3_RESERVED_SONG_IDS,
    reserved_slot,
    reserved_ui,
)
from ..models import DifficultyId, SongProject
from ..storage import ProjectAssetError, ProjectNotFoundError, ProjectStore
from .pm3_resources import (
    PM3_CUSTOM_MV_IDS,
    PM3_MV_IDS,
    Pm3ResourceError,
    convert_pm3_key_sound,
    inspect_pm3_mv_swf,
)
from .pm3_roms import (
    Pm3RomBuildError,
    Pm3RomBuilder,
    Pm3RomKeySound,
    Pm3RomMv,
    Pm3RomSong,
)
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
        music_style: int = 0,
        guest_available: bool = True,
        mv_id: int = 0,
        resource_profile: str = "extracted-media-overlay",
    ) -> dict[str, Any]:
        self._validate_mv_id(mv_id)
        self._validate_resource_profile(resource_profile)
        song_id, slot = self._reserved_assignment(song_id, difficulty, slot)
        built = self.adapter.build_with_report(
            project, difficulty, slot=slot, song_id=song_id,
        )
        files = [self._file_info(f"rewrite/script_download/{built.filename}", built.container)]
        resource_package = self._resource_package(
            project, built.song_id, mv_id, resource_profile, built=built
        )
        if include_resources and resource_package["complete"]:
            if resource_profile == "squashfs-ota":
                files.extend(self._planned_rom_files(resource_package))
            else:
                resource_artifacts = self._build_resource_artifacts(
                    project,
                    built.song_id,
                    mv_id,
                    resource_package,
                    resource_profile,
                    built=built,
                )
                files.extend(
                    self._file_info(relative, payload)
                    for relative, payload in resource_artifacts.items()
                )
        song_list_preview = None
        if include_song_list:
            song_list, song_list_plaintext, song_list_encoding, song_warnings = self._build_song_list(
                project, difficulty, built, guest_available, music_style
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
            "music_style": music_style,
            "guest_available": guest_available,
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
        music_style: int = 0,
        guest_available: bool = True,
        mv_id: int = 0,
        resource_profile: str = "extracted-media-overlay",
        fail_after_files: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._validate_mv_id(mv_id)
            self._validate_resource_profile(resource_profile)
            song_id, slot = self._reserved_assignment(song_id, difficulty, slot)
            target = self._target(target_id)
            built = self.adapter.build_with_report(
                project, difficulty, slot=slot, song_id=song_id,
            )
            artifacts = {f"rewrite/script_download/{built.filename}": built.container}
            warnings = list(built.warnings)
            resource_package = self._resource_package(
                project, built.song_id, mv_id, resource_profile, built=built
            )
            if include_resources:
                artifacts.update(self._build_resource_artifacts(
                    project,
                    built.song_id,
                    mv_id,
                    resource_package,
                    resource_profile,
                    built=built,
                ))
                warnings.extend(resource_package["warnings"])
            if include_song_list:
                song_list, _, _, song_warnings = self._build_song_list(
                    project, difficulty, built, guest_available, music_style
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
                    resource_profile, resource_package, guest_available, music_style,
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
                self._save_project_assignment(
                    project, built.song_id, built.slot, guest_available, music_style
                )
                return report
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise

    def version_candidates(self) -> list[dict[str, Any]]:
        if self.project_store is None:
            raise Pm3ExportError("PM3 多曲版本需要项目存储")
        latest_report = self._latest_version_report()
        released_by_project = {
            str(song.get("project_id")): song
            for song in (latest_report or {}).get("songs", [])
            if isinstance(song, dict) and song.get("project_id")
        }
        next_version_name = self._next_version_name(latest_report)
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
                "music_style": self._project_music_style(project),
                "guest_available": self._project_guest_available(project),
                "difficulties": difficulties,
                "audio_ready": bool(background["available"] and preview["available"]),
                "audio": {"background": background, "preview": preview},
                "released": self._released_candidate(
                    released_by_project.get(project.id), latest_report
                ),
                "next_version_name": next_version_name,
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
            "cumulative": True,
            "lineage": prepared["lineage"],
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
                    if not song["resource_ready"]
                ]
                detail = (
                    f"曲目 {', '.join(missing)} 缺少完整歌曲资源"
                    if missing
                    else "ROM 构建环境不完整"
                )
                raise Pm3ExportError(f"PM3 多曲版本无法构建：{detail}")
            rom_songs = [
                Pm3RomSong(
                    song_id=song["song_id"],
                    mv_id=song["mv_id"],
                    title=song["project"].metadata.title,
                    artist=song["project"].metadata.artist,
                    background=self._read_resource(
                        song["project"], self._audio_resource_ref(song["project"], "background")
                    ),
                    preview=self._read_resource(
                        song["project"], self._audio_resource_ref(song["project"], "preview")
                    ),
                    key_sounds=self._prepare_key_sounds(
                        song["project"], song["key_sound_paths"]
                    ),
                    custom_mv=self._prepare_custom_mv(
                        song["project"], song["mv_id"]
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
                    "cumulative": True,
                    "lineage": prepared["lineage"],
                    "target_version": f"PM3 offline {version_name}",
                    "target": {
                        "id": "staging",
                        "label": "安全导出目录",
                        "kind": "staging",
                        "path": str(self.exports_root),
                    },
                    "songs": [
                        {
                            key: value
                            for key, value in song.items()
                            if key not in {"project", "key_sound_paths"}
                        }
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
                for song in prepared["resource_songs"]:
                    chart_slots = {
                        int(chart["slot"])
                        for chart in song["charts"]
                    }
                    if len(chart_slots) != 1:
                        raise Pm3ExportError(
                            f"曲目 {song['song_id']} 的多难度 Key slot 不一致"
                        )
                    self._save_project_assignment(
                        song["project"],
                        song["song_id"],
                        next(iter(chart_slots)),
                        song["guest_available"],
                        song["music_style"],
                    )
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
        if len(entries) > 250:
            raise Pm3ExportError("PM3 多曲版本一次最多包含 250 张谱面")
        lineage = self._version_lineage(version_name)

        charts: list[tuple[SongProject, DifficultyId, Pm3BuildResult, bool, int]] = []
        chart_summaries: list[dict[str, Any]] = []
        resource_by_song: dict[int, dict[str, Any]] = {}
        filenames: set[str] = set()
        warnings: list[str] = []
        artifacts: dict[str, bytes] = {}
        selected_settings: dict[tuple[str, str], dict[str, Any]] = {}

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
            expected_slot = reserved_slot(song_id, difficulty)
            if expected_slot is None:
                if song_id not in PM3_RESERVED_SONG_IDS:
                    choices = "、".join(str(value) for value in PM3_RESERVED_SONG_IDS)
                    raise Pm3ExportError(f"PM3 新曲只能使用预留曲目 ID：{choices}")
                raise Pm3ExportError(
                    f"预留曲目 ID {song_id} 不支持 {difficulty.value} 难度"
                )
            if slot != expected_slot:
                raise Pm3ExportError(
                    f"曲目 ID {song_id} 的 {difficulty.value} 难度必须使用 Key slot {expected_slot}"
                )
            self._validate_mv_id(mv_id)
            guest_available = raw.get(
                "guest_available", self._project_guest_available(project)
            )
            if not isinstance(guest_available, bool):
                raise Pm3ExportError(f"{project.metadata.title} 的游客开放设置无效")
            try:
                music_style = int(raw.get(
                    "music_style", self._project_music_style(project)
                ))
            except (TypeError, ValueError) as exc:
                raise Pm3ExportError(f"{project.metadata.title} 的音乐分类无效") from exc
            if music_style < 0 or music_style > 2:
                raise Pm3ExportError("PM3 音乐分类必须在 0..2")
            entry_key = (project.id, difficulty.value)
            if entry_key in selected_settings:
                raise Pm3ExportError(
                    f"{project.metadata.title} 的 {difficulty.value} 难度被重复加入版本"
                )
            selected_settings[entry_key] = {
                "song_id": song_id,
                "slot": slot,
                "mv_id": mv_id,
                "guest_available": guest_available,
                "music_style": music_style,
            }
            built = self.adapter.build_with_report(
                project, difficulty, slot=slot, song_id=song_id,
            )
            relative = f"rewrite/script_download/{built.filename}"
            lowered = relative.lower()
            if lowered in filenames:
                raise Pm3ExportError(f"多曲版本包含重复谱面：{built.filename}")
            filenames.add(lowered)
            artifacts[relative] = built.container
            charts.append((project, difficulty, built, guest_available, music_style))
            warnings.extend(built.warnings)

            resource_song = resource_by_song.get(song_id)
            if resource_song is None:
                package = self._resource_package(
                    project,
                    song_id,
                    mv_id,
                    "squashfs-ota",
                    key_sound_paths=built.key_sound_paths,
                )
                resource_song = {
                    "song_id": song_id,
                    "project_id": project.id,
                    "title": project.metadata.title,
                    "artist": project.metadata.artist,
                    "mv_id": mv_id,
                    "guest_available": guest_available,
                    "music_style": music_style,
                    "audio_ready": bool(
                        package["audio"]["background"]["available"]
                        and package["audio"]["preview"]["available"]
                    ),
                    "key_sounds_ready": package["key_sounds"]["complete"],
                    "key_sound_count": package["key_sounds"]["required_count"],
                    "resource_ready": bool(
                        package["audio"]["background"]["available"]
                        and package["audio"]["preview"]["available"]
                        and package["key_sounds"]["complete"]
                        and package["mv"]["available"]
                    ),
                    "key_sound_paths": dict(built.key_sound_paths),
                    "project": project,
                    "package": package,
                    "charts": [],
                }
                resource_by_song[song_id] = resource_song
            elif (
                resource_song["project_id"] != project.id
                or resource_song["mv_id"] != mv_id
                or resource_song["guest_available"] != guest_available
                or resource_song["music_style"] != music_style
            ):
                raise Pm3ExportError(
                    f"曲目序号 {song_id} 被不同项目、MV、分类或游客开放设置重复使用"
                )
            else:
                for asset_id, path in built.key_sound_paths.items():
                    previous = resource_song["key_sound_paths"].get(asset_id)
                    if previous is not None and previous != path:
                        raise Pm3ExportError(
                            f"曲目 {song_id} 的 Key 音 {asset_id} 逻辑路径不一致"
                        )
                    resource_song["key_sound_paths"][asset_id] = path
                package = self._resource_package(
                    project,
                    song_id,
                    mv_id,
                    "squashfs-ota",
                    key_sound_paths=resource_song["key_sound_paths"],
                )
                resource_song["package"] = package
                resource_song["key_sounds_ready"] = package["key_sounds"]["complete"]
                resource_song["key_sound_count"] = package["key_sounds"]["required_count"]
                resource_song["resource_ready"] = bool(
                    resource_song["audio_ready"]
                    and package["key_sounds"]["complete"]
                    and package["mv"]["available"]
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
                "guest_available": guest_available,
                "music_style": music_style,
                "filename": built.filename,
                "note_objects": built.stats["note_objects"],
                "event_count": built.stats["event_count"],
            }
            chart_summaries.append(chart_summary)
            resource_song["charts"].append(chart_summary)

        self._validate_cumulative_selection(lineage, selected_settings)
        resource_songs = list(resource_by_song.values())
        custom_mv_hashes: dict[int, str] = {}
        for song in resource_songs:
            mv = song["package"]["mv"]
            if not mv["custom"] or not mv["available"]:
                continue
            inspection = mv.get("inspection")
            digest = inspection.get("sha256") if isinstance(inspection, dict) else None
            previous = custom_mv_hashes.get(song["mv_id"])
            if previous is not None and previous != digest:
                raise Pm3ExportError(
                    f"自定义 MV {song['mv_id']} 被多个不同文件重复使用"
                )
            if isinstance(digest, str):
                custom_mv_hashes[song["mv_id"]] = digest
        try:
            rom = self.rom_builder.inspect_many(
                [song["song_id"] for song in resource_songs],
                custom_mv_ids=list(custom_mv_hashes),
            )
        except Pm3RomBuildError as exc:
            raise Pm3ExportError(str(exc)) from exc
        song_list, song_list_plaintext, song_list_encoding, song_warnings = (
            self._build_song_list_many(charts)
        )
        artifacts["rewrite/script_download/SongList.enc"] = song_list
        warnings.extend(song_warnings)
        for song in resource_songs:
            warnings.extend(song["package"]["warnings"])
        if not rom["available"]:
            warnings.append(f"离线 ROM 构建环境不完整：{'、'.join(rom['missing'])}")
        warnings.extend([
            (
                f"已继承 {lineage['base_version_name']} 的 "
                f"{lineage['required_song_count']} 首歌曲和 "
                f"{lineage['required_chart_count']} 张谱面"
                if lineage["base_version_name"]
                else "这是当前本地发布谱系的首个累计版本"
            ),
            "版本会从只读原版基线重建全部历史歌曲的共享 ROM 和 SongList，不会移除早期自制歌曲",
            f"{version_name} 仅生成与 OTA 镜像同形的归档目录，不会连接 FTP 或修改 update.cfg",
            "ROM 会逐曲回读校验；尚未经过 PM3 真机动态验证",
        ])
        songs = [
            {
                key: value
                for key, value in song.items()
                if key not in {"project", "package", "key_sound_paths"}
            }
            for song in resource_songs
        ]
        complete = bool(
            rom["available"] and all(song["resource_ready"] for song in resource_songs)
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
            "lineage": {
                key: value for key, value in lineage.items()
                if key != "required_entries"
            },
            "warnings": list(dict.fromkeys(warnings)),
            "stats": {
                "song_count": len(resource_songs),
                "chart_count": len(charts),
                "bundle_count": len(rom["bundles"]),
                "bundles": rom["bundles"],
                "note_objects": sum(item["note_objects"] for item in chart_summaries),
                "event_count": sum(item["event_count"] for item in chart_summaries),
                "custom_key_sound_count": sum(
                    song["key_sound_count"] for song in resource_songs
                ),
                "custom_mv_count": len(custom_mv_hashes),
            },
        }

    def _latest_version_report(self) -> dict[str, Any] | None:
        reports: list[tuple[int, str, dict[str, Any]]] = []
        for path in self.exports_root.glob("*/report.json"):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(report, dict) or report.get("kind") != "pm3-version":
                continue
            version_name = str(report.get("version_name", ""))
            match = PM3_VERSION_DIRECTORY.fullmatch(version_name)
            if match is None:
                continue
            reports.append((
                int(version_name[3:]),
                str(report.get("created_at", "")),
                report,
            ))
        return max(reports, key=lambda item: (item[0], item[1]))[2] if reports else None

    @staticmethod
    def _next_version_name(report: dict[str, Any] | None) -> str:
        if report is None:
            return "ver010"
        current = int(str(report["version_name"])[3:])
        return f"ver{min(999, current + 1):03d}"

    @staticmethod
    def _released_candidate(
        song: Any,
        report: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(song, dict) or report is None:
            return None
        charts = song.get("charts")
        chart_rows = charts if isinstance(charts, list) else []
        difficulties = [
            str(chart.get("difficulty"))
            for chart in chart_rows
            if isinstance(chart, dict) and chart.get("difficulty")
        ]
        slots = {
            int(chart["slot"])
            for chart in chart_rows
            if isinstance(chart, dict) and isinstance(chart.get("slot"), int)
        }
        return {
            "version_name": report.get("version_name"),
            "song_id": song.get("song_id"),
            "slot": next(iter(slots)) if len(slots) == 1 else 0,
            "mv_id": song.get("mv_id", 0),
            "guest_available": song.get("guest_available", True),
            "music_style": song.get("music_style", 0),
            "difficulties": list(dict.fromkeys(difficulties)),
        }

    def _version_lineage(self, version_name: str) -> dict[str, Any]:
        latest = self._latest_version_report()
        target_version = int(version_name[3:])
        if latest is None:
            return {
                "cumulative": True,
                "base_export_id": None,
                "base_version_name": None,
                "required_song_count": 0,
                "required_chart_count": 0,
                "required_entries": {},
            }
        latest_version = int(str(latest["version_name"])[3:])
        if target_version < latest_version:
            raise Pm3ExportError(
                f"本地最新累计版本是 {latest['version_name']}，不能回到更早的 {version_name}"
            )
        if target_version > latest_version + 1:
            raise Pm3ExportError(
                f"本地累计版本必须连续；当前应使用 {self._next_version_name(latest)}"
            )
        required_entries: dict[tuple[str, str], dict[str, Any]] = {}
        songs = latest.get("songs")
        if not isinstance(songs, list):
            raise Pm3ExportError("本地最新版本报告缺少歌曲清单，无法安全累计发布")
        for song in songs:
            if not isinstance(song, dict):
                continue
            project_id = song.get("project_id")
            song_id = song.get("song_id")
            mv_id = song.get("mv_id")
            guest_available = song.get("guest_available", True)
            music_style = song.get("music_style", 0)
            charts = song.get("charts")
            if (
                not isinstance(project_id, str)
                or not isinstance(song_id, int)
                or not isinstance(mv_id, int)
                or not isinstance(guest_available, bool)
                or not isinstance(music_style, int)
                or not isinstance(charts, list)
            ):
                raise Pm3ExportError("本地最新版本报告的歌曲字段不完整")
            for chart in charts:
                if not isinstance(chart, dict):
                    continue
                difficulty = chart.get("difficulty")
                slot = chart.get("slot")
                if not isinstance(difficulty, str) or not isinstance(slot, int):
                    raise Pm3ExportError("本地最新版本报告的谱面字段不完整")
                required_entries[(project_id, difficulty)] = {
                    "project_id": project_id,
                    "difficulty": difficulty,
                    "song_id": song_id,
                    "slot": slot,
                    "mv_id": mv_id,
                    "guest_available": guest_available,
                    "music_style": music_style,
                    "title": str(song.get("title", project_id)),
                }
        return {
            "cumulative": True,
            "base_export_id": latest.get("export_id"),
            "base_version_name": latest.get("version_name"),
            "required_song_count": len({
                item["song_id"] for item in required_entries.values()
            }),
            "required_chart_count": len(required_entries),
            "required_entries": required_entries,
        }

    @staticmethod
    def _validate_cumulative_selection(
        lineage: dict[str, Any],
        selected: dict[tuple[str, str], dict[str, Any]],
    ) -> None:
        required = lineage["required_entries"]
        missing = [
            item for key, item in required.items()
            if key not in selected
        ]
        if missing:
            labels = "、".join(
                f"{item['title']} ({item['difficulty']})"
                for item in missing[:6]
            )
            suffix = f" 等 {len(missing)} 张" if len(missing) > 6 else ""
            raise Pm3ExportError(
                f"累计版本不能移除已发布谱面：{labels}{suffix}"
            )
        for key, expected in required.items():
            actual = selected[key]
            remapping_legacy_id = (
                expected["song_id"] not in PM3_RESERVED_SONG_IDS
                and actual["song_id"] in PM3_RESERVED_SONG_IDS
            )
            for field, label in (
                ("song_id", "曲目序号"),
                ("slot", "Key slot"),
                ("mv_id", "MV"),
                ("guest_available", "游客开放设置"),
                ("music_style", "音乐分类"),
            ):
                if remapping_legacy_id and field in {"song_id", "slot"}:
                    continue
                if actual[field] != expected[field]:
                    raise Pm3ExportError(
                        f"已发布谱面 {expected['title']} ({expected['difficulty']}) "
                        f"不能改变{label}"
                    )

    @staticmethod
    def _project_song_id(project: SongProject) -> int | None:
        configured = project.game_specific_data.get("pm3_song_id")
        if isinstance(configured, int) and 0 <= configured <= 999:
            return configured
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

    def _save_project_assignment(
        self,
        project: SongProject,
        song_id: int,
        slot: int,
        guest_available: bool,
        music_style: int,
    ) -> None:
        if self.project_store is None:
            return
        assigned = project.model_copy(deep=True)
        assigned.metadata.game_song_id = f"p{song_id:03d}"
        assigned.game_specific_data["pm3_song_id"] = song_id
        assigned.game_specific_data["pm3_slot"] = slot
        assigned.game_specific_data["pm3_guest_available"] = guest_available
        assigned.game_specific_data["pm3_music_style"] = music_style
        self.project_store.save(assigned)

    @staticmethod
    def _project_slot(project: SongProject) -> int:
        slot = project.game_specific_data.get("pm3_slot")
        return int(slot) if isinstance(slot, int) and 0 <= slot <= 9 else 0

    @staticmethod
    def _project_guest_available(project: SongProject) -> bool:
        value = project.game_specific_data.get("pm3_guest_available")
        return value if isinstance(value, bool) else True

    @staticmethod
    def _project_music_style(project: SongProject) -> int:
        value = project.game_specific_data.get("pm3_music_style")
        if isinstance(value, int) and 0 <= value <= 2:
            return value
        raw = project.game_specific_data.get("pm3_song_info_raw_fields")
        if isinstance(raw, list) and len(raw) == 16:
            try:
                style = int(raw[11])
            except (TypeError, ValueError):
                return 0
            return style if 0 <= style <= 2 else 0
        return 0

    def _project_mv_id(self, project: SongProject) -> int:
        value = project.mv_configuration.get("pm3_mv_id")
        if isinstance(value, int) and value in PM3_MV_IDS:
            return value
        mv = self._pm3_mv_config(project)
        if (
            isinstance(value, int)
            and value in PM3_CUSTOM_MV_IDS
            and mv.get("id") == value
        ):
            return value
        return 0

    @staticmethod
    def _validate_version_name(version_name: str) -> None:
        if PM3_VERSION_DIRECTORY.fullmatch(version_name) is None:
            raise Pm3ExportError("PM3 版本目录必须使用 verNNN 格式")

    @staticmethod
    def _reserved_assignment(
        song_id: int | None,
        difficulty: DifficultyId,
        slot: int | None,
    ) -> tuple[int, int]:
        if song_id is None:
            raise Pm3ExportError("请选择 PM3 预留曲目 ID")
        expected_slot = reserved_slot(song_id, difficulty)
        if expected_slot is None:
            if song_id not in PM3_RESERVED_SONG_IDS:
                choices = "、".join(str(value) for value in PM3_RESERVED_SONG_IDS)
                raise Pm3ExportError(f"PM3 新曲只能使用预留曲目 ID：{choices}")
            raise Pm3ExportError(
                f"预留曲目 ID {song_id} 不支持 {difficulty.value} 难度"
            )
        if slot is not None and slot != expected_slot:
            raise Pm3ExportError(
                f"曲目 ID {song_id} 的 {difficulty.value} 难度必须使用 Key slot {expected_slot}"
            )
        return song_id, expected_slot

    def _build_song_list(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        built: Pm3BuildResult,
        guest_available: bool = True,
        music_style: int = 0,
    ) -> tuple[bytes, bytes, str, list[str]]:
        return self._build_song_list_many([
            (project, difficulty, built, guest_available, music_style)
        ])

    def _build_song_list_many(
        self,
        charts: list[tuple[SongProject, DifficultyId, Pm3BuildResult, bool, int]],
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
        for project, difficulty, built, guest_available, music_style in charts:
            filename = Path(built.filename).stem
            filenames.append(filename)
            matching = next(
                (row for row in rows if row.filename.lower() == filename.lower()), None
            )
            fields = self._song_list_fields(
                project, difficulty, built, matching, guest_available, music_style
            )
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
        guest_available: bool = True,
        music_style: int = 0,
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
        # SelSong::UISelFrmInfo reads field 5 directly for the displayed note
        # count. Leaving the two fields at zero makes otherwise valid custom
        # charts appear empty in the selection UI.
        fields[4] = str(int(built.stats["note_objects"]))
        fields[5] = str(int(built.stats["playable_events"]))
        fields[6] = f"{built.song_id:03d}"
        fields[7] = project.metadata.title
        fields[8] = project.metadata.artist
        fields[9] = str(built.song_id)
        ui = reserved_ui(built.song_id)
        if ui is None:
            raise Pm3ExportError(f"曲目 {built.song_id} 没有 PM3 UI 预留帧")
        fields[10] = str(ui["singer_id"])
        fields[11] = str(music_style)
        fields[12] = "0" if guest_available else "1"
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
        guest_available: bool,
        music_style: int,
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
            "guest_available": guest_available,
            "music_style": music_style,
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
        *,
        built: Pm3BuildResult | None = None,
        key_sound_paths: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        selected_key_sound_paths = (
            built.key_sound_paths if built is not None else (key_sound_paths or {})
        )
        background_ref = self._audio_resource_ref(project, "background")
        preview_ref = self._audio_resource_ref(project, "preview")
        background = self._resource_status(project, background_ref)
        preview = self._resource_status(project, preview_ref)
        key_sounds = self._key_sound_status(project, selected_key_sound_paths)
        mv = self._mv_status(project, mv_id)
        warnings = []
        if not background["available"]:
            warnings.append("尚未准备 PM3 主音乐；需要 Ogg Vorbis 44.1 kHz 双声道资源")
        if not preview["available"]:
            warnings.append("尚未准备 PM3 选歌试听；需要 PCM S16LE WAV 44.1 kHz 双声道资源")
        if not key_sounds["complete"]:
            if key_sounds["missing_count"]:
                warnings.append(
                    "尚有 "
                    f"{key_sounds['missing_count']} 个谱面使用的 Key 音缺少原始音频资源"
                )
            elif not key_sounds["transcoder_available"]:
                warnings.append("未找到 ffmpeg，无法把自定义 Key 音转换为 PM3 PCM WAV")
        if not mv["available"]:
            warnings.append(
                mv.get("error")
                or f"自定义 MV {mv_id} 尚未上传或与当前项目配置不一致"
            )
        rom = None
        if resource_profile == "squashfs-ota":
            rom = self.rom_builder.inspect(
                song_id,
                custom_mv_id=mv_id if mv["custom"] else None,
            )
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
                and key_sounds["complete"]
                and mv["available"]
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
            "key_sounds": key_sounds,
            "mv": {
                **mv,
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
        *,
        built: Pm3BuildResult,
    ) -> dict[str, bytes]:
        if not package["complete"]:
            missing = [
                label for label, key in (("主音乐", "background"), ("选歌试听", "preview"))
                if not package["audio"][key]["available"]
            ]
            rom = package.get("rom")
            if isinstance(rom, dict) and not rom.get("available"):
                missing.append("ROM 构建环境")
            if not package["key_sounds"]["complete"]:
                missing.append("Key 音")
            if not package["mv"]["available"]:
                missing.append("自定义 MV")
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
                    title=project.metadata.title,
                    artist=project.metadata.artist,
                    background=background,
                    preview=preview,
                    key_sounds=self._prepare_key_sounds(
                        project, built.key_sound_paths
                    ),
                    custom_mv=self._prepare_custom_mv(project, mv_id),
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
        custom_mv = self._prepare_custom_mv(project, mv_id)
        if custom_mv is not None:
            artifacts[f"media/ui/mv/mv{mv_id}.swf"] = custom_mv.payload
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
            "key_sounds": package["key_sounds"],
            "mv": {
                "id": mv_id,
                "custom": package["mv"]["custom"],
                "source_name": package["mv"].get("source_name"),
                "output_path": package["mv"].get("output_path"),
                "inspection": package["mv"].get("inspection"),
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

    def _key_sound_status(
        self,
        project: SongProject,
        key_sound_paths: dict[str, str],
    ) -> dict[str, Any]:
        assets = {asset.id: asset for asset in project.key_sounds}
        items: list[dict[str, Any]] = []
        for asset_id, logical_path in sorted(
            key_sound_paths.items(), key=lambda item: item[1]
        ):
            asset = assets.get(asset_id)
            if asset is None or not self._requires_custom_key_sound(asset, logical_path):
                continue
            reference = self._key_sound_resource_ref(asset)
            status = self._resource_status(project, reference)
            items.append({
                "asset_id": asset_id,
                "name": asset.name,
                "logical_path": logical_path.lstrip("./"),
                **status,
            })
        missing = [item for item in items if not item["available"]]
        transcoder_available = shutil.which("ffmpeg") is not None
        return {
            "complete": not missing and (not items or transcoder_available),
            "required_count": len(items),
            "available_count": len(items) - len(missing),
            "missing_count": len(missing),
            "transcoder_available": transcoder_available,
            "items": items,
        }

    def _prepare_key_sounds(
        self,
        project: SongProject,
        key_sound_paths: dict[str, str],
    ) -> tuple[Pm3RomKeySound, ...]:
        assets = {asset.id: asset for asset in project.key_sounds}
        result: list[Pm3RomKeySound] = []
        for asset_id, logical_path in sorted(
            key_sound_paths.items(), key=lambda item: item[1]
        ):
            asset = assets.get(asset_id)
            if asset is None or not self._requires_custom_key_sound(asset, logical_path):
                continue
            reference = self._key_sound_resource_ref(asset)
            if reference is None:
                raise Pm3ExportError(f"Key 音 {asset.name} 缺少可打包的音频资源")
            try:
                source = self._resolve_resource(project, reference)
                payload = convert_pm3_key_sound(source)
            except (
                Pm3ExportError,
                Pm3ResourceError,
                Pm3WorkspaceError,
                ProjectAssetError,
                ProjectNotFoundError,
                OSError,
            ) as exc:
                raise Pm3ExportError(f"Key 音 {asset.name} 转换失败：{exc}") from exc
            result.append(Pm3RomKeySound(
                relative_path=logical_path.lstrip("./"),
                payload=payload,
            ))
        return tuple(result)

    @staticmethod
    def _requires_custom_key_sound(asset: Any, logical_path: str) -> bool:
        extension = asset.extensions.get("pm3", {})
        raw_path = extension.get("raw_path") if isinstance(extension, dict) else None
        normalized = logical_path.replace("\\", "/").lstrip("./").casefold()
        return not raw_path and normalized.startswith("note/b2p_") and normalized.endswith(".wav")

    @staticmethod
    def _key_sound_resource_ref(asset: Any) -> dict[str, Any] | None:
        for namespace in ("editor", "bms", "bmson", "pm3"):
            extension = asset.extensions.get(namespace)
            reference = extension.get("resource") if isinstance(extension, dict) else None
            if isinstance(reference, dict):
                return reference
        return None

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

    @staticmethod
    def _pm3_mv_config(project: SongProject) -> dict[str, Any]:
        package = project.game_specific_data.get("pm3_package")
        if not isinstance(package, dict):
            return {}
        mv = package.get("mv")
        return mv if isinstance(mv, dict) else {}

    def _mv_status(self, project: SongProject, mv_id: int) -> dict[str, Any]:
        if mv_id in PM3_MV_IDS:
            return {
                "id": mv_id,
                "custom": False,
                "available": True,
                "source_name": None,
                "output_path": None,
                "inspection": None,
            }
        config = self._pm3_mv_config(project)
        reference = config.get("resource")
        base = {
            "id": mv_id,
            "custom": True,
            "source_name": config.get("source_name"),
            "output_path": f"media/ui/mv/mv{mv_id}.swf",
            "inspection": config.get("inspection"),
        }
        if config.get("id") != mv_id or not isinstance(reference, dict):
            return {**base, "available": False}
        status = self._resource_status(project, reference)
        if not status["available"]:
            return {**base, **status, "available": False}
        try:
            payload = self._read_resource(project, reference)
            inspection = inspect_pm3_mv_swf(payload)
        except (
            Pm3ExportError,
            Pm3ResourceError,
            Pm3WorkspaceError,
            ProjectAssetError,
            ProjectNotFoundError,
            OSError,
        ) as exc:
            return {
                **base,
                **status,
                "available": False,
                "error": f"自定义 MV {mv_id} 校验失败：{exc}",
            }
        return {**base, **status, "available": True, "inspection": inspection}

    def _prepare_custom_mv(
        self,
        project: SongProject,
        mv_id: int,
    ) -> Pm3RomMv | None:
        if mv_id in PM3_MV_IDS:
            return None
        status = self._mv_status(project, mv_id)
        if not status["available"]:
            raise Pm3ExportError(
                status.get("error")
                or f"自定义 MV {mv_id} 尚未上传或与当前项目配置不一致"
            )
        reference = self._pm3_mv_config(project).get("resource")
        if not isinstance(reference, dict):
            raise Pm3ExportError(f"自定义 MV {mv_id} 的资源引用缺失")
        return Pm3RomMv(
            mv_id=mv_id,
            payload=self._read_resource(project, reference),
        )

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
        if mv_id not in PM3_MV_IDS and mv_id not in PM3_CUSTOM_MV_IDS:
            builtins = ", ".join(str(value) for value in PM3_MV_IDS)
            raise Pm3ExportError(
                f"PM3 MV ID 无效；内置值为 {builtins}，自定义值为 20..99"
            )

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
