from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import AudioAsset, SongProject
from ..storage import ProjectAssetError, ProjectStore


PM3_AUDIO_SUFFIXES = {".aif", ".aiff", ".flac", ".mp3", ".ogg", ".wav"}
PM3_MV_IDS = tuple(value for value in range(20) if value != 17)
MAX_PM3_AUDIO_BYTES = 256 * 1024 * 1024
MAX_PM3_KEY_SOUND_BYTES = 128 * 1024 * 1024
MAX_PM3_MV_BYTES = 128 * 1024 * 1024
PM3_CUSTOM_MV_IDS = tuple(range(20, 100))
PM3_MV_WIDTH = 656
PM3_MV_HEIGHT = 488


class Pm3ResourceError(ValueError):
    pass


def inspect_pm3_mv_swf(
    payload: bytes,
    *,
    require_state_labels: bool = True,
) -> dict[str, Any]:
    if len(payload) < 16 or len(payload) > MAX_PM3_MV_BYTES:
        raise Pm3ResourceError("PM3 MV SWF 必须在 16 字节到 128 MB 之间")
    signature = payload[:3]
    if signature not in {b"FWS", b"CWS"}:
        raise Pm3ResourceError("PM3 MV 仅支持 FWS/CWS，不支持 ZWS 或其他容器")
    version = payload[3]
    if version not in {8, 9}:
        raise Pm3ResourceError("PM3 MV 必须使用 SWF 8 或 9")
    declared_length = int.from_bytes(payload[4:8], "little")
    if declared_length < 16 or declared_length > MAX_PM3_MV_BYTES:
        raise Pm3ResourceError("PM3 MV SWF 声明长度无效")
    if signature == b"CWS":
        decompressor = zlib.decompressobj()
        try:
            body_limit = MAX_PM3_MV_BYTES - 8
            body = decompressor.decompress(payload[8:], body_limit + 1)
            if len(body) > body_limit:
                raise Pm3ResourceError("PM3 MV SWF 解压后超过 128 MB")
            body += decompressor.flush(body_limit - len(body) + 1)
        except zlib.error as exc:
            raise Pm3ResourceError(f"PM3 MV SWF 压缩数据无效：{exc}") from exc
        if len(body) > body_limit:
            raise Pm3ResourceError("PM3 MV SWF 解压后超过 128 MB")
        if not decompressor.eof or decompressor.unused_data:
            raise Pm3ResourceError("PM3 MV SWF 压缩数据不完整")
    else:
        body = payload[8:]
    if len(body) + 8 != declared_length:
        raise Pm3ResourceError("PM3 MV SWF 实际长度与文件头不一致")

    nbits = _swf_bits(body, 0, 5)
    if nbits < 1 or nbits > 31:
        raise Pm3ResourceError("PM3 MV SWF 舞台矩形无效")
    bit_offset = 5
    coordinates = []
    for _ in range(4):
        value = _swf_bits(body, bit_offset, nbits)
        bit_offset += nbits
        if value & (1 << (nbits - 1)):
            value -= 1 << nbits
        coordinates.append(value)
    rect_bytes = (bit_offset + 7) // 8
    if rect_bytes + 4 > len(body):
        raise Pm3ResourceError("PM3 MV SWF 时间轴头不完整")
    xmin, xmax, ymin, ymax = coordinates
    width = (xmax - xmin) / 20
    height = (ymax - ymin) / 20
    if width != PM3_MV_WIDTH or height != PM3_MV_HEIGHT:
        raise Pm3ResourceError(
            f"PM3 MV 舞台必须为 {PM3_MV_WIDTH}x{PM3_MV_HEIGHT}，当前为 {width:g}x{height:g}"
        )
    frame_rate = int.from_bytes(body[rect_bytes:rect_bytes + 2], "little") / 256
    frame_count = int.from_bytes(body[rect_bytes + 2:rect_bytes + 4], "little")

    labels: set[str] = set()
    has_as3 = False
    offset = rect_bytes + 4
    saw_end = False
    while offset + 2 <= len(body):
        header = int.from_bytes(body[offset:offset + 2], "little")
        offset += 2
        tag_code = header >> 6
        tag_length = header & 0x3F
        if tag_length == 0x3F:
            if offset + 4 > len(body):
                raise Pm3ResourceError("PM3 MV SWF 标签长度不完整")
            tag_length = int.from_bytes(body[offset:offset + 4], "little")
            offset += 4
        end = offset + tag_length
        if end > len(body):
            raise Pm3ResourceError("PM3 MV SWF 标签越过文件结尾")
        tag = body[offset:end]
        if tag_code == 82:
            has_as3 = True
        elif tag_code == 69 and len(tag) >= 4:
            has_as3 = has_as3 or bool(int.from_bytes(tag[:4], "little") & 0x08)
        elif tag_code == 43:
            label = tag.split(b"\0", 1)[0].decode("ascii", errors="ignore")
            if label:
                labels.add(label)
        offset = end
        if tag_code == 0:
            saw_end = True
            break
    if not saw_end:
        raise Pm3ResourceError("PM3 MV SWF 缺少 End 标签")
    if has_as3:
        raise Pm3ResourceError("PM3 Scaleform 运行时只支持 AS2 MV，不能包含 AS3 DoABC")
    if require_state_labels:
        required_labels = {"low", "middle", "high", "full"}
        missing_labels = sorted(required_labels - labels)
        if missing_labels:
            raise Pm3ResourceError(
                "PM3 MV 缺少控制器状态帧：" + "、".join(missing_labels)
            )
    return {
        "signature": signature.decode("ascii"),
        "version": version,
        "width": PM3_MV_WIDTH,
        "height": PM3_MV_HEIGHT,
        "frame_rate": frame_rate,
        "frame_count": frame_count,
        "labels": sorted(labels),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "as2_compatible": True,
    }


def build_pm3_mv_state_preview(payload: bytes, state: str) -> bytes:
    inspection = inspect_pm3_mv_swf(payload)
    if state not in {"low", "middle", "high", "full"}:
        raise Pm3ResourceError("PM3 MV 预览状态无效")
    if state not in inspection["labels"]:
        raise Pm3ResourceError(f"PM3 MV 不包含 {state} 状态帧")

    if payload[:3] == b"CWS":
        try:
            body = zlib.decompress(payload[8:])
        except zlib.error as exc:
            raise Pm3ResourceError(f"PM3 MV SWF 压缩数据无效：{exc}") from exc
    else:
        body = payload[8:]

    nbits = _swf_bits(body, 0, 5)
    tags_offset = (5 + nbits * 4 + 7) // 8 + 4
    # PM3 MV files define each state on a separate root-timeline frame. The
    # images and sprites for later states are also defined inside those frames,
    # so an ActionGoToLabel inserted into frame zero runs before a large SWF has
    # loaded the destination frame. Flatten all tags through the requested
    # frame into one frame instead. This produces a self-contained preview and
    # does not depend on AVM1 loading timing or ExternalInterface support.
    offset = tags_offset
    frame = 0
    target_frame: int | None = None
    preview_tags = bytearray()
    while offset + 2 <= len(body):
        tag_start = offset
        header = int.from_bytes(body[offset:offset + 2], "little")
        offset += 2
        tag_code = header >> 6
        tag_length = header & 0x3F
        if tag_length == 0x3F:
            if offset + 4 > len(body):
                break
            tag_length = int.from_bytes(body[offset:offset + 4], "little")
            offset += 4
        tag_end = offset + tag_length
        if tag_end > len(body):
            raise Pm3ResourceError("PM3 MV SWF 标签越过文件结尾")
        tag = body[offset:tag_end]
        offset = tag_end

        if tag_code == 43:
            label = tag.split(b"\0", 1)[0].decode("ascii", errors="ignore")
            if label == state:
                target_frame = frame
        elif tag_code not in {0, 1, 12}:
            preview_tags.extend(body[tag_start:tag_end])

        if tag_code == 1:
            if target_frame == frame:
                break
            frame += 1
        elif tag_code == 0:
            break
    else:
        raise Pm3ResourceError("PM3 MV SWF 缺少 End 标签")

    if target_frame is None or frame != target_frame:
        raise Pm3ResourceError(f"PM3 MV 无法定位 {state} 状态帧")

    timeline_header = bytearray(body[:tags_offset])
    timeline_header[tags_offset - 2:tags_offset] = (1).to_bytes(2, "little")
    stop_action = ((12 << 6) | 2).to_bytes(2, "little") + b"\x07\0"
    show_frame = (1 << 6).to_bytes(2, "little")
    preview_body = bytes(timeline_header + preview_tags + stop_action + show_frame + b"\0\0")
    declared_length = len(preview_body) + 8
    header = payload[:4] + declared_length.to_bytes(4, "little")
    if payload[:3] == b"CWS":
        return header + zlib.compress(preview_body)
    return header + preview_body


def prepare_pm3_mv(
    store: ProjectStore,
    project: SongProject,
    *,
    filename: str,
    payload: bytes,
    mv_id: int,
) -> SongProject:
    if mv_id not in PM3_CUSTOM_MV_IDS:
        raise Pm3ResourceError("自定义 PM3 MV ID 必须在 20..99")
    source_name = Path(filename).name or f"mv{mv_id}.swf"
    if Path(source_name).suffix.casefold() != ".swf":
        raise Pm3ResourceError("自定义 PM3 MV 必须是 .swf 文件")
    inspection = inspect_pm3_mv_swf(payload)
    relative = f"pm3-package/mv/mv{mv_id}.swf"
    try:
        stored_path = store.save_asset(project.id, relative, payload)
    except (OSError, ProjectAssetError) as exc:
        raise Pm3ResourceError(f"保存 PM3 MV 失败：{exc}") from exc

    updated = project.model_copy(deep=True)
    package = updated.game_specific_data.get("pm3_package")
    package = dict(package) if isinstance(package, dict) else {}
    resource = {
        "project_id": updated.id,
        "path": stored_path,
        "exists": True,
        "size": len(payload),
    }
    package["mv"] = {
        "id": mv_id,
        "source_name": source_name,
        "resource": resource,
        "output_path": f"media/ui/mv/mv{mv_id}.swf",
        "bundle": 6,
        "inspection": inspection,
    }
    updated.game_specific_data["pm3_package"] = package
    updated.mv_configuration["pm3_mv_id"] = mv_id
    updated.source_files = [
        item for item in updated.source_files
        if not isinstance(item, dict) or item.get("role") != "pm3-package-mv"
    ]
    updated.source_files.append({
        "role": "pm3-package-mv",
        **resource,
        "filename": source_name,
        "mv_id": mv_id,
    })
    return store.save(updated)


def _swf_bits(payload: bytes, bit_offset: int, length: int) -> int:
    if bit_offset + length > len(payload) * 8:
        raise Pm3ResourceError("PM3 MV SWF 位字段不完整")
    value = 0
    for index in range(length):
        absolute = bit_offset + index
        value = (value << 1) | (
            (payload[absolute // 8] >> (7 - absolute % 8)) & 1
        )
    return value


def convert_pm3_key_sound(source: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise Pm3ResourceError("未找到 ffmpeg，无法生成 PM3 Key 音")
    try:
        with tempfile.TemporaryDirectory(prefix="bmson2pm-pm3-key-") as directory:
            output = Path(directory) / "key.wav"
            _run_ffmpeg(
                ffmpeg,
                [
                    "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1", "-vn",
                    "-ar", "44100", "-c:a", "pcm_s16le", str(output),
                ],
            )
            payload = output.read_bytes()
    except OSError as exc:
        raise Pm3ResourceError(f"生成 PM3 Key 音失败：{exc}") from exc
    if not payload.startswith(b"RIFF") or len(payload) > MAX_PM3_KEY_SOUND_BYTES:
        raise Pm3ResourceError("生成的 PM3 Key 音不是有效的小型 PCM WAV")
    return payload


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
    vorbis_arguments = _vorbis_encoder_arguments(ffmpeg)

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
                    "-ac", "2", "-ar", "44100", *vorbis_arguments,
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


def _vorbis_encoder_arguments(ffmpeg: str) -> list[str]:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Pm3ResourceError(f"无法检查 ffmpeg Vorbis 编码器：{exc}") from exc
    encoders = result.stdout
    if re.search(r"(?m)^\s*A\S*\s+libvorbis\s", encoders):
        return ["-c:a", "libvorbis", "-q:a", "5"]
    if re.search(r"(?m)^\s*A\S*\s+vorbis\s", encoders):
        return ["-c:a", "vorbis", "-strict", "experimental", "-q:a", "5"]
    raise Pm3ResourceError("当前 ffmpeg 没有可用的 Vorbis 编码器")


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
