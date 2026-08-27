from __future__ import annotations

import pytest

from agent_nettools.investigation_activity import (
    ActivityKind,
    ActivityStatus,
    InvestigationActivity,
)
from agent_nettools.investigation_view import InvestigationView, reduce_view


def _activity(activity_id: str, sequence: int, **overrides) -> InvestigationActivity:
    values = {
        "activity_id": activity_id,
        "incident_id": "INC-20260824-00001",
        "event_id": "event-1",
        "sequence": sequence,
        "kind": ActivityKind.STAGE_STARTED,
        "status": ActivityStatus.RUNNING,
        "stage": "investigation",
    }
    values.update(overrides)
    return InvestigationActivity(**values)


def test_reducer_is_immutable_and_deduplicates_activity_ids():
    initial = InvestigationView.new(incident_id="INC-20260824-00001", event_id="event-1")
    activity = _activity("a-1", 1)

    updated = reduce_view(initial, activity)

    assert initial.status is ActivityStatus.QUEUED
    assert updated.status is ActivityStatus.RUNNING
    assert reduce_view(updated, activity) is updated


def test_reducer_ignores_stale_sequence_and_keeps_parallel_stage_state():
    view = InvestigationView.new(incident_id="INC-20260824-00001", event_id="event-1")
    view = reduce_view(view, _activity("a-1", 1, stage="context"))
    view = reduce_view(view, _activity("a-2", 2, stage="protocol"))

    stale = reduce_view(view, _activity("a-stale", 1, stage="context", status=ActivityStatus.HEALTHY))

    assert stale is view
    assert [stage.name for stage in view.stages] == ["context", "protocol"]
    assert view.running_checks == 2


def test_terminal_activity_is_monotonic():
    view = InvestigationView.new(incident_id="INC-20260824-00001", event_id="event-1")
    completed = reduce_view(
        view,
        _activity(
            "a-final",
            1,
            kind=ActivityKind.COMPLETED,
            status=ActivityStatus.ANOMALY,
            classification="ACTIVE_FAULT_LOCALIZED",
            finding="transport_blocked",
        ),
    )

    later = reduce_view(completed, _activity("a-later", 2, stage="late"))

    assert completed.terminal is True
    assert completed.status is ActivityStatus.COMPLETED
    assert later is completed


def test_reducer_refuses_activity_for_another_incident():
    view = InvestigationView.new(incident_id="INC-20260824-00001", event_id="event-1")

    with pytest.raises(ValueError, match="identity"):
        reduce_view(view, _activity("a-1", 1, incident_id="INC-20260824-00002"))