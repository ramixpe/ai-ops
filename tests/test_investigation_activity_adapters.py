from __future__ import annotations

from agent_nettools.event_reporting import InvestigationReceipt
from agent_nettools.event_routing import RoutingDecision
from agent_nettools.investigation_activity import ActivityStatus
from agent_nettools.investigation_activity_adapters import activities_for_receipt, started_activity


def _receipt(*, finding: str, trustworthy: bool, cause: dict | None) -> InvestigationReceipt:
    return InvestigationReceipt.parse(
        {
            "tool": "investigate",
            "device": "PE2",
            "subject": "10.255.0.31",
            "flow": "bgp_session",
            "finding": finding,
            "reason": "typed reason",
            "trustworthy": trustworthy,
            "cause": cause,
            "causal_chain": [cause] if cause else [],
            "rungs_examined": 1,
            "rungs": [{
                "position": 1,
                "of": 1,
                "rung": "bgp_session",
                "device": "PE2",
                "status": "broken" if cause else "healthy",
                "reason": "typed rung reason",
                "evidence_keys": ["PE2:bgp:10.255.0.31"],
            }],
            "coherence": None,
            "coverage": None,
            "sessions": None,
            "operator_notes": [],
            "off_path": [],
            "report": {"status": "emitted"},
            "correlation": {"status": "not_attempted"},
        }
    )


def test_started_activity_uses_routing_identity_without_raw_trigger():
    decision = RoutingDecision(routable=True, flow="bgp_session", device="PE2", subject="10.255.0.31")

    activity = started_activity(decision, incident_id="INC-20260824-00001")

    assert activity.event_id == decision.event_id
    assert activity.stage == "bgp_session"
    assert not hasattr(activity, "raw_event")


def test_receipt_adapter_projects_rungs_and_localized_terminal_classification():
    cause = {"rung": "bgp_session", "device": "PE2", "reason": "typed cause"}
    receipt = _receipt(finding="peer_not_established", trustworthy=True, cause=cause)

    activities = activities_for_receipt(
        receipt,
        incident_id="INC-20260824-00001",
        event_id="event-1",
        sequence_start=2,
    )

    assert activities[0].status is ActivityStatus.ANOMALY
    assert activities[-1].classification == "ACTIVE_FAULT_LOCALIZED"
    assert activities[-1].sequence == 4


def test_receipt_adapter_marks_untrustworthy_result_inconclusive():
    receipt = _receipt(finding="undetermined", trustworthy=False, cause=None)

    terminal = activities_for_receipt(
        receipt,
        incident_id="INC-20260824-00001",
        event_id="event-1",
        sequence_start=0,
    )[-1]

    assert terminal.classification == "INCONCLUSIVE"
    assert terminal.limitation == "deterministic evidence is not trustworthy"