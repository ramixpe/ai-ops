"""Golden cases for the report prompt (T-027).

**These tests assert shape and citation integrity only. They never assert
prose.** Prose varies at any temperature; structure must not. A test that pins
wording fails on a harmless rewording and passes on a fabricated citation,
which is exactly backwards — so if one of these starts failing because a model
phrased something differently, the test is wrong and gets fixed, not the
prompt.

Three golden scenarios, all built from committed fixtures:

* **localised cause** — the `broken` label. Five rungs, lowest broken at
  `interface` on PE2, four in the causal chain.
* **`cause_not_localised`** — composed: the `broken` label's BGP section over
  the `healthy` label's lower layers. Both halves are real captured output;
  only the combination is synthetic, and there is no way to capture it from a
  fabric that behaves consistently.
* **`undetermined`** — the `t0` label, which genuinely stops at `transport`
  because `t0` predates template capture. The refusal path, from real data.

The third is the one that matters most. A model that fills an unread rung with
a plausible cause produces something indistinguishable from a finding, and that
is the single failure the Constraints slot exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import set_device_environment

from agent_nettools import flows
from agent_nettools.descent import run_descent
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
from agent_nettools.network_tools import run_template
from agent_nettools.prompt_library import (
    PromptNotFoundError,
    build_report_prompt,
    descent_payload,
    load_prompt,
)


def _flat(text: str) -> str:
    """Collapse whitespace before matching.

    A prompt is wrapped for humans to read, so a phrase can span a line break.
    Asserting on the wrapped form would make these tests fail when someone
    rewraps a paragraph -- the same "pin the formatting, miss the substance"
    mistake the golden tests exist to avoid.
    """

    return " ".join(text.split())

CASES_FILE = Path(__file__).resolve().parent.parent / "prompts" / "tests" / "cases" / "report.cases.json"


def _case(name: str) -> dict:
    """Read one golden case. The file is load-bearing, not documentation --
    a case file nothing reads is exactly the decorative artifact these rules
    exist to prevent."""

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    return next(c for c in cases if c["name"] == name)


_OWNER = {
    "10.255.0.11": "PE1",
    "10.255.0.12": "PE2",
    "10.255.0.13": "PE3",
    "10.255.0.14": "PE4",
    "10.255.0.31": "RR1",
}


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


def _descent(label):
    return run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=lambda d, r, s: _collect(d, r, s, label), resolver=_resolver,
    )


def _mixed_descent():
    """`cause_not_localised`: session Idle, every layer beneath it healthy."""

    def collect(device, rung, subject):
        return _collect(device, rung, subject, "broken" if rung.name == "bgp_session" else "healthy")

    return run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=collect, resolver=_resolver,
    )


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    set_device_environment(monkeypatch)


# --------------------------------------------------------------------------- #
# The loader
# --------------------------------------------------------------------------- #


def test_the_report_prompt_loads_by_version():
    assert "GROUNDING" in load_prompt("report", 1)


def test_an_unknown_version_raises_rather_than_falling_back():
    """Falling back to another version would attribute a report to a prompt
    that did not produce it."""

    with pytest.raises(PromptNotFoundError):
        load_prompt("report", 99)


# --------------------------------------------------------------------------- #
# Invariant 4 -- no unparsed device text reaches a model
# --------------------------------------------------------------------------- #


def test_the_rendered_prompt_carries_no_raw_device_output():
    """§0.6 invariant 4, enforced structurally rather than by filtering.

    The renderer never receives device text -- it takes a DescentResult, which
    holds verdicts and evidence keys. This asserts that nothing recognisable as
    raw IOS-XR output reaches the prompt.
    """

    prompt = build_report_prompt(_descent("broken"))

    for marker in (
        "RP/0/RP0/CPU0",            # the IOS-XR log/prompt prefix
        "Routing entry for",        # show route
        "BGP neighbor is",          # show bgp neighbor
        "Interface state transitions",  # show interfaces
        "Type escape sequence",     # ping/traceroute
    ):
        assert marker not in prompt, f"raw device output leaked into the prompt: {marker!r}"


def test_the_payload_is_verdicts_and_keys_only():
    payload = descent_payload(_descent("broken"))
    allowed = {"flow", "device", "subject", "finding", "reason", "rungs", "cause",
               "causal_chain", "evidence_keys"}
    assert set(payload) == allowed
    for rung in payload["rungs"]:
        assert set(rung) == {"rung", "device", "status", "reason", "evidence_keys"}


# --------------------------------------------------------------------------- #
# Golden case 1 -- a localised cause, with its chain
# --------------------------------------------------------------------------- #


def test_the_prompt_carries_the_whole_causal_chain_not_just_the_finding():
    """The chain is the product.

    A prompt that passes the finding without the rungs above it asks the model
    to render an assertion. The chain is what makes the answer checkable.
    """

    result = _descent("broken")
    payload = descent_payload(result)

    expect = _case("localised_cause")["expect"]
    assert payload["finding"] == expect["finding"]
    assert payload["cause"]["rung"] == expect["cause_rung"]
    assert payload["cause"]["device"] == expect["cause_device"]
    assert [c["rung"] for c in payload["causal_chain"]] == expect["causal_chain"]
    # Every rung the descent walked is present for the model to render.
    assert len(payload["rungs"]) == expect["rung_count"]


def test_every_rung_in_the_prompt_carries_citable_evidence_keys():
    """Citation integrity at the input end: the model can only cite keys that
    are in front of it, so every conclusive rung must supply some."""

    payload = descent_payload(_descent("broken"))

    for rung in payload["rungs"]:
        if rung["status"] in ("healthy", "broken"):
            assert rung["evidence_keys"], f"{rung['rung']} offers no key to cite"
            for key in rung["evidence_keys"]:
                assert key in payload["evidence_keys"]


def test_the_prompt_renders_and_contains_the_descent_json():
    prompt = build_report_prompt(_descent("broken"))

    assert "{descent_json}" not in prompt, "the placeholder was not substituted"
    assert "interface_line_down" in prompt
    assert "PE2:interface:Gi0/0/0/0" in prompt


# --------------------------------------------------------------------------- #
# Golden case 2 -- cause_not_localised
# --------------------------------------------------------------------------- #


def test_cause_not_localised_reaches_the_prompt_as_itself():
    """The finding most likely to be reported badly.

    The model will want to say "the cause is the BGP session"; the honest
    answer is that rungs were broken above and everything beneath was healthy,
    so nothing in the evidence explains the fault. The prompt must carry the
    finding *and* the healthy lower rungs, so the model can see there is
    nothing underneath rather than inferring one.
    """

    payload = descent_payload(_mixed_descent())

    expect = _case("cause_not_localised")["expect"]
    assert payload["finding"] == expect["finding"]
    assert payload["causal_chain"] == expect["causal_chain"]
    statuses = {r["rung"]: r["status"] for r in payload["rungs"]}
    for rung in expect["broken_rungs"]:
        assert statuses[rung] == "broken"
    for rung in expect["healthy_rungs"]:
        assert statuses[rung] == "healthy"


def test_the_prompt_names_the_cause_not_localised_constraint():
    """The C slot must tell the model what this finding means, or it will
    reach for the topmost broken rung and call it the cause."""

    prompt = _flat(load_prompt("report", 1))
    assert "cause_not_localised" in prompt
    assert "must not be dressed up as a diagnosis" in prompt


# --------------------------------------------------------------------------- #
# Golden case 3 -- the refusal path
# --------------------------------------------------------------------------- #


def test_an_unevaluated_rung_reaches_the_prompt_as_undetermined():
    """The refusal path, from real data: `t0` predates template capture, so the
    transport rung genuinely cannot be read."""

    payload = descent_payload(_descent("t0"))

    expect = _case("refusal_path")["expect"]
    assert payload["finding"] == expect["finding"]
    assert bool(payload["reason"]) is expect["reason_present"]
    statuses = [r["status"] for r in payload["rungs"]]
    assert ("unevaluated" in statuses) is expect["has_unevaluated_rung"]
    # The walk stopped there -- nothing below was collected or presented.
    assert len(payload["rungs"]) == expect["rung_count"]


def test_the_prompt_forbids_filling_an_unread_rung_with_a_plausible_cause():
    """The single most valuable thing the Constraints slot buys.

    A model that supplies a likely cause for a rung it could not read produces
    something indistinguishable from a finding -- the same silent-degradation
    shape as OBS-006, OBS-043 and OBS-044, arriving in the report layer.
    """

    prompt = _flat(load_prompt("report", 1))

    assert "Do not supply a likely cause" in prompt
    assert "Do not speculate" in prompt
    assert "a plausible guess in its place is worse than the gap" in prompt
    assert "undetermined" in prompt


# --------------------------------------------------------------------------- #
# Shape of the expected output
# --------------------------------------------------------------------------- #


def test_the_prompt_specifies_the_three_section_schema():
    prompt = _flat(load_prompt("report", 1))

    for key in ("observations", "interpretations", "recommendation"):
        assert f'"{key}"' in prompt
    assert '"requires_human": true' in prompt


def test_the_anchor_example_is_itself_valid_json_with_intact_citations():
    """The worked example is the strongest signal in the prompt. If its own
    citations were inconsistent it would teach exactly the wrong thing."""

    prompt = load_prompt("report", 1)
    start = prompt.index('{\n  "observations"')
    depth, end = 0, None
    for i, ch in enumerate(prompt[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            end = i + 1
            break
    anchor = json.loads(prompt[start:end])

    assert len(anchor["observations"]) == 5
    labels = {f"obs-{i}" for i in range(1, len(anchor["observations"]) + 1)}
    for interpretation in anchor["interpretations"]:
        assert interpretation["based_on"], "an interpretation citing nothing"
        for ref in interpretation["based_on"]:
            assert ref in labels, f"anchor cites {ref}, which does not exist"
    for observation in anchor["observations"]:
        assert observation["evidence_key"]
    assert anchor["recommendation"]["requires_human"] is True
