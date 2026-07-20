from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .adapters import (
    BmsAdapter,
    BmsFormatError,
    BmsonAdapter,
    BmsonFormatError,
    NoteListAdapter,
    NoteListFormatError,
    Pm3FormatError,
)
from .factory import new_project
from .adapters.pm3_reservations import reservation_catalog
from .models import (
    CreateProjectRequest,
    DifficultyId,
    KeySoundAsset,
    ProjectSummary,
    SongProject,
    ValidationIssue,
)
from .services.validation import validate_project
from .services.bms_resources import (
    AUDIO_SUFFIXES,
    BROWSER_VIDEO_SUFFIXES,
    VIDEO_SUFFIXES,
    VISUAL_SUFFIXES,
    create_browser_video_preview,
    match_bms_bga_resources,
    match_bms_key_sound_resources,
    normalize_bms_resource_path,
    visual_resource_kind,
)
from .services.pm3_export import Pm3ExportError, Pm3ExportService
from .services.pm3_ota_audit import Pm3OtaAuditError, Pm3OtaAuditor
from .services.pm3_resources import (
    MAX_PM3_AUDIO_BYTES,
    MAX_PM3_MV_BYTES,
    PM3_CUSTOM_MV_IDS,
    PM3_MV_IDS,
    Pm3ResourceError,
    build_pm3_mv_state_preview,
    prepare_pm3_audio,
    prepare_pm3_mv,
)
from .services.pm3_workspace import Pm3Workspace, Pm3WorkspaceError
from .storage import ProjectAssetError, ProjectNotFoundError, ProjectStore


class Pm3FileRef(BaseModel):
    root_id: str
    path: str


class Pm3DiffRequest(BaseModel):
    left: Pm3FileRef
    right: Pm3FileRef


class Pm3ImportRequest(Pm3FileRef):
    difficulty: DifficultyId = DifficultyId.hard


class Pm3ExportRequest(BaseModel):
    difficulty: DifficultyId = DifficultyId.hard
    target_id: str = "staging"
    slot: int | None = None
    song_id: int | None = None
    include_song_list: bool = False
    include_resources: bool = False
    music_style: int = Field(default=0, ge=0, le=2)
    guest_available: bool = True
    mv_id: int = Field(default=0, ge=0, le=99)
    resource_profile: Literal["extracted-media-overlay", "squashfs-ota"] = "extracted-media-overlay"


class Pm3VersionEntryRequest(BaseModel):
    project_id: str
    difficulty: DifficultyId
    song_id: int = Field(ge=0, le=210)
    slot: int = Field(default=0, ge=0, le=9)
    mv_id: int = Field(default=0, ge=0, le=99)
    music_style: int = Field(default=0, ge=0, le=2)
    guest_available: bool = True


class Pm3VersionRequest(BaseModel):
    version_name: str = Field(pattern=r"^ver[0-9]{3}$")
    entries: list[Pm3VersionEntryRequest] = Field(min_length=1, max_length=250)


class Pm3OtaChainRequest(BaseModel):
    export_ids: list[str] = Field(min_length=1, max_length=20)


def create_app(
    store: ProjectStore | None = None,
    pm3_workspace: Pm3Workspace | None = None,
    pm3_export_service: Pm3ExportService | None = None,
) -> FastAPI:
    app = FastAPI(title="BMSON2PM API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store or ProjectStore()
    app.state.pm3_workspace = pm3_workspace or Pm3Workspace()
    app.state.pm3_export = pm3_export_service or Pm3ExportService(
        workspace=app.state.pm3_workspace,
        project_store=app.state.store,
    )
    app.state.pm3_export.project_store = app.state.store
    app.state.pm3_ota_audit = Pm3OtaAuditor(app.state.pm3_export, app.state.pm3_workspace)
    bmson_adapter = BmsonAdapter()
    bms_adapter = BmsAdapter()
    notelist_adapter = NoteListAdapter()

    def get_project(project_id: str) -> SongProject:
        try:
            project = app.state.store.get(project_id)
            notelist_adapter.promote_legacy_tracks(project)
            return project
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="项目不存在") from exc

    async def attach_bms_key_sounds(
        project: SongProject,
        warnings: list[str],
        resources: list[UploadFile],
        raw_paths: str,
    ) -> None:
        declared_count = len(project.key_sounds)
        if not declared_count:
            return
        if len(resources) > 2048:
            raise HTTPException(status_code=413, detail="BMS Key 音资源不得超过 2048 个文件")
        try:
            parsed_paths = json.loads(raw_paths) if raw_paths else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="BMS 资源路径必须是 JSON 数组") from exc
        if parsed_paths and (
            not isinstance(parsed_paths, list)
            or len(parsed_paths) != len(resources)
            or not all(isinstance(item, str) for item in parsed_paths)
        ):
            raise HTTPException(status_code=422, detail="BMS 资源路径与上传文件不匹配")
        supplied_paths = parsed_paths or [Path(item.filename or "resource").name for item in resources]
        try:
            normalized_paths = [normalize_bms_resource_path(item) for item in supplied_paths]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not resources:
            warnings.append(
                f"检测到 {declared_count} 个 WAV 定义，但未提供 BMS 资源目录；试听将使用合成兜底音"
            )
            project.unknown_data["bms_resource_report"] = {
                "declared": declared_count, "matched": 0, "missing": declared_count,
            }
            return

        upload_by_path = {
            path.casefold(): upload for path, upload in zip(normalized_paths, resources)
        }
        matches = match_bms_key_sound_resources(project, normalized_paths)
        assets_by_id = {asset.id: asset for asset in project.key_sounds}
        stored_by_path: dict[str, str] = {}
        total_size = 0
        try:
            for resource_path in dict.fromkeys(matches.by_asset_id.values()):
                upload = upload_by_path.get(resource_path.casefold())
                if upload is None:
                    continue
                await upload.seek(0)
                payload = await upload.read(64 * 1024 * 1024 + 1)
                if len(payload) > 64 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"Key 音 {resource_path} 超过 64 MB")
                total_size += len(payload)
                if total_size > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="BMS Key 音资源合计不得超过 512 MB")
                stored_by_path[resource_path] = app.state.store.save_asset(
                    project.id, resource_path, payload
                )
        except (OSError, ProjectAssetError) as exc:
            app.state.store.clear_assets(project.id)
            raise HTTPException(status_code=422, detail=f"保存 BMS Key 音失败：{exc}") from exc
        except HTTPException:
            app.state.store.clear_assets(project.id)
            raise

        for asset_id, resource_path in matches.by_asset_id.items():
            stored_path = stored_by_path.get(resource_path)
            asset = assets_by_id.get(asset_id)
            if not stored_path or asset is None:
                continue
            bms_data = asset.extensions.setdefault("bms", {})
            if not isinstance(bms_data, dict):
                bms_data = {}
                asset.extensions["bms"] = bms_data
            bms_data["resource"] = {
                "project_id": project.id,
                "path": stored_path,
                "declared_path": asset.filename,
                "exists": True,
            }
            project.source_files.append({
                "role": "bms-key-sound",
                "project_id": project.id,
                "path": stored_path,
                "declared_path": asset.filename,
                "exists": True,
            })

        matched_count = len(matches.by_asset_id)
        warnings.append(f"已关联 {matched_count}/{declared_count} 个 BMS Key 音资源")
        if matches.extension_fallback_count:
            warnings.append(
                f"{matches.extension_fallback_count} 个 WAV 引用已按同名其他音频扩展名匹配"
            )
        if matches.missing_count:
            warnings.append(f"仍有 {matches.missing_count} 个 BMS Key 音资源缺失")
        project.unknown_data["bms_resource_report"] = {
            "declared": declared_count,
            "matched": matched_count,
            "exact": matches.exact_count,
            "extension_fallback": matches.extension_fallback_count,
            "missing": matches.missing_count,
            "bytes": total_size,
        }

    async def attach_bms_bga(
        project: SongProject,
        warnings: list[str],
        resources: list[UploadFile],
        raw_paths: str,
    ) -> None:
        bga = project.mv_configuration.get("bms_bga")
        definitions = bga.get("bmp_defs") if isinstance(bga, dict) else None
        bmp_defs = definitions if isinstance(definitions, dict) else {}
        declared_count = len(bmp_defs)
        if not declared_count:
            return
        if len(resources) > 2048:
            raise HTTPException(status_code=413, detail="BMS 资源不得超过 2048 个文件")
        try:
            parsed_paths = json.loads(raw_paths) if raw_paths else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="BMS 资源路径必须是 JSON 数组") from exc
        if parsed_paths and (
            not isinstance(parsed_paths, list)
            or len(parsed_paths) != len(resources)
            or not all(isinstance(item, str) for item in parsed_paths)
        ):
            raise HTTPException(status_code=422, detail="BMS 资源路径与上传文件不匹配")
        supplied_paths = parsed_paths or [Path(item.filename or "resource").name for item in resources]
        try:
            normalized_paths = [normalize_bms_resource_path(item) for item in supplied_paths]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not resources:
            warnings.append(f"检测到 {declared_count} 个 BMP 定义，但未提供 BGA 图片或视频资源")
            project.unknown_data["bms_visual_resource_report"] = {
                "declared": declared_count, "matched": 0, "missing": declared_count,
            }
            return

        upload_by_path = {
            path.casefold(): upload for path, upload in zip(normalized_paths, resources)
        }
        matches = match_bms_bga_resources(project, normalized_paths)
        stored_by_path: dict[str, str] = {}
        preview_by_path: dict[str, str] = {}
        preview_error_by_path: dict[str, str] = {}
        total_size = 0
        preview_size = 0
        try:
            for resource_path in dict.fromkeys(matches.by_asset_id.values()):
                upload = upload_by_path.get(resource_path.casefold())
                if upload is None:
                    continue
                await upload.seek(0)
                payload = await upload.read(256 * 1024 * 1024 + 1)
                if len(payload) > 256 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"BGA 资源 {resource_path} 超过 256 MB")
                total_size += len(payload)
                if total_size > 1024 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="BMS BGA 资源合计不得超过 1 GB")
                stored_by_path[resource_path] = app.state.store.save_asset(
                    project.id, resource_path, payload
                )
                suffix = Path(resource_path).suffix.casefold()
                if suffix in VIDEO_SUFFIXES and suffix not in BROWSER_VIDEO_SUFFIXES:
                    preview, preview_error = create_browser_video_preview(payload, suffix)
                    if preview:
                        preview_relative = (
                            f"_bga_preview/{Path(resource_path).with_suffix('.mp4').as_posix()}"
                        )
                        preview_by_path[resource_path] = app.state.store.save_asset(
                            project.id, preview_relative, preview
                        )
                        preview_size += len(preview)
                    elif preview_error:
                        preview_error_by_path[resource_path] = preview_error
        except (OSError, ProjectAssetError) as exc:
            app.state.store.clear_assets(project.id)
            raise HTTPException(status_code=422, detail=f"保存 BMS BGA 失败：{exc}") from exc
        except HTTPException:
            app.state.store.clear_assets(project.id)
            raise

        assets = bga.setdefault("assets", {}) if isinstance(bga, dict) else {}
        if not isinstance(assets, dict):
            assets = {}
            if isinstance(bga, dict):
                bga["assets"] = assets
        for bmp_id, resource_path in matches.by_asset_id.items():
            stored_path = stored_by_path.get(resource_path)
            if not stored_path:
                continue
            declared_path = str(bmp_defs.get(bmp_id, resource_path))
            suffix = Path(resource_path).suffix.casefold()
            resource_data: dict[str, object] = {
                "project_id": project.id,
                "path": stored_path,
                "declared_path": declared_path,
                "exists": True,
                "mime_type": mimetypes.guess_type(resource_path)[0] or "application/octet-stream",
            }
            preview_path = preview_by_path.get(resource_path)
            if preview_path:
                resource_data.update(preview_path=preview_path, preview_mime_type="video/mp4")
            preview_error = preview_error_by_path.get(resource_path)
            if preview_error:
                resource_data["preview_error"] = preview_error
            assets[bmp_id] = {
                "id": bmp_id,
                "filename": declared_path,
                "kind": visual_resource_kind(resource_path),
                "resource": resource_data,
            }
            project.source_files.append({
                "role": "bms-bga",
                "project_id": project.id,
                "path": stored_path,
                "declared_path": declared_path,
                "preview_path": preview_path,
                "exists": True,
            })

        matched_count = len(matches.by_asset_id)
        warnings.append(f"已关联 {matched_count}/{declared_count} 个 BMS BGA 资源")
        if matches.extension_fallback_count:
            warnings.append(
                f"{matches.extension_fallback_count} 个 BMP 引用已按同名其他视觉扩展名匹配"
            )
        if preview_by_path:
            warnings.append(f"已为 {len(preview_by_path)} 个视频生成浏览器 MP4 预览代理")
        if preview_error_by_path:
            warnings.append(f"{len(preview_error_by_path)} 个视频无法生成浏览器预览代理")
        if matches.missing_count:
            warnings.append(f"仍有 {matches.missing_count} 个 BMS BGA 资源缺失")
        project.unknown_data["bms_visual_resource_report"] = {
            "declared": declared_count,
            "matched": matched_count,
            "exact": matches.exact_count,
            "extension_fallback": matches.extension_fallback_count,
            "missing": matches.missing_count,
            "bytes": total_size,
            "preview_bytes": preview_size,
            "preview_count": len(preview_by_path),
        }

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects", response_model=list[ProjectSummary])
    def list_projects() -> list[ProjectSummary]:
        return app.state.store.list()

    @app.get("/api/pm3/roots")
    def pm3_roots() -> list[dict[str, object]]:
        return app.state.pm3_workspace.root_descriptors()

    @app.get("/api/pm3/ota/mirror")
    def audit_pm3_ota_mirror(
        generation: int | None = Query(None, ge=0, le=999),
        installed_version: int | None = Query(None, ge=0, le=999),
        installed_edition: int | None = Query(None, ge=0, le=999),
        downloaded_version: int | None = Query(None, ge=0, le=999),
        downloaded_edition: int | None = Query(None, ge=0, le=999),
        verify_payloads: bool = Query(False),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_ota_audit.audit_mirror(
                generation=generation,
                installed_version=installed_version,
                installed_edition=installed_edition,
                downloaded_version=downloaded_version,
                downloaded_edition=downloaded_edition,
                verify_payloads=verify_payloads,
            )
        except (Pm3OtaAuditError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/tree")
    def pm3_tree(
        root_id: str = Query(..., alias="root"),
        path: str = Query(""),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_workspace.list_directory(root_id, path)
        except Pm3WorkspaceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/file")
    def pm3_file(
        root_id: str = Query(..., alias="root"),
        path: str = Query(...),
        offset: int = Query(0, ge=0),
        length: int = Query(4096, ge=16, le=65536),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_workspace.inspect_file(root_id, path, offset=offset, length=length)
        except (Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/key-sound")
    def pm3_key_sound(
        root_id: str = Query("game", alias="root"),
        path: str = Query(...),
    ) -> FileResponse:
        if root_id != "game":
            raise HTTPException(status_code=422, detail="Key 音只允许从 PM3 游戏目录读取")
        try:
            note_root = app.state.pm3_workspace.resolve(
                "game", "media/sound/note", expect="directory"
            ).resolve()
            resource = app.state.pm3_workspace.resolve(root_id, path, expect="file").resolve()
        except (Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not resource.is_relative_to(note_root):
            raise HTTPException(status_code=422, detail="Key 音必须位于 media/sound/note 目录")
        if resource.suffix.lower() not in {".wav", ".ogg"}:
            raise HTTPException(status_code=415, detail="仅支持 WAV 或 OGG Key 音")
        if resource.stat().st_size > 64 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Key 音文件不得超过 64 MB")
        return FileResponse(
            resource,
            media_type=mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/pm3/audio")
    def pm3_audio(
        root_id: str = Query("game", alias="root"),
        path: str = Query(...),
    ) -> FileResponse:
        if root_id != "game":
            raise HTTPException(status_code=422, detail="PM3 音频只允许从游戏目录读取")
        try:
            sound_root = app.state.pm3_workspace.resolve(
                "game", "media/sound", expect="directory"
            ).resolve()
            resource = app.state.pm3_workspace.resolve(root_id, path, expect="file").resolve()
        except (Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        allowed_roots = tuple(
            (sound_root / directory).resolve() for directory in ("BG", "preview")
        )
        if not any(resource.is_relative_to(directory) for directory in allowed_roots):
            raise HTTPException(
                status_code=422,
                detail="自动音乐只允许位于 media/sound/BG 或 media/sound/preview",
            )
        if resource.suffix.lower() not in {".wav", ".ogg"}:
            raise HTTPException(status_code=415, detail="仅支持 WAV 或 OGG 自动音乐")
        if resource.stat().st_size > 256 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="自动音乐文件不得超过 256 MB")
        return FileResponse(
            resource,
            media_type=mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/pm3/diff")
    def pm3_diff(request: Pm3DiffRequest) -> dict[str, object]:
        try:
            return app.state.pm3_workspace.diff_files(
                request.left.model_dump(), request.right.model_dump()
            )
        except (Pm3WorkspaceError, OSError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/catalog")
    def pm3_catalog(
        search: str = Query("", max_length=100),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_workspace.catalog(search, offset=offset, limit=limit)
        except (Pm3WorkspaceError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/chart")
    def pm3_chart(
        root_id: str = Query(..., alias="root"),
        path: str = Query(...),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_workspace.inspect_chart(root_id, path)
        except (Pm3WorkspaceError, Pm3FormatError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/import/pm3", response_model=SongProject, status_code=201)
    def import_pm3(request: Pm3ImportRequest) -> SongProject:
        try:
            result = app.state.pm3_workspace.import_chart(
                request.root_id, request.path, request.difficulty
            )
        except (Pm3WorkspaceError, Pm3FormatError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result.project.unknown_data["import_warnings"] = result.warnings
        return app.state.store.save(result.project, touch=False)

    @app.get("/api/pm3/export-targets")
    def pm3_export_targets() -> list[dict[str, object]]:
        return app.state.pm3_export.target_descriptors()

    @app.get("/api/pm3/song-id-reservations")
    def pm3_song_id_reservations() -> list[dict[str, object]]:
        return reservation_catalog()

    @app.get("/api/pm3/version-candidates")
    def pm3_version_candidates() -> list[dict[str, object]]:
        try:
            return app.state.pm3_export.version_candidates()
        except (Pm3ExportError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/pm3/versions/preview")
    def preview_pm3_version(request: Pm3VersionRequest) -> dict[str, object]:
        try:
            return app.state.pm3_export.preview_version(
                version_name=request.version_name,
                entries=[entry.model_dump() for entry in request.entries],
            )
        except (Pm3ExportError, Pm3FormatError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/pm3/versions/export")
    def export_pm3_version(request: Pm3VersionRequest) -> dict[str, object]:
        try:
            return app.state.pm3_export.export_version(
                version_name=request.version_name,
                entries=[entry.model_dump() for entry in request.entries],
            )
        except (Pm3ExportError, Pm3FormatError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/projects/{project_id}/pm3/audio",
        response_model=SongProject,
    )
    async def prepare_project_pm3_audio(
        project_id: str,
        file: UploadFile = File(...),
        preview_start: float = Form(0, ge=0, le=24 * 60 * 60),
        preview_duration: float = Form(12, ge=1, le=60),
    ) -> SongProject:
        project = get_project(project_id)
        payload = await file.read(MAX_PM3_AUDIO_BYTES + 1)
        try:
            return prepare_pm3_audio(
                app.state.store,
                project,
                filename=file.filename or "music",
                payload=payload,
                preview_start=preview_start,
                preview_duration=preview_duration,
            )
        except (Pm3ResourceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/projects/{project_id}/pm3/mv",
        response_model=SongProject,
    )
    async def prepare_project_pm3_mv(
        project_id: str,
        file: UploadFile = File(...),
        mv_id: int = Form(..., ge=20, le=99),
    ) -> SongProject:
        project = get_project(project_id)
        payload = await file.read(MAX_PM3_MV_BYTES + 1)
        try:
            return prepare_pm3_mv(
                app.state.store,
                project,
                filename=file.filename or f"mv{mv_id}.swf",
                payload=payload,
                mv_id=mv_id,
            )
        except (Pm3ResourceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/pm3/mv-preview/mvctrl/mvctrl.swf")
    def preview_project_pm3_mv_controller(project_id: str) -> FileResponse:
        get_project(project_id)
        try:
            resource = app.state.pm3_workspace.resolve(
                "game", "media/ui/mvctrl/mvctrl.swf", expect="file"
            )
        except (Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=404, detail="PM3 MV 控制器不可用") from exc
        if resource.stat().st_size > MAX_PM3_MV_BYTES:
            raise HTTPException(status_code=413, detail="PM3 MV 控制器超过大小限制")
        return FileResponse(
            resource,
            media_type="application/x-shockwave-flash",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/projects/{project_id}/pm3/mv-preview/mv/mv{mv_id}.swf")
    def preview_project_pm3_mv_resource(
        project_id: str,
        mv_id: int,
        state: Literal["low", "middle", "high", "full"] | None = Query(None),
    ) -> Response:
        project = get_project(project_id)
        if mv_id in PM3_MV_IDS:
            try:
                resource = app.state.pm3_workspace.resolve(
                    "game", f"media/ui/mv/mv{mv_id}.swf", expect="file"
                )
            except (Pm3WorkspaceError, OSError) as exc:
                raise HTTPException(status_code=404, detail=f"PM3 MV {mv_id} 不可用") from exc
        elif mv_id in PM3_CUSTOM_MV_IDS:
            package = project.game_specific_data.get("pm3_package")
            mv = package.get("mv") if isinstance(package, dict) else None
            reference = mv.get("resource") if isinstance(mv, dict) else None
            if (
                not isinstance(mv, dict)
                or mv.get("id") != mv_id
                or not isinstance(reference, dict)
                or reference.get("project_id") != project_id
                or not isinstance(reference.get("path"), str)
            ):
                raise HTTPException(status_code=404, detail=f"项目未配置 PM3 MV {mv_id}")
            try:
                resource = app.state.store.asset_path(project_id, reference["path"])
            except (ProjectNotFoundError, ProjectAssetError) as exc:
                raise HTTPException(status_code=404, detail=f"PM3 MV {mv_id} 资源不存在") from exc
        else:
            raise HTTPException(status_code=404, detail=f"PM3 MV {mv_id} 不存在")

        if resource.suffix.casefold() != ".swf":
            raise HTTPException(status_code=415, detail="PM3 MV 预览仅支持 SWF")
        if resource.stat().st_size > MAX_PM3_MV_BYTES:
            raise HTTPException(status_code=413, detail="PM3 MV 超过大小限制")
        headers = {
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        }
        if state is not None:
            try:
                preview_payload = build_pm3_mv_state_preview(resource.read_bytes(), state)
            except (Pm3ResourceError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return Response(
                content=preview_payload,
                media_type="application/x-shockwave-flash",
                headers={**headers, "Cache-Control": "no-store"},
            )
        return FileResponse(
            resource,
            media_type="application/x-shockwave-flash",
            headers=headers,
        )

    @app.get("/api/projects/{project_id}/export/pm3/preview")
    def preview_pm3_export(
        project_id: str,
        difficulty: DifficultyId = Query(DifficultyId.hard),
        slot: int | None = Query(None, ge=0, le=9),
        song_id: int | None = Query(None, ge=0, le=210),
        include_song_list: bool = Query(False),
        include_resources: bool = Query(False),
        music_style: int = Query(0, ge=0, le=2),
        guest_available: bool = Query(True),
        mv_id: int = Query(0, ge=0, le=99),
        resource_profile: Literal["extracted-media-overlay", "squashfs-ota"] = Query(
            "extracted-media-overlay"
        ),
    ) -> dict[str, object]:
        try:
            return app.state.pm3_export.preview(
                get_project(project_id), difficulty, slot=slot, song_id=song_id,
                include_song_list=include_song_list,
                include_resources=include_resources,
                music_style=music_style,
                guest_available=guest_available,
                mv_id=mv_id,
                resource_profile=resource_profile,
            )
        except (Pm3ExportError, Pm3FormatError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/export/pm3")
    def export_pm3(project_id: str, request: Pm3ExportRequest) -> dict[str, object]:
        try:
            return app.state.pm3_export.export(
                get_project(project_id), request.difficulty,
                target_id=request.target_id, slot=request.slot,
                song_id=request.song_id,
                include_song_list=request.include_song_list,
                include_resources=request.include_resources,
                music_style=request.music_style,
                guest_available=request.guest_available,
                mv_id=request.mv_id,
                resource_profile=request.resource_profile,
            )
        except (Pm3ExportError, Pm3FormatError, Pm3WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/exports")
    def pm3_exports() -> list[dict[str, object]]:
        return app.state.pm3_export.list_reports()

    @app.get("/api/pm3/exports/{export_id}")
    def pm3_export_report(export_id: str) -> dict[str, object]:
        try:
            return app.state.pm3_export.get_report(export_id)
        except Pm3ExportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/pm3/exports/{export_id}/audit")
    def audit_pm3_export(export_id: str) -> dict[str, object]:
        try:
            return app.state.pm3_ota_audit.audit_export(export_id)
        except Pm3ExportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (Pm3OtaAuditError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/pm3/exports/audit-chain")
    def audit_pm3_export_chain(request: Pm3OtaChainRequest) -> dict[str, object]:
        try:
            return app.state.pm3_ota_audit.simulate_chain(request.export_ids)
        except Pm3ExportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (Pm3OtaAuditError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/pm3/exports/{export_id}/download")
    def download_pm3_export(export_id: str) -> FileResponse:
        try:
            archive = app.state.pm3_export.archive_path(export_id)
        except Pm3ExportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(archive, media_type="application/zip", filename=f"pm3-{export_id}.zip")

    @app.post("/api/pm3/exports/{export_id}/rollback")
    def rollback_pm3_export(export_id: str) -> dict[str, object]:
        try:
            return app.state.pm3_export.rollback(export_id)
        except Pm3ExportError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects", response_model=SongProject, status_code=201)
    def create_project(request: CreateProjectRequest) -> SongProject:
        return app.state.store.save(new_project(request), touch=False)

    @app.get("/api/projects/{project_id}", response_model=SongProject)
    def read_project(project_id: str) -> SongProject:
        return get_project(project_id)

    @app.put("/api/projects/{project_id}", response_model=SongProject)
    def save_project(project_id: str, project: SongProject) -> SongProject:
        if project_id != project.id:
            raise HTTPException(status_code=400, detail="项目 ID 不一致")
        return app.state.store.save(project)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str) -> Response:
        try:
            app.state.store.delete(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="项目不存在") from exc
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/key-sound")
    def project_key_sound(
        project_id: str,
        path: str = Query(...),
    ) -> FileResponse:
        get_project(project_id)
        try:
            resource = app.state.store.asset_path(project_id, path)
        except (ProjectNotFoundError, ProjectAssetError) as exc:
            raise HTTPException(status_code=404, detail="BMS Key 音资源不存在") from exc
        if resource.suffix.casefold() not in AUDIO_SUFFIXES:
            raise HTTPException(status_code=415, detail="不支持的 BMS Key 音格式")
        return FileResponse(
            resource,
            media_type=mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post(
        "/api/projects/{project_id}/key-sounds",
        response_model=KeySoundAsset,
        status_code=201,
    )
    async def upload_project_key_sound(
        project_id: str,
        file: UploadFile = File(...),
    ) -> KeySoundAsset:
        get_project(project_id)
        filename = Path(file.filename or "key-sound").name
        suffix = Path(filename).suffix.casefold()
        if suffix not in AUDIO_SUFFIXES:
            raise HTTPException(status_code=415, detail="Key 音仅支持 WAV、OGG、MP3、FLAC 或 AIFF")
        payload = await file.read(64 * 1024 * 1024 + 1)
        if not payload:
            raise HTTPException(status_code=422, detail="Key 音文件不能为空")
        if len(payload) > 64 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Key 音文件不得超过 64 MB")
        asset_id = str(uuid4())
        relative = f"key-sounds/{asset_id}{suffix}"
        try:
            stored_path = app.state.store.save_asset(project_id, relative, payload)
        except (OSError, ProjectAssetError) as exc:
            raise HTTPException(status_code=422, detail=f"保存 Key 音失败：{exc}") from exc
        return KeySoundAsset(
            id=asset_id,
            name=Path(filename).stem or "Key 音",
            filename=filename,
            source="manual",
            extensions={
                "editor": {
                    "resource": {
                        "project_id": project_id,
                        "path": stored_path,
                        "exists": True,
                    },
                },
            },
        )

    @app.delete("/api/projects/{project_id}/key-sounds/{asset_id}", status_code=204)
    def delete_project_key_sound(
        project_id: str,
        asset_id: str,
        path: str = Query(...),
    ) -> Response:
        get_project(project_id)
        expected_prefix = f"key-sounds/{asset_id}."
        if not path.casefold().startswith(expected_prefix.casefold()):
            raise HTTPException(status_code=422, detail="只能删除项目内手动上传的 Key 音")
        try:
            app.state.store.delete_asset(project_id, path)
        except (OSError, ProjectNotFoundError, ProjectAssetError) as exc:
            raise HTTPException(status_code=404, detail="Key 音资源不存在") from exc
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/bga-resource")
    def project_bga_resource(
        project_id: str,
        path: str = Query(...),
    ) -> FileResponse:
        get_project(project_id)
        try:
            resource = app.state.store.asset_path(project_id, path)
        except (ProjectNotFoundError, ProjectAssetError) as exc:
            raise HTTPException(status_code=404, detail="BMS BGA 资源不存在") from exc
        if resource.suffix.casefold() not in VISUAL_SUFFIXES:
            raise HTTPException(status_code=415, detail="不支持的 BMS BGA 格式")
        return FileResponse(
            resource,
            media_type=mimetypes.guess_type(resource.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/import/bmson", response_model=SongProject, status_code=201)
    async def import_bmson(
        file: UploadFile = File(...),
        difficulty: DifficultyId = Query(DifficultyId.hard),
    ) -> SongProject:
        filename = Path(file.filename or "chart.bmson").name
        if not filename.lower().endswith((".bmson", ".json")):
            raise HTTPException(status_code=415, detail="仅支持 .bmson 或 .json 文件")
        payload = await file.read(10 * 1024 * 1024 + 1)
        if len(payload) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="BMSON 文件不得超过 10 MB")
        try:
            result = bmson_adapter.parse_with_warnings(payload, difficulty)
        except BmsonFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result.project.metadata.source_name = filename
        result.project.unknown_data["import_warnings"] = result.warnings
        return app.state.store.save(result.project, touch=False)

    @app.post("/api/import/json", response_model=SongProject, status_code=201)
    async def import_json_chart(
        file: UploadFile = File(...),
        difficulty: DifficultyId = Query(DifficultyId.hard),
    ) -> SongProject:
        filename, payload = await read_json_upload(file)
        try:
            if not notelist_adapter.detect(payload, filename).supported:
                if not bmson_adapter.detect(payload, filename).supported:
                    raise NoteListFormatError("无法识别 JSON 谱面：需要 BMSON 或 NoteList JSON 结构")
                result = bmson_adapter.parse_with_warnings(payload, difficulty)
            else:
                result = notelist_adapter.parse_with_warnings(payload, difficulty)
        except (BmsonFormatError, NoteListFormatError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.project.metadata.title == "NoteList 导入":
            result.project.metadata.title = Path(filename).stem.removesuffix(".notelist") or "未命名曲目"
        result.project.metadata.source_name = filename
        result.project.unknown_data["import_warnings"] = result.warnings
        return app.state.store.save(result.project, touch=False)

    @app.post("/api/import/notelist", response_model=SongProject, status_code=201)
    async def import_notelist(
        file: UploadFile = File(...),
        difficulty: DifficultyId = Query(DifficultyId.hard),
    ) -> SongProject:
        filename, payload = await read_json_upload(file)
        try:
            result = notelist_adapter.parse_with_warnings(payload, difficulty)
        except NoteListFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result.project.metadata.title = Path(filename).stem.removesuffix(".notelist") or "未命名曲目"
        result.project.metadata.source_name = filename
        result.project.unknown_data["import_warnings"] = result.warnings
        return app.state.store.save(result.project, touch=False)

    @app.get("/api/projects/{project_id}/export/bmson")
    def export_bmson(
        project_id: str,
        difficulty: DifficultyId = Query(DifficultyId.hard),
    ) -> Response:
        project = get_project(project_id)
        issues = validate_project(project, difficulty)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise HTTPException(status_code=409, detail=[issue.model_dump(mode="json") for issue in errors])
        payload = bmson_adapter.build(project, difficulty)
        stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in project.metadata.title)[:64]
        filename = f"{stem or 'chart'}-{difficulty.value}.bmson"
        ascii_name = f"chart-{difficulty.value}.bmson"
        return Response(
            payload,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
                )
            },
        )

    @app.get("/api/projects/{project_id}/export/notelist")
    def export_notelist(
        project_id: str,
        difficulty: DifficultyId = Query(DifficultyId.hard),
        tpb: int = Query(48, ge=1, le=9600),
    ) -> Response:
        project = get_project(project_id)
        issues = validate_project(project, difficulty)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise HTTPException(status_code=409, detail=[issue.model_dump(mode="json") for issue in errors])
        try:
            payload = notelist_adapter.build(project, difficulty, tpb=tpb)
        except NoteListFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in project.metadata.title)[:64]
        filename = f"{stem or 'chart'}-{difficulty.value}.notelist.json"
        ascii_name = f"chart-{difficulty.value}.notelist.json"
        return Response(
            payload,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
                )
            },
        )

    @app.post("/api/import/bms/inspect")
    async def inspect_bms(
        file: UploadFile = File(...),
        encoding: str | None = Form(None),
    ) -> dict[str, object]:
        filename, payload = await read_bms_upload(file)
        try:
            inspection = bms_adapter.inspect(payload, encoding or None)
        except BmsFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inspection["filename"] = filename
        return inspection

    @app.post("/api/import/bms", response_model=SongProject, status_code=201)
    async def import_bms(
        file: UploadFile = File(...),
        resources: list[UploadFile] = File(default=[]),
        difficulty: DifficultyId = Query(DifficultyId.hard),
        encoding: str | None = Form(None),
        lane_map: str = Form("{}"),
        random_values: str = Form("{}"),
        preserve_unmapped: bool = Form(True),
        resource_paths: str = Form("[]"),
    ) -> SongProject:
        filename, payload = await read_bms_upload(file)
        parsed_lane_map = parse_int_map(lane_map, "Lane 映射")
        parsed_random_values = parse_int_map(random_values, "RANDOM 分支")
        try:
            result = bms_adapter.parse_with_warnings(
                payload,
                difficulty,
                encoding=encoding or None,
                lane_map={str(key).upper(): value for key, value in parsed_lane_map.items()},
                random_values={int(key): value for key, value in parsed_random_values.items()},
                preserve_unmapped=preserve_unmapped,
            )
        except (BmsFormatError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result.project.metadata.source_name = filename
        await attach_bms_key_sounds(result.project, result.warnings, resources, resource_paths)
        await attach_bms_bga(result.project, result.warnings, resources, resource_paths)
        result.project.unknown_data["import_warnings"] = result.warnings
        return app.state.store.save(result.project, touch=False)

    @app.get("/api/projects/{project_id}/export/bms")
    def export_bms(
        project_id: str,
        difficulty: DifficultyId = Query(DifficultyId.hard),
        encoding: str = Query("utf-8"),
    ) -> Response:
        project = get_project(project_id)
        issues = validate_project(project, difficulty)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise HTTPException(status_code=409, detail=[issue.model_dump(mode="json") for issue in errors])
        try:
            payload = bms_adapter.build(project, difficulty, encoding=encoding)
        except BmsFormatError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in project.metadata.title)[:64]
        filename = f"{stem or 'chart'}-{difficulty.value}.bms"
        ascii_name = f"chart-{difficulty.value}.bms"
        return Response(
            payload,
            media_type="text/plain",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
                )
            },
        )

    @app.get(
        "/api/projects/{project_id}/compatibility/bms",
        response_model=list[ValidationIssue],
    )
    def bms_compatibility_report(
        project_id: str,
        difficulty: DifficultyId = Query(DifficultyId.hard),
    ) -> list[ValidationIssue]:
        return bms_adapter.compatibility_report(get_project(project_id), difficulty)

    @app.post("/api/projects/{project_id}/validate", response_model=list[ValidationIssue])
    def validate_saved_project(
        project_id: str,
        difficulty: DifficultyId | None = Query(None),
    ) -> list[ValidationIssue]:
        return validate_project(get_project(project_id), difficulty)

    async def read_bms_upload(file: UploadFile) -> tuple[str, bytes]:
        filename = Path(file.filename or "chart.bms").name
        if not filename.lower().endswith(BmsAdapter.FILE_EXTENSIONS):
            raise HTTPException(status_code=415, detail="仅支持 .bms、.bme、.bml 或 .pms 文件")
        payload = await file.read(10 * 1024 * 1024 + 1)
        if len(payload) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="BMS 文件不得超过 10 MB")
        return filename, payload

    async def read_json_upload(file: UploadFile) -> tuple[str, bytes]:
        filename = Path(file.filename or "chart.json").name
        if not filename.lower().endswith((".bmson", ".json")):
            raise HTTPException(status_code=415, detail="仅支持 .bmson 或 .json 文件")
        payload = await file.read(10 * 1024 * 1024 + 1)
        if len(payload) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="JSON 谱面文件不得超过 10 MB")
        return filename, payload

    def parse_int_map(raw: str, label: str) -> dict[str, int]:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return {str(key): int(item) for key, item in value.items()}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{label}必须是整数值 JSON 对象") from exc

    return app


app = create_app()
