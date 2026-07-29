"""Fabric-wide analysis: the prompt must actually carry the cross-device
correlation evidence, and the deterministic (Phase 4) health verdicts
alongside it -- so the model's job is interpretation, not detection.

Ground truth (measured, from the committed t0 fixtures -- see
tests/test_health.py for the exact same numbers pinned against health.py
directly): PE2 and PE4 have zero IS-IS adjacencies, so neither can reach
RR1's loopback, so their iBGP sessions toward RR1 sit Idle -- and RR1
independently reports those same two sessions (10.255.0.12, 10.255.0.14) as
Idle from the other side. That is one correlated incident with a single root
cause, not four unrelated facts. This test only asserts the *prompt* carries
what is needed to reach that conclusion; it never asserts on model output.
"""

from __future__ import annotations

from helpers import set_device_environment

from agent_nettools.fabric_analysis import build_fabric_prompt
from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.health import evaluate_fabric
from agent_nettools.lab import all_devices


def _fabric_evidence(monkeypatch) -> dict:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label="t0") for name in all_devices()}


def test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers(monkeypatch):
    evidence_by_device = _fabric_evidence(monkeypatch)
    verdicts = evaluate_fabric(evidence_by_device)

    # Sanity-check the ground truth itself before trusting the prompt built
    # from it -- if this ever stops matching, the fixtures changed and the
    # rest of this test is meaningless.
    assert verdicts["devices"]["PE2"]["severity"] == "critical"
    assert verdicts["devices"]["PE4"]["severity"] == "critical"
    assert verdicts["devices"]["RR1"]["severity"] == "critical"
    pe2_rules = {f["rule"] for f in verdicts["devices"]["PE2"]["findings"]}
    pe4_rules = {f["rule"] for f in verdicts["devices"]["PE4"]["findings"]}
    assert "isis_isolated" in pe2_rules
    assert "isis_isolated" in pe4_rules
    rr1_idle_subjects = {
        f["subject"] for f in verdicts["devices"]["RR1"]["findings"] if f["rule"] == "bgp_session_down"
    }
    assert rr1_idle_subjects == {"10.255.0.12", "10.255.0.14"}

    prompt = build_fabric_prompt(evidence_by_device, verdicts)

    # The correlation evidence: PE2/PE4's IS-IS isolation ...
    assert "isis_isolated" in prompt
    assert '"PE2"' in prompt or "### PE2" in prompt
    assert '"PE4"' in prompt or "### PE4" in prompt
    # ... and RR1's two Idle peers, by address, so the model can tie the two
    # sides of the same incident together.
    assert "10.255.0.12" in prompt
    assert "10.255.0.14" in prompt
    assert "bgp_session_down" in prompt
    assert "RR1" in prompt

    # The prompt must instruct correlation, not just dump data.
    assert "Correlated Incidents" in prompt


def test_fabric_prompt_never_interpolates_a_timestamp_into_the_instructions(monkeypatch):
    """The static instructions must stay stable across calls with different
    evidence timestamps -- see llm_analysis.py's caching notes: interpolating
    a timestamp into the cached (system) half would invalidate the cache on
    every single call."""

    from agent_nettools.fabric_analysis import FABRIC_ANALYSIS_PROMPT

    evidence_by_device = _fabric_evidence(monkeypatch)
    prompt = build_fabric_prompt(evidence_by_device)

    assert prompt.startswith(FABRIC_ANALYSIS_PROMPT)


def test_build_fabric_prompt_defaults_verdicts_to_evaluate_fabric(monkeypatch):
    evidence_by_device = _fabric_evidence(monkeypatch)

    prompt_default = build_fabric_prompt(evidence_by_device)
    prompt_explicit = build_fabric_prompt(evidence_by_device, evaluate_fabric(evidence_by_device))

    assert prompt_default == prompt_explicit
