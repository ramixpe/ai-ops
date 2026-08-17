"""The gate (T-029).

Grounding is the only thing standing between a model's prose and a human
acting on it, so these tests are written against the **real** `broken` descent
rather than a constructed one — five rungs, four of them the causal chain, all
five carrying evidence keys produced by checks over parsed fixtures.

The reports are hand-written, and deliberately so. Every one of them is a
report a model plausibly emits: the good one, the one that names the cause and
drops the chain, the one that cites a real-looking key nothing read, the one
that cites `obs-9` in a report with three observations. A model in the loop
would produce a *sample* of these; enumerating them is what makes the coverage
knowable.
"""

from __future__ import annotations

import pathlib

import pytest
from helpers import set_device_environment

from agent_nettools import flows, grounding
from agent_nettools.checks import BROKEN, HEALTHY, UNEVALUATED, CheckResult
from agent_nettools.descent import DescentResult, RungOutcome, run_descent
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
from agent_nettools.network_tools import run_template

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "cisco_xr"

_OWNER = {"10.255.0.12": "PE2", "10.255.0.31": "RR1"}


def _resolver(subject: str) -> str:
    return _OWNER[subject]


def _collect(device, rung, subject, label):
    evidence = dict(load_fixture_evidence(device, label=label))
    sender = fixture_sender(label=label)
    for step in rung.collect:
        if not step.is_template:
            continue
        if step.parameter == "prefix":
            key = f"{subject}/32"
            evidence[f"{step.name}:{key}"] = run_template(
                device, step.name, sender=sender, prefix=key
            )
        elif step.parameter == "interface":
            parsed = evidence.get("interfaces", {}).get("data", {}).get("parsed") or {}
            for record in parsed.get("records", []):
                name = record.get("interface", "")
                if name.startswith("Gi") and "." not in name:
                    evidence[f"{step.name}:{name}"] = run_template(
                        device, step.name, sender=sender, interface=name
                    )
        else:
            evidence[f"{step.name}:{subject}"] = run_template(
                device, step.name, sender=sender, **{step.parameter: subject}
            )
    return evidence


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    set_device_environment(monkeypatch)


@pytest.fixture(scope="function")
def broken():
    """The measured five-rung descent: RR1 -> 10.255.0.12 on the `broken` label."""

    return run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=lambda d, r, s: _collect(d, r, s, "broken"), resolver=_resolver,
    )


def _first_key(outcome) -> str:
    return outcome.result.evidence_keys[0]


def _full_report(descent) -> dict:
    """The report the prompt is meant to produce: one observation per rung, an
    interpretation naming the cause and citing the whole chain."""

    observations = [
        {"claim": f"{o.rung} on {o.device} is {o.status}", "evidence_key": _first_key(o)}
        for o in descent.outcomes
    ]
    labels = grounding.observation_labels(len(observations))
    return {
        "observations": observations,
        "interpretations": [
            {"claim": "The admin-down uplinks isolated PE2, which is why the session is Idle.",
             "based_on": list(labels)}
        ],
        "recommendation": {"next_check": "confirm the shutdown was intended",
                           "requires_human": True},
    }


# --------------------------------------------------------------------------- #
# The descent is what these tests rest on -- assert it before asserting on it
# --------------------------------------------------------------------------- #


def test_the_broken_descent_has_a_chain_worth_checking(broken):
    """Anti-vacuity. Every chain-coverage test below is trivially satisfiable if
    the descent turns out to have one rung or none, and it would pass green."""

    assert broken.finding == "interface_line_down"
    assert len(broken.outcomes) == 5
    assert len(broken.causal_chain) == 4
    assert broken.cause is not None and broken.cause.rung == "interface"
    assert all(o.result.evidence_keys for o in broken.outcomes)


# --------------------------------------------------------------------------- #
# check_grounding -- citation integrity. The three tests T-029 specifies.
# --------------------------------------------------------------------------- #


def test_a_valid_report_passes(broken):
    result = grounding.ground_report(_full_report(broken), broken)

    assert result.ok, result.summary()
    assert not result.vacuous
    assert result.observations_checked == 5
    assert result.rungs_covered == result.rungs_required == 5


def test_an_invented_evidence_key_fails(broken):
    report = _full_report(broken)
    report["observations"][2]["evidence_key"] = "PE2:interface:GigabitEthernet0/0/0/9"

    result = grounding.ground_report(report, broken)

    assert not result.ok
    assert {f.kind for f in result.failures} >= {"invented_evidence_key"}


def test_a_dangling_based_on_reference_fails(broken):
    report = _full_report(broken)
    report["interpretations"][0]["based_on"] = ["obs-1", "obs-9"]

    result = grounding.ground_report(report, broken)

    assert not result.ok
    assert any(f.kind == "dangling_reference" for f in result.failures)


def test_a_key_that_exists_but_was_never_read_by_this_descent_fails(broken):
    """The distinction `descent_evidence_keys` exists for.

    `PE2:interface:GigabitEthernet0/0/0/2` is a perfectly real key shape for a
    perfectly real interface. Nothing in this descent read it. A citation to it
    is an uncited claim wearing a citation, and a check against "keys that
    exist" rather than "keys that were read" would wave it through.
    """

    keys = grounding.descent_evidence_keys(broken)
    assert keys
    assert not any(k.endswith("GigabitEthernet0/0/0/2") for k in keys)


def test_an_observation_with_no_evidence_key_fails(broken):
    report = _full_report(broken)
    del report["observations"][0]["evidence_key"]

    result = grounding.ground_report(report, broken)

    assert any(f.kind == "uncited_observation" for f in result.failures)


def test_an_interpretation_citing_nothing_fails(broken):
    report = _full_report(broken)
    report["interpretations"][0]["based_on"] = []

    result = grounding.ground_report(report, broken)

    assert any(f.kind == "uncited_interpretation" for f in result.failures)


@pytest.mark.parametrize("flag", [False, None, "true", 1, "yes"])
def test_the_recommendation_must_actually_carry_requires_human_true(broken, flag):
    """`is True`, not truthiness.

    The recommendation is the only uncited claim the report is allowed, and the
    flag is the entire basis of that exemption. `"true"` and `1` are truthy and
    are not the flag; accepting them would exempt a claim on the strength of a
    field that was filled in wrong.
    """

    report = _full_report(broken)
    report["recommendation"]["requires_human"] = flag

    result = grounding.ground_report(report, broken)

    assert any(f.kind == "recommendation_not_flagged" for f in result.failures)


def test_a_report_with_no_recommendation_is_fine(broken):
    """The recommendation is optional. Only a present one must be flagged."""

    report = _full_report(broken)
    del report["recommendation"]

    assert grounding.ground_report(report, broken).ok


# --------------------------------------------------------------------------- #
# check_chain_coverage -- the requirement citation integrity cannot see
# --------------------------------------------------------------------------- #


def test_a_report_that_names_the_cause_and_drops_the_chain_is_not_emitted(broken):
    """The failure this half exists for, and the §0.12 companion for
    `ground_report`.

    This report is *flawless* by citation integrity: one observation, a real
    evidence key the descent genuinely read, an interpretation citing it, a
    flagged recommendation. `check_grounding` passes it. And it is exactly the
    output `prompts/README.md` rules out -- "BGP is down. Cause:
    interface_line_down on PE2" -- an assertion where the descent produced an
    argument.

    The two assertions together are the point: the component passes, the gate
    does not. A runner calling `check_grounding` alone has turned this off.
    """

    cause = broken.cause
    report = {
        "observations": [
            {"claim": "PE2's uplinks are administratively down",
             "evidence_key": _first_key(cause)}
        ],
        "interpretations": [{"claim": "The interface is the cause.", "based_on": ["obs-1"]}],
        "recommendation": {"next_check": "check with the operator", "requires_human": True},
    }

    assert grounding.check_grounding(report, grounding.descent_evidence_keys(broken)).ok

    gated = grounding.ground_report(report, broken)
    assert not gated.ok
    assert {f.kind for f in gated.failures} == {"uncited_rung"}
    assert len(gated.failures) == 4, "one per dropped rung"
    assert gated.rungs_covered == 1 and gated.rungs_required == 5


def test_a_chain_link_observed_but_never_argued_fails(broken):
    """Rule 2, and it is a genuinely different failure from rule 1.

    Every rung is observed and every key is real, so nothing is uncited. But the
    interpretation cites only the cause, so the four links are present as facts
    and absent from the argument. The reader gets a list and a verdict with no
    stated connection between them, which reads as an RCA and is not one.
    """

    report = _full_report(broken)
    report["interpretations"][0]["based_on"] = ["obs-5"]

    result = grounding.ground_report(report, broken)

    assert not result.ok
    assert {f.kind for f in result.failures} == {"chain_link_not_argued"}
    assert len(result.failures) == 4
    assert result.rungs_covered == 5, "all rungs observed -- only the argument is missing"


def test_the_chain_may_be_argued_across_several_interpretations(broken):
    """Deliberate looseness, stated so it does not read as an oversight.

    Splitting a five-link chain into two sentences is better prose and no weaker
    an argument. The union of `based_on` is what must cover the chain; a single
    interpretation is not required. Dropping a link is what rule 2 forbids.
    """

    report = _full_report(broken)
    report["interpretations"] = [
        {"claim": "PE2's uplinks are down, so it has no IS-IS adjacencies.",
         "based_on": ["obs-4", "obs-5"]},
        {"claim": "So RR1 has no route to the loopback, so transport fails, so BGP is Idle.",
         "based_on": ["obs-1", "obs-2", "obs-3"]},
    ]

    assert grounding.ground_report(report, broken).ok


def test_citing_some_other_rungs_key_is_not_coverage(broken):
    """An observation must cite a key belonging to the rung it covers.

    Five observations all citing the interface rung's key would satisfy a naive
    "every key is real, and there are five of them" check while covering one
    rung five times.
    """

    key = _first_key(broken.cause)
    report = {
        "observations": [
            {"claim": f"observation {i}", "evidence_key": key} for i in range(1, 6)
        ],
        "interpretations": [{"claim": "c", "based_on": ["obs-1"]}],
    }

    result = grounding.ground_report(report, broken)

    assert not result.ok
    assert result.rungs_covered == 1
    assert sum(f.kind == "uncited_rung" for f in result.failures) == 4


# --------------------------------------------------------------------------- #
# The refusal path, and the vacuous case §0.12 requires be visible
# --------------------------------------------------------------------------- #


def test_an_unevaluated_rung_with_nothing_to_cite_is_exempt():
    """The stated boundary of rule 1.

    A rung that could not be read has no evidence key by construction, so there
    is nothing for an observation to cite. Requiring one would make the refusal
    path unsatisfiable. That the report must *say* the rung was unread is the
    prompt's refusal case, pinned in `test_report_prompt.py`, not here -- and
    saying so is the reason this exemption is safe.
    """

    read = RungOutcome("bgp_session", "RR1", CheckResult(
        BROKEN, reason="Idle", subject="10.255.0.12",
        evidence_keys=("RR1:bgp:10.255.0.12",)))
    unread = RungOutcome("transport", "RR1", CheckResult(
        UNEVALUATED, reason="not captured at this label", subject="10.255.0.12"))
    descent = DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="undetermined", outcomes=(read, unread),
        evidence_keys=("RR1:bgp:10.255.0.12",))

    report = {
        "observations": [{"claim": "the session is Idle",
                          "evidence_key": "RR1:bgp:10.255.0.12"}],
        "interpretations": [{"claim": "The transport rung could not be read, so no "
                                      "cause can be determined.", "based_on": ["obs-1"]}],
        "recommendation": {"next_check": "capture the transport rung",
                           "requires_human": True},
    }

    result = grounding.ground_report(report, descent)

    assert result.ok, result.summary()
    assert result.rungs_required == 1, "the unread rung is not required, the read one is"


def test_a_pass_over_nothing_is_reported_as_vacuous():
    """§0.12, applied to this module's own result type.

    An all-`unevaluated` descent and an empty report satisfy every rule here by
    having nothing to check. That is a legitimate outcome, and `ok` alone would
    be indistinguishable from a real pass -- so the result says which it was.
    """

    descent = DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="undetermined",
        outcomes=(RungOutcome("bgp_session", "RR1",
                              CheckResult(UNEVALUATED, reason="no evidence")),))

    result = grounding.ground_report({"observations": [], "interpretations": []}, descent)

    assert result.ok
    assert result.vacuous
    assert "vacuous" in result.summary()


def test_the_real_pass_is_not_reported_as_vacuous(broken):
    """The other half of the pair -- otherwise `vacuous` could be hardcoded True."""

    result = grounding.ground_report(_full_report(broken), broken)
    assert result.ok and not result.vacuous
    assert "vacuous" not in result.summary()


def test_an_all_healthy_descent_requires_no_interpretation():
    """Rule 2 is vacuous when there is no chain, which is correct and must not
    become "rule 2 never fires". Its companion is
    `test_a_chain_link_observed_but_never_argued_fails`."""

    outcome = RungOutcome("bgp_session", "RR1", CheckResult(
        HEALTHY, reason="Established", subject="10.255.0.12",
        evidence_keys=("RR1:bgp:10.255.0.12",)))
    descent = DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="all_layers_healthy", outcomes=(outcome,),
        evidence_keys=("RR1:bgp:10.255.0.12",))

    report = {"observations": [{"claim": "Established",
                                "evidence_key": "RR1:bgp:10.255.0.12"}]}

    result = grounding.ground_report(report, descent)
    assert result.ok
    assert not result.vacuous, "one rung was genuinely checked"


# --------------------------------------------------------------------------- #
# Structural containment -- a rejected report's prose is not emitted
# --------------------------------------------------------------------------- #


def test_a_failure_never_carries_the_models_prose(broken):
    """OBS-061's rule, applied here.

    "The report is not emitted" is worth nothing if the rejection reason quotes
    it. `GroundingFailure` has no field a claim can occupy, so this cannot be
    got wrong by forgetting to redact -- but the assertion pins the property
    against someone adding one.
    """

    invented = "Gi0/0/0/0 is down because the operator was told to shut it"
    report = {
        "observations": [{"claim": invented, "evidence_key": "PE2:interface:Nonsense"}],
        "interpretations": [{"claim": invented, "based_on": ["obs-7"]}],
        "recommendation": {"next_check": invented, "requires_human": "no"},
    }

    result = grounding.ground_report(report, broken)

    assert not result.ok
    assert not hasattr(grounding.GroundingFailure, "claim")
    rendered = result.summary()
    assert invented not in rendered
    for word in ("operator", "because", "told"):
        assert word not in rendered.lower(), f"model prose leaked: {word}"


def test_an_overlong_or_multiline_evidence_key_is_flattened_and_capped(broken):
    """A key in a rejected report is model-authored text like any other. An
    unbounded one would let a rejection reformat the caller's output."""

    report = {
        "observations": [{"claim": "x", "evidence_key": "a\nb\n" + "z" * 500}],
        "interpretations": [],
    }

    rendered = grounding.ground_report(report, broken).summary()

    assert "\n" not in rendered
    assert "z" * 200 not in rendered
    assert "…" in rendered


# --------------------------------------------------------------------------- #
# Untrusted input -- a malformed report is a verdict, never an exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"observations": None, "interpretations": None},
        {"observations": "not a list"},
        {"observations": ["a string, not an object"]},
        {"observations": [{"claim": "", "evidence_key": "RR1:bgp:10.255.0.12"}]},
        {"observations": [], "interpretations": ["nope"]},
        {"observations": [], "interpretations": [{"claim": "c", "based_on": "obs-1"}]},
        {"observations": [], "recommendation": "not an object"},
        {"observations": [], "recommendation": {}},
    ],
    ids=lambda r: str(r)[:40],
)
def test_a_malformed_report_is_a_grounding_failure_not_a_crash(broken, report):
    """The report is model output arriving over a network. Every shape here is
    one a model has plausibly emitted, and none may raise -- a traceback in the
    emit path is an outage where a refusal was the correct answer."""

    result = grounding.ground_report(report, broken)
    assert isinstance(result, grounding.GroundingResult)
    assert not result.ok
    assert result.summary()


def test_a_report_that_is_not_a_dict_at_all_is_refused(broken):
    for shape in ([], "text", None, 7):
        result = grounding.ground_report(shape, broken)
        assert not result.ok
        assert any(f.kind == "malformed_report" for f in result.failures)


# --------------------------------------------------------------------------- #
# The label scheme is a contract with the prompt
# --------------------------------------------------------------------------- #


def test_the_observation_labels_match_what_the_prompt_tells_the_model_to_emit():
    """`report.v1.txt`: "Observations are numbered obs-1, obs-2, … in the order
    you emit them". If those two ever disagree, every citation dangles and the
    gate rejects every report a correctly-behaving model produces."""

    from agent_nettools.prompt_library import load_prompt

    assert grounding.observation_labels(3) == ("obs-1", "obs-2", "obs-3")
    assert grounding.observation_labels(0) == ()

    prompt = " ".join(load_prompt("report", 1).split())
    assert "obs-1" in prompt
    assert "in the order you emit them" in prompt


# --------------------------------------------------------------------------- #
# T-029a -- absence claims must be backed by coverage
# --------------------------------------------------------------------------- #


def _window(label: str):
    from agent_nettools import log_window, template_parsers

    raw = (FIXTURES / "PE2" / label / "show-logging-last-200.txt").read_text()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return log_window.shape_window(
        parsed["records"], coverage=log_window.coverage_from_logging(parsed, "PE2")
    )


_REFUSAL = {"correlation": {"found": False,
                            "summary": "no correlating events in the available coverage"}}


def test_the_gap_that_existed_before_this_check():
    """An absence claim with nothing behind it.

    This is what "no correlating events in window" was worth before T-029a: a
    statement about a source whose completeness nobody had established, on a
    fabric where the platform is measured to drop severity 5 and 6.
    """

    result = grounding.ground_correlation(_REFUSAL, None)

    assert not result.ok
    assert [f.kind for f in result.failures] == ["unbacked_absence_claim"]
    assert result.absence_claims_checked == 1


def test_the_real_refusal_case_cannot_assert_a_clean_negative():
    """Measured, and the answer is uncomfortable and correct.

    PE2's `healthy` buffer holds **555** messages; `show logging last 200`
    retrieved 200. Whatever is in the other 355 was not read, so "there were no
    correlating events" is a stronger claim than this evidence supports. The
    supportable one is "none in the available coverage" -- an `unevaluated`,
    not a `no`.

    The remedy is not to weaken the rule. It is to widen the window until the
    source is exhausted, which is exactly the incentive the rule should create.
    """

    shaped = _window("healthy")
    result = grounding.ground_correlation(_REFUSAL, shaped.coverage)

    assert not result.ok
    assert [f.kind for f in result.failures] == ["absence_claim_exceeds_coverage"]
    assert "200 of 555" in str(result.failures[0])


def test_an_exhausted_source_supports_a_negative():
    """The other side of the pair, and the §0.12 companion.

    Without this, `check_absence_coverage` could reject every absence claim
    unconditionally and every test above would still pass. When the source has
    handed over everything it holds, the negative is real.
    """

    from agent_nettools.coverage import Coverage

    exhausted = Coverage(
        device="PE2", source="device_buffer",
        severity_available=tuple(range(8)),
        records_available=180, records_returned=180,
    )
    assert exhausted.complete, exhausted.gaps()

    result = grounding.ground_correlation(_REFUSAL, exhausted)
    assert result.ok
    assert not result.vacuous
    assert result.absence_claims_checked == 1


def test_a_source_that_drops_severities_can_never_support_a_negative():
    """The measured Loki case, as a coverage record.

    Per B-206a the platform carries only severity 3 and 4. Every event in the
    causal sequence is 5 or 6. An absence claim over that source is unsupportable
    by construction, and it must be the coverage record that says so rather than
    anyone remembering.
    """

    from agent_nettools.coverage import Coverage

    loki = Coverage(device="PE2", source="loki", severity_available=(3, 4),
                    records_available=2, records_returned=2)

    assert not loki.complete
    gaps = " ".join(loki.gaps())
    for severity in ("0", "1", "2", "5", "6", "7"):
        assert severity in gaps

    result = grounding.ground_correlation(_REFUSAL, loki)
    assert not result.ok
    assert result.failures[0].kind == "absence_claim_exceeds_coverage"


def test_presence_is_not_weakened_by_a_coverage_gap():
    """Only absence needs coverage. A found correlation over a truncated window
    is still a found correlation -- the events are there, whatever else is
    missing. Rejecting it would be the mirror-image mistake.

    The timeline has to be real and the window has to be supplied, because since
    T-029c a `found: true` with nothing behind it is refused as unmeasured. That
    is the point: "presence survives a coverage gap" is a claim about *cited*
    presence, not about the word `true`.
    """

    shaped = _window("broken")
    assert not shaped.coverage.complete

    found = {
        "timeline": [{"at": r["timestamp"], "event": r["text"][:20],
                      "mnemonic": r["mnemonic"]} for r in shaped.records[:2]],
        "correlation": {"found": True, "summary": "the uplinks went down"},
    }
    result = grounding.ground_correlation(found, shaped.coverage, shaped)
    assert result.ok, result.summary()
    assert result.timeline_entries_checked == 2


@pytest.mark.parametrize(
    "claim",
    [{}, {"correlation": None}, {"correlation": "no"}, {"correlation": {}},
     {"correlation": {"found": "false"}}, {"correlation": {"found": 0}}],
    ids=lambda c: str(c)[:36],
)
def test_a_malformed_correlation_is_not_read_as_an_absence_claim(claim):
    """`is not False`, so a missing or string-valued `found` is never silently
    treated as "nothing to check". `"false"` and `0` are falsy and are not
    `False`; reading them as an absence claim would apply the rule to a report
    whose shape is already wrong.

    Two separate properties, and T-029c added the second. The absence rule does
    not fire -- `absence_claims_checked == 0`. And the *gate* still refuses any
    payload that carries a `correlation.found` nothing verified, which is the
    right outcome for a malformed one: it asserts a result and no check touched
    it.
    """

    result = grounding.ground_correlation(claim, None)

    assert result.absence_claims_checked == 0, "the absence rule must not fire"
    if grounding.claims_present(claim):
        assert not result.ok
        assert any(f.kind == "verified_nothing" for f in result.failures)
    else:
        assert result.ok, "a payload asserting nothing has nothing to refuse"


def test_coverage_is_read_from_what_the_device_reported_not_asserted():
    """Every field traceable to the `show logging` header the device emitted."""

    shaped = _window("broken")
    coverage = shaped.coverage

    assert coverage.source == "device_buffer"
    assert coverage.records_available == 593, "Buffer logging: ... 593 messages logged"
    assert coverage.records_returned == 200
    assert coverage.severity_available == tuple(range(8)), "buffer level is debugging"
    assert coverage.severity_missing == ()
    assert coverage.records_dropped_at_source == 0
    assert coverage.window_start and coverage.window_end
    assert coverage.records_kept_unattributable == 8


def test_the_buffer_level_is_read_not_the_trap_level():
    """A real trap on this fabric.

    `show logging` returns the *buffer*, at level debugging (0-7). The *trap*
    level governs what is shipped to the collector and is informational (0-6).
    Reading the trap level here would understate the local source by exactly
    the severity class B-206a is about -- and would do it while looking correct.
    """

    from agent_nettools import log_window, template_parsers

    raw = (FIXTURES / "PE2" / "broken" / "show-logging-last-200.txt").read_text()
    parsed, _ = template_parsers.parse_template_output("cisco_xr", "logging", raw)

    assert parsed["meta"]["buffer_level"] == "debugging"
    assert parsed["meta"]["trap_level"] == "informational"
    assert parsed["meta"]["buffer_level"] != parsed["meta"]["trap_level"], (
        "if these ever coincide this test stops discriminating"
    )
    assert log_window.coverage_from_logging(parsed, "PE2").severity_available == tuple(range(8))


def test_an_unknown_level_is_a_gap_not_an_assumption_of_completeness():
    """The one thing a missing level must never resolve to is "everything"."""

    from agent_nettools.coverage import severities_at_or_below

    assert severities_at_or_below(None) == ()
    assert severities_at_or_below("") == ()
    assert severities_at_or_below("nonsense") == ()
    assert severities_at_or_below("errors") == (0, 1, 2, 3)
    assert severities_at_or_below("debugging") == tuple(range(8))


def test_the_rendered_prompt_carries_the_coverage_and_its_gaps():
    """The model must be able to see what it is allowed to conclude."""

    from agent_nettools.prompt_library import build_correlate_prompt

    # B-421: build_correlate_prompt returns a RenderedPrompt (system/user
    # split, so the static half is cacheable); system + user is the same text
    # one fully-rendered prompt used to be.
    rendered = build_correlate_prompt(_finding_for_correlation(), _window("healthy"))
    prompt = f"{rendered.system}\n\n{rendered.user}"

    assert "{coverage_json}" not in prompt
    assert '"complete": false' in prompt
    assert "200 of 555" in prompt
    assert "in the available coverage" in prompt


def test_a_window_shaped_without_coverage_renders_as_a_gap():
    """Not silently as complete. A caller that never built a coverage record has
    established nothing, and the rendered prompt must say so."""

    from agent_nettools import log_window, template_parsers
    from agent_nettools.prompt_library import build_correlate_prompt

    raw = (FIXTURES / "PE2" / "healthy" / "show-logging-last-200.txt").read_text()
    parsed, _ = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    shaped = log_window.shape_window(parsed["records"])
    assert shaped.coverage is None

    rendered = build_correlate_prompt(_finding_for_correlation(), shaped)
    prompt = f"{rendered.system}\n\n{rendered.user}"
    assert '"complete": false' in prompt
    assert "no coverage record was produced" in prompt


def _finding_for_correlation() -> DescentResult:
    outcome = RungOutcome(
        "interface", "PE2",
        CheckResult(BROKEN, reason="both uplinks admin-down", subject="Gi0/0/0/0",
                    evidence_keys=("PE2:interface:Gi0/0/0/0",)))
    return DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="interface_line_down", outcomes=(outcome,),
        evidence_keys=("PE2:interface:Gi0/0/0/0",))


# --------------------------------------------------------------------------- #
# T-029b -- a timeline must cite records that exist
# --------------------------------------------------------------------------- #


def _found(timeline):
    return {"timeline": timeline,
            "correlation": {"found": True, "summary": "s",
                            "followed_a_commit": True, "recurrence": "once"}}


def test_the_exact_timestamp_the_model_fabricated_live_is_now_refused():
    """The T-033 regression, verbatim.

    MiniMax emitted `Aug 14 04:28.238 UTC` for a record whose real timestamp is
    `Aug 16 14:04:28.238 UTC` -- characters dropped, producing a malformed date
    two days earlier, in the one field `correlate.v3` constraint 2 says to quote
    exactly. One of nine timeline entries did not exist in the evidence and the
    correlation was emitted anyway, because nothing checked.
    """

    window = _window("broken")
    real = window.records[0]["timestamp"]
    assert real.startswith("Aug ")

    mangled = _found([{"at": "Aug 14 04:28.238 UTC", "event": "adjacency down",
                       "mnemonic": "ROUTING-ISIS-5-ADJCHANGE"}])
    result = grounding.check_timeline_citations(mangled, window)

    assert not result.ok
    assert [f.kind for f in result.failures] == ["invented_timestamp"]
    assert result.timeline_entries_checked == 1


def test_a_timeline_citing_real_records_passes():
    """The companion. Without it the check could refuse everything."""

    window = _window("broken")
    entries = [{"at": r["timestamp"], "event": r["text"][:30], "mnemonic": r["mnemonic"]}
               for r in window.records[:4]]

    result = grounding.check_timeline_citations(_found(entries), window)

    assert result.ok, result.summary()
    assert result.timeline_entries_checked == 4
    assert not result.vacuous


def test_a_real_instant_with_the_wrong_event_is_refused():
    """The second rule, and it is not redundant with the first.

    A verbatim-correct timestamp attached to an event that did not happen at it
    is the same fabrication wearing a valid citation -- and a timestamp check
    alone waves it through.
    """

    window = _window("broken")
    record = next(r for r in window.records if r["mnemonic"].startswith("ROUTING-ISIS"))
    wrong = _found([{"at": record["timestamp"], "event": "the peer reset",
                     "mnemonic": "ROUTING-BGP-5-ADJCHANGE"}])

    result = grounding.check_timeline_citations(wrong, window)

    assert not result.ok
    assert [f.kind for f in result.failures] == ["mnemonic_mismatch"]
    assert record["mnemonic"] in str(result.failures[0])


def test_a_timeline_with_no_window_is_refused_rather_than_passed():
    """`window` is optional only so an existing caller keeps working. Grading a
    timeline against nothing must not look like grading it successfully."""

    result = grounding.check_timeline_citations(
        _found([{"at": "Aug 16 07:41:54.688 UTC", "event": "x", "mnemonic": "y"}]), None)

    assert not result.ok
    assert result.failures[0].kind == "uncited_timeline"


def test_an_empty_timeline_is_not_a_citation_problem():
    """A `found: false` correlation has an empty timeline by contract. That is
    `check_absence_coverage`'s business, not this one's."""

    assert grounding.check_timeline_citations({"timeline": []}, _window("broken")).ok
    assert grounding.check_timeline_citations({}, _window("broken")).ok


def test_the_gate_now_runs_both_halves():
    """§0.12's companion for `ground_correlation`, and the gap B-424 recorded.

    Until T-029b this returned a *vacuous pass* for any correlation asserting
    presence -- `found: true` meant nothing was checked at all. The two
    assertions together are the point: absence checking alone passes a
    fabricated timeline, and the composed gate does not.
    """

    window = _window("broken")
    fabricated = _found([{"at": "Aug 14 04:28.238 UTC", "event": "x",
                          "mnemonic": "ROUTING-ISIS-5-ADJCHANGE"}])

    assert grounding.check_absence_coverage(fabricated, window.coverage).ok
    assert grounding.check_absence_coverage(fabricated, window.coverage).vacuous

    gated = grounding.ground_correlation(fabricated, window.coverage, window)
    assert not gated.ok
    assert [f.kind for f in gated.failures] == ["invented_timestamp"]


def test_presence_and_absence_are_now_both_covered_for_both_outputs():
    """The symmetry that was missing, asserted as a property.

    The report checked presence; the correlation checked absence (T-029a) and
    not presence. That asymmetry is why the live fabrication got through, and it
    was invisible because the report's presence check made the other half feel
    covered.
    """

    window = _window("healthy")

    absence = {"timeline": [], "correlation": {"found": False, "summary": "none"}}
    assert not grounding.ground_correlation(absence, window.coverage, window).ok

    presence = _found([{"at": "Aug 14 04:28.238 UTC", "event": "x", "mnemonic": "z"}])
    assert not grounding.ground_correlation(presence, window.coverage, window).ok


@pytest.mark.parametrize(
    "claim",
    [{"timeline": "not a list"}, {"timeline": ["a string"]},
     {"timeline": [{"at": None}]}, {"timeline": [{"at": 12345}]},
     "not a dict", []],
    ids=lambda c: str(c)[:30],
)
def test_a_malformed_timeline_is_a_verdict_not_a_crash(claim):
    result = grounding.check_timeline_citations(claim, _window("broken"))
    assert isinstance(result, grounding.GroundingResult)
    assert not result.ok


# --------------------------------------------------------------------------- #
# T-029c -- a vacuous verdict beside claims is a contradiction, not a note
# --------------------------------------------------------------------------- #


def test_the_exact_t033_shape_is_now_refused_by_the_gate_itself():
    """The gap B-429 recorded, closed.

    At T-033 the payload printed `vacuous pass -- 0 observations, 0 citations`
    beside a nine-entry timeline carrying a fabricated timestamp. The instrument
    was correct and nobody read it. The verdict now carries the contradiction as
    a failure rather than as a note somebody has to notice.
    """

    nine = _found([{"at": "Aug 16 07:41:54.688 UTC", "event": f"e{i}",
                    "mnemonic": "PKT_INFRA-LINK-5-CHANGED"} for i in range(9)])

    bare = grounding.check_absence_coverage(nine, None)
    assert bare.ok and bare.vacuous, "the component alone still measures nothing"

    gated = grounding.ground_correlation(nine, None, None)
    assert not gated.ok

    # `uncited_timeline`, not `verified_nothing` -- and the distinction is the
    # point rather than a technicality. The timeline check *ran*, found no
    # window to grade against, and refused. `verified_nothing` is the backstop
    # for the case where nothing examined the payload at all; here something
    # did, so the specific failure is the better one and fires first.
    assert [f.kind for f in gated.failures] == ["uncited_timeline"]
    assert gated.timeline_entries_checked == 9
    assert not gated.vacuous, "a gate that refused something did not measure nothing"


def test_a_positive_correlation_with_no_timeline_is_refused():
    """`found: true` with nothing behind it.

    Previously accepted, and it is the purest form of the defect: an assertion
    that a correlation exists, with no cited event, verified by nothing.
    """

    empty_positive = {"timeline": [], "correlation": {"found": True, "summary": "s"}}
    result = grounding.ground_correlation(empty_positive, _window("broken").coverage,
                                          _window("broken"))

    assert not result.ok
    assert [f.kind for f in result.failures] == ["verified_nothing"]


def test_a_genuinely_empty_payload_is_still_a_clean_vacuous_pass():
    """The §0.12 companion, and the line the rule must not cross.

    A payload that asserts nothing has nothing to contradict. Without this, the
    check could refuse every vacuous result and the test above would still pass
    -- and refusing an honest "nothing to grade" is how a rule this shape starts
    producing noise nobody reads, which is the failure it exists to prevent.
    """

    result = grounding.ground_correlation({"timeline": []}, None, None)

    assert result.ok
    assert result.vacuous
    assert "vacuous" in result.summary()


def test_a_report_asserting_nothing_still_passes_but_one_with_claims_does_not():
    """The same rule on the report gate, in both directions."""

    descent = DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="undetermined",
        outcomes=(RungOutcome("bgp_session", "RR1",
                              CheckResult(UNEVALUATED, reason="no evidence")),))

    assert grounding.ground_report({"observations": []}, descent).ok

    recommendation_only = {"observations": [], "interpretations": [],
                           "recommendation": {"next_check": "look at it",
                                              "requires_human": True}}
    result = grounding.ground_report(recommendation_only, descent)
    assert not result.ok
    assert any(f.kind == "verified_nothing" for f in result.failures)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ()),
        ({"observations": []}, ()),
        ({"timeline": []}, ()),
        ({"observations": [1, 2]}, ("observations[2]",)),
        ({"recommendation": {}}, ("recommendation",)),
        ({"correlation": {"found": False}}, ("correlation.found=False",)),
        ({"correlation": {}}, ()),
    ],
    ids=lambda x: str(x)[:34],
)
def test_claims_present_counts_assertions_not_fields(payload, expected):
    """Empty containers assert nothing. A report with `observations: []` is not
    making a claim, and treating it as one would refuse every honest refusal."""

    assert grounding.claims_present(payload) == expected


def test_informational_flags_are_not_turned_into_errors():
    """The narrowness of the rule, asserted.

    `unattributed_kept`, `repairs` and `retries` are *facts* with nothing
    inconsistent about them -- the noise filter declined to drop 8 records, a
    markdown fence was stripped, a command was retried. Turning every
    reader-facing number into an error is the opposite mistake and trains people
    to ignore these too, which is precisely how the T-033 warning went unread.
    """

    window = _window("broken")
    assert window.unattributed_kept == 8

    faithful = _found([{"at": r["timestamp"], "event": "e", "mnemonic": r["mnemonic"]}
                       for r in window.records[:2]])
    result = grounding.ground_correlation(faithful, window.coverage, window)

    assert result.ok, "a kept-unattributable count must not fail a grounded correlation"
    assert not window.coverage.complete, "nor must incomplete coverage, on a presence claim"


# --------------------------------------------------------------------------- #
# B-453 -- identifier containment
#
# Reviewer A's counterexample class, and the MCP experiment produced the live
# version of it (B-459): a fully grounded, correctly cited, deterministically
# derived investigation of a session that does not exist. Citation integrity
# cannot see it, because nothing in such a report is uncited.
# --------------------------------------------------------------------------- #


def _contained_descent() -> DescentResult:
    """A five-rung descent over identifiers this fabric really has."""

    return DescentResult(
        flow="bgp_session",
        device="RR1",
        subject="10.255.0.12",
        finding="interface_line_down",
        outcomes=(
            RungOutcome("bgp_session", "RR1", CheckResult(
                BROKEN, reason="session to 10.255.0.12 is Idle",
                subject="10.255.0.12", evidence_keys=("RR1:bgp:10.255.0.12",))),
            RungOutcome("interface", "PE2", CheckResult(
                BROKEN, reason="Gi0/0/0/0 is administratively down",
                subject="Gi0/0/0/0", evidence_keys=("PE2:interface:Gi0/0/0/0",))),
        ),
        evidence_keys=("RR1:bgp:10.255.0.12", "PE2:interface:Gi0/0/0/0"),
    )


def _report(*claims: str) -> dict:
    return {
        "observations": [
            {"claim": c, "evidence_key": "RR1:bgp:10.255.0.12"} for c in claims
        ],
        "interpretations": [],
    }


def test_a_report_naming_an_invented_device_is_refused():
    """Reviewer A's example. `PE7` is not in this fabric."""

    result = grounding.check_identifier_containment(
        _report("PE7's uplink is administratively down"), _contained_descent()
    )

    assert not result.ok
    assert [f.kind for f in result.failures] == ["uncontained_identifier"]
    assert "PE7" in result.failures[0].detail


def test_a_report_naming_an_invented_address_is_refused():
    result = grounding.check_identifier_containment(
        _report("the session to 10.255.0.99 is Idle"), _contained_descent()
    )

    assert not result.ok
    assert "10.255.0.99" in result.failures[0].detail


def test_a_report_naming_an_invented_interface_is_refused():
    result = grounding.check_identifier_containment(
        _report("Gi0/0/0/7 is down"), _contained_descent()
    )

    assert not result.ok
    assert "Gi0/0/0/7" in result.failures[0].detail


def test_the_companion_a_report_naming_only_real_identifiers_passes():
    """Without this the check could refuse everything and look like it works.

    `BUILD-PLAN.md` §0.12: a guardrail needs the case that must *not* fire.
    """

    result = grounding.check_identifier_containment(
        _report(
            "RR1's BGP session to 10.255.0.12 is Idle",
            "PE2's Gi0/0/0/0 is administratively down",
        ),
        _contained_descent(),
    )

    assert result.ok, result.summary()
    assert result.identifiers_checked >= 4
    assert not result.vacuous


def test_an_abbreviated_interface_name_is_the_same_identifier():
    """A report may write `GigabitEthernet0/0/0/0` for the descent's `Gi0/0/0/0`.

    Canonicalisation goes both ways -- neither spelling is privileged, and a
    report refused for spelling an interface out in full would be a false
    positive of exactly the kind this check is measured on.
    """

    assert grounding.canonical_identifier("Gi0/0/0/0") == \
        grounding.canonical_identifier("GigabitEthernet0/0/0/0")

    result = grounding.check_identifier_containment(
        _report("PE2's GigabitEthernet0/0/0/0 is administratively down"),
        _contained_descent(),
    )
    assert result.ok, result.summary()


def test_ordinary_english_is_never_mistaken_for_a_device_name():
    """The device-name family is derived from the fabric's own names.

    `PE7` matches `^(?:RR|PE)\\d+$` and `Established` does not. A fixed pattern
    could not tell those apart across fabrics, which is why the convention is
    read off the names that exist rather than guessed.
    """

    result = grounding.check_identifier_containment(
        _report(
            "The session is not Established and the adjacency count is 0. "
            "Nothing in the evidence indicates a hardware fault."
        ),
        _contained_descent(),
    )

    assert result.ok, result.summary()


def test_a_fabric_with_arbitrary_device_names_gets_no_device_checking():
    """Silence rather than a rule generalised from one instance (§0.13, rules).

    If no device name matches `letters+digits`, no family can be derived, and
    the check must decline to guess rather than invent a pattern.
    """

    descent = DescentResult(
        flow="bgp_session", device="core-router-alpha", subject="10.0.0.1",
        finding="peer_not_established",
        outcomes=(RungOutcome("bgp_session", "core-router-alpha", CheckResult(
            BROKEN, reason="Idle", subject="10.0.0.1",
            evidence_keys=("core-router-alpha:bgp:10.0.0.1",))),),
        evidence_keys=("core-router-alpha:bgp:10.0.0.1",),
    )

    result = grounding.check_identifier_containment(
        _report("edge-router-beta is unreachable"), descent
    )

    # No device family, so no device claim is refused -- but an invented
    # *address* still is, because that kind is unambiguous everywhere.
    assert result.ok, result.summary()
    assert not grounding.check_identifier_containment(
        _report("the peer 10.0.0.99 is Idle"), descent
    ).ok


def test_the_recommendation_is_checked_even_though_it_is_uncited():
    """Exempt from citation, not from existing.

    The recommendation is the model's advice rather than a reading, which is why
    it carries no evidence key. Advice about `PE7` is still advice about a
    device that is not there.
    """

    report = _report("RR1's session to 10.255.0.12 is Idle")
    report["recommendation"] = {"claim": "Check PE7's uplinks", "requires_human": True}

    result = grounding.check_identifier_containment(report, _contained_descent())

    assert not result.ok
    assert result.failures[0].locus == "recommendation"


def test_a_failure_never_carries_the_model_s_sentence():
    """Same rule as every other failure in this module (OBS-061).

    The failure names the identifier, which is what failed and what makes the
    failure actionable. It must not name the sentence, or a refused report's
    prose reaches a human through the rejection.
    """

    sentence = "PE7 is the root cause and the operator should reload it immediately"
    result = grounding.check_identifier_containment(_report(sentence), _contained_descent())

    assert not result.ok
    for failure in result.failures:
        assert "reload" not in str(failure)
        assert sentence not in str(failure)


def test_ground_report_runs_containment_so_the_emit_path_cannot_miss_it():
    """The wiring is the point.

    A check that exists and is not called by `ground_report` is not a gate.
    """

    descent = _contained_descent()
    report = {
        "observations": [
            {"claim": "RR1's BGP session to 10.255.0.12 is Idle",
             "evidence_key": "RR1:bgp:10.255.0.12"},
            {"claim": "PE7's Gi0/0/0/0 is administratively down",
             "evidence_key": "PE2:interface:Gi0/0/0/0"},
        ],
        "interpretations": [
            {"claim": "interface_line_down on PE2", "based_on": ["obs-1", "obs-2"]}
        ],
        "recommendation": {"claim": "Check the uplinks", "requires_human": True},
    }

    result = grounding.ground_report(report, descent)

    assert not result.ok
    assert "uncontained_identifier" in [f.kind for f in result.failures]
