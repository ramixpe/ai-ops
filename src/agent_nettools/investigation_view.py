"""Pure immutable reducer for one Telegram investigation card."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .investigation_activity import ActivityKind, ActivityStatus, InvestigationActivity

__all__ = ["InvestigationStage", "InvestigationView", "reduce_view"]

_TERMINAL_KINDS = frozenset({ActivityKind.COMPLETED, ActivityKind.FAILED})


@dataclass(frozen=True)
class InvestigationStage:
    name: str
    status: ActivityStatus
    finding: str | None = None
    rung: str | None = None
    device: str | None = None


@dataclass(frozen=True)
class InvestigationView:
    incident_id: str
    event_id: str
    status: ActivityStatus = ActivityStatus.QUEUED
    stages: tuple[InvestigationStage, ...] = ()
    finding: str | None = None
    classification: str | None = None
    limitation: str | None = None
    last_sequence: int = -1
    applied_activity_ids: frozenset[str] = frozenset()
    terminal: bool = False

    @classmethod
    def new(cls, *, incident_id: str, event_id: str) -> "InvestigationView":
        if not incident_id or not event_id:
            raise ValueError("view identity fields must be non-empty")
        return cls(incident_id=incident_id, event_id=event_id)

    @property
    def completed_checks(self) -> int:
        return sum(stage.status in {ActivityStatus.HEALTHY, ActivityStatus.ANOMALY} for stage in self.stages)

    @property
    def running_checks(self) -> int:
        return sum(stage.status in {ActivityStatus.RUNNING, ActivityStatus.RETRYING} for stage in self.stages)


def reduce_view(view: InvestigationView, activity: InvestigationActivity) -> InvestigationView:
    """Apply one activity, ignoring duplicate/stale updates and preserving terminality."""

    if (activity.incident_id, activity.event_id) != (view.incident_id, view.event_id):
        raise ValueError("activity identity does not match investigation view")
    if activity.activity_id in view.applied_activity_ids or activity.sequence <= view.last_sequence:
        return view
    if view.terminal:
        return view

    stages = {stage.name: stage for stage in view.stages}
    if activity.stage:
        stages[activity.stage] = InvestigationStage(
            name=activity.stage,
            status=activity.status,
            finding=activity.finding,
            rung=activity.rung,
            device=activity.device,
        )

    terminal = activity.kind in _TERMINAL_KINDS
    status = activity.status
    if activity.kind is ActivityKind.COMPLETED:
        status = ActivityStatus.COMPLETED
    elif activity.kind is ActivityKind.FAILED:
        status = ActivityStatus.FAILED

    return replace(
        view,
        status=status,
        stages=tuple(sorted(stages.values(), key=lambda stage: stage.name)),
        finding=activity.finding or view.finding,
        classification=activity.classification or view.classification,
        limitation=activity.limitation or view.limitation,
        last_sequence=activity.sequence,
        applied_activity_ids=view.applied_activity_ids | {activity.activity_id},
        terminal=terminal,
    )