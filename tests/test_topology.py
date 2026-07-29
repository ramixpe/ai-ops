"""Derived expected-topology counts and the fabric anomaly report.

Uses the committed t0 fixtures (real captured lab output) via
``load_fixture_evidence``, exactly like ``nettools learn-topology
--from-fixtures`` would. The expected values asserted here are independently
derivable by reading the fixture files by eye (see
``tests/fixtures/cisco_xr/<device>/t0/``); this test pins them so a future
change cannot silently alter what the tool reports about a fabric it does not
control the truth of.
"""

from __future__ import annotations

from helpers import set_device_environment

from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.lab import all_devices
from agent_nettools.topology import (
    build_anomaly_report,
    derive_expected,
    find_lldp_disagreements,
    find_neighbors_not_in_inventory,
    find_zero_adjacency_devices,
    format_anomaly_report,
    update_expected_in_yaml,
)


def _evidence_by_device(monkeypatch, label: str = "t0") -> dict[str, dict]:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label=label) for name in all_devices()}


def test_derive_expected_matches_the_fixtures(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    derived = derive_expected(evidence)

    assert derived == {
        "P1": {"isis_adjacencies": 2},
        "P2": {"isis_adjacencies": 4},
        "P3": {"isis_adjacencies": 1},
        "P4": {"isis_adjacencies": 3},
        "PE1": {"isis_adjacencies": 1, "bgp_peers": 1},
        "PE2": {"isis_adjacencies": 0, "bgp_peers": 1},
        "PE3": {"isis_adjacencies": 2, "bgp_peers": 1},
        "PE4": {"isis_adjacencies": 0},
        "RR1": {"isis_adjacencies": 1, "bgp_peers": 4},
    }


def test_devices_with_no_bgp_process_have_no_bgp_peers_key(monkeypatch):
    """P1-P4 and PE4 answer '% BGP instance not active': absent, never zero."""

    evidence = _evidence_by_device(monkeypatch)

    derived = derive_expected(evidence)

    for name in ("P1", "P2", "P3", "P4", "PE4"):
        assert "bgp_peers" not in derived[name]


def test_lldp_disagreement_between_p1_and_p2_is_detected(monkeypatch):
    """P1 claims Gi0/0/0/0 faces P2's Gi0/0/0/0; P2 reports that port facing
    LEAF05_DHCP_SERVER instead. This is a verified fact about the fixture data,
    not a bug to paper over."""

    evidence = _evidence_by_device(monkeypatch)

    disagreements = find_lldp_disagreements(evidence)

    assert len(disagreements) == 1
    entry = disagreements[0]
    assert entry["device"] == "P1"
    assert entry["claims_neighbor"] == "P2"
    assert entry["neighbor_actually_reports"] == [
        {"neighbor": "LEAF05_DHCP_SERVER", "local_interface": "GigabitEthernet0/0/0/0"}
    ]


def test_lldp_agreeing_links_are_not_reported_as_disagreements(monkeypatch):
    """P4<->PE3, P2<->PE1, P2<->PE3, P4<->RR1, and P2<->P4 all mirror cleanly;
    only the P1/P2 link should ever surface."""

    evidence = _evidence_by_device(monkeypatch)

    disagreements = find_lldp_disagreements(evidence)

    devices_involved = {(entry["device"], entry["claims_neighbor"]) for entry in disagreements}
    assert devices_involved == {("P1", "P2")}


def test_neighbors_not_in_inventory_are_all_found(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    unknown = find_neighbors_not_in_inventory(evidence)

    assert set(unknown) == {"Lab-leaf01", "LEAF05_DHCP_SERVER", "SDWAN-Edge01"}
    assert "P1:GigabitEthernet0/0/0/1" in unknown["Lab-leaf01"]
    assert "PE4:GigabitEthernet0/0/0/0" in unknown["Lab-leaf01"]
    assert "P2:GigabitEthernet0/0/0/0" in unknown["LEAF05_DHCP_SERVER"]
    assert "P3:GigabitEthernet0/0/0/0" in unknown["LEAF05_DHCP_SERVER"]
    assert "P3:GigabitEthernet0/0/0/4" in unknown["SDWAN-Edge01"]


def test_zero_adjacency_devices_are_pe2_and_pe4(monkeypatch):
    """PE2 is isolated at the link layer entirely (0/0). PE4 has an LLDP
    neighbor but zero IS-IS adjacencies -- a different failure shape, reported
    with both counts so the two are not conflated."""

    evidence = _evidence_by_device(monkeypatch)

    zero = find_zero_adjacency_devices(evidence)
    by_device = {entry["device"]: entry for entry in zero}

    assert set(by_device) == {"PE2", "PE4"}
    assert by_device["PE2"] == {"device": "PE2", "lldp_neighbors": 0, "isis_adjacencies": 0}
    assert by_device["PE4"] == {"device": "PE4", "lldp_neighbors": 1, "isis_adjacencies": 0}


def test_pe2_has_a_bgp_router_id_despite_zero_adjacencies(monkeypatch):
    """The isolation is a link-layer fact, not a BGP one -- PE2 still has an
    active BGP process with one (idle) peer toward RR1."""

    evidence = _evidence_by_device(monkeypatch)

    derived = derive_expected(evidence)

    assert derived["PE2"]["bgp_peers"] == 1
    assert derived["PE2"]["isis_adjacencies"] == 0


def test_report_surfaces_all_four_anomaly_classes(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    report = build_anomaly_report(evidence)

    assert len(report["lldp_disagreements"]) == 1
    assert set(report["neighbors_not_in_inventory"]) == {
        "Lab-leaf01",
        "LEAF05_DHCP_SERVER",
        "SDWAN-Edge01",
    }
    zero_devices = {entry["device"] for entry in report["zero_adjacency_devices"]}
    assert zero_devices == {"PE2", "PE4"}

    text = format_anomaly_report(report)
    assert "P1" in text and "P2" in text
    assert "Lab-leaf01" in text
    assert "LEAF05_DHCP_SERVER" in text
    assert "SDWAN-Edge01" in text
    assert "PE2" in text and "PE4" in text


def test_update_expected_in_yaml_writes_derived_counts(tmp_path, monkeypatch):
    evidence = _evidence_by_device(monkeypatch)
    derived = derive_expected(evidence)

    source = tmp_path / "source.yaml"
    source.write_text(
        (
            "version: 1\n"
            "defaults:\n"
            "  platform: cisco_xr\n"
            "  credential_group: lab\n"
            "  port: 22\n"
            "credential_groups:\n"
            "  lab:\n"
            "    username_env: DEVICE_USERNAME\n"
            "    password_env: DEVICE_PASSWORD\n"
            "devices:\n"
            "  - name: P1\n"
            "    mgmt_ip: 172.20.250.11\n"
            "    role: core\n"
            "    site: lab\n"
            "    expected:\n"
            "      isis_adjacencies: 999\n"  # stale value, must be overwritten
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.yaml"

    update_expected_in_yaml(source, derived, out_path=out)

    import yaml

    written = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert written["devices"][0]["expected"] == {"isis_adjacencies": 2}
    # The source is untouched: reading from source, writing to out.
    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert original["devices"][0]["expected"] == {"isis_adjacencies": 999}
