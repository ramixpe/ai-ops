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
    """Measured real state (see the phase spec / fixture files), not hypothetical:

    - PE2 and PE4 are the only two devices with zero IS-IS adjacencies, and
      both have a baseline that records that as "expected" -- the fabric is
      broken and the baseline blesses it. Role invariants must still flag it.
    - RR1 has two Idle BGP peers (10.255.0.12, 10.255.0.14); PE2 has one Idle
      peer (10.255.0.31); everything else is Established or absent.
    - PE1 has two admin-up/line-down subinterfaces and one down SR-TE policy;
      PE3 has one admin-up/line-down subinterface. No other device has either.
    """

    evidence = _evidence_by_device(monkeypatch)

    result = evaluate_fabric(evidence)

    severities = {name: verdict["severity"] for name, verdict in result["devices"].items()}
    assert severities == {
        "P1": "ok",
        "P2": "ok",
        "P3": "ok",
        "P4": "ok",
        "PE1": "warning",
        "PE2": "critical",
        "PE3": "warning",
        "PE4": "critical",
        "RR1": "critical",
    }
    assert result["severity"] == "critical"

    # No fixture at t0 fails to parse, so nothing should ever land in
    # "unevaluated" -- a silently-skipped intent would be a distinct bug from
    # a wrong severity.
    for verdict in result["devices"].values():
        assert verdict["unevaluated"] == []


def test_pe2_findings_are_isolation_session_down_and_suspicious_baseline(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE2"], _device_from_inventory("PE2"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"isis_isolated", "bgp_session_down", "suspicious_baseline"}
    down = next(f for f in verdict["findings"] if f["rule"] == "bgp_session_down")
    assert down["subject"] == "10.255.0.31"
    assert down["severity"] == "critical"


def test_pe4_findings_are_isolation_process_absent_and_suspicious_baseline(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE4"], _device_from_inventory("PE4"))

    rules = {finding["rule"] for finding in verdict["findings"]}
    assert rules == {"isis_isolated", "bgp_process_absent", "suspicious_baseline"}


def test_rr1_findings_include_both_idle_sessions(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["RR1"], _device_from_inventory("RR1"))

    down_subjects = {
        f["subject"] for f in verdict["findings"] if f["rule"] == "bgp_session_down"
    }
    assert down_subjects == {"10.255.0.12", "10.255.0.14"}
    assert verdict["severity"] == "critical"


def test_pe1_findings_are_two_line_down_subinterfaces_one_sr_policy_and_info(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    verdict = evaluate_device(evidence["PE1"], _device_from_inventory("PE1"))

    by_rule: dict[str, list[dict]] = {}
    for finding in verdict["findings"]:
        by_rule.setdefault(finding["rule"], []).append(finding)

    assert {f["subject"] for f in by_rule["interface_admin_up_line_down"]} == {
        "Gi0/0/0/2.300",
        "Gi0/0/0/2.400",
    }
    assert {f["subject"] for f in by_rule["sr_policy_down"]} == {"20:10.255.0.13"}
    assert by_rule["bgp_no_prefixes"][0]["severity"] == "info"
    assert verdict["severity"] == "warning"
    assert verdict["counts"] == {"critical": 0, "warning": 3, "info": 1}


def test_core_routers_are_clean(monkeypatch):
    """P1-P4 all have non-zero IS-IS adjacencies matching their baseline, no BGP
    process (fine for role core), and no interface/SR anomalies."""

    evidence = _evidence_by_device(monkeypatch)

    for name in ("P1", "P2", "P3", "P4"):
        verdict = evaluate_device(evidence[name], _device_from_inventory(name))
        assert verdict["severity"] == "ok", (name, verdict["findings"])
        assert verdict["findings"] == []


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


def test_bgp_session_down_uses_numeric_vs_state_word():
    device = _device("PE9")
    evidence = {
        "platform": "cisco_xr",
        "bgp": _parsed_section(
            [
                {"neighbor": "10.0.0.1", "state_pfx_rcd": "5"},
                {"neighbor": "10.0.0.2", "state_pfx_rcd": "Idle"},
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
        bgp=_parsed_section([{"neighbor": "10.0.0.1", "state_pfx_rcd": "0"}], {"active": True}),
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


def test_bgp_peer_count_drift_fires_on_mismatch_and_skips_when_absent():
    evidence = {
        "platform": "cisco_xr",
        "bgp": _parsed_section([{"neighbor": "10.0.0.1", "state_pfx_rcd": "5"}]),
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


def test_suspicious_baseline_only_fires_for_zero_isis_expectation():
    zero_baseline = evaluate_device({"platform": "cisco_xr"}, _device("PE9", isis_adjacencies=0))
    assert "suspicious_baseline" in {f["rule"] for f in zero_baseline["findings"]}

    nonzero_baseline = evaluate_device({"platform": "cisco_xr"}, _device("PE9", isis_adjacencies=1))
    assert "suspicious_baseline" not in {f["rule"] for f in nonzero_baseline["findings"]}

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

    assert verdict["severity"] == "critical"
    assert verdict["severity"] != "ok"
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


def test_exit_code_fails_the_gate_when_every_intent_failed_collection():
    """The whole point of the fix: a cron/CI gate must fail (nonzero exit)
    when a device is unreachable, not pass with exit code 0."""

    device = _device("PE9")
    evidence = {"platform": "cisco_xr"}

    verdict = evaluate_device(evidence, device)

    assert exit_code_for_severity(verdict["severity"]) == 2


# --------------------------------------------------------------------------- #
# Severity ordering, counts, and the fabric roll-up.
# --------------------------------------------------------------------------- #


def test_severity_rank_orders_ok_below_info_below_warning_below_critical():
    assert severity_rank("ok") < severity_rank("info") < severity_rank("warning") < severity_rank(
        "critical"
    )


def test_device_severity_is_the_max_of_its_findings():
    device = _device("PE9")
    evidence = _healthy_evidence(
        bgp=_parsed_section(
            [
                {"neighbor": "10.0.0.1", "state_pfx_rcd": "0"},  # info
                {"neighbor": "10.0.0.2", "state_pfx_rcd": "Idle"},  # critical
            ],
            {"active": True},
        ),
        interfaces=_parsed_section(
            [{"interface": "Gi0", "admin_state": "up", "line_protocol": "down"}]  # warning
        ),
    )

    verdict = evaluate_device(evidence, device)

    assert verdict["severity"] == "critical"
    assert verdict["counts"] == {"critical": 1, "warning": 1, "info": 1}


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
    assert exit_code_for_severity("critical") == 2
