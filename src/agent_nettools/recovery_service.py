"""Bounded autonomous recovery sweep for durable events and Telegram outbox."""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .event_agent import AgentBounds, run_event
from .event_caller import minimax_event_caller
from .event_notification import configured_telegram_sender
from .event_receiver import EventWorker
from .event_store import EventRecord, EventStore
from .telegram_delivery import drain_notifications

LOG = logging.getLogger(__name__)
NETTOOLS_RECOVERY_SWEEP_SECONDS_ENV = "NETTOOLS_RECOVERY_SWEEP_SECONDS"
NETTOOLS_RECOVERY_LEASE_SECONDS_ENV = "NETTOOLS_RECOVERY_LEASE_SECONDS"
DEFAULT_SWEEP_SECONDS = 15.0
DEFAULT_LEASE_SECONDS = 180.0


@dataclass(frozen=True)
class SweepResult:
    claimed_events: tuple[str, ...]
    notification_outcomes: int


class RecoveryService:
    """Run one bounded recovery sweep; runner and notifier are injectable for tests."""

    def __init__(
        self,
        store: EventStore,
        *,
        owner: str | None = None,
        event_runner: Callable[[EventRecord], None] | None = None,
        notification_drain: Callable[[EventStore], tuple] | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if lease_seconds < AgentBounds().time_budget_s + 60:
            raise ValueError("recovery lease must exceed the event budget by at least 60 seconds")
        self._store = store
        self._owner = owner or f"recovery:{socket.gethostname()}:{os.getpid()}"
        self._event_runner = event_runner or self._run_event
        self._notification_drain = notification_drain or self._drain_notifications
        self._lease_seconds = lease_seconds

    def sweep_once(self, *, now: datetime | None = None) -> SweepResult:
        """Recover due events first, then bounded notification work; isolate both failures."""

        started = now or utc_now()
        worker = EventWorker(self._store, owner=self._owner, lease_seconds=self._lease_seconds)
        try:
            claimed = worker.run_eligible(self._event_runner, now=now)
        except Exception:  # noqa: BLE001 -- one sweep failure must not kill service ownership
            LOG.exception("event recovery sweep failed")
            claimed = ()
        try:
            notifications = self._notification_drain(self._store)
        except Exception:  # noqa: BLE001 -- notification recovery is independent from event recovery
            LOG.exception("notification recovery sweep failed")
            notifications = ()
        result = SweepResult(tuple(claimed), len(notifications))
        self._store.record_recovery_sweep(
            worker_id=self._owner,
            started_at=started,
            completed_at=utc_now() if now is None else started,
            events_claimed=len(result.claimed_events),
            notifications_attempted=result.notification_outcomes,
        )
        return result

    def _run_event(self, lease: EventRecord) -> None:
        from .event_envelope import EventEnvelopeV2

        decision = EventEnvelopeV2.from_payload(lease.payload).routing_decision()
        run_event(
            decision,
            caller=minimax_event_caller,
            event_store=self._store,
            event_lease=lease,
        )

    def _drain_notifications(self, store: EventStore) -> tuple:
        sender = configured_telegram_sender()
        if sender is None:
            return ()
        return drain_notifications(store, notifier=sender, owner=self._owner)


def configured_sweep_seconds() -> float:
    raw = os.getenv(NETTOOLS_RECOVERY_SWEEP_SECONDS_ENV, str(DEFAULT_SWEEP_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SWEEP_SECONDS
    return value if value >= 1 else DEFAULT_SWEEP_SECONDS


def configured_lease_seconds() -> float:
    raw = os.getenv(NETTOOLS_RECOVERY_LEASE_SECONDS_ENV, str(DEFAULT_LEASE_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LEASE_SECONDS
    return value if value >= AgentBounds().time_budget_s + 60 else DEFAULT_LEASE_SECONDS


def utc_now() -> datetime:
    return datetime.now(timezone.utc)