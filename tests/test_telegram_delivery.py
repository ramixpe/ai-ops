from __future__ import annotations

from agent_nettools.event_store import EventStore
from agent_nettools.investigation_activity import (
    ActivityKind,
    ActivityStatus,
    InvestigationActivity,
)
from agent_nettools.telegram_delivery import (
    deliver_activity,
    deliver_card,
    drain_notifications,
    enqueue_activity,
    enqueue_card,
)
from agent_nettools.telegram_renderer import TelegramRender


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, str]] = []

    def send_text_to(self, *, chat_id: str, text: str) -> int:
        self.calls.append((chat_id, None, text))
        return 100

    def edit_text(self, *, chat_id: str, message_id: int, text: str) -> int:
        self.calls.append((chat_id, message_id, text))
        return message_id

    def send_reply_text(self, *, chat_id: str, reply_to_message_id: int, text: str) -> int:
        self.calls.append((chat_id, reply_to_message_id, text))
        return 101


def _store(tmp_path) -> EventStore:
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    return store


def test_card_delivery_sends_then_edits_and_coalesces_unchanged_render(tmp_path):
    store = _store(tmp_path)
    notifier = _Notifier()
    first = TelegramRender("first", "hash-1", False)
    second = TelegramRender("second", "hash-2", True)

    initial = enqueue_card(store, event_id="event-1", chat_id="123", render=first)
    sent = deliver_card(store, outbox_id=initial.outbox_id, notifier=notifier, owner="worker")
    unchanged = enqueue_card(store, event_id="event-1", chat_id="123", render=first)
    update = enqueue_card(store, event_id="event-1", chat_id="123", render=second)
    edited = deliver_card(store, outbox_id=update.outbox_id, notifier=notifier, owner="worker")

    assert sent.state == "sent"
    assert unchanged is None
    assert edited.state == "sent"
    assert notifier.calls == [("123", None, "first"), ("123", 100, "second")]


def test_activity_delivery_replies_to_the_persisted_card_root(tmp_path):
    store = _store(tmp_path)
    notifier = _Notifier()
    store.record_telegram_card(event_id="event-1", chat_id="123", message_id=100, render_hash="card")
    activity = InvestigationActivity(
        activity_id="event-1:selected", incident_id="INC-1", event_id="event-1", sequence=1,
        kind=ActivityKind.TOOL_SELECTED, status=ActivityStatus.RUNNING, stage="bgp_session", tool="investigate_lab",
    )

    queued = enqueue_activity(store, chat_id="123", activity=activity)
    delivered = deliver_activity(store, outbox_id=queued.outbox_id, notifier=notifier, owner="worker")

    assert delivered.state == "sent"
    assert notifier.calls == [("123", 100, "🔎 Selecting investigate_lab for bgp_session.")]


def test_outbox_drain_retries_failed_delivery_after_backoff(tmp_path):
    from datetime import datetime, timedelta, timezone

    from agent_nettools.notifier import NotifierError

    class FlakyNotifier(_Notifier):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        def send_text_to(self, *, chat_id: str, text: str) -> int:
            if self.fail:
                raise NotifierError("temporary provider failure")
            return super().send_text_to(chat_id=chat_id, text=text)

    store = _store(tmp_path)
    notifier = FlakyNotifier()
    queued = enqueue_card(
        store,
        event_id="event-1",
        chat_id="123",
        render=TelegramRender("first", "hash-1", False),
    )
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)

    failed = drain_notifications(store, notifier=notifier, owner="worker", now=started)
    notifier.fail = False
    early = drain_notifications(
        store,
        notifier=notifier,
        owner="worker",
        now=started + timedelta(seconds=4),
    )
    sent = drain_notifications(
        store,
        notifier=notifier,
        owner="worker",
        now=started + timedelta(seconds=5),
    )

    assert failed[0].state == "pending"
    assert early == ()
    assert sent[0].state == "sent"
    assert sent[0].outbox_id == queued.outbox_id
