"""B-477 -- the fabric judged against itself.

The fixture assertions were MEASURED first, then pinned -- not assumed. The
three labels carry the fabric's real history: healthy is clean, broken carries
PE2's dead-but-configured BGP, t0 additionally carries the three renamed
hostnames (OBS-103).
"""

from __future__ import annotations

import copy

from agent_nettools.audit import run_audit
from agent_nettools.fixtures import load_fixture_evidence

DEVICES = ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"]


def _fabric(label):
    return {d: load_fixture_evidence(d, label=label) for d in DEVICES}


# --------------------------------------------------------------------------- #
# Measured-then-pinned fixture behaviour
# --------------------------------------------------------------------------- #


def test_the_healthy_fabric_audits_clean():
    """The §0.12 companion for the whole module: rules that never pass on the
    known-good fabric would be alarms, not audits."""

    result = run_audit(_fabric("healthy"))

    assert result["severity"] == "ok"
    assert result["findings"] == []


def test_broken_catches_pe2_configured_but_dead():
    result = run_audit(_fabric("broken"))

    assert result["severity"] == "warning"
    rules = {f["rule"] for f in result["findings"]}
    assert rules == {"isolated_but_configured"}
    assert result["findings"][0]["devices"] == ["PE2"]


def test_t0_catches_the_three_renamed_hostnames():
    """OBS-103's three renames, surfaced as info -- benign but named."""

    result = run_audit(_fabric("t0"))

    drift = [f for f in result["findings"] if f["rule"] == "hostname_inventory_drift"]
    assert sorted(f["devices"][0] for f in drift) == ["P1", "P3", "PE4"]
    assert all(f["severity"] == "info" for f in drift)


# --------------------------------------------------------------------------- #
# Synthetic positives -- the healthy fixtures cannot exercise these
# --------------------------------------------------------------------------- #


def test_a_duplicate_router_id_is_critical():
    fabric = _fabric("healthy")
    doctored = copy.deepcopy(fabric)
    # Give PE3 PE1's router-ID. Deep copy: fixtures are cached upstream and a
    # mutated shared dict would poison every later test.
    meta = doctored["PE3"]["bgp"]["data"]["parsed"]["meta"]
    meta["router_id"] = fabric["PE1"]["bgp"]["data"]["parsed"]["meta"]["router_id"]

    result = run_audit(doctored)

    dup = [f for f in result["findings"] if f["rule"] == "duplicate_router_id"]
    assert len(dup) == 1
    assert dup[0]["devices"] == ["PE1", "PE3"]
    assert result["severity"] == "critical"


def test_an_mtu_mismatch_on_a_real_adjacency_fires_once_per_link():
    fabric = copy.deepcopy(_fabric("healthy"))
    # P1's Gi0/0/0/0 faces P2 (a real LLDP adjacency in the fixtures) --
    # doctor one end's MTU.
    for record in fabric["P1"]["interfaces"]["data"]["parsed"]["records"]:
        if record["interface"] == "Gi0/0/0/0":
            record["mtu"] = "9000"

    result = run_audit(fabric)

    mtu = [f for f in result["findings"] if f["rule"] == "mtu_mismatch_on_adjacency"]
    assert len(mtu) == 1, "each link reported once, not once per end"
    assert sorted(mtu[0]["devices"]) == ["P1", "P2"]


def test_a_failed_parse_lands_in_unevaluated_never_in_clean():
    """The absence rule: a device that could not be read contributes neither a
    finding nor a clean bill."""

    fabric = copy.deepcopy(_fabric("healthy"))
    fabric["PE1"]["bgp"]["data"]["parse_status"] = "failed"

    result = run_audit(fabric)

    bgp_unevaluated = [u for u in result["unevaluated"]
                       if u["rule"] in ("duplicate_router_id", "isolated_but_configured",
                                        "local_as_mix")]
    assert bgp_unevaluated
    assert all("PE1" in u["devices"] for u in bgp_unevaluated)


def test_local_as_mix_is_info_not_alarm():
    fabric = copy.deepcopy(_fabric("healthy"))
    fabric["PE1"]["bgp"]["data"]["parsed"]["meta"]["local_as"] = "65001"

    result = run_audit(fabric)

    mix = [f for f in result["findings"] if f["rule"] == "local_as_mix"]
    assert len(mix) == 1 and mix[0]["severity"] == "info"
    assert "65001" in mix[0]["message"]


# --------------------------------------------------------------------------- #
# CLI exit codes
# --------------------------------------------------------------------------- #


def test_cli_exit_codes_follow_the_health_scheme():
    import argparse

    from agent_nettools import cli

    def run(label):
        return cli._cmd_audit(argparse.Namespace(
            from_fixtures=True, label=label, format="json", quiet=True))

    assert run("healthy") == cli.EXIT_OK
    assert run("broken") == cli.EXIT_WARNING
