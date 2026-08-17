"""The authoritative report, rendered by code (B-439).

The property this file exists to pin is unusual, and it is worth naming: the
tests here **cannot** be tests that the report is faithful to the descent,
because a projection of the descent cannot be unfaithful to it. That is the
whole point of the item, and it means the honest thing to test is different —
that the report *is* a projection (no field invents anything), that nothing
downstream can prefer a model's paraphrase to it, and that the now-vacuous
grounding of it is **labelled as vacuous** rather than reported as a pass.

That last one is the trap. A gate that checks code against itself and reports
success is silent-failure shape 5, appearing in the one place in this build
whose job is to prevent shape 5.
"""

from __future__ import annotations

import pytest

from agent_nettools import flows, investigation, render
from agent_nettools.descent import DescentResult, RungOutcome, run_descent
from agent_nettools.fixtures import fixture_sender

SUBJECT = "10.255.0.12"


def _resolver(_subject):
    return "PE2"


#: Router ID -> owning device, from the inventory. Used instead of a fixed
#: resolver so a test can run an investigation in either direction.
_OWNER = {"10.255.0.12": "PE2", "10.255.0.31": "RR1"}


def _descent(label):
    return _descent_between("RR1", SUBJECT, label=label)


def _descent_between(device, subject, label="broken"):
    from agent_nettools.epoch import collect_epoch

    flow = flows.flow_for("bgp_session")
    resolve = _OWNER.__getitem__
    built = collect_epoch(flow, device, subject, resolver=resolve,
                          sender=fixture_sender(label=label))
    return run_descent(flow, device, subject,
                       collector=lambda d, _r, _s: built.for_device(d), resolver=resolve)


# --------------------------------------------------------------------------- #
# The report is a projection
# --------------------------------------------------------------------------- #


def test_every_observation_cites_a_key_its_own_check_actually_read():
    """Not a key chosen to look supporting.

    `CheckResult.evidence_keys` records what the predicate consulted, so a
    citation here is a fact about the code path rather than a claim about
    relevance. This is the difference between the rendered report and the model
    one: the model was asked to pick a key, and picking is where a plausible
    wrong citation comes from.
    """

    descent = _descent("broken")
    report = render.render_report(descent)

    declared = {k for o in descent.outcomes for k in o.result.evidence_keys}
    cited = {o["evidence_key"] for o in report["observations"] if "evidence_key" in o}

    assert cited, "the broken label must produce citations, or this is vacuous"
    assert cited <= declared, f"invented citations: {sorted(cited - declared)}"


def test_there_is_one_observation_per_rung_walked_and_no_more():
    descent = _descent("broken")
    report = render.render_report(descent)

    assert len(report["observations"]) == len(descent.outcomes) == 5
    for outcome, observation in zip(descent.outcomes, report["observations"], strict=True):
        assert outcome.rung in observation["claim"]
        assert outcome.status in observation["claim"]


def test_the_interpretation_names_the_cause_and_is_backed_by_the_chain():
    descent = _descent("broken")
    report = render.render_report(descent)

    assert len(report["interpretations"]) == 1
    interpretation = report["interpretations"][0]

    assert descent.cause.rung in interpretation["claim"]
    assert descent.cause.device in interpretation["claim"]
    # The chain plus the cause: five broken rungs on this label.
    assert len(interpretation["based_on"]) == len(descent.causal_chain) + 1


def test_a_finding_that_names_no_cause_asserts_no_cause():
    """The `_FINDINGS_WITHOUT_A_CAUSE` rule, enforced in the renderer too.

    A rendered report that named a cause for `no_fault_on_path` would reintroduce
    B-428's defect in the one place nobody would look for it, because the payload
    suppresses the `cause` field and the prose would not be suppressed with it.
    """

    outcomes = (
        RungOutcome("bgp_session", "RR1", _healthy()),
        RungOutcome("interface", "PE2", _broken()),
    )
    descent = DescentResult(
        flow="bgp_session", device="RR1", subject=SUBJECT,
        finding=flows.NO_FAULT_ON_PATH, outcomes=outcomes,
    )

    report = render.render_report(descent)
    claim = report["interpretations"][0]["claim"]

    assert "not on the dependency path" in claim
    assert "cause" not in claim.lower().replace("because", "")


def test_every_universal_finding_has_a_registered_next_check():
    """An unmapped finding would silently inherit generic advice.

    §0.12: a table that quietly covers a shrinking fraction of its domain is
    exactly the kind of thing that passes forever.
    """

    for finding in flows.UNIVERSAL_FINDINGS:
        assert finding in render._NEXT_CHECK, f"{finding} has no recommendation"

    for flow_name in flows.FLOWS:
        for rung in flows.flow_for(flow_name).descent:
            assert rung.finding in render._NEXT_CHECK, f"{rung.finding} has none"


def test_an_unmapped_finding_says_it_is_unmapped_rather_than_guessing():
    advice = render.next_check_for("something_nobody_registered")

    assert "No specific guidance is registered" in advice


def test_requires_human_is_always_true():
    """D3's ceiling. This tool localises and hands over; it does not remediate."""

    for label in ("broken", "healthy"):
        report = render.render_report(_descent(label))
        assert report["recommendation"]["requires_human"] is True


# --------------------------------------------------------------------------- #
# The vacuous gate, labelled
# --------------------------------------------------------------------------- #


def test_the_authoritative_report_is_not_graded_and_says_why():
    """The trap this item creates.

    `ground_report` over a projection of the descent would pass every time and
    the pass would mean nothing. Reporting that as a grounding verdict would be
    shape 5 in the one module built to prevent shape 5, so the payload carries a
    sentence instead of a verdict.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver
    )
    grounding = result.to_payload()["report"]["grounding"]

    assert isinstance(grounding, str)
    assert "check code against itself" in grounding
    assert "not graded" in grounding


def test_the_grounding_note_is_not_merely_true_but_load_bearing():
    """Prove the vacuity claim rather than asserting it.

    If `ground_report` really would pass unconditionally, then it passes on a
    rendered report for a descent whose model report *would* have been rejected.
    """

    from agent_nettools.grounding import ground_report

    descent = _descent("broken")
    verdict = ground_report(render.render_report(descent), descent)

    assert verdict.ok, "which is precisely why reporting it would be misleading"
    assert verdict.rungs_covered == len(descent.outcomes)


# --------------------------------------------------------------------------- #
# The paraphrase cannot be preferred
# --------------------------------------------------------------------------- #


class _Scripted:
    def __init__(self, text):
        self.text = text

    def __call__(self, _prompt):
        return self.text


def test_a_withheld_paraphrase_leaves_the_authoritative_report_intact():
    """The structural separation, end to end.

    Before B-439 this run produced no report at all. The finding is now
    unaffected by a model failing to restate it.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver,
        analyst=_Scripted("this is not JSON"),
    )

    assert result.paraphrase is None
    assert result.paraphrase_status == investigation.WITHHELD
    assert result.report is not None and result.report["authoritative"] is True
    assert result.trustworthy is True
    assert result.finding == "interface_line_down"


def test_a_paraphrase_is_marked_non_authoritative_even_when_it_grounds():
    report = render.render_report(_descent("broken"))
    # A paraphrase that would pass grounding: reuse the rendered content, which
    # by construction cites correctly.
    import json

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver,
        analyst=_Scripted(json.dumps(report)),
    )

    assert result.paraphrase_status == investigation.EMITTED
    assert result.paraphrase["authoritative"] is False, (
        "the flag is overwritten on the way out, so a model claiming authority cannot have it"
    )
    payload = result.to_payload()
    assert payload["report"]["authoritative"] is True
    assert payload["report"]["paraphrase"]["authoritative"] is False


# --------------------------------------------------------------------------- #
# The correlation
# --------------------------------------------------------------------------- #


def test_the_timeline_only_contains_events_mentioning_the_cause():
    """The deterministic half of the correlate contract.

    And the honest limit: this finds events *mentioning* the object, which is
    not the same as events *caused by* the fault. A model did not know the
    difference either -- it inferred from the same text -- but it wrote
    sentences that read as though it did.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver
    )
    correlation = result.correlation

    assert correlation["generated_by"] == "code"
    identifiers = correlation["matched_identifiers"]
    assert identifiers, "the cause must yield something to match on"

    for entry in correlation["timeline"]:
        haystack = f"{entry['event']} {entry['mnemonic']}"
        assert any(i in haystack for i in identifiers)


def test_an_empty_timeline_reports_found_false_rather_than_a_bare_absence():
    outcomes = (RungOutcome("bgp_session", "RR1", _broken()),)
    descent = DescentResult(
        flow="bgp_session", device="RR1", subject=SUBJECT,
        finding="peer_not_established", outcomes=outcomes,
    )
    from agent_nettools.log_window import ShapedWindow

    correlation = render.render_correlation(descent, ShapedWindow())

    assert correlation["correlation"]["found"] is False
    assert correlation["timeline"] == []
    assert correlation["correlation"]["recurrence"] == "none in the retained window"


def test_followed_a_commit_is_never_true_without_a_matched_event():
    """A commit line with nothing to attribute it to is not a correlation.

    Reporting `followed_a_commit: true` beside an empty timeline would be the
    tool implying a relationship between a config change and a fault it did not
    observe together.
    """

    from agent_nettools.log_window import ShapedWindow

    window = ShapedWindow(records=(
        {"timestamp": "Aug 14 08:00:00.000 UTC", "mnemonic": "CONFIG-6-DB_COMMIT",
         "text": "user clab committed"},
    ))
    outcomes = (RungOutcome("bgp_session", "RR1", _broken()),)
    descent = DescentResult(
        flow="bgp_session", device="RR1", subject=SUBJECT,
        finding="peer_not_established", outcomes=outcomes,
    )

    correlation = render.render_correlation(descent, window)

    assert correlation["correlation"]["found"] is False
    assert correlation["correlation"]["followed_a_commit"] is False


# --------------------------------------------------------------------------- #


def _healthy():
    from agent_nettools.checks import CheckResult

    return CheckResult("healthy", reason="fine", evidence_keys=("bgp",))


def _broken():
    from agent_nettools.checks import CheckResult

    return CheckResult("broken", reason="Gi0/0/0/1 is down", evidence_keys=("interfaces",))


# --------------------------------------------------------------------------- #
# OBS-115 -- making an omission detectable where nothing can be enforced
# --------------------------------------------------------------------------- #


def test_the_report_states_its_rung_count_and_numbers_every_observation():
    """**Not enforcement, and it must not be read as enforcement.**

    A chat client restated a correct five-rung report as four, dropping
    `route_to_peer`, and attributed two PE2 rungs to RR1 (OBS-115). That
    happened outside our `paraphrase` field and outside every gate this build
    has, on a surface we do not own and cannot instrument.

    Nothing here prevents that. What it does is make the omission *detectable*:
    a stated total and `1/5 … 5/5` means a four-item restatement is visibly
    short to a human reading both, without anyone having to re-derive the
    expected count.
    """

    descent = _descent("broken")
    report = render.render_report(descent)

    assert report["rungs_examined"] == len(descent.outcomes) == 5

    for position, observation in enumerate(report["observations"], start=1):
        assert observation["position"] == position
        assert observation["of"] == 5
        assert observation["claim"].startswith(f"{position}/5 ")


#: Both directions of the same session. The **same two rungs** resolve to
#: opposite devices depending on which end the investigation runs from, which is
#: why no test here may name a device literally.
#:
#: `(device, subject, subject_device, expected_finding)`
DIRECTIONS = [
    ("RR1", "10.255.0.12", "PE2", "interface_line_down"),
    ("PE2", "10.255.0.31", "RR1", "peer_unreachable_no_route"),
]


@pytest.mark.parametrize(
    ("device", "subject", "subject_device", "finding"),
    DIRECTIONS,
    ids=[f"{d}->{s}" for d, s, _, _ in DIRECTIONS],
)
def test_subject_scoped_rungs_resolve_to_the_subjects_device(
    device, subject, subject_device, finding
):
    """A subject-scoped rung is evaluated against **the subject's** device.

    **This test replaces one that hardcoded `PE2`, and the reason is the point.**
    `igp_adjacency` and `interface` carry `DeviceScope.SUBJECT` because the far
    end is where the fault lives (Q-013, OBS-055). Which device that *is*
    depends entirely on the direction of the investigation:

        RR1 -> 10.255.0.12   subject device PE2
        PE2 -> 10.255.0.31   subject device RR1

    A fixed name passes for one direction and is wrong for the other, so it
    would pin a specific investigation's mapping as though it were the rule --
    §0.13's tests face, arriving through a *specification* rather than an
    implementation. Parameterising over both directions makes the assertion
    about resolution rather than about a device, and fails if resolution is ever
    hardcoded.
    """

    descent = _descent_between(device, subject)
    scopes = {r.name: r.device_scope for r in flows.flow_for("bgp_session").descent}
    seen = {o.rung: o.device for o in descent.outcomes}

    assert descent.finding == finding, "the two directions are genuinely different cases"

    for rung, scope in scopes.items():
        expected = subject_device if scope is flows.DeviceScope.SUBJECT else device
        assert seen[rung] == expected, (
            f"{rung} ({scope.value}) resolved to {seen[rung]}, expected {expected}"
        )

    # And the rendered report says so on every line, which is the half that
    # decides which router someone walks to.
    report = render.render_report(descent)
    for outcome, observation in zip(descent.outcomes, report["observations"], strict=True):
        assert f"on {outcome.device}" in observation["claim"]


def test_the_two_directions_disagree_about_the_same_rungs():
    """The assertion above is only meaningful if the directions differ.

    Without this, a resolver bug that returned the local device for everything
    would satisfy both parameterisations of a weaker test and look correct.
    """

    by_direction = {
        device: {o.rung: o.device for o in _descent_between(device, subject).outcomes}
        for device, subject, _, _ in DIRECTIONS
    }

    assert by_direction["RR1"]["igp_adjacency"] == "PE2"
    assert by_direction["PE2"]["igp_adjacency"] == "RR1"
    assert by_direction["RR1"]["bgp_session"] == "RR1"
    assert by_direction["PE2"]["bgp_session"] == "PE2"

def test_the_payload_carries_the_same_count_and_numbering():
    """A consumer reading the payload rather than the report gets it too --
    the MCP surface reads the payload."""

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver
    )
    payload = result.to_payload()

    assert payload["rungs_examined"] == len(payload["rungs"]) == 5
    assert [r["position"] for r in payload["rungs"]] == [1, 2, 3, 4, 5]
    assert {r["of"] for r in payload["rungs"]} == {5}
    assert [r["device"] for r in payload["rungs"]][-2:] == ["PE2", "PE2"]
