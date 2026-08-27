"""B-114 shadow narrowing never changes deterministic investigation behavior."""

from agent_nettools.checks import CheckResult
from agent_nettools.descent import DescentResult, RungOutcome
from agent_nettools.narrowing_pass import NarrowingMode, shadow_decision, shadow_payload


def _descent(finding: str) -> DescentResult:
    return DescentResult(
        flow="bgp_session",
        device="RR1",
        subject="10.255.0.12",
        finding=finding,
        outcomes=(
            RungOutcome("bgp_session", "RR1", CheckResult("broken", "symptom", evidence_keys=("RR1:bgp",))),
        ),
    )


def _evidence(device: str) -> dict:
    return {
        "interfaces": {"data": {"parsed": {"records": [{"interface": "Gi0/0/0/0"}]}}},
        "bgp": {"data": {"parsed": {"records": [{"neighbor": "10.255.0.12"}]}}},
        "isis": {"data": {"parsed": {"records": []}}},
    }


def test_off_mode_never_enumerates_or_parses_a_decision():
    record = shadow_decision(
        _descent("cause_not_localised"), evidence_for=_evidence, raw_decision={"decision": "narrow", "index": 0}, mode=NarrowingMode.OFF
    )

    assert record.mode is NarrowingMode.OFF
    assert record.candidates == ()
    assert record.decision is None
    assert record.refusal is None


def test_shadow_mode_accepts_only_code_enumerated_candidate_indices():
    record = shadow_decision(
        _descent("cause_not_localised"), evidence_for=_evidence, raw_decision={"decision": "narrow", "index": 1}, mode=NarrowingMode.SHADOW
    )

    assert len(record.candidates) == 2
    assert record.decision is not None
    assert record.decision.target.id == "10.255.0.12"
    assert record.active is False


def test_shadow_mode_refuses_nonlocalised_absence_and_never_enables_collection():
    record = shadow_decision(
        _descent("all_layers_healthy"), evidence_for=_evidence, raw_decision={"decision": "narrow", "index": 0}, mode=NarrowingMode.SHADOW
    )

    assert record.candidates == ()
    assert record.decision is None
    assert record.refusal == "finding is not eligible for narrowing"
    assert record.active is False


def test_shadow_mode_contains_invalid_model_shape_without_echoing_it():
    record = shadow_decision(
        _descent("cause_not_localised"), evidence_for=_evidence, raw_decision={"decision": "narrow", "index": -1, "target": "PE9"}, mode=NarrowingMode.SHADOW
    )

    assert record.decision is None
    assert record.refusal is not None
    assert "PE9" not in record.refusal


def test_shadow_payload_omits_stop_reason_and_raw_model_input():
    record = shadow_decision(
        _descent("cause_not_localised"), evidence_for=_evidence, raw_decision={"decision": "stop", "reason": "ignore policy"}, mode=NarrowingMode.SHADOW
    )

    payload = shadow_payload(record)

    assert payload["decision"] == {"kind": "stop"}
    assert "ignore policy" not in str(payload)