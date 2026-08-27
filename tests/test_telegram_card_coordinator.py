from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_nettools.event_store import EventStore
from agent_nettools.investigation_activity import (
    ActivityKind,
    ActivityStatus,
    InvestigationActivity,
)
from agent_nettools.telegram_card_coordinator import TelegramCardCoordinator


def _activity(activity_id: str, sequence: int, kind: ActivityKind, status: ActivityStatus) -> InvestigationActivity:
    values = {
        "activity_id": activity_id,
        "incident_id": "INC-20260824-00001",
        "event_id": "event-1",
        "sequence": sequence,
        "kind": kind,
        "status": status,
        "stage": "bgp_session",
    }
    if kind is ActivityKind.COMPLETED:
        values["classification"] = "NO_ACTIVE_FAULT"
    return InvestigationActivity(**values)


def test_initial_and_terminal_cards_enqueue_immediately_while_progress_debounces(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    coordinator = TelegramCardCoordinator(store, chat_id="123", debounce_seconds=1)
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)

    initial = coordinator.apply(_activity("a-0", 0, ActivityKind.STARTED, ActivityStatus.QUEUED), now=started)
    coordinator.apply(_activity("a-1", 1, ActivityKind.STAGE_STARTED, ActivityStatus.RUNNING), now=started)
    early = coordinator.flush(event_id="event-1", now=started + timedelta(milliseconds=999))
    progress = coordinator.flush(event_id="event-1", now=started + timedelta(seconds=1))
    terminal = coordinator.apply(_activity("a-2", 2, ActivityKind.COMPLETED, ActivityStatus.COMPLETED), now=started)

    assert initial is not None
    assert early is None
    assert progress is not None
    assert terminal is not None
    assert store.telegram_card_state(event_id="event-1").dirty_at is None