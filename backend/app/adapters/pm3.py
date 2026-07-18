from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
from .base import ChartFormatAdapter, DetectionResult
from .pm3_crypto import (
    Pm3CryptoError,
    Pm3Decryption,
    decrypt_chart,
    encrypt_chart,
    header_for_slot,
    slot_for_header,
)
from .pm3_parser import Pm3ChartDocument, Pm3Event, Pm3ParseError, Pm3SongInfo, parse_chart_text


class Pm3FormatError(ValueError):
    pass


@dataclass
class Pm3ImportResult:
    project: SongProject
    warnings: list[str]
    inspection: dict[str, Any]


@dataclass
class Pm3BuildResult:
    plaintext: bytes
    container: bytes
    filename: str
    song_id: int
    header: int
    slot: int
    warnings: list[str]
    stats: dict[str, Any]
    key_sound_paths: dict[str, str]


class Pm3Adapter(ChartFormatAdapter):
    # PM3 uses 12 ticks per quarter note. The platform minimum is 24 PPQN,
    # so imported ticks are doubled without losing timing precision.
    SOURCE_PPQN = 12
    TARGET_PPQN = 24
    PULSE_SCALE = TARGET_PPQN // SOURCE_PPQN
    TRACK_TO_LANE = {0: 5, 1: 6, 2: 1, 3: 2, 4: 3, 5: 4}
    BACKGROUND_TRACK = 16
    AUXILIARY_COLORS = (
        "#8aa1a8", "#79a9c7", "#a493c7", "#79b59d", "#c49a75", "#b78c9c",
    )
    DIFFICULTY_SUFFIXES = {
        "easy": DifficultyId.easy,
        "normal": DifficultyId.normal,
        "hard": DifficultyId.hard,
        "special": DifficultyId.special,
        "master": DifficultyId.master,
    }

    def detect(self, payload: bytes, filename: str = "") -> DetectionResult:
        lowered = filename.lower()
        extension_match = lowered.endswith((".enc", ".enccut"))
        container_match = len(payload) >= 20 and len(payload) % 16 in {0, 4}
        if extension_match and container_match:
            return DetectionResult(True, 0.82, "pm3", "PM3 加密容器结构匹配")
        try:
            document, _ = parse_chart_text(payload)
        except (Pm3ParseError, UnicodeError):
            return DetectionResult(False, 0, "pm3", "未发现 PM3 谱面 token")
        return DetectionResult(bool(document.events), 0.95, "pm3", "PM3 明文谱面 token 匹配")

    def decrypt(self, payload: bytes, filename: str, cut_data: bytes | None = None) -> Pm3Decryption:
        lowered = filename.lower()
        try:
            if lowered.endswith(".enccut"):
                if cut_data is None:
                    raise Pm3FormatError("内建 .enccut 谱面缺少 A36 cut data")
                return decrypt_chart(payload, cut_data=cut_data)
            if lowered.endswith(".enc"):
                return decrypt_chart(payload)
            return Pm3Decryption(payload, -1, 0, len(payload), False)
        except Pm3CryptoError as exc:
            raise Pm3FormatError(str(exc)) from exc

    def inspect(
        self,
        payload: bytes,
        *,
        filename: str,
        cut_data: bytes | None = None,
        song_info: Pm3SongInfo | None = None,
        game_root: Path | None = None,
    ) -> dict[str, Any]:
        decrypted = self.decrypt(payload, filename, cut_data)
        try:
            document, encoding = parse_chart_text(decrypted.plaintext)
        except Pm3ParseError as exc:
            raise Pm3FormatError(str(exc)) from exc
        playable = [event for event in document.events if event.track in self.TRACK_TO_LANE]
        holds = sum(1 for event in playable if event.hold_start)
        normal = sum(1 for event in playable if not event.hold_start and not event.hold_end)
        tracks = sorted({event.track for event in document.events})
        result: dict[str, Any] = {
            "format": "pm3-chart",
            "filename": filename,
            "encoding": encoding,
            "encrypted": filename.lower().endswith((".enc", ".enccut")),
            "used_cut": decrypted.used_cut,
            "slot": decrypted.slot,
            "header": f"0x{decrypted.header:08x}",
            "plain_length": decrypted.plain_length,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bpm_changes": [
                {"tick": tick, "pulse": self._pulse(tick), "bpm": value / 100}
                for tick, value in document.bpm_changes
            ],
            "rhythm_changes": [
                {"section": section, "beats": beats, "tick": tick, "pulse": self._pulse(tick)}
                for section, beats, tick in document.rhythm_changes
            ],
            "track_ids": tracks,
            "playable_events": len(playable),
            "note_objects": normal + holds,
            "hold_notes": holds,
            "auxiliary_events": len(document.events) - len(playable),
            "event_count": len(document.events),
            "declared_total_note": document.total_note,
            "wav_count": len(document.wavs),
            "unknown_line_count": len(document.unknown_lines),
            "warnings": list(document.warnings),
            "text_preview": decrypted.plaintext.decode(
                encoding.replace("-replace", ""), errors="replace"
            )[:32768],
            "resources": self._resource_manifest(document, song_info, game_root),
        }
        if song_info:
            result["song"] = song_info.as_dict()
        return result

    def parse_with_warnings(
        self,
        payload: bytes,
        difficulty: DifficultyId = DifficultyId.hard,
        *,
        filename: str = "chart.enc",
        cut_data: bytes | None = None,
        song_info: Pm3SongInfo | None = None,
        source_ref: dict[str, Any] | None = None,
        game_root: Path | None = None,
    ) -> Pm3ImportResult:
        decrypted = self.decrypt(payload, filename, cut_data)
        try:
            document, encoding = parse_chart_text(decrypted.plaintext)
        except Pm3ParseError as exc:
            raise Pm3FormatError(str(exc)) from exc
        inferred = self._difficulty_from_filename(filename)
        difficulty = song_info.difficulty if song_info and song_info.difficulty else (inferred or difficulty)
        initial_bpm = self._initial_bpm(document, song_info)
        title = song_info.song_name if song_info else self._song_stem(filename)
        artist = self._artist(song_info.singer_name) if song_info else "未知艺术家"
        project = new_project(CreateProjectRequest(title=title, artist=artist, initial_bpm=initial_bpm))
        project.metadata.import_format = "pm3"
        project.metadata.source_name = Path(filename).name
        project.metadata.game_song_id = f"p{song_info.song_id:03d}" if song_info else self._song_id(filename)
        project.timing.resolution = self.TARGET_PPQN
        chart = project.difficulties[difficulty]
        chart.description = "PM3 original chart import"
        if song_info:
            chart.level = max(0, min(99, song_info.level))
            project.metadata.audio_duration = self._duration_with_bpm(
                song_info.length, document.bpm_changes, initial_bpm
            )

        warnings = list(document.warnings)
        asset_by_index = self._create_assets(project, document, song_info, game_root)
        playable_events = [event for event in document.events if event.track in self.TRACK_TO_LANE]
        auxiliary_events = [event.as_dict() for event in document.events if event.track not in self.TRACK_TO_LANE]
        self._create_notes(chart.notes, playable_events, asset_by_index, warnings)
        visible_auxiliary = [
            event for event in document.events
            if event.track not in self.TRACK_TO_LANE and event.track != self.BACKGROUND_TRACK
        ]
        self._create_auxiliary_notes(project, chart.notes, visible_auxiliary, asset_by_index, difficulty)
        chart.extensions["pm3"] = {
            "auxiliary_model_version": 1,
            "background_events": [
                event.as_dict() for event in document.events if event.track == self.BACKGROUND_TRACK
            ],
            "source_auxiliary_tracks": sorted({event.track for event in visible_auxiliary}),
        }
        chart.notes.sort(key=lambda note: (note.pulse, note.lane_id, note.id))
        input_lane_ids = {lane.id for lane in project.lanes if lane.kind == "input"}
        playable_note_objects = sum(1 for note in chart.notes if note.lane_id in input_lane_ids)
        if document.total_note is not None and document.total_note not in {
            len(playable_events), playable_note_objects
        }:
            warnings.append(
                f"TotalNote={document.total_note}，实际可玩事件 {len(playable_events)} 个、转换后玩家音符对象 {playable_note_objects} 个"
            )

        for tick, raw_bpm in document.bpm_changes:
            bpm = raw_bpm / 100
            if tick == 0 or bpm <= 0 or bpm > 1000:
                if bpm <= 0 or bpm > 1000:
                    warnings.append(f"Tick {tick} 的 BPM {bpm:g} 超出平台范围，已保留但未加入时间轴")
                continue
            project.timing.bpm_events.append(BpmEvent(
                pulse=self._pulse(tick),
                bpm=bpm,
                extensions={"pm3": {"tick": tick, "raw_bpm": raw_bpm}},
            ))
        project.timing.bpm_events.sort(key=lambda event: event.pulse)
        self._create_bar_lines(project, document)

        max_pulse = max(
            [note.pulse + note.length for note in chart.notes]
            + [self._pulse(event.tick) for event in document.events]
            + [0]
        )
        if not song_info:
            project.metadata.audio_duration = max(
                1,
                self._duration_with_bpm(
                    max_pulse // self.PULSE_SCALE, document.bpm_changes, initial_bpm
                ) + 2,
            )

        source = {
            "role": "pm3-chart",
            "filename": Path(filename).name,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "slot": decrypted.slot,
            "used_cut": decrypted.used_cut,
        }
        if source_ref:
            source.update(source_ref)
        project.source_files.append(source)
        project.unknown_data.update({
            "pm3_encrypted_source_b64": base64.b64encode(payload).decode("ascii"),
            "pm3_plaintext": document.raw_text,
            "pm3_unknown_lines": document.unknown_lines,
            "pm3_auxiliary_events": auxiliary_events,
            "pm3_import_warnings": warnings,
        })
        if cut_data is not None:
            project.unknown_data["pm3_cut_data_b64"] = base64.b64encode(cut_data).decode("ascii")
        project.game_specific_data.update({
            "pm3_source_ppqn": self.SOURCE_PPQN,
            "pm3_pulse_scale": self.PULSE_SCALE,
            "pm3_encoding": encoding,
            "pm3_slot": decrypted.slot,
            "pm3_header": decrypted.header,
            "pm3_track_lane_map": {str(track): lane for track, lane in self.TRACK_TO_LANE.items()},
            "pm3_change_bpm_count": document.bpm_count,
            "pm3_bpm_changes": [
                {"tick": tick, "raw_bpm": bpm} for tick, bpm in document.bpm_changes
            ],
            "pm3_change_rhythm_count": document.rhythm_count,
            "pm3_rhythm_changes": [
                {"section": section, "beats": beats, "tick": tick}
                for section, beats, tick in document.rhythm_changes
            ],
            "pm3_wav_count": document.wav_count,
            "pm3_total_note": document.total_note,
        })
        if song_info:
            project.game_specific_data["pm3_song_info"] = song_info.as_dict()
            project.game_specific_data["pm3_song_info_raw_fields"] = list(song_info.raw_fields)
        project.mv_configuration.setdefault("pm3", {
            "resource_roots": ["media/ui/mv", "media/ui_mv1", "media/ui_mv2", "media/ui_mv3", "media/ui_mv4", "media/ui_mv5", "media/ui_mv6"],
            "controller": "media/ui/mvctrl/mvctrl.swf",
            "selection": "由运行时 Stage::GetMVID 决定，原谱面不直接存储 MV ID",
        })
        self._attach_mv_resources(project, game_root)
        inspection = self.inspect(
            payload,
            filename=filename,
            cut_data=cut_data,
            song_info=song_info,
            game_root=game_root,
        )
        inspection["difficulty"] = difficulty.value
        return Pm3ImportResult(project, warnings, inspection)

    def extract_resources(
        self,
        payload: bytes,
        *,
        filename: str,
        cut_data: bytes | None = None,
        song_info: Pm3SongInfo | None = None,
        game_root: Path | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        decrypted = self.decrypt(payload, filename, cut_data)
        try:
            document, _ = parse_chart_text(decrypted.plaintext)
        except Pm3ParseError as exc:
            raise Pm3FormatError(str(exc)) from exc
        return self._resource_manifest(document, song_info, game_root)

    def preserve_unknown_data(
        self,
        payload: bytes,
        *,
        filename: str,
        cut_data: bytes | None = None,
    ) -> dict[str, Any]:
        decrypted = self.decrypt(payload, filename, cut_data)
        try:
            document, _ = parse_chart_text(decrypted.plaintext)
        except Pm3ParseError as exc:
            raise Pm3FormatError(str(exc)) from exc
        preserved: dict[str, Any] = {
            "pm3_encrypted_source_b64": base64.b64encode(payload).decode("ascii"),
            "pm3_plaintext": document.raw_text,
            "pm3_unknown_lines": document.unknown_lines,
            "pm3_auxiliary_events": [
                event.as_dict() for event in document.events if event.track not in self.TRACK_TO_LANE
            ],
        }
        if cut_data is not None:
            preserved["pm3_cut_data_b64"] = base64.b64encode(cut_data).decode("ascii")
        return preserved

    def parse(self, payload: bytes, difficulty: DifficultyId = DifficultyId.hard) -> SongProject:
        return self.parse_with_warnings(payload, difficulty, filename="chart.txt").project

    def validate(self, payload: bytes) -> list[ValidationIssue]:
        try:
            document, _ = parse_chart_text(payload)
        except Pm3ParseError as exc:
            return [ValidationIssue(severity="error", code="pm3.parse", message=str(exc))]
        return [
            ValidationIssue(severity="warning", code="pm3.parse.warning", message=warning)
            for warning in document.warnings
        ]

    def build(self, project: SongProject, difficulty: DifficultyId) -> bytes:
        return self.build_with_report(project, difficulty).container

    def build_with_report(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        *,
        slot: int | None = None,
        song_id: int | None = None,
    ) -> Pm3BuildResult:
        if song_id is not None and (
            isinstance(song_id, bool) or not isinstance(song_id, int) or song_id not in range(1000)
        ):
            raise Pm3FormatError("PM3 曲目序号必须在 0..999")
        chart = project.difficulties[difficulty]
        warnings: list[str] = []
        source_document = self._source_document(project)
        bpm_changes = self._build_bpm_changes(project, warnings)
        rhythm_changes = self._build_rhythm_changes(project)
        lanes = {lane.id: lane for lane in project.lanes}
        referenced_asset_ids = {
            asset_id
            for note in chart.notes
            if (lane := lanes.get(note.lane_id)) is not None
            if (asset_id := note.key_sound_id or lane.default_key_sound_id)
        }
        wavs, wav_by_asset = self._build_wavs(
            project,
            source_document,
            warnings,
            referenced_asset_ids=referenced_asset_ids,
            target_song_id=song_id,
        )
        events = self._build_events(project, difficulty, wavs, wav_by_asset, warnings)
        playable_count = sum(
            1 for event in events if event.track in self.TRACK_TO_LANE
        )
        unknown_lines = project.unknown_data.get("pm3_unknown_lines", [])
        plaintext = self._render_chart(
            bpm_changes,
            rhythm_changes,
            events,
            wavs,
            playable_count,
            unknown_lines if isinstance(unknown_lines, list) else [],
        )
        try:
            parsed, encoding = parse_chart_text(plaintext)
        except Pm3ParseError as exc:
            raise Pm3FormatError(f"PM3 重建文本无法重新解析：{exc}") from exc
        warnings.extend(parsed.warnings)
        source_slot = project.game_specific_data.get("pm3_slot", 0)
        selected_slot = source_slot if slot is None else slot
        if not isinstance(selected_slot, int) or selected_slot not in range(10):
            selected_slot = 0
            warnings.append("来源 key slot 无效，已使用 slot 0")
        source_header = project.game_specific_data.get("pm3_header")
        if (
            isinstance(source_header, int)
            and 0 <= source_header <= 0xFFFFFFFF
            and slot_for_header(source_header) == selected_slot
        ):
            header = source_header
        else:
            header = header_for_slot(selected_slot)
            if source_header not in {None, 0}:
                warnings.append("来源 header 与 key slot 不一致，已生成兼容 header")
        try:
            container = encrypt_chart(plaintext, header=header, slot=selected_slot)
            verified = decrypt_chart(container)
        except Pm3CryptoError as exc:
            raise Pm3FormatError(str(exc)) from exc
        if verified.plaintext != plaintext:
            raise Pm3FormatError("PM3 加密后解密校验不一致")
        filename = self._output_filename(project, difficulty, song_id=song_id)
        output_song_id = song_id
        if output_song_id is None:
            output_song_id = self._numeric_song_id(self._song_id(filename))
        if output_song_id is None:
            output_song_id = self._numeric_song_id(project.metadata.game_song_id) or 0
        if unknown_lines:
            warnings.append(f"已把 {len(unknown_lines)} 条未知源数据合并到重建文本末尾")
        input_lane_ids = {lane.id for lane in project.lanes if lane.kind == "input"}
        auxiliary_lane_ids = {lane.id for lane in project.lanes if lane.kind == "auxiliary"}
        input_note_objects = sum(1 for note in chart.notes if note.lane_id in input_lane_ids)
        auxiliary_note_objects = sum(1 for note in chart.notes if note.lane_id in auxiliary_lane_ids)
        stats = {
            "difficulty": difficulty.value,
            "note_objects": input_note_objects,
            "auxiliary_note_objects": auxiliary_note_objects,
            "editor_event_objects": len(chart.notes),
            "playable_events": playable_count,
            "auxiliary_events": len(events) - playable_count,
            "event_count": len(events),
            "wav_count": len(wavs),
            "custom_key_sound_count": sum(
                raw_path.replace("\\", "/").lstrip("./").casefold().startswith("note/b2p_")
                for asset_id, index in wav_by_asset.items()
                if asset_id in referenced_asset_ids and (raw_path := wavs[index])
            ),
            "bpm_change_count": len(bpm_changes),
            "rhythm_change_count": len(rhythm_changes),
            "plaintext_size": len(plaintext),
            "container_size": len(container),
            "encoding": encoding,
            "round_trip_verified": True,
        }
        return Pm3BuildResult(
            plaintext=plaintext,
            container=container,
            filename=filename,
            song_id=output_song_id,
            header=header,
            slot=selected_slot,
            warnings=list(dict.fromkeys(warnings)),
            stats=stats,
            key_sound_paths={
                asset_id: wavs[index]
                for asset_id, index in wav_by_asset.items()
                if index in wavs
            },
        )

    def round_trip_project(self, project: SongProject, difficulty: DifficultyId) -> dict[str, Any]:
        built = self.build_with_report(project, difficulty)
        decrypted = decrypt_chart(built.container)
        document, _ = parse_chart_text(decrypted.plaintext)
        expected = []
        for note in project.difficulties[difficulty].notes:
            if note.lane_id in self.TRACK_TO_LANE.values():
                expected.append((note.lane_id, self._tick(note.pulse, project.timing.resolution, []), self._tick(note.length, project.timing.resolution, [])))
        actual: list[tuple[int, int, int]] = []
        by_track: dict[int, list[Pm3Event]] = {}
        for event in document.events:
            if event.track in self.TRACK_TO_LANE:
                by_track.setdefault(event.track, []).append(event)
        for track, items in by_track.items():
            pending: Pm3Event | None = None
            for event in sorted(items, key=lambda item: (item.tick, item.line_number)):
                if event.hold_end and pending is not None:
                    actual.append((self.TRACK_TO_LANE[track], pending.tick, event.tick - pending.tick))
                    pending = None
                elif event.hold_start:
                    pending = event
                elif not event.hold_end:
                    actual.append((self.TRACK_TO_LANE[track], event.tick, 0))
        expected.sort()
        actual.sort()
        return {
            "passed": expected == actual and built.stats["round_trip_verified"],
            "notes_before": len(expected),
            "notes_after": len(actual),
            "events_after": len(document.events),
            "slot": built.slot,
            "warnings": built.warnings,
        }

    def _source_document(self, project: SongProject) -> Pm3ChartDocument | None:
        text = project.unknown_data.get("pm3_plaintext")
        if not isinstance(text, str) or not text.strip():
            return None
        encoding = str(project.game_specific_data.get("pm3_encoding", "cp950")).replace("-replace", "")
        try:
            payload = text.encode(encoding)
            return parse_chart_text(payload)[0]
        except (LookupError, UnicodeError, Pm3ParseError):
            return None

    def _tick(self, pulse: int, resolution: int, warnings: list[str]) -> int:
        raw = pulse * self.SOURCE_PPQN / resolution
        tick = round(raw)
        if abs(raw - tick) > 1e-9:
            warnings.append(f"Pulse {pulse} 无法精确表示为 PM3 tick，已量化到 {tick}")
        if tick < 0 or tick > 0x3FFF:
            raise Pm3FormatError(f"Pulse {pulse} 转换后的 PM3 tick {tick} 超出 0..16383")
        return tick

    def _build_bpm_changes(self, project: SongProject, warnings: list[str]) -> list[tuple[int, int]]:
        values = {0: round(project.timing.initial_bpm * 100)}
        for event in sorted(project.timing.bpm_events, key=lambda item: item.pulse):
            values[self._tick(event.pulse, project.timing.resolution, warnings)] = round(event.bpm * 100)
        return sorted(values.items())

    @staticmethod
    def _build_rhythm_changes(project: SongProject) -> list[tuple[int, int, int]]:
        raw = project.game_specific_data.get("pm3_rhythm_changes")
        if isinstance(raw, list):
            result = []
            for item in raw:
                if isinstance(item, dict):
                    try:
                        result.append((int(item["section"]), int(item["beats"]), int(item["tick"])))
                    except (KeyError, TypeError, ValueError):
                        continue
            if result:
                return sorted(result, key=lambda item: item[2])
        return [(1, 4, 0)]

    def _build_wavs(
        self,
        project: SongProject,
        source: Pm3ChartDocument | None,
        warnings: list[str],
        *,
        referenced_asset_ids: set[str],
        target_song_id: int | None = None,
    ) -> tuple[dict[int, str], dict[str, int]]:
        wavs = dict(source.wavs) if source else {}
        used = set(wavs)
        if target_song_id is not None:
            replacement = f"./{target_song_id:03d}/BG.wav"
            background_found = False
            for index, raw_path in list(wavs.items()):
                if PurePosixPath(raw_path.replace("\\", "/")).name.lower() == "bg.wav":
                    background_found = True
                    if raw_path != replacement:
                        wavs[index] = replacement
                        warnings.append(f"背景音乐逻辑路径已改为 {replacement}")
            if not background_found and self._has_prepared_background(project):
                index = next((candidate for candidate in range(1024) if candidate not in used), None)
                if index is None:
                    raise Pm3FormatError("PM3 WAV 索引已超过 1024 个")
                wavs[index] = replacement
                used.add(index)
                warnings.append(f"已加入背景音乐 WAV {index}：{replacement}")
        by_asset: dict[str, int] = {}
        for asset in project.key_sounds:
            if asset.id not in referenced_asset_ids:
                continue
            extension = asset.extensions.get("pm3", {})
            index = extension.get("wav_index") if isinstance(extension, dict) else None
            if not isinstance(index, int) or index < 0 or index > 1023:
                index = next((candidate for candidate in range(1024) if candidate not in used), None)
                if index is None:
                    raise Pm3FormatError("PM3 WAV 索引已超过 1024 个")
                warnings.append(f"音色 {asset.name} 缺少 PM3 索引，已分配 WAV {index}")
            used.add(index)
            raw_path = extension.get("raw_path") if isinstance(extension, dict) else None
            if not isinstance(raw_path, str) or not raw_path:
                if target_song_id is not None:
                    raw_path = self._custom_key_sound_path(target_song_id, asset.id)
                else:
                    cleaned = asset.filename.replace("\\", "/")
                    cleaned = cleaned.removeprefix("media/sound/")
                    raw_path = f"./{cleaned}"
            wavs[index] = raw_path
            by_asset[asset.id] = index
        if not wavs:
            wavs[0] = "./note/default.wav"
            warnings.append("项目没有 PM3 WAV 表，已加入 WAV 0 占位路径")
        if max(wavs, default=0) > 1023:
            raise Pm3FormatError("PM3 WAV 索引必须在 0..1023")
        return dict(sorted(wavs.items())), by_asset

    def _build_events(
        self,
        project: SongProject,
        difficulty: DifficultyId,
        wavs: dict[int, str],
        wav_by_asset: dict[str, int],
        warnings: list[str],
    ) -> list[Pm3Event]:
        inverse_lanes = {lane: track for track, lane in self.TRACK_TO_LANE.items()}
        chart = project.difficulties[difficulty]
        lanes = {lane.id: lane for lane in project.lanes}
        unclassified_notes = [
            note for note in chart.notes
            if note.lane_id not in lanes or lanes[note.lane_id].kind == "anonymous"
        ]
        if unclassified_notes:
            lane_ids = ", ".join(map(str, sorted({note.lane_id for note in unclassified_notes})))
            raise Pm3FormatError(
                f"仍有 {len(unclassified_notes)} 个事件位于待分类 Track（Lane {lane_ids}）；"
                "请先迁移到六路 PM3 输入，或显式分类为 PM3 辅助 Track"
            )

        used_lane_ids = {note.lane_id for note in chart.notes}
        auxiliary_tracks: dict[int, int] = {}
        track_owners: dict[int, int] = {}
        for lane in project.lanes:
            if lane.kind != "auxiliary" or lane.id not in used_lane_ids:
                continue
            extension = lane.extensions.get("pm3", {})
            raw_track = extension.get("track_id") if isinstance(extension, dict) else None
            if not isinstance(raw_track, int) or raw_track < 6 or raw_track > 23 or raw_track == self.BACKGROUND_TRACK:
                raise Pm3FormatError(f"辅助 Lane {lane.id} 缺少有效 PM3 Track 编号（允许 6..15、17..23）")
            if raw_track in track_owners:
                raise Pm3FormatError(
                    f"辅助 Lane {track_owners[raw_track]} 与 Lane {lane.id} 同时使用 PM3 Track {raw_track}"
                )
            auxiliary_tracks[lane.id] = raw_track
            track_owners[raw_track] = lane.id

        events: list[Pm3Event] = []
        chart_pm3 = chart.extensions.get("pm3", {})
        if isinstance(chart_pm3, dict) and chart_pm3.get("auxiliary_model_version") == 1:
            preserved_events = [
                *self._event_rows(chart_pm3.get("background_events")),
                *self._event_rows(chart_pm3.get("opaque_events")),
            ]
        else:
            preserved_events = self._event_rows(project.unknown_data.get("pm3_auxiliary_events"))
        for raw in preserved_events:
            if not isinstance(raw, dict):
                continue
            try:
                event = Pm3Event(**{key: int(raw.get(key, 0)) for key in (
                    "track", "tick", "wav_index", "volume", "hold_start", "hold_end", "hold_number", "line_number"
                )})
            except (TypeError, ValueError):
                warnings.append("一条 PM3 辅助事件无效，未写入")
                continue
            if event.track < 0 or event.track > 23 or event.tick < 0 or event.tick > 0x3FFF:
                raise Pm3FormatError("PM3 辅助事件的 track 或 tick 超出范围")
            events.append(event)
        if (
            self._has_prepared_background(project)
            and not any(event.track == self.BACKGROUND_TRACK for event in events)
        ):
            background_wav = next(
                (
                    index
                    for index, raw_path in wavs.items()
                    if PurePosixPath(raw_path.replace("\\", "/")).name.casefold() == "bg.wav"
                ),
                None,
            )
            if background_wav is not None:
                events.append(Pm3Event(
                    self.BACKGROUND_TRACK, 0, background_wav, 127, 0, 0, 0, 0
                ))
                warnings.append("已在 Track 16 的 Tick 0 加入背景音乐触发事件")
        assets = {asset.id: asset for asset in project.key_sounds}
        for line_number, note in enumerate(chart.notes, start=1):
            lane = lanes.get(note.lane_id)
            if lane is None:
                raise Pm3FormatError(f"音符 {note.id} 的 Lane {note.lane_id} 不存在")
            if lane.kind == "input":
                track = inverse_lanes.get(note.lane_id)
                if track is None:
                    raise Pm3FormatError(f"输入 Lane {note.lane_id} 没有 PM3 六路映射")
                if not note.playable:
                    warnings.append(f"音符 {note.id} 位于 PM3 输入 Track，已按可操作事件写入")
            elif lane.kind == "auxiliary":
                track = auxiliary_tracks[note.lane_id]
                if note.playable:
                    warnings.append(f"音符 {note.id} 位于 PM3 辅助 Track，已按非计分事件写入")
            else:
                raise Pm3FormatError(f"音符 {note.id} 的 Lane {note.lane_id} 尚未分类")
            tick = self._tick(note.pulse, project.timing.resolution, warnings)
            asset_id = note.key_sound_id or lane.default_key_sound_id
            asset = assets.get(asset_id or "")
            if asset_id and asset is None:
                raise Pm3FormatError(f"音符 {note.id} 引用了不存在的 Key 音 {asset_id}")
            source = note.extensions.get("pm3", {})
            source_event = source.get("event", {}) if isinstance(source, dict) else {}
            source_wav = source_event.get("wav_index") if isinstance(source_event, dict) else None
            wav_index = wav_by_asset.get(asset.id) if asset else None
            if wav_index is None and isinstance(source_wav, int):
                wav_index = source_wav
            if wav_index is None:
                wav_index = 0
            if wav_index not in wavs:
                raise Pm3FormatError(f"音符 {note.id} 引用了未定义的 WAV {wav_index}")
            volume = max(0, min(255, round(note.volume * 127)))
            hold_number = source_event.get("hold_number", 1) if isinstance(source_event, dict) else 1
            if note.length:
                end_tick = self._tick(note.pulse + note.length, project.timing.resolution, warnings)
                if end_tick <= tick:
                    raise Pm3FormatError(f"长音 {note.id} 的 PM3 终点没有晚于起点")
                events.append(Pm3Event(track, tick, wav_index, volume, 1, 0, max(1, int(hold_number)), line_number))
                end_source = source.get("hold_end_event", {}) if isinstance(source, dict) else {}
                end_wav = end_source.get("wav_index", wav_index) if isinstance(end_source, dict) else wav_index
                end_volume = end_source.get("volume", volume) if isinstance(end_source, dict) else volume
                if end_wav not in wavs:
                    end_wav = wav_index
                events.append(Pm3Event(track, end_tick, int(end_wav), max(0, min(255, int(end_volume))), 0, 1, 0, line_number))
            elif lane.kind == "auxiliary" and isinstance(source_event, dict) and source_event:
                events.append(Pm3Event(
                    track,
                    tick,
                    wav_index,
                    volume,
                    max(0, min(1, int(source_event.get("hold_start", 0)))),
                    max(0, min(1, int(source_event.get("hold_end", 0)))),
                    max(0, int(source_event.get("hold_number", 0))),
                    line_number,
                ))
            else:
                events.append(Pm3Event(track, tick, wav_index, volume, 0, 0, 0, line_number))
        counts: dict[int, int] = {}
        for event in events:
            counts[event.track] = counts.get(event.track, 0) + 1
        overflowing = [track for track, count in counts.items() if count > 512]
        if overflowing:
            raise Pm3FormatError(f"PM3 单轨最多 512 个事件，超出轨道：{overflowing}")
        return sorted(events, key=lambda item: (item.track, item.tick, item.line_number, item.hold_end))

    @staticmethod
    def _has_prepared_background(project: SongProject) -> bool:
        package = project.game_specific_data.get("pm3_package")
        audio = package.get("audio") if isinstance(package, dict) else None
        return isinstance(audio, dict) and isinstance(audio.get("background"), dict)

    @staticmethod
    def _custom_key_sound_path(song_id: int, asset_id: str) -> str:
        digest = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:16]
        return f"./note/b2p_{song_id:03d}_{digest}.wav"

    @staticmethod
    def _event_rows(value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _render_chart(
        bpm_changes: list[tuple[int, int]],
        rhythm_changes: list[tuple[int, int, int]],
        events: list[Pm3Event],
        wavs: dict[int, str],
        total_note: int,
        unknown_lines: list[dict[str, Any]],
    ) -> bytes:
        lines = [
            "#------ BASE DATA ------", "",
            f"ChangeBPMCount {len(bpm_changes)}",
            "#------ [Event] [BPM] ------",
            *(f"ChangeBPM\t{tick}\t{bpm}" for tick, bpm in bpm_changes), "",
            f"ChangeRhythmCount {len(rhythm_changes)}",
            "#------ [Section] [Beat] [Event] ------",
            *(f"ChangeRhythm\t{section}\t{beats}\t{tick}" for section, beats, tick in rhythm_changes), "",
        ]
        by_track: dict[int, list[Pm3Event]] = {}
        for event in events:
            by_track.setdefault(event.track, []).append(event)
        for track, track_events in sorted(by_track.items()):
            lines.extend((
                f"#------ TRACK NUMBER {track} ------", f"TRACK {track}",
                "#------ [Grid] [Idx] [Vol] [HSta] [HEnd] [HNum] ------",
            ))
            lines.extend(
                f"EVENT {event.tick} {event.wav_index} {event.volume} {event.hold_start} {event.hold_end} {event.hold_number}"
                for event in track_events
            )
            lines.extend(("EVENT -1", ""))
        lines.extend(("#------ WAVE INFO ------", f"WAVNUM {len(wavs)}", "#------ [Idx] [WavPath] ------"))
        lines.extend(f"WAV {index:03d} {path}" for index, path in sorted(wavs.items()))
        lines.extend(("", f"TotalNote {total_note}", ""))
        preserved = [str(item.get("text", "")) for item in unknown_lines if isinstance(item, dict) and item.get("text")]
        if preserved:
            lines.extend(("#------ PRESERVED UNKNOWN DATA ------", *preserved, ""))
        lines.extend(("#------ FILE END ------", ""))
        try:
            return "\r\n".join(lines).encode("cp950")
        except UnicodeEncodeError as exc:
            raise Pm3FormatError(f"PM3 文本含有 CP950 无法编码的字符：{exc}") from exc

    @staticmethod
    def _output_filename(
        project: SongProject,
        difficulty: DifficultyId,
        *,
        song_id: int | None = None,
    ) -> str:
        if song_id is not None:
            return f"p{song_id:03d}_{difficulty.value}.enc"
        source = Path(project.metadata.source_name or "").stem
        if re.fullmatch(r"[A-Za-z0-9_-]+", source):
            if source.lower().endswith(tuple(f"_{value}" for value in Pm3Adapter.DIFFICULTY_SUFFIXES)):
                return f"{source}.enc"
        game_id = project.metadata.game_song_id or "p000"
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", game_id)[:32] or "p000"
        return f"{safe_id}_{difficulty.value}.enc"

    @classmethod
    def _pulse(cls, tick: int) -> int:
        return max(0, tick * cls.PULSE_SCALE)

    @classmethod
    def _difficulty_from_filename(cls, filename: str) -> DifficultyId | None:
        stem = Path(filename).stem.lower()
        for suffix, difficulty in cls.DIFFICULTY_SUFFIXES.items():
            if stem.endswith(f"_{suffix}"):
                return difficulty
        return None

    @staticmethod
    def _song_stem(filename: str) -> str:
        stem = Path(filename).stem
        return re.sub(r"_(easy|normal|hard|special|master)$", "", stem, flags=re.I) or "未命名曲目"

    @staticmethod
    def _song_id(filename: str) -> str | None:
        match = re.search(r"p(\d{3})", Path(filename).stem, re.I)
        return f"p{match.group(1)}" if match else None

    @staticmethod
    def _artist(value: str) -> str:
        return re.sub(r"^原唱[：:]\s*", "", value).strip() or value

    @staticmethod
    def _duration_with_bpm(
        length_ticks: int,
        changes: list[tuple[int, int]],
        initial_bpm: float,
    ) -> float:
        if length_ticks <= 0 or initial_bpm <= 0:
            return 0
        current_tick = 0
        current_bpm = initial_bpm
        seconds = 0.0
        for tick, raw_bpm in sorted(changes, key=lambda item: item[0]):
            if tick < current_tick or tick > length_ticks:
                continue
            seconds += (tick - current_tick) / Pm3Adapter.SOURCE_PPQN * 60 / current_bpm
            candidate = raw_bpm / 100
            if 0 < candidate <= 1000:
                current_bpm = candidate
            current_tick = tick
        seconds += (length_ticks - current_tick) / Pm3Adapter.SOURCE_PPQN * 60 / current_bpm
        return max(0, seconds)

    @staticmethod
    def _initial_bpm(document: Pm3ChartDocument, song_info: Pm3SongInfo | None) -> float:
        at_zero = next((raw / 100 for tick, raw in document.bpm_changes if tick == 0 and raw > 0), None)
        if at_zero and at_zero <= 1000:
            return at_zero
        if song_info and 0 < song_info.bpm / 100 <= 1000:
            return song_info.bpm / 100
        return 120

    def _create_assets(
        self,
        project: SongProject,
        document: Pm3ChartDocument,
        song_info: Pm3SongInfo | None,
        game_root: Path | None,
    ) -> dict[int, KeySoundAsset]:
        result: dict[int, KeySoundAsset] = {}
        song_number = song_info.song_id if song_info else self._numeric_song_id(project.metadata.game_song_id)
        wav_dir = song_info.wav_dir if song_info else (f"{song_number:03d}" if song_number is not None else "")
        for index, raw_path in sorted(document.wavs.items()):
            normalized = raw_path.replace("\\", "/")
            filename = PurePosixPath(normalized).name
            if filename.lower() == "bg.wav":
                if song_number is not None:
                    relative = f"media/sound/BG/BG_{song_number:03d}.ogg"
                    fallback = f"media/sound/BG/BG_{song_number:03d}.wav"
                    if game_root and not (game_root / relative).is_file() and (game_root / fallback).is_file():
                        relative = fallback
                    project.audio_assets.append(AudioAsset(
                        name="PM3 Background Music", filename=relative, duration=project.metadata.audio_duration,
                    ))
                    project.source_files.append(self._resource_ref(game_root, relative, "background-audio"))
                continue
            relative = self._resolve_key_sound_path(normalized)
            asset = KeySoundAsset(
                name=filename or f"WAV {index}",
                filename=relative,
                source="pm3",
                extensions={"pm3": {
                    "wav_index": index,
                    "raw_path": raw_path,
                    "resource": self._resource_ref(game_root, relative, "key-sound"),
                }},
            )
            result[index] = asset
            project.key_sounds.append(asset)
            project.source_files.append(self._resource_ref(game_root, relative, "key-sound"))
        if song_number is not None:
            preview = f"media/sound/preview/p{song_number:03d}.wav"
            if not game_root or (game_root / preview).is_file():
                project.audio_assets.append(AudioAsset(name="PM3 Preview", filename=preview))
                project.source_files.append(self._resource_ref(game_root, preview, "preview-audio"))
        project.game_specific_data["pm3_wav_dir"] = wav_dir
        return result

    @staticmethod
    def _numeric_song_id(value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"(\d{1,4})", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _resolve_key_sound_path(raw_path: str) -> str:
        cleaned = raw_path.replace("\\", "/").lstrip("./")
        if cleaned.lower().startswith("sound/"):
            return f"media/{cleaned}"
        return f"media/sound/{cleaned}"

    @staticmethod
    def _resource_ref(game_root: Path | None, relative: str, role: str) -> dict[str, Any]:
        result: dict[str, Any] = {"role": role, "root_id": "game", "path": relative}
        if game_root:
            path = game_root / relative
            result["exists"] = path.is_file()
            if path.is_file():
                result["size"] = path.stat().st_size
        return result

    def _create_auxiliary_notes(
        self,
        project: SongProject,
        target: list[Note],
        events: list[Pm3Event],
        assets: dict[int, KeySoundAsset],
        difficulty: DifficultyId,
    ) -> None:
        track_ids = sorted({event.track for event in events})
        lane_by_track: dict[int, Lane] = {}
        next_lane_id = max((lane.id for lane in project.lanes), default=0) + 1
        for index, track in enumerate(track_ids):
            lane = Lane(
                id=next_lane_id,
                code=f"pm3_aux_{track}",
                display_name=f"PM3 Aux Track {track}",
                color=self.AUXILIARY_COLORS[index % len(self.AUXILIARY_COLORS)],
                hand="either",
                kind="auxiliary",
                extensions={"pm3": {"track_id": track, "source_difficulty": difficulty.value}},
            )
            project.lanes.append(lane)
            lane_by_track[track] = lane
            next_lane_id += 1

        for event in events:
            lane = lane_by_track[event.track]
            asset = assets.get(event.wav_index)
            note = Note(
                lane_id=lane.id,
                pulse=self._pulse(event.tick),
                key_sound_id=asset.id if asset else None,
                volume=max(0, min(2, event.volume / 127)),
                playable=False,
                source="pm3",
                extensions={"pm3": {
                    "event": event.as_dict(),
                    "source_tick": event.tick,
                    "source_track": event.track,
                    "auxiliary": True,
                }},
            )
            target.append(note)
            if asset and lane.id not in asset.lane_ids:
                asset.lane_ids.append(lane.id)
            if asset and lane.default_key_sound_id is None:
                lane.default_key_sound_id = asset.id

    def _create_notes(
        self,
        target: list[Note],
        events: list[Pm3Event],
        assets: dict[int, KeySoundAsset],
        warnings: list[str],
    ) -> None:
        by_track: dict[int, list[Pm3Event]] = {}
        for event in events:
            by_track.setdefault(event.track, []).append(event)
        for track, track_events in sorted(by_track.items()):
            pending: tuple[Pm3Event, Note] | None = None
            for event in sorted(track_events, key=lambda item: (item.tick, item.line_number)):
                event_data = event.as_dict()
                key_sound_id = assets[event.wav_index].id if event.wav_index in assets else None
                if event.hold_end:
                    if pending is None:
                        warnings.append(f"TRACK {track} Tick {event.tick} 的长音终点没有起点")
                    else:
                        start, note = pending
                        note.length = max(0, self._pulse(event.tick - start.tick))
                        note.extensions.setdefault("pm3", {})["hold_end_event"] = event_data
                        pending = None
                    continue
                note = Note(
                    lane_id=self.TRACK_TO_LANE[track],
                    pulse=self._pulse(event.tick),
                    key_sound_id=key_sound_id,
                    volume=max(0, min(2, event.volume / 127)),
                    source="pm3",
                    extensions={"pm3": {"event": event_data, "source_tick": event.tick}},
                )
                target.append(note)
                if key_sound_id and note.lane_id not in assets[event.wav_index].lane_ids:
                    assets[event.wav_index].lane_ids.append(note.lane_id)
                if event.hold_start:
                    if pending is not None:
                        warnings.append(f"TRACK {track} Tick {event.tick} 出现重叠长音起点")
                    pending = (event, note)
            if pending is not None:
                warnings.append(f"TRACK {track} Tick {pending[0].tick} 的长音起点没有终点")

    def _create_bar_lines(self, project: SongProject, document: Pm3ChartDocument) -> None:
        max_tick = max(
            [event.tick for event in document.events]
            + [tick for _, _, tick in document.rhythm_changes]
            + [0]
        )
        changes = sorted(document.rhythm_changes, key=lambda item: item[2])
        if not changes:
            changes = [(1, 4, 0)]
        seen: set[int] = set()
        for index, (section, beats, start_tick) in enumerate(changes):
            next_tick = changes[index + 1][2] if index + 1 < len(changes) else max_tick + max(beats, 1) * self.SOURCE_PPQN
            interval = max(beats, 1) * self.SOURCE_PPQN
            tick = max(0, start_tick)
            while tick <= next_tick:
                pulse = self._pulse(tick)
                if pulse not in seen:
                    project.timing.bar_lines.append(BarLine(
                        pulse=pulse,
                        extensions={"pm3": {"section": section, "beats": beats, "source_tick": tick}},
                    ))
                    seen.add(pulse)
                tick += interval
        project.timing.bar_lines.sort(key=lambda line: line.pulse)

    def _attach_mv_resources(self, project: SongProject, game_root: Path | None) -> None:
        config = project.mv_configuration["pm3"]
        resources: list[str] = []
        if game_root:
            for relative_root in config["resource_roots"]:
                directory = game_root / relative_root
                if directory.is_dir():
                    resources.extend(
                        path.relative_to(game_root).as_posix()
                        for path in sorted(directory.glob("*.swf"))
                    )
            controller = game_root / config["controller"]
            if controller.is_file():
                resources.append(config["controller"])
        config["resources"] = list(dict.fromkeys(resources))
        for relative in config["resources"]:
            project.source_files.append(self._resource_ref(game_root, relative, "mv"))

    def _resource_manifest(
        self,
        document: Pm3ChartDocument,
        song_info: Pm3SongInfo | None,
        game_root: Path | None,
    ) -> dict[str, list[dict[str, Any]]]:
        song_number = song_info.song_id if song_info else None
        key_sounds: list[dict[str, Any]] = []
        audio: list[dict[str, Any]] = []
        for index, raw_path in sorted(document.wavs.items()):
            if PurePosixPath(raw_path.replace("\\", "/")).name.lower() == "bg.wav":
                if song_number is not None:
                    relative = f"media/sound/BG/BG_{song_number:03d}.ogg"
                    fallback = f"media/sound/BG/BG_{song_number:03d}.wav"
                    if game_root and not (game_root / relative).is_file() and (game_root / fallback).is_file():
                        relative = fallback
                    audio.append(self._resource_ref(game_root, relative, "background-audio"))
                continue
            relative = self._resolve_key_sound_path(raw_path)
            key_sounds.append({
                **self._resource_ref(game_root, relative, "key-sound"),
                "wav_index": index,
                "raw_path": raw_path,
            })
        if song_number is not None:
            preview = f"media/sound/preview/p{song_number:03d}.wav"
            audio.append(self._resource_ref(game_root, preview, "preview-audio"))
        mv: list[dict[str, Any]] = []
        if game_root:
            for relative_root in ("media/ui/mv", "media/ui_mv1", "media/ui_mv2", "media/ui_mv3", "media/ui_mv4", "media/ui_mv5", "media/ui_mv6"):
                directory = game_root / relative_root
                if directory.is_dir():
                    mv.extend(
                        self._resource_ref(game_root, path.relative_to(game_root).as_posix(), "mv")
                        for path in sorted(directory.glob("*.swf"))
                    )
        return {"audio": audio, "key_sounds": key_sounds, "mv": mv}
