"""The NetBox inventory collector: the pure projection, dry-run planning, and
the thin writer.

Structured the same way ``tests/test_graph.py`` is, because :mod:`netbox` is
structured the same way ``graph.py`` is: fixture-backed tests against the
real ``healthy``/``t0``/``isis-broken`` captures first (every expected count
and record below is independently readable from the fixture files by eye),
then synthetic guard tests that isolate one behaviour each, then the
dry-run/writer tests, which use a fake client (``client_factory=``) so they
run with **no pynetbox package installed and no NetBox reachable** -- the
same seam convention this repo already uses for netmiko
(``install_fake_netmiko``) and neo4j (``graph.py``'s ``driver_factory=``).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from helpers import set_device_environment

from agent_nettools import parsers
from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.lab import all_devices
from agent_nettools.netbox import (
    NETBOX_TOKEN_ENV,
    NETBOX_URL_ENV,
    NETBOX_WRITE_ENABLED_ENV,
    NetBoxCable,
    NetBoxDevice,
    NetBoxInterface,
    NetBoxIPAddress,
    NetBoxRecords,
    build_records,
    describe_writes,
    format_plan,
    write_enabled,
    write_records,
)

# --------------------------------------------------------------------------- #
# Fixture-backed tests.
# --------------------------------------------------------------------------- #


def _evidence_by_device(monkeypatch, label: str = "healthy") -> dict[str, dict]:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label=label) for name in all_devices()}


def test_every_device_becomes_a_device_record(monkeypatch):
    evidence = _evidence_by_device(monkeypatch, label="healthy")

    records = build_records(evidence)

    assert {d.name for d in records.devices} == set(all_devices())
    # Sorted -- a caller (or a diff between two runs) can rely on the order.
    assert [d.name for d in records.devices] == sorted(all_devices())


def test_device_carries_type_and_version_from_facts(monkeypatch):
    """Cross-checked against tests/fixtures/cisco_xr/PE1/healthy/show-version.txt:
    "cisco XRd-CP-C-01 processor with 256GB of memory" and "Version : 7.11.2",
    flavor "LNT" (from "Cisco IOS XR Software, Version 7.11.2 LNT")."""

    evidence = _evidence_by_device(monkeypatch, label="healthy")

    records = build_records(evidence)

    by_name = {d.name: d for d in records.devices}
    assert by_name["PE1"].platform == "cisco_xr"
    assert by_name["PE1"].configured_hostname == "PE1"
    assert by_name["PE1"].device_type == "cisco XRd-CP-C-01 processor with 256GB of memory"
    assert by_name["PE1"].software_version == "7.11.2 LNT"


def test_interfaces_come_from_the_interfaces_brief_table(monkeypatch):
    """PE1/healthy's `show interfaces brief` (see the fixture file) lists Lo0,
    Lo100, Lo150, Lo200, Nu0, two srte_c tunnels, Mg0/RP0/CPU0/0, three
    GigabitEthernet ports and one subinterface -- 12 rows."""

    evidence = _evidence_by_device(monkeypatch, label="healthy")

    records = build_records(evidence)

    pe1_interfaces = {i.name: i for i in records.interfaces if i.device == "PE1"}
    assert len(pe1_interfaces) == 12
    assert pe1_interfaces["Gi0/0/0/0"].kind == "physical"
    assert pe1_interfaces["Gi0/0/0/0"].enabled is True
    assert pe1_interfaces["Gi0/0/0/0"].mtu == 1514
    assert pe1_interfaces["Gi0/0/0/0"].bandwidth_kbps == 1000000
    assert pe1_interfaces["Lo0"].kind == "virtual"
    assert pe1_interfaces["Gi0/0/0/2.300"].kind == "subinterface"
    # `enabled` reflects `admin_state` (NetBox's own semantics for this
    # field -- administratively up, not the operational line-protocol
    # state), and this row's admin state is "up" even though its LineP
    # State is "down": "Gi0/0/0/2.300  up  down  802.1Q  1514  1000000".
    assert pe1_interfaces["Gi0/0/0/2.300"].enabled is True
    assert pe1_interfaces["Mg0/RP0/CPU0/0"].kind == "management"


def test_ip_addresses_come_only_from_active_bgp_router_id(monkeypatch):
    """P1-P4 are P-routers with no active BGP process (see
    tests/fixtures/cisco_xr/P1/healthy/show-bgp-summary.txt: "% BGP instance
    'default' not active") -- zero IP records for them. PE1/PE2/PE3/PE4/RR1
    all run BGP and get exactly one, matching their own "BGP router
    identifier" line."""

    evidence = _evidence_by_device(monkeypatch, label="healthy")

    records = build_records(evidence)

    by_device = {ip.device: ip for ip in records.ip_addresses}
    assert set(by_device) == {"PE1", "PE2", "PE3", "PE4", "RR1"}
    assert by_device["PE1"].address == "10.255.0.11/32"
    assert by_device["PE1"].source == "bgp_router_id"
    # Sorted -- deterministic, reviewable order.
    assert [ip.device for ip in records.ip_addresses] == sorted(by_device)


def test_p1_p2_is_a_mutually_confirmed_cable(monkeypatch):
    """Cross-checked against the raw fixture text (P1/healthy, PE2/healthy is
    not this pair -- P1/P2): P1's LLDP table names P2 on Gi0/0/0/0 with P2's
    port as Gi0/0/0/0, and P2's own table names P1 back on Gi0/0/0/0 with
    P1's port as Gi0/0/0/0 -- an exact mutual match."""

    evidence = _evidence_by_device(monkeypatch, label="healthy")

    records = build_records(evidence)

    by_pair = {(c.device_a, c.device_b): c for c in records.cables}
    cable = by_pair[("P1", "P2")]
    assert cable.interface_a == "GigabitEthernet0/0/0/0"
    assert cable.interface_b == "GigabitEthernet0/0/0/0"


def test_pe2_isolated_at_t0_is_a_device_and_ip_with_no_cables(monkeypatch):
    """topology.py's own docstring: PE2 is 0/0 at t0/t1 -- isolated at the
    link layer, not absent from the fabric, and still carries a BGP
    router-id. The record set must show the same shape: a device and an IP
    address, zero cables touching it."""

    evidence = _evidence_by_device(monkeypatch, label="t0")

    records = build_records(evidence)

    assert "PE2" in {d.name for d in records.devices}
    assert any(ip.device == "PE2" for ip in records.ip_addresses)
    touching_pe2 = [c for c in records.cables if "PE2" in (c.device_a, c.device_b)]
    assert touching_pe2 == []


def test_bgp_vpnv4_peers_corrects_the_stale_pe4_no_bgp_claim(monkeypatch):
    """B-504: ``inventory/lab.yaml`` carried a stale comment claiming PE4 has
    "no BGP process configured at all". Cross-checked against the raw fixture
    text: PE4/t0's `show-bgp-vpnv4-unicast-summary.txt` shows neighbor
    10.255.0.31 (RR1) Established with 2 prefixes, up 5d07h; RR1/t0's own file
    shows the mirror image -- neighbor 10.255.0.14 (PE4) Established, 2
    prefixes, up 5d07h. Confirmed from both ends, same as the backlog row.

    PE4's *default* address family really is inactive ("% BGP instance
    'default' not active", like every P-router) -- that half of the old
    comment was true. What was false is generalizing that into "no BGP
    process at all": VPNv4 is a second, separate process, and PE4 runs it.
    P1 (a P-router) has neither AF active, so it gets ``None`` on both; PE4
    gets ``None`` on the default AF's ``bgp_peers`` (inventory/lab.yaml, by
    design) and a real, non-``None`` count here."""

    evidence = _evidence_by_device(monkeypatch, label="t0")

    records = build_records(evidence)

    by_name = {d.name: d for d in records.devices}
    assert by_name["PE4"].bgp_vpnv4_peers == 1  # Established to RR1 alone.
    assert by_name["RR1"].bgp_vpnv4_peers == 4  # Established to all four PEs.
    assert by_name["P1"].bgp_vpnv4_peers is None  # No BGP process, either AF.


def test_isis_broken_pe3_p2_cable_matches_graphs_lldp_edge(monkeypatch):
    """B-496's fixture (same pair graph.py's acceptance test uses). P2 and
    PE3 are cabled and mutually confirm each other in LLDP even though
    IS-IS has no adjacency between them -- this collector reads LLDP only,
    so the missing IS-IS adjacency does not affect it at all, which is
    itself worth pinning: NetBox cabling is a physical-layer fact, not a
    protocol-adjacency one."""

    set_device_environment(monkeypatch)
    evidence = {name: load_fixture_evidence(name, label="isis-broken") for name in ("P2", "PE3")}

    records = build_records(evidence)

    assert len(records.cables) == 1
    cable = records.cables[0]
    assert {cable.device_a, cable.device_b} == {"P2", "PE3"}


# --------------------------------------------------------------------------- #
# Guards, isolated with hand-built evidence (same convention as
# tests/test_graph.py's `_fabricated`).
# --------------------------------------------------------------------------- #


def _fabricated(
    lldp_by_device=None,
    interfaces_by_device=None,
    bgp_by_device=None,
    hostnames=None,
    bgp_vpnv4_by_device=None,
):
    lldp_by_device = lldp_by_device or {}
    interfaces_by_device = interfaces_by_device or {}
    bgp_by_device = bgp_by_device or {}
    hostnames = hostnames or {}
    bgp_vpnv4_by_device = bgp_vpnv4_by_device or {}
    names = (
        set(lldp_by_device)
        | set(interfaces_by_device)
        | set(bgp_by_device)
        | set(hostnames)
        | set(bgp_vpnv4_by_device)
    )
    out = {}
    for name in names:
        out[name] = {
            "platform": "cisco_xr",
            "facts": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {"hostname": hostnames.get(name, name)}, "records": []},
                }
            },
            "interfaces": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {}, "records": interfaces_by_device.get(name, [])},
                }
            },
            "lldp": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {}, "records": lldp_by_device.get(name, [])},
                }
            },
            "bgp": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": bgp_by_device.get(name, {}), "records": []},
                }
            },
            "bgp_vpnv4": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": bgp_vpnv4_by_device.get(name, {"meta": {}, "records": []}),
                }
            },
        }
    return out


def test_nb1_an_unparsed_section_contributes_nothing():
    """A section that failed to parse must contribute nothing -- never a
    guessed device_type or interface built from a record that was never
    really read."""

    evidence = _fabricated(interfaces_by_device={"A": [{"interface": "Gi0/0/0/0", "admin_state": "up"}]})
    evidence["A"]["interfaces"]["data"]["parse_status"] = parsers.PARSE_FAILED
    evidence["A"]["facts"]["data"]["parse_status"] = parsers.PARSE_FAILED

    records = build_records(evidence)

    assert records.devices == (NetBoxDevice(name="A", platform="cisco_xr"),)
    assert records.interfaces == ()


def test_nb2_a_malformed_router_id_produces_no_ip_address():
    """Guards `_looks_like_ipv4`: a router-id that is not a real IPv4 address
    (garbage, a hostname, empty) must never become an IP address record."""

    evidence = _fabricated(bgp_by_device={"A": {"router_id": "not-an-ip", "active": True}})

    records = build_records(evidence)

    assert records.ip_addresses == ()


def test_nb2b_a_well_formed_router_id_does_produce_one():
    """The companion to NB2 the vacuity rule (BUILD-PLAN.md §0.12) demands:
    a stub that always returns no IP addresses would also pass NB2 alone."""

    evidence = _fabricated(bgp_by_device={"A": {"router_id": "10.0.0.1", "active": True}})

    records = build_records(evidence)

    assert records.ip_addresses == (NetBoxIPAddress(device="A", address="10.0.0.1/32"),)


def test_bgp_vpnv4_peers_is_none_when_the_af_is_not_active():
    """Mirrors NB2's malformed-input guard, for B-504's field: a device whose
    VPNv4 AF reports IOS-XR's "% BGP instance 'default' not active" (``meta
    ["active"] is False``) gets ``None``, never ``0`` -- the absent/zero
    distinction ``topology.derive_device_expected`` already applies to the
    default AF, applied here to VPNv4 instead."""

    evidence = _fabricated(bgp_vpnv4_by_device={"A": {"meta": {"active": False}, "records": []}})

    records = build_records(evidence)

    assert len(records.devices) == 1
    assert records.devices[0].name == "A"
    assert records.devices[0].bgp_vpnv4_peers is None


def test_bgp_vpnv4_peers_counts_established_neighbors_only():
    """The vacuity companion: a stub that always returns ``None`` would also
    pass the test above alone. Two neighbours, one Established with a real
    prefix count and one still Idle -- only the Established one counts."""

    evidence = _fabricated(
        bgp_vpnv4_by_device={
            "A": {
                "meta": {"router_id": "10.0.0.1", "local_as": "65000"},
                "records": [
                    {"neighbor": "10.0.0.2", "session_state": "Established", "prefixes_received": 2},
                    {"neighbor": "10.0.0.3", "session_state": "Idle"},
                ],
            }
        }
    )

    records = build_records(evidence)

    assert len(records.devices) == 1
    assert records.devices[0].name == "A"
    assert records.devices[0].bgp_vpnv4_peers == 1


def test_nb3_an_interface_row_with_no_name_is_skipped():
    evidence = _fabricated(interfaces_by_device={"A": [{"admin_state": "up"}]})

    records = build_records(evidence)

    assert records.interfaces == ()


def test_nb4_an_unresolvable_lldp_neighbor_is_not_a_phantom_cable():
    """A foreign neighbour (not part of this evidence collection) must never
    become a cable -- topology.find_neighbors_not_in_inventory's class to
    report, not this module's to guess a device for.

    On this particular input the `mirrors_back` check alone already refuses
    it (an unresolvable neighbour's own LLDP table can never be found to
    iterate), so this case does not independently prove the NB4 guard is
    load-bearing -- `test_nb4b_a_self_referential_lldp_record_is_not_a_cable`
    below is the one that does, the same "note what is not independently
    checked here" honesty graph.py's own G1 comment models."""

    evidence = _fabricated(lldp_by_device={
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "some-switch", "neighbor_interface": "Gi1/0/1"}],
    })

    records = build_records(evidence)

    assert records.cables == ()


def test_nb4b_a_self_referential_lldp_record_is_not_a_cable():
    """The half of NB4 that IS independently load-bearing: a device naming
    itself as its own LLDP neighbour, with a second record that happens to
    mirror it exactly (a real shape a corrupted or looped-cable capture could
    produce), must never become a self-cable. Without the `neighbor ==
    device_name` half of the guard, `mirrors_back` alone would be satisfied
    here -- the two records mirror each other's claimed ports exactly -- so
    this genuinely exercises the guard rather than the mirrors_back check
    saving it."""

    evidence = _fabricated(lldp_by_device={
        "A": [
            {"local_interface": "Gi0/0/0/0", "neighbor": "A", "neighbor_interface": "Gi0/0/0/1"},
            {"local_interface": "Gi0/0/0/1", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"},
        ],
    })

    records = build_records(evidence)

    assert records.cables == ()


def test_nb5_a_one_sided_lldp_report_is_not_a_cable():
    """A only sees B; B reports nothing back (or something else). Real,
    honest asymmetric evidence -- a fine graph edge, per graph.py -- but
    NEVER a NetBox cable: an interface can hold only one Cable in NetBox's
    own data model, so a one-sided claim must not risk writing a wrong
    physical link."""

    evidence = _fabricated(lldp_by_device={
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [],
    })

    records = build_records(evidence)

    assert records.cables == ()


def test_nb5b_a_disagreeing_lldp_report_is_not_a_cable():
    """A claims B is on Gi0/0/0/1; B's own record names a *different* port.
    The companion the vacuity rule demands for NB5: a stub that always
    returns no cables would also pass the one-sided case alone, so this
    proves the mutual-agreement check is a real comparison, not a stub."""

    evidence = _fabricated(lldp_by_device={
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/9", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })

    records = build_records(evidence)

    assert records.cables == ()


def test_a_mutually_agreeing_pair_is_a_cable():
    evidence = _fabricated(lldp_by_device={
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })

    records = build_records(evidence)

    assert records.cables == (
        NetBoxCable(device_a="A", interface_a="Gi0/0/0/0", device_b="B", interface_b="Gi0/0/0/1"),
    )


def test_build_records_is_order_independent():
    lldp = {
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    }
    evidence = _fabricated(lldp_by_device=lldp, bgp_by_device={"A": {"router_id": "1.2.3.4", "active": True}})
    reversed_evidence = {name: evidence[name] for name in reversed(list(evidence))}

    assert build_records(evidence) == build_records(reversed_evidence)


def test_build_records_is_idempotent():
    evidence = _fabricated(bgp_by_device={"A": {"router_id": "1.2.3.4", "active": True}})

    first = build_records(evidence)
    second = build_records(evidence)

    assert first == second


# --------------------------------------------------------------------------- #
# describe_writes / format_plan -- the dry-run half. Still pure.
# --------------------------------------------------------------------------- #


def _sample_records() -> NetBoxRecords:
    return NetBoxRecords(
        devices=(NetBoxDevice(name="A", platform="cisco_xr"), NetBoxDevice(name="B", platform="cisco_xr")),
        interfaces=(
            NetBoxInterface(device="A", name="Gi0/0/0/0", kind="physical", enabled=True, mtu=1514, bandwidth_kbps=1000000),
        ),
        ip_addresses=(NetBoxIPAddress(device="A", address="10.0.0.1/32"),),
        cables=(NetBoxCable(device_a="A", interface_a="Gi0/0/0/0", device_b="B", interface_b="Gi0/0/0/1"),),
    )


def test_describe_writes_needs_no_pynetbox_and_no_credentials(monkeypatch):
    for name in (NETBOX_URL_ENV, NETBOX_TOKEN_ENV, NETBOX_WRITE_ENABLED_ENV):
        monkeypatch.delenv(name, raising=False)

    operations = describe_writes(_sample_records())

    resources = [op.resource for op in operations]
    assert resources == ["device", "device", "interface", "ip_address", "cable"]
    assert operations[0].key == {"name": "A"}


def test_format_plan_is_human_readable_and_names_the_operations():
    text = format_plan(_sample_records())

    assert "dry run" in text.lower()
    assert "devices=2 interfaces=1 ip_addresses=1 cables=1" in text
    assert "upsert device: name=A" in text
    assert "upsert interface: device=A, name=Gi0/0/0/0" in text


def test_nb8_the_module_imports_with_no_pynetbox_package_installed():
    """Real proof, not a simulation: this venv genuinely has no `pynetbox`
    package installed (it is an optional extra), so importing the module in
    a fresh subprocess either proves the lazy-import discipline holds or
    fails with exactly the ImportError a module-level `import pynetbox`
    would produce."""

    result = subprocess.run(
        [sys.executable, "-c", "import agent_nettools.netbox"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    result_modules = subprocess.run(
        [sys.executable, "-c", "import agent_nettools.netbox, sys; assert 'pynetbox' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result_modules.returncode == 0, result_modules.stderr


# --------------------------------------------------------------------------- #
# write_records -- the two independent gates, then the fake-client writer.
# --------------------------------------------------------------------------- #


def test_dry_run_is_the_default_and_touches_nothing(monkeypatch):
    for name in (NETBOX_URL_ENV, NETBOX_TOKEN_ENV, NETBOX_WRITE_ENABLED_ENV):
        monkeypatch.delenv(name, raising=False)

    result = write_records(_sample_records())

    assert result.dry_run is True
    assert result.applied is False
    assert result.counts == {"device": 2, "interface": 1, "ip_address": 1, "cable": 1}
    assert len(result.operations) == 5


def test_nb9_real_write_refused_when_gate_env_var_is_unset(monkeypatch):
    """The write-enable gate, checked before credentials or a client are ever
    touched -- a client_factory that would fail the test if reached proves
    the refusal happens first."""

    monkeypatch.delenv(NETBOX_WRITE_ENABLED_ENV, raising=False)

    try:
        write_records(
            _sample_records(),
            dry_run=False,
            url="http://example.invalid",
            token="x",
            client_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")),
        )
    except RuntimeError as exc:
        assert NETBOX_WRITE_ENABLED_ENV in str(exc)
    else:
        raise AssertionError("expected RuntimeError: write gate not enabled")


def test_nb9b_a_typo_in_the_gate_env_var_stays_disabled(monkeypatch):
    """unknown_bool_disables: any spelling other than 1/true/yes/on must stay
    off -- the same footgun-proofing settings.py declares for
    NETTOOLS_ENABLE_AGENT/NETTOOLS_MCP_ALLOW_ACTIVE_PROBES."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "tru")  # typo

    assert write_enabled() is False
    try:
        write_records(_sample_records(), dry_run=False, url="http://x", token="y")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError: typo must not enable the gate")


def test_write_enabled_recognizes_every_declared_truthy_spelling(monkeypatch):
    for spelling in ("1", "true", "yes", "on", "TRUE", "On"):
        monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, spelling)
        assert write_enabled() is True, spelling


def test_nb10_missing_credentials_refuse_rather_than_default(monkeypatch):
    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.delenv(NETBOX_URL_ENV, raising=False)
    monkeypatch.delenv(NETBOX_TOKEN_ENV, raising=False)

    try:
        write_records(
            _sample_records(),
            dry_run=False,
            client_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")),
        )
    except RuntimeError as exc:
        assert NETBOX_URL_ENV in str(exc)
        assert NETBOX_TOKEN_ENV in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing credentials")


def test_credentials_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(NETBOX_URL_ENV, "http://labnet-test:8080")
    monkeypatch.setenv(NETBOX_TOKEN_ENV, "test-only-token")

    captured = {}

    def factory(url, token):
        captured["url"] = url
        captured["token"] = token
        return _FakeClient()

    write_records(NetBoxRecords(), dry_run=False, client_factory=factory)

    assert captured["url"] == "http://labnet-test:8080"
    assert captured["token"] == "test-only-token"


def test_write_records_without_pynetbox_installed_fails_at_the_call_not_the_import(monkeypatch):
    """No `client_factory` given, and no `pynetbox` package installed: the
    ImportError must happen inside `write_records`, never at module import."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")

    # Simulate absence rather than relying on it. This test used to assert that
    # pynetbox "is not installed in this venv", which made it a test of the
    # ENVIRONMENT, not of the code: installing pynetbox to perform a real write
    # turned it into a live connection attempt to host "x" and a DNS failure
    # (2026-08-19, OBS-193). What it means to pin is that the ImportError
    # happens inside the call and not at module import -- true whether or not
    # the package is present.
    monkeypatch.setitem(sys.modules, "pynetbox", None)

    try:
        write_records(_sample_records(), dry_run=False, url="http://x", token="y")
    except (ModuleNotFoundError, ImportError) as exc:
        assert "pynetbox" in str(exc)
    else:
        raise AssertionError("expected an ImportError naming pynetbox")


# --------------------------------------------------------------------------- #
# The fake pynetbox-shaped client -- no pynetbox package, no NetBox reachable.
# --------------------------------------------------------------------------- #


class _FakeRecord:
    def __init__(self, **fields):
        self.cable = None
        self._fields = dict(fields)
        for key, value in fields.items():
            setattr(self, key, value)
        self.id = id(self)

    def update(self, fields):
        self._fields.update(fields)
        for key, value in fields.items():
            setattr(self, key, value)
        return True


class _FakeEndpoint:
    def __init__(self):
        self.records: list[_FakeRecord] = []
        self.get_calls = 0
        self.create_calls = 0

    def get(self, **filters):
        self.get_calls += 1
        for record in self.records:
            if all(getattr(record, key, None) == value for key, value in filters.items()):
                return record
        return None

    def create(self, **fields):
        self.create_calls += 1
        record = _FakeRecord(**fields)
        self.records.append(record)
        return record


class _FakeRelatedEndpoint(_FakeEndpoint):
    """A fake endpoint that models NetBox's asymmetry between FILTER and WRITE.

    Real NetBox accepts `?device=P1` as a filter (it resolves the name) but
    rejects `device="P1"` in a create body, which must carry a numeric ID. The
    original fake accepted a bare name in BOTH directions, so the writer shipped
    creates that a live NetBox answered with `400 Related objects must be
    referenced by numeric ID` -- three separate times, for `platform`, then
    `interface.device` (OBS-193).

    Modelling only the half a fake finds convenient is how a fake stops being a
    test and becomes a second implementation that agrees with you. This one
    records the ID it was created with AND resolves a name filter back through
    the endpoint that owns those IDs, so a lookup by name still finds a record
    created by ID -- which is what the real API does.
    """

    def __init__(self, related_field: str, owner: "_FakeEndpoint"):
        super().__init__()
        self._related_field = related_field
        self._owner = owner

    def get(self, **filters):
        value = filters.get(self._related_field)
        if isinstance(value, str):
            parent = next((r for r in self._owner.records if getattr(r, "name", None) == value), None)
            if parent is not None:
                filters = {**filters, self._related_field: parent.id}
        return super().get(**filters)

    def create(self, **fields):
        value = fields.get(self._related_field)
        if isinstance(value, str):
            raise AssertionError(
                f"{self._related_field}={value!r} is a name, not an ID -- a real "
                "NetBox rejects this create with 400. Resolve it to an ID first."
            )
        return super().create(**fields)


class _FakeCables(_FakeEndpoint):
    def create(self, **fields):
        record = super().create(**fields)
        # Mark both terminations cabled, the way a real NetBox would -- this
        # is what makes a second write_records() run skip re-cabling.
        for termination_key in ("a_terminations", "b_terminations"):
            for termination in fields.get(termination_key, []):
                for endpoint in self._all_interface_records():
                    if endpoint.id == termination["object_id"]:
                        endpoint.cable = record
        return record

    def _all_interface_records(self):
        return self._interfaces_endpoint.records if self._interfaces_endpoint else []


class _Dcim:
    def __init__(self):
        self.sites = _FakeEndpoint()
        self.manufacturers = _FakeEndpoint()
        self.device_roles = _FakeEndpoint()
        self.device_types = _FakeEndpoint()
        self.devices = _FakeEndpoint()
        self.interfaces = _FakeRelatedEndpoint("device", self.devices)
        # Present because the REAL dcim API has it and the writer resolves
        # `platform` through it. Its absence here is what let the writer ship
        # sending a bare "cisco_xr" string that a live NetBox rejects with 400
        # -- the fake accepted any shape, so the shape was never tested
        # (OBS-193). A fake that is missing an endpoint does not fail loudly;
        # it fails by never exercising the code that would have used it.
        self.platforms = _FakeEndpoint()
        self.cables = _FakeCables()
        self.cables._interfaces_endpoint = self.interfaces


class _Ipam:
    def __init__(self):
        self.ip_addresses = _FakeEndpoint()


class _FakeClient:
    def __init__(self):
        self.dcim = _Dcim()
        self.ipam = _Ipam()


def _fake_factory():
    client = _FakeClient()
    return client, (lambda *a, **k: client)


def test_writer_upserts_devices_interfaces_and_ips(monkeypatch):
    """`_sample_records()` declares a cable, but only ONE of its two
    terminating interfaces (A's) is in `records.interfaces` -- B's
    Gi0/0/0/1 never was. NB6 in `_apply`: an interface this run never
    upserted cannot be cabled, so the cable count here is deliberately 0.
    `test_writer_cables_a_mutually_upserted_pair_and_stays_idempotent` below
    covers the case where both ends really were upserted."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(NETBOX_URL_ENV, "http://x")
    monkeypatch.setenv(NETBOX_TOKEN_ENV, "y")
    client, factory = _fake_factory()

    records = _sample_records()
    result = write_records(records, dry_run=False, client_factory=factory)

    assert result.applied is True
    assert result.counts == {"device": 2, "interface": 1, "ip_address": 1, "cable": 0}
    assert {d.name for d in client.dcim.devices.records} == {"A", "B"}
    assert len(client.dcim.interfaces.records) == 1
    assert len(client.ipam.ip_addresses.records) == 1
    assert len(client.dcim.cables.records) == 0


def test_nb11_running_twice_does_not_duplicate_devices(monkeypatch):
    """THE idempotency mutation test. Two devices, two runs: the fake
    client's create_calls for `devices` must be exactly 2 (not 4) -- the
    second run's `.get()` must find what the first run created and update it
    in place, never create a second copy."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(NETBOX_URL_ENV, "http://x")
    monkeypatch.setenv(NETBOX_TOKEN_ENV, "y")
    client, factory = _fake_factory()
    records = _sample_records()

    write_records(records, dry_run=False, client_factory=factory)
    write_records(records, dry_run=False, client_factory=factory)

    assert client.dcim.devices.create_calls == 2
    assert len(client.dcim.devices.records) == 2
    assert client.dcim.interfaces.create_calls == 1
    assert len(client.dcim.interfaces.records) == 1
    assert client.ipam.ip_addresses.create_calls == 1
    assert len(client.ipam.ip_addresses.records) == 1


def test_nb12_a_changed_field_updates_in_place_not_a_duplicate(monkeypatch):
    """The exact defect `_get_or_create`'s key/fields split exists to
    prevent: a device's software_version changes between two runs (an
    upgrade). The second run must update the SAME device object, not create
    a second one filtered out by its own new value."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(NETBOX_URL_ENV, "http://x")
    monkeypatch.setenv(NETBOX_TOKEN_ENV, "y")
    client, factory = _fake_factory()

    before = NetBoxRecords(devices=(NetBoxDevice(name="A", software_version="7.11.1"),))
    after = NetBoxRecords(devices=(NetBoxDevice(name="A", software_version="7.11.2"),))

    write_records(before, dry_run=False, client_factory=factory)
    write_records(after, dry_run=False, client_factory=factory)

    assert len(client.dcim.devices.records) == 1
    assert client.dcim.devices.create_calls == 1


def test_writer_cables_a_mutually_upserted_pair_and_stays_idempotent(monkeypatch):
    """Both interfaces of a cable are upserted this time (unlike the smaller
    _sample_records case above): the cable is created once, and a second run
    does not create a duplicate -- the .cable attribute on each termination
    is what the real NetBox one-cable-per-interface constraint would enforce
    too, so this mirrors it rather than fighting it."""

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(NETBOX_URL_ENV, "http://x")
    monkeypatch.setenv(NETBOX_TOKEN_ENV, "y")
    client, factory = _fake_factory()

    records = NetBoxRecords(
        devices=(NetBoxDevice(name="A"), NetBoxDevice(name="B")),
        interfaces=(
            NetBoxInterface(device="A", name="Gi0/0/0/0", kind="physical", enabled=True, mtu=1500, bandwidth_kbps=1000000),
            NetBoxInterface(device="B", name="Gi0/0/0/1", kind="physical", enabled=True, mtu=1500, bandwidth_kbps=1000000),
        ),
        cables=(NetBoxCable(device_a="A", interface_a="Gi0/0/0/0", device_b="B", interface_b="Gi0/0/0/1"),),
    )

    first = write_records(records, dry_run=False, client_factory=factory)
    second = write_records(records, dry_run=False, client_factory=factory)

    assert first.counts["cable"] == 1
    assert second.counts["cable"] == 0  # already cabled -- see the `.cable` skip in _apply
    assert len(client.dcim.cables.records) == 1


def test_cables_match_when_lldp_and_the_interface_list_spell_the_name_differently(monkeypatch):
    """LLDP says `GigabitEthernet0/0/0/0`; `show interfaces brief` says
    `Gi0/0/0/0`. They are the same port and must cable together.

    Measured against a live NetBox on 2026-08-19: the writer keyed its
    interface cache on the exact string, so every cable lookup missed on BOTH
    ends and the NB6 guard ("an interface this run never upserted cannot be
    cabled") skipped all fifteen -- silently, because the guard is correct and
    only its premise was false. Zero cables looked exactly like a fabric with
    no LLDP. Third recurrence of the long-vs-short mismatch (OBS-178, OBS-193).
    """

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "fake-token-never-used-the-client-is-injected")
    client = _FakeClient()
    records = NetBoxRecords(
        devices=(
            NetBoxDevice(name="PE1", platform="cisco_xr"),
            NetBoxDevice(name="P1", platform="cisco_xr"),
        ),
        interfaces=(
            # short form, as `show interfaces brief` reports it
            NetBoxInterface(device="PE1", name="Gi0/0/0/0", kind="physical", enabled=True, mtu=1514, bandwidth_kbps=1000000),
            NetBoxInterface(device="P1", name="Gi0/0/0/2", kind="physical", enabled=True, mtu=1514, bandwidth_kbps=1000000),
        ),
        ip_addresses=(),
        cables=(
            # long form, as LLDP reports it
            NetBoxCable(
                device_a="PE1", interface_a="GigabitEthernet0/0/0/0",
                device_b="P1", interface_b="GigabitEthernet0/0/0/2",
            ),
        ),
    )

    write_records(records, dry_run=False, client_factory=lambda url, token: client)

    assert client.dcim.cables.create_calls == 1, (
        "the cable was skipped: the interface cache was keyed on the exact "
        "spelling, so the long-form LLDP name never matched the short-form "
        "interface name"
    )


def test_the_cable_match_is_not_merely_matching_everything(monkeypatch):
    """Anti-vacuity companion. The test above would also pass if the lookup had
    been loosened to match any interface on the right device. A cable to an
    interface that genuinely was not upserted must still be skipped.
    """

    monkeypatch.setenv(NETBOX_WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "fake-token-never-used-the-client-is-injected")
    client = _FakeClient()
    records = NetBoxRecords(
        devices=(NetBoxDevice(name="PE1", platform="cisco_xr"),
                 NetBoxDevice(name="P1", platform="cisco_xr")),
        interfaces=(NetBoxInterface(device="PE1", name="Gi0/0/0/0", kind="physical", enabled=True, mtu=1514, bandwidth_kbps=1000000),
                    NetBoxInterface(device="P1", name="Gi0/0/0/2", kind="physical", enabled=True, mtu=1514, bandwidth_kbps=1000000)),
        ip_addresses=(),
        cables=(NetBoxCable(device_a="PE1", interface_a="GigabitEthernet0/0/0/9",
                            device_b="P1", interface_b="GigabitEthernet0/0/0/2"),),
    )

    write_records(records, dry_run=False, client_factory=lambda url, token: client)

    assert client.dcim.cables.create_calls == 0


# --------------------------------------------------------------------------- #
# read_records -- the read half (this task). Stdlib urllib, fetcher= seam
# (the same injection idiom logs_loki.py's `fetcher=` and metrics_prometheus
# .py's own already use) so this suite runs with no NetBox reachable and no
# pynetbox package required.
# --------------------------------------------------------------------------- #

from agent_nettools import netbox  # noqa: E402 -- module-qualified for run_named_read/etc below


def test_unknown_read_query_is_refused_and_the_fetcher_is_never_called(monkeypatch):
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "t")
    called = []

    def fetcher(base_url, token, path):
        called.append(path)
        return {"count": 0, "next": None, "results": []}

    result = netbox.run_named_read("not_a_real_query", fetcher=fetcher)

    assert result["status"] == "error"
    assert "unknown netbox query" in result["errors"][0].lower()
    assert called == [], "an unknown query name must never reach the fetcher"


def test_run_named_read_has_no_parameter_shaped_like_a_raw_query_string():
    import inspect

    params = set(inspect.signature(netbox.run_named_read).parameters)
    assert not (params & {"filter", "query", "cypher", "path", "url"})


def test_missing_credentials_are_refused_not_forwarded_to_the_fetcher(monkeypatch):
    monkeypatch.delenv("NETBOX_URL", raising=False)
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    called = []

    result = netbox.run_named_read(
        "device_inventory", fetcher=lambda *a: called.append(a) or {"results": []}
    )

    assert result["status"] == "error"
    assert "Required environment variable is not set" in result["errors"][0]
    assert "NETBOX_URL" in result["errors"][0] and "NETBOX_TOKEN" in result["errors"][0]
    assert called == []


def test_device_inventory_shapes_the_recorded_fields(monkeypatch):
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "t")

    def fetcher(base_url, token, path):
        assert path == "/api/dcim/devices/"
        return {
            "count": 1,
            "next": None,
            "results": [{
                "name": "PE1",
                "platform": {"name": "cisco_xr"},
                "device_type": {"model": "cisco XRd-CP-C-01"},
                "status": {"value": "active"},
                "custom_fields": {"configured_hostname": "PE1", "software_version": "7.11.2 LNT"},
                "interface_count": 8,
                "description": "",
                "last_updated": "2026-08-19T09:46:57Z",
            }],
        }

    result = netbox.run_named_read("device_inventory", fetcher=fetcher)

    assert result["status"] == "success"
    [record] = result["data"]["parsed"]["records"]
    assert record == {
        "name": "PE1",
        "platform": "cisco_xr",
        "configured_hostname": "PE1",
        "device_type": "cisco XRd-CP-C-01",
        "software_version": "7.11.2 LNT",
        "status": "active",
        "interface_count": 8,
        "description": "",
        "last_updated": "2026-08-19T09:46:57Z",
    }
    assert result["data"]["parsed"]["meta"]["truncated"] is False
    assert result["data"]["parsed"]["meta"]["newest_last_updated"] == "2026-08-19T09:46:57Z"


def test_cable_topology_matches_terminations_to_device_and_interface(monkeypatch):
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "t")

    def fetcher(base_url, token, path):
        assert path == "/api/dcim/cables/"
        return {
            "count": 1,
            "next": None,
            "results": [{
                "status": {"value": "connected"},
                "description": "",
                "last_updated": "2026-08-19T09:54:31Z",
                "a_terminations": [
                    {"object_type": "dcim.interface",
                     "object": {"name": "Gi0/0/0/0", "device": {"name": "P1"}}}
                ],
                "b_terminations": [
                    {"object_type": "dcim.interface",
                     "object": {"name": "Gi0/0/0/0", "device": {"name": "P2"}}}
                ],
            }],
        }

    result = netbox.run_named_read("cable_topology", fetcher=fetcher)

    [record] = result["data"]["parsed"]["records"]
    assert record["device_a"] == "P1" and record["interface_a"] == "Gi0/0/0/0"
    assert record["device_b"] == "P2" and record["interface_b"] == "Gi0/0/0/0"


def test_a_malformed_cable_termination_degrades_to_none_rather_than_crashing(monkeypatch):
    """A NetBox this collector does not own exclusively can hold a cable
    shape this reader was not written against (a breakout, a non-interface
    termination) -- must not raise."""

    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "t")

    def fetcher(base_url, token, path):
        return {
            "count": 1, "next": None,
            "results": [{
                "status": {"value": "connected"}, "description": "",
                "last_updated": "2026-08-19T09:54:31Z",
                "a_terminations": [],  # zero terminations -- not this collector's shape
                "b_terminations": [
                    {"object_type": "dcim.rearport", "object": {"name": "x"}}
                ],  # not a plain interface
            }],
        }

    result = netbox.run_named_read("cable_topology", fetcher=fetcher)

    [record] = result["data"]["parsed"]["records"]
    assert record["device_a"] is None and record["interface_a"] is None
    assert record["device_b"] is None and record["interface_b"] is None


def test_a_non_null_next_is_reported_as_truncated_not_silently_dropped(monkeypatch):
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "t")

    def fetcher(base_url, token, path):
        return {
            "count": 200, "next": "http://netbox.invalid/api/dcim/devices/?offset=1",
            "results": [],
        }

    result = netbox.run_named_read("device_inventory", fetcher=fetcher)

    assert result["data"]["parsed"]["meta"]["truncated"] is True
    assert result["data"]["parsed"]["meta"]["netbox_reported_count"] == 200


def test_an_http_error_status_classifies_through_the_status_entry(monkeypatch):
    monkeypatch.setenv("NETBOX_URL", "http://netbox.invalid")
    monkeypatch.setenv("NETBOX_TOKEN", "bad-token")

    def raising_fetcher(base_url, token, path):
        raise netbox.NetBoxTransportError("netbox returned http status 403")

    result = netbox.run_named_read("device_inventory", fetcher=raising_fetcher)

    assert result["status"] == "error"
    from mcp_server.boundary import sanitize

    sanitized = sanitize(result)
    assert "unclassified" not in sanitized["errors"][0]
    assert "non-success HTTP status" in sanitized["errors"][0]


def test_a_malformed_json_body_is_a_transport_error_not_a_crash():
    # Exercises the real fetcher's JSON/shape guards directly (no network,
    # via a faked `urllib.request.urlopen`): a bytes-decodable but non-JSON
    # body, and a JSON body missing "results".
    class _FakeHTTPResponse:
        def __init__(self, body: bytes):
            self._body = body
            self.status = 200

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import urllib.request

    original_urlopen = urllib.request.urlopen

    def fake_urlopen(request, timeout=None):
        return _FakeHTTPResponse(b"not json")

    try:
        urllib.request.urlopen = fake_urlopen
        with pytest.raises(netbox.NetBoxTransportError, match="not valid json"):
            netbox._http_fetcher("http://netbox.invalid", "t", "/api/dcim/devices/")
    finally:
        urllib.request.urlopen = original_urlopen

    def fake_urlopen_bad_shape(request, timeout=None):
        return _FakeHTTPResponse(b'{"no_results_key": true}')

    try:
        urllib.request.urlopen = fake_urlopen_bad_shape
        with pytest.raises(netbox.NetBoxTransportError, match="expected paginated shape"):
            netbox._http_fetcher("http://netbox.invalid", "t", "/api/dcim/devices/")
    finally:
        urllib.request.urlopen = original_urlopen


def test_the_netbox_read_free_text_field_name_choice_adds_nothing_new_to_free_text_fields():
    """Documents and pins the reuse decision (module docstring, "What is
    (and is not) free text here"): `description` was already a member of
    `model_egress.FREE_TEXT_FIELDS`'s flattened name set from
    `("interface", "description")` -- this module's own read tools add ZERO
    new entries."""

    from agent_nettools import model_egress

    names = frozenset(field for _context, field in model_egress.FREE_TEXT_FIELDS)
    assert "description" in names
    assert not any(
        context in ("device_inventory", "cable_topology")
        for context, _field in model_egress.FREE_TEXT_FIELDS
    )
