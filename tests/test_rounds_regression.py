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

**Round 5 (B-451/OBS-109, `docs/build/ROUND-5.md`) extends this file the same
way round 4 did**, and for the same reason: it is a live result whose scored
outcome, at the time, was produced by code that has since been fixed (B-454).
Per B-427's binding corpus-integrity rule, the round's *historical* score is
not re-derived here -- it is recorded in prose, below and in
`docs/build/EVALUATION-CORPUS.md`, exactly as it was scored on 2026-08-17.
What this file pins is only what current code does with the same rung vector
and the same coherence inputs (skew, re-read agreement) round 5 actually
produced -- replayable because `docs/build/ROUND-5.md` §7 archived the full
per-probe table, not only the findings (§6.1d).

Four representative probes out of round 5's thirteen, chosen to cover the
distinct mechanisms B-454 separated:

| Probe | Vector | Skew | Re-read | Finding **then** (exit) | Finding **now** (exit) |
|---|---|---:|---|---|---|
| 00 | H H H H H | 4.0s | agrees | `all_layers_healthy` (0) | unchanged |
| 03 | H H B B H | 4.0s | agrees | `no_fault_on_path` (0) | unchanged |
| 08 | H H B B H | 34.0s | agrees | `temporally_incoherent` (2) | **`no_fault_on_path` (0)** |
| 09 | H B B B H | 38.5s | **disagrees** (`bgp_session` H->B) | `temporally_incoherent` (2) | unchanged |
| 10 | B B B B H | 38.2s | agrees | `temporally_incoherent` (2) | **`igp_isolated` (1)** |

Probes 08 and 10 are the measured cost B-454's own docstring names: a width
breach with an agreeing re-read used to be indistinguishable from a real
fabric transition, and it threw away a correct, settled `igp_isolated`
finding (probe 10) four minutes after the fabric had stopped changing.
Probe 09 is the mechanism working as designed, unaffected by the fix: a
genuine re-read *disagreement* still refuses, before and after B-454.

Round 6 is sealed and unrun (`docs/build/ROUND-6.md` §5 is empty) and has no
payload to pin. Rounds 7, 8 and 8b are mechanism validation, not blind
diagnostic trials -- none of them ran `investigate`, so none produced a rung
vector `_finding_for` can replay; see `docs/build/EVALUATION-CORPUS.md` for
where they are scored instead.
"""

from __future__ import annotations

import pytest

from agent_nettools import checks, flows
from agent_nettools.checks import CheckResult
from agent_nettools.descent import DescentResult, RungOutcome, _finding_for
from agent_nettools.epoch import Coherence, Reread

B, H = checks.BROKEN, checks.HEALTHY

#: (id, rung statuses, per-rung device, expected finding, expected exit code)
ROUNDS = [
    ("round-1-igp-shut-PE3",      [B, B, B, B, H],
     ["RR1", "RR1", "RR1", "PE3", "PE3"], "igp_isolated",       1),
    ("round-2-transport-PE1",     [B, B, H, H, H],
     ["RR1", "RR1", "RR1", "PE1", "PE1"], "transport_blocked",  1),
    ("round-3-bgp-admin-shut-PE2", [B, B, H, H, H],
     ["RR1", "RR1", "RR1", "PE2", "PE2"], "transport_blocked",  1),
    # **B-456 note, 2026-08-17.** This vector is kept and still asserts the
    # right thing: *given* rung 1 healthy and rung 5 broken, `no_fault_on_path`
    # is correct, and `_finding_for` is unchanged.
    #
    # What is now open is whether the vector is still **producible**. Round 4
    # was one uplink shut on a device with two, with the IGP reconverged --
    # under `EACH_PATH_INTERFACE` the reverse route names only the survivor, so
    # rung 5 should read healthy and the vector should become `[H,H,H,H,H]`,
    # i.e. `all_layers_healthy` with no `no_fault_on_path` involved.
    #
    # **That is reasoning, not measurement.** Round 4 ran live and its payload
    # was not archived, so it cannot be replayed and this cannot be checked from
    # the corpus. The vector stays until round 6 measures it. Deleting it now
    # would remove a true assertion about the finding logic on the strength of
    # an untested prediction about the rung -- and if the prediction is right,
    # what should follow is a *recorded change*, not a quiet disappearance.
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


def _descend(statuses, devices=None, coherence=None):
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
    finding = _finding_for(flow, outcomes, coherence)
    return DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding=finding, outcomes=tuple(outcomes),
        evidence_keys=tuple(k for o in outcomes for k in o.result.evidence_keys),
        coherence=coherence,
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


# --------------------------------------------------------------------------- round 5

#: Round 5's device list, matching round 3's: rungs 1-3 are `DeviceScope.LOCAL`
#: to RR1 (the subject's owner is resolved for rungs 4-5 only), and both rounds
#: share the same subject, `10.255.0.12` (PE2's loopback) -- see `ROUND-5.md`
#: S1 and OBS-091, retained in Git history.
_ROUND5_DEVICES = ["RR1", "RR1", "RR1", "PE2", "PE2"]


def _reread(rung: str, device: str, *, agrees: bool) -> Reread:
    """One re-read, for a rung whose *status* either held or flipped.

    The two strings only have to differ to disagree -- `Reread.agrees` is a
    plain `before == after` -- so `"healthy"`/`"broken"` stands in for
    whatever detail a real epoch would carry.
    """

    after = "healthy" if agrees else "broken"
    return Reread(rung=rung, device=device, before="healthy", after=after)


#: (id, rung statuses, skew seconds, rereads, expected finding under CURRENT
#: code, expected exit code under CURRENT code, finding AS SCORED AT THE TIME
#: (`ROUND-5.md` S7), exit code AS SCORED AT THE TIME)
#:
#: The last two columns are data, not assertions -- B-427's binding rule is
#: that a corpus never rewrites what the system did at the time, so this file
#: does not attempt to reproduce the pre-B-454 scoring logic. They are carried
#: here only so a reader of this file sees both numbers beside each other,
#: the same way `ROUNDS`' docstring table does for round 4.
ROUND5_PROBES = [
    ("round-5-probe-00-baseline", [H, H, H, H, H], 4.0,
     (_reread("bgp_session", "RR1", agrees=True),),
     flows.ALL_LAYERS_HEALTHY, 0, flows.ALL_LAYERS_HEALTHY, 0),
    ("round-5-probe-03-propagating", [H, H, B, B, H], 4.0,
     (_reread("bgp_session", "RR1", agrees=True),),
     flows.NO_FAULT_ON_PATH, 0, flows.NO_FAULT_ON_PATH, 0),
    # B-454's measured cost, half 1: a width breach with an agreeing re-read.
    # Scored `temporally_incoherent`/exit 2 at the time (OBS-109) -- the same
    # rungs as probe 03 above, thrown away for width alone. Current code keeps
    # the finding and qualifies it (`Coherence.caveat`) instead of refusing.
    ("round-5-probe-08-window-limited", [H, H, B, B, H], 34.0,
     (_reread("bgp_session", "RR1", agrees=True),),
     flows.NO_FAULT_ON_PATH, 0, flows.TEMPORALLY_INCOHERENT, 2),
    # The mechanism firing on a real transition -- unaffected by B-454 in
    # either direction, because a re-read *disagreement* refused before the
    # fix and still refuses after it.
    ("round-5-probe-09-fabric-moved", [H, B, B, B, H], 38.5,
     (_reread("bgp_session", "RR1", agrees=False),),
     flows.TEMPORALLY_INCOHERENT, 2, flows.TEMPORALLY_INCOHERENT, 2),
    # B-454's measured cost, half 2, and the sharper of the two: a *settled*,
    # fully-converged broken fabric -- four minutes stable -- scored
    # `temporally_incoherent`/exit 2 at the time. The correct answer
    # (`igp_isolated`) was sitting in the same payload the whole time.
    ("round-5-probe-10-settled-igp-isolated", [B, B, B, B, H], 38.2,
     (_reread("bgp_session", "RR1", agrees=True),),
     "igp_isolated", 1, flows.TEMPORALLY_INCOHERENT, 2),
]


def _coherence_for(skew_seconds, rereads):
    return Coherence(skew_seconds=skew_seconds, bound_seconds=30.0, rereads=rereads)


def _full_exit_code(result):
    """The mapping `cli._cmd_investigate` applies when it also has a
    trustworthy check to make, isolated from argument wiring -- `_exit_code`
    above cannot express exit 2 because none of rounds 1-4's pinned vectors
    ever produced an untrustworthy answer.
    """

    from agent_nettools.cli import EXIT_CRITICAL, EXIT_OK, EXIT_WARNING
    from agent_nettools.investigation import InvestigationResult

    trustworthy = InvestigationResult(
        device=result.device, subject=result.subject, flow=result.flow, descent=result
    ).trustworthy
    if not trustworthy:
        return EXIT_CRITICAL
    if result.finding in (flows.ALL_LAYERS_HEALTHY, flows.NO_FAULT_ON_PATH):
        return EXIT_OK
    return EXIT_WARNING


@pytest.mark.parametrize(
    ("name", "statuses", "skew", "rereads", "finding_now", "exit_now", "finding_then", "exit_then"),
    ROUND5_PROBES, ids=[p[0] for p in ROUND5_PROBES],
)
def test_round_5_probe_reproduces_current_behavior(
    name, statuses, skew, rereads, finding_now, exit_now, finding_then, exit_then
):
    """Replays a round 5 probe's actual rung vector and coherence inputs
    (skew, re-read agreement) against current code.

    `finding_then`/`exit_then` are unused by the assertion below, on purpose
    -- see the module docstring and `ROUND5_PROBES`'s comment. They are
    parametrized in anyway so `pytest -v` prints both numbers on one line,
    which is the fastest way for a human to see a B-454-shaped regression
    (`finding_now` drifting back toward `finding_then`) without reading this
    file.
    """

    result = _descend(statuses, _ROUND5_DEVICES, coherence=_coherence_for(skew, rereads))

    assert result.finding == finding_now
    assert _full_exit_code(result) == exit_now


def test_round_5_probe_09_is_the_one_case_b454_must_not_change():
    """The positive control for the whole B-454 split (OBS-109's `4.2`).

    A re-read *disagreement* is positive evidence the fabric moved during the
    walk -- refuted only if no probe in the whole round ever shows it. It
    must keep refusing regardless of anything width-related, or the coherence
    check stops catching real transitions.
    """

    coherence = _coherence_for(38.5, (_reread("bgp_session", "RR1", agrees=False),))
    assert coherence.refuses is True

    result = _descend([H, B, B, B, H], _ROUND5_DEVICES, coherence=coherence)
    assert result.finding == flows.TEMPORALLY_INCOHERENT
    assert _full_exit_code(result) == 2


def test_round_5_width_only_breach_no_longer_destroys_a_settled_answer():
    """B-454's headline claim, pinned directly against probe 10's own numbers.

    Before B-454: any skew over the bound refused, full stop, regardless of
    re-read agreement -- probe 10 scored `temporally_incoherent`/exit 2 on a
    fabric that had been settled and broken for four minutes (OBS-109).
    After: a width breach with an agreeing re-read only qualifies the finding.
    """

    coherence = _coherence_for(38.2, (_reread("bgp_session", "RR1", agrees=True),))
    assert coherence.within_bound is False
    assert coherence.stable is True
    assert coherence.refuses is False, "an agreeing re-read must not refuse on width alone"

    result = _descend([B, B, B, B, H], _ROUND5_DEVICES, coherence=coherence)
    assert result.finding == "igp_isolated"
    assert _full_exit_code(result) == 1
