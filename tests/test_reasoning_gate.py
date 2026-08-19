"""The reasoning gate — B-101 (the typed decision object) and B-102 (code-
enumerated candidates). See `docs/design/reasoning-gate.md` for the approved
design and `reasoning_gate.py`'s module docstring for how "unrepresentable" is
achieved rather than filtered.

Every refusal test here has a positive control alongside it, through the
identical path (`decide`/`NarrowRequest`/`Stop` construction) — OBS-181: a
refusal that is never shown to let a legitimate case through proves nothing.
"""

from __future__ import annotations

import pytest

from agent_nettools.checks import BROKEN, HEALTHY, UNEVALUATED, CheckResult
from agent_nettools.descent import DescentResult, RungOutcome
from agent_nettools.reasoning_gate import (
    Candidate,
    GateRefusal,
    NarrowRequest,
    Stop,
    candidates_for_descent,
    candidates_from_evidence,
    parse_decision,
)


def _section(records):
    return {"data": {"parse_status": "ok", "parsed": {"records": records}}}


def _interfaces_evidence(*names: str) -> dict:
    return {"interfaces": _section([{"interface": n} for n in names])}


def _candidate(kind="interface", id_="Gi0/0/0/0", device="PE1") -> Candidate:
    return Candidate(kind, id_, device, f"{device}:{kind}:{id_}")


# --------------------------------------------------------------------------- #
# B-101 -- the two shapes, and why a third is unrepresentable
# --------------------------------------------------------------------------- #


class TestNarrowRequestTargetTypeLevel:
    """A fabricated target cannot be represented -- at the type level, not by
    filtering. `NarrowRequest.target` can only ever hold a `Candidate`."""

    def test_a_bare_string_standing_in_for_a_fabricated_target_is_refused(self):
        # Exactly what a model inventing "GigabitEthernet0/0/0/9" would try to
        # smuggle in if `target` accepted anything string-shaped.
        with pytest.raises(TypeError):
            NarrowRequest(target="GigabitEthernet0/0/0/9")  # type: ignore[arg-type]

    def test_a_dict_shaped_like_a_candidate_is_also_refused(self):
        # Same failure mode wearing the right *shape* but the wrong *type* --
        # a dict with the right keys is still not a `Candidate` this module
        # enumerated.
        with pytest.raises(TypeError):
            NarrowRequest(
                target={"kind": "interface", "id": "Gi0/0/0/9", "device": "PE2"}  # type: ignore[arg-type]
            )

    def test_positive_control_a_real_candidate_is_accepted(self):
        """OBS-181: the refusal above proves nothing unless the identical
        constructor accepts a legitimate `Candidate`."""

        candidate = _candidate()
        request = NarrowRequest(target=candidate)
        assert request.target is candidate


class TestVerdictShapedPayloadIsUnrepresentable:
    """Neither shape has a field a verdict, a cause, or a finding could
    occupy -- demonstrated by the dataclass refusing the extra keyword, not by
    a runtime check that could be forgotten."""

    def test_narrow_request_refuses_a_finding_keyword(self):
        with pytest.raises(TypeError):
            NarrowRequest(  # type: ignore[call-arg]
                target=_candidate(), finding="peer_not_established"
            )

    def test_narrow_request_refuses_a_cause_keyword(self):
        with pytest.raises(TypeError):
            NarrowRequest(target=_candidate(), cause="X")  # type: ignore[call-arg]

    def test_stop_refuses_a_finding_keyword(self):
        with pytest.raises(TypeError):
            Stop(reason="nothing more to ask", finding="peer_not_established")  # type: ignore[call-arg]

    def test_positive_control_each_shape_accepts_only_its_own_field(self):
        assert NarrowRequest(target=_candidate()).target == _candidate()
        assert Stop(reason="nothing more to ask").reason == "nothing more to ask"


class TestParseDecisionRefusesAVerdictShapedPayload:
    """The wire-level companion to the type-level tests above: a payload
    shaped like the pre-D7 sketch (`decision: sufficient`, a `finding`, an
    `object`) is not a third shape this function recognises."""

    def test_a_sufficient_decision_is_refused(self):
        candidates = (_candidate(),)
        payload = {
            "decision": "sufficient",
            "finding": "peer_not_established",
            "object": "10.255.0.12",
        }
        with pytest.raises(GateRefusal):
            parse_decision(payload, candidates)

    def test_an_unrecognised_decision_word_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "conclude", "cause": "X"}, (_candidate(),))

    def test_a_non_mapping_payload_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision("the cause is X", (_candidate(),))  # type: ignore[arg-type]

    def test_positive_control_narrow_and_stop_still_parse(self):
        candidates = (_candidate(),)
        assert parse_decision({"decision": "narrow", "index": 0}, candidates) == NarrowRequest(
            target=candidates[0]
        )
        assert parse_decision(
            {"decision": "stop", "reason": "every candidate checks out"}, candidates
        ) == Stop(reason="every candidate checks out")


# --------------------------------------------------------------------------- #
# parse_decision -- index-based selection, and everything that can go wrong
# with an index
# --------------------------------------------------------------------------- #


class TestParseDecisionIndexValidation:
    def test_an_out_of_range_index_is_refused(self):
        candidates = (_candidate(id_="Gi0/0/0/0"), _candidate(id_="Gi0/0/0/1"))
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow", "index": 2}, candidates)

    def test_a_negative_index_is_refused_not_silently_wrapped(self):
        """The Python footgun this guards against: `candidates[-1]` would
        silently resolve to the LAST candidate, a fabricated-looking selection
        wearing valid syntax."""

        candidates = (_candidate(id_="Gi0/0/0/0"), _candidate(id_="Gi0/0/0/1"))
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow", "index": -1}, candidates)

    def test_narrowing_with_zero_candidates_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow", "index": 0}, ())

    def test_a_non_integer_index_is_refused(self):
        candidates = (_candidate(),)
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow", "index": "0"}, candidates)

    def test_a_boolean_index_is_refused(self):
        """`bool` is an `int` subclass in Python; `True`/`False` must not
        quietly resolve to `candidates[1]`/`candidates[0]`."""

        candidates = (_candidate(id_="Gi0/0/0/0"), _candidate(id_="Gi0/0/0/1"))
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow", "index": True}, candidates)

    def test_a_missing_index_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "narrow"}, (_candidate(),))

    def test_positive_control_a_valid_index_selects_the_matching_candidate(self):
        candidates = (
            _candidate(id_="Gi0/0/0/0"),
            _candidate(id_="Gi0/0/0/1"),
            _candidate(id_="Gi0/0/0/2"),
        )
        decision = parse_decision({"decision": "narrow", "index": 2}, candidates)
        assert isinstance(decision, NarrowRequest)
        assert decision.target is candidates[2]


class TestParseDecisionStopValidation:
    def test_a_missing_reason_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "stop"}, (_candidate(),))

    def test_an_empty_reason_is_refused(self):
        with pytest.raises(GateRefusal):
            parse_decision({"decision": "stop", "reason": "   "}, (_candidate(),))

    def test_positive_control_a_real_reason_produces_stop(self):
        decision = parse_decision(
            {"decision": "stop", "reason": "every layer beneath the symptom is healthy"},
            (_candidate(),),
        )
        assert decision == Stop(reason="every layer beneath the symptom is healthy")


# --------------------------------------------------------------------------- #
# B-102 -- candidates enumerated by code, from observed evidence only
# --------------------------------------------------------------------------- #


class TestCandidatesFromEvidence:
    def test_three_interfaces_in_evidence_offer_exactly_three_candidates(self):
        evidence = _interfaces_evidence("Gi0/0/0/0", "Gi0/0/0/1", "Gi0/0/0/2")
        candidates = candidates_from_evidence("PE2", evidence)

        assert len(candidates) == 3
        assert [c.id for c in candidates] == ["Gi0/0/0/0", "Gi0/0/0/1", "Gi0/0/0/2"]
        assert {c.kind for c in candidates} == {"interface"}
        assert {c.device for c in candidates} == {"PE2"}

    def test_a_fourth_interface_never_named_in_evidence_is_not_offered(self):
        """The direct counterpart to B-459: an object the evidence never
        named is not a candidate, so it cannot be selected by any index."""

        evidence = _interfaces_evidence("Gi0/0/0/0", "Gi0/0/0/1", "Gi0/0/0/2")
        candidates = candidates_from_evidence("PE2", evidence)
        assert "Gi0/0/0/9" not in [c.id for c in candidates]

    def test_bgp_peers_are_enumerated_from_the_bgp_section(self):
        evidence = {"bgp": _section([{"neighbor": "10.255.0.12"}, {"neighbor": "10.255.0.31"}])}
        candidates = candidates_from_evidence("RR1", evidence)
        assert [c.id for c in candidates] == ["10.255.0.12", "10.255.0.31"]
        assert {c.kind for c in candidates} == {"bgp_peer"}

    def test_isis_adjacencies_are_enumerated_by_system_id(self):
        evidence = {
            "isis": _section(
                [
                    {"interface": "Gi0/0/0/0", "system_id": "P2", "state": "Up"},
                    {"interface": "Gi0/0/0/1", "system_id": "PE3", "state": "Init"},
                ]
            )
        }
        candidates = candidates_from_evidence("PE1", evidence)
        assert [c.id for c in candidates] == ["P2", "PE3"]
        assert {c.kind for c in candidates} == {"isis_adjacency"}

    def test_three_kinds_combine_when_all_three_sections_are_present(self):
        evidence = {
            "interfaces": _section([{"interface": "Gi0/0/0/0"}]),
            "bgp": _section([{"neighbor": "10.255.0.12"}]),
            "isis": _section([{"interface": "Gi0/0/0/0", "system_id": "P2", "state": "Up"}]),
        }
        candidates = candidates_from_evidence("PE2", evidence)
        assert len(candidates) == 3
        assert {c.kind for c in candidates} == {"interface", "bgp_peer", "isis_adjacency"}

    def test_a_duplicate_record_is_offered_once(self):
        evidence = _interfaces_evidence("Gi0/0/0/0", "Gi0/0/0/0")
        candidates = candidates_from_evidence("PE2", evidence)
        assert len(candidates) == 1

    @pytest.mark.parametrize(
        "evidence",
        [
            {},
            {"interfaces": "not a dict"},
            {"interfaces": {"data": "not a dict"}},
            {"interfaces": {"data": {"parsed": "not a dict"}}},
            {"interfaces": {"data": {"parsed": {"records": "not a list"}}}},
        ],
    )
    def test_malformed_or_absent_sections_offer_nothing_rather_than_crash(self, evidence):
        assert candidates_from_evidence("PE2", evidence) == ()

    def test_every_candidate_cites_an_evidence_key(self):
        """A `NarrowRequest` built from one of these must be as citable as any
        `CheckResult` -- grounding's own rule (T-020) applied to the gate."""

        evidence = _interfaces_evidence("Gi0/0/0/0")
        (candidate,) = candidates_from_evidence("PE2", evidence)
        assert candidate.evidence_key == "PE2:interfaces:Gi0/0/0/0"


# --------------------------------------------------------------------------- #
# B-102's other half -- no new devices
# --------------------------------------------------------------------------- #


def _outcome(device: str, status: str = HEALTHY) -> RungOutcome:
    keys = ("k",) if status in (HEALTHY, BROKEN) else ()
    return RungOutcome(
        rung="interface", device=device, result=CheckResult(status, evidence_keys=keys)
    )


class TestCandidatesForDescentNeverQueriesANewDevice:
    def test_only_devices_the_descent_touched_are_queried(self):
        descent = DescentResult(
            flow="bgp_session",
            device="RR1",
            subject="10.255.0.12",
            finding="igp_isolated",
            outcomes=(_outcome("RR1"), _outcome("PE2")),
        )

        # A trap: any device outside the descent's own set raises, so an
        # invented device call is a hard failure, not a value quietly used.
        touched = {"RR1", "PE2"}

        def evidence_for(device: str):
            if device not in touched:
                raise AssertionError(
                    f"candidates_for_descent asked for {device!r}, which this "
                    f"descent never touched"
                )
            return _interfaces_evidence(f"Gi-on-{device}")

        candidates = candidates_for_descent(descent, evidence_for)
        assert {c.device for c in candidates} == touched

    def test_a_comma_joined_path_scoped_device_field_is_split(self):
        """`RungOutcome.device` can be `", ".join(devices)` for a PATH-scoped
        rung (`run_descent`'s own construction). No flow uses PATH today, but
        the closed-set guarantee must hold if one does."""

        descent = DescentResult(
            flow="bgp_session",
            device="RR1",
            subject="10.255.0.12",
            finding="igp_isolated",
            outcomes=(
                RungOutcome(
                    rung="interface",
                    device="PE2, PE3",
                    result=CheckResult(HEALTHY, evidence_keys=("k",)),
                ),
            ),
        )
        touched = {"RR1", "PE2", "PE3"}

        def evidence_for(device: str):
            if device not in touched:
                raise AssertionError(f"unexpected device {device!r}")
            return _interfaces_evidence(f"Gi-on-{device}")

        candidates = candidates_for_descent(descent, evidence_for)
        assert {c.device for c in candidates} == touched

    def test_positive_control_a_healthy_and_an_unevaluated_outcome_both_contribute(self):
        """The refusal above (never query beyond the touched set) proves
        nothing unless devices actually IN that set are genuinely queried and
        their candidates genuinely returned."""

        descent = DescentResult(
            flow="interface",
            device="PE1",
            subject="Gi0/0/0/0",
            finding="all_layers_healthy",
            outcomes=(_outcome("PE1", HEALTHY), _outcome("PE1", UNEVALUATED)),
        )
        candidates = candidates_for_descent(descent, lambda d: _interfaces_evidence("Gi0/0/0/0"))
        assert len(candidates) == 1
        assert candidates[0].device == "PE1"

    def test_no_outcomes_still_queries_the_origin_device(self):
        descent = DescentResult(
            flow="interface", device="PE1", subject="Gi0/0/0/0",
            finding="subject_not_found",
        )
        candidates = candidates_for_descent(descent, lambda d: _interfaces_evidence("Gi0/0/0/0"))
        assert {c.device for c in candidates} == {"PE1"}
