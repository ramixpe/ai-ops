from __future__ import annotations

import pytest

from agent_nettools.event_reporting import InvestigationReceipt, ReceiptValidationError


def _payload(*, finding: str = "peer_not_established", cause: dict | None = None) -> dict:
    rungs = [
        {
            "position": 1,
            "of": 2,
            "rung": "bgp_session",
            "device": "RR1",
            "status": "broken",
            "reason": "BGP session is not Established",
            "evidence_keys": ["bgp:RR1"],
        },
        {
            "position": 2,
            "of": 2,
            "rung": "transport",
            "device": "RR1",
            "status": "healthy",
            "reason": "TCP session is established",
            "evidence_keys": ["bgp_neighbor:RR1"],
        },
    ]
    return {
        "tool": "investigate",
        "device": "RR1",
        "subject": "10.255.0.12",
        "flow": "bgp_session",
        "finding": finding,
        "reason": "BGP session is not Established",
        "trustworthy": True,
        "cause": cause or {"rung": "bgp_session", "device": "RR1", "reason": "BGP session is not Established"},
        "causal_chain": [{"rung": "bgp_session", "device": "RR1", "reason": "BGP session is not Established"}],
        "rungs_examined": 2,
        "rungs": rungs,
        "report": {"status": "emitted"},
        "correlation": {"status": "not_attempted"},
        "coherence": {"refuses": False},
        "coverage": None,
        "sessions": None,
        "operator_notes": [{"scope": "bgp", "note": "operator context"}],
        "off_path": [],
    }


def test_receipt_accepts_a_declared_finding_with_a_broken_cause_rung():
    receipt = InvestigationReceipt.parse(_payload())

    assert receipt.finding == "peer_not_established"
    assert receipt.cause["rung"] == "bgp_session"
    assert receipt.ticket_extra()["rungs"][0]["status"] == "broken"


def test_test_only_hold_timer_token_is_refused_as_an_rca():
    payload = _payload(finding="bgp_hold_timer_expired")

    with pytest.raises(ReceiptValidationError, match="undeclared finding"):
        InvestigationReceipt.parse(payload)


def test_no_cause_finding_refuses_a_cause_even_when_the_shape_is_valid():
    payload = _payload(finding="all_layers_healthy")

    with pytest.raises(ReceiptValidationError, match="must not carry a cause"):
        InvestigationReceipt.parse(payload)


def test_cause_must_reference_a_broken_rung():
    payload = _payload(cause={"rung": "transport", "device": "RR1", "reason": "wrong"})

    with pytest.raises(ReceiptValidationError, match="broken rung"):
        InvestigationReceipt.parse(payload)