"""Deterministic health verdicts: role invariants, baseline rules, and the meta rule.

The ground-truth test below is the phase's proof: every number in it is
independently verifiable by reading ``tests/fixtures/cisco_xr/<device>/t0/``
by eye (see the fixture dumps referenced in the docstrings of the smaller
tests further down), not invented. A future change to a rule that disagrees
with this map is a regression, not a rule improvement, unless the fixtures
themselves are shown to be wrong.
"""

from __future__ import annotations

from helpers import set_device_environment

from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.health import (
    evaluate_device,
    evaluate_fabric,
    exit_code_for_severity,
    severity_rank,
)
from agent_nettools.inventory_model import Device, Expected
from agent_nettools.lab import all_devices


def _evidence_by_device(monkeypatch, label: str = "t0") -> dict[str, dict]:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label=label) for name in all_devices()}


def _device(
    name: str,
    role: str = "edge",
    *,
    isis_adjacencies: int | None = None,
    bgp_peers: int | None = None,
) -> Device:
    expected = None
    if isis_adjacencies is not None or bgp_peers is not None:
        expected = Expected(isis_adjacencies=isis_adjacencies, bgp_peers=bgp_peers)
    return Device(name=name, mgmt_ip="10.0.0.1", role=role, site="lab", expected=expected)


def _parsed_section(records: list[dict], meta: dict | None = None) -> dict:
    return {
        "status": "success",
        "data": {"parse_status": "ok", "parsed": {"meta": meta or {}, "records": records}},
    }


def _unavailable_section(status: str = "unsupported") -> dict:
    return {"status": status, "data": {"parse_status": "unavailable", "parsed": None}}


def _unparsed_section(output: str = "garbled, unparseable output") -> dict:
    """A section whose commands ran fine but whose parser could not read the result."""

    return {
        "status": "success",
        "data": {"commands": {"show cmd": output}, "parse_status": "failed", "parsed": None},
    }


def _healthy_evidence(**overrides: dict) -> dict:
    """A complete, all-four-intents-healthy evidence dict.

    Every health intent (``isis``, ``bgp``, ``interfaces``, ``sr``) is present
    and parses successfully with data that trips no rule -- one adjacency Up
    (or ``isis_isolated`` fires), an active BGP process with no peers (or
    ``bgp_process_absent``/session/prefix rules fire), and empty interface/SR
    record lists. A test overrides only the intent(s) it means to exercise, so
    the other three don't contribute "collection failed" noise (see the
    ``_uneval_reason`` docstring in ``health.py``) to a verdict that is
    supposed to isolate one rule.
    """

    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section([{"system_id": "P1", "state": "Up"}]),
        "bgp": _parsed_section([], {"active": True}),
        "interfaces": _parsed_section([]),
        "sr": _parsed_section([]),
    }
    evidence.update(overrides)
    return evidence


# --------------------------------------------------------------------------- #
# Ground truth: the exact per-device severity map, from the committed fixtures.
# --------------------------------------------------------------------------- #


def test_ground_truth_severity_map_matches_fixtures(monkeypatch):
    """Measured real state (see the phase spec / fixture files), not hypothetical.

    `t0`/`t1` were fully re-captured live 2026-08-19 (B-516: the prior `t0` was
    internally inconsistent -- its isis/lldp/bgp/version files were three
    weeks stale relative to the rest of the label) and every `expected:`
    isis_adjacencies baseline in ``inventory/lab.yaml`` re-derived against the
    fresh capture except PE3's, deliberately left alone (B-465/B-496):

    - PE2 and PE4, once isolated, are now fully repaired: 2 IS-IS adjacencies
      each, matching their re-derived baseline and PE1/RR1's pattern, and both
      now carry an Established BGP session (0 prefixes, this lab's norm).
    - P2 and PE3 both show `isis_adjacency_count_drift` as a `warning` -- the
      one fault still live in this fabric (B-496: PE3's Gi0/0/0/0 is
      IS-IS-enabled but has no ipv4 address, so no adjacency forms on either
      end of the P2<->PE3 link). PE3's baseline was deliberately *not*
      rewritten to match its current broken count, so the drift rule keeps
      catching it every run instead of the baseline quietly blessing it.
    - RR1's four BGP peers are all Established with 0 prefixes (`info`); none
      is Idle any more.
    - PE1 has one admin-up/line-down subinterface (Gi0/0/0/2.300 -- its sibling
      Gi0/0/0/2.400 no longer exists on the device at all) and one down SR-TE
      policy; PE3 has the same single line-down subinterface. No other device
      has either.
    """

    evidence = _evidence_by_device(monkeypatch)

    result = evaluate_fabric(evidence)

    severities = {name: verdict["severity"] for name, verdict in result["devices"].items()}
    assert severities == {
        "P1": "ok",
        "P2": "warning",
        "P3": "ok",
        "P4": "ok",
        "PE1": "warning",
        "PE2": "info",
        "PE3": "warning",
        "PE4": "info",
        "RR1": "info",
    }
    assert result["severity"] == "warning"

    # No fixture at t0 fails to parse, so nothing should ever land in
    # "unevaluated" -- a silently-skipped intent would be a distinct bug from
    # a wrong severity.
    for verdict in result["devices"].values():
        assert verdict["unevaluated"] == []


def test_pe2_is_repaired_only_bgp_no_prefixes_remains(monkeypatch):
    """Was isolation + a down session + suspicious_baseline before B-465/B-516;
    PE2 is now fully repaired (2 IS-IS adjacencies, matching its re-derived
    baseline) and its only remaining finding is this lab's universal
    zero-prefixes info note."""

    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE2"], _device_from_inventory("PE2"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"bgp_no_prefixes"}
    assert verdict["severity"] == "info"


def test_pe4_is_repaired_and_now_has_an_active_default_bgp_process(monkeypatch):
    """Was isolation + no active BGP process + suspicious_baseline before
    B-465/B-516. PE4 is now fully repaired at the IS-IS layer (2 adjacencies,
    matching its re-derived baseline) and its default-AF BGP process, believed
    inactive by an earlier (B-504) comment in inventory/lab.yaml written from
    this same stale fixture data, is genuinely active: an Established session
    to RR1, up for 5d19h in the live capture -- so `bgp_process_absent` does
    not fire either."""

    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE4"], _device_from_inventory("PE4"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"bgp_no_prefixes"}
    assert verdict["severity"] == "info"


def test_rr1_findings_are_now_four_established_zero_prefix_peers(monkeypatch):
    """Was two Idle sessions (PE2, PE4) before B-465/B-516; both are repaired,
    so no `bgp_session_down` finding remains -- only the lab-wide zero-prefixes
    info note, once per peer."""

    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["RR1"], _device_from_inventory("RR1"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"bgp_no_prefixes"}
    assert "bgp_session_down" not in rules
    no_prefix_subjects = {
        f["subject"] for f in verdict["findings"] if f["rule"] == "bgp_no_prefixes"
    }
    assert no_prefix_subjects == {"10.255.0.11", "10.255.0.12", "10.255.0.13", "10.255.0.14"}
    assert verdict["severity"] == "info"


def test_pe1_findings_are_one_line_down_subinterface_one_sr_policy_and_info(monkeypatch):
    """Was two line-down subinterfaces before B-516's re-capture; Gi0/0/0/2.400
    no longer exists on the device at all (not merely down), so only
    Gi0/0/0/2.300 remains."""

    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE1"], _device_from_inventory("PE1"))

    by_rule: dict[str, list[dict]] = {}
    for finding in verdict["findings"]:
        by_rule.setdefault(finding["rule"], []).append(finding)

    assert {f["subject"] for f in by_rule["interface_admin_up_line_down"]} == {
        "Gi0/0/0/2.300",
    }
    assert {f["subject"] for f in by_rule["sr_policy_down"]} == {"20:10.255.0.13"}
    assert by_rule["bgp_no_prefixes"][0]["severity"] == "info"
    assert verdict["severity"] == "warning"
    assert verdict["counts"] == {"critical": 0, "unreachable": 0, "warning": 2, "info": 1}


def test_pe3_shows_the_confirmed_b496_fault_as_drift_not_a_baked_in_baseline(monkeypatch):
    """PE3 is the one device B-465 deliberately did not re-baseline: it has a
    real, still-open fault (B-496 -- Gi0/0/0/0 is IS-IS-enabled but has no
    ipv4 address, so no adjacency forms to P2). Writing PE3's current broken
    count (1) into `expected:` would make `isis_adjacency_count_drift` fall
    silent forever; leaving the baseline at its pre-fault value (2) means the
    rule reports the fault -- correctly as `warning`, since 1 is *below* 2 --
    on every run instead."""

    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE3"], _device_from_inventory("PE3"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"bgp_no_prefixes", "interface_admin_up_line_down", "isis_adjacency_count_drift"}
    drift = next(f for f in verdict["findings"] if f["rule"] == "isis_adjacency_count_drift")
    assert drift["severity"] == "warning"
    assert drift["expected"] == 2
    assert drift["actual"] == 1
    assert verdict["severity"] == "warning"


def test_core_routers_are_clean_except_p2_which_shows_the_b496_fault(monkeypatch):
    """P1, P3, P4 all have 5 IS-IS adjacencies exactly matching their re-derived
    baseline, no BGP process (fine for role core), and no interface/SR
    anomalies. P2 is the one core router touching the B-496 fault (it is the
    other end of the down PE3 link): 4 adjacencies against a baseline of 5, so
    `isis_adjacency_count_drift` correctly fires as `warning` rather than
    reading as clean."""

    evidence = _evidence_by_device(monkeypatch)

    for name in ("P1", "P3", "P4"):
        verdict = evaluate_device(evidence[name], _device_from_inventory(name))
        assert verdict["severity"] == "ok", (name, verdict["findings"])
        assert verdict["findings"] == []

    p2_verdict = evaluate_device(evidence["P2"], _device_from_inventory("P2"))
    assert p2_verdict["severity"] == "warning"
    p2_rules = {f["rule"] for f in p2_verdict["findings"]}
    assert p2_rules == {"isis_adjacency_count_drift"}
    drift = next(f for f in p2_verdict["findings"] if f["rule"] == "isis_adjacency_count_drift")
    assert drift["expected"] == 5
    assert drift["actual"] == 4


def _device_from_inventory(name: str) -> Device:
    from agent_nettools.inventory_model import load_inventory_file

    return next(d for d in load_inventory_file().devices if d.name == name)


# --------------------------------------------------------------------------- #
# Role invariants: independent of the (possibly broken) baseline.
# --------------------------------------------------------------------------- #


def test_isis_isolated_fires_even_when_baseline_expects_zero():
    """The central design constraint: a baseline of zero must never be read as
    healthy just because it matches observed state."""

    device = _device("PE9", role="edge", isis_adjacencies=0)
    evidence = {"platform": "cisco_xr", "isis": _parsed_section([])}

    verdict = evaluate_device(evidence, device)

    rules = {f["rule"] for f in verdict["findings"]}
    assert "isis_isolated" in rules
    assert "suspicious_baseline" in rules
    assert verdict["severity"] == "critical"


def test_isis_isolated_does_not_fire_with_adjacencies_present():
    device = _device("PE9", isis_adjacencies=1)
    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section([{"system_id": "P1", "state": "Up"}]),
    }

    verdict = evaluate_device(evidence, device)

    assert "isis_isolated" not in {f["rule"] for f in verdict["findings"]}


def test_isis_adjacency_not_up_reports_the_system_id():
    device = _device("PE9")
    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section(
            [
                {"system_id": "P1", "state": "Up"},
                {"system_id": "P2", "state": "Init"},
            ]
        ),
    }

    verdict = evaluate_device(evidence, device)

    findings = [f for f in verdict["findings"] if f["rule"] == "isis_adjacency_not_up"]
    assert len(findings) == 1
    assert findings[0]["subject"] == "P2"
    assert findings[0]["severity"] == "critical"


def test_bgp_process_absent_excluded_for_core_but_flagged_for_edge():
    evidence = {"platform": "cisco_xr", "bgp": _parsed_section([], {"active": False})}

    core_verdict = evaluate_device(evidence, _device("P9", role="core"))
    edge_verdict = evaluate_device(evidence, _device("PE9", role="edge"))
    rr_verdict = evaluate_device(evidence, _device("RR9", role="route-reflector"))

    assert "bgp_process_absent" not in {f["rule"] for f in core_verdict["findings"]}
    assert "bgp_process_absent" in {f["rule"] for f in edge_verdict["findings"]}
    assert "bgp_process_absent" in {f["rule"] for f in rr_verdict["findings"]}
    assert next(
        f for f in edge_verdict["findings"] if f["rule"] == "bgp_process_absent"
    )["severity"] == "warning"


def test_bgp_session_down_reads_session_state_directly():
    device = _device("PE9")
    evidence = {
        "platform": "cisco_xr",
        "bgp": _parsed_section(
            [
                {"neighbor": "10.0.0.1", "session_state": "Established", "prefixes_received": 5},
                {"neighbor": "10.0.0.2", "session_state": "Idle"},
            ]
        ),
    }

    verdict = evaluate_device(evidence, device)

    findings = [f for f in verdict["findings"] if f["rule"] == "bgp_session_down"]
    assert len(findings) == 1
    assert findings[0]["subject"] == "10.0.0.2"


def test_bgp_no_prefixes_is_info_only():
    device = _device("PE9")
    evidence = _healthy_evidence(
        bgp=_parsed_section([{"neighbor": "10.0.0.1", "session_state": "Established", "prefixes_received": 0}], {"active": True}),
    )

    verdict = evaluate_device(evidence, device)

    findings = [f for f in verdict["findings"] if f["rule"] == "bgp_no_prefixes"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert verdict["severity"] == "info"


def test_interface_admin_down_is_never_flagged():
    device = _device("PE9")
    evidence = {
        "platform": "cisco_xr",
        "interfaces": _parsed_section(
            [
                {"interface": "Gi0/0/0/2", "admin_state": "admin-down", "line_protocol": "admin-down"},
                {"interface": "Gi0/0/0/3", "admin_state": "up", "line_protocol": "down"},
            ]
        ),
    }

    verdict = evaluate_device(evidence, device)

    findings = [f for f in verdict["findings"] if f["rule"] == "interface_admin_up_line_down"]
    assert len(findings) == 1
    assert findings[0]["subject"] == "Gi0/0/0/3"


def test_sr_policy_down_reports_the_policy_id():
    device = _device("PE9")
    evidence = {
        "platform": "cisco_xr",
        "sr": _parsed_section(
            [
                {"policy": "10:10.0.0.1", "operational_state": "up"},
                {"policy": "20:10.0.0.1", "operational_state": "down"},
            ]
        ),
    }

    verdict = evaluate_device(evidence, device)

    findings = [f for f in verdict["findings"] if f["rule"] == "sr_policy_down"]
    assert len(findings) == 1
    assert findings[0]["subject"] == "20:10.0.0.1"


# --------------------------------------------------------------------------- #
# Baseline rules: drift from the recorded expectation.
# --------------------------------------------------------------------------- #


def test_isis_adjacency_count_drift_fires_on_mismatch_and_skips_when_absent():
    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section([{"system_id": "P1", "state": "Up"}]),
    }

    drifted = evaluate_device(evidence, _device("PE9", isis_adjacencies=2))
    assert "isis_adjacency_count_drift" in {f["rule"] for f in drifted["findings"]}

    matching = evaluate_device(evidence, _device("PE9", isis_adjacencies=1))
    assert "isis_adjacency_count_drift" not in {f["rule"] for f in matching["findings"]}

    no_baseline = evaluate_device(evidence, _device("PE9"))
    assert "isis_adjacency_count_drift" not in {f["rule"] for f in no_baseline["findings"]}


def test_losing_an_adjacency_warns_and_gaining_one_does_not():
    """B-465. Direction matters, and treating both as `warning` was measured wrong.

    Every `expected:` block in this lab was derived by `learn-topology` from
    the **broken** fabric and never re-derived. The fabric was then repaired,
    so the drift rule began firing on devices that had got *better* — and a
    warning about a restored adjacency is indistinguishable from one about a
    lost adjacency, which is the only case the rule was built for.

    An increase is `info` and says the baseline is probably stale. It is not
    silenced: a genuinely new adjacency is still worth a line, and suppressing
    it would trade one blind spot for another.
    """

    two_up = {
        "platform": "cisco_xr",
        "isis": _parsed_section([
            {"system_id": "P1", "state": "Up"},
            {"system_id": "P2", "state": "Up"},
        ]),
    }

    # Observed 2, baseline 1 -- the repaired case, as measured on PE1.
    gained = evaluate_device(two_up, _device("PE9", isis_adjacencies=1))
    drift = [f for f in gained["findings"] if f["rule"] == "isis_adjacency_count_drift"]
    assert len(drift) == 1
    assert drift[0]["severity"] == "info"
    assert "not a fault" in drift[0]["message"]
    assert "learn-topology" in drift[0]["message"]

    # Observed 2, baseline 3 -- lost connectivity, which is the real case.
    lost = evaluate_device(two_up, _device("PE9", isis_adjacencies=3))
    drift = [f for f in lost["findings"] if f["rule"] == "isis_adjacency_count_drift"]
    assert len(drift) == 1
    assert drift[0]["severity"] == "warning"
    assert "below" in drift[0]["message"]


def test_a_stale_baseline_no_longer_raises_a_healthy_device_to_warning():
    """The operational consequence, which is the reason the fix is worth making.

    A device that is genuinely fine, whose only complaint is that it has more
    adjacencies than a baseline learned from a broken fabric, must not page
    anyone. Before B-465 this scored `warning`.
    """

    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section([
            {"system_id": "P1", "state": "Up"},
            {"system_id": "P2", "state": "Up"},
        ]),
    }

    verdict = evaluate_device(evidence, _device("PE9", isis_adjacencies=1))

    # This fixture supplies only `isis`, so the other three intents are
    # correctly `intent_collection_failed` -- absence is never health. What
    # matters is that **no warning comes from the drift rule**, which is what
    # raised a repaired device to `warning` before B-465. `suspicious_baseline`
    # *does* legitimately warn here -- role "edge" has a floor of 2 and this
    # baseline is recorded at 1, exactly the PE1 shape B-465/OBS-146 measured
    # -- which is a real, separate signal about the stale baseline itself,
    # not the direction-of-drift defect this test isolates.
    warnings = [f for f in verdict["findings"] if f["severity"] == "warning"]
    assert {f["rule"] for f in warnings} == {"intent_collection_failed", "suspicious_baseline"}
    assert all(
        f["severity"] == "info"
        for f in verdict["findings"]
        if f["rule"] == "isis_adjacency_count_drift"
    )


def test_a_fragment_may_override_its_rule_severity_and_normally_does_not():
    """The override is the exception, and the default must stay the rule's.

    Without this, a fragment forgetting `severity` would silently inherit
    whatever the last edit left in place rather than the rule's declared value.
    """

    evidence = {
        "platform": "cisco_xr",
        "isis": _parsed_section([{"system_id": "P1", "state": "Up"}]),
    }
    verdict = evaluate_device(evidence, _device("PE9", isis_adjacencies=5))
    drift = [f for f in verdict["findings"] if f["rule"] == "isis_adjacency_count_drift"]
    # No `severity` in the fragment for the decrease path -> the rule's own.
    assert drift[0]["severity"] == "warning"


def test_bgp_peer_count_drift_fires_on_mismatch_and_skips_when_absent():
    evidence = {
        "platform": "cisco_xr",
        "bgp": _parsed_section([{"neighbor": "10.0.0.1", "session_state": "Established", "prefixes_received": 5}]),
    }

    drifted = evaluate_device(evidence, _device("PE9", bgp_peers=2))
    assert "bgp_peer_count_drift" in {f["rule"] for f in drifted["findings"]}

    matching = evaluate_device(evidence, _device("PE9", bgp_peers=1))
    assert "bgp_peer_count_drift" not in {f["rule"] for f in matching["findings"]}

    no_baseline = evaluate_device(evidence, _device("PE9"))
    assert "bgp_peer_count_drift" not in {f["rule"] for f in no_baseline["findings"]}


# --------------------------------------------------------------------------- #
# Meta rule.
# --------------------------------------------------------------------------- #


def test_suspicious_baseline_fires_on_zero_regardless_of_role():
    """The value the old, narrow rule already caught -- still caught."""

    for role in ("core", "edge", "route-reflector"):
        zero_baseline = evaluate_device(
            {"platform": "cisco_xr"}, _device("D9", role=role, isis_adjacencies=0)
        )
        assert "suspicious_baseline" in {f["rule"] for f in zero_baseline["findings"]}, role


def test_suspicious_baseline_catches_pe1s_nonzero_case_the_old_rule_missed():
    """B-465/OBS-146: PE1's recorded baseline was ``isis_adjacencies: 1``, role
    "edge". `_isis_isolated` only requires ">= 1", so `1` satisfies it and a
    role invariant reused from there would still miss this -- which is
    exactly what the first (narrow, value-only) version of this rule did. The
    fix is a role-specific floor (`ROLE_MIN_ISIS_ADJACENCIES`): every role in
    this fabric is dual-homed at minimum, so `1` is below the floor for
    "edge" and must fire even though it is not zero."""

    pe1_shaped = evaluate_device(
        {"platform": "cisco_xr"}, _device("PE1", role="edge", isis_adjacencies=1)
    )
    findings = {f["rule"]: f for f in pe1_shaped["findings"]}
    assert "suspicious_baseline" in findings
    finding = findings["suspicious_baseline"]
    assert finding["severity"] == "warning"
    assert finding["actual"] == 1
    # The finding must say why: which role, what was expected, what is
    # recorded -- a bare "suspicious" is not actionable.
    assert "edge" in finding["message"]
    assert "1" in finding["message"]
    assert "2" in finding["message"]


def test_suspicious_baseline_does_not_fire_on_a_correct_baseline():
    """Positive control: the rule must not fire on everything. A baseline at
    or above its role's floor -- this fabric's real, re-derived values,
    P-routers at 5 and edge/route-reflector devices at 2 -- is not
    suspicious."""

    for role, value in (("core", 5), ("edge", 2), ("route-reflector", 2)):
        verdict = evaluate_device(
            {"platform": "cisco_xr"}, _device("D9", role=role, isis_adjacencies=value)
        )
        assert "suspicious_baseline" not in {f["rule"] for f in verdict["findings"]}, (role, value)


def test_suspicious_baseline_does_not_fire_on_a_missing_baseline():
    """Absence is never zero: a device with no recorded isis_adjacencies
    baseline at all has nothing established yet to be suspicious of -- that
    is a different fact from a baseline recorded as 0, and must not fire."""

    no_baseline = evaluate_device({"platform": "cisco_xr"}, _device("PE9"))
    assert "suspicious_baseline" not in {f["rule"] for f in no_baseline["findings"]}


# --------------------------------------------------------------------------- #
# "unevaluated": a failed/unparsed intent must never look healthy. Unlike
# "unsupported" (below), these genuinely raise severity -- see the
# device_unreachable / intent_collection_failed tests further down for the
# severity consequences this drives.
# --------------------------------------------------------------------------- #


def test_errored_intent_is_unevaluated_and_raises_warning_not_ok():
    """If isis errored while every other intent is healthy, isis_isolated must
    not fire "ok" (zero records would look identical to a real isolation) --
    it must be listed as unevaluated, no isis-dependent rule may produce a
    finding, and (this is the point of the fix) the missing assessment itself
    must raise severity rather than reading as health."""

    device = _device("PE9", isis_adjacencies=1)
    evidence = _healthy_evidence(isis=_unavailable_section("error"))

    verdict = evaluate_device(evidence, device)

    assert "isis" in verdict["unevaluated"]
    isis_rules = {"isis_isolated", "isis_adjacency_not_up", "isis_adjacency_count_drift"}
    assert isis_rules.isdisjoint({f["rule"] for f in verdict["findings"]})
    failed = [f for f in verdict["findings"] if f["rule"] == "intent_collection_failed"]
    assert [f["intent"] for f in failed] == ["isis"]
    assert verdict["severity"] == "warning"
    assert verdict["severity"] != "ok"


def test_unsupported_intent_is_benign_not_unevaluated():
    """A platform with no such command (or no parser) is not the device's
    fault -- it lands in "unsupported", not "unevaluated", and must not raise
    severity by itself."""

    device = _device("PE9")
    evidence = _healthy_evidence(sr=_unavailable_section("unsupported"))

    verdict = evaluate_device(evidence, device)

    assert "sr" in verdict["unsupported"]
    assert "sr" not in verdict["unevaluated"]
    assert verdict["severity"] == "ok"


def test_missing_intent_section_is_treated_as_collection_failed():
    """A hand-built or older evidence dict with no key at all for an intent
    must be treated the same as an explicit failure -- never as "zero
    records" -- while the other, healthy intents keep the device from being
    misread as totally unreachable."""

    device = _device("PE9")
    evidence = _healthy_evidence()
    del evidence["bgp"]

    verdict = evaluate_device(evidence, device)

    assert "bgp" in verdict["unevaluated"]
    bgp_rules = {"bgp_session_down", "bgp_process_absent", "bgp_no_prefixes", "bgp_peer_count_drift"}
    assert bgp_rules.isdisjoint({f["rule"] for f in verdict["findings"]})
    failed = [f for f in verdict["findings"] if f["rule"] == "intent_collection_failed"]
    assert [f["intent"] for f in failed] == ["bgp"]
    assert verdict["severity"] == "warning"


# --------------------------------------------------------------------------- #
# The fix itself: a device with nothing collected must never score "ok". This
# is what the whole module exists to prevent -- see the module docstring.
# --------------------------------------------------------------------------- #


def test_every_intent_failing_collection_is_device_unreachable_critical():
    """The dangerous case the fix targets: nothing could be collected at all.
    Previously every rule guarded its own intent, so an empty collection
    produced zero findings and read as "ok" -- a cron gate would pass while
    the device was unreachable. Now it must be the single most severe
    outcome the module can produce."""

    device = _device("PE9")
    evidence = {"platform": "cisco_xr"}  # no section at all for any health intent

    verdict = evaluate_device(evidence, device)

    assert verdict["severity"] == "unreachable"
    assert verdict["severity"] not in {"ok", "critical"}
    assert "device_unreachable" in {f["rule"] for f in verdict["findings"]}


def test_one_failed_intent_among_healthy_others_is_warning_not_unreachable():
    """Only a *total* failure is device_unreachable. One failed intent among
    otherwise-healthy ones is a per-intent warning, not a device-down verdict."""

    device = _device("PE9")
    evidence = _healthy_evidence(interfaces=_unavailable_section("error"))

    verdict = evaluate_device(evidence, device)

    assert verdict["severity"] == "warning"
    failed = [f for f in verdict["findings"] if f["rule"] == "intent_collection_failed"]
    assert [f["intent"] for f in failed] == ["interfaces"]
    assert "device_unreachable" not in {f["rule"] for f in verdict["findings"]}


def test_platform_with_no_parsers_at_all_is_unsupported_not_unevaluated():
    """A platform this tool has no parsers for at all (e.g. a vendor never
    onboarded) must not be misread as an unreachable device: every intent
    lands in "unsupported", "unevaluated" stays empty, and severity is
    untouched by that alone."""

    device = _device("PE9")
    evidence = {
        "platform": "juniper_junos",
        "isis": _unavailable_section("unsupported"),
        "bgp": _unavailable_section("unsupported"),
        "interfaces": _unavailable_section("unsupported"),
        "sr": _unavailable_section("unsupported"),
    }

    verdict = evaluate_device(evidence, device)

    assert set(verdict["unsupported"]) == {"isis", "bgp", "interfaces", "sr"}
    assert verdict["unevaluated"] == []
    assert verdict["severity"] == "ok"


def test_parse_failed_with_output_present_is_intent_unparsed_warning():
    """Output arrived but the parser could not read it -- distinct from both
    "unsupported" (no parser exists) and "collection_failed" (nothing ran)."""

    device = _device("PE9")
    evidence = _healthy_evidence(sr=_unparsed_section())

    verdict = evaluate_device(evidence, device)

    assert "sr" in verdict["unevaluated"]
    unparsed = [f for f in verdict["findings"] if f["rule"] == "intent_unparsed"]
    assert [f["intent"] for f in unparsed] == ["sr"]
    assert verdict["severity"] == "warning"


def test_exit_code_reports_unreachable_as_actionable_but_not_critical():
    """B-715: a cron/CI gate must fail without calling unreachability a fault."""

    device = _device("PE9")
    evidence = {"platform": "cisco_xr"}

    verdict = evaluate_device(evidence, device)

    assert exit_code_for_severity(verdict["severity"]) == 1


# --------------------------------------------------------------------------- #
# Severity ordering, counts, and the fabric roll-up.
# --------------------------------------------------------------------------- #


def test_severity_rank_keeps_unreachable_distinct_from_confirmed_critical():
    assert severity_rank("ok") < severity_rank("info") < severity_rank("warning")
    assert severity_rank("warning") < severity_rank("unreachable") < severity_rank("critical")


def test_device_severity_is_the_max_of_its_findings():
    device = _device("PE9")
    evidence = _healthy_evidence(
        bgp=_parsed_section(
            [
                {"neighbor": "10.0.0.1", "session_state": "Established", "prefixes_received": 0},  # info
                {"neighbor": "10.0.0.2", "session_state": "Idle"},  # critical
            ],
            {"active": True},
        ),
        interfaces=_parsed_section(
            [{"interface": "Gi0", "admin_state": "up", "line_protocol": "down"}]  # warning
        ),
    )

    verdict = evaluate_device(evidence, device)

    assert verdict["severity"] == "critical"
    assert verdict["counts"] == {"critical": 1, "unreachable": 0, "warning": 1, "info": 1}


def test_evaluate_fabric_rolls_up_to_the_worst_device_severity():
    healthy = _device("OK1")
    broken = _device("BAD1", isis_adjacencies=1)
    evidence_by_device = {
        "OK1": _healthy_evidence(),
        "BAD1": _healthy_evidence(isis=_parsed_section([])),
    }

    result = evaluate_fabric(evidence_by_device, devices=[healthy, broken])

    assert result["devices"]["OK1"]["severity"] == "ok"
    assert result["devices"]["BAD1"]["severity"] == "critical"
    assert result["severity"] == "critical"


def test_evaluate_fabric_skips_evidence_for_devices_not_in_the_given_inventory():
    evidence_by_device = {"GHOST": {"platform": "cisco_xr"}}

    result = evaluate_fabric(evidence_by_device, devices=[_device("PE9")])

    assert result["devices"] == {}
    assert result["severity"] == "ok"


def test_exit_code_for_severity_maps_to_ci_gate_levels():
    assert exit_code_for_severity("ok") == 0
    assert exit_code_for_severity("info") == 0
    assert exit_code_for_severity("warning") == 1
    assert exit_code_for_severity("unreachable") == 1
    assert exit_code_for_severity("critical") == 2
