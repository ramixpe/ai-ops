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
