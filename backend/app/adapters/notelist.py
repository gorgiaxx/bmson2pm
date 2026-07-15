from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ..factory import new_project
from ..models import (
    AudioAsset,
    BarLine,
    BpmEvent,
    CreateProjectRequest,
    DifficultyId,
    KeySoundAsset,
    Lane,
    Note,
    SongProject,
    ValidationIssue,
)
from ..services.timing import pulse_to_seconds
from .base import ChartFormatAdapter, DetectionResult


class NoteListFormatError(ValueError):
    pass


@dataclass
class NoteListImportResult:
    project: SongProject
    warnings: list[str]


class NoteListAdapter(ChartFormatAdapter):
    """Adapter for the note-centred JSON interchange format used by PM tools."""

    HANDLED_TOP_LEVEL = {
        "TPB",
        "TEMPO",
        "samplelist",
        "notelist",
        "tempolist",
        "tracknotelist",
        "measurelist",
        "duration",
    }
    SAMPLE_FIELDS = {"id", "name", "bg"}
    NOTE_FIELDS = {"tick", "track", "sample", "soundid", "duration", "pan", "vol", "attr"}
    TEMPO_FIELDS = {"tick", "tempo", "bpm"}
    MEASURE_FIELDS = {"tick"}

    def detect(self, payload: bytes, filename: str = "") -> DetectionResult:
        try:
            data = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return DetectionResult(False, 0, "notelist", "not valid UTF-8 JSON")
        if not isinstance(data, dict):
            return DetectionResult(False, 0, "notelist", "root is not an object")
        markers = sum(
            (
                "TPB" in data,
                "TEMPO" in data,
                "samplelist" in data,
                "notelist" in data,
            )
        )
        supported = markers >= 3 and "notelist" in data
        return DetectionResult(
            supported,
            markers / 4,
            "notelist",
            "NoteList JSON structure detected" if supported else "missing NoteList fields",
        )

    def parse_with_warnings(
        self,
        payload: bytes,
        difficulty: DifficultyId = DifficultyId.hard,
    ) -> NoteListImportResult:
        try:
            data = json.loads(payload.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise NoteListFormatError("NoteList JSON 必须使用 UTF-8 编码") from exc
        except json.JSONDecodeError as exc:
            raise NoteListFormatError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
        if not isinstance(data, dict):
            raise NoteListFormatError("NoteList JSON 根节点必须是对象")

        tpb = self._positive_int(data.get("TPB"), "TPB")
        if tpb > 9600:
            raise NoteListFormatError("TPB 不得超过 9600")
        try:
            tempo = float(data.get("TEMPO"))
        except (TypeError, ValueError) as exc:
            raise NoteListFormatError("TEMPO 必须是有效数字") from exc
        if not 0 < tempo <= 1000:
            raise NoteListFormatError("TEMPO 必须在 0 到 1000 之间")

        # The platform model requires at least 24 pulses per beat. Low-TPB files
        # are scaled by an integer so their tick positions remain exact.
        scale = max(1, math.ceil(24 / tpb))
        resolution = tpb * scale
        project = new_project(CreateProjectRequest(
            title="NoteList 导入",
            artist="未知艺术家",
            initial_bpm=tempo,
        ))
        project.timing.resolution = resolution
        project.metadata.import_format = "notelist"
        project.game_specific_data["notelist_export_tpb"] = tpb
        project.game_specific_data["notelist_import_difficulty"] = difficulty.value
        warnings: list[str] = []

        samples = data.get("samplelist", [])
        if not isinstance(samples, list):
            raise NoteListFormatError("samplelist 必须是数组")
        key_samples: list[KeySoundAsset | None] = []
        sample_id_assets: dict[int, KeySoundAsset] = {}
        for index, raw in enumerate(samples):
            if not isinstance(raw, dict):
                warnings.append(f"samplelist[{index}] 不是对象，已保留但无法使用")
                key_samples.append(None)
                continue
            try:
                sample_id = int(raw.get("id", index))
                if sample_id < 0:
                    raise ValueError
            except (TypeError, ValueError):
                warnings.append(f"samplelist[{index}] 的 id 无效，已改用 {index}")
                sample_id = index
            name = str(raw.get("name") or f"sample_{sample_id}.wav")
            extension = {"notelist": dict(raw)}
            if bool(raw.get("bg", False)):
                project.audio_assets.append(AudioAsset(
                    name=name,
                    filename=name,
                    extensions=extension,
                ))
                key_samples.append(None)
                continue
            asset = KeySoundAsset(
                name=name,
                filename=name,
                source="notelist",
                extensions=extension,
            )
            project.key_sounds.append(asset)
            key_samples.append(asset)
            if sample_id in sample_id_assets:
                warnings.append(f"samplelist 的 id {sample_id} 重复；按数组下标引用仍可正常导入")
            else:
                sample_id_assets[sample_id] = asset

        notes = data.get("notelist", [])
        if not isinstance(notes, list):
            raise NoteListFormatError("notelist 必须是数组")
        discovered_tracks: set[int] = set()
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            try:
                track_id = int(raw["track"])
            except (KeyError, TypeError, ValueError):
                continue
            if track_id >= 0:
                discovered_tracks.add(track_id)

        track_map = {track_id: track_id + 1 for track_id in discovered_tracks if track_id < 6}
        anonymous_tracks = sorted(track_id for track_id in discovered_tracks if track_id >= 6)
        palette = ("#8aa1a8", "#79a9c7", "#a493c7", "#79b59d", "#c49a75", "#b78c9c")
        for index, track_id in enumerate(anonymous_tracks):
            lane_id = 7 + index
            if lane_id > 255:
                warnings.append(f"NoteList Track {track_id} 超过平台 255 Track 上限，音符将原样保留")
                continue
            project.lanes.append(Lane(
                id=lane_id,
                code=f"notelist_{track_id}",
                display_name=f"NoteList Track {track_id}",
                color=palette[index % len(palette)],
                hand="either",
                kind="anonymous",
                extensions={"notelist": {"track_id": track_id}},
            ))
            track_map[track_id] = lane_id
        if anonymous_tracks:
            created = sum(track_id in track_map for track_id in anonymous_tracks)
            warnings.append(f"检测到 {len(anonymous_tracks)} 个非六路 Track，已创建 {created} 个匿名 Track")
        project.game_specific_data["notelist_track_map"] = {
            str(track_id): lane_id for track_id, lane_id in sorted(track_map.items())
        }

        chart = project.difficulties[difficulty]
        preserved_notes: list[Any] = []
        for index, raw in enumerate(notes):
            if not isinstance(raw, dict):
                warnings.append(f"notelist[{index}] 不是对象，已原样保留")
                preserved_notes.append(raw)
                continue
            try:
                tick = int(raw["tick"])
                track = int(raw["track"])
                length = max(0, int(raw.get("duration", 0)))
                volume = max(0.0, min(2.0, float(raw.get("vol", 127)) / 127))
            except (KeyError, TypeError, ValueError):
                warnings.append(f"notelist[{index}] 的 tick、track、duration 或 vol 无效，已原样保留")
                preserved_notes.append(raw)
                continue
            if tick < 0 or track < 0:
                warnings.append(f"notelist[{index}] 的 tick 或 track 不能为负数，已原样保留")
                preserved_notes.append(raw)
                continue
            lane_id = track_map.get(track)
            if lane_id is None:
                warnings.append(f"notelist[{index}] 的 Track {track} 无法建立 Lane，已原样保留")
                preserved_notes.append(raw)
                continue

            asset: KeySoundAsset | None = None
            reference = raw.get("soundid") if "soundid" in raw else raw.get("sample")
            try:
                sample_reference = int(reference)
            except (TypeError, ValueError):
                sample_reference = -1
            if "soundid" in raw:
                asset = sample_id_assets.get(sample_reference)
                if asset is None and 0 <= sample_reference < len(key_samples):
                    asset = key_samples[sample_reference]
            else:
                # The canonical `sample` field is a samplelist array index. ID
                # lookup is accepted as a compatibility fallback.
                if 0 <= sample_reference < len(key_samples):
                    asset = key_samples[sample_reference]
                if asset is None:
                    asset = sample_id_assets.get(sample_reference)
            if asset is None:
                warnings.append(f"notelist[{index}] 引用了不可用的 sample {reference!r}，音符已保留")
                preserved_notes.append(raw)
                continue

            note = Note(
                lane_id=lane_id,
                pulse=tick * scale,
                length=length * scale,
                key_sound_id=asset.id,
                volume=volume,
                source="notelist",
                extensions={"notelist": dict(raw)},
            )
            chart.notes.append(note)
            if lane_id not in asset.lane_ids:
                asset.lane_ids.append(lane_id)
            lane = next(item for item in project.lanes if item.id == lane_id)
            if lane.default_key_sound_id is None:
                lane.default_key_sound_id = asset.id
        chart.notes.sort(key=lambda note: (note.pulse, note.lane_id, note.id))
        if preserved_notes:
            project.unknown_data["notelist_unmapped_notes"] = preserved_notes

        tempo_list = data.get("tempolist", [])
        if not isinstance(tempo_list, list):
            warnings.append("tempolist 不是数组，已原样保留")
            project.unknown_data["notelist_tempolist_raw"] = tempo_list
        else:
            for index, raw in enumerate(tempo_list):
                try:
                    if not isinstance(raw, dict):
                        raise TypeError
                    tick = int(raw["tick"])
                    bpm = float(raw.get("tempo", raw.get("bpm")))
                    if tick < 0 or not 0 < bpm <= 1000:
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    warnings.append(f"tempolist[{index}] 无效，已跳过")
                    continue
                project.timing.bpm_events.append(BpmEvent(
                    pulse=tick * scale,
                    bpm=bpm,
                    extensions={"notelist": dict(raw)},
                ))
        project.timing.bpm_events.sort(key=lambda event: event.pulse)

        measure_list = data.get("measurelist", [])
        if not isinstance(measure_list, list):
            warnings.append("measurelist 不是数组，已原样保留")
            project.unknown_data["notelist_measurelist_raw"] = measure_list
        else:
            for index, raw in enumerate(measure_list):
                try:
                    tick = int(raw.get("tick")) if isinstance(raw, dict) else int(raw)
                    if tick < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    warnings.append(f"measurelist[{index}] 无效，已跳过")
                    continue
                extension = {"notelist": dict(raw)} if isinstance(raw, dict) else {}
                project.timing.bar_lines.append(BarLine(pulse=tick * scale, extensions=extension))
        project.timing.bar_lines.sort(key=lambda line: line.pulse)

        project.unknown_data["notelist_tracknotelist"] = data.get("tracknotelist", [])
        try:
            duration_tick = max(0, int(data.get("duration", 0)))
        except (TypeError, ValueError):
            duration_tick = 0
            warnings.append("duration 无效，已根据音符计算")
        duration_pulse = duration_tick * scale
        project.unknown_data["notelist_duration_pulse"] = duration_pulse
        maximum_pulse = max(
            [duration_pulse, *(note.pulse + note.length for note in chart.notes)],
            default=0,
        )
        if maximum_pulse:
            project.metadata.audio_duration = max(0.01, pulse_to_seconds(project.timing, maximum_pulse))

        unknown = {key: value for key, value in data.items() if key not in self.HANDLED_TOP_LEVEL}
        if unknown:
            project.unknown_data["notelist_top_level"] = unknown
            warnings.append(f"已保留 {len(unknown)} 个 NoteList 顶层扩展字段")
        return NoteListImportResult(project=project, warnings=warnings)

    def parse(self, payload: bytes, difficulty: DifficultyId = DifficultyId.hard) -> SongProject:
        return self.parse_with_warnings(payload, difficulty).project

    def promote_legacy_tracks(self, project: SongProject) -> int:
        """Restore arbitrary-track notes preserved by the former six-track importer."""
        preserved = project.unknown_data.get("notelist_unmapped_notes")
        if project.metadata.import_format != "notelist" or not isinstance(preserved, list) or not preserved:
            return 0

        raw_difficulty = project.game_specific_data.get("notelist_import_difficulty")
        try:
            difficulty = DifficultyId(str(raw_difficulty))
        except ValueError:
            source_difficulties = [
                difficulty_id
                for difficulty_id, candidate in project.difficulties.items()
                if any(note.source == "notelist" for note in candidate.notes)
            ]
            if len(source_difficulties) != 1:
                return 0
            difficulty = source_difficulties[0]
        chart = project.difficulties[difficulty]

        by_sample_id: dict[int, KeySoundAsset] = {}
        for asset in project.key_sounds:
            raw = asset.extensions.get("notelist") if isinstance(asset.extensions, dict) else None
            try:
                sample_id = int(raw.get("id")) if isinstance(raw, dict) else -1
            except (TypeError, ValueError):
                sample_id = -1
            if sample_id >= 0:
                by_sample_id.setdefault(sample_id, asset)

        tpb = self._safe_int(project.game_specific_data.get("notelist_export_tpb"), project.timing.resolution)
        scale = project.timing.resolution / max(1, tpb)
        candidates: list[tuple[dict[str, Any], int, int, int, float, KeySoundAsset]] = []
        remaining: list[Any] = []
        for raw in preserved:
            if not isinstance(raw, dict):
                remaining.append(raw)
                continue
            try:
                tick = int(raw["tick"])
                track_id = int(raw["track"])
                length = max(0, int(raw.get("duration", 0)))
                volume = max(0.0, min(2.0, float(raw.get("vol", 127)) / 127))
                reference = int(raw.get("soundid") if "soundid" in raw else raw.get("sample"))
            except (KeyError, TypeError, ValueError):
                remaining.append(raw)
                continue
            if tick < 0 or track_id < 0:
                remaining.append(raw)
                continue
            if "soundid" in raw:
                asset = by_sample_id.get(reference)
                if asset is None and 0 <= reference < len(project.key_sounds):
                    asset = project.key_sounds[reference]
            else:
                asset = project.key_sounds[reference] if not project.audio_assets and 0 <= reference < len(project.key_sounds) else None
                if asset is None:
                    asset = by_sample_id.get(reference)
            if asset is None:
                remaining.append(raw)
                continue
            candidates.append((raw, tick, track_id, length, volume, asset))
        if not candidates:
            return 0

        track_map: dict[int, int] = {}
        stored = project.game_specific_data.get("notelist_track_map")
        if isinstance(stored, dict):
            for raw_track, raw_lane in stored.items():
                try:
                    track_map[int(raw_track)] = int(raw_lane)
                except (TypeError, ValueError):
                    continue
        for note in chart.notes:
            raw = note.extensions.get("notelist") if isinstance(note.extensions, dict) else None
            try:
                track_id = int(raw.get("track")) if isinstance(raw, dict) else note.lane_id - 1
            except (TypeError, ValueError):
                track_id = note.lane_id - 1
            track_map.setdefault(track_id, note.lane_id)
        for track_id in range(6):
            track_map.setdefault(track_id, track_id + 1)

        palette = ("#8aa1a8", "#79a9c7", "#a493c7", "#79b59d", "#c49a75", "#b78c9c")
        existing_lane_ids = {lane.id for lane in project.lanes}
        next_lane_id = max(existing_lane_ids, default=0) + 1
        for track_id in sorted({item[2] for item in candidates}):
            mapped = track_map.get(track_id)
            if mapped in existing_lane_ids:
                continue
            if next_lane_id > 255:
                continue
            lane = Lane(
                id=next_lane_id,
                code=f"notelist_{track_id}",
                display_name=f"NoteList Track {track_id}",
                color=palette[(next_lane_id - 7) % len(palette)],
                hand="either",
                kind="anonymous",
                extensions={"notelist": {"track_id": track_id}},
            )
            project.lanes.append(lane)
            existing_lane_ids.add(next_lane_id)
            track_map[track_id] = next_lane_id
            next_lane_id += 1

        promoted = 0
        for raw, tick, track_id, length, volume, asset in candidates:
            lane_id = track_map.get(track_id)
            if lane_id not in existing_lane_ids:
                remaining.append(raw)
                continue
            chart.notes.append(Note(
                lane_id=lane_id,
                pulse=max(0, round(tick * scale)),
                length=max(0, round(length * scale)),
                key_sound_id=asset.id,
                volume=volume,
                source="notelist",
                extensions={"notelist": dict(raw)},
            ))
            if lane_id not in asset.lane_ids:
                asset.lane_ids.append(lane_id)
            lane = next(item for item in project.lanes if item.id == lane_id)
            if lane.default_key_sound_id is None:
                lane.default_key_sound_id = asset.id
            promoted += 1
        chart.notes.sort(key=lambda note: (note.pulse, note.lane_id, note.id))
        project.game_specific_data["notelist_track_map"] = {
            str(track_id): lane_id for track_id, lane_id in sorted(track_map.items())
        }
        project.game_specific_data["notelist_import_difficulty"] = difficulty.value
        project.game_specific_data["notelist_legacy_tracks_promoted"] = promoted
        if remaining:
            project.unknown_data["notelist_unmapped_notes"] = remaining
        else:
            project.unknown_data.pop("notelist_unmapped_notes", None)
        warnings = project.unknown_data.setdefault("import_warnings", [])
        if isinstance(warnings, list):
            warnings.append(f"已从旧项目恢复 {promoted} 个任意 Track NoteList 音符")
        return promoted

    def validate(self, payload: bytes) -> list[ValidationIssue]:
        try:
            result = self.parse_with_warnings(payload)
        except NoteListFormatError as exc:
            return [ValidationIssue(severity="error", code="notelist.invalid", message=str(exc))]
        return [
            ValidationIssue(severity="warning", code="notelist.compatibility", message=warning)
            for warning in result.warnings
        ]

    def build(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        tpb: int = 48,
    ) -> bytes:
        if difficulty not in project.difficulties:
            raise NoteListFormatError(f"难度 {difficulty.value} 不存在")
        if not 1 <= tpb <= 9600:
            raise NoteListFormatError("导出 TPB 必须在 1 到 9600 之间")

        scale = tpb / project.timing.resolution
        to_tick = lambda pulse: max(0, round(pulse * scale))
        lane_tracks = self._export_track_map(project)
        sample_rows: list[dict[str, Any]] = []
        asset_sample_indexes: dict[str, int] = {}
        used_ids: set[int] = set()
        next_id = 0

        def allocate_id(preferred: Any = None) -> int:
            nonlocal next_id
            try:
                value = int(preferred)
            except (TypeError, ValueError):
                value = -1
            if value >= 0 and value not in used_ids:
                used_ids.add(value)
                next_id = max(next_id, value + 1)
                return value
            while next_id in used_ids:
                next_id += 1
            value = next_id
            used_ids.add(value)
            next_id += 1
            return value

        for asset in project.key_sounds:
            raw = self._extension_payload(asset.extensions)
            pm3 = asset.extensions.get("pm3") if isinstance(asset.extensions, dict) else None
            preferred = raw.get("id")
            if preferred is None and isinstance(pm3, dict):
                preferred = pm3.get("wav_index")
            row = dict(raw)
            row.update({
                "id": allocate_id(preferred),
                "name": asset.filename or asset.name,
                "bg": False,
            })
            asset_sample_indexes[asset.id] = len(sample_rows)
            sample_rows.append(row)

        for asset in project.audio_assets:
            raw = self._extension_payload(asset.extensions)
            row = dict(raw)
            row.update({
                "id": allocate_id(raw.get("id")),
                "name": asset.filename or asset.name,
                "bg": True,
            })
            sample_rows.append(row)

        fallback_indexes: dict[str, int] = {}

        def sample_index_for(note: Note) -> int:
            if note.key_sound_id and note.key_sound_id in asset_sample_indexes:
                return asset_sample_indexes[note.key_sound_id]
            lane = next((item for item in project.lanes if item.id == note.lane_id), None)
            if lane and lane.default_key_sound_id in asset_sample_indexes:
                return asset_sample_indexes[lane.default_key_sound_id]
            key = note.key_sound_id or f"lane:{note.lane_id}"
            if key not in fallback_indexes:
                name = f"{key}.wav" if note.key_sound_id else f"lane_{note.lane_id}.wav"
                fallback_indexes[key] = len(sample_rows)
                sample_rows.append({"id": allocate_id(), "name": name, "bg": False})
            return fallback_indexes[key]

        chart = project.difficulties[difficulty]
        note_rows: list[Any] = []
        for note in sorted(chart.notes, key=lambda item: (item.pulse, item.lane_id, item.id)):
            raw = self._extension_payload(note.extensions)
            row = dict(raw)
            row.pop("soundid", None)
            row.update({
                "tick": to_tick(note.pulse),
                "track": lane_tracks[note.lane_id],
                "sample": sample_index_for(note),
                "duration": to_tick(note.length),
                "pan": self._safe_int(raw.get("pan"), 64),
                "vol": max(0, min(255, round(note.volume * 127))),
                "attr": self._safe_int(raw.get("attr"), 0),
            })
            note_rows.append(row)
        preserved = project.unknown_data.get("notelist_unmapped_notes")
        if isinstance(preserved, list):
            note_rows.extend(preserved)

        tempo_rows = []
        for event in sorted(project.timing.bpm_events, key=lambda item: item.pulse):
            raw = self._extension_payload(event.extensions)
            row = dict(raw)
            row.pop("bpm", None)
            row.update({"tick": to_tick(event.pulse), "tempo": event.bpm})
            tempo_rows.append(row)

        measure_rows = []
        for line in sorted(project.timing.bar_lines, key=lambda item: item.pulse):
            raw = self._extension_payload(line.extensions)
            row = dict(raw)
            row["tick"] = to_tick(line.pulse)
            measure_rows.append(row)

        duration_pulse = max(
            [
                int(project.unknown_data.get("notelist_duration_pulse", 0) or 0),
                *(note.pulse + note.length for note in chart.notes),
                *(line.pulse for line in project.timing.bar_lines),
            ],
            default=0,
        )
        unknown = project.unknown_data.get("notelist_top_level")
        data: dict[str, Any] = dict(unknown) if isinstance(unknown, dict) else {}
        track_notes = project.unknown_data.get("notelist_tracknotelist", [])
        data.update({
            "TPB": tpb,
            "TEMPO": project.timing.initial_bpm,
            "samplelist": sample_rows,
            "notelist": note_rows,
            "tempolist": tempo_rows,
            "tracknotelist": track_notes if isinstance(track_notes, list) else [],
            "measurelist": measure_rows,
            "duration": to_tick(duration_pulse),
        })
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _positive_int(value: Any, label: str) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise NoteListFormatError(f"{label} 必须是正整数") from exc
        if result <= 0:
            raise NoteListFormatError(f"{label} 必须是正整数")
        return result

    @staticmethod
    def _safe_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _extension_payload(extensions: dict[str, Any]) -> dict[str, Any]:
        raw = extensions.get("notelist") if isinstance(extensions, dict) else None
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _export_track_map(project: SongProject) -> dict[int, int]:
        lane_ids = {lane.id for lane in project.lanes}
        result: dict[int, int] = {}
        used_tracks: set[int] = set()
        stored = project.game_specific_data.get("notelist_track_map")
        if isinstance(stored, dict):
            for raw_track, raw_lane in sorted(stored.items(), key=lambda item: NoteListAdapter._safe_int(item[0], 0)):
                try:
                    track_id = int(raw_track)
                    lane_id = int(raw_lane)
                except (TypeError, ValueError):
                    continue
                if track_id < 0 or lane_id not in lane_ids or lane_id in result or track_id in used_tracks:
                    continue
                result[lane_id] = track_id
                used_tracks.add(track_id)

        for lane in project.lanes:
            extension = lane.extensions.get("notelist") if isinstance(lane.extensions, dict) else None
            try:
                track_id = int(extension.get("track_id")) if isinstance(extension, dict) else -1
            except (TypeError, ValueError):
                track_id = -1
            if lane.id not in result and track_id >= 0 and track_id not in used_tracks:
                result[lane.id] = track_id
                used_tracks.add(track_id)

        for lane_id in range(1, 7):
            track_id = lane_id - 1
            if lane_id in lane_ids and lane_id not in result and track_id not in used_tracks:
                result[lane_id] = track_id
                used_tracks.add(track_id)

        next_track = 0
        for lane in project.lanes:
            if lane.id in result:
                continue
            preferred = lane.id - 1
            if preferred >= 0 and preferred not in used_tracks:
                track_id = preferred
            else:
                while next_track in used_tracks:
                    next_track += 1
                track_id = next_track
            result[lane.id] = track_id
            used_tracks.add(track_id)
        return result
