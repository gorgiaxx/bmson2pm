from __future__ import annotations

from collections import defaultdict

from ..models import TimingMap


def pulse_to_seconds(timing: TimingMap, pulse: int) -> float:
    bpm_by_pulse: dict[int, list[float]] = defaultdict(list)
    stops_by_pulse: dict[int, int] = defaultdict(int)
    for event in timing.bpm_events:
        bpm_by_pulse[event.pulse].append(event.bpm)
    for event in timing.stop_events:
        stops_by_pulse[event.pulse] += event.duration_pulses

    current_pulse = 0
    current_bpm = timing.initial_bpm
    seconds = timing.chart_offset_ms / 1000
    for event_pulse in sorted(set(bpm_by_pulse) | set(stops_by_pulse)):
        if event_pulse >= pulse:
            break
        seconds += _pulse_duration(event_pulse - current_pulse, timing.resolution, current_bpm)
        current_pulse = event_pulse
        if bpm_by_pulse[event_pulse]:
            current_bpm = bpm_by_pulse[event_pulse][-1]
        seconds += _pulse_duration(stops_by_pulse[event_pulse], timing.resolution, current_bpm)
    seconds += _pulse_duration(pulse - current_pulse, timing.resolution, current_bpm)
    return seconds


def _pulse_duration(pulses: int, resolution: int, bpm: float) -> float:
    return pulses / resolution * 60 / bpm
