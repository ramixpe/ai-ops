"""Argument fabrication — the input side of the containment boundary (B-459).

Every other mechanism in this build guards what a tool **returns**. The
allowlist governs what may be sent, `render_command` how a parameter is
rendered, `boundary.sanitize` what may leave the MCP surface, grounding what a
model may claim. **Nothing guarded what a caller supplies.**

The failure is worth stating precisely because nothing in it malfunctions.
`investigate("RR1", "10.255.0.99")` for a peer that does not exist:
`render_command` accepts a well-formed address — canonicalisation is about
syntax, which is correct and is not this. Rung 1 reads `show bgp summary`, finds
no matching row, and honestly reports the session is not Established. Every rung
beneath answers honestly. The report cites real evidence keys. Grounding
confirms they resolve.

**The output is a fully grounded, correctly cited, deterministically derived
investigation of a session that does not exist.** Every component did its job on
the input it was given, which is why no gate caught it and why the check has to
sit at the input.
"""

from __future__ import annotations

import pytest

from agent_nettools import checks, flows
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
from agent_nettools.investigation import investigate

OWNER = {"10.255.0.11": "PE1", "10.255.0.12": "PE2", "10.255.0.13": "PE3",
         "10.255.0.31": "RR1", "10.255.0.99": "PE2"}


def _run(subject, label="broken", device="RR1"):
    return investigate(device, subject, sender=fixture_sender(label=label),
                       resolver=lambda s: OWNER[s])


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #


def test_a_fabricated_peer_is_refused_before_the_descent_walks():
    """**The item.** Refused, and refused *early* -- no rungs at all.

    Walking first and refusing afterwards would still collect from two devices
    and still produce a rung table about a session nobody has.
    """

    result = _run("10.255.0.99")

    assert result.finding == flows.SUBJECT_NOT_FOUND
    assert result.descent.outcomes == (), "nothing was walked"
    assert result.trustworthy is False


def test_the_refusal_is_not_undetermined():
    """The distinction the acceptance criterion turns on.

    `undetermined` means *a rung could not be read*, and reading is exactly what
    succeeded here -- the device answered, and what it said is that it has no
    such peer. A caller told `undetermined` retries; a caller told
    `subject_not_found` corrects the question.
    """

    assert _run("10.255.0.99").finding != flows.UNDETERMINED
    assert flows.SUBJECT_NOT_FOUND in flows.UNIVERSAL_FINDINGS


def test_the_refusal_says_what_the_device_actually_has():
    """A refusal that only says "not found" invites another guess."""

    reason = _run("10.255.0.99").descent.reason or ""

    assert "no BGP neighbour at 10.255.0.99" in reason
    for real in ("10.255.0.11", "10.255.0.12", "10.255.0.13", "10.255.0.14"):
        assert real in reason


def test_a_real_peer_is_unaffected():
    """Anti-vacuity: the check must not refuse everything.

    Without this, a `subject_present` that always returned broken would satisfy
    every other test in this file.
    """

    result = _run("10.255.0.12")

    assert result.finding == "interface_line_down"
    assert len(result.descent.outcomes) == 5


# --------------------------------------------------------------------------- #
# The check itself
# --------------------------------------------------------------------------- #


def test_an_unreadable_bgp_intent_is_unevaluated_not_absent():
    """"The device has no such peer" and "we could not ask" are different
    answers, and only one of them means the caller should change the question.

    Reading a failed collection as "no such peer" would turn every transport
    failure into an accusation that the operator made the address up.
    """

    result = checks.bgp_peer_exists({"device": "RR1"}, "10.255.0.12")

    assert result.status == "unevaluated"


def test_the_interface_flow_matches_short_and_long_spellings():
    """`show route` says `GigabitEthernet0/0/0/0`, `show interfaces brief` says
    `Gi0/0/0/0`. Both name the same port **on this device**, which is the only
    scope that comparison is valid in (OBS-117)."""

    evidence = dict(load_fixture_evidence("PE2", label="healthy"))

    for spelling in ("Gi0/0/0/0", "GigabitEthernet0/0/0/0"):
        assert checks.interface_exists(evidence, spelling).status == "healthy", spelling

    assert checks.interface_exists(evidence, "Gi9/9/9/9").status == "broken"


# --------------------------------------------------------------------------- #
# Every flow declares one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("object_type", sorted(flows.FLOWS), ids=sorted(flows.FLOWS))
def test_every_implemented_flow_declares_a_subject_check(object_type):
    """§0.12. A flow added without one would accept any subject silently, and
    the gap would look exactly like a flow that had been checked."""

    flow = flows.flow_for(object_type)

    assert flow.subject_present is not None, (
        f"{object_type} accepts any subject; declare `subject_present`"
    )
    assert callable(flow.subject_present)


def test_an_injected_collector_skips_the_check_deliberately():
    """A caller supplying its own evidence has already decided what exists.

    Asking it whether the subject is real would be asking the test harness to
    validate the test -- §0.13's setup face, and the reason this is `None`
    rather than a silent pass.
    """

    from agent_nettools.investigation import _collect_for_rung

    result = investigate(
        "RR1", "10.255.0.99", resolver=lambda s: OWNER[s],
        collector=lambda d, r, s: _collect_for_rung(
            d, r, s, sender=fixture_sender(label="broken")
        ),
    )

    assert result.finding != flows.SUBJECT_NOT_FOUND, (
        "the injected path is not policed by a check over injected evidence"
    )


def test_the_refusal_is_still_a_rendered_report():
    """B-439's contract does not have an exception for refusals.

    A `None` report here would send a caller to a model's prose for the one
    result whose entire value is that it is **not** a claim about the network.
    """

    from agent_nettools.investigation import EMITTED

    payload = _run("10.255.0.99").to_payload()

    assert payload["report"]["status"] == EMITTED
    assert payload["report"]["authoritative"] is True
    content = payload["report"]["content"]
    assert content["observations"] == [], "nothing was observed, so nothing is cited"
    assert "No investigation was run" in content["interpretations"][0]["claim"]
    assert content["recommendation"]["requires_human"] is True


def test_exit_two_because_no_answer_about_the_network_was_produced():
    """Not exit 1. Exit 1 means the network is broken, and this run made no
    finding about the network at all -- it made one about the question."""

    from agent_nettools import cli

    result = _run("10.255.0.99")
    assert result.trustworthy is False
    assert result.finding not in (flows.ALL_LAYERS_HEALTHY, flows.NO_FAULT_ON_PATH)
    assert cli.EXIT_CRITICAL == 2
