"""The four injection rounds, as a regression suite (B-427's seed).

Each round is recorded as the rung-status vector its live run produced, plus the
finding and exit code that vector must yield. Rung statuses are the whole of what
determines a finding — `_finding_for` reads nothing else — so replaying the
vector reproduces the round exactly, without a lab and without a model.

**Round 4 is the reason this file exists.** It produced `interface_line_down`
with exit code 1 on a BGP session that was Established and carrying traffic,
exactly as OBS-082 predicted before the fault was applied. B-428 changed that to
`no_fault_on_path` and exit 0. **Rounds 1–3 must be untouched by that change**,
and pinning all four here is what makes "untouched" a fact rather than a claim.

Live results:

| # | Vector | Finding then | Finding now |
|---|---|---|---|
| 1 | B B B B H | `igp_isolated` | unchanged |
| 2 | B B H H H | `transport_blocked` | unchanged |
| 3 | B B H H H | `transport_blocked` | unchanged |
| 4 | H H H H B | `interface_line_down`, exit 1 | **`no_fault_on_path`, exit 0** |
"""

from __future__ import annotations

import pytest

from agent_nettools import checks, flows
from agent_nettools.checks import CheckResult
from agent_nettools.descent import DescentResult, RungOutcome, _finding_for

B, H = checks.BROKEN, checks.HEALTHY

#: (id, rung statuses, per-rung device, expected finding, expected exit code)
ROUNDS = [
    ("round-1-igp-shut-PE3",      [B, B, B, B, H],
     ["RR1", "RR1", "RR1", "PE3", "PE3"], "igp_isolated",       1),
    ("round-2-transport-PE1",     [B, B, H, H, H],
     ["RR1", "RR1", "RR1", "PE1", "PE1"], "transport_blocked",  1),
    ("round-3-bgp-admin-shut-PE2", [B, B, H, H, H],
     ["RR1", "RR1", "RR1", "PE2", "PE2"], "transport_blocked",  1),
    ("round-4-true-negative-PE2", [H, H, H, H, B],
     ["RR1", "RR1", "RR1", "PE2", "PE2"], flows.NO_FAULT_ON_PATH, 0),
]

#: Two shapes from the committed corpus, pinned alongside so a change to the
#: finding logic cannot pass by only satisfying the live rounds.
CORPUS = [
    ("captured-broken-label", [B, B, B, B, B], "interface_line_down", 1),
    ("healthy-label",         [H, H, H, H, H], flows.ALL_LAYERS_HEALTHY, 0),
    ("cause-not-localised",   [B, H, H, H, H], flows.CAUSE_NOT_LOCALISED, 1),
]


def _descend(statuses, devices=None):
    flow = flows.flow_for("bgp_session")
    devices = devices or ["RR1"] * len(statuses)
    outcomes = [
        RungOutcome(
            rung.name, device,
            CheckResult(status, reason=f"{rung.name} is {status}", subject="10.255.0.12",
                        evidence_keys=(f"{device}:{rung.name}",)),
        )
        for rung, status, device in zip(flow.descent, statuses, devices, strict=True)
    ]
    finding = _finding_for(flow, outcomes, None)
    return DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding=finding, outcomes=tuple(outcomes),
        evidence_keys=tuple(k for o in outcomes for k in o.result.evidence_keys),
    )


def _exit_code(result):
    """The mapping `cli._cmd_investigate` applies, isolated from argument wiring."""

    from agent_nettools.cli import EXIT_OK, EXIT_WARNING

    if result.finding in (flows.ALL_LAYERS_HEALTHY, flows.NO_FAULT_ON_PATH):
        return EXIT_OK
    return EXIT_WARNING


@pytest.mark.parametrize(
    ("name", "statuses", "devices", "finding", "code"), ROUNDS, ids=[r[0] for r in ROUNDS]
)
def test_each_round_reproduces_its_recorded_result(name, statuses, devices, finding, code):
    result = _descend(statuses, devices)

    assert result.finding == finding
    assert _exit_code(result) == code


@pytest.mark.parametrize(
    ("name", "statuses", "finding", "code"), CORPUS, ids=[c[0] for c in CORPUS]
)
def test_the_corpus_shapes_are_unchanged(name, statuses, finding, code):
    result = _descend(statuses)

    assert result.finding == finding
    assert _exit_code(result) == code


def test_round_4_is_the_only_round_b428_changed():
    """The claim "rounds 1–3 are untouched", asserted rather than stated.

    Without this, a future change to `_finding_for` could satisfy round 4 by
    breaking one of the others and the suite would still be green on three of
    four parametrised cases — which reads as a pass.
    """

    findings = {name: _descend(s, d).finding for name, s, d, _, _ in ROUNDS}

    assert findings["round-1-igp-shut-PE3"] == "igp_isolated"
    assert findings["round-2-transport-PE1"] == "transport_blocked"
    assert findings["round-3-bgp-admin-shut-PE2"] == "transport_blocked"
    assert findings["round-4-true-negative-PE2"] == flows.NO_FAULT_ON_PATH

    assert len({f for f in findings.values()}) == 3, "three distinct findings across four rounds"


def test_round_4_names_no_cause_and_still_reports_what_is_broken():
    """B-428's actual requirement, in one place.

    "Broken rungs recorded as observations rather than as a cause" is two
    assertions, and only having the first would produce a run that silently
    drops a real interface fault — the opposite mistake, and worse than the
    false positive it replaced.
    """

    from agent_nettools.investigation import InvestigationResult

    result = _descend([H, H, H, H, B], ["RR1", "RR1", "RR1", "PE2", "PE2"])
    payload = InvestigationResult(
        device="RR1", subject="10.255.0.12", flow="bgp_session", descent=result
    ).to_payload()

    assert payload["finding"] == flows.NO_FAULT_ON_PATH
    assert payload["cause"] is None, "no cause is named"
    assert payload["off_path"] == [
        {"rung": "interface", "device": "PE2", "reason": "interface is broken"}
    ], "and the broken rung is still reported"

    broken = [r for r in payload["rungs"] if r["status"] == checks.BROKEN]
    assert len(broken) == 1 and broken[0]["rung"] == "interface"


@pytest.mark.parametrize("fmt", ["json", "table", "summary"])
def test_every_renderer_says_something_is_broken_off_the_path(fmt):
    """A reader must not come away with "nothing was found".

    Something on PE2 genuinely is down. It is not on the path between RR1 and
    this peer, and reporting only the first half of that would be the opposite
    of the false positive B-428 removed.
    """

    from agent_nettools import output
    from agent_nettools.investigation import InvestigationResult

    result = _descend([H, H, H, H, B], ["RR1", "RR1", "RR1", "PE2", "PE2"])
    rendered = output.render(
        InvestigationResult(
            device="RR1", subject="10.255.0.12", flow="bgp_session", descent=result
        ).to_payload(),
        fmt,
    )

    assert "no_fault_on_path" in rendered
    assert "interface" in rendered
    assert "PE2" in rendered


def test_no_fault_on_path_is_in_the_flow_s_closed_finding_set():
    """A finding the descent can emit and the flow does not declare would slip
    past `Flow.__post_init__`'s check, which only validates rung findings."""

    flow = flows.flow_for("bgp_session")

    assert flows.NO_FAULT_ON_PATH in flow.findings
    assert flows.NO_FAULT_ON_PATH in flows.UNIVERSAL_FINDINGS
    for object_type in flows.FLOWS:
        assert flows.NO_FAULT_ON_PATH in flows.flow_for(object_type).findings
