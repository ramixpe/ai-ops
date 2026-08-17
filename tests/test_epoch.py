"""The evidence epoch and the coherence check (B-436, review item 3).

The defect all three reviewers converged on has one property that made it
invisible to every test in the build: **nothing was wrong with any individual
observation.** Each rung's verdict was true of the instant it was taken. The
error was in treating five instants as one state, and no test that supplies a
single fixture snapshot can see it, because a fixture snapshot *is* one state
by construction — silent-failure shape 5.

So the tests here that matter are the ones where the fabric **moves between the
walk and the re-read**. `_recovering_sender` is the whole point of this file:
it serves the `broken` capture while the epoch is being collected and the
`healthy` capture when rung 1 is read again, which is exactly the reviewers'
scenario with the clock removed.
"""

from __future__ import annotations

import pytest

from agent_nettools import descent, epoch, flows, investigation
from agent_nettools.fixtures import fixture_sender

SUBJECT = "10.255.0.12"  # PE2's loopback -- the flow's subject, on the far end


def _resolver(subject):
    return {"10.255.0.12": "PE2"}[subject]


def _flow():
    return flows.flow_for("bgp_session")


def _recovering_sender():
    """`broken` during collection, `healthy` when rung 1 is read a second time.

    Anchored to the **meaning** of the call rather than to a call count: the
    only time `show bgp summary` is issued twice against RR1 is the epoch's
    collection and then the coherence re-read. A count-based flip would silently
    stop testing anything the first time the collection changed size.
    """

    broken = fixture_sender(label="broken")
    healthy = fixture_sender(label="healthy")
    seen = {"n": 0}

    def send(device, command):
        if device["name"] == "RR1" and command == "show bgp summary":
            seen["n"] += 1
            if seen["n"] >= 2:
                return healthy(device, command)
        return broken(device, command)

    return send


# --------------------------------------------------------------------------- #
# Collect once per device
# --------------------------------------------------------------------------- #


def test_each_device_is_collected_once_not_once_per_rung():
    """The measurement the design was built on.

    Five rungs across two devices used to mean five collections. It is the
    redundancy *and* the mechanism of the defect: each re-collection observes a
    different instant.
    """

    collections: list[str] = []

    def send(device, command):
        if command == "show bgp summary":
            collections.append(device["name"])
        return fixture_sender(label="broken")(device, command)

    built = epoch.collect_epoch(
        _flow(), "RR1", SUBJECT, resolver=_resolver, sender=send
    )

    assert collections == ["RR1", "PE2"], (
        "each device the flow touches is collected exactly once, in first-touched order"
    )
    assert built.devices == ("RR1", "PE2")


def test_the_epoch_costs_fewer_commands_than_collecting_per_rung():
    """B's speed objection, as a number rather than an intention.

    Measured rather than asserted approximately, so a change that quietly
    reintroduces per-rung collection fails here instead of only being slow.
    """

    def counted():
        base = fixture_sender(label="broken")
        calls: list[str] = []

        def send(device, command):
            calls.append(command)
            return base(device, command)

        return send, calls

    send_a, epoch_calls = counted()
    investigation.investigate("RR1", SUBJECT, sender=send_a, resolver=_resolver)

    send_b, per_rung_calls = counted()
    investigation.investigate(
        "RR1", SUBJECT, sender=send_b, resolver=_resolver,
        collector=lambda d, r, s: investigation._collect_for_rung(d, r, s, sender=send_b),
    )

    assert len(epoch_calls) < len(per_rung_calls)
    # 24 against 40 as measured 2026-08-17: 19 to collect, 5 to re-read. The
    # design predicted "19 + 2 re-reads" and the re-read costs 5, because the
    # interface rung fans out over three members plus its `interfaces` intent.
    # Pinned so the cost of the re-read stays visible rather than drifting.
    assert (len(epoch_calls), len(per_rung_calls)) == (24, 40)


def test_for_device_returns_the_shape_checks_already_read():
    """The contract that made this a change to *when*, not to *what*.

    Every pre-existing test passing untouched is the real evidence; this states
    it directly so the reason is written down somewhere.
    """

    built = epoch.collect_epoch(
        _flow(), "RR1", SUBJECT, resolver=_resolver, sender=fixture_sender(label="broken")
    )

    evidence = built.for_device("RR1")

    assert evidence["bgp"]["status"] == "success"
    assert f"bgp_neighbor:{SUBJECT}" in evidence
    assert f"route:{SUBJECT}/32" in evidence
    # A device's evidence never leaks another device's.
    assert not any(k.startswith("interface:") for k in evidence)
    assert any(k.startswith("interface:") for k in built.for_device("PE2"))


# --------------------------------------------------------------------------- #
# The reviewers' scenario
# --------------------------------------------------------------------------- #


def test_a_symptom_that_recovers_during_the_walk_is_not_a_causal_finding():
    """**The item.**

    Rung 1 reads `broken`; the fault clears; the interface rung reads a genuine
    failure on PE2. Every citation resolves, the chain is deterministic and
    grounding would pass — and the two observations never coexisted. Before
    B-436 this produced `interface_line_down`, exit 1, `trustworthy: true`.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=_recovering_sender(), resolver=_resolver
    )

    assert result.descent.finding == flows.TEMPORALLY_INCOHERENT
    assert result.trustworthy is False, "exit 2 -- an answer problem, not a network one"

    coherence = result.descent.coherence
    assert coherence is not None and not coherence.stable
    moved = [r for r in coherence.rereads if not r.agrees]
    assert [r.rung for r in moved] == ["bgp_session"]
    assert (moved[0].before, moved[0].after) == ("broken", "healthy")


def test_the_off_path_rungs_are_still_reported_when_the_epoch_is_incoherent():
    """Refusing the causal claim is not refusing the observations.

    The interface on PE2 really is down. Suppressing it along with the causal
    chain would turn "we cannot say these are related" into "we found nothing",
    which is a different and worse answer -- the same distinction B-428 drew.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=_recovering_sender(), resolver=_resolver
    )
    payload = result.to_payload()

    assert payload["cause"] is None, "no cause is asserted"
    assert any(r["status"] == "broken" for r in payload["rungs"]), (
        "but the broken rungs are all still there to be read"
    )


# --------------------------------------------------------------------------- #
# The bound, and what it is for
# --------------------------------------------------------------------------- #


def test_a_wide_window_with_agreeing_rereads_qualifies_and_does_not_refuse():
    """B-454, the half round 5 measured the cost of.

    Two agreeing reads across a wide window **bracket** a stable interval. What
    is unestablished is the middle, not the endpoints — so this qualifies the
    finding rather than destroying it, the same treatment `COVERAGE_LIMITED`
    gives a correlation the source could not fully support.
    """

    verdict = epoch.Coherence(
        skew_seconds=201.0,
        bound_seconds=30.0,
        rereads=(epoch.Reread("bgp_session", "RR1", "broken", "broken"),),
    )

    assert verdict.stable and not verdict.within_bound
    assert verdict.status == epoch.WINDOW_LIMITED
    assert not verdict.refuses, "a wide window qualifies a finding; it does not forbid one"
    assert not verdict.ok, "but it is not clean either, and says so"
    assert verdict.caveat and "not as 'true throughout'" in verdict.caveat


def test_a_disagreeing_reread_refuses_however_narrow_the_window():
    """The other half, and the asymmetry between them.

    Disagreement is *positive evidence* that the premise of a causal claim is
    false. Width is only absence of evidence about an interval whose endpoints
    both looked the same — and absence of evidence is what this layer refuses to
    convert into a verdict everywhere else.
    """

    verdict = epoch.Coherence(
        skew_seconds=0.5,
        bound_seconds=30.0,
        rereads=(epoch.Reread("bgp_session", "RR1", "healthy", "broken"),),
    )

    assert verdict.within_bound, "well inside the bound"
    assert verdict.status == epoch.FABRIC_MOVED
    assert verdict.refuses
    assert verdict.caveat is None, "a refusal carries no qualification -- there is no finding"


def test_the_skew_is_reported_on_a_pass_as_well_as_a_breach():
    """Design §2.3a.

    A bound that only speaks when violated has no observed distribution behind
    it, so it can only ever be revised on argument. If real epochs land at 25s
    against a 30s bound, that is a finding.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver
    )
    coherence = result.to_payload()["coherence"]

    assert coherence["ok"] is True
    assert coherence["within_bound"] is True
    assert coherence["skew_seconds"] >= 0.0
    assert coherence["bound_seconds"] == epoch.DEFAULT_SKEW_BOUND_SECONDS
    assert "of 30s allowed" in coherence["detail"], "the margin, not only the breach"


def test_an_over_bound_epoch_keeps_its_finding_and_carries_the_caveat():
    """B-454 end to end, and the regression round 5 paid for.

    **This test asserted the opposite until 2026-08-17**, and the change is a
    specification reversal rather than a fix to a broken test — recorded so the
    reversal is visible in the history rather than looking like drift.

    Round 5 probes 10, 11 and 99 saw a settled, fully converged broken fabric
    whose lowest broken rung was `igp_adjacency`. The right answer was
    `igp_isolated`. The bound replaced it with a refusal, four minutes after the
    fabric had stopped changing, because collecting it had taken 36 s.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver,
        skew_bound_seconds=0.0,
    )
    coherence = result.descent.coherence

    assert coherence.skew_seconds > 0.0, "a zero-skew epoch would make this vacuous"
    assert not coherence.within_bound and coherence.stable

    assert result.descent.finding == "interface_line_down", (
        "the finding survives a width breach"
    )
    assert result.trustworthy is True, "a qualified answer is still an answer"
    assert coherence.status == epoch.WINDOW_LIMITED

    payload = result.to_payload()
    assert payload["coherence"]["caveat"], (
        "and it never travels without its qualification -- a field, not something "
        "a renderer is trusted to add"
    )


# --------------------------------------------------------------------------- #
# Stability is a positive claim
# --------------------------------------------------------------------------- #


def test_an_empty_reread_set_is_not_stable():
    """`all([])` is `True`, which is the trap.

    Same rule as `_aggregate` refusing to call an empty member set healthy, and
    the same rule as `unevaluated` everywhere in this layer: nothing was
    checked, so nothing is known.
    """

    verdict = epoch.Coherence(skew_seconds=1.0, bound_seconds=30.0, rereads=())

    assert verdict.within_bound
    assert not verdict.stable
    assert not verdict.ok
    assert "stability is unknown" in verdict.detail


def _coherence_with(sender, *, resolver=_resolver):
    """Collect a clean epoch, then re-read it through ``sender``."""

    built = epoch.collect_epoch(
        _flow(), "RR1", SUBJECT, resolver=_resolver, sender=fixture_sender(label="broken")
    )
    outcomes = descent.run_descent(
        _flow(), "RR1", SUBJECT,
        collector=lambda d, _r, _s: built.for_device(d), resolver=_resolver,
    ).outcomes
    return epoch.check_coherence(
        _flow(), outcomes, built, SUBJECT,
        device="RR1", resolver=resolver, sender=sender,
    )


def test_a_device_that_went_unreachable_does_not_count_as_agreement():
    """A re-read we could not perform is a claim we cannot make.

    The tempting failure is to treat an errored re-read as "no change observed".
    That is absence-as-health (shape 2) in a new place.

    Note the *mechanism*: the SSH boundary returns a structured error rather
    than raising, so the check answers `unevaluated` -- which is not equal to
    `broken`, so the re-read disagrees and the epoch is incoherent. The rule
    holds through the layer's existing `unevaluated` discipline rather than
    through a special case, which is the better of the two ways to get here.
    """

    def send(device, command):
        if device["name"] == "PE2" and command.startswith("show interfaces"):
            raise RuntimeError("device unreachable")
        return fixture_sender(label="broken")(device, command)

    verdict = _coherence_with(send)

    interface = next(r for r in verdict.rereads if r.rung == "interface")
    assert (interface.before, interface.after) == ("broken", "unevaluated")
    assert not interface.agrees
    assert not verdict.ok


def test_a_reread_that_raises_is_a_sentinel_that_can_never_agree():
    """The second layer, and it is reachable rather than defensive decoration.

    Everything below the SSH boundary returns a structured error, but the
    re-read also resolves devices, and a resolver raising mid-run is a real
    failure mode. `reread_failed` is a value no rung status can equal, so
    "unknown" cannot collapse into "unchanged" by accident.
    """

    def raising_resolver(_subject):
        raise ValueError("the inventory moved under us")

    verdict = _coherence_with(fixture_sender(label="broken"), resolver=raising_resolver)

    failed = [r for r in verdict.rereads if r.after == "reread_failed"]
    assert failed, "the subject-scoped re-read could not resolve its device"
    assert not failed[0].agrees
    assert "the inventory moved under us" in (failed[0].detail or "")
    assert not verdict.ok


def test_only_the_symptom_and_the_cause_are_reread():
    """Two rungs, not five.

    Re-reading every rung would double the descent's cost and re-introduce the
    skew problem inside the re-read itself.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver
    )

    rungs = [r.rung for r in result.descent.coherence.rereads]
    assert rungs == ["bgp_session", "interface"], (
        "rung 1 is the symptom; `interface` is the lowest broken rung, the cause"
    )


def test_undetermined_outranks_incoherence():
    """Both are exit 2, and `undetermined` says *which rung could not be read*.

    Ordering them the other way would trade a specific reason for a general one
    at no gain.
    """

    outcomes = [
        descent.RungOutcome(
            "bgp_session", "RR1",
            descent.CheckResult("unevaluated", reason="the buffer could not be read"),
        )
    ]
    incoherent = epoch.Coherence(skew_seconds=900.0, bound_seconds=30.0)

    assert descent._finding_for(_flow(), outcomes, None, incoherent) == flows.UNDETERMINED


# --------------------------------------------------------------------------- #
# The prewalk precondition
# --------------------------------------------------------------------------- #


def test_every_shipped_flow_satisfies_the_prewalk_precondition():
    for object_type in flows.FLOWS:
        epoch.validate_prewalk_collection(flows.flow_for(object_type))


def test_a_parameter_the_builder_cannot_resolve_is_refused_by_name():
    """The tripwire, and the reason it is a hard failure.

    The code this replaced ended in an `else` that filled *any* unrecognised
    parameter with the raw subject -- correct for `address` and silently wrong
    for the first parameter that is not an address. A wrong object collected
    silently reads as a healthy answer about something nobody asked about.
    """

    bad = flows.Flow(
        object_type="interface",
        subject_schema="x",
        descent=(
            flows.Rung(
                name="vrf",
                collect=(flows.CollectStep("route", parameter="vrf", is_template=True),),
                check=lambda *_a, **_k: None,
                finding="interface_line_down",
            ),
        ),
        findings=frozenset({"interface_line_down"}) | flows.UNIVERSAL_FINDINGS,
    )

    with pytest.raises(ValueError, match="cannot resolve before the walk"):
        epoch.validate_prewalk_collection(bad)


def test_the_precondition_is_documented_where_a_flow_author_meets_it():
    """Not only enforced. A guard tells you after you have written the ladder.

    `Flow` is the type someone reads while designing one, so the constraint is
    stated in its docstring -- the builder's check is the enforcement of a
    stated rule, not the only place the rule appears.
    """

    doc = flows.Flow.__doc__ or ""

    assert "before the walk begins" in doc
    assert "earlier rung concluded" in doc


# --------------------------------------------------------------------------- #
# Opting out
# --------------------------------------------------------------------------- #


def test_an_injected_collector_gets_the_pre_epoch_behaviour():
    """What keeps every test written against the old contract meaningful.

    A coherence check over evidence that never came from a device would be
    measuring the test harness. It is `None`, not a vacuous pass.
    """

    result = investigation.investigate(
        "RR1", SUBJECT, resolver=_resolver,
        collector=lambda d, r, s: investigation._collect_for_rung(
            d, r, s, sender=fixture_sender(label="broken")
        ),
    )

    assert result.descent.coherence is None
    assert result.to_payload()["coherence"] is None
    assert result.descent.finding == "interface_line_down"
