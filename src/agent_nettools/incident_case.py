"""Conservative IncidentCase domain contract for shadow operational workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["IncidentCase", "IncidentIdentity", "IncidentState"]


class IncidentState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class IncidentIdentity:
    """Exact code-owned cause identity; proximity is not identity."""

    device: str
    cause_rung: str
    cause_subject: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.device, self.cause_rung, self.cause_subject)):
            raise ValueError("incident identity fields must be non-empty")


@dataclass(frozen=True)
class IncidentCase:
    """Immutable incident lifecycle record; storage and consumers remain separate."""

    identity: IncidentIdentity
    state: IncidentState
    event_ids: tuple[str, ...]
    opened_at: datetime
    updated_at: datetime

    @classmethod
    def open(cls, identity: IncidentIdentity, *, event_id: str, now: datetime) -> "IncidentCase":
        if not event_id:
            raise ValueError("incident event identity must be non-empty")
        if now.tzinfo is None:
            raise ValueError("incident timestamp must be timezone-aware")
        return cls(identity, IncidentState.OPEN, (event_id,), now, now)

    def can_join(self, identity: IncidentIdentity) -> bool:
        """Only an identical deterministic cause can join a case."""

        return self.state in {IncidentState.OPEN, IncidentState.ACKNOWLEDGED} and self.identity == identity

    def join_event(self, identity: IncidentIdentity, *, event_id: str, now: datetime) -> "IncidentCase":
        """Add one distinct event only when its deterministic identity matches."""

        if now.tzinfo is None:
            raise ValueError("incident timestamp must be timezone-aware")
        if not event_id:
            raise ValueError("incident event identity must be non-empty")
        if not self.can_join(identity):
            raise ValueError("event identity cannot join this incident")
        if event_id in self.event_ids:
            return self
        return IncidentCase(self.identity, self.state, (*self.event_ids, event_id), self.opened_at, now)

    def transition(self, target: IncidentState, *, now: datetime) -> "IncidentCase":
        allowed = {
            IncidentState.OPEN: {IncidentState.ACKNOWLEDGED, IncidentState.RESOLVED},
            IncidentState.ACKNOWLEDGED: {IncidentState.RESOLVED},
            IncidentState.RESOLVED: {IncidentState.ARCHIVED},
            IncidentState.ARCHIVED: set(),
        }
        if now.tzinfo is None:
            raise ValueError("incident timestamp must be timezone-aware")
        if target not in allowed[self.state]:
            raise ValueError(f"cannot transition incident from {self.state} to {target}")
        return IncidentCase(self.identity, target, self.event_ids, self.opened_at, now)