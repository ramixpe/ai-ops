"""Typed, coverage-aware temporal correlation without causal inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

__all__ = ["ChangeEvidence", "CorrelationClass", "CorrelationResult", "EventOnset", "correlate_change"]


class CorrelationClass(Enum):
    PRECEDING_CHANGE = "preceding_change"
    CONCURRENT_EVENT = "concurrent_event"
    RECURRING_EPISODE = "recurring_episode"
    NO_MATCH_COMPLETE_COVERAGE = "no_match_complete_coverage"
    COVERAGE_LIMITED = "coverage_limited"
    TEMPORALLY_INCOHERENT = "temporally_incoherent"


@dataclass(frozen=True)
class EventOnset:
    event_id: str
    observed_at: datetime
    coverage_complete: bool
    clock_quality: str


@dataclass(frozen=True)
class ChangeEvidence:
    change_id: str
    observed_at: datetime
    source: str
    coverage_complete: bool


@dataclass(frozen=True)
class CorrelationResult:
    classification: CorrelationClass
    change_id: str | None
    delta_seconds: float | None
    limitation: str | None


def correlate_change(
    onset: EventOnset,
    changes: tuple[ChangeEvidence, ...],
    *,
    preceding_window_seconds: float = 300.0,
    concurrent_window_seconds: float = 30.0,
) -> CorrelationResult:
    """Classify time relationship only; callers must not render it as cause."""

    if onset.clock_quality != "trusted":
        return CorrelationResult(CorrelationClass.TEMPORALLY_INCOHERENT, None, None, "event clock quality is not trusted")
    if not onset.coverage_complete or any(not change.coverage_complete for change in changes):
        return CorrelationResult(CorrelationClass.COVERAGE_LIMITED, None, None, "event or change coverage is incomplete")
    candidates = []
    for change in changes:
        delta = (onset.observed_at - change.observed_at).total_seconds()
        if 0 <= delta <= preceding_window_seconds:
            candidates.append((delta, change))
    if not candidates:
        return CorrelationResult(CorrelationClass.NO_MATCH_COMPLETE_COVERAGE, None, None, None)
    delta, change = min(candidates, key=lambda item: item[0])
    classification = CorrelationClass.CONCURRENT_EVENT if delta <= concurrent_window_seconds else CorrelationClass.PRECEDING_CHANGE
    return CorrelationResult(classification, change.change_id, delta, None)