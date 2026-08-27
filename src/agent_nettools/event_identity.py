"""Domain-specific identity checks for durable event payload updates."""

from __future__ import annotations

from typing import Any


class EventIdentityError(ValueError):
    """An event ID was reused for a different immutable domain identity."""


_SYSLOG_MUTABLE = frozenset({"received_at"})
_CAMPAIGN_MUTABLE = frozenset({"phase", "outcome", "rollback_deadline", "ticket_id", "incident_id", "event_id"})
_CAMPAIGN_IMMUTABLE = frozenset({"kind", "campaign_id", "round_ordinal", "round_total", "target", "role", "fault_id"})


def validate_payload_update(existing: dict[str, Any], proposed: dict[str, Any]) -> None:
    """Allow only declared volatile fields for known kinds; unknown kinds are strict."""

    if _is_syslog(existing) and _is_syslog(proposed):
        _validate_keys(existing, proposed, mutable=_SYSLOG_MUTABLE)
        return
    if existing.get("kind") == proposed.get("kind") == "campaign_phase":
        _validate_keys(existing, proposed, mutable=_CAMPAIGN_MUTABLE)
        return
    if existing != proposed:
        raise EventIdentityError("event payload identity differs for an unknown or incompatible kind")


def _is_syslog(payload: dict[str, Any]) -> bool:
    return payload.get("schema_version") == 2 and isinstance(payload.get("source_kind"), str)


def _validate_keys(existing: dict[str, Any], proposed: dict[str, Any], *, mutable: frozenset[str]) -> None:
    if set(existing) != set(proposed):
        raise EventIdentityError("event payload keys differ")
    immutable = set(existing) - mutable
    if any(existing[key] != proposed[key] for key in immutable):
        raise EventIdentityError("event payload immutable identity differs")