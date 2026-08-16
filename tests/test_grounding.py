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

import pytest
from helpers import set_device_environment

from agent_nettools import flows, grounding
from agent_nettools.checks import BROKEN, HEALTHY, UNEVALUATED, CheckResult
from agent_nettools.descent import DescentResult, RungOutcome, run_descent
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
from agent_nettools.network_tools import run_template

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
