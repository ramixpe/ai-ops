"""Fabric-wide analysis: the prompt must actually carry the cross-device
correlation evidence, and the deterministic (Phase 4) health verdicts
alongside it -- so the model's job is interpretation, not detection.

**B-590, the 2026-08-19 refresh.** This module used to ground its test in one
specific measured incident: PE2 and PE4 had zero IS-IS adjacencies, so neither
could reach RR1's loopback, so their iBGP sessions toward RR1 sat Idle -- and
RR1 independently reported those same two sessions (10.255.0.12, 10.255.0.14)
as Idle from the other side. One correlated incident with a single root cause,
not four unrelated facts, and the prompt-content test below asserted the
*prompt* carried what was needed to reach that conclusion.

That incident is fully resolved in the live lab (see tests/test_health.py's
``test_ground_truth_severity_map_matches_fixtures`` for the current, measured
per-device severities): PE2 and PE4 both carry live IS-IS adjacencies now, and
every one of RR1's four iBGP sessions is Established. Nothing in the fabric is
`critical` any more, and the findings that remain (baseline drift from an
unregenerated ``inventory/lab.yaml``, a few `bgp_no_prefixes`/
`interface_admin_up_line_down` warnings) do not share a single root cause the
way the old PE2/PE4/RR1 triangle did -- asserting them as one "correlated
incident" would misdescribe the fabric, not measure it. The test that pinned
the old incident's presence in the prompt was removed rather than repointed
at a fabricated substitute; see its former location below for the reasoning
in full. The two tests that remain in this file assert on `build_fabric_prompt`
structurally (stability, verdict defaulting) and are unaffected by any of
this.
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


# --------------------------------------------------------------------------- #
# REMOVED, 2026-08-19 (B-590): `test_fabric_prompt_contains_pe2_pe4_isolation_
# and_rr1_idle_peers`. It pinned the PE2/PE4-isolation-causes-RR1-Idle
# incident described in the module docstring above by asserting on
# `evaluate_fabric`'s real output, then asserting the prompt carried that
# same content (rule names, device names, the two Idle peer addresses) so a
# model reading it could correlate the two sides. That incident no longer
# exists in the live fabric -- there is no critical severity anywhere in the
# current `t0` capture (see tests/test_health.py) -- and nothing that
# replaced it shares a single root cause the same clean way, so writing a
# new version of this test would mean asserting a correlation that is not
# actually there. Per this session's brief: propose deletion with reasoning
# rather than contrive a replacement finding. If the fabric ever regains a
# real cross-device incident with a demonstrable single root cause, a test
# like this one is exactly the right shape to re-add -- `git log` this file
# to recover the removed version as a template.
#
# What is not lost: `test_fabric_prompt_content_carries_a_real_finding` below
# still pins that verdict content (a rule name, a subject) reaches the
# rendered prompt text at all -- the plumbing this test also exercised --
# using whatever the fabric's real findings are today, without claiming they
# correlate.
# --------------------------------------------------------------------------- #


def test_fabric_prompt_content_carries_a_real_finding(monkeypatch):
    """The plumbing the removed test above also covered: a verdict's rule
    name and subject must actually reach the rendered prompt text, not just
    exist in the `verdicts` dict handed to `build_fabric_prompt`.

    PE3 is the device to ground this in: post-refresh (B-590/592) it is the
    one device whose `isis_adjacency_count_drift` is a real regression (its
    IS-IS count fell below its learned baseline -- the live B-496 fault, not
    baseline staleness), not just a stale-baseline artifact like most of the
    other findings elsewhere in the fabric. See tests/test_health.py's
    ground-truth test for the full severity map this reads from.
    """

    evidence_by_device = _fabric_evidence(monkeypatch)
    verdicts = evaluate_fabric(evidence_by_device)

    pe3_rules = {f["rule"] for f in verdicts["devices"]["PE3"]["findings"]}
    assert "isis_adjacency_count_drift" in pe3_rules
    assert verdicts["devices"]["PE3"]["severity"] == "warning"

    prompt = build_fabric_prompt(evidence_by_device, verdicts)

    assert "isis_adjacency_count_drift" in prompt
    assert '"PE3"' in prompt or "### PE3" in prompt

    # The prompt must instruct correlation, not just dump data -- unchanged
    # by any of this, since it is a static instruction, not derived from
    # `verdicts`.
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
