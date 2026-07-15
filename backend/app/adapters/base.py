from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..models import DifficultyId, SongProject, ValidationIssue


@dataclass(frozen=True)
class DetectionResult:
    supported: bool
    confidence: float
    format_name: str
    reason: str = ""


class ChartFormatAdapter(ABC):
    """Boundary for BMSON, BMS and future PM3 format implementations."""

    @abstractmethod
    def detect(self, payload: bytes, filename: str = "") -> DetectionResult:
        raise NotImplementedError

    @abstractmethod
    def parse(self, payload: bytes, difficulty: DifficultyId = DifficultyId.hard) -> SongProject:
        raise NotImplementedError

    @abstractmethod
    def validate(self, payload: bytes) -> list[ValidationIssue]:
        raise NotImplementedError

    @abstractmethod
    def build(self, project: SongProject, difficulty: DifficultyId) -> bytes:
        raise NotImplementedError

    def round_trip_test(self, payload: bytes, difficulty: DifficultyId) -> dict[str, Any]:
        project = self.parse(payload, difficulty)
        rebuilt = self.build(project, difficulty)
        reparsed = self.parse(rebuilt, difficulty)
        before = project.difficulties[difficulty].notes
        after = reparsed.difficulties[difficulty].notes
        return {
            "passed": len(before) == len(after),
            "notes_before": len(before),
            "notes_after": len(after),
        }

