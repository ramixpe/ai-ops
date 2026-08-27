"""Durable view reducer and explicit debounce coordinator for Telegram cards."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .event_store import EventStore, OutboxRecord
from .investigation_activity import ActivityKind, ActivityStatus, InvestigationActivity
from .investigation_view import InvestigationStage, InvestigationView, reduce_view
from .telegram_delivery import enqueue_card
from .telegram_renderer import render_card

__all__ = ["TelegramCardCoordinator"]


class TelegramCardCoordinator:
    """Persists card state; callers decide when to invoke ``flush``."""

    def __init__(self, store: EventStore, *, chat_id: str, debounce_seconds: float = 1.0) -> None:
        if debounce_seconds < 0:
            raise ValueError("debounce_seconds must be non-negative")
        self.store = store
        self.chat_id = chat_id
        self.debounce_seconds = debounce_seconds

    def apply(self, activity: InvestigationActivity, *, now: datetime | None = None) -> OutboxRecord | None:
        """Reduce and persist one activity; initial/terminal cards enqueue immediately."""

        current = self.store.telegram_card_state(event_id=activity.event_id)
        view = _decode_view(current.payload) if current is not None else InvestigationView.new(
            incident_id=activity.incident_id, event_id=activity.event_id
        )
        updated = reduce_view(view, activity)
        if updated is view:
            return None
        timestamp = now or datetime.now(timezone.utc)
        immediate = activity.kind in {ActivityKind.STARTED, ActivityKind.COMPLETED, ActivityKind.FAILED}
        dirty_at = None if immediate else timestamp.isoformat()
        self.store.save_telegram_card_state(
            event_id=activity.event_id,
            payload=_encode_view(updated),
            dirty_at=dirty_at,
        )
        return self._enqueue(updated) if immediate else None

    def flush(self, *, event_id: str, now: datetime | None = None) -> OutboxRecord | None:
        """Enqueue one debounced changed render when its delay has elapsed."""

        state = self.store.telegram_card_state(event_id=event_id)
        if state is None or state.dirty_at is None:
            return None
        dirty_at = datetime.fromisoformat(state.dirty_at)
        current = now or datetime.now(timezone.utc)
        if current < dirty_at + timedelta(seconds=self.debounce_seconds):
            return None
        view = _decode_view(state.payload)
        self.store.save_telegram_card_state(event_id=event_id, payload=state.payload, dirty_at=None)
        return self._enqueue(view)

    def _enqueue(self, view: InvestigationView) -> OutboxRecord | None:
        return enqueue_card(
            self.store,
            event_id=view.event_id,
            chat_id=self.chat_id,
            render=render_card(view),
        )


def _encode_view(view: InvestigationView) -> dict:
    return {
        **asdict(view),
        "status": view.status.value,
        "stages": [{**asdict(stage), "status": stage.status.value} for stage in view.stages],
        "applied_activity_ids": sorted(view.applied_activity_ids),
    }


def _decode_view(payload: dict) -> InvestigationView:
    return InvestigationView(
        incident_id=payload["incident_id"],
        event_id=payload["event_id"],
        status=ActivityStatus(payload["status"]),
        stages=tuple(
            InvestigationStage(
                name=item["name"], status=ActivityStatus(item["status"]), finding=item.get("finding"),
                rung=item.get("rung"), device=item.get("device"),
            )
            for item in payload["stages"]
        ),
        finding=payload.get("finding"),
        classification=payload.get("classification"),
        limitation=payload.get("limitation"),
        last_sequence=payload["last_sequence"],
        applied_activity_ids=frozenset(payload["applied_activity_ids"]),
        terminal=payload["terminal"],
    )