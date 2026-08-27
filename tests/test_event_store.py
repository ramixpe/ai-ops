from __future__ import annotations

import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_nettools.event_store import (
    EventLeaseError,
    EventStore,
    EventStoreError,
    EventTransitionError,
)
from agent_nettools.incident_case import IncidentIdentity, IncidentState


def _store(tmp_path) -> EventStore:
    return EventStore(tmp_path / "state" / "events.sqlite3")


def test_creates_one_received_event_and_returns_it_for_a_duplicate(tmp_path):
    store = _store(tmp_path)

    created, was_created = store.create_or_get(
        event_id="event-1", device="PE2", payload={"mnemonic": "ROUTING-BGP-5-ADJCHANGE"}
    )
    duplicate, duplicate_was_created = store.create_or_get(
        event_id="event-1", device="PE2", payload={"mnemonic": "different"}
    )

    assert was_created is True
    assert created.state == "received"
    assert duplicate_was_created is False
    assert duplicate.payload == created.payload
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path.parent).st_mode) == 0o700
    connection = store._connect()
    try:
        sidecars = [path for path in (store.path.with_name(store.path.name + "-wal"), store.path.with_name(store.path.name + "-shm")) if path.exists()]
        assert sidecars
        assert all(stat.S_IMODE(os.stat(path).st_mode) == 0o600 for path in sidecars)
    finally:
        connection.close()


def test_strict_duplicate_refuses_reused_identity_with_changed_content(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={"phase": "started"})

    with pytest.raises(EventStoreError, match="different content"):
        store.create_or_get(
            event_id="event-1",
            device="PE2",
            payload={"phase": "recovered"},
            strict=True,
        )


def test_syslog_identity_allows_only_received_timestamp_to_change(tmp_path):
    store = _store(tmp_path)
    original = {"schema_version": 2, "source_kind": "syslog", "received_at": "2026-08-27T00:00:00+00:00", "mnemonic": "BGP"}
    later = {**original, "received_at": "2026-08-27T00:01:00+00:00"}
    store.create_or_get(event_id="event-1", device="PE2", payload=original)

    record, created = store.create_or_get(event_id="event-1", device="PE2", payload=later)

    assert created is False
    assert record.payload == original
    with pytest.raises(EventStoreError, match="identity differs"):
        store.create_or_get(event_id="event-1", device="PE2", payload={**later, "mnemonic": "ISIS"})


def test_store_refuses_an_unknown_future_schema_version(tmp_path):
    path = tmp_path / "state" / "events.sqlite3"
    EventStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (999, '2026-08-24T00:00:00+00:00')")

    with pytest.raises(EventStoreError, match="newer schema version"):
        EventStore(path)


def test_populated_v1_store_upgrades_to_v7_without_losing_event_state(tmp_path):
    path = tmp_path / "state" / "events.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-08-24T00:00:00+00:00');
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, device TEXT NOT NULL, state TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                ticket_id TEXT, lease_owner TEXT, lease_expires_at TEXT, terminal_reason TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO events(event_id, device, state, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy-event", "PE2", "completed", '{"mnemonic":"BGP"}', "2026-08-24T00:00:00+00:00", "2026-08-24T00:01:00+00:00"),
        )

    store = EventStore(path)

    assert store.get("legacy-event").payload == {"mnemonic": "BGP"}
    with sqlite3.connect(path) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert version == 7
    assert {"retry_at", "lease_epoch"} <= columns
    assert {"incidents", "narrowing_shadow_records", "procedure_approvals", "procedure_simulations"} <= tables


def test_compaction_report_is_read_only_and_reports_database_bytes(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted", now=now - timedelta(days=8))
    store.transition("event-1", "running", now=now - timedelta(days=8))
    store.transition("event-1", "completed", now=now - timedelta(days=8))

    report = store.compaction_report(now=now)

    assert report["database_bytes"] > 0
    assert report["completed_event_candidates"] == 1
    assert store.get("event-1") is not None


def test_compaction_backs_up_and_deletes_only_unreferenced_aged_records(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="old", device="PE2", payload={})
    store.transition("old", "admitted", now=now - timedelta(days=8))
    store.transition("old", "running", now=now - timedelta(days=8))
    store.transition("old", "completed", now=now - timedelta(days=8))
    store.create_or_get(event_id="retained", device="PE2", payload={})
    store.transition("retained", "admitted", now=now - timedelta(days=8))
    store.enqueue_notification(event_id="retained", destination="telegram:1", kind="card:x", payload={})

    result = store.compact(now=now)

    assert result["events_deleted"] == 1
    assert store.get("old") is None
    assert store.get("retained") is not None
    assert Path(result["backup_path"]).is_file()


def test_state_machine_records_a_ticket_bound_terminal_transition(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    store.transition("event-1", "admitted")
    store.transition("event-1", "running")
    completed = store.transition("event-1", "completed", ticket_id="000001")

    assert completed.state == "completed"
    assert completed.ticket_id == "000001"


def test_lease_renewal_requires_the_current_owner_epoch_and_live_lease(tmp_path):
    store = _store(tmp_path)
    started = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted", now=started)
    leased = store.acquire_lease("event-1", owner="worker-a", lease_seconds=10, now=started)

    renewed = store.renew_lease(
        "event-1", owner="worker-a", lease_epoch=leased.lease_epoch, lease_seconds=30,
        now=started + timedelta(seconds=5),
    )

    assert renewed.lease_epoch == leased.lease_epoch
    assert renewed.lease_expires_at is not None
    with pytest.raises(EventLeaseError, match="requires lease owner"):
        store.renew_lease(
            "event-1", owner="worker-b", lease_epoch=leased.lease_epoch, lease_seconds=30,
            now=started + timedelta(seconds=5),
        )
    with pytest.raises(EventLeaseError, match="expired"):
        store.renew_lease(
            "event-1", owner="worker-a", lease_epoch=leased.lease_epoch, lease_seconds=30,
            now=started + timedelta(seconds=36),
        )


def test_notification_finish_refuses_a_stale_same_owner_epoch(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    item, _ = store.enqueue_notification(event_id="event-1", destination="telegram:1", kind="card:test", payload={})
    first = store.claim_notification(item.outbox_id, owner="worker", lease_seconds=1, now=now)
    second = store.claim_notification(item.outbox_id, owner="worker", lease_seconds=10, now=now + timedelta(seconds=2))

    with pytest.raises(EventLeaseError, match="epoch"):
        store.finish_notification(item.outbox_id, sent=True, owner="worker", lease_epoch=first.lease_epoch, now=now + timedelta(seconds=2))
    assert store.finish_notification(item.outbox_id, sent=True, owner="worker", lease_epoch=second.lease_epoch, now=now + timedelta(seconds=2)).state == "sent"


def test_incident_store_joins_exact_active_identity_and_allows_resolved_recurrence(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.create_or_get(event_id="event-2", device="PE2", payload={})
    store.create_or_get(event_id="event-3", device="PE2", payload={})
    identity = IncidentIdentity("PE2", "transport", "Gi0/0/0/0")
    started = datetime(2026, 8, 26, tzinfo=timezone.utc)

    first, created = store.create_or_join_incident(identity=identity, event_id="event-1", now=started)
    joined, joined_created = store.create_or_join_incident(identity=identity, event_id="event-2", now=started)
    resolved = store.transition_incident(first.incident_id, IncidentState.RESOLVED, now=started)
    recurring, recurring_created = store.create_or_join_incident(identity=identity, event_id="event-3", now=started)

    assert created is True
    assert joined_created is False
    assert joined.event_ids == ("event-1", "event-2")
    assert resolved.state is IncidentState.RESOLVED
    assert recurring_created is True
    assert recurring.incident_id != first.incident_id


def test_narrowing_shadow_records_are_event_scoped_and_never_active(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    record = store.record_narrowing_shadow(
        event_id="event-1",
        mode="shadow",
        candidates=({"kind": "interface", "id": "Gi0/0/0/0", "device": "PE2"},),
        decision={"kind": "narrow", "index": 0},
        refusal=None,
    )

    assert record.active is False
    assert store.narrowing_shadow_records(event_id="event-1") == (record,)


def test_procedure_audit_persists_one_time_approval_and_dry_run_receipt(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    approval = store.record_procedure_approval(
        proposal_digest="digest", procedure="collect_device_evidence", version=1,
        parameters={"device": "PE2"}, nonce="nonce", expires_at=now + timedelta(minutes=5),
        signature="signature", now=now,
    )
    consumed = store.consume_procedure_approval(proposal_digest="digest", nonce="nonce", now=now)
    simulation = store.record_procedure_simulation(
        proposal_digest="digest", status="not_executed", network_activity=False,
        device_writes=False, verification="not_executed", now=now,
    )

    assert approval.consumed_at is None
    assert consumed.consumed_at is not None
    assert simulation.network_activity is False
    with pytest.raises(EventStoreError, match="replayed"):
        store.consume_procedure_approval(proposal_digest="digest", nonce="nonce", now=now)


def test_list_events_filters_by_state_and_device_in_updated_order(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="old", device="PE1", payload={})
    store.create_or_get(event_id="current", device="PE2", payload={})
    store.transition("current", "admitted")

    admitted = store.list_events(state="admitted")
    pe1 = store.list_events(device="PE1")

    assert [event.event_id for event in admitted] == ["current"]
    assert [event.event_id for event in pe1] == ["old"]
    assert store.list_events(limit=0) == ()


def test_invalid_transition_is_refused_without_mutating_event(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    with pytest.raises(EventTransitionError, match="received to completed"):
        store.transition("event-1", "completed")

    assert store.get("event-1").state == "received"


def test_unknown_event_is_a_store_error(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(EventStoreError, match="does not exist"):
        store.transition("missing", "admitted")


def test_retryable_failure_can_be_readmitted_but_completed_event_cannot(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="retry", device="PE2", payload={})
    store.transition("retry", "admitted")
    store.transition("retry", "running")
    store.transition("retry", "retryable_failed", reason="provider timeout")
    assert store.transition("retry", "admitted").state == "admitted"

    store.create_or_get(event_id="done", device="PE2", payload={})
    store.transition("done", "admitted")
    store.transition("done", "running")
    store.transition("done", "completed")
    with pytest.raises(EventTransitionError):
        store.transition("done", "admitted")


def test_retry_schedule_backoff_and_dead_letter_are_durable_and_queryable(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="retry", device="PE2", payload={})
    store.transition("retry", "admitted")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)

    first = store.schedule_retry("retry", reason="provider timeout", ticket_id="000001", base_seconds=10, max_attempts=2, now=started)
    assert first.attempt_count == 1
    assert first.terminal is False
    assert store.get("retry").ticket_id == "000001"
    assert store.eligible_retries(now=started + timedelta(seconds=9)) == ()
    assert [event.event_id for event in store.eligible_retries(now=started + timedelta(seconds=10))] == ["retry"]

    second = store.schedule_retry("retry", reason="provider timeout", base_seconds=10, max_attempts=2, now=started + timedelta(seconds=10))
    assert second.terminal is True
    assert [event.event_id for event in store.dead_letters()] == ["retry"]


def test_admission_shadow_records_legacy_outcome_without_authority_change(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    record = store.record_admission_shadow(event_id="event-1", legacy_admitted=False, legacy_reason="event_idempotency")

    assert record.legacy_admitted is False
    assert record.durable_state == "received"
    assert store.admission_shadow_records(event_id="event-1") == (record,)


def test_health_snapshot_exposes_shadow_retry_dead_letter_and_outbox_counts(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="retry", device="PE2", payload={})
    store.transition("retry", "admitted")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store.schedule_retry("retry", reason="timeout", base_seconds=10, max_attempts=1, now=started)
    store.create_or_get(event_id="event-2", device="PE2", payload={})
    store.record_admission_shadow(event_id="event-2", legacy_admitted=True, legacy_reason=None)
    store.enqueue_notification(event_id="event-2", destination="telegram:123", kind="card", payload={})

    snapshot = store.health_snapshot(now=started + timedelta(seconds=5))

    assert snapshot["dead_letter_count"] == 1
    assert snapshot["admission_shadow"] == {"legacy_admitted": 1, "legacy_refused": 0}
    assert snapshot["outbox"]["pending"] == 1


def test_active_lease_refuses_a_competing_worker_and_expired_lease_recovers(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted")
    started = datetime(2026, 8, 23, tzinfo=timezone.utc)

    leased = store.acquire_lease("event-1", owner="worker-a", lease_seconds=30, now=started)
    assert leased.state == "running"
    assert leased.lease_owner == "worker-a"
    with pytest.raises(EventLeaseError, match="worker-a"):
        store.acquire_lease(
            "event-1", owner="worker-b", lease_seconds=30, now=started + timedelta(seconds=1)
        )

    recovered = store.acquire_lease(
        "event-1", owner="worker-b", lease_seconds=30, now=started + timedelta(seconds=31)
    )
    assert recovered.lease_owner == "worker-b"
    assert recovered.lease_epoch == leased.lease_epoch + 1

    with pytest.raises(EventLeaseError, match="epoch"):
        store.transition(
            "event-1",
            "completed",
            lease_owner="worker-a",
            lease_epoch=leased.lease_epoch,
            now=started + timedelta(seconds=32),
        )
    completed = store.transition(
        "event-1",
        "completed",
        lease_owner="worker-b",
        lease_epoch=recovered.lease_epoch,
        now=started + timedelta(seconds=32),
    )
    assert completed.state == "completed"


def test_notification_outbox_is_idempotent_leased_and_retryable(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    item, created = store.enqueue_notification(
        event_id="event-1", destination="telegram:123", kind="root", payload={"ticket_id": "INC-1"}
    )
    duplicate, duplicate_created = store.enqueue_notification(
        event_id="event-1", destination="telegram:123", kind="root", payload={"ticket_id": "different"}
    )
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    claimed = store.claim_notification(item.outbox_id, owner="worker-a", lease_seconds=30, now=started)
    with pytest.raises(EventLeaseError, match="worker-a"):
        store.finish_notification(
            item.outbox_id,
            sent=True,
            now=started,
            owner="worker-b",
            lease_epoch=claimed.lease_epoch,
        )
    retryable = store.finish_notification(
        item.outbox_id,
        sent=False,
        error="provider timeout",
        now=started,
        owner="worker-a",
        lease_epoch=claimed.lease_epoch,
    )
    assert store.eligible_notifications(now=started + timedelta(seconds=4)) == ()
    retried = store.claim_notification(
        item.outbox_id,
        owner="worker-b",
        lease_seconds=30,
        now=started + timedelta(seconds=5),
    )
    sent = store.finish_notification(
        item.outbox_id,
        sent=True,
        now=started + timedelta(seconds=5),
        owner="worker-b",
        lease_epoch=retried.lease_epoch,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.outbox_id == item.outbox_id
    assert claimed.state == "sending"
    assert retryable.state == "pending"
    assert retryable.retry_at == (started + timedelta(seconds=5)).isoformat()
    assert retryable.last_error == "provider timeout"
    assert retried.attempts == 2
    assert sent.state == "sent"


def test_notification_retries_dead_letter_and_surface_in_health(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    item, _ = store.enqueue_notification(
        event_id="event-1", destination="telegram:123", kind="root", payload={}
    )
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)

    claimed = store.claim_notification(item.outbox_id, owner="worker", lease_seconds=30, now=started)
    dead = store.finish_notification(
        item.outbox_id,
        sent=False,
        error="provider unavailable",
        max_attempts=1,
        now=started,
        owner="worker",
        lease_epoch=claimed.lease_epoch,
    )

    assert dead.state == "dead_letter"
    assert store.outbox_dead_letters() == (dead,)
    assert store.health_snapshot(now=started)["outbox_dead_letter_count"] == 1


def test_health_and_time_based_queries_reject_naive_datetimes(tmp_path):
    store = _store(tmp_path)
    naive = datetime(2026, 8, 24)

    for operation in (
        lambda: store.health_snapshot(now=naive),
        lambda: store.eligible_retries(now=naive),
        lambda: store.recoverable_events(now=naive),
        lambda: store.eligible_notifications(now=naive),
    ):
        with pytest.raises(EventStoreError, match="timezone-aware"):
            operation()


def test_telegram_card_receipt_is_upserted_per_event_and_chat(tmp_path):
    store = _store(tmp_path)
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    first = store.record_telegram_card(
        event_id="event-1", chat_id="123", message_id=44, render_hash="first"
    )
    updated = store.record_telegram_card(
        event_id="event-1", chat_id="123", message_id=44, render_hash="second"
    )

    assert first.message_id == 44
    assert updated.render_hash == "second"
    assert store.telegram_card_receipt(event_id="event-1", chat_id="123") == updated
