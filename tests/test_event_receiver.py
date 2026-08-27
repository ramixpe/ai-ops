from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from agent_nettools.event_receiver import (
    EventReceiverError,
    EventWorker,
    LeaseHeartbeat,
    receive_syslog,
)
from agent_nettools.event_store import EventStore


def _signed(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_receiver_authenticates_before_routing_and_deduplicates(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    body = b"RP/0/RP0/CPU0: Aug 24 12:00:00.000 UTC: bgp[1]: %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12 Down"
    secret = "receiver-secret"

    first = receive_syslog(body=body, signature=_signed(body, secret), secret=secret, device="RR1", store=store)
    duplicate = receive_syslog(body=body, signature=_signed(body, secret), secret=secret, device="RR1", store=store)

    assert first.created is True
    assert duplicate.created is False
    assert first.envelope.event_id == duplicate.envelope.event_id


def test_receiver_refuses_bad_signature_and_oversized_body(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    with pytest.raises(EventReceiverError, match="signature"):
        receive_syslog(body=b"line", signature="bad", secret="secret", device="RR1", store=store)
    with pytest.raises(EventReceiverError, match="size"):
        receive_syslog(body=b"x" * 10, signature=_signed(b"x" * 10, "secret"), secret="secret", device="RR1", store=store, maximum_bytes=2)


def test_worker_claims_only_due_retryable_events(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="retry", device="RR1", payload={})
    store.transition("retry", "admitted")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store.schedule_retry("retry", reason="timeout", base_seconds=10, now=started)
    seen: list[str] = []

    early = EventWorker(store, owner="worker").run_eligible(lambda record: seen.append(record.event_id), now=started + timedelta(seconds=9))
    due = EventWorker(store, owner="worker").run_eligible(lambda record: seen.append(record.event_id), now=started + timedelta(seconds=10))

    assert early == ()
    assert due == ("retry",)
    assert seen == ["retry"]


def test_worker_recovers_an_expired_running_lease(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="stuck", device="RR1", payload={})
    store.transition("stuck", "admitted")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    first = store.acquire_lease("stuck", owner="crashed", lease_seconds=10, now=started)
    seen = []

    claimed = EventWorker(store, owner="recovery").run_eligible(
        seen.append,
        now=started + timedelta(seconds=11),
    )

    assert claimed == ("stuck",)
    assert seen[0].lease_owner == "recovery"
    assert seen[0].lease_epoch == first.lease_epoch + 1


def test_worker_records_runner_exception_and_continues_batch(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    for event_id in ("bad", "good"):
        store.create_or_get(event_id=event_id, device="RR1", payload={})
        store.transition(event_id, "admitted")
        store.schedule_retry(event_id, reason="initial", base_seconds=1, now=started)
    seen = []

    def runner(record):
        seen.append(record.event_id)
        if record.event_id == "bad":
            raise RuntimeError("poisoned event")

    claimed = EventWorker(store, owner="worker").run_eligible(
        runner,
        now=started + timedelta(seconds=1),
    )

    assert claimed == ("bad", "good")
    assert seen == ["bad", "good"]
    assert store.get("bad").state == "retryable_failed"


def test_worker_reconstructs_a_schema_validated_routing_decision(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    body = b"RP/0/RP0/CPU0: Aug 24 12:00:00.000 UTC: bgp[1]: %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12 Down"
    secret = "receiver-secret"
    received = receive_syslog(body=body, signature=_signed(body, secret), secret=secret, device="RR1", store=store)
    store.transition(received.envelope.event_id, "admitted")
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    store.schedule_retry(received.envelope.event_id, reason="timeout", base_seconds=1, now=started)
    decisions = []

    EventWorker(store, owner="worker").run_eligible_decisions(decisions.append, now=started + timedelta(seconds=1))

    assert len(decisions) == 1
    assert decisions[0].event_id == received.envelope.event_id


def test_lease_heartbeat_renews_the_exact_fenced_lease(monkeypatch, tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted", now=now)
    lease = store.acquire_lease("event-1", owner="worker", lease_seconds=180, now=now)
    calls = []
    monkeypatch.setattr(store, "renew_lease", lambda *args, **kwargs: calls.append((args, kwargs)) or lease)
    heartbeat = LeaseHeartbeat(store, lease, lease_seconds=180, interval_seconds=0.01)

    heartbeat.start()
    heartbeat._stop.wait(0.03)
    heartbeat.stop()

    assert calls
    assert calls[0][1]["lease_epoch"] == lease.lease_epoch
    assert heartbeat.failed is False
