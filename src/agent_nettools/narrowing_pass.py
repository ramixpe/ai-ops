"""B-114 typed narrowing controller, initially limited to shadow decisions.

This module deliberately has no collector, tool, or command dependency. It
can record how a model would select a code-enumerated candidate, but cannot
perform the additional collection that an active narrowing pass would need.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from .descent import DescentResult
from .reasoning_gate import (
    Candidate,
    GateDecision,
    GateRefusal,
    NarrowRequest,
    candidates_for_descent,
    parse_decision,
)

__all__ = ["NarrowingMode", "ShadowNarrowingRecord", "shadow_decision", "shadow_payload"]


class NarrowingMode(StrEnum):
    """Rollout mode; only ``SHADOW`` is implemented in this module."""

    OFF = "off"
    SHADOW = "shadow"


@dataclass(frozen=True)
class ShadowNarrowingRecord:
    """A non-authoritative typed decision that cannot alter the descent."""

    mode: NarrowingMode
    candidates: tuple[Candidate, ...]
    decision: GateDecision | None
    refusal: str | None

    @property
    def active(self) -> bool:
        """Shadow records never dispatch a collection or modify a finding."""

        return False


def shadow_decision(
    descent: DescentResult,
    *,
    evidence_for: Callable[[str], Mapping[str, Any]],
    raw_decision: Mapping[str, Any] | None,
    mode: NarrowingMode,
) -> ShadowNarrowingRecord:
    """Parse a candidate index in shadow mode without creating a live branch."""

    if mode is NarrowingMode.OFF:
        return ShadowNarrowingRecord(mode, (), None, None)
    if descent.finding != "cause_not_localised":
        return ShadowNarrowingRecord(mode, (), None, "finding is not eligible for narrowing")

    candidates = candidates_for_descent(descent, evidence_for)
    if not candidates:
        return ShadowNarrowingRecord(mode, (), None, "no observed candidates are available")
    if raw_decision is None:
        return ShadowNarrowingRecord(mode, candidates, None, "no gate decision was supplied")
    try:
        decision = parse_decision(raw_decision, candidates)
    except GateRefusal as exc:
        return ShadowNarrowingRecord(mode, candidates, None, str(exc))
    return ShadowNarrowingRecord(mode, candidates, decision, None)


def shadow_payload(record: ShadowNarrowingRecord) -> dict[str, Any]:
    """Project shadow decisions without emitting model free text or raw input."""

    decision: dict[str, Any] | None = None
    if isinstance(record.decision, NarrowRequest):
        decision = {
            "kind": "narrow",
            "index": record.candidates.index(record.decision.target),
            "target": {
                "kind": record.decision.target.kind,
                "id": record.decision.target.id,
                "device": record.decision.target.device,
                "evidence_key": record.decision.target.evidence_key,
            },
        }
    elif record.decision is not None:
        decision = {"kind": "stop"}
    return {
        "mode": record.mode.value,
        "active": False,
        "candidates": [
            {"kind": candidate.kind, "id": candidate.id, "device": candidate.device, "evidence_key": candidate.evidence_key}
            for candidate in record.candidates
        ],
        "decision": decision,
        "refusal": record.refusal,
    }