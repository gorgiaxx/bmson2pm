from __future__ import annotations

from collections import Counter, defaultdict

from ..models import DifficultyId, SongProject, ValidationIssue
from .timing import pulse_to_seconds


def validate_project(project: SongProject, difficulty: DifficultyId | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    lane_by_id = {lane.id: lane for lane in project.lanes}
    lane_ids = set(lane_by_id)
    charts = (
        [(difficulty, project.difficulties[difficulty])]
        if difficulty is not None
        else list(project.difficulties.items())
    )
    if project.timing.initial_bpm <= 0:
        issues.append(ValidationIssue(severity="error", code="timing.bpm", message="初始 BPM 必须大于 0"))
    for bpm in project.timing.bpm_events:
        if bpm.bpm <= 0:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="timing.bpm_event",
                    message="BPM 事件必须大于 0",
                    pulse=bpm.pulse,
                )
            )

    for difficulty_id, chart in charts:
        ids = Counter(note.id for note in chart.notes)
        notes_by_lane: dict[int, list] = defaultdict(list)
        simultaneous_lanes: dict[int, set[int]] = defaultdict(set)
        for note in chart.notes:
            if ids[note.id] > 1:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="note.duplicate_id",
                        message="音符 ID 重复",
                        difficulty=difficulty_id,
                        note_id=note.id,
                        pulse=note.pulse,
                    )
                )
            if note.lane_id not in lane_ids:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="note.invalid_lane",
                        message=f"Lane {note.lane_id} 不存在",
                        difficulty=difficulty_id,
                        note_id=note.id,
                        pulse=note.pulse,
                    )
                )
            notes_by_lane[note.lane_id].append(note)
            lane = lane_by_id.get(note.lane_id)
            if lane is not None and lane.kind == "input" and note.playable:
                simultaneous_lanes[note.pulse].add(note.lane_id)
            if project.metadata.audio_duration > 0:
                seconds = pulse_to_seconds(project.timing, note.pulse)
                if seconds > project.metadata.audio_duration:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="note.after_audio",
                            message="音符超出音乐时长",
                            difficulty=difficulty_id,
                            note_id=note.id,
                            pulse=note.pulse,
                        )
                    )
        for lane_id, lane_notes in notes_by_lane.items():
            lane = lane_by_id.get(lane_id)
            if lane is None or lane.kind != "input":
                continue
            ordered = sorted(lane_notes, key=lambda note: (note.pulse, note.length, note.id))
            for left, right in zip(ordered, ordered[1:]):
                if left.pulse == right.pulse:
                    uniform = left.length == right.length
                    issues.append(
                        ValidationIssue(
                            severity="warning" if uniform else "error",
                            code="note.layered" if uniform else "note.nonuniform_layer",
                            message=(
                                f"Lane {lane_id} 同一位置存在叠音"
                                if uniform
                                else f"Lane {lane_id} 同一位置的叠音长度不一致"
                            ),
                            difficulty=difficulty_id,
                            note_id=right.id,
                            pulse=right.pulse,
                        )
                    )
                    continue
                if right.pulse - left.pulse < project.timing.resolution / 8:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="playability.close_notes",
                            message=f"Lane {lane_id} 的音符间隔小于三十二分音符",
                            difficulty=difficulty_id,
                            note_id=right.id,
                            pulse=right.pulse,
                        )
                    )
            active_long_notes = []
            for note in ordered:
                active_long_notes = [
                    active for active in active_long_notes if active.pulse + active.length > note.pulse
                ]
                if any(active.pulse < note.pulse for active in active_long_notes):
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="note.overlap",
                            message=f"Lane {lane_id} 的长音符区间互相重叠",
                            difficulty=difficulty_id,
                            note_id=note.id,
                            pulse=note.pulse,
                        )
                    )
                if note.length > 0:
                    active_long_notes.append(note)
        for pulse, active_lane_ids in simultaneous_lanes.items():
            if len(active_lane_ids) <= 1:
                continue
            both_hand_lane_ids = {
                lane_id for lane_id in active_lane_ids
                if lane_by_id[lane_id].hand == "both"
            }
            if both_hand_lane_ids:
                both_names = "、".join(
                    lane_by_id[lane_id].display_name for lane_id in sorted(both_hand_lane_ids)
                )
                other_names = "、".join(
                    lane_by_id[lane_id].display_name
                    for lane_id in sorted(active_lane_ids - both_hand_lane_ids)
                )
                message = (
                    f"{both_names}需要双手，不能与{other_names}同时激活"
                    if other_names
                    else f"{both_names}均需要双手，不能同时激活"
                )
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="playability.both_hands_conflict",
                        message=message,
                        difficulty=difficulty_id,
                        pulse=pulse,
                    )
                )
            elif len(active_lane_ids) > 2:
                lane_names = "、".join(
                    lane_by_id[lane_id].display_name for lane_id in sorted(active_lane_ids)
                )
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="playability.too_many_simultaneous",
                        message=f"同一时刻激活了 {len(active_lane_ids)} 个 Track（{lane_names}），超出双手上限",
                        difficulty=difficulty_id,
                        pulse=pulse,
                    )
                )
        anonymous_lane_ids = {lane.id for lane in project.lanes if lane.kind == "anonymous"}
        anonymous_notes = [
            note for note in chart.notes if note.lane_id in anonymous_lane_ids
        ]
        if anonymous_notes:
            issues.append(ValidationIssue(
                severity="info",
                code="track.anonymous_notes",
                message=f"{len(anonymous_notes)} 个事件仍在待分类 Track，导出 PM3 前需迁移或指定辅助 Track",
                difficulty=difficulty_id,
            ))
    return issues
