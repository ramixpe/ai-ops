#!/usr/bin/env python3
"""Queue and best-effort deliver one typed network campaign phase from stdin."""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_nettools import event_notification
from agent_nettools.campaign_reporting import (
    CampaignPhaseEvent,
    open_campaign_ticket,
    queue_campaign_phase,
)
from agent_nettools.event_store import EventStore


def deliver_phase(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and deliver a metadata-only phase without exposing secrets or evidence."""

    event = CampaignPhaseEvent(**payload)
    ticket = None
    if event.ticket_id is None:
        ticket = open_campaign_ticket(event)
        event = CampaignPhaseEvent(**{**event.__dict__, "ticket_id": ticket.run_id})
    store = EventStore()
    queued = queue_campaign_phase(store, event)
    for record in queued:
        event_notification.deliver_live_card(store, record)
    if ticket is not None:
        ticket.close()
    return {
        "campaign_id": event.campaign_id,
        "round_ordinal": event.round_ordinal,
        "phase": event.phase,
        "notification_records": len(queued),
        "ticket_id": event.ticket_id,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("campaign phase input must be an object")
        print(json.dumps(deliver_phase(payload), sort_keys=True))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())