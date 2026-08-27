from __future__ import annotations

from datetime import datetime, timezone

from agent_nettools.event_envelope import EVENT_ENVELOPE_V2_SCHEMA_VERSION, EventEnvelopeV2
from agent_nettools.event_routing import route_syslog_line


def test_v2_envelope_preserves_routing_identity_and_provenance():
    decision = route_syslog_line(
        "RP/0/RP0/CPU0: Aug 24 12:00:00.000 UTC: bgp[123]: "
        "%ROUTING-BGP-5-ADJCHANGE : Neighbor 10.255.0.31 Down",
        device="PE2",
    )
    received_at = datetime(2026, 8, 24, 12, 0, 1, tzinfo=timezone.utc)

    envelope = EventEnvelopeV2.from_routing_decision(decision, received_at=received_at)

    assert envelope.schema_version == EVENT_ENVELOPE_V2_SCHEMA_VERSION
    assert envelope.event_id == decision.event_id
    assert envelope.source_kind == decision.source_kind
    assert envelope.received_at == received_at.isoformat()
    assert envelope.raw_trigger == decision.raw_event
    assert envelope.payload()["routing_reason"] == decision.reason


def test_v2_envelope_refuses_a_naive_received_timestamp():
    decision = route_syslog_line("not a log line", device="PE2")

    try:
        EventEnvelopeV2.from_routing_decision(decision, received_at=datetime(2026, 8, 24))
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:  # pragma: no cover - the assertion above must raise
        raise AssertionError("naive received_at was accepted")