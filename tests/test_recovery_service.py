"""Recovery service sweeps durable queues without coupling their failure paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_nettools.event_store import EventStore
from agent_nettools.recovery_service import RecoveryService


def test_recovery_service_claims_expired_work_and_drains_notifications(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted", now=now)
    store.acquire_lease("event-1", owner="crashed", lease_seconds=10, now=now)
    seen = []

    result = RecoveryService(
        store,
        owner="recovery",
        event_runner=lambda record: seen.append(record.event_id),
        notification_drain=lambda _store: (object(),),
    ).sweep_once(now=now + timedelta(seconds=11))

    assert result.claimed_events == ("event-1",)
    assert result.notification_outcomes == 1
    assert seen == ["event-1"]


def test_recovery_service_requires_lease_margin(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")

    try:
        RecoveryService(store, lease_seconds=149)
    except ValueError as exc:
        assert "at least 60" in str(exc)
    else:
        raise AssertionError("short lease was accepted")


def test_recovery_service_skips_default_outbox_drain_without_telegram_sender(monkeypatch, tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    monkeypatch.setattr("agent_nettools.recovery_service.configured_telegram_sender", lambda: None)

    result = RecoveryService(store).sweep_once()

    assert result.notification_outcomes == 0


def test_recovery_service_persists_sweep_liveness(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)

    RecoveryService(store, notification_drain=lambda _store: ()).sweep_once(now=now)

    health = store.health_snapshot(now=now)
    assert health["recovery_sweep"]["events_claimed"] == 0
    assert health["recovery_sweep"]["age_seconds"] == 0.0