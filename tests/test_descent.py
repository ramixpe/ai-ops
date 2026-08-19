"""The walker's semantics (T-024) and the acceptance test (T-025).

T-025 is the milestone that proves the architecture: a deterministic ladder
that localises a cause, offline, with no lab and no API key and no model call.

Both the walk rule and this test's own criteria were rewritten on 2026-08-16.
The original said "the descent stops at a named rung / no rung below the
stopping rung was collected", which endorsed the defect it should have caught
(Q-017, OBS-055) — it would have passed against the buggy walker and failed
against the correct one.
"""

from __future__ import annotations

import pytest
from helpers import set_device_environment

from agent_nettools import checks, flows
from agent_nettools.descent import run_descent
from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
from agent_nettools.flows import Aggregation, CollectStep, DeviceScope, Flow, Rung, SubjectRule
from agent_nettools.investigation import investigate
from agent_nettools.network_tools import run_template

# The inventory arithmetic that maps a BGP subject to the device that owns it.
# Never a name-pattern inference: this fabric punishes that twice out of four
# (T-006, OBS-024).
_LOOPBACK_OWNER = {
    "10.255.0.11": "PE1",
    "10.255.0.12": "PE2",
    "10.255.0.13": "PE3",
    "10.255.0.14": "PE4",
    "10.255.0.31": "RR1",
}


def _resolver(subject: str) -> str:
    return _LOOPBACK_OWNER[subject]


def _collector_for(label: str):
    """Serve a rung's evidence from committed fixtures. No lab, no network."""

    def collect(device, rung, subject):
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
        return collect.calls.append((device, rung.name)) or evidence

    collect.calls = []
    return collect


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    set_device_environment(monkeypatch)


# --------------------------------------------------------------------------- #
# T-025 — the acceptance test
# --------------------------------------------------------------------------- #


def test_acceptance_the_descent_localises_the_cause_on_the_broken_label():
    """**The test that would have caught Q-017.**

    RR1 → 10.255.0.12 with PE2 isolated must descend all the way to
    `interface_line_down` **on PE2**, with rungs 1–3 in the causal chain.
    Under the original walk rule this returned `peer_not_established` — a
    restatement of the alert — and never reached the shut interface.
    """

    collector = _collector_for("broken")
    result = run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=collector, resolver=_resolver,
    )

    # The descent visited every rung -- it did not stop at the first fault.
    assert result.rung_path == (
        "bgp_session", "transport", "route_to_peer", "igp_adjacency", "interface",
    )

    # The reported finding is the LOWEST broken rung.
    assert result.finding == "interface_line_down"
    assert result.finding in flows.flow_for("bgp_session").findings

    # And it is on PE2 -- the subject's device, not the device we started from.
    assert result.cause is not None
    assert result.cause.rung == "interface"
    assert result.cause.device == "PE2"

    # Higher broken rungs are the causal chain: the evidence that this cause
    # explains the symptom the investigation started from.
    assert [o.rung for o in result.causal_chain] == [
        "bgp_session", "transport", "route_to_peer", "igp_adjacency",
    ]

    # Every conclusive CheckResult cites the evidence it read.
    for outcome in result.outcomes:
        if outcome.result.is_conclusive:
            assert outcome.result.evidence_keys, outcome.rung
    assert result.evidence_keys


def test_acceptance_an_established_peer_walks_the_whole_ladder_healthy():
    result = run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.11",
        collector=_collector_for("healthy"), resolver=_resolver,
    )

    assert result.finding == "all_layers_healthy"
    assert len(result.outcomes) == 5
    assert result.cause is None
    assert result.causal_chain == ()


def test_acceptance_the_same_peer_is_healthy_once_the_link_is_restored():
    """`healthy` and `broken` differ only by the shut interfaces, so the same
    subject must give opposite answers. If a future recapture made the labels
    identical this fails, rather than quietly halving the coverage."""

    result = run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=_collector_for("healthy"), resolver=_resolver,
    )

    assert result.finding == "all_layers_healthy"


def test_acceptance_no_model_was_called(monkeypatch):
    """The central claim: the descent is parse-and-compare end to end.

    Any attempt to reach a provider raises, so a model call cannot pass
    silently.
    """

    import agent_nettools.llm_analysis as llm

    def explode(*_a, **_k):
        raise AssertionError("the descent called a model")

    monkeypatch.setattr(llm, "get_provider", explode)
    monkeypatch.setattr(llm, "analyze_evidence", explode)

    result = run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=_collector_for("broken"), resolver=_resolver,
    )
    assert result.finding == "interface_line_down"


# --------------------------------------------------------------------------- #
# B-107 — the second flow, proving the pattern repeats
#
# `isis_adjacency` needs no resolver at all: every rung is DeviceScope.LOCAL,
# unlike bgp_session's rungs 4-5 which cross to the subject's device. That
# absence is itself part of what this flow demonstrates -- not every ladder
# needs Q-013's machinery, and a flow author should not reach for a resolver
# it does not need.
# --------------------------------------------------------------------------- #


def test_acceptance_isis_adjacency_is_cause_not_localised_on_the_naturally_broken_pair():
    """PE3's `Gi0/0/0/0` on the `isis-broken` label (B-496): LLDP confirms P2
    is cabled there, from both ends; neither end has the IS-IS adjacency; the
    interface itself is up. **A real, naturally-occurring fault the healthy
    corpus has structurally zero coverage of** -- the fixture this flow was
    built alongside, per B-107's own row in BACKLOG.md.

    The honest answer is `cause_not_localised`, not a guessed IS-IS-specific
    cause: the interface is ruled healthy, and what remains (area mismatch,
    authentication, network type) lives on the config axis this build does
    not read (B-104).
    """

    result = run_descent(
        flows.flow_for("isis_adjacency"), "PE3", "Gi0/0/0/0",
        collector=_collector_for("isis-broken"),
    )

    assert result.rung_path == ("isis_adjacency", "interface")
    assert result.finding == "cause_not_localised"
    assert result.finding in flows.flow_for("isis_adjacency").findings

    assert result.cause is not None
    assert result.cause.rung == "isis_adjacency"
    assert result.cause.device == "PE3"
    assert result.causal_chain == ()

    for outcome in result.outcomes:
        if outcome.result.is_conclusive:
            assert outcome.result.evidence_keys, outcome.rung
    assert result.evidence_keys


def test_acceptance_isis_adjacency_is_provable_from_the_other_end_too():
    """B-496 captured both PE3 and P2 specifically so the asymmetry is
    provable from either side. Same finding, the other device's evidence."""

    result = run_descent(
        flows.flow_for("isis_adjacency"), "P2", "Gi0/0/0/4",
        collector=_collector_for("isis-broken"),
    )

    assert result.finding == "cause_not_localised"
    assert result.cause is not None
    assert result.cause.device == "P2"


def test_acceptance_isis_adjacency_is_healthy_on_the_healthy_label():
    result = run_descent(
        flows.flow_for("isis_adjacency"), "PE3", "Gi0/0/0/0",
        collector=_collector_for("healthy"),
    )

    assert result.finding == "all_layers_healthy"
    assert len(result.outcomes) == 2
    assert result.cause is None
    assert result.causal_chain == ()


def test_acceptance_isis_adjacency_localises_to_the_shut_interface_on_broken():
    """PE2's `Gi0/0/0/0` on `broken`: admin-down explains the silence in both
    `isis` and `lldp` at once. Unlike the `isis-broken` case above, this one
    *does* localise, because the interface rung itself is broken -- the
    causal chain the walk is supposed to produce when it can."""

    result = run_descent(
        flows.flow_for("isis_adjacency"), "PE2", "Gi0/0/0/0",
        collector=_collector_for("broken"),
    )

    assert result.rung_path == ("isis_adjacency", "interface")
    assert result.finding == "interface_line_down"
    assert result.cause is not None
    assert result.cause.rung == "interface"
    assert result.cause.device == "PE2"
    assert [o.rung for o in result.causal_chain] == ["isis_adjacency"]


def test_acceptance_isis_adjacency_no_model_was_called(monkeypatch):
    import agent_nettools.llm_analysis as llm

    def explode(*_a, **_k):
        raise AssertionError("the descent called a model")

    monkeypatch.setattr(llm, "get_provider", explode)
    monkeypatch.setattr(llm, "analyze_evidence", explode)

    result = run_descent(
        flows.flow_for("isis_adjacency"), "PE3", "Gi0/0/0/0",
        collector=_collector_for("isis-broken"),
    )
    assert result.finding == "cause_not_localised"


def test_acceptance_isis_adjacency_survives_the_coherence_re_read():
    """Regression pin for a real bug found while building this flow (B-107).

    The first draft of `isis_neighbor_up` read the `interfaces` section
    without the top rung declaring it in `collect`. The full walk (built from
    the whole epoch) saw it and correctly localised to `interface_line_down`;
    the coherence re-read (`epoch._collect_one_rung`, which re-collects only a
    rung's *own* `collect` tuple) did not, rung 1 flipped from `broken` to
    `unevaluated` on re-read, and the finding became `temporally_incoherent`
    instead. Caught by running the CLI end to end against the `broken`
    fixture, not by a unit test of the check in isolation -- which is why this
    test goes through `investigate()` and a real epoch rather than
    `run_descent` with the hand-built, always-complete collector the other
    tests in this file use (that collector does not gate evidence by
    `rung.collect` at all, so it could not have caught this).
    """

    from agent_nettools.fixtures import fixture_sender
    from agent_nettools.investigation import investigate

    result = investigate(
        "PE2", "Gi0/0/0/0", flow="isis_adjacency", sender=fixture_sender(label="broken"),
    )

    assert result.descent.finding == "interface_line_down"
    assert result.descent.coherence is not None
    assert result.descent.coherence.status == "coherent"


# --------------------------------------------------------------------------- #
# B-109 — `ldp_session` through `investigate()`, every label
#
# The operator explicitly approved adding an LDP command on 2026-08-19,
# lifting the block a previous agent correctly hit (no approved command, no
# fixture -- see the git history of this section for that agent's fallback,
# which pinned `isis_adjacency` a second time instead). `platforms.py` grew
# two additive intents ("ldp"/"ldp_discovery"), and `tests/fixtures/` grew
# real captures under `t0`/`t1` for all nine devices (2026-08-19) -- see
# `parsers.py`'s `parse_xr_ldp_neighbor`/`parse_xr_ldp_discovery` docstrings
# and `flows.LDP_SESSION_FLOW`'s module comment for the command shapes and the
# dependency reasoning.
#
# Same reason as the isis_adjacency block above for going through
# `investigate()` rather than `run_descent(..., collector=_collector_for(...))`:
# only the real `EvidenceEpoch` gates a rung's evidence by its declared
# `collect` tuple, so only it can catch an undeclared dependency. Every case
# below reports `coherence.status == "coherent"`, asserted directly rather
# than trusted, so a future rung that reads evidence its own `collect` does
# not name would fail here exactly the way B-107's did.
#
# `ldp`/`ldp_discovery` are captured under `t0`/`t1` only (not `healthy`/
# `broken`/`isis-broken` -- see the capture note in this session's report:
# backfilling those older labels with today's live LDP data would mix two
# different points in time inside one label, which is worse than the two
# labels this flow actually has). That means fewer rows than the
# isis_adjacency table above.
#
# **B-592, 2026-08-19 refresh.** The row this comment used to describe here
# (P3's `Gi0/0/0/2`, then `no_fault_on_path`) was itself a symptom of the
# *previous* capture's own inconsistency, not a real fact about the fabric:
# that capture's `ldp`/`ldp_discovery` commands had been taken live while its
# `interfaces`/`isis` commands were three weeks stale, so the interface rung
# still called the port `admin-down` from before the link existed. A single
# consistent pass on 2026-08-19 recaptured everything in the fabric at once,
# and with `interfaces brief` no longer lying about that port, the row now
# reads `all_layers_healthy`, like the other same-shape rows above it.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("device", "subject", "label", "expected_finding"),
    [
        # PE1's session to P1 is genuinely Oper in both live captures.
        ("PE1", "Gi0/0/0/0", "t0", "all_layers_healthy"),
        ("PE1", "Gi0/0/0/0", "t1", "all_layers_healthy"),
        # P2's session to P1 -- the control for the broken case two rows down,
        # same device, a different, healthy interface.
        ("P2", "Gi0/0/0/0", "t0", "all_layers_healthy"),
        # The live, still-open P2<->PE3 defect (OBS-159/B-496), read from P2's
        # side: `show mpls ldp discovery` shows Hello sending on Gi0/0/0/4
        # (direction `xmit`) with no `LDP Id:` line -- never receiving a
        # reply. The interface itself is up, so the walk localises to the top
        # rung and stops there: `cause_not_localised`, the same finding
        # isis_adjacency reaches on this identical underlying link.
        ("P2", "Gi0/0/0/4", "t0", "cause_not_localised"),
        ("P2", "Gi0/0/0/4", "t1", "cause_not_localised"),
        # The same defect read from PE3's side is *not* symmetric the way
        # isis_adjacency's LLDP corroboration made the IS-IS case: PE3's own
        # `show mpls ldp discovery` has no entry at all for Gi0/0/0/0 -- not
        # even an orphan "sending, no reply" one -- so this check has nothing
        # to corroborate against and honestly reports `undetermined` rather
        # than guessing. Real, measured asymmetry, not a bug: the two ends of
        # one broken link can carry different amounts of diagnostic evidence.
        ("PE3", "Gi0/0/0/0", "t0", "undetermined"),
        # A loopback carries no LDP Hello at all -- link-local multicast has
        # nothing to attach to. The module's absence-is-unevaluated rule.
        ("PE1", "Lo0", "t0", "undetermined"),
        # B-592 (2026-08-19 refresh): P3's Gi0/0/0/2 now reads healthy on both
        # rungs. LDP reports an Oper session to P2, and `interfaces brief`
        # (recaptured live in the same pass) now shows the port up/up rather
        # than the admin-down state a three-week-stale capture used to carry
        # here -- see the section comment above. Previously pinned as
        # `no_fault_on_path`; that finding described the old capture's own
        # internal inconsistency, not a fact about the fabric.
        ("P3", "Gi0/0/0/2", "t0", "all_layers_healthy"),
    ],
)
def test_acceptance_ldp_session_through_investigate_pins_every_measured_case(
    device, subject, label, expected_finding
):
    """Measured, not predicted: every value here came from running
    `investigate()` against the named fixture and reading the result, the
    same discipline `test_acceptance_isis_adjacency_through_investigate_pins
    _every_label` established for the previous flow.
    """

    result = investigate(
        device, subject, flow="ldp_session", sender=fixture_sender(label=label),
    )

    assert result.descent.finding == expected_finding
    assert result.descent.finding in flows.flow_for("ldp_session").findings
    assert result.descent.coherence is not None
    assert result.descent.coherence.status == "coherent"


@pytest.mark.parametrize(
    ("device", "subject", "label", "expected_finding"),
    [
        # PE3's Gi0/0/0/0 carries a real IS-IS adjacency to P2 on `healthy`,
        # the synthetic no-fault-injected label -- unaffected by the
        # 2026-08-19 refresh, which only touched `t0`/`t1`.
        ("PE3", "Gi0/0/0/0", "healthy", "all_layers_healthy"),
        # B-592 (2026-08-19 refresh): `t0`/`t1` used to agree with `healthy`
        # here too, but the live lab's own B-496 fault (below) is not
        # intermittent -- it is present in *every* live capture taken since,
        # including these two. LLDP still confirms P2 is cabled on this port
        # (both ends), the interface itself is up, and there is still no
        # IS-IS adjacency -- the same `cause_not_localised` shape `isis-broken`
        # pins on purpose, now measured on the live labels instead of only a
        # constructed one. This is not something to normalise away: B-496 is
        # real and still open in the lab.
        ("PE3", "Gi0/0/0/0", "t0", "cause_not_localised"),
        ("PE3", "Gi0/0/0/0", "t1", "cause_not_localised"),
        # PE2's Gi0/0/0/0 is admin-down on `broken` -- the interface rung
        # explains the isis_adjacency rung's silence, so the walk localises.
        ("PE2", "Gi0/0/0/0", "broken", "interface_line_down"),
        # `isis-broken` (B-496): a real, naturally-occurring fault with no
        # interface fault to explain it. LLDP confirms the P2<->PE3 cabling
        # from both ends while neither end has formed the IS-IS adjacency,
        # so the honest answer is `cause_not_localised`, not a guessed
        # IS-IS-specific cause -- provable from either side of the link.
        ("PE3", "Gi0/0/0/0", "isis-broken", "cause_not_localised"),
        ("P2", "Gi0/0/0/4", "isis-broken", "cause_not_localised"),
    ],
)
def test_acceptance_isis_adjacency_through_investigate_pins_every_label(
    device, subject, label, expected_finding
):
    """Measured, not predicted: every value here came from running
    `investigate()` against the named fixture label and reading the result,
    the same way `test_acceptance_isis_adjacency_survives_the_coherence_re_read`
    above measured `broken`. See the section comment for why this needs the
    real path rather than `_collector_for`.
    """

    result = investigate(
        device, subject, flow="isis_adjacency", sender=fixture_sender(label=label),
    )

    assert result.descent.finding == expected_finding
    assert result.descent.finding in flows.flow_for("isis_adjacency").findings

    # Both of this flow's rungs were walked on every label, fault or not --
    # the ladder is only two rungs deep and nothing short-circuits it early.
    assert result.descent.rung_path == ("isis_adjacency", "interface")

    # No collector was injected, so this ran through a real EvidenceEpoch and
    # its coherence re-read agreed with the first read on every label.
    assert result.descent.coherence is not None
    assert result.descent.coherence.status == "coherent"

    # Every conclusive rung cites the evidence it read.
    for outcome in result.descent.outcomes:
        if outcome.result.is_conclusive:
            assert outcome.result.evidence_keys, outcome.rung
    assert result.descent.evidence_keys


# --------------------------------------------------------------------------- #
# T-024 — walk semantics
# --------------------------------------------------------------------------- #


def _stub_flow(*statuses, findings=None):
    """A flow whose rungs return fixed verdicts, for testing the walk itself."""

    def make(status, index):
        def check(_evidence, _subject=None):
            if status == checks.UNEVALUATED:
                return checks.unevaluated(reason=f"rung {index} unread")
            builder = checks.healthy if status == checks.HEALTHY else checks.broken
            kwargs = {"subject": "s", "evidence_keys": (f"k{index}",)}
            if status == checks.BROKEN:
                kwargs["reason"] = f"rung {index} broken"
            return builder(**kwargs)

        return check

    names = findings or [f"finding_{i}" for i in range(len(statuses))]
    rungs = tuple(
        Rung(name=f"r{i}", collect=(CollectStep("bgp"),), check=make(s, i), finding=names[i])
        for i, s in enumerate(statuses)
    )
    return Flow(
        object_type="bgp_session",
        subject_schema="x",
        descent=rungs,
        findings=frozenset(names) | flows.UNIVERSAL_FINDINGS,
    )


def _null_collector(_device, _rung, _subject):
    return {}


def test_a_broken_rung_does_not_stop_the_walk():
    """The defect Q-017 fixed. All three rungs must be visited."""

    flow = _stub_flow(checks.BROKEN, checks.BROKEN, checks.BROKEN)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.rung_path == ("r0", "r1", "r2")
    assert result.finding == "finding_2"  # the LOWEST broken rung


def test_a_healthy_rung_does_not_stop_the_walk_either():
    """RR1's healthy IS-IS did not prove PE2's was.

    The property under test is that the walk **visits every rung**, which
    `rung_path` asserts. The finding changed at B-428: with rung 1 healthy there
    is no symptom, so the broken rung below it is an observation and not a
    cause, and the descent says `no_fault_on_path` rather than naming it.

    That is the whole of B-428 in one test, and the walk behaviour it was
    written for is untouched.
    """

    flow = _stub_flow(checks.HEALTHY, checks.HEALTHY, checks.BROKEN)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.rung_path == ("r0", "r1", "r2"), "the walk still visits every rung"
    assert result.finding == flows.NO_FAULT_ON_PATH
    assert result.cause is None or result.finding == flows.NO_FAULT_ON_PATH
    assert [o.rung for o in result.outcomes if o.status == checks.BROKEN] == ["r2"], (
        "the broken rung is still recorded -- as an observation, not a cause"
    )


def test_a_healthy_rung_does_not_stop_the_walk_when_there_IS_a_symptom():
    """The companion B-428 would otherwise have silently removed.

    The test above used a rung-1-healthy ladder, which now routes to
    `no_fault_on_path` -- so it no longer exercises *descending past a healthy
    rung to a broken one below and naming it*. That is the property round 1
    depends on, and without this it would have no coverage at all.
    """

    flow = _stub_flow(checks.BROKEN, checks.HEALTHY, checks.BROKEN)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.rung_path == ("r0", "r1", "r2")
    assert result.finding == "finding_2", "the LOWEST broken rung, past a healthy one"
    assert result.cause is not None and result.cause.rung == "r2"
    assert [o.rung for o in result.causal_chain] == ["r0"]


def test_an_unevaluated_rung_stops_the_walk():
    """Nothing below a rung we could not read is trustworthy."""

    flow = _stub_flow(checks.HEALTHY, checks.UNEVALUATED, checks.BROKEN)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.rung_path == ("r0", "r1")
    assert result.finding == flows.UNDETERMINED
    assert result.reason


def test_nothing_below_an_unevaluated_rung_is_collected():
    """Asserted on the collector, not just the result."""

    calls = []

    def counting(device, rung, subject):
        calls.append(rung.name)
        return {}

    flow = _stub_flow(checks.HEALTHY, checks.UNEVALUATED, checks.BROKEN)
    run_descent(flow, "RR1", "s", collector=counting)

    assert calls == ["r0", "r1"], "a rung below the unevaluated one was collected"


def test_an_all_healthy_ladder_reports_all_layers_healthy():
    flow = _stub_flow(checks.HEALTHY, checks.HEALTHY)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.finding == flows.ALL_LAYERS_HEALTHY


def test_only_the_symptom_rung_broken_is_cause_not_localised():
    """Broken at the top, healthy all the way down: the descent confirmed the
    symptom and found nothing beneath to explain it. Reporting the top rung's
    finding here would restate the alert and dress it as a diagnosis."""

    flow = _stub_flow(checks.BROKEN, checks.HEALTHY, checks.HEALTHY)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.finding == flows.CAUSE_NOT_LOCALISED
    assert result.cause is not None and result.cause.rung == "r0"


def test_a_deeper_broken_rung_keeps_its_own_finding():
    """The other side of `cause_not_localised`: "no route while the IGP and
    interface are healthy" *is* localised -- to routing -- and must not be
    flattened into "could not localise"."""

    flow = _stub_flow(checks.BROKEN, checks.BROKEN, checks.HEALTHY)
    result = run_descent(flow, "RR1", "s", collector=_null_collector)

    assert result.finding == "finding_1"
    assert [o.rung for o in result.causal_chain] == ["r0"]


# --------------------------------------------------------------------------- #
# Scope and aggregation
# --------------------------------------------------------------------------- #


def test_a_subject_scoped_rung_without_a_resolver_is_unevaluated_not_local():
    """Falling back to the local device would produce the wrong-device reading
    Q-013 exists to prevent -- and it would look healthy."""

    rung = Rung(
        name="igp",
        collect=(CollectStep("isis"),),
        check=lambda _e, _s=None: checks.healthy(subject="s", evidence_keys=("k",)),
        finding="igp_isolated",
        device_scope=DeviceScope.SUBJECT,
        subject_rule=SubjectRule.DEVICE_WIDE,
    )
    flow = Flow(
        object_type="bgp_session", subject_schema="x", descent=(rung,),
        findings=frozenset({"igp_isolated"}) | flows.UNIVERSAL_FINDINGS,
    )

    result = run_descent(flow, "RR1", "s", collector=_null_collector, resolver=None)

    assert result.finding == flows.UNDETERMINED
    assert "resolver" in result.reason


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [(Aggregation.ALL_HEALTHY, checks.BROKEN), (Aggregation.ANY_HEALTHY, checks.HEALTHY)],
)
def test_aggregation_over_a_set_follows_the_declared_rule(aggregation, expected):
    """One healthy member and one broken. ECMP is any-suffices; path hops are
    all-must. The rule is the rung's to declare, not the check's to assume."""

    seen = {"n": 0}

    def alternating(_evidence, _subject=None):
        seen["n"] += 1
        if seen["n"] == 1:
            return checks.healthy(subject="s", evidence_keys=("k1",))
        return checks.broken(reason="down", subject="s", evidence_keys=("k2",))

    rung = Rung(
        name="path", collect=(CollectStep("isis"),), check=alternating,
        finding="igp_isolated", device_scope=DeviceScope.PATH,
        subject_rule=SubjectRule.DEVICE_WIDE, aggregation=aggregation,
    )
    flow = Flow(
        object_type="bgp_session", subject_schema="x", descent=(rung,),
        findings=frozenset({"igp_isolated"}) | flows.UNIVERSAL_FINDINGS,
    )

    result = run_descent(
        flow, "RR1", "s", collector=_null_collector, resolver=lambda _s: ["P1", "P2"]
    )
    assert result.outcomes[0].status == expected


def test_unevaluated_dominates_an_aggregation():
    """If one member could not be read, the set's verdict is not known either.
    Absence is not health, at every level."""

    seen = {"n": 0}

    def mixed(_evidence, _subject=None):
        seen["n"] += 1
        if seen["n"] == 1:
            return checks.healthy(subject="s", evidence_keys=("k1",))
        return checks.unevaluated(reason="not read")

    rung = Rung(
        name="path", collect=(CollectStep("isis"),), check=mixed, finding="igp_isolated",
        device_scope=DeviceScope.PATH, subject_rule=SubjectRule.DEVICE_WIDE,
        aggregation=Aggregation.ANY_HEALTHY,
    )
    flow = Flow(
        object_type="bgp_session", subject_schema="x", descent=(rung,),
        findings=frozenset({"igp_isolated"}) | flows.UNIVERSAL_FINDINGS,
    )

    result = run_descent(
        flow, "RR1", "s", collector=_null_collector, resolver=lambda _s: ["P1", "P2"]
    )
    assert result.finding == flows.UNDETERMINED
