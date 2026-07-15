from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
import shutil
import subprocess
import tempfile

from ..models import SongProject


AUDIO_SUFFIXES = {".wav", ".ogg", ".mp3", ".flac", ".aif", ".aiff"}
IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_SUFFIXES = {".avi", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ogv", ".webm"}
VISUAL_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
BROWSER_VIDEO_SUFFIXES = {".mp4", ".ogv", ".webm"}


def normalize_bms_resource_path(raw_path: str) -> str:
    cleaned = raw_path.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    path = PurePosixPath(cleaned)
    if (
        not cleaned
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"BMS 资源路径无效：{raw_path}")
    return path.as_posix()


@dataclass(frozen=True)
class BmsResourceMatches:
    by_asset_id: dict[str, str]
    exact_count: int
    extension_fallback_count: int
    missing_count: int


def visual_resource_kind(filename: str) -> str:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.casefold()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "unsupported"


def create_browser_video_preview(payload: bytes, suffix: str) -> tuple[bytes | None, str | None]:
    """Remux compatible video when possible, then fall back to H.264 transcoding."""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None, "未找到 ffmpeg，无法生成浏览器视频预览"
    normalized_suffix = suffix.casefold() if suffix.startswith(".") else f".{suffix.casefold()}"
    try:
        with tempfile.TemporaryDirectory(prefix="bmson2pm-bga-") as directory:
            source = PurePosixPath(f"source{normalized_suffix}").name
            source_path = f"{directory}/{source}"
            preview_path = f"{directory}/preview.mp4"
            with open(source_path, "wb") as handle:
                handle.write(payload)
            commands = (
                [
                    ffmpeg, "-v", "error", "-y", "-i", source_path,
                    "-map", "0:v:0", "-an", "-c:v", "copy",
                    "-movflags", "+faststart", preview_path,
                ],
                [
                    ffmpeg, "-v", "error", "-y", "-i", source_path,
                    "-map", "0:v:0", "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", preview_path,
                ],
            )
            errors: list[str] = []
            for command in commands:
                try:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        check=False,
                        timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    errors.append("ffmpeg 处理超时")
                    continue
                if result.returncode == 0:
                    with open(preview_path, "rb") as handle:
                        preview = handle.read()
                    if preview:
                        return preview, None
                message = result.stderr.decode("utf-8", errors="replace").strip()
                errors.append(message.splitlines()[-1] if message else "ffmpeg 处理失败")
            return None, errors[-1]
    except OSError as exc:
        return None, str(exc)


def match_bms_key_sound_resources(
    project: SongProject,
    candidate_paths: list[str],
) -> BmsResourceMatches:
    normalized = list(dict.fromkeys(normalize_bms_resource_path(path) for path in candidate_paths))
    exact: dict[str, str] = {}
    by_parent_stem: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_stem: dict[str, list[str]] = defaultdict(list)
    for path_text in normalized:
        path = PurePosixPath(path_text)
        if path.suffix.casefold() not in AUDIO_SUFFIXES:
            continue
        exact.setdefault(path_text.casefold(), path_text)
        by_parent_stem[(path.parent.as_posix().casefold(), path.stem.casefold())].append(path_text)
        by_stem[path.stem.casefold()].append(path_text)

    matches: dict[str, str] = {}
    exact_count = 0
    fallback_count = 0
    for asset in project.key_sounds:
        try:
            declared_text = normalize_bms_resource_path(asset.filename)
        except ValueError:
            continue
        declared = PurePosixPath(declared_text)
        matched = exact.get(declared_text.casefold())
        if matched:
            exact_count += 1
        else:
            candidates = by_parent_stem.get(
                (declared.parent.as_posix().casefold(), declared.stem.casefold()),
                [],
            )
            if not candidates:
                global_candidates = by_stem.get(declared.stem.casefold(), [])
                if len(global_candidates) == 1:
                    candidates = global_candidates
            if candidates:
                matched = sorted(
                    candidates,
                    key=lambda item: (
                        PurePosixPath(item).suffix.casefold() != declared.suffix.casefold(),
                        item.casefold(),
                    ),
                )[0]
                fallback_count += 1
        if matched:
            matches[asset.id] = matched

    return BmsResourceMatches(
        by_asset_id=matches,
        exact_count=exact_count,
        extension_fallback_count=fallback_count,
        missing_count=max(0, len(project.key_sounds) - len(matches)),
    )


def match_bms_bga_resources(
    project: SongProject,
    candidate_paths: list[str],
) -> BmsResourceMatches:
    bga = project.mv_configuration.get("bms_bga")
    definitions = bga.get("bmp_defs") if isinstance(bga, dict) else None
    bmp_defs = definitions if isinstance(definitions, dict) else {}
    normalized = list(dict.fromkeys(normalize_bms_resource_path(path) for path in candidate_paths))
    exact: dict[str, str] = {}
    by_parent_stem: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_stem: dict[str, list[str]] = defaultdict(list)
    for path_text in normalized:
        path = PurePosixPath(path_text)
        exact.setdefault(path_text.casefold(), path_text)
        if path.suffix.casefold() in VISUAL_SUFFIXES:
            by_parent_stem[(path.parent.as_posix().casefold(), path.stem.casefold())].append(path_text)
            by_stem[path.stem.casefold()].append(path_text)

    matches: dict[str, str] = {}
    exact_count = 0
    fallback_count = 0
    for raw_id, raw_filename in bmp_defs.items():
        bmp_id = str(raw_id).upper()
        try:
            declared_text = normalize_bms_resource_path(str(raw_filename))
        except ValueError:
            continue
        declared = PurePosixPath(declared_text)
        matched = exact.get(declared_text.casefold())
        if matched:
            exact_count += 1
        else:
            candidates = by_parent_stem.get(
                (declared.parent.as_posix().casefold(), declared.stem.casefold()),
                [],
            )
            if not candidates:
                global_candidates = by_stem.get(declared.stem.casefold(), [])
                if len(global_candidates) == 1:
                    candidates = global_candidates
            if candidates:
                matched = sorted(
                    candidates,
                    key=lambda item: (
                        PurePosixPath(item).suffix.casefold() != declared.suffix.casefold(),
                        item.casefold(),
                    ),
                )[0]
                fallback_count += 1
        if matched:
            matches[bmp_id] = matched

    return BmsResourceMatches(
        by_asset_id=matches,
        exact_count=exact_count,
        extension_fallback_count=fallback_count,
        missing_count=max(0, len(bmp_defs) - len(matches)),
    )
