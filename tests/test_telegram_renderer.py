from __future__ import annotations

import pytest

from agent_nettools.investigation_activity import (
    ActivityKind,
    ActivityStatus,
    InvestigationActivity,
)
from agent_nettools.investigation_view import InvestigationView, reduce_view
from agent_nettools.telegram_renderer import render_card


def _view(*, terminal: bool = False) -> InvestigationView:
    view = InvestigationView.new(incident_id="INC-20260824-00001", event_id="event-1")
    view = reduce_view(
        view,
        InvestigationActivity(
            activity_id="stage-1",
            incident_id=view.incident_id,
            event_id=view.event_id,
            sequence=1,
            kind=ActivityKind.RUNG_OBSERVED,
            status=ActivityStatus.ANOMALY,
            stage="protocol",
            rung="bgp_session",
            device="PE2",
            finding="transport_blocked",
        ),
    )
    if terminal:
        view = reduce_view(
            view,
            InvestigationActivity(
                activity_id="final-1",
                incident_id=view.incident_id,
                event_id=view.event_id,
                sequence=2,
                kind=ActivityKind.COMPLETED,
                status=ActivityStatus.ANOMALY,
                classification="ACTIVE_FAULT_LOCALIZED",
                finding="transport_blocked",
                limitation="impact is not modeled",
            ),
        )
    return view


def test_renderer_is_deterministic_and_exposes_only_view_fields():
    view = _view(terminal=True)

    first = render_card(view)
    second = render_card(view)

    assert first == second
    assert "INC-20260824-00001" in first.text
    assert "ACTIVE_FAULT_LOCALIZED" in first.text
    assert "transport_blocked" in first.text
    assert first.terminal is True


def test_renderer_elides_stage_detail_before_terminal_identity():
    view = _view(terminal=True)

    rendered = render_card(view, max_chars=180)

    assert len(rendered.text) <= 180
    assert "INC-20260824-00001" in rendered.text
    assert "Classification: ACTIVE_FAULT_LOCALIZED" in rendered.text


def test_renderer_rejects_an_unusable_limit():
    with pytest.raises(ValueError, match="header"):
        render_card(_view(), max_chars=159)