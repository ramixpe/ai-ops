"""Tests for the B-707 ticket-to-Telegram lifecycle."""

from __future__ import annotations

import json
import stat
import time

from agent_nettools import event_notification as lifecycle


class _Telegram:
    def __init__(self):
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def send_text(self, text: str, *, reply_to_message_ids=(), require_message_ids=True):
        self.calls.append((text, tuple(reply_to_message_ids)))
        return [101] if not reply_to_message_ids else [102 + len(self.calls)]


def test_lifecycle_is_inert_until_explicitly_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv(lifecycle.NETTOOLS_EVENT_NOTIFY_ENV, raising=False)
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV, str(tmp_path / "state.json"))
    monkeypatch.setattr(lifecycle, "_telegram_sender", lambda: (_ for _ in ()).throw(AssertionError("sent")))

    notification = lifecycle.open_notification("event-1", ticket_id="ticket-1", device="PE2", subject="10.255.0.31")

    assert notification.enabled is False
    assert notification.root_message_ids == ()
    assert not (tmp_path / "state.json").exists()


def test_live_card_destinations_are_fail_closed_to_delivery_allowlist(monkeypatch):
    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_ENV, "1")
    monkeypatch.setenv(lifecycle.notifier.TELEGRAM_CHAT_ENV, "11,22")
    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS_ENV, "22,99")

    assert lifecycle.live_card_chat_ids() == ("22",)

    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_ENV, "0")
    assert lifecycle.live_card_chat_ids() == ()


def test_live_card_replacement_refuses_a_partial_chat_subset(monkeypatch):
    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_ENV, "1")
    monkeypatch.setenv(lifecycle.notifier.TELEGRAM_CHAT_ENV, "11,22")
    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS_ENV, "22")

    assert lifecycle.live_card_replaces_legacy() is False

    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS_ENV, "11,22")
    assert lifecycle.live_card_replaces_legacy() is True


def test_visible_reasoning_tail_is_disabled_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(lifecycle.NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL_ENV, raising=False)
    assert lifecycle.reasoning_tail_enabled() is False

    monkeypatch.setenv(lifecycle.NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL_ENV, "1")
    assert lifecycle.reasoning_tail_enabled() is True


def test_lifecycle_persists_root_and_replies_with_code_authored_milestones(monkeypatch, tmp_path):
    sender = _Telegram()
    relay_calls: list[dict] = []
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFY_ENV, "1")
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV, str(tmp_path / "state.json"))
    monkeypatch.setattr(lifecycle, "_telegram_sender", lambda: sender)
    monkeypatch.setattr(
        lifecycle.relay_policy,
        "relay",
        lambda report, **kwargs: relay_calls.append({"report": report, **kwargs}) or {
            "decision": "sent", "deliveries": [{"ok": True, "message_ids": [101]}]
        },
    )

    first = lifecycle.open_notification("event-1", ticket_id="ticket-1", device="PE2", subject="10.255.0.31")
    second = lifecycle.open_notification("event-1", ticket_id="ticket-1", device="PE2", subject="10.255.0.31")
    lifecycle.progress(first, "investigation started")
    lifecycle.close(first, outcome="resolved", finding="bgp_hold_timer_expired")

    assert first.root_message_ids == (101,)
    assert second.root_message_ids == (101,)
    assert len(relay_calls) == 1
    assert relay_calls[0]["ticket_id"] == "ticket-1"
    assert len(sender.calls) == 2
    assert sender.calls[0][1] == (101,)
    assert sender.calls[1][1] == (101,)
    assert "resolved" in sender.calls[1][0]
    assert "bgp_hold_timer_expired" in sender.calls[1][0]
    state = json.loads((tmp_path / "state.json").read_text())
    assert [record["action"] for record in state["event-1"]["audit"]] == [
        "pending", "opened", "progress", "closed",
    ]
    assert stat.S_IMODE((tmp_path / "state.json").stat().st_mode) == 0o600


def test_unreachable_terminal_outcome_is_audited_without_a_fault_reply(monkeypatch, tmp_path):
    sender = _Telegram()
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFY_ENV, "1")
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV, str(tmp_path / "state.json"))
    monkeypatch.setattr(lifecycle, "_telegram_sender", lambda: sender)
    monkeypatch.setattr(
        lifecycle.relay_policy,
        "relay",
        lambda report, **kwargs: {"decision": "sent", "deliveries": [{"ok": True, "message_ids": [101]}]},
    )
    notification = lifecycle.open_notification("event-1", ticket_id="ticket-1", device="PE2", subject="10.255.0.31")

    lifecycle.close(notification, outcome="needs human review", finding="device_unreachable")

    assert sender.calls == []
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["event-1"]["audit"][-1] == {
        "action": "terminal_suppressed",
        "outcome": "needs human review",
        "finding": "device_unreachable",
    }


def test_pending_root_delivery_is_not_sent_twice_by_a_second_process(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "event-1": {
            "ticket_id": "ticket-1",
            "status": "pending",
            "updated": time.time(),
            "audit": [],
        },
    }))
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFY_ENV, "1")
    monkeypatch.setenv(lifecycle.NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV, str(state_path))
    monkeypatch.setattr(
        lifecycle.relay_policy,
        "relay",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate root send")),
    )

    notification = lifecycle.open_notification(
        "event-1", ticket_id="ticket-1", device="PE2", subject="10.255.0.31"
    )

    assert notification.enabled is False
    assert notification.root_message_ids == ()
