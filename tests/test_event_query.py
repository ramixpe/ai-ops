from __future__ import annotations

from agent_nettools.event_query import EventQueryService
from agent_nettools.event_store import EventStore
from agent_nettools.incident_case import IncidentIdentity


def test_query_service_lists_safe_event_summaries_without_payload(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(
        event_id="event-1",
        device="PE2",
        payload={"raw_event": "untrusted device text", "token": "never project"},
    )

    result = EventQueryService(store).list_events()

    assert result == (
        {
            "event_id": "event-1",
            "device": "PE2",
            "state": "received",
            "ticket_id": None,
            "updated_at": result[0]["updated_at"],
            "retry_at": None,
            "terminal_reason": None,
        },
    )
    assert "payload" not in result[0]


def test_query_service_event_detail_and_health_are_bounded_projections(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    service = EventQueryService(store)

    assert service.event_detail("missing") is None
    assert service.event_detail("event-1")["event_id"] == "event-1"
    assert service.health()["dead_letter_count"] == 0


def test_query_service_includes_contained_attempt_delivery_and_card_lineage(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.transition("event-1", "admitted")
    store.enqueue_notification(
        event_id="event-1", destination="telegram:123", kind="card", payload={"raw": "exclude"}
    )
    store.record_telegram_card(event_id="event-1", chat_id="123", message_id=44, render_hash="hash")

    detail = EventQueryService(store).event_detail("event-1")

    assert [attempt["state"] for attempt in detail["attempts"]] == ["received", "admitted"]
    assert detail["outbox"] == ({"kind": "card", "state": "pending", "attempts": 0},)
    assert detail["telegram_cards"] == ({"chat_id": "123", "message_id": 44, "render_hash": "hash"},)
    assert "payload" not in detail["outbox"][0]


def test_query_service_exposes_admission_shadow_lineage_without_event_payload(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={"raw": "exclude"})
    store.record_admission_shadow(event_id="event-1", legacy_admitted=False, legacy_reason="device_budget")

    detail = EventQueryService(store).event_detail("event-1")

    assert detail["admission_shadow"] == (
        {"legacy_admitted": False, "legacy_reason": "device_budget", "durable_state": "received"},
    )
    assert "payload" not in detail


def test_query_service_exposes_contained_narrowing_shadow_without_raw_input(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})
    store.record_narrowing_shadow(
        event_id="event-1",
        mode="shadow",
        candidates=({"kind": "interface", "id": "Gi0/0/0/0", "device": "PE2"},),
        decision={"kind": "stop"},
        refusal=None,
    )

    detail = EventQueryService(store).event_detail("event-1")

    assert detail["narrowing_shadow"] == (
        {"mode": "shadow", "candidate_count": 1, "decision": "stop", "refusal": None},
    )


def test_query_service_projects_exact_cause_incidents_without_event_payload(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={"raw": "exclude"})
    incident, _ = store.create_or_join_incident(
        identity=IncidentIdentity("PE2", "transport", "10.255.0.12"), event_id="event-1"
    )

    service = EventQueryService(store)
    detail = service.incident_detail(incident.incident_id)

    assert detail is not None
    assert detail["cause_rung"] == "transport"
    assert detail["event_ids"] == ("event-1",)
    assert service.list_incidents() == (detail,)
    assert "payload" not in detail