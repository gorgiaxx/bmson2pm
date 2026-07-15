from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import StringIO

from ..models import DifficultyId


class Pm3ParseError(ValueError):
    pass


DIFFICULTY_BY_CLASS = {
    0: DifficultyId.easy,
    1: DifficultyId.normal,
    2: DifficultyId.hard,
    3: DifficultyId.special,
    4: DifficultyId.master,
}


@dataclass(frozen=True)
class Pm3SongInfo:
    bpm: int
    min_bpm: int
    max_bpm: int
    length: int
    total_hit: int
    max_combo: int
    wav_dir: str
    song_name: str
    singer_name: str
    song_id: int
    singer_id: int
    music_style: int
    hidden: int
    class_id: int
    level: int
    filename: str
    line_number: int
    raw_fields: tuple[str, ...]

    @property
    def difficulty(self) -> DifficultyId | None:
        return DIFFICULTY_BY_CLASS.get(self.class_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "bpm": self.bpm,
            "min_bpm": self.min_bpm,
            "max_bpm": self.max_bpm,
            "length": self.length,
            "total_hit": self.total_hit,
            "max_combo": self.max_combo,
            "wav_dir": self.wav_dir,
            "song_name": self.song_name,
            "singer_name": self.singer_name,
            "song_id": self.song_id,
            "singer_id": self.singer_id,
            "music_style": self.music_style,
            "hidden": self.hidden,
            "class_id": self.class_id,
            "difficulty": self.difficulty.value if self.difficulty else None,
            "level": self.level,
            "filename": self.filename,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class Pm3Event:
    track: int
    tick: int
    wav_index: int
    volume: int
    hold_start: int
    hold_end: int
    hold_number: int
    line_number: int

    def as_dict(self) -> dict[str, int]:
        return {
            "track": self.track,
            "tick": self.tick,
            "wav_index": self.wav_index,
            "volume": self.volume,
            "hold_start": self.hold_start,
            "hold_end": self.hold_end,
            "hold_number": self.hold_number,
            "line_number": self.line_number,
        }


@dataclass
class Pm3ChartDocument:
    bpm_count: int | None = None
    bpm_changes: list[tuple[int, int]] = field(default_factory=list)
    rhythm_count: int | None = None
    rhythm_changes: list[tuple[int, int, int]] = field(default_factory=list)
    events: list[Pm3Event] = field(default_factory=list)
    wav_count: int | None = None
    wavs: dict[int, str] = field(default_factory=dict)
    total_note: int | None = None
    unknown_lines: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""


def decode_pm3_text(payload: bytes) -> tuple[str, str]:
    for encoding in ("ascii", "cp950", "utf-8"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return payload.decode("cp950", errors="replace"), "cp950-replace"


def parse_song_list(payload: bytes) -> tuple[list[Pm3SongInfo], list[str], str]:
    text, encoding = decode_pm3_text(payload)
    rows: list[Pm3SongInfo] = []
    warnings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower() in {"q", "end"}:
            break
        try:
            fields = next(csv.reader(StringIO(line), skipinitialspace=True))
        except csv.Error as exc:
            warnings.append(f"SongList 第 {line_number} 行 CSV 无法解析：{exc}")
            continue
        values = tuple(value.strip() for value in fields)
        if len(values) != 16:
            warnings.append(f"SongList 第 {line_number} 行应有 16 个字段，实际为 {len(values)}")
            continue
        try:
            rows.append(Pm3SongInfo(
                bpm=int(values[0]), min_bpm=int(values[1]), max_bpm=int(values[2]),
                length=int(values[3]), total_hit=int(values[4]), max_combo=int(values[5]),
                wav_dir=values[6], song_name=values[7], singer_name=values[8],
                song_id=int(values[9]), singer_id=int(values[10]), music_style=int(values[11]),
                hidden=int(values[12]), class_id=int(values[13]), level=int(values[14]),
                filename=values[15], line_number=line_number, raw_fields=values,
            ))
        except ValueError:
            warnings.append(f"SongList 第 {line_number} 行含有无效整数")
    if not rows:
        raise Pm3ParseError("SongList 没有可用曲目记录")
    return rows, warnings, encoding


def parse_chart_text(payload: bytes) -> tuple[Pm3ChartDocument, str]:
    text, encoding = decode_pm3_text(payload)
    document = Pm3ChartDocument(raw_text=text)
    current_track: int | None = None
    saw_chart_token = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        token = parts[0]
        lower = token.lower()
        if lower in {"q", "end"}:
            break
        try:
            if token == "ChangeBPMCount" and len(parts) >= 2:
                document.bpm_count = int(parts[1]); saw_chart_token = True
            elif token == "ChangeBPM" and len(parts) >= 3:
                document.bpm_changes.append((int(parts[1]), int(parts[2]))); saw_chart_token = True
            elif token == "ChangeRhythmCount" and len(parts) >= 2:
                document.rhythm_count = int(parts[1]); saw_chart_token = True
            elif token == "ChangeRhythm" and len(parts) >= 4:
                document.rhythm_changes.append((int(parts[1]), int(parts[2]), int(parts[3]))); saw_chart_token = True
            elif token == "TRACK" and len(parts) >= 2:
                current_track = int(parts[1]); saw_chart_token = True
                if current_track < 0 or current_track > 23:
                    document.warnings.append(f"第 {line_number} 行 TRACK {current_track} 超出 0..23")
            elif token == "EVENT" and len(parts) >= 2:
                saw_chart_token = True
                if parts[1] == "-1":
                    current_track = None
                elif len(parts) >= 7 and current_track is not None:
                    document.events.append(Pm3Event(
                        track=current_track,
                        tick=int(parts[1]),
                        wav_index=int(parts[2]),
                        volume=int(parts[3]),
                        hold_start=int(parts[4]),
                        hold_end=int(parts[5]),
                        hold_number=int(parts[6]),
                        line_number=line_number,
                    ))
                else:
                    document.unknown_lines.append({"line_number": line_number, "text": line})
                    document.warnings.append(f"第 {line_number} 行 EVENT 缺少 TRACK 或字段")
            elif token == "WAVNUM" and len(parts) >= 2:
                document.wav_count = int(parts[1]); saw_chart_token = True
            elif token == "WAV" and len(parts) >= 3:
                document.wavs[int(parts[1])] = stripped.split(None, 2)[2]; saw_chart_token = True
            elif token == "TotalNote" and len(parts) >= 2:
                document.total_note = int(parts[1]); saw_chart_token = True
            else:
                document.unknown_lines.append({"line_number": line_number, "text": line})
        except ValueError:
            document.unknown_lines.append({"line_number": line_number, "text": line})
            document.warnings.append(f"第 {line_number} 行 {token} 含有无效整数")
    if not saw_chart_token or not document.events:
        raise Pm3ParseError("文件不是有效的 PM3 单曲谱面文本")
    if document.bpm_count is not None and document.bpm_count != len(document.bpm_changes):
        document.warnings.append(
            f"ChangeBPMCount={document.bpm_count}，实际记录 {len(document.bpm_changes)} 条"
        )
    if document.rhythm_count is not None and document.rhythm_count != len(document.rhythm_changes):
        document.warnings.append(
            f"ChangeRhythmCount={document.rhythm_count}，实际记录 {len(document.rhythm_changes)} 条"
        )
    if document.wav_count is not None and document.wav_count != len(document.wavs):
        document.warnings.append(f"WAVNUM={document.wav_count}，实际定义 {len(document.wavs)} 个")
    return document, encoding
