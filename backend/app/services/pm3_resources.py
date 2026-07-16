from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import AudioAsset, SongProject
from ..storage import ProjectAssetError, ProjectStore


PM3_AUDIO_SUFFIXES = {".aif", ".aiff", ".flac", ".mp3", ".ogg", ".wav"}
PM3_MV_IDS = tuple(value for value in range(20) if value != 17)
MAX_PM3_AUDIO_BYTES = 256 * 1024 * 1024


class Pm3ResourceError(ValueError):
    pass


def prepare_pm3_audio(
    store: ProjectStore,
    project: SongProject,
    *,
    filename: str,
    payload: bytes,
    preview_start: float,
    preview_duration: float,
) -> SongProject:
    source_name = Path(filename).name or "music"
    suffix = Path(source_name).suffix.casefold()
    if suffix not in PM3_AUDIO_SUFFIXES:
        raise Pm3ResourceError("主音乐仅支持 WAV、OGG、MP3、FLAC 或 AIFF")
    if not payload:
        raise Pm3ResourceError("主音乐文件不能为空")
    if len(payload) > MAX_PM3_AUDIO_BYTES:
        raise Pm3ResourceError("主音乐文件不得超过 256 MB")
    if preview_start < 0:
        raise Pm3ResourceError("试听起点不能小于 0 秒")
    if not 1 <= preview_duration <= 60:
        raise Pm3ResourceError("试听长度必须在 1 到 60 秒之间")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise Pm3ResourceError("未找到 ffmpeg/ffprobe，无法生成 PM3 音频资源")

    try:
        with tempfile.TemporaryDirectory(prefix="bmson2pm-pm3-audio-") as directory:
            temporary = Path(directory)
            source = temporary / f"source{suffix}"
            background = temporary / "background.ogg"
            preview = temporary / "preview.wav"
            source.write_bytes(payload)
            duration = _probe_duration(ffprobe, source)
            if preview_start >= duration:
                raise Pm3ResourceError(
                    f"试听起点 {preview_start:g} 秒超出音频长度 {duration:.2f} 秒"
                )
            effective_preview_duration = min(preview_duration, duration - preview_start)
            _run_ffmpeg(
                ffmpeg,
                [
                    "-i", str(source), "-map_metadata", "-1", "-vn",
                    "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5",
                    str(background),
                ],
            )
            _run_ffmpeg(
                ffmpeg,
                [
                    "-i", str(source), "-ss", f"{preview_start:.6f}",
                    "-t", f"{effective_preview_duration:.6f}", "-map_metadata", "-1", "-vn",
                    "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(preview),
                ],
            )
            background_payload = background.read_bytes()
            preview_payload = preview.read_bytes()
    except OSError as exc:
        raise Pm3ResourceError(f"生成 PM3 音频资源失败：{exc}") from exc

    source_path = f"pm3-package/source{suffix}"
    background_path = "pm3-package/background.ogg"
    preview_path = "pm3-package/preview.wav"
    try:
        store.save_asset(project.id, source_path, payload)
        store.save_asset(project.id, background_path, background_payload)
        store.save_asset(project.id, preview_path, preview_payload)
    except (OSError, ProjectAssetError) as exc:
        raise Pm3ResourceError(f"保存 PM3 音频资源失败：{exc}") from exc

    updated = project.model_copy(deep=True)
    updated.metadata.audio_duration = duration
    updated.metadata.preview_time = preview_start
    existing = {
        _pm3_asset_role(asset): asset
        for asset in updated.audio_assets
        if _pm3_asset_role(asset) in {"background", "preview"}
    }
    updated.audio_assets = [
        asset for asset in updated.audio_assets
        if _pm3_asset_role(asset) not in {"background", "preview"}
    ]
    background_resource = _resource_ref(updated.id, background_path, background_payload)
    preview_resource = _resource_ref(updated.id, preview_path, preview_payload)
    background_id = existing["background"].id if "background" in existing else str(uuid4())
    preview_id = existing["preview"].id if "preview" in existing else str(uuid4())
    updated.audio_assets.extend([
        AudioAsset(
            id=background_id,
            name="PM3 Background Music",
            filename=background_path,
            duration=duration,
            sample_rate=44100,
            extensions={"pm3_package": {"role": "background", "resource": background_resource}},
        ),
        AudioAsset(
            id=preview_id,
            name="PM3 Preview",
            filename=preview_path,
            duration=effective_preview_duration,
            sample_rate=44100,
            extensions={"pm3_package": {"role": "preview", "resource": preview_resource}},
        ),
    ])
    updated.source_files = [
        item for item in updated.source_files
        if not isinstance(item, dict) or item.get("role") not in {
            "pm3-package-source", "pm3-package-background", "pm3-package-preview",
        }
    ]
    updated.source_files.extend([
        {
            "role": "pm3-package-source",
            "project_id": updated.id,
            "path": source_path,
            "filename": source_name,
            "exists": True,
            "size": len(payload),
        },
        {"role": "pm3-package-background", **background_resource},
        {"role": "pm3-package-preview", **preview_resource},
    ])
    package = updated.game_specific_data.get("pm3_package")
    package = dict(package) if isinstance(package, dict) else {}
    package["audio"] = {
        "source_name": source_name,
        "source": {"project_id": updated.id, "path": source_path, "exists": True},
        "background": background_resource,
        "preview": preview_resource,
        "duration": round(duration, 6),
        "preview_start": round(preview_start, 6),
        "preview_duration": round(effective_preview_duration, 6),
        "background_format": "Ogg Vorbis, 44.1 kHz, stereo",
        "preview_format": "PCM S16LE WAV, 44.1 kHz, stereo",
    }
    updated.game_specific_data["pm3_package"] = package
    return store.save(updated)


def _pm3_asset_role(asset: AudioAsset) -> str | None:
    package = asset.extensions.get("pm3_package")
    return str(package.get("role")) if isinstance(package, dict) and package.get("role") else None


def _resource_ref(project_id: str, path: str, payload: bytes) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "path": path,
        "exists": True,
        "size": len(payload),
    }


def _probe_duration(ffprobe: str, source: Path) -> float:
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(source),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise Pm3ResourceError("ffprobe 分析音频超时") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise Pm3ResourceError(detail[-1] if detail else "无法读取音频信息")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Pm3ResourceError("无法确定主音乐长度") from exc
    if duration <= 0:
        raise Pm3ResourceError("主音乐长度必须大于 0 秒")
    return duration


def _run_ffmpeg(ffmpeg: str, arguments: list[str]) -> None:
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-y", *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise Pm3ResourceError("ffmpeg 处理音频超时") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise Pm3ResourceError(detail[-1] if detail else "ffmpeg 处理音频失败")
