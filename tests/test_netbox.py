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


def _fabricated(lldp_by_device=None, interfaces_by_device=None, bgp_by_device=None, hostnames=None):
    lldp_by_device = lldp_by_device or {}
    interfaces_by_device = interfaces_by_device or {}
    bgp_by_device = bgp_by_device or {}
    hostnames = hostnames or {}
    names = set(lldp_by_device) | set(interfaces_by_device) | set(bgp_by_device) | set(hostnames)
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

    try:
        write_records(_sample_records(), dry_run=False, url="http://x", token="y")
    except ModuleNotFoundError as exc:
        assert "pynetbox" in str(exc)
    else:
        raise AssertionError("expected ModuleNotFoundError: pynetbox is not installed in this venv")


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
        self.interfaces = _FakeEndpoint()
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
