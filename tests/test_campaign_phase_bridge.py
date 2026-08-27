"""The campaign bridge validates typed input before interacting with delivery."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "scripts" / "campaign_phase_bridge.py"
_SPEC = importlib.util.spec_from_file_location("campaign_phase_bridge", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_bridge_queues_only_valid_typed_campaign_phase(monkeypatch):
    calls = []
    monkeypatch.setattr(_MODULE, "EventStore", lambda: object())
    monkeypatch.setattr(_MODULE, "open_campaign_ticket", lambda event: type("Ticket", (), {"run_id": "ticket-1", "close": lambda self: None})())
    monkeypatch.setattr(_MODULE, "queue_campaign_phase", lambda store, event: calls.append((store, event)) or ())

    result = _MODULE.deliver_phase({
        "campaign_id": "campaign-a", "round_ordinal": 1, "round_total": 24,
        "phase": "armed", "target": "PE2", "role": "edge", "fault_id": "interface_shutdown", "outcome": "pending",
    })

    assert result == {"campaign_id": "campaign-a", "round_ordinal": 1, "phase": "armed", "notification_records": 0, "ticket_id": "ticket-1"}
    assert len(calls) == 1


def test_bridge_refuses_freeform_phase_before_delivery(monkeypatch):
    monkeypatch.setattr(_MODULE, "EventStore", lambda: pytest.fail("store must not be opened"))

    with pytest.raises(ValueError, match="unsupported"):
        _MODULE.deliver_phase({
            "campaign_id": "campaign-a", "round_ordinal": 1, "round_total": 24,
            "phase": "raw_output", "target": "PE2", "role": "edge", "fault_id": "interface_shutdown", "outcome": "pending",
        })