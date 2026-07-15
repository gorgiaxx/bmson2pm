from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

from ..factory import new_project
from ..models import (
    BarLine,
    BpmEvent,
    CreateProjectRequest,
    DifficultyId,
    KeySoundAsset,
    Lane,
    Note,
    SongProject,
    StopEvent,
    ValidationIssue,
)
from ..services.bms_resources import visual_resource_kind
from .base import ChartFormatAdapter, DetectionResult
from .bms_parser import (
    BmsDocument,
    BmsObject,
    base36_code,
    base36_value,
    decode_bms,
    is_long_channel,
    normalized_play_channel,
    parse_bms_text,
)


class BmsFormatError(ValueError):
    pass


@dataclass
class BmsImportResult:
    project: SongProject
    warnings: list[str]


@dataclass(frozen=True)
class PositionedObject:
    source: BmsObject
    beat: Fraction
    pulse: int


class BmsAdapter(ChartFormatAdapter):
    FILE_EXTENSIONS = (".bms", ".bme", ".bml", ".pms")
    MAX_CHANNEL_CELLS = 262_144
    DEFAULT_CHANNEL_ORDER = (
        "11", "12", "13", "14", "15", "18", "19", "16",
        "21", "22", "23", "24", "25", "28", "29", "26",
    )

    def detect(self, payload: bytes, filename: str = "") -> DetectionResult:
        try:
            text, _, _ = decode_bms(payload)
        except ValueError:
            return DetectionResult(False, 0, "bms", "unable to decode BMS text")
        markers = sum(bool(pattern) for pattern in (
            "#TITLE" in text.upper(),
            "#BPM" in text.upper(),
            any(line.startswith("#") and len(line) > 7 and line[4:7].endswith(":") for line in text.splitlines()),
        ))
        extension_match = filename.lower().endswith(self.FILE_EXTENSIONS)
        confidence = min(1.0, markers / 3 + (0.2 if extension_match else 0))
        return DetectionResult(markers >= 2 or (extension_match and markers >= 1), confidence, "bms", "BMS text detected")

    def inspect(self, payload: bytes, encoding: str | None = None) -> dict[str, Any]:
        try:
            text, selected_encoding, candidates = decode_bms(payload, encoding)
        except ValueError as exc:
            raise BmsFormatError(str(exc)) from exc
        document = parse_bms_text(text, selected_encoding)
        self._ensure_document(document)
        starts, positions = self._geometry(document)
        resolution, resolution_warning = self._choose_resolution(document, starts, positions)
        warnings = list(document.warnings)
        if resolution_warning:
            warnings.append(resolution_warning)
        initial_bpm = self._positive_float(document.headers.get("BPM"), 120)
        if document.headers.get("BPM") and not self._is_valid_bpm(document.headers["BPM"]):
            warnings.append("主 BPM 无效或超过 1000，已使用 120")
        counts = Counter(
            channel
            for obj in document.objects
            if (channel := normalized_play_channel(obj.channel)) is not None
        )
        ordered_channels = sorted(
            counts,
            key=lambda channel: (
                self.DEFAULT_CHANNEL_ORDER.index(channel)
                if channel in self.DEFAULT_CHANNEL_ORDER
                else len(self.DEFAULT_CHANNEL_ORDER),
                channel,
            ),
        )
        default_map = {channel: lane for lane, channel in enumerate(ordered_channels[:6], start=1)}
        return {
            "format": "bms",
            "encoding": selected_encoding,
            "encoding_candidates": [asdict(candidate) for candidate in candidates],
            "title": document.headers.get("TITLE", "未命名曲目"),
            "artist": document.headers.get("ARTIST", "未知艺术家"),
            "initial_bpm": initial_bpm,
            "resolution": resolution,
            "measure_count": document.max_measure + 1,
            "wav_count": len(document.wav_defs),
            "wav_files": [
                {"id": code, "filename": filename}
                for code, filename in sorted(document.wav_defs.items(), key=lambda item: base36_value(item[0]))
            ],
            "bmp_count": len(document.bmp_defs),
            "bmp_files": [
                {"id": code, "filename": filename, "kind": visual_resource_kind(filename)}
                for code, filename in sorted(document.bmp_defs.items(), key=lambda item: base36_value(item[0]))
            ],
            "playable_channels": [
                {
                    "channel": channel,
                    "label": self._channel_label(channel),
                    "note_count": counts[channel],
                    "default_lane": default_map.get(channel),
                }
                for channel in ordered_channels
            ],
            "random_blocks": document.random_blocks,
            "warnings": warnings,
        }

    def parse_with_warnings(
        self,
        payload: bytes,
        difficulty: DifficultyId = DifficultyId.hard,
        *,
        encoding: str | None = None,
        lane_map: dict[str, int] | None = None,
        random_values: dict[int, int] | None = None,
        preserve_unmapped: bool = True,
    ) -> BmsImportResult:
        try:
            text, selected_encoding, _ = decode_bms(payload, encoding)
        except ValueError as exc:
            raise BmsFormatError(str(exc)) from exc
        document = parse_bms_text(text, selected_encoding, random_values)
        self._ensure_document(document)
        starts, position_map = self._geometry(document)
        resolution, resolution_warning = self._choose_resolution(document, starts, position_map)
        warnings = list(document.warnings)
        if resolution_warning:
            warnings.append(resolution_warning)
        positioned = [
            PositionedObject(obj, position_map[id(obj)], self._pulse(position_map[id(obj)], resolution))
            for obj in document.objects
        ]
        positioned.sort(key=lambda item: (item.beat, item.source.line_number, item.source.channel))

        discovered = []
        for item in positioned:
            channel = normalized_play_channel(item.source.channel)
            if channel and channel not in discovered:
                discovered.append(channel)
        effective_map: dict[str, int] = {}
        for channel, lane in (lane_map or {}).items():
            normalized = normalized_play_channel(str(channel).upper())
            try:
                lane_id = int(lane)
            except (TypeError, ValueError):
                continue
            if normalized and 1 <= lane_id <= 6:
                effective_map[normalized] = lane_id
        if lane_map is None:
            ordered = sorted(
                discovered,
                key=lambda channel: (
                    self.DEFAULT_CHANNEL_ORDER.index(channel)
                    if channel in self.DEFAULT_CHANNEL_ORDER
                    else len(self.DEFAULT_CHANNEL_ORDER),
                    channel,
                ),
            )
            effective_map = {channel: lane for lane, channel in enumerate(ordered[:6], start=1)}
        if len(effective_map.values()) != len(set(effective_map.values())):
            raise BmsFormatError("每个六路输入 Lane 只能映射一个 BMS 通道")

        initial_bpm = self._positive_float(document.headers.get("BPM"), 120)
        if document.headers.get("BPM") and not self._is_valid_bpm(document.headers["BPM"]):
            warnings.append("主 BPM 无效或超过 1000，已使用 120")
        project = new_project(CreateProjectRequest(
            title=document.headers.get("TITLE") or "未命名曲目",
            artist=document.headers.get("ARTIST") or "未知艺术家",
            initial_bpm=initial_bpm,
        ))
        project.metadata.subtitle = document.headers.get("SUBTITLE", "")
        project.metadata.import_format = "bms"
        project.timing.resolution = resolution
        anonymous_channels = [channel for channel in discovered if channel not in effective_map]
        if preserve_unmapped and anonymous_channels:
            palette = ("#8aa1a8", "#79a9c7", "#a493c7", "#79b59d", "#c49a75", "#b78c9c")
            next_lane_id = max((lane.id for lane in project.lanes), default=0) + 1
            for index, channel in enumerate(anonymous_channels):
                lane_id = next_lane_id + index
                project.lanes.append(Lane(
                    id=lane_id,
                    code=f"bms_{channel.lower()}",
                    display_name=f"匿名 Track {channel}",
                    color=palette[index % len(palette)],
                    hand="either",
                    kind="anonymous",
                    extensions={"bms": {
                        "channel": channel,
                        "label": self._channel_label(channel),
                        "source": "playable",
                    }},
                ))
                effective_map[channel] = lane_id
            warnings.append(f"{len(anonymous_channels)} 个额外按键通道已创建为匿名 Track")
        chart = project.difficulties[difficulty]
        chart.level = self._safe_int(document.headers.get("PLAYLEVEL"), chart.level, 0, 99)
        chart.description = "BMS import"

        for measure in range(document.max_measure + 2):
            beat = starts[measure]
            project.timing.bar_lines.append(BarLine(
                pulse=self._pulse(beat, resolution),
                extensions={"bms": {
                    "measure": measure,
                    "length": self._fraction_text(document.measure_lengths.get(measure, Fraction(1))),
                }},
            ))

        asset_by_code: dict[str, KeySoundAsset] = {}
        for code, filename in sorted(document.wav_defs.items(), key=lambda item: base36_value(item[0])):
            asset = KeySoundAsset(
                name=filename or f"WAV {code}",
                filename=filename,
                source="bms",
                extensions={"bms": {"id": code}},
            )
            asset_by_code[code] = asset
            project.key_sounds.append(asset)

        missing_assets: set[str] = set()

        def asset_id(code: str) -> str:
            if code not in asset_by_code:
                asset = KeySoundAsset(
                    name=f"缺失 WAV {code}",
                    filename="",
                    source="bms",
                    extensions={"bms": {"id": code, "missing": True}},
                )
                asset_by_code[code] = asset
                project.key_sounds.append(asset)
                if code not in missing_assets:
                    warnings.append(f"WAV {code} 未定义，已建立缺失资源占位")
                    missing_assets.add(code)
            return asset_by_code[code].id

        normal_objects: list[PositionedObject] = []
        long_objects: list[PositionedObject] = []
        bgm_objects: list[dict[str, Any]] = []
        unmapped_objects: list[dict[str, Any]] = []
        unknown_objects: list[dict[str, Any]] = []
        bga_events: dict[str, list[dict[str, Any]]] = {"base": [], "poor": [], "layer": []}
        missing_bmp: set[str] = set()

        for item in positioned:
            obj = item.source
            common = self._object_payload(item)
            if obj.channel == "01":
                bgm_objects.append({**common, "key_sound_id": asset_id(obj.value)})
            elif obj.channel == "03":
                try:
                    bpm = int(obj.value, 16)
                    if bpm > 0:
                        project.timing.bpm_events.append(BpmEvent(
                            pulse=item.pulse, bpm=bpm,
                            extensions={"bms": common},
                        ))
                except ValueError:
                    warnings.append(f"第 {obj.line_number} 行直接 BPM {obj.value} 无效")
            elif obj.channel == "08":
                bpm_value = document.bpm_defs.get(obj.value)
                if bpm_value and 0 < bpm_value <= 1000:
                    project.timing.bpm_events.append(BpmEvent(
                        pulse=item.pulse, bpm=float(bpm_value),
                        extensions={"bms": {**common, "id": obj.value}},
                    ))
                elif bpm_value and bpm_value > 1000:
                    warnings.append(f"BPM {obj.value} 超过平台上限 1000，已跳过")
                else:
                    warnings.append(f"BPM {obj.value} 未定义")
            elif obj.channel == "09":
                stop_value = document.stop_defs.get(obj.value)
                if stop_value is None:
                    warnings.append(f"STOP {obj.value} 未定义")
                elif stop_value > 0:
                    duration = self._pulse(stop_value / 48, resolution)
                    project.timing.stop_events.append(StopEvent(
                        pulse=item.pulse,
                        duration_pulses=max(duration, 0),
                        extensions={"bms": {**common, "id": obj.value, "stop_value": self._fraction_text(stop_value)}},
                    ))
                else:
                    warnings.append(f"STOP {obj.value} 必须大于 0，已跳过")
            elif obj.channel in {"04", "06", "07"}:
                kind = {"04": "base", "06": "poor", "07": "layer"}[obj.channel]
                bga_events[kind].append({**common, "bmp_id": obj.value})
                if obj.value not in document.bmp_defs and obj.value not in missing_bmp:
                    warnings.append(f"BMP {obj.value} 未定义，BGA 引用已保留")
                    missing_bmp.add(obj.value)
            elif normalized_play_channel(obj.channel):
                (long_objects if is_long_channel(obj.channel) else normal_objects).append(item)
            else:
                unknown_objects.append(common)

        pending_normal: dict[int, Note] = {}
        lnobj = document.headers.get("LNOBJ", "").upper()
        for item in normal_objects:
            base_channel = normalized_play_channel(item.source.channel) or item.source.channel
            lane = effective_map.get(base_channel)
            if lane is None:
                unmapped_objects.append(self._object_payload(item))
                continue
            if lnobj and item.source.value == lnobj:
                start_note = pending_normal.pop(lane, None)
                if start_note is None:
                    warnings.append(f"Pulse {item.pulse} 的 LNOBJ {lnobj} 没有起点")
                else:
                    start_note.length = max(0, item.pulse - start_note.pulse)
                    start_note.extensions.setdefault("bms", {})["lnobj_end"] = self._object_payload(item)
                continue
            note = self._make_note(item, lane, asset_id(item.source.value))
            chart.notes.append(note)
            pending_normal[lane] = note

        pending_long: dict[int, tuple[PositionedObject, Note]] = {}
        for item in long_objects:
            base_channel = normalized_play_channel(item.source.channel) or item.source.channel
            lane = effective_map.get(base_channel)
            if lane is None:
                unmapped_objects.append(self._object_payload(item))
                continue
            pending = pending_long.pop(lane, None)
            if pending is None:
                note = self._make_note(item, lane, asset_id(item.source.value))
                chart.notes.append(note)
                pending_long[lane] = (item, note)
            else:
                _, note = pending
                note.length = max(0, item.pulse - note.pulse)
                note.extensions.setdefault("bms", {})["long_end"] = self._object_payload(item)
        for lane, (_, note) in pending_long.items():
            warnings.append(f"Lane {lane} 的长音符起点 Pulse {note.pulse} 没有终点")
        if unmapped_objects:
            warnings.append(f"{len(unmapped_objects)} 个按键对象没有 Lane 映射，已保留但未加入谱面")
        if unknown_objects:
            warnings.append(f"{len(unknown_objects)} 个不支持的 BMS 通道对象已原样保留")

        chart.notes.sort(key=lambda note: (note.pulse, note.lane_id, note.id))
        assets_by_id = {asset.id: asset for asset in project.key_sounds}
        lanes_by_id = {lane.id: lane for lane in project.lanes}
        for note in chart.notes:
            asset = assets_by_id.get(note.key_sound_id or "")
            lane = lanes_by_id.get(note.lane_id)
            if asset and note.lane_id not in asset.lane_ids:
                asset.lane_ids.append(note.lane_id)
            if asset and lane and lane.default_key_sound_id is None:
                lane.default_key_sound_id = asset.id
        project.timing.bpm_events.sort(key=lambda event: event.pulse)
        project.timing.stop_events.sort(key=lambda event: event.pulse)
        last_pulse = max(
            [line.pulse for line in project.timing.bar_lines]
            + [note.pulse + note.length for note in chart.notes]
            + [item["pulse"] for item in bgm_objects]
            + [0]
        )
        project.metadata.audio_duration = max(30, last_pulse / resolution * 60 / initial_bpm + 4)

        project.unknown_data["bms_unknown_lines"] = document.unknown_lines
        project.unknown_data["bms_control_lines"] = document.control_lines
        project.unknown_data["bms_bgm_objects"] = bgm_objects
        project.unknown_data["bms_unmapped_objects"] = unmapped_objects
        project.unknown_data["bms_unknown_objects"] = unknown_objects
        project.unknown_data["bms_headers"] = document.headers
        project.mv_configuration["bms_bga"] = {
            "bmp_defs": document.bmp_defs,
            "assets": {
                code: {
                    "id": code,
                    "filename": filename,
                    "kind": visual_resource_kind(filename),
                    "resource": {
                        "project_id": project.id,
                        "declared_path": filename,
                        "exists": False,
                    },
                }
                for code, filename in document.bmp_defs.items()
            },
            "events": bga_events,
        }
        project.game_specific_data.update({
            "bms_encoding": selected_encoding,
            "bms_lane_map": effective_map,
            "bms_measure_lengths": {
                str(measure): self._fraction_text(length)
                for measure, length in document.measure_lengths.items()
            },
            "bms_random_values": {
                str(block["index"]): block["selected"] for block in document.random_blocks
            },
        })
        return BmsImportResult(project, warnings)

    def parse(self, payload: bytes, difficulty: DifficultyId = DifficultyId.hard) -> SongProject:
        return self.parse_with_warnings(payload, difficulty).project

    def validate(self, payload: bytes) -> list[ValidationIssue]:
        try:
            result = self.parse_with_warnings(payload)
        except BmsFormatError as exc:
            return [ValidationIssue(severity="error", code="bms.invalid", message=str(exc))]
        return [
            ValidationIssue(severity="warning", code="bms.compatibility", message=warning)
            for warning in result.warnings
        ]

    def compatibility_report(
        self,
        project: SongProject,
        difficulty: DifficultyId,
    ) -> list[ValidationIssue]:
        if difficulty not in project.difficulties:
            return [ValidationIssue(
                severity="error",
                code="bms.difficulty_missing",
                message=f"难度 {difficulty.value} 不存在，无法导出 BMS",
                difficulty=difficulty,
            )]
        chart = project.difficulties[difficulty]
        issues: list[ValidationIssue] = []
        if len(project.key_sounds) >= 36 * 36:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.wav_limit",
                message="Key 音资源超过传统 BMS 的 1295 项上限",
                difficulty=difficulty,
            ))
        if len({self._number_text(event.bpm) for event in project.timing.bpm_events}) >= 36 * 36:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.bpm_limit",
                message="BPM 定义超过传统 BMS 的 1295 项上限",
                difficulty=difficulty,
            ))
        if len({
            self._fraction_text(Fraction(event.duration_pulses * 48, project.timing.resolution))
            for event in project.timing.stop_events
        }) >= 36 * 36:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.stop_limit",
                message="STOP 定义超过传统 BMS 的 1295 项上限",
                difficulty=difficulty,
            ))

        bga = project.mv_configuration.get("bms_bga")
        if isinstance(bga, dict) and isinstance(bga.get("bmp_defs"), dict) and len(bga["bmp_defs"]) >= 36 * 36:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.bmp_limit",
                message="BGA 资源超过传统 BMS 的 1295 项上限",
                difficulty=difficulty,
            ))

        boundaries = self._export_boundaries(project, chart.notes)
        if len(boundaries) - 1 > 1000:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.measure_limit",
                message="谱面超过传统 BMS 的 1000 小节上限",
                difficulty=difficulty,
            ))

        lane_channels = self._export_lane_channels(project)
        unsupported_lanes = sorted({note.lane_id for note in chart.notes if note.lane_id not in lane_channels})
        if unsupported_lanes:
            issues.append(ValidationIssue(
                severity="error",
                code="bms.lane_unmapped",
                message=f"Lane {', '.join(map(str, unsupported_lanes))} 没有 BMS 通道映射",
                difficulty=difficulty,
            ))

        if any(not note.playable for note in chart.notes):
            issues.append(ValidationIssue(
                severity="warning",
                code="bms.non_playable_notes",
                message="非操作音符将按普通 BMS 按键音符导出",
                difficulty=difficulty,
            ))
        if any(note.volume != 1 or note.continues or note.notes for note in chart.notes):
            issues.append(ValidationIssue(
                severity="warning",
                code="bms.note_properties",
                message="音符音量、continues 与备注无法写入传统 BMS",
                difficulty=difficulty,
            ))
        if any(asset.volume != 1 or asset.delay_ms != 0 or asset.tags for asset in project.key_sounds):
            issues.append(ValidationIssue(
                severity="warning",
                code="bms.asset_properties",
                message="Key 音音量、延迟与标签无法写入传统 BMS",
                difficulty=difficulty,
            ))
        timing = project.timing
        if any(value != 0 for value in (
            timing.audio_offset_ms,
            timing.chart_offset_ms,
            timing.key_sound_offset_ms,
            timing.mv_offset_ms,
        )):
            issues.append(ValidationIssue(
                severity="warning",
                code="bms.offsets",
                message="平台时间偏移无法完整表达为传统 BMS 指令",
                difficulty=difficulty,
            ))
        preserved = self._dict_list(project.unknown_data.get("bms_unknown_lines"))
        if preserved:
            issues.append(ValidationIssue(
                severity="info",
                code="bms.preserved_commands",
                message=f"将原样合并 {len(preserved)} 条 BMS 未知命令或注释",
                difficulty=difficulty,
            ))
        return issues

    def build(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        encoding: str = "utf-8",
    ) -> bytes:
        if difficulty not in project.difficulties:
            raise BmsFormatError(f"难度 {difficulty.value} 不存在")
        chart = project.difficulties[difficulty]
        boundaries = self._export_boundaries(project, chart.notes)
        lane_channels = self._export_lane_channels(project)
        asset_codes, wav_rows = self._export_assets(project)
        channel_events: dict[tuple[int, str], list[tuple[Fraction, str]]] = defaultdict(list)

        def add_event(
            pulse: int,
            channel: str,
            value: str,
            source: dict[str, Any] | None = None,
        ) -> None:
            try:
                base36_value(value)
            except ValueError as exc:
                raise BmsFormatError(f"BMS 通道 #{channel} 的对象编号无效：{value}") from exc
            preserved = self._preserved_measure_position(pulse, boundaries, source)
            measure, position = preserved or self._measure_position(pulse, boundaries)
            if measure > 999:
                raise BmsFormatError("BMS 最多支持 1000 个小节（000-999）")
            channel_events[(measure, channel)].append((position, value))

        fallback_codes: dict[int, str] = {}
        next_code = max([base36_value(code) for code in asset_codes.values()] + [0]) + 1

        def note_code(note: Note) -> str:
            nonlocal next_code
            if note.key_sound_id and note.key_sound_id in asset_codes:
                return asset_codes[note.key_sound_id]
            if note.lane_id not in fallback_codes:
                if next_code >= 36 * 36:
                    raise BmsFormatError("WAV 资源超过 BMS 的 1295 项限制")
                code = base36_code(next_code)
                next_code += 1
                fallback_codes[note.lane_id] = code
                wav_rows.append((code, f"lane_{note.lane_id}.wav"))
            return fallback_codes[note.lane_id]

        for note in sorted(chart.notes, key=lambda item: (item.pulse, item.lane_id)):
            channel = lane_channels.get(note.lane_id)
            if not channel:
                raise BmsFormatError(f"Lane {note.lane_id} 没有可用的 BMS 通道映射")
            code = note_code(note)
            note_bms = note.extensions.get("bms") if isinstance(note.extensions, dict) else None
            source = note_bms if isinstance(note_bms, dict) else None
            if note.length > 0:
                long_channel = ("5" if channel[0] == "1" else "6") + channel[1]
                add_event(note.pulse, long_channel, code, source)
                raw_end = source.get("long_end") if source else None
                if not isinstance(raw_end, dict) and source:
                    raw_end = source.get("lnobj_end")
                add_event(
                    note.pulse + note.length,
                    long_channel,
                    code,
                    raw_end if isinstance(raw_end, dict) else None,
                )
            else:
                add_event(note.pulse, channel, code, source)

        for item in self._dict_list(project.unknown_data.get("bms_bgm_objects")):
            code = asset_codes.get(str(item.get("key_sound_id") or ""), str(item.get("value") or "00"))
            if code != "00":
                add_event(int(item.get("pulse") or 0), "01", code, item)

        bpm_codes: dict[str, str] = {}
        bpm_rows: list[tuple[str, str]] = []
        for event in sorted(project.timing.bpm_events, key=lambda item: item.pulse):
            value = self._number_text(event.bpm)
            if value not in bpm_codes:
                if len(bpm_codes) + 1 >= 36 * 36:
                    raise BmsFormatError("BPM 定义超过 BMS 的 1295 项限制")
                code = base36_code(len(bpm_codes) + 1)
                bpm_codes[value] = code
                bpm_rows.append((code, value))
            bms = event.extensions.get("bms") if isinstance(event.extensions, dict) else None
            add_event(event.pulse, "08", bpm_codes[value], bms if isinstance(bms, dict) else None)

        stop_codes: dict[str, str] = {}
        stop_rows: list[tuple[str, str]] = []
        for event in sorted(project.timing.stop_events, key=lambda item: item.pulse):
            value = self._fraction_text(Fraction(event.duration_pulses * 48, project.timing.resolution))
            if value not in stop_codes:
                if len(stop_codes) + 1 >= 36 * 36:
                    raise BmsFormatError("STOP 定义超过 BMS 的 1295 项限制")
                code = base36_code(len(stop_codes) + 1)
                stop_codes[value] = code
                stop_rows.append((code, value))
            bms = event.extensions.get("bms") if isinstance(event.extensions, dict) else None
            add_event(event.pulse, "09", stop_codes[value], bms if isinstance(bms, dict) else None)

        bga = project.mv_configuration.get("bms_bga")
        bmp_defs: dict[str, Any] = {}
        if isinstance(bga, dict):
            if isinstance(bga.get("bmp_defs"), dict):
                bmp_defs = bga["bmp_defs"]
            events = bga.get("events")
            if isinstance(events, dict):
                for kind, channel in (("base", "04"), ("poor", "06"), ("layer", "07")):
                    for item in self._dict_list(events.get(kind)):
                        add_event(
                            int(item.get("pulse") or 0),
                            channel,
                            str(item.get("bmp_id") or "00"),
                            item,
                        )

        bmp_rows: list[tuple[str, str]] = []
        for raw_code, filename in bmp_defs.items():
            code = str(raw_code).upper()
            try:
                base36_value(code)
            except ValueError as exc:
                raise BmsFormatError(f"BMP 对象编号无效：{raw_code}") from exc
            bmp_rows.append((code, str(filename)))

        preserved_headers = project.unknown_data.get("bms_headers")
        headers = dict(preserved_headers) if isinstance(preserved_headers, dict) else {}
        lines = [
            "#PLAYER 1",
            f"#TITLE {project.metadata.title}",
            f"#SUBTITLE {project.metadata.subtitle}" if project.metadata.subtitle else "",
            f"#ARTIST {project.metadata.artist}",
        ]
        for key in ("GENRE", "SUBARTIST", "STAGEFILE", "BANNER", "BACKBMP", "RANK", "TOTAL", "VOLWAV", "DIFFICULTY"):
            if headers.get(key):
                lines.append(f"#{key} {headers[key]}")
        lines.extend([
            f"#PLAYLEVEL {chart.level}",
            f"#BPM {self._number_text(project.timing.initial_bpm)}",
            "#LNTYPE 1",
            "",
        ])
        lines.extend(f"#WAV{code} {filename}" for code, filename in sorted(wav_rows, key=lambda item: base36_value(item[0])))
        lines.extend(f"#BMP{code} {filename}" for code, filename in sorted(bmp_rows, key=lambda item: base36_value(item[0])))
        lines.extend(f"#BPM{code} {value}" for code, value in bpm_rows)
        lines.extend(f"#STOP{code} {value}" for code, value in stop_rows)

        unknown_lines = [
            str(item.get("text"))
            for item in self._dict_list(project.unknown_data.get("bms_unknown_lines"))
            if item.get("kind") in {"command", "comment"} and item.get("text")
        ]
        if unknown_lines:
            lines.extend(["", *unknown_lines])
        lines.append("")

        for measure in range(len(boundaries) - 1):
            ratio = self._preserved_measure_ratio(project, measure, boundaries)
            if ratio != 1:
                lines.append(f"#{measure:03d}02:{self._fraction_text(ratio)}")
            keys = sorted(key for key in channel_events if key[0] == measure)
            for _, channel in keys:
                for data in self._encode_channel(channel_events[(measure, channel)]):
                    lines.append(f"#{measure:03d}{channel}:{data}")
        text = "\r\n".join(line for line in lines if line is not None) + "\r\n"
        try:
            return text.encode(encoding)
        except LookupError as exc:
            raise BmsFormatError(f"不支持的导出编码：{encoding}") from exc
        except UnicodeEncodeError as exc:
            raise BmsFormatError(f"文本无法使用 {encoding} 编码；请改用 UTF-8") from exc

    def _geometry(self, document: BmsDocument) -> tuple[dict[int, Fraction], dict[int, Fraction]]:
        starts: dict[int, Fraction] = {0: Fraction(0)}
        for measure in range(document.max_measure + 1):
            starts[measure + 1] = starts[measure] + document.measure_lengths.get(measure, Fraction(1)) * 4
        positions = {
            id(obj): starts[obj.measure] + document.measure_lengths.get(obj.measure, Fraction(1)) * 4 * obj.position
            for obj in document.objects
        }
        return starts, positions

    @staticmethod
    def _ensure_document(document: BmsDocument) -> None:
        if not (
            document.headers
            or document.wav_defs
            or document.bmp_defs
            or document.bpm_defs
            or document.stop_defs
            or document.objects
            or document.measure_lengths
        ):
            raise BmsFormatError("文件中没有可识别的 BMS 指令")

    def _choose_resolution(
        self,
        document: BmsDocument,
        starts: dict[int, Fraction],
        positions: dict[int, Fraction],
    ) -> tuple[int, str | None]:
        required = 240
        fractions = [*starts.values(), *positions.values(), *(value / 48 for value in document.stop_defs.values())]
        for value in fractions:
            required = math.lcm(required, value.denominator)
            if required > 9600:
                return 9600, f"精确时间分辨率需要 {required} PPQN，已限制为 9600；部分位置会舍入"
        return max(240, required), None

    @staticmethod
    def _pulse(beat: Fraction, resolution: int) -> int:
        value = beat * resolution
        quotient, remainder = divmod(value.numerator, value.denominator)
        return quotient + (1 if remainder * 2 >= value.denominator else 0)

    def _make_note(self, item: PositionedObject, lane: int, key_sound_id: str) -> Note:
        return Note(
            lane_id=lane,
            pulse=item.pulse,
            length=0,
            key_sound_id=key_sound_id,
            source="bms",
            extensions={"bms": {
                "channel": item.source.channel,
                "value": item.source.value,
                "measure": item.source.measure,
                "position": self._ratio_text(item.source.position),
                "source_pulse": item.pulse,
            }},
        )

    def _object_payload(self, item: PositionedObject) -> dict[str, Any]:
        return {
            "pulse": item.pulse,
            "channel": item.source.channel,
            "value": item.source.value,
            "measure": item.source.measure,
            "position": self._ratio_text(item.source.position),
            "line": item.source.line_number,
        }

    @staticmethod
    def _channel_label(channel: str) -> str:
        side = "P1" if channel[0] == "1" else "P2"
        key = "Scratch" if channel[1] == "6" else f"Key {channel[1]}"
        return f"{side} {key} (#{channel})"

    @staticmethod
    def _positive_float(value: Any, fallback: float) -> float:
        try:
            result = float(value)
            return result if 0 < result <= 1000 else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _is_valid_bpm(value: Any) -> bool:
        try:
            return 0 < float(value) <= 1000
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _safe_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            return min(max(int(value), minimum), maximum)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _fraction_text(value: Fraction) -> str:
        if value.denominator == 1:
            return str(value.numerator)
        decimal = value.numerator / value.denominator
        return f"{decimal:.10f}".rstrip("0").rstrip(".")

    @staticmethod
    def _ratio_text(value: Fraction) -> str:
        return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"

    @staticmethod
    def _number_text(value: float) -> str:
        return f"{value:.10f}".rstrip("0").rstrip(".")

    @staticmethod
    def _dict_list(value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _export_assets(self, project: SongProject) -> tuple[dict[str, str], list[tuple[str, str]]]:
        used: set[str] = set()
        codes: dict[str, str] = {}
        rows: list[tuple[str, str]] = []
        next_value = 1
        for asset in project.key_sounds:
            extension = asset.extensions.get("bms") if isinstance(asset.extensions, dict) else None
            preferred = str(extension.get("id") or "").upper() if isinstance(extension, dict) else ""
            try:
                valid = preferred != "00" and base36_value(preferred) > 0
            except ValueError:
                valid = False
            if not valid or preferred in used:
                while next_value < 36 * 36 and base36_code(next_value) in used:
                    next_value += 1
                if next_value >= 36 * 36:
                    raise BmsFormatError("WAV 资源超过 BMS 的 1295 项限制")
                preferred = base36_code(next_value)
                next_value += 1
            used.add(preferred)
            codes[asset.id] = preferred
            rows.append((preferred, asset.filename or f"missing_{preferred}.wav"))
        return codes, rows

    def _export_lane_channels(self, project: SongProject) -> dict[int, str]:
        stored = project.game_specific_data.get("bms_lane_map")
        result: dict[int, str] = {}
        if isinstance(stored, dict):
            ordered = sorted(stored.items(), key=lambda item: (
                self.DEFAULT_CHANNEL_ORDER.index(str(item[0]).upper())
                if str(item[0]).upper() in self.DEFAULT_CHANNEL_ORDER
                else len(self.DEFAULT_CHANNEL_ORDER)
            ))
            for channel, lane in ordered:
                try:
                    lane_id = int(lane)
                except (TypeError, ValueError):
                    continue
                result.setdefault(lane_id, str(channel).upper())
        defaults = ("11", "12", "13", "14", "15", "18")
        for lane, channel in enumerate(defaults, start=1):
            result.setdefault(lane, channel)
        return result

    @classmethod
    def _export_boundaries(cls, project: SongProject, notes: list[Note]) -> list[int]:
        boundaries = sorted({line.pulse for line in project.timing.bar_lines if line.pulse >= 0})
        if not boundaries or boundaries[0] != 0:
            boundaries.insert(0, 0)
        extra_pulses = [note.pulse + note.length for note in notes]
        extra_pulses += [event.pulse for event in project.timing.bpm_events]
        extra_pulses += [event.pulse for event in project.timing.stop_events]
        extra_pulses += [
            int(item.get("pulse") or 0)
            for item in cls._dict_list(project.unknown_data.get("bms_bgm_objects"))
        ]
        bga = project.mv_configuration.get("bms_bga")
        if isinstance(bga, dict) and isinstance(bga.get("events"), dict):
            for kind in ("base", "poor", "layer"):
                extra_pulses += [
                    int(item.get("pulse") or 0)
                    for item in cls._dict_list(bga["events"].get(kind))
                ]
        target = max(extra_pulses + boundaries + [project.timing.resolution * 4])
        while boundaries[-1] <= target:
            boundaries.append(boundaries[-1] + project.timing.resolution * 4)
        return boundaries

    @staticmethod
    def _measure_position(pulse: int, boundaries: list[int]) -> tuple[int, Fraction]:
        measure = max(0, bisect_right(boundaries, pulse) - 1)
        if measure >= len(boundaries) - 1:
            measure = len(boundaries) - 2
        length = boundaries[measure + 1] - boundaries[measure]
        return measure, Fraction(pulse - boundaries[measure], max(length, 1))

    @classmethod
    def _preserved_measure_position(
        cls,
        pulse: int,
        boundaries: list[int],
        source: dict[str, Any] | None,
    ) -> tuple[int, Fraction] | None:
        if not source:
            return None
        try:
            source_pulse = int(source.get("source_pulse", source.get("pulse")))
            measure = int(source.get("measure"))
            position = Fraction(str(source.get("position"))).limit_denominator(cls.MAX_CHANNEL_CELLS)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        if source_pulse != pulse or not 0 <= measure < len(boundaries) - 1 or not 0 <= position < 1:
            return None
        computed_measure = max(0, bisect_right(boundaries, pulse) - 1)
        if computed_measure != measure:
            return None
        length = boundaries[measure + 1] - boundaries[measure]
        expected_pulse = Fraction(boundaries[measure]) + position * length
        if abs(expected_pulse - pulse) > 1:
            return None
        return measure, position

    @staticmethod
    def _preserved_measure_ratio(
        project: SongProject,
        measure: int,
        boundaries: list[int],
    ) -> Fraction:
        actual_length = boundaries[measure + 1] - boundaries[measure]
        fallback = Fraction(actual_length, project.timing.resolution * 4)
        for line in project.timing.bar_lines:
            if line.pulse != boundaries[measure] or not isinstance(line.extensions, dict):
                continue
            bms = line.extensions.get("bms")
            if not isinstance(bms, dict):
                continue
            try:
                source_measure = int(bms.get("measure"))
                ratio = Fraction(str(bms.get("length")))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if source_measure != measure or ratio <= 0:
                continue
            expected_length = ratio * project.timing.resolution * 4
            if abs(expected_length - actual_length) <= 1:
                return ratio
        return fallback

    def _encode_channel(self, events: list[tuple[Fraction, str]]) -> list[str]:
        tracks: list[dict[Fraction, str]] = []
        resolutions: list[int] = []
        for position, value in sorted(events, key=lambda item: item[0]):
            if not 0 <= position < 1:
                raise BmsFormatError(f"对象位置超出小节范围：{position}")
            if position.denominator > self.MAX_CHANNEL_CELLS:
                raise BmsFormatError(
                    f"单个小节的 BMS 对象分辨率超过 {self.MAX_CHANNEL_CELLS}"
                )
            best_index: int | None = None
            best_resolution = position.denominator
            best_cost = position.denominator
            for index, track in enumerate(tracks):
                if position in track:
                    continue
                resolution = math.lcm(resolutions[index], position.denominator)
                if resolution > self.MAX_CHANNEL_CELLS:
                    continue
                cost = resolution - resolutions[index]
                if cost <= best_cost:
                    best_index = index
                    best_resolution = resolution
                    best_cost = cost
            if best_index is None:
                tracks.append({position: value.upper()})
                resolutions.append(position.denominator)
            else:
                tracks[best_index][position] = value.upper()
                resolutions[best_index] = best_resolution
        result: list[str] = []
        for track, resolution in zip(tracks, resolutions):
            cells = ["00"] * resolution
            for position, value in track.items():
                index = position.numerator * (resolution // position.denominator)
                cells[index] = value
            result.append("".join(cells))
        return result
