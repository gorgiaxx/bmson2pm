from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..factory import new_project
from ..models import (
    BarLine,
    BpmEvent,
    CreateProjectRequest,
    DifficultyId,
    KeySoundAsset,
    Note,
    SongProject,
    StopEvent,
    ValidationIssue,
)
from .base import ChartFormatAdapter, DetectionResult
from .bmson_normalize import normalize_bmson


class BmsonFormatError(ValueError):
    pass


@dataclass
class ImportResult:
    project: SongProject
    warnings: list[str]


class BmsonAdapter(ChartFormatAdapter):
    HANDLED_TOP_LEVEL = {
        "version",
        "info",
        "lines",
        "bpm_events",
        "stop_events",
        "sound_channels",
        "bga",
    }
    HANDLED_INFO = {
        "title",
        "subtitle",
        "artist",
        "chart_name",
        "level",
        "init_bpm",
        "resolution",
        "chart_duration",
    }
    NOTE_FIELDS = {"x", "y", "l", "c"}
    CHANNEL_FIELDS = {"name", "notes"}
    BPM_FIELDS = {"y", "bpm"}
    STOP_FIELDS = {"y", "duration"}
    BAR_LINE_FIELDS = {"y"}

    def detect(self, payload: bytes, filename: str = "") -> DetectionResult:
        try:
            data = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return DetectionResult(False, 0, "bmson", "not valid UTF-8 JSON")
        if not isinstance(data, dict):
            return DetectionResult(False, 0, "bmson", "root is not an object")
        markers = sum(
            (
                "info" in data,
                "sound_channels" in data or "soundChannel" in data,
                any(key in data for key in ("version", "lines", "bpm_events", "bpmNotes")),
            )
        )
        supported = markers >= 2
        return DetectionResult(
            supported,
            markers / 3,
            "bmson",
            "BMSON structure detected" if supported else "missing BMSON fields",
        )

    def parse_with_warnings(
        self,
        payload: bytes,
        difficulty: DifficultyId = DifficultyId.hard,
    ) -> ImportResult:
        try:
            loaded = json.loads(payload.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise BmsonFormatError("BMSON 必须使用 UTF-8 编码") from exc
        except json.JSONDecodeError as exc:
            raise BmsonFormatError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
        if not isinstance(loaded, dict):
            raise BmsonFormatError("BMSON 根节点必须是对象")

        normalized = normalize_bmson(loaded)
        data = normalized.data
        warnings = list(normalized.warnings)
        info = data.get("info") or {}
        if not isinstance(info, dict):
            raise BmsonFormatError("info 必须是对象")
        try:
            resolution = int(info.get("resolution") or 240)
            initial_bpm = float(info.get("init_bpm") or 120)
        except (TypeError, ValueError) as exc:
            raise BmsonFormatError("resolution 或 init_bpm 无效") from exc
        if not 24 <= resolution <= 9600:
            raise BmsonFormatError("resolution 必须在 24 到 9600 之间")
        if not 0 < initial_bpm <= 1000:
            raise BmsonFormatError("init_bpm 必须在 0 到 1000 之间")

        request = CreateProjectRequest(
            title=str(info.get("title") or "未命名曲目"),
            artist=str(info.get("artist") or "未知艺术家"),
            initial_bpm=initial_bpm,
        )
        project = new_project(request)
        project.timing.resolution = resolution
        project.metadata.subtitle = str(info.get("subtitle") or "")
        project.metadata.import_format = "bmson"
        try:
            chart_duration = float(info.get("chart_duration") or 0)
        except (TypeError, ValueError):
            chart_duration = 0
            warnings.append("chart_duration 无效，已使用默认时长")
        project.metadata.audio_duration = max(chart_duration / 1000, 0) or 120
        chart = project.difficulties[difficulty]
        chart.level = self._safe_level(info.get("level"), chart.level)
        chart.display_name = str(info.get("chart_name") or chart.display_name)

        seen_ids: set[tuple[int, int, str]] = set()
        channels = data.get("sound_channels") or []
        if not isinstance(channels, list):
            warnings.append("sound_channels 不是数组，已忽略")
            channels = []
        for channel_index, channel in enumerate(channels):
            if not isinstance(channel, dict):
                warnings.append(f"sound_channels[{channel_index}] 不是对象，已跳过")
                continue
            channel_name = str(channel.get("name") or "")
            asset = KeySoundAsset(
                name=channel_name or f"未命名通道 {channel_index + 1}",
                filename=channel_name,
                source="bmson",
                extensions=self._extensions(channel, self.CHANNEL_FIELDS),
            )
            project.key_sounds.append(asset)
            raw_notes = channel.get("notes") or []
            if not isinstance(raw_notes, list):
                warnings.append(f"通道 {channel_index} 的 notes 不是数组，已忽略")
                continue
            for note_index, raw_note in enumerate(raw_notes):
                if not isinstance(raw_note, dict):
                    warnings.append(f"通道 {channel_index} 的音符 {note_index} 无效，已跳过")
                    continue
                try:
                    lane_id = int(raw_note.get("x", 0))
                    pulse = int(raw_note.get("y", 0))
                    length = max(int(raw_note.get("l", 0)), 0)
                except (TypeError, ValueError):
                    warnings.append(f"通道 {channel_index} 的音符 {note_index} 坐标无效，已跳过")
                    continue
                preserved_note = {
                    "key_sound_id": asset.id,
                    "channel": channel_name,
                    "note": raw_note,
                }
                if lane_id == 0:
                    project.unknown_data.setdefault("bmson_bgm_notes", []).append(preserved_note)
                    continue
                if not 1 <= lane_id <= 6:
                    warnings.append(f"Lane {lane_id} 尚未映射，音符已原样保留")
                    project.unknown_data.setdefault("bmson_unmapped_notes", []).append(preserved_note)
                    continue
                dedupe_key = (lane_id, max(pulse, 0), asset.id)
                if dedupe_key in seen_ids:
                    warnings.append(f"Lane {lane_id} 在 pulse {pulse} 存在重复音符")
                seen_ids.add(dedupe_key)
                chart.notes.append(
                    Note(
                        lane_id=lane_id,
                        pulse=max(pulse, 0),
                        length=length,
                        key_sound_id=asset.id,
                        playable=True,
                        continues=bool(raw_note.get("c", False)),
                        source="bmson",
                        extensions=self._extensions(raw_note, self.NOTE_FIELDS),
                    )
                )

        bpm_events = data.get("bpm_events") or []
        if not isinstance(bpm_events, list):
            bpm_events = []
            warnings.append("bpm_events 不是数组，已忽略")
        for raw in bpm_events:
            try:
                if not isinstance(raw, dict):
                    raise TypeError
                project.timing.bpm_events.append(
                    BpmEvent(
                        pulse=int(raw["y"]),
                        bpm=float(raw["bpm"]),
                        extensions=self._extensions(raw, self.BPM_FIELDS),
                    )
                )
            except (KeyError, TypeError, ValueError):
                warnings.append("发现无效 BPM 事件，已跳过")

        stop_events = data.get("stop_events") or []
        if not isinstance(stop_events, list):
            stop_events = []
            warnings.append("stop_events 不是数组，已忽略")
        for raw in stop_events:
            try:
                if not isinstance(raw, dict):
                    raise TypeError
                project.timing.stop_events.append(
                    StopEvent(
                        pulse=int(raw["y"]),
                        duration_pulses=max(int(raw["duration"]), 0),
                        extensions=self._extensions(raw, self.STOP_FIELDS),
                    )
                )
            except (KeyError, TypeError, ValueError):
                warnings.append("发现无效 STOP 事件，已跳过")

        lines = data.get("lines") or []
        if not isinstance(lines, list):
            lines = []
            warnings.append("lines 不是数组，已忽略")
        for raw in lines:
            try:
                if not isinstance(raw, dict):
                    raise TypeError
                pulse = int(raw["y"])
                if pulse < 0:
                    raise ValueError
                project.timing.bar_lines.append(
                    BarLine(pulse=pulse, extensions=self._extensions(raw, self.BAR_LINE_FIELDS))
                )
            except (KeyError, TypeError, ValueError):
                warnings.append("发现无效小节线，已跳过")
        project.timing.bar_lines.sort(key=lambda line: line.pulse)

        passthrough = {key: value for key, value in data.items() if key not in self.HANDLED_TOP_LEVEL}
        if passthrough:
            project.unknown_data["bmson_top_level"] = passthrough
            warnings.append(f"已保留 {len(passthrough)} 个未处理顶层字段")
        passthrough_info = {key: value for key, value in info.items() if key not in self.HANDLED_INFO}
        if passthrough_info:
            project.unknown_data["bmson_info_fields"] = passthrough_info
            warnings.append(f"已保留 {len(passthrough_info)} 个未处理 info 字段")
        if "bga" in data:
            project.mv_configuration["bmson_bga"] = data["bga"]
        project.game_specific_data["bmson_source_version"] = normalized.source_version
        project.game_specific_data["bmson_export_version"] = "1.0.0"
        chart.notes.sort(key=lambda note: (note.pulse, note.lane_id))
        return ImportResult(project=project, warnings=warnings)

    def parse(self, payload: bytes, difficulty: DifficultyId = DifficultyId.hard) -> SongProject:
        return self.parse_with_warnings(payload, difficulty).project

    def validate(self, payload: bytes) -> list[ValidationIssue]:
        try:
            result = self.parse_with_warnings(payload)
        except BmsonFormatError as exc:
            return [ValidationIssue(severity="error", code="bmson.invalid", message=str(exc))]
        return [
            ValidationIssue(severity="warning", code="bmson.compatibility", message=warning)
            for warning in result.warnings
        ]

    def build(self, project: SongProject, difficulty: DifficultyId) -> bytes:
        if difficulty not in project.difficulties:
            raise BmsonFormatError(f"难度 {difficulty.value} 不存在")
        chart = project.difficulties[difficulty]
        assets = {asset.id: asset for asset in project.key_sounds}
        sound_channels: dict[str, dict[str, Any]] = {}

        # Imported channels stay first and remain distinct even when names match.
        for asset in project.key_sounds:
            if asset.source != "bmson":
                continue
            row = self._extension_payload(asset.extensions)
            row.update({"name": asset.filename, "notes": []})
            sound_channels[asset.id] = row

        for note in sorted(chart.notes, key=lambda item: (item.pulse, item.lane_id)):
            asset = assets.get(note.key_sound_id or "")
            channel_key = note.key_sound_id or f"lane:{note.lane_id}"
            if channel_key not in sound_channels:
                row = self._extension_payload(asset.extensions) if asset else {}
                channel_name = (
                    asset.filename
                    if asset is not None
                    else note.key_sound_id or f"lane_{note.lane_id}.wav"
                )
                row.update({"name": channel_name, "notes": []})
                sound_channels[channel_key] = row
            raw_note = self._extension_payload(note.extensions)
            raw_note.update(
                {
                    "x": note.lane_id,
                    "y": note.pulse,
                    "l": note.length,
                    "c": note.continues,
                }
            )
            sound_channels[channel_key]["notes"].append(raw_note)

        for collection in ("bmson_bgm_notes", "bmson_unmapped_notes"):
            preserved = project.unknown_data.get(collection, [])
            if not isinstance(preserved, list):
                continue
            for item in preserved:
                if not isinstance(item, dict) or not isinstance(item.get("note"), dict):
                    continue
                channel_id = str(item.get("key_sound_id") or "")
                channel_name = str(item.get("channel") or "bgm.wav")
                channel_key = channel_id or f"legacy-preserved:{channel_name}"
                if channel_key not in sound_channels:
                    asset = assets.get(channel_id)
                    row = self._extension_payload(asset.extensions) if asset else {}
                    row.update({"name": asset.filename if asset else channel_name, "notes": []})
                    sound_channels[channel_key] = row
                sound_channels[channel_key]["notes"].append(dict(item["note"]))

        preserved_info = project.unknown_data.get("bmson_info_fields")
        info: dict[str, Any] = dict(preserved_info) if isinstance(preserved_info, dict) else {}
        info.update(
            {
                "title": project.metadata.title,
                "subtitle": project.metadata.subtitle,
                "artist": project.metadata.artist,
                "chart_name": chart.display_name,
                "level": chart.level,
                "init_bpm": project.timing.initial_bpm,
                "resolution": project.timing.resolution,
                "chart_duration": round(project.metadata.audio_duration * 1000),
            }
        )
        bars = project.timing.bar_lines or self._generated_bar_lines(project, chart.notes)
        data: dict[str, Any] = {
            "version": project.game_specific_data.get("bmson_export_version", "1.0.0"),
            "info": info,
            "lines": [self._build_bar_line(line) for line in bars],
            "bpm_events": [self._build_bpm_event(event) for event in sorted(project.timing.bpm_events, key=lambda event: event.pulse)],
            "stop_events": [self._build_stop_event(event) for event in sorted(project.timing.stop_events, key=lambda event: event.pulse)],
            "sound_channels": list(sound_channels.values()),
        }
        if project.mv_configuration.get("bmson_bga") is not None:
            data["bga"] = project.mv_configuration["bmson_bga"]
        unknown = project.unknown_data.get("bmson_top_level")
        if isinstance(unknown, dict):
            for key, value in unknown.items():
                if key not in data:
                    data[key] = value
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _safe_level(value: Any, fallback: int) -> int:
        try:
            return min(max(int(value), 0), 99)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _extensions(raw: dict[str, Any], known: set[str]) -> dict[str, Any]:
        extras = {key: value for key, value in raw.items() if key not in known}
        return {"bmson": extras} if extras else {}

    @staticmethod
    def _extension_payload(extensions: dict[str, Any]) -> dict[str, Any]:
        raw = extensions.get("bmson") if isinstance(extensions, dict) else None
        return dict(raw) if isinstance(raw, dict) else {}

    def _build_bar_line(self, line: BarLine) -> dict[str, Any]:
        raw = self._extension_payload(line.extensions)
        raw["y"] = line.pulse
        return raw

    def _build_bpm_event(self, event: BpmEvent) -> dict[str, Any]:
        raw = self._extension_payload(event.extensions)
        raw.update({"y": event.pulse, "bpm": event.bpm})
        return raw

    def _build_stop_event(self, event: StopEvent) -> dict[str, Any]:
        raw = self._extension_payload(event.extensions)
        raw.update({"y": event.pulse, "duration": event.duration_pulses})
        return raw

    @staticmethod
    def _generated_bar_lines(project: SongProject, notes: list[Note]) -> list[BarLine]:
        resolution = project.timing.resolution
        last_pulse = max((note.pulse + note.length for note in notes), default=resolution * 4 * 16)
        return [
            BarLine(pulse=pulse)
            for pulse in range(0, last_pulse + resolution * 4, resolution * 4)
        ]
