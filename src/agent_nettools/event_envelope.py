"""Versioned event-envelope compatibility adapters for the V2 transaction path.

This module translates the existing deterministic routing decision into the
durable event contract without changing routing or event-agent behavior. A
later migration may persist this envelope through ``EventStore`` only after
its crash/retry tests are in place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .event_routing import RoutingDecision

__all__ = ["EVENT_ENVELOPE_V2_SCHEMA_VERSION", "EventEnvelopeV2"]

EVENT_ENVELOPE_V2_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EventEnvelopeV2:
    """One canonical, provenance-bearing event ready for durable processing."""

    schema_version: int
    event_id: str
    source_kind: str
    received_at: str
    device: str | None
    object_type: str | None
    subject: str | None
    matched: str | None
    transition: str | None
    raw_trigger: str | None
    device_time: str | None
    source_timestamp_ns: str | None
    routing_reason: str
    routable: bool

    @classmethod
    def from_routing_decision(
        cls,
        decision: RoutingDecision,
        *,
        received_at: datetime | None = None,
    ) -> EventEnvelopeV2:
        """Adapt an existing routing decision without redefining its identity."""

        observed_at = received_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("received_at must carry timezone information")
        return cls(
            schema_version=EVENT_ENVELOPE_V2_SCHEMA_VERSION,
            event_id=decision.event_id,
            source_kind=decision.source_kind,
            received_at=observed_at.isoformat(),
            device=decision.device,
            object_type=decision.flow,
            subject=decision.subject,
            matched=decision.matched,
            transition=decision.transition,
            raw_trigger=decision.raw_event,
            device_time=None,
            source_timestamp_ns=decision.ingest_timestamp_ns,
            routing_reason=decision.reason,
            routable=decision.routable,
        )

    def payload(self) -> dict[str, Any]:
        """Store-safe representation retaining structured provenance fields."""

        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EventEnvelopeV2":
        """Validate a durable payload before a worker reconstructs routing state."""

        required = (
            "schema_version", "event_id", "source_kind", "received_at", "routing_reason", "routable"
        )
        if any(name not in payload for name in required):
            raise ValueError("event envelope payload is missing required fields")
        if payload["schema_version"] != EVENT_ENVELOPE_V2_SCHEMA_VERSION:
            raise ValueError("event envelope schema version is unsupported")
        if not isinstance(payload["event_id"], str) or not payload["event_id"]:
            raise ValueError("event envelope identity is invalid")
        if not isinstance(payload["received_at"], str):
            raise ValueError("event envelope receive time is invalid")
        try:
            received_at = datetime.fromisoformat(payload["received_at"])
        except ValueError as exc:
            raise ValueError("event envelope receive time is invalid") from exc
        if received_at.tzinfo is None:
            raise ValueError("event envelope receive time lacks timezone")
        return cls(
            schema_version=payload["schema_version"],
            event_id=payload["event_id"],
            source_kind=str(payload["source_kind"]),
            received_at=payload["received_at"],
            device=payload.get("device") if isinstance(payload.get("device"), str) else None,
            object_type=payload.get("object_type") if isinstance(payload.get("object_type"), str) else None,
            subject=payload.get("subject") if isinstance(payload.get("subject"), str) else None,
            matched=payload.get("matched") if isinstance(payload.get("matched"), str) else None,
            transition=payload.get("transition") if isinstance(payload.get("transition"), str) else None,
            raw_trigger=payload.get("raw_trigger") if isinstance(payload.get("raw_trigger"), str) else None,
            device_time=payload.get("device_time") if isinstance(payload.get("device_time"), str) else None,
            source_timestamp_ns=payload.get("source_timestamp_ns") if isinstance(payload.get("source_timestamp_ns"), str) else None,
            routing_reason=str(payload["routing_reason"]),
            routable=bool(payload["routable"]),
        )

    def routing_decision(self) -> RoutingDecision:
        """Reconstruct the decision a worker may pass to the bounded agent."""

        decision = RoutingDecision(
            routable=self.routable,
            flow=self.object_type,
            device=self.device,
            subject=self.subject,
            reason=self.routing_reason,
            source_kind=self.source_kind,
            matched=self.matched,
            transition=self.transition,
            raw_event=self.raw_trigger,
            ingest_timestamp_ns=self.source_timestamp_ns,
        )
        if decision.event_id != self.event_id:
            raise ValueError("event envelope identity does not match reconstructed routing decision")
        return decision