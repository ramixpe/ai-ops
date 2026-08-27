"""Contained read-only operator projections over durable event state."""

from __future__ import annotations

from typing import Any

from .event_store import EventRecord, EventState, EventStore
from .incident_case import IncidentState

__all__ = ["EventQueryService"]


class EventQueryService:
    """Expose lifecycle metadata without projecting stored raw event payloads."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def list_events(
        self,
        *,
        state: EventState | None = None,
        device: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """List bounded event summaries suitable for a local operator surface."""

        return tuple(
            self._event_summary(event)
            for event in self._store.list_events(state=state, device=device, limit=limit)
        )

    def event_detail(self, event_id: str) -> dict[str, Any] | None:
        """Return lifecycle metadata for one event, never its raw payload."""

        event = self._store.get(event_id)
        if event is None:
            return None
        detail = self._event_summary(event)
        detail["attempts"] = tuple(
            {"state": attempt.state, "recorded_at": attempt.recorded_at, "reason": attempt.reason}
            for attempt in self._store.event_attempts(event_id=event_id)
        )
        detail["outbox"] = tuple(
            {"kind": record.kind, "state": record.state, "attempts": record.attempts}
            for record in self._store.outbox_records(event_id=event_id)
        )
        detail["telegram_cards"] = tuple(
            {"chat_id": receipt.chat_id, "message_id": receipt.message_id, "render_hash": receipt.render_hash}
            for receipt in self._store.telegram_card_receipts(event_id=event_id)
        )
        detail["admission_shadow"] = tuple(
            {
                "legacy_admitted": record.legacy_admitted,
                "legacy_reason": record.legacy_reason,
                "durable_state": record.durable_state,
            }
            for record in self._store.admission_shadow_records(event_id=event_id)
        )
        detail["narrowing_shadow"] = tuple(
            {
                "mode": record.mode,
                "candidate_count": len(record.candidates),
                "decision": record.decision.get("kind") if record.decision is not None else None,
                "refusal": record.refusal,
            }
            for record in self._store.narrowing_shadow_records(event_id=event_id)
        )
        return detail

    def health(self) -> dict[str, Any]:
        """Return the store's bounded health snapshot."""

        return self._store.health_snapshot()

    def list_incidents(
        self,
        *,
        state: IncidentState | None = None,
        device: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """List exact-cause incident summaries without event payloads."""

        return tuple(
            self._incident_summary(incident)
            for incident in self._store.list_incidents(state=state, device=device, limit=limit)
        )

    def incident_detail(self, incident_id: str) -> dict[str, Any] | None:
        """Return bounded incident linkage; raw event payloads remain excluded."""

        incident = self._store.incident(incident_id)
        if incident is None:
            return None
        return self._incident_summary(incident)

    @staticmethod
    def _event_summary(event: EventRecord) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "device": event.device,
            "state": event.state,
            "ticket_id": event.ticket_id,
            "updated_at": event.updated_at,
            "retry_at": event.retry_at,
            "terminal_reason": event.terminal_reason,
        }

    @staticmethod
    def _incident_summary(incident) -> dict[str, Any]:
        return {
            "incident_id": incident.incident_id,
            "device": incident.identity.device,
            "cause_rung": incident.identity.cause_rung,
            "cause_subject": incident.identity.cause_subject,
            "state": incident.state.value,
            "event_ids": incident.event_ids,
            "opened_at": incident.opened_at,
            "updated_at": incident.updated_at,
            "resolved_at": incident.resolved_at,
        }