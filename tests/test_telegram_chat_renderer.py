from __future__ import annotations

import pytest

from agent_nettools.investigation_activity import (
    ActivityKind,
    ActivityStatus,
    InvestigationActivity,
)
from agent_nettools.investigation_activity_adapters import visible_reasoning_tail_activity
from agent_nettools.telegram_chat_renderer import render_activity_message


def _activity(kind: ActivityKind, **kwargs) -> InvestigationActivity:
    values = {
        "activity_id": "a-1",
        "incident_id": "INC-20260824-00001",
        "event_id": "event-1",
        "sequence": 1,
        "kind": kind,
        "status": ActivityStatus.RUNNING,
    }
    values.update(kwargs)
    return InvestigationActivity(**values)


def test_chat_renderer_shows_safe_command_preview_and_result_summary():
    command = render_activity_message(
        _activity(
            ActivityKind.COMMAND_PREVIEW,
            tool="investigate_lab",
            device="RR1",
            command_label="BGP session investigation for pinned peer",
        )
    )
    result = render_activity_message(
        _activity(
            ActivityKind.TOOL_RESULT_SUMMARY,
            tool="investigate_lab",
            status=ActivityStatus.ANOMALY,
            finding="transport_blocked",
            duration_ms=42,
            evidence_keys=("RR1:bgp:10.255.0.12",),
        )
    )

    assert "approved check" in command
    assert "BGP session investigation" in command
    assert "transport_blocked" in result
    assert "RR1:bgp:10.255.0.12" in result


def test_chat_renderer_labels_visible_reasoning_and_caps_to_two_sentences():
    activity = _activity(
        ActivityKind.VISIBLE_REASONING_TAIL,
        visible_summary=("BGP evidence is inconsistent with a healthy transport path.", "A deterministic check will resolve the lowest broken rung."),
    )

    rendered = render_activity_message(activity)

    assert "non-authoritative" in rendered
    assert "BGP evidence" in rendered

    with pytest.raises(ValueError, match="one or two"):
        _activity(ActivityKind.VISIBLE_REASONING_TAIL, visible_summary=("one", "two", "three"))


def test_visible_reasoning_tail_is_derived_from_closed_receipt_fields_only():
    activity = visible_reasoning_tail_activity(
        incident_id="INC-20260824-00001",
        event_id="event-1",
        sequence=3,
        finding="transport_blocked",
        trustworthy=True,
        coverage_complete=False,
    )

    assert activity.visible_summary == (
        "Deterministic investigation classified the event as transport_blocked.",
        "Evidence coverage is incomplete; this is not a final causal explanation.",
    )