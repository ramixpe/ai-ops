from datetime import datetime, timezone

import pytest

from agent_nettools.incident_case import IncidentCase, IncidentIdentity, IncidentState


def _identity() -> IncidentIdentity:
    return IncidentIdentity(device="PE2", cause_rung="transport", cause_subject="Gi0/0/0/0")


def test_incident_case_identity_is_exact_and_lifecycle_is_monotonic():
    case = IncidentCase.open(_identity(), event_id="event-1", now=datetime(2026, 8, 26, tzinfo=timezone.utc))

    assert case.state is IncidentState.OPEN
    assert case.can_join(_identity()) is True
    acknowledged = case.transition(IncidentState.ACKNOWLEDGED, now=datetime(2026, 8, 26, 1, tzinfo=timezone.utc))
    resolved = acknowledged.transition(IncidentState.RESOLVED, now=datetime(2026, 8, 26, 2, tzinfo=timezone.utc))

    assert resolved.state is IncidentState.RESOLVED
    assert resolved.event_ids == ("event-1",)


def test_incident_case_joins_only_matching_active_events_idempotently():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    case = IncidentCase.open(_identity(), event_id="event-1", now=now)
    joined = case.join_event(_identity(), event_id="event-2", now=now)

    assert joined.event_ids == ("event-1", "event-2")
    assert joined.join_event(_identity(), event_id="event-2", now=now) is joined
    with pytest.raises(ValueError, match="cannot join"):
        joined.join_event(IncidentIdentity("PE2", "transport", "Gi0/0/0/1"), event_id="event-3", now=now)


def test_incident_case_refuses_incomplete_identity_and_invalid_transitions():
    with pytest.raises(ValueError, match="identity"):
        IncidentIdentity(device="PE2", cause_rung="transport", cause_subject="")

    case = IncidentCase.open(_identity(), event_id="event-1", now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="cannot transition"):
        case.transition(IncidentState.ARCHIVED, now=datetime(2026, 8, 26, 1, tzinfo=timezone.utc))
    assert case.can_join(IncidentIdentity(device="PE2", cause_rung="transport", cause_subject="Gi0/0/0/1")) is False