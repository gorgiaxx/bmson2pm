from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DifficultyId(str, Enum):
    easy = "easy"
    normal = "normal"
    hard = "hard"
    special = "special"
    master = "master"


class Metadata(BaseModel):
    title: str = "未命名曲目"
    artist: str = "未知艺术家"
    subtitle: str = ""
    game_song_id: str | None = None
    version: str = ""
    audio_duration: float = Field(default=120.0, ge=0)
    preview_time: float = Field(default=0.0, ge=0)
    import_format: str = "platform"
    source_name: str | None = None
    notes: str = ""


class Lane(BaseModel):
    id: int = Field(ge=1, le=255)
    code: str
    display_name: str
    color: str
    hand: Literal["left", "right", "either", "both"] = "either"
    kind: Literal["input", "anonymous", "auxiliary"] = "input"
    default_key_sound_id: str | None = None
    muted: bool = False
    extensions: dict[str, Any] = Field(default_factory=dict)


class ExtensibleModel(BaseModel):
    """Format-specific fields that the platform does not own."""

    extensions: dict[str, Any] = Field(default_factory=dict)


class BpmEvent(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    pulse: int = Field(ge=0)
    bpm: float = Field(gt=0, le=1000)


class StopEvent(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    pulse: int = Field(ge=0)
    duration_pulses: int = Field(ge=0)


class BarLine(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    pulse: int = Field(ge=0)


class TimingMap(BaseModel):
    resolution: int = Field(default=240, ge=24, le=9600)
    initial_bpm: float = Field(default=120.0, gt=0, le=1000)
    audio_offset_ms: float = 0
    chart_offset_ms: float = 0
    key_sound_offset_ms: float = 0
    mv_offset_ms: float = 0
    bpm_events: list[BpmEvent] = Field(default_factory=list)
    stop_events: list[StopEvent] = Field(default_factory=list)
    bar_lines: list[BarLine] = Field(default_factory=list)

    @field_validator("bar_lines", mode="before")
    @classmethod
    def migrate_legacy_bar_lines(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [{"pulse": item} if isinstance(item, int) else item for item in value]


class Note(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    lane_id: int
    pulse: int = Field(ge=0)
    length: int = Field(default=0, ge=0)
    key_sound_id: str | None = None
    volume: float = Field(default=1.0, ge=0, le=2)
    playable: bool = True
    continues: bool = False
    source: str = "manual"
    notes: str = ""


class DifficultyChart(BaseModel):
    id: DifficultyId
    display_name: str
    level: int = Field(default=1, ge=0, le=99)
    notes: list[Note] = Field(default_factory=list)
    locked: bool = False
    description: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict)


class KeySoundAsset(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    filename: str
    lane_ids: list[int] = Field(default_factory=list)
    volume: float = Field(default=1.0, ge=0, le=2)
    delay_ms: float = 0
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"


class AudioAsset(ExtensibleModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    filename: str
    duration: float = Field(default=0, ge=0)
    sample_rate: int | None = None


class SongProject(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.3"
    id: str = Field(default_factory=lambda: str(uuid4()))
    metadata: Metadata = Field(default_factory=Metadata)
    timing: TimingMap = Field(default_factory=TimingMap)
    lanes: list[Lane]
    difficulties: dict[DifficultyId, DifficultyChart]
    audio_assets: list[AudioAsset] = Field(default_factory=list)
    key_sounds: list[KeySoundAsset] = Field(default_factory=list)
    mv_configuration: dict[str, Any] = Field(default_factory=dict)
    game_specific_data: dict[str, Any] = Field(default_factory=dict)
    source_files: list[dict[str, Any]] = Field(default_factory=list)
    unknown_data: dict[str, Any] = Field(default_factory=dict)
    version_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("lanes")
    @classmethod
    def lane_ids_are_unique(cls, lanes: list[Lane]) -> list[Lane]:
        ids = [lane.id for lane in lanes]
        if len(ids) != len(set(ids)):
            raise ValueError("lane ids must be unique")
        return lanes

    @model_validator(mode="after")
    def migrate_legacy_six_input_lanes(self) -> "SongProject":
        old_to_new = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 6}
        lanes_by_id = {lane.id: lane for lane in self.lanes}
        old_codes = {
            1: {"small_left"},
            2: {"small_right"},
            3: {"rim_simultaneous", "rim_left"},
            4: {"rim_single", "rim_right"},
            5: {"head_simultaneous", "head_left"},
            6: {"head_single", "head_right"},
        }
        legacy_layout = all(
            lane_id in lanes_by_id and lanes_by_id[lane_id].code in codes
            for lane_id, codes in old_codes.items()
        )
        if legacy_layout:
            for lane in self.lanes:
                lane.id = old_to_new.get(lane.id, lane.id)
            for chart in self.difficulties.values():
                for note in chart.notes:
                    note.lane_id = old_to_new.get(note.lane_id, note.lane_id)
            for asset in self.key_sounds:
                asset.lane_ids = [old_to_new.get(lane_id, lane_id) for lane_id in asset.lane_ids]
            for key in ("bms_lane_map", "notelist_track_map", "pm3_track_lane_map"):
                mapping = self.game_specific_data.get(key)
                if isinstance(mapping, dict):
                    self.game_specific_data[key] = {
                        source: old_to_new.get(target, target)
                        if isinstance(target, int) else target
                        for source, target in mapping.items()
                    }

        canonical = {
            1: ("head_simultaneous", "鼓面同时击打", "both"),
            2: ("rim_single", "鼓缘单击", "either"),
            3: ("rim_simultaneous", "鼓缘同时击打", "both"),
            4: ("small_right", "右小鼓", "right"),
            5: ("small_left", "左小鼓", "left"),
            6: ("head_single", "鼓面单击", "either"),
        }
        for lane in self.lanes:
            if lane.id > 6 and lane.kind == "input":
                lane.kind = "anonymous"
            if lane.id in canonical and lane.kind == "input":
                lane.code, lane.display_name, lane.hand = canonical[lane.id]
        self.lanes.sort(key=lambda lane: lane.id)
        self.schema_version = "1.3"
        self.game_specific_data["lane_semantics"] = "pm3-six-input-v3"
        return self


class ProjectSummary(BaseModel):
    id: str
    title: str
    artist: str
    updated_at: datetime
    note_count: int


class ValidationIssue(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    difficulty: DifficultyId | None = None
    note_id: str | None = None
    pulse: int | None = None


class CreateProjectRequest(BaseModel):
    title: str = "未命名曲目"
    artist: str = "未知艺术家"
    initial_bpm: float = Field(default=120.0, gt=0, le=1000)
