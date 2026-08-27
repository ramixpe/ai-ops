"""Framework-free authenticated event receiver and durable worker contracts."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .event_envelope import EventEnvelopeV2
from .event_routing import RoutingDecision, route_syslog_line
from .event_store import EventLeaseError, EventRecord, EventStore

__all__ = ["EventReceiverError", "EventWorker", "ReceivedEvent", "receive_syslog"]
LOG = logging.getLogger(__name__)


class EventReceiverError(ValueError):
    """An inbound event failed authentication or strict transport validation."""


@dataclass(frozen=True)
class ReceivedEvent:
    envelope: EventEnvelopeV2
    decision: RoutingDecision
    created: bool


def receive_syslog(
    *,
    body: bytes,
    signature: str,
    secret: str,
    device: str,
    store: EventStore,
    maximum_bytes: int = 16_384,
    received_at: datetime | None = None,
) -> ReceivedEvent:
    """Authenticate one syslog body before routing and durable creation.

    `device` is trusted receiver metadata, never derived from device-authored
    message text. The HMAC is over the exact body bytes so parsing cannot alter
    the authenticated material.
    """

    if not secret or not isinstance(signature, str):
        raise EventReceiverError("event receiver authentication is not configured")
    if not body or len(body) > maximum_bytes:
        raise EventReceiverError("event receiver body size is invalid")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise EventReceiverError("event receiver signature is invalid")
    try:
        line = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EventReceiverError("event receiver body is not UTF-8") from exc
    decision = route_syslog_line(line, device=device)
    envelope = EventEnvelopeV2.from_routing_decision(decision, received_at=received_at)
    _record, created = store.create_or_get(
        event_id=envelope.event_id,
        device=envelope.device or "unknown",
        payload=envelope.payload(),
    )
    return ReceivedEvent(envelope, decision, created)


class EventWorker:
    """Atomically claim due retries or expired leases and invoke a runner."""

    def __init__(self, store: EventStore, *, owner: str, lease_seconds: float = 90.0) -> None:
        self.store = store
        self.owner = owner
        self.lease_seconds = lease_seconds

    def run_eligible(self, runner: Callable[[EventRecord], None], *, now: datetime | None = None) -> tuple[str, ...]:
        """Claim recoverable work; runner owns the actual event-agent invocation."""

        claimed: list[str] = []
        fixed_clock = now is not None
        current = now or datetime.now(timezone.utc)
        for record in self.store.recoverable_events(now=current):
            try:
                leased = self.store.acquire_lease(
                    record.event_id,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                    now=current,
                )
            except EventLeaseError:
                continue
            claimed.append(record.event_id)
            try:
                runner(leased)
            except Exception as exc:  # noqa: BLE001 -- one poisoned event must not stop the worker batch
                LOG.exception("event runner failed: event_id=%s", record.event_id)
                try:
                    self.store.schedule_retry(
                        record.event_id,
                        reason=f"event runner failed: {type(exc).__name__}",
                        now=current if fixed_clock else datetime.now(timezone.utc),
                        lease_owner=leased.lease_owner,
                        lease_epoch=leased.lease_epoch,
                    )
                except EventLeaseError:
                    LOG.warning(
                        "event runner failure could not be scheduled because its lease was lost: event_id=%s",
                        record.event_id,
                    )
        return tuple(claimed)

    def run_eligible_decisions(
        self, runner: Callable[[RoutingDecision], None], *, now: datetime | None = None
    ) -> tuple[str, ...]:
        """Claim due work and reconstruct only schema-validated decisions."""

        def run(record: EventRecord) -> None:
            envelope = EventEnvelopeV2.from_payload(record.payload)
            runner(envelope.routing_decision())

        return self.run_eligible(run, now=now)
