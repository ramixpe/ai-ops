"""Contained campaign phase records for Telegram and ticket adapters.

The fault injector owns device interaction. This module accepts only typed
campaign identifiers and fixed result codes, never raw device/config output.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import event_notification
from .event_store import EventStore, OutboxRecord
from .telegram_delivery import enqueue_card
from .telegram_renderer import TelegramRender
from .ticket import Ticket, open_ticket

_PHASES = frozenset({"started", "armed", "effect_observed", "diagnosed", "recovered", "failed", "stopped"})
_OUTCOMES = frozenset({"pending", "observed", "not_observed", "verified", "failed", "not_available"})


@dataclass(frozen=True)
class CampaignPhaseEvent:
    campaign_id: str
    round_ordinal: int
    round_total: int
    phase: str
    target: str
    role: str
    fault_id: str
    outcome: str
    rollback_deadline: str | None = None
    ticket_id: str | None = None
    incident_id: str | None = None
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.campaign_id, self.target, self.role, self.fault_id)):
            raise ValueError("campaign phase identity fields must be non-empty")
        if not 1 <= self.round_ordinal <= self.round_total:
            raise ValueError("campaign phase round ordinal is invalid")
        if self.phase not in _PHASES or self.outcome not in _OUTCOMES:
            raise ValueError("campaign phase uses an unsupported code")


def render_campaign_phase(event: CampaignPhaseEvent) -> TelegramRender:
    """Render a bounded phase update from fixed metadata only."""

    lines = [
        "NETWORK CHAOS CAMPAIGN",
        f"Campaign: {event.campaign_id}",
        f"Round: {event.round_ordinal}/{event.round_total}",
        f"Target: {event.target} ({event.role})",
        f"Fault: {event.fault_id}",
        f"Phase: {event.phase.upper()}",
        f"Outcome: {event.outcome.upper()}",
    ]
    if event.rollback_deadline is not None:
        lines.append(f"Rollback deadline: {event.rollback_deadline}")
    for label, value in (("Ticket", event.ticket_id), ("Incident", event.incident_id), ("Event", event.event_id)):
        if value is not None:
            lines.append(f"{label}: {value}")
    text = "\n".join(lines)
    return TelegramRender(text, hashlib.sha256(text.encode("utf-8")).hexdigest(), event.phase in {"recovered", "failed", "stopped"})


def queue_campaign_phase(store: EventStore, event: CampaignPhaseEvent) -> tuple[OutboxRecord, ...]:
    """Queue configured Telegram cards; notification availability is non-authoritative."""

    if not event_notification.notifications_enabled():
        return ()
    event_id = event.event_id or f"campaign:{event.campaign_id}:round:{event.round_ordinal}"
    store.create_or_update(
        event_id=event_id,
        device=event.target,
        payload={"kind": "campaign_phase", "campaign_id": event.campaign_id, "phase": event.phase},
    )
    render = render_campaign_phase(event)
    queued = tuple(
        record
        for chat_id in event_notification.live_card_chat_ids()
        if (record := enqueue_card(store, event_id=event_id, chat_id=chat_id, render=render)) is not None
    )
    return queued


def open_campaign_ticket(event: CampaignPhaseEvent) -> Ticket:
    """Open a per-round ticket and record the typed phase, never raw evidence."""

    ticket = open_ticket(
        event.target,
        entry_point="network_chaos_campaign",
        device=event.target,
        flow="interface",
        run_id=f"campaign:{event.campaign_id}:round:{event.round_ordinal}",
        incident_id=event.incident_id,
    )
    ticket.record_intent(
        flow="interface",
        resolved_subject=event.target,
        resolver="certified_campaign_contract",
        notes=f"campaign phase {event.phase}",
        extra={
            "campaign_id": event.campaign_id,
            "round_ordinal": event.round_ordinal,
            "round_total": event.round_total,
            "fault_id": event.fault_id,
            "role": event.role,
            "outcome": event.outcome,
            "rollback_deadline": event.rollback_deadline,
            "event_id": event.event_id,
        },
    )
    return ticket
