from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedBmson:
    data: dict[str, Any]
    source_version: str
    warnings: list[str]


def normalize_bmson(data: dict[str, Any]) -> NormalizedBmson:
    """Normalize supported BMSON revisions to the platform's 1.0 boundary."""

    raw_version = data.get("version")
    source_version = str(raw_version) if raw_version not in (None, "") else "0.21"
    if not _is_legacy_version(raw_version):
        return NormalizedBmson(deepcopy(data), source_version, [])

    normalized = deepcopy(data)
    normalized.pop("bpmNotes", None)
    normalized.pop("stopNotes", None)
    normalized.pop("soundChannel", None)
    normalized["version"] = "1.0.0"
    normalized["info"] = _normalize_info(data.get("info"))
    normalized["bpm_events"] = [
        _rename_fields(item, {"v": "bpm"}) for item in _object_list(data.get("bpmNotes"))
    ]
    normalized["stop_events"] = [
        _rename_fields(item, {"v": "duration"}) for item in _object_list(data.get("stopNotes"))
    ]
    normalized["sound_channels"] = [
        _normalize_channel(item) for item in _object_list(data.get("soundChannel"))
    ]
    if isinstance(data.get("bga"), dict):
        normalized["bga"] = _normalize_bga(data["bga"])
    return NormalizedBmson(
        normalized,
        source_version,
        ["BMSON 0.21 已归一化为 1.0 内部模型；导出将使用 1.0.0"],
    )


def _is_legacy_version(version: Any) -> bool:
    if version in (None, ""):
        return True
    text = str(version).strip()
    try:
        return int(text.split(".", 1)[0]) == 0
    except ValueError:
        return False


def _normalize_info(value: Any) -> dict[str, Any]:
    info = deepcopy(value) if isinstance(value, dict) else {}
    result = _rename_fields(info, {"initBPM": "init_bpm", "judgeRank": "judge_rank"})
    result.setdefault("mode_hint", "beat-7k")
    result.setdefault("resolution", 240)
    return result


def _normalize_channel(channel: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(channel)
    result["notes"] = [deepcopy(note) for note in _object_list(channel.get("notes"))]
    return result


def _normalize_bga(bga: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(bga)
    for old_key in ("bgaHeader", "bgaNotes", "layerNotes", "poorNotes"):
        result.pop(old_key, None)
    result["bga_header"] = [
        _rename_fields(item, {"ID": "id"}) for item in _object_list(bga.get("bgaHeader"))
    ]
    for old_key, new_key in (
        ("bgaNotes", "bga_events"),
        ("layerNotes", "layer_events"),
        ("poorNotes", "poor_events"),
    ):
        result[new_key] = [
            _rename_fields(item, {"ID": "id"}) for item in _object_list(bga.get(old_key))
        ]
    return result


def _rename_fields(value: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = deepcopy(value)
    for old_key, new_key in mapping.items():
        if old_key in result:
            result[new_key] = result.pop(old_key)
    return result


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
