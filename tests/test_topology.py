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

from agent_nettools import parsers, topology
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


# --------------------------------------------------------------------------- #
# B-435 -- LLDP device IDs resolve to inventory names before comparison.
#
# These four tests asserted the *defect*. At t0 three devices ran configured
# hostnames differing from their labels (P1 = LEAF05_DHCP_SERVER, P3 =
# Lab-leaf01, PE4 = SDWAN-Edge01), and the finders compared LLDP's
# device-reported names against inventory labels -- so both ends saying the same
# thing read as a disagreement, and three of this fabric's own devices read as
# foreign. The tests were written from the same premise as the code and agreed
# with it (§0.13, the tests face).
#
# The positive cases are now synthetic, deliberately. The real fixtures no
# longer contain either anomaly, and a fix that removes the only coverage of
# behaviour that is still correct is its own defect shape -- so a genuine
# disagreement and a genuine unknown are constructed here rather than lost.
# --------------------------------------------------------------------------- #


def test_the_p1_p2_link_was_never_a_disagreement(monkeypatch):
    """OBS-103, now enforced rather than described.

    P1 reports Gi0/0/0/0 facing P2; P2 reports that same port facing
    ``LEAF05_DHCP_SERVER``, which *is* P1. Two spellings, one statement.
    """

    evidence = _evidence_by_device(monkeypatch)

    assert find_lldp_disagreements(evidence) == []


def test_the_three_hostnames_are_this_fabric_s_own_devices(monkeypatch):
    """They were reported as foreign for the life of the project."""

    evidence = _evidence_by_device(monkeypatch)

    assert find_neighbors_not_in_inventory(evidence) == {}


def test_the_hostname_map_carries_both_spellings(monkeypatch):
    evidence = _evidence_by_device(monkeypatch)

    mapping = topology.hostname_map(evidence)

    assert topology.resolve_device("LEAF05_DHCP_SERVER", mapping) == "P1"
    assert topology.resolve_device("Lab-leaf01", mapping) == "P3"
    assert topology.resolve_device("SDWAN-Edge01", mapping) == "PE4"
    assert topology.resolve_device("P1", mapping) == "P1"
    assert topology.resolve_device("lab-LEAF01", mapping) == "P3"  # case-insensitive
    assert topology.resolve_device("NotAThing", mapping) is None


def _fabricated(records_by_device, hostnames=None):
    """A minimal two-device evidence set with hand-written LLDP records."""

    hostnames = hostnames or {}
    out = {}
    for name, records in records_by_device.items():
        out[name] = {
            "facts": {"data": {"parse_status": parsers.PARSE_OK,
                               "parsed": {"meta": {"hostname": hostnames.get(name, name)},
                                          "records": []}}},
            "lldp": {"data": {"parse_status": parsers.PARSE_OK,
                              "parsed": {"meta": {}, "records": records}}},
            "isis": {"data": {"parse_status": parsers.PARSE_OK,
                              "parsed": {"meta": {}, "records": [{"x": 1}]}}},
        }
    return out


def test_a_genuine_disagreement_is_still_detected():
    """The companion. Without it the finder could return [] always and pass.

    A really is cabled to B's Gi0/0/0/1, and B reports that port facing a third
    device. Neither name needs resolving -- this is a wiring disagreement, which
    is what the class was always meant to mean.
    """

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B",
               "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "C",
               "neighbor_interface": "Gi0/0/0/9"}],
    })

    disagreements = find_lldp_disagreements(evidence)

    assert len(disagreements) == 1
    assert disagreements[0]["device"] == "A"
    assert disagreements[0]["claims_neighbor"] == "B"
    assert disagreements[0]["neighbor_actually_reports"] == [
        {"neighbor": "C", "local_interface": "Gi0/0/0/1"}
    ]


def test_a_disagreement_survives_a_renamed_device():
    """Resolution must not paper over a real disagreement.

    B is configured ``bee`` and A names it correctly, so the *identity*
    resolves -- and B still reports that port facing something else. The link
    is genuinely inconsistent and must still be reported.
    """

    evidence = _fabricated(
        {
            "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "bee",
                   "neighbor_interface": "Gi0/0/0/1"}],
            "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "C",
                   "neighbor_interface": "Gi0/0/0/9"}],
        },
        hostnames={"B": "bee"},
    )

    disagreements = find_lldp_disagreements(evidence)

    assert len(disagreements) == 1
    assert disagreements[0]["claims_neighbor"] == "B"
    assert disagreements[0]["claims_neighbor_as_reported"] == "bee"


def test_a_genuinely_unknown_neighbour_is_still_reported():
    """The companion for the other class."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "some-switch",
               "neighbor_interface": "Gi1/0/1"}],
    })

    unknown = find_neighbors_not_in_inventory(evidence)

    assert set(unknown) == {"some-switch"}
    assert unknown["some-switch"] == ["A:Gi0/0/0/0"]


def test_a_device_whose_facts_did_not_parse_is_not_assumed_aligned():
    """The absence rule, applied here.

    B's ``facts`` failed, so its configured hostname is unknown. A peer naming
    ``bee`` cannot be resolved and stays *unknown* rather than being assumed to
    be B. Guessing would manufacture exactly the agreement this function exists
    to stop assuming.
    """

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "bee",
               "neighbor_interface": "Gi0/0/0/1"}],
        "B": [],
    })
    evidence["B"]["facts"]["data"]["parse_status"] = parsers.PARSE_FAILED

    assert topology.configured_hostname(evidence["B"]) is None
    assert set(find_neighbors_not_in_inventory(evidence)) == {"bee"}


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

    # B-435: two of the classes are empty on this fabric now, and that is the
    # correct answer -- both were naming artefacts. The remaining anomaly is
    # real and is the one that always mattered.
    assert report["lldp_disagreements"] == []
    assert report["neighbors_not_in_inventory"] == {}
    zero_devices = {entry["device"] for entry in report["zero_adjacency_devices"]}
    assert zero_devices == {"PE2", "PE4"}

    text = format_anomaly_report(report)
    assert "PE2" in text and "PE4" in text
    # The report still renders when a class is empty, rather than omitting the
    # heading -- an absent section reads as "not checked", not "nothing found".
    assert "LLDP neighbors not in inventory (0)" in text
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
