from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any


CHANNEL_RE = re.compile(r"^#(?P<measure>\d{3})(?P<channel>[0-9A-Za-z]{2}):(?P<data>\S+)\s*$")
HEADER_RE = re.compile(r"^#(?P<command>[0-9A-Za-z]+)(?:\s+(?P<value>.*))?$")
CONTROL_RE = re.compile(r"^#(?P<command>RANDOM|SETRANDOM|IF|ELSEIF|ELSE|ENDIF|ENDRANDOM)(?:\s+(?P<value>.*))?$", re.I)
BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SUPPORTED_ENCODINGS = ("utf-8-sig", "utf-8", "cp932", "gb18030", "big5", "euc_jp", "latin-1")
KNOWN_HEADERS = {
    "TITLE", "SUBTITLE", "GENRE", "ARTIST", "SUBARTIST", "STAGEFILE", "BANNER", "BACKBMP",
    "PLAYER", "RANK", "TOTAL", "VOLWAV", "PLAYLEVEL", "DIFFICULTY", "BPM", "LNTYPE", "LNOBJ",
}


@dataclass(frozen=True)
class EncodingCandidate:
    encoding: str
    label: str
    preview: str


@dataclass(frozen=True)
class BmsObject:
    measure: int
    channel: str
    position: Fraction
    value: str
    line_number: int
    raw_line: str


@dataclass
class BmsDocument:
    encoding: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    wav_defs: dict[str, str] = field(default_factory=dict)
    bmp_defs: dict[str, str] = field(default_factory=dict)
    bpm_defs: dict[str, Fraction] = field(default_factory=dict)
    stop_defs: dict[str, Fraction] = field(default_factory=dict)
    measure_lengths: dict[int, Fraction] = field(default_factory=dict)
    objects: list[BmsObject] = field(default_factory=list)
    unknown_lines: list[dict[str, Any]] = field(default_factory=list)
    control_lines: list[dict[str, Any]] = field(default_factory=list)
    random_blocks: list[dict[str, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    max_measure: int = 0


def encoding_candidates(payload: bytes) -> list[EncodingCandidate]:
    encodings = list(SUPPORTED_ENCODINGS)
    if not payload.startswith(b"\xef\xbb\xbf"):
        encodings.remove("utf-8-sig")
        encodings.insert(1, "utf-8-sig")
    candidates: list[EncodingCandidate] = []
    seen_text: set[str] = set()
    labels = {
        "utf-8-sig": "UTF-8 (BOM)",
        "utf-8": "UTF-8",
        "cp932": "CP932 / Shift_JIS",
        "gb18030": "GB18030",
        "big5": "Big5",
        "euc_jp": "EUC-JP",
        "latin-1": "Latin-1 fallback",
    }
    for encoding in encodings:
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        signature = text[:2048]
        if signature in seen_text:
            continue
        seen_text.add(signature)
        preview_lines = [line.strip() for line in text.splitlines() if line.strip()][:6]
        preview = "\n".join(preview_lines)[:600]
        candidates.append(EncodingCandidate(encoding, labels[encoding], preview))
    return candidates


def decode_bms(payload: bytes, encoding: str | None = None) -> tuple[str, str, list[EncodingCandidate]]:
    candidates = encoding_candidates(payload)
    selected = encoding or (candidates[0].encoding if candidates else "")
    if selected not in SUPPORTED_ENCODINGS:
        raise ValueError(f"不支持的 BMS 编码：{selected}")
    try:
        return payload.decode(selected), selected, candidates
    except UnicodeDecodeError as exc:
        raise ValueError(f"无法使用 {selected} 解码 BMS：字节位置 {exc.start}") from exc


def parse_bms_text(
    text: str,
    encoding: str,
    random_values: dict[int, int] | None = None,
) -> BmsDocument:
    document = BmsDocument(encoding=encoding, text=text)
    active_lines = _preprocess_control(text, document, random_values or {})
    for line_number, raw_line in active_lines:
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            document.unknown_lines.append({"line": line_number, "text": raw_line, "kind": "comment"})
            continue
        channel_match = CHANNEL_RE.match(line)
        if channel_match:
            _parse_channel_line(document, line_number, raw_line, channel_match)
            continue
        header_match = HEADER_RE.match(line)
        if not header_match:
            document.unknown_lines.append({"line": line_number, "text": raw_line, "kind": "malformed"})
            document.warnings.append(f"第 {line_number} 行无法识别，已原样保留")
            continue
        command = header_match.group("command").upper()
        value = (header_match.group("value") or "").strip()
        if _parse_definition(document, command, value, line_number):
            continue
        if command in KNOWN_HEADERS:
            document.headers[command] = value
            continue
        document.unknown_lines.append({"line": line_number, "text": raw_line, "kind": "command"})
        document.warnings.append(f"未知指令 #{command} 已原样保留")
    return document


def base36_value(value: str) -> int:
    if len(value) != 2:
        raise ValueError(value)
    high = BASE36.find(value[0].upper())
    low = BASE36.find(value[1].upper())
    if high < 0 or low < 0:
        raise ValueError(value)
    return high * 36 + low


def base36_code(value: int) -> str:
    if not 0 <= value < 36 * 36:
        raise ValueError(value)
    return BASE36[value // 36] + BASE36[value % 36]


def normalized_play_channel(channel: str) -> str | None:
    channel = channel.upper()
    if channel[0] in "12":
        return channel
    if channel[0] == "5":
        return "1" + channel[1]
    if channel[0] == "6":
        return "2" + channel[1]
    return None


def is_long_channel(channel: str) -> bool:
    return channel[0].upper() in "56"


def _parse_channel_line(document: BmsDocument, line_number: int, raw_line: str, match: re.Match[str]) -> None:
    measure = int(match.group("measure"))
    channel = match.group("channel").upper()
    data = match.group("data").strip()
    document.max_measure = max(document.max_measure, measure)
    if channel == "02":
        try:
            length = Fraction(data)
            if length <= 0:
                raise ValueError
            document.measure_lengths[measure] = length
        except (ValueError, ZeroDivisionError):
            document.warnings.append(f"第 {line_number} 行小节长度无效：{data}")
            document.unknown_lines.append({"line": line_number, "text": raw_line, "kind": "channel"})
        return
    if len(data) % 2:
        document.warnings.append(f"第 {line_number} 行对象列长度不是偶数，已原样保留")
        document.unknown_lines.append({"line": line_number, "text": raw_line, "kind": "channel"})
        return
    count = len(data) // 2
    if count == 0:
        return
    for index in range(count):
        value = data[index * 2:index * 2 + 2].upper()
        if value == "00":
            continue
        try:
            if channel != "03":
                base36_value(value)
            else:
                int(value, 16)
        except ValueError:
            document.warnings.append(f"第 {line_number} 行包含无效对象 {value}")
            continue
        document.objects.append(
            BmsObject(measure, channel, Fraction(index, count), value, line_number, raw_line)
        )


def _parse_definition(document: BmsDocument, command: str, value: str, line_number: int) -> bool:
    for prefix, target in (
        ("WAV", document.wav_defs),
        ("BMP", document.bmp_defs),
        ("BPM", document.bpm_defs),
        ("STOP", document.stop_defs),
    ):
        if len(command) != len(prefix) + 2 or not command.startswith(prefix):
            continue
        code = command[-2:]
        try:
            base36_value(code)
        except ValueError:
            return False
        if prefix in {"WAV", "BMP"}:
            target[code] = value
        else:
            try:
                number = Fraction(value)
                if number <= 0 and prefix == "BPM":
                    raise ValueError
                target[code] = number
            except (ValueError, ZeroDivisionError):
                document.warnings.append(f"第 {line_number} 行 #{prefix}{code} 数值无效")
        return True
    return False


def _preprocess_control(
    text: str,
    document: BmsDocument,
    random_values: dict[int, int],
) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    random_stack: list[int] = [1]
    if_stack: list[dict[str, Any]] = []
    random_index = 0

    def active() -> bool:
        return all(bool(frame["active"]) for frame in if_stack)

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        match = CONTROL_RE.match(raw_line.strip())
        if not match:
            if active():
                result.append((line_number, raw_line))
            continue
        command = match.group("command").upper()
        value_text = (match.group("value") or "").strip()
        document.control_lines.append({"line": line_number, "text": raw_line, "kind": "control"})
        try:
            value = int(value_text) if value_text else 0
        except ValueError:
            value = 0

        if command == "RANDOM":
            random_index += 1
            maximum = max(value, 1)
            selected = min(max(random_values.get(random_index, 1), 1), maximum)
            document.random_blocks.append({"index": random_index, "maximum": maximum, "selected": selected})
            random_stack.append(selected)
        elif command == "SETRANDOM":
            random_stack[-1] = max(value, 1)
        elif command == "IF":
            parent_active = active()
            matched = random_stack[-1] == value
            if_stack.append({"parent": parent_active, "matched": matched, "active": parent_active and matched})
        elif command == "ELSEIF":
            if not if_stack:
                document.warnings.append(f"第 {line_number} 行 #ELSEIF 缺少 #IF")
                continue
            frame = if_stack[-1]
            matched = not frame["matched"] and random_stack[-1] == value
            frame["active"] = frame["parent"] and matched
            frame["matched"] = frame["matched"] or matched
        elif command == "ELSE":
            if not if_stack:
                document.warnings.append(f"第 {line_number} 行 #ELSE 缺少 #IF")
                continue
            frame = if_stack[-1]
            frame["active"] = frame["parent"] and not frame["matched"]
            frame["matched"] = True
        elif command == "ENDIF":
            if if_stack:
                if_stack.pop()
            else:
                document.warnings.append(f"第 {line_number} 行 #ENDIF 缺少 #IF")
        elif command == "ENDRANDOM":
            if len(random_stack) > 1:
                random_stack.pop()
    if if_stack:
        document.warnings.append("BMS 存在未闭合的 #IF 区块")
    if len(random_stack) > 1:
        document.warnings.append("BMS 存在未闭合的 #RANDOM 区块")
    if document.random_blocks:
        document.warnings.append("已按所选 #RANDOM 分支解析；控制指令原文仍保留在项目中")
    return result
