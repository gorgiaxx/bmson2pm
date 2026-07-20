from __future__ import annotations

import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from struct import pack, unpack_from
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image, ImageFont


class Pm3UiError(ValueError):
    pass


@dataclass(frozen=True)
class Pm3UiText:
    character_id: int
    text: str
    style: str


_FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/Adwaita/AdwaitaSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _pillow_modules() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError as exc:
        raise Pm3UiError(
            "缺少 Pillow，无法生成 PM3 歌名/作者 UI；请重新执行 make install"
        ) from exc
    return Image, ImageDraw, ImageFont


def _font_path() -> Path | None:
    configured = os.getenv("BMSON2PM_PM3_UI_FONT")
    candidates = (configured, *_FONT_CANDIDATES) if configured else _FONT_CANDIDATES
    return next((Path(item) for item in candidates if item and Path(item).is_file()), None)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    _, _, image_font = _pillow_modules()
    path = _font_path()
    if path is not None:
        try:
            return image_font.truetype(str(path), size=size)
        except OSError as exc:
            raise Pm3UiError(f"无法加载 PM3 UI 字体：{path}") from exc
    return image_font.load_default(size=size)


def _fit_font(
    text: str,
    width: int,
    height: int,
    *,
    stroke_width: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int]]:
    image, image_draw, _ = _pillow_modules()
    probe = image_draw.Draw(image.new("L", (1, 1)))
    for size in range(max(6, height - 2), 5, -1):
        font = _font(size)
        bounds = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        if (
            bounds[2] - bounds[0] <= max(1, width - 2)
            and bounds[3] - bounds[1] <= max(1, height - 2)
        ):
            return font, bounds
    font = _font(6)
    return font, probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width)


def render_pm3_text(text: str, width: int, height: int, style: str) -> Image.Image:
    image_module, image_draw, _ = _pillow_modules()
    normalized = " ".join(text.strip().split()) or "?"
    image = image_module.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = image_draw.Draw(image)
    outer_stroke = 3 if min(width, height) >= 24 else 2
    font, bounds = _fit_font(
        normalized, width, height, stroke_width=outer_stroke,
    )
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    position = (
        (width - text_width) / 2 - bounds[0],
        (height - text_height) / 2 - bounds[1],
    )
    if style == "songB":
        draw.text(
            position, normalized, font=font, fill=(0, 105, 255, 255),
            stroke_width=outer_stroke, stroke_fill=(0, 45, 145, 255),
        )
        draw.text(
            position, normalized, font=font, fill=(0, 125, 255, 255),
            stroke_width=max(1, outer_stroke - 1), stroke_fill=(255, 255, 255, 255),
        )
    elif style == "songS":
        draw.text(
            position, normalized, font=font, fill=(255, 255, 255, 255),
            stroke_width=outer_stroke, stroke_fill=(16, 16, 16, 255),
        )
    elif style == "singer":
        draw.text(
            position, normalized, font=font, fill=(0, 195, 255, 255),
            stroke_width=outer_stroke, stroke_fill=(0, 65, 175, 255),
        )
        draw.text(
            position, normalized, font=font, fill=(0, 195, 255, 255),
            stroke_width=max(1, outer_stroke - 1), stroke_fill=(255, 255, 255, 255),
        )
    else:
        raise Pm3UiError(f"未知 PM3 UI 文本样式：{style}")
    return image


def _decode_swf(payload: bytes) -> tuple[bytes, int, bool]:
    if len(payload) < 12 or payload[:3] not in {b"CWS", b"FWS"}:
        raise Pm3UiError("PM3 UI 文件不是受支持的 SWF")
    compressed = payload[:3] == b"CWS"
    version = payload[3]
    try:
        body = zlib.decompress(payload[8:]) if compressed else payload[8:]
    except zlib.error as exc:
        raise Pm3UiError("PM3 UI SWF 解压失败") from exc
    return b"FWS" + bytes([version]) + pack("<I", len(body) + 8) + body, version, compressed


def _tag_offset(uncompressed: bytes) -> int:
    if len(uncompressed) < 13:
        raise Pm3UiError("PM3 UI SWF header 不完整")
    nbits = uncompressed[8] >> 3
    rect_bytes = (5 + 4 * nbits + 7) // 8
    offset = 8 + rect_bytes + 4
    if offset > len(uncompressed):
        raise Pm3UiError("PM3 UI SWF RECT 越界")
    return offset


def _encode_tag(code: int, payload: bytes) -> bytes:
    if len(payload) < 0x3F:
        return pack("<H", (code << 6) | len(payload)) + payload
    return pack("<HI", (code << 6) | 0x3F, len(payload)) + payload


def _premultiplied_argb(image: Image.Image) -> bytes:
    output = bytearray()
    rgba = image.convert("RGBA").tobytes()
    for offset in range(0, len(rgba), 4):
        red, green, blue, alpha = rgba[offset:offset + 4]
        output.extend((
            alpha,
            (red * alpha + 127) // 255,
            (green * alpha + 127) // 255,
            (blue * alpha + 127) // 255,
        ))
    return bytes(output)


def patch_pm3_ui_swf(payload: bytes, replacements: list[Pm3UiText]) -> bytes:
    wanted = {item.character_id: item for item in replacements}
    if len(wanted) != len(replacements):
        raise Pm3UiError("PM3 UI bitmap character ID 重复")
    uncompressed, version, was_compressed = _decode_swf(payload)
    offset = _tag_offset(uncompressed)
    output = bytearray(uncompressed[:offset])
    replaced: set[int] = set()
    while offset + 2 <= len(uncompressed):
        header = unpack_from("<H", uncompressed, offset)[0]
        offset += 2
        code = header >> 6
        length = header & 0x3F
        if length == 0x3F:
            if offset + 4 > len(uncompressed):
                raise Pm3UiError("PM3 UI SWF 长 tag header 不完整")
            length = unpack_from("<I", uncompressed, offset)[0]
            offset += 4
        end = offset + length
        if end > len(uncompressed):
            raise Pm3UiError("PM3 UI SWF tag 越界")
        tag = uncompressed[offset:end]
        offset = end
        if code == 36 and len(tag) >= 7:
            character_id = unpack_from("<H", tag)[0]
            replacement = wanted.get(character_id)
            if replacement is not None:
                bitmap_format = tag[2]
                width, height = unpack_from("<HH", tag, 3)
                if bitmap_format != 5:
                    raise Pm3UiError(
                        f"PM3 UI bitmap {character_id} 不是 32-bit Lossless2"
                    )
                rendered = render_pm3_text(replacement.text, width, height, replacement.style)
                tag = tag[:7] + zlib.compress(_premultiplied_argb(rendered), level=9)
                replaced.add(character_id)
        output.extend(_encode_tag(code, tag))
        if code == 0:
            output.extend(uncompressed[offset:])
            break
    missing = sorted(set(wanted) - replaced)
    if missing:
        raise Pm3UiError(
            "PM3 UI SWF 找不到 bitmap character：" + ", ".join(map(str, missing))
        )
    uncompressed_result = bytes(output)
    uncompressed_result = (
        b"FWS" + bytes([version]) + pack("<I", len(uncompressed_result))
        + uncompressed_result[8:]
    )
    if not was_compressed:
        return uncompressed_result
    return (
        b"CWS" + bytes([version]) + pack("<I", len(uncompressed_result))
        + zlib.compress(uncompressed_result[8:], level=9)
    )


def lossless2_bitmap_size(payload: bytes, character_id: int) -> tuple[int, int] | None:
    uncompressed, _, _ = _decode_swf(payload)
    offset = _tag_offset(uncompressed)
    while offset + 2 <= len(uncompressed):
        header = unpack_from("<H", uncompressed, offset)[0]
        offset += 2
        code = header >> 6
        length = header & 0x3F
        if length == 0x3F:
            length = unpack_from("<I", uncompressed, offset)[0]
            offset += 4
        tag = uncompressed[offset:offset + length]
        offset += length
        if code == 36 and len(tag) >= 7 and unpack_from("<H", tag)[0] == character_id:
            return unpack_from("<HH", tag, 3)
        if code == 0:
            return None
    return None
