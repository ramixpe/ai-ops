"""Campaign monitoring emits bounded metadata and no free-form evidence."""

from __future__ import annotations

import pytest

from agent_nettools import ticket as ticket_module
from agent_nettools.campaign_reporting import (
    CampaignPhaseEvent,
    open_campaign_ticket,
    queue_campaign_phase,
    render_campaign_phase,
)
from agent_nettools.event_store import EventStore


def test_campaign_phase_render_is_deterministic_and_contains_only_typed_metadata():
    event = CampaignPhaseEvent(
        campaign_id="chaos-20260827-a", round_ordinal=1, round_total=24,
        phase="armed", target="PE2", role="edge", fault_id="interface_shutdown",
        outcome="pending", rollback_deadline="2026-08-27T12:07:00Z", ticket_id="000123",
    )

    rendered = render_campaign_phase(event)

    assert rendered == render_campaign_phase(event)
    assert "PE2 (edge)" in rendered.text
    assert "Rollback deadline" in rendered.text
    assert rendered.terminal is False


def test_campaign_phase_refuses_unknown_phase_or_invalid_round():
    with pytest.raises(ValueError, match="unsupported"):
        CampaignPhaseEvent("c", 1, 24, "raw_output", "PE2", "edge", "interface_shutdown", "pending")
    with pytest.raises(ValueError, match="round ordinal"):
        CampaignPhaseEvent("c", 0, 24, "started", "PE2", "edge", "interface_shutdown", "pending")


def test_campaign_phase_queues_durable_card_only_when_notification_is_enabled(monkeypatch, tmp_path):
    event = CampaignPhaseEvent("campaign-a", 1, 24, "armed", "PE2", "edge", "interface_shutdown", "pending")
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setenv("NETTOOLS_EVENT_NOTIFY", "1")
    monkeypatch.setenv("NETTOOLS_TELEGRAM_LIVE_CARD", "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS", "123")

    queued = queue_campaign_phase(store, event)

    assert len(queued) == 1
    assert queued[0].destination == "telegram:123"
    assert store.get("campaign:campaign-a:round:1") is not None

    recovered = CampaignPhaseEvent(
        "campaign-a", 1, 24, "recovered", "PE2", "edge",
        "interface_shutdown", "verified",
    )
    queue_campaign_phase(store, recovered)
    assert store.get("campaign:campaign-a:round:1").payload["phase"] == "recovered"


def test_campaign_ticket_contains_typed_phase_metadata_only(monkeypatch, tmp_path):
    monkeypatch.setenv(ticket_module.NETTOOLS_TICKET_DIR_ENV, str(tmp_path))
    event = CampaignPhaseEvent("campaign-a", 1, 24, "armed", "PE2", "edge", "interface_shutdown", "pending")

    ticket = open_campaign_ticket(event)
    ticket.close()
    stored = ticket_module.read_ticket(ticket.path)

    assert stored["header"]["entry_point"] == "network_chaos_campaign"
    assert stored["intent"]["campaign_id"] == "campaign-a"
    assert "raw_output" not in str(stored)
