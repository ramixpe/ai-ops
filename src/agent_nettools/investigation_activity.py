"""Code-authored investigation activities for presentation reducers.

Activities are intentionally smaller than tool payloads. They carry only the
closed, operator-visible facts a renderer may show; raw evidence and model
prose remain in their existing durable records.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["ActivityKind", "ActivityStatus", "InvestigationActivity"]


class ActivityKind(Enum):
    STARTED = "started"
    PLAN_DECLARED = "plan_declared"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    RUNG_OBSERVED = "rung_observed"
    TOOL_REFUSED = "tool_refused"
    TOOL_SELECTED = "tool_selected"
    COMMAND_PREVIEW = "command_preview"
    TOOL_RESULT_SUMMARY = "tool_result_summary"
    VISIBLE_REASONING_TAIL = "visible_reasoning_tail"
    RETRYING = "retrying"
    HUMAN_INPUT_REQUIRED = "human_input_required"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    HEALTHY = "healthy"
    ANOMALY = "anomaly"
    REFUSED = "refused"
    RETRYING = "retrying"
    INCONCLUSIVE = "inconclusive"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class InvestigationActivity:
    """One ordered, renderer-safe incident state transition."""

    activity_id: str
    incident_id: str
    event_id: str
    sequence: int
    kind: ActivityKind
    status: ActivityStatus
    stage: str | None = None
    finding: str | None = None
    classification: str | None = None
    rung: str | None = None
    device: str | None = None
    limitation: str | None = None
    tool: str | None = None
    command_label: str | None = None
    duration_ms: int | None = None
    evidence_keys: tuple[str, ...] = ()
    visible_summary: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.activity_id or not self.incident_id or not self.event_id:
            raise ValueError("activity identity fields must be non-empty")
        if self.sequence < 0:
            raise ValueError("activity sequence must be non-negative")
        if self.kind in {ActivityKind.STAGE_STARTED, ActivityKind.STAGE_COMPLETED, ActivityKind.RUNG_OBSERVED}:
            if not self.stage:
                raise ValueError(f"{self.kind.value} activity requires a stage")
        if self.kind is ActivityKind.RUNG_OBSERVED and not self.rung:
            raise ValueError("rung_observed activity requires a rung")
        if self.kind is ActivityKind.COMPLETED and not self.classification:
            raise ValueError("completed activity requires a classification")
        if self.kind in {ActivityKind.TOOL_SELECTED, ActivityKind.COMMAND_PREVIEW, ActivityKind.TOOL_RESULT_SUMMARY}:
            if not self.tool:
                raise ValueError(f"{self.kind.value} activity requires a tool")
        if self.kind is ActivityKind.COMMAND_PREVIEW and not self.command_label:
            raise ValueError("command_preview activity requires a command label")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("activity duration must be non-negative")
        if len(self.visible_summary) > 2 or any(not sentence.strip() for sentence in self.visible_summary):
            raise ValueError("visible reasoning tail must contain one or two non-empty sentences")