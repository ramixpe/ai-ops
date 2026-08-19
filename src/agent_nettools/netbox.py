"""Project parsed device evidence into NetBox-shaped inventory records, and
(optionally) push them into NetBox over its REST API.

**NetBox is DERIVED, never authored** (docs/design/stage-2-architecture.md
§4, "NetBox inventory -- DECIDED, build now": *"a simple collector script
reads the network and feeds NetBox (the same derive-don't-author rule as
neo4j: NetBox is populated from parsed device evidence, not hand-maintained,
so it cannot become a second source of truth that drifts)"*). This module is
that collector, and it is :mod:`graph`'s sibling -- read that module's
docstring first; the reasoning is not repeated here, only the parts that
differ.

Two authored sources of topology that can disagree is OBS-103's shape
exactly: P1 ran the configured hostname ``LEAF05_DHCP_SERVER`` for the life
of the project, and comparing LLDP's device-reported name against a
hand-typed label read as fabric-wide drift that never existed. If NetBox and
the evidence it was built from ever disagree, the evidence wins and NetBox is
rebuilt from it -- there is no path in this module that edits a NetBox
record by hand.

Two layers, deliberately kept apart, the same split :mod:`graph` uses:

- :func:`build_records` -- **pure**. Evidence in, a :class:`NetBoxRecords`
  out. No HTTP, no import of ``pynetbox`` anywhere in its call path. Testable
  against committed fixtures with zero lab access -- this is the half worth
  testing hard, and per BUILD-PLAN.md's instruction to this task, a complete
  deliverable on its own.
- :func:`describe_writes` -- **pure**. ``NetBoxRecords`` in, the exact set of
  upsert operations :func:`write_records` would attempt out, as plain data.
  No HTTP either. This is what makes dry-run first class: a caller sees
  precisely what would be written with no NetBox reachable, no ``pynetbox``
  installed, and no credentials set.
- :func:`write_records` -- **thin**. Takes a :class:`NetBoxRecords` already
  built and pushes it into NetBox. Imports ``pynetbox`` lazily, inside the
  function body, so a caller that only ever calls :func:`build_records` or
  :func:`describe_writes` -- every test in ``tests/test_netbox.py`` but a
  handful -- never needs the optional dependency installed, and importing
  this module at all never requires it either. Defaults to ``dry_run=True``
  and additionally refuses a real write unless
  ``NETTOOLS_NETBOX_WRITE_ENABLED`` is set to a recognized truthy value (see
  :func:`write_enabled`) -- two independent gates, so neither a forgotten
  keyword argument nor a stray import can mutate anything.

Device identity is the inventory device name -- the same stable key
:mod:`topology` resolves every LLDP-reported name back to via
:func:`topology.hostname_map`/:func:`topology.resolve_device` (B-435), reused
here rather than re-derived, exactly as :mod:`graph` reuses it. See
``graph.py``'s module docstring for the full LLDP-hostname-drift story this
protects against.

What is derivable from this fabric's evidence, and what is not
----------------------------------------------------------------
The standard per-device evidence bundle (``facts``/``interfaces``/``bgp``/
``lldp``/``isis``/...) is what :func:`build_records` reads -- the same shape
:func:`graph.build_graph` takes, on purpose: one evidence contract, not a
second one for NetBox to drift against. Inside that bundle:

- **Devices**: name (the evidence dict's own key), ``platform``, the
  configured hostname (``topology.configured_hostname``), a
  ``device_type``/``software_version`` pair read from ``facts``' own parsed
  meta (the ``hardware``/``version``/``flavor`` fields ``parsers.parse_xr_facts``
  already extracts from ``show version`` -- no new command, no new parser),
  and ``bgp_vpnv4_peers`` -- a count of ``Established`` VPNv4 neighbours from
  the ``bgp_vpnv4`` intent, ``None`` when that address family has no BGP
  process. This closes B-504 (see :class:`NetBoxDevice`'s docstring): the
  correction the operator asked to live in NetBox, derived the same way
  every other device fact here is, never hand-typed.
- **Interfaces**: one record per row of ``show interfaces brief`` (the
  ``interfaces`` intent), classified by :mod:`interface_kind` -- the same
  table ``descent.py``/``investigation.py``/``fixtures.py`` already use for
  "what kind of thing is this interface name" (B-431), reused rather than
  re-derived a fourth time.
- **IP addresses**: **device-level only, from the BGP router-id**
  (``bgp``'s parsed ``meta["router_id"]``), asserted as a ``/32`` host
  address. The ``interfaces`` intent (``show interfaces brief``) carries no
  IP data at all -- IOS-XR's brief table has no address column. A richer,
  per-interface address (with a real prefix length, e.g. ``172.16.10.0/31``)
  *is* available in this codebase, from ``network_tools.get_interface`` /
  ``template_parsers.parse_xr_interface``'s ``meta["ip_address"]`` -- but
  that comes from the parameterized ``interface`` *template*
  (``show interfaces <name>``), issued once per interface name, which is a
  materially larger collection than the six fixed per-device intents this
  module and ``graph.py`` both key off. Wiring that in is real, valuable
  future work (see "what I chose not to build" in the task report); v1 keeps
  the same evidence contract ``graph.py`` uses and accepts the coarser
  device-level IP as the honest cost of that choice. The router-id is
  validated as a real IPv4 address before being written (see
  :func:`_looks_like_ipv4`); a router with no active BGP process (this
  fabric's P-routers) contributes no IP address record at all -- never a
  guessed one.
- **Cabling**: derived from ``lldp``, and *only* where **both ends' LLDP
  records mutually agree** -- device A reports device B on interface X
  claiming B's port is Y, and device B's own record independently confirms
  exactly that pairing (the same "mirrors back" check
  ``topology.find_lldp_disagreements`` already makes, reused rather than
  re-derived). This is a **stricter** bar than ``graph.py``'s LLDP edges,
  which are deliberately allowed to be one-sided or disagreeing (an edge is
  a fact about what was observed, disagreement included). A NetBox
  ``Cable`` is a stronger physical claim than a graph edge -- an interface
  can hold only one Cable in NetBox's own data model -- so asserting a
  one-sided or disagreeing LLDP report as a cable risks writing a wrong
  physical link into NetBox that a human then has to notice and untangle by
  hand, exactly the authored-drift risk this whole module exists to avoid.
  A disagreeing pair is reported by ``topology.find_lldp_disagreements``
  elsewhere; it is never turned into a cable here.

What NetBox's own schema needs that evidence cannot supply
-------------------------------------------------------------
NetBox requires every ``Device`` to reference a site, a role, a manufacturer
and a device type -- none of which any ``show`` command in this project's
allowlist reports (a device does not know its own rack location). These are
declared, fixed, deployment-level constants
(:data:`DEFAULT_SITE_SLUG`, :data:`DEFAULT_DEVICE_ROLE_SLUG`,
:data:`DEFAULT_MANUFACTURER_SLUG`) -- the same kind of choice ``graph.py``
makes with :data:`graph.NODE_LABEL`/:data:`graph.RELATIONSHIP_TYPE`: a
scoping decision belonging to *this collector*, not a per-device fact typed
in by a human that could drift from reality. They live only in the writer
layer (:func:`_apply`), never in :class:`NetBoxRecords` itself -- the pure
half stays strictly evidence-derived. If this lab ever spans multiple sites
or device roles, this is the one place to extend, deliberately outside the
derive-don't-author boundary the rest of the module holds to.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from . import interface_kind, parsers, topology
from .interface_kind import canonical

# --------------------------------------------------------------------------- #
# The record model -- plain, frozen, comparable data. No network, no pynetbox.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NetBoxDevice:
    """One device, as it will appear as a NetBox ``dcim.Device``.

    ``device_type``/``software_version`` are read straight from ``facts``'
    own parsed meta (``show version``'s hardware line and version string) --
    real per-device evidence, not a guess. Either may be ``None`` when
    ``facts`` did not parse cleanly or the device did not report that field.

    ``bgp_vpnv4_peers`` closes B-504: ``inventory/lab.yaml`` carried a stale
    comment claiming PE4 has "no BGP process configured at all", derived from
    the *default* address family alone (``show bgp summary`` -- genuinely
    "% BGP instance 'default' not active" on every PE in this fabric, because
    none of them peer in IPv4 unicast). The VPNv4 address family
    (``show bgp vpnv4 unicast summary``, the ``bgp_vpnv4`` intent) is a
    *different* BGP process on the same device and was never checked by that
    claim -- PE4 runs it, with an Established session to RR1. Rather than
    hand-typing the correction into NetBox (which would make NetBox a second
    authored source exactly like the stale note it replaces), this field
    derives the same way ``configured_hostname``/``software_version`` do: a
    count of ``Established`` VPNv4 neighbours, read straight from evidence.
    ``None`` means the same thing it means throughout this codebase --
    "no VPNv4 BGP process observed here" (``meta["active"] is False``), not
    zero; a P-router (P1/P2/P3) is ``None`` on both address families, a PE is
    not. See :func:`_bgp_vpnv4_peers`.
    """

    name: str
    platform: str | None = None
    configured_hostname: str | None = None
    device_type: str | None = None
    software_version: str | None = None
    bgp_vpnv4_peers: int | None = None


@dataclass(frozen=True)
class NetBoxInterface:
    """One interface, as it will appear as a NetBox ``dcim.Interface``.

    ``kind`` is :class:`interface_kind.InterfaceKind`'s value
    (``"physical"``/``"subinterface"``/``"aggregate"``/``"virtual"``/
    ``"management"``/``"unknown"``) -- read by the writer to choose a NetBox
    interface ``type`` slug (see :data:`_INTERFACE_TYPE_BY_KIND`), and kept
    on the record itself so a test or a reviewer can see the classification
    without re-deriving it from ``name``.
    """

    device: str
    name: str
    kind: str
    enabled: bool | None
    mtu: int | None
    bandwidth_kbps: int | None


@dataclass(frozen=True)
class NetBoxIPAddress:
    """One IPv4 host address, associated with a device but **not** an interface.

    ``address`` is CIDR-shaped (``"10.255.0.11/32"``) so it round-trips
    directly through NetBox's IPAM API. ``source`` names where the value came
    from (currently always ``"bgp_router_id"``) so a reviewer -- human or
    writer -- knows exactly what claim is being made and what it is not: this
    is a device-level fact, not an assertion about which interface holds it.
    """

    device: str
    address: str
    source: str = "bgp_router_id"


@dataclass(frozen=True)
class NetBoxCable:
    """One physical link between two devices' interfaces, as it will appear
    as a NetBox ``dcim.Cable``.

    Only ever built from **mutually agreeing** LLDP records -- see the
    module docstring's "Cabling" section. ``device_a``/``device_b`` are
    always ordered lexicographically, the same canonicalisation
    :class:`graph.GraphEdge` uses, so the same physical link collapses to
    exactly one record regardless of iteration order.
    """

    device_a: str
    interface_a: str
    device_b: str
    interface_b: str


@dataclass(frozen=True)
class NetBoxRecords:
    """One fabric-wide projection: every device given evidence, its
    interfaces, its BGP-router-id IP address (when it has an active BGP
    process), and every mutually-confirmed LLDP cable."""

    devices: tuple[NetBoxDevice, ...] = field(default_factory=tuple)
    interfaces: tuple[NetBoxInterface, ...] = field(default_factory=tuple)
    ip_addresses: tuple[NetBoxIPAddress, ...] = field(default_factory=tuple)
    cables: tuple[NetBoxCable, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# build_records -- the pure half.
# --------------------------------------------------------------------------- #


def _clean_section(evidence: dict[str, Any], intent: str) -> dict[str, Any]:
    """The ``parsed`` block for one intent, or ``{}`` if it did not parse cleanly.

    The same "clean parse or nothing" contract :mod:`topology` and
    :mod:`graph` each enforce with their own private copy of this check
    (NB1, mutation-tested below): a section that errored, was unsupported on
    this platform, or failed to parse contributes nothing -- never a guessed
    record built from partial or absent data.
    """

    section = evidence.get(intent, {}) if isinstance(evidence, dict) else {}
    data = section.get("data", {}) if isinstance(section, dict) else {}
    if not isinstance(data, dict) or data.get("parse_status") != parsers.PARSE_OK:
        return {}
    parsed = data.get("parsed")
    return parsed if isinstance(parsed, dict) else {}


def _clean_records(evidence: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    return list(_clean_section(evidence, intent).get("records") or [])


def _clean_meta(evidence: dict[str, Any], intent: str) -> dict[str, Any]:
    meta = _clean_section(evidence, intent).get("meta")
    return dict(meta) if isinstance(meta, dict) else {}


def _looks_like_ipv4(value: Any) -> bool:
    """Whether ``value`` is a real, well-formed IPv4 address.

    Guards :func:`build_records`' one IP-address source (NB2, mutation-tested
    below): a BGP router-id is *usually* dotted-decimal, but this must never
    trust that without checking -- a malformed or missing value must produce
    no IP address record, not a garbage one NetBox would reject or, worse,
    silently store wrong.
    """

    if not isinstance(value, str) or not value.strip():
        return False
    try:
        ipaddress.IPv4Address(value.strip())
    except ValueError:
        return False
    return True


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _device_type_and_version(evidence: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(device_type, software_version)`` from ``facts``' own parsed meta."""

    meta = _clean_meta(evidence, "facts")
    hardware = meta.get("hardware")
    version = meta.get("version")
    flavor = meta.get("flavor")
    software_version = f"{version} {flavor}" if version and flavor else version
    return hardware, software_version


def _device_interfaces(name: str, evidence: dict[str, Any]) -> list[NetBoxInterface]:
    interfaces = []
    for record in _clean_records(evidence, "interfaces"):
        iface_name = record.get("interface")
        if not iface_name:
            continue  # NB3: a row with no interface name names nothing to attach a fact to.
        admin_state = record.get("admin_state")
        interfaces.append(
            NetBoxInterface(
                device=name,
                name=iface_name,
                kind=interface_kind.classify(iface_name).value,
                enabled=(admin_state == "up") if admin_state is not None else None,
                mtu=_int_or_none(record.get("mtu")),
                bandwidth_kbps=_int_or_none(record.get("bandwidth_kbps")),
            )
        )
    return interfaces


def _device_ip_address(name: str, evidence: dict[str, Any]) -> NetBoxIPAddress | None:
    router_id = _clean_meta(evidence, "bgp").get("router_id")
    if not _looks_like_ipv4(router_id):
        return None
    return NetBoxIPAddress(device=name, address=f"{router_id}/32", source="bgp_router_id")


def _bgp_vpnv4_peers(evidence: dict[str, Any]) -> int | None:
    """Count of ``Established`` VPNv4 neighbours from the ``bgp_vpnv4`` intent.

    Mirrors ``topology.derive_device_expected``'s treatment of the *default*
    AF's ``bgp`` intent (``meta["active"] is False`` means "absent, not
    zero"), applied to the VPNv4 AF instead -- the two are separate BGP
    processes on IOS-XR and this fabric's PEs run only the second one (B-504).
    A clean parse with ``active`` unset (the normal, running case) but zero
    ``Established`` rows is a real ``0``, not ``None`` -- a configured peer
    that has not come up is a fact worth keeping, not the same as no process.
    """

    meta = _clean_meta(evidence, "bgp_vpnv4")
    if not meta:
        return None  # NB1: no clean parse at all -- nothing safe to derive.
    if not meta.get("active", True):
        return None  # "% BGP instance 'default' not active" -- no VPNv4 process here.
    return sum(
        1
        for record in _clean_records(evidence, "bgp_vpnv4")
        if record.get("session_state") == "Established"
    )


def _lldp_cables(evidence_by_device: dict[str, dict[str, Any]]) -> tuple[NetBoxCable, ...]:
    """Every LLDP link both ends mutually agree on, keyed by the canonical pair.

    See the module docstring's "Cabling" section: this is deliberately
    stricter than :mod:`graph`'s LLDP edges. Order-independent for the same
    reason ``graph._protocol_edges`` is -- the dict key is the sorted device
    pair, not whichever device the outer loop reaches first.
    """

    mapping = topology.hostname_map(evidence_by_device)
    pairs: dict[tuple[str, str], NetBoxCable] = {}

    for device_name, evidence in evidence_by_device.items():
        for record in _clean_records(evidence, "lldp"):
            neighbor = topology.resolve_device(record.get("neighbor"), mapping)
            if neighbor is None or neighbor == device_name:
                continue  # NB4: foreign or self-referential -- graph.py's G1, same reasoning.
            local_if = record.get("local_interface")
            claimed_neighbor_if = record.get("neighbor_interface")

            mirrors_back = any(
                topology.resolve_device(other.get("neighbor"), mapping) == device_name
                and other.get("local_interface") == claimed_neighbor_if
                and other.get("neighbor_interface") == local_if
                for other in _clean_records(evidence_by_device.get(neighbor, {}), "lldp")
            )
            if not mirrors_back:
                continue  # NB5: one-sided or disagreeing -- never a cable, only a graph edge.

            key = tuple(sorted((device_name, neighbor)))
            if key in pairs:
                continue  # Already recorded from the other end's iteration.
            device_a, device_b = key
            if device_name == device_a:
                iface_a, iface_b = local_if, claimed_neighbor_if
            else:
                iface_a, iface_b = claimed_neighbor_if, local_if
            pairs[key] = NetBoxCable(
                device_a=device_a, interface_a=iface_a, device_b=device_b, interface_b=iface_b
            )

    return tuple(pairs[key] for key in sorted(pairs))


def build_records(evidence_by_device: dict[str, dict[str, Any]]) -> NetBoxRecords:
    """Project one fabric-wide evidence collection into a :class:`NetBoxRecords`.

    Pure: no network access, and no import of ``pynetbox`` anywhere in this
    function's call path. Every device in ``evidence_by_device`` becomes a
    device record -- even one with no clean ``facts`` parse (device_type and
    software_version simply come back ``None``) -- because a device
    genuinely collected from is a real device, not something to omit for
    having incomplete evidence.

    **Idempotent and rebuildable.** Devices are built by sorted iteration
    over ``evidence_by_device`` (sorted by name); each device's interfaces
    are appended in the order the device itself reported them (the parsed
    record order, not any dict iteration order); IP addresses are sorted by
    ``(device, address)``; cables are built via a canonical sorted-pair key
    exactly like ``graph.build_graph``'s edges. So calling this twice on the
    same evidence, or on the same evidence with its dict keys in a different
    order, returns an equal :class:`NetBoxRecords` (frozen dataclasses over
    tuples, so ``==`` is structural). Nothing here accumulates state between
    calls -- there is no cache, no counter, no file written.

    **This is a different idempotency mechanism from ``graph.build_graph``'s,
    deliberately.** ``graph.py``'s neo4j writer deletes-and-recreates its
    whole prior graph in one transaction on every run, because neo4j nodes
    have no natural key of their own to upsert against safely. NetBox is not
    that: a device's ``name`` (unique per site), an interface's
    ``(device, name)`` pair, and an IP address's CIDR string are all real,
    stable natural keys NetBox's own REST API already indexes and can filter
    on. So :func:`write_records` upserts by natural key instead of replacing
    wholesale -- cheaper, and it does not require deleting and recreating
    objects other NetBox users (or a future NetBox MCP, per
    stage-2-architecture.md §4) may be holding references to between runs.
    """

    devices = []
    interfaces: list[NetBoxInterface] = []
    ip_addresses: list[NetBoxIPAddress] = []

    for name, evidence in sorted(evidence_by_device.items()):
        platform = evidence.get("platform") if isinstance(evidence, dict) else None
        device_type, software_version = _device_type_and_version(evidence)
        devices.append(
            NetBoxDevice(
                name=name,
                platform=platform,
                configured_hostname=topology.configured_hostname(evidence),
                device_type=device_type,
                software_version=software_version,
                bgp_vpnv4_peers=_bgp_vpnv4_peers(evidence),
            )
        )
        interfaces.extend(_device_interfaces(name, evidence))
        ip_address = _device_ip_address(name, evidence)
        if ip_address is not None:
            ip_addresses.append(ip_address)

    return NetBoxRecords(
        devices=tuple(devices),
        interfaces=tuple(interfaces),
        ip_addresses=tuple(sorted(ip_addresses, key=lambda ip: (ip.device, ip.address))),
        cables=_lldp_cables(evidence_by_device),
    )


# --------------------------------------------------------------------------- #
# describe_writes / format_plan -- the dry-run half. Still pure: no HTTP, no
# pynetbox import, so a dry run needs no NetBox reachable and no optional
# dependency installed.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NetBoxOperation:
    """One upsert :func:`write_records` would attempt, as plain data.

    ``key`` names the natural-key fields the upsert matches an existing
    NetBox object on; ``fields`` is the full desired field set. Never a verb
    like "create"/"update" -- :func:`describe_writes` runs with no NetBox
    reachable, so it cannot know which of the two a live run would turn out
    to be, only what the *target* state is. That is deliberate: dry-run's
    job is "what would this fabric's inventory look like", not a guess at
    NetBox's current contents.
    """

    resource: str  # "device" | "interface" | "ip_address" | "cable"
    key: dict[str, str]
    fields: dict[str, Any]


def describe_writes(records: NetBoxRecords) -> tuple[NetBoxOperation, ...]:
    """Every upsert :func:`write_records` would attempt for ``records``, in stable order.

    Pure -- no HTTP, no ``pynetbox`` import. This is the whole dry-run
    mechanism: a caller reviews this list (or its rendering,
    :func:`format_plan`) before ever setting ``NETTOOLS_NETBOX_WRITE_ENABLED``.
    """

    operations: list[NetBoxOperation] = []

    for device in records.devices:
        operations.append(
            NetBoxOperation(
                resource="device",
                key={"name": device.name},
                fields={
                    "name": device.name,
                    "platform": device.platform,
                    "configured_hostname": device.configured_hostname,
                    "device_type": device.device_type,
                    "software_version": device.software_version,
                    "bgp_vpnv4_peers": device.bgp_vpnv4_peers,
                },
            )
        )

    for interface in records.interfaces:
        operations.append(
            NetBoxOperation(
                resource="interface",
                key={"device": interface.device, "name": interface.name},
                fields={
                    "kind": interface.kind,
                    "enabled": interface.enabled,
                    "mtu": interface.mtu,
                    "bandwidth_kbps": interface.bandwidth_kbps,
                },
            )
        )

    for ip in records.ip_addresses:
        operations.append(
            NetBoxOperation(
                resource="ip_address",
                key={"address": ip.address},
                fields={"device": ip.device, "source": ip.source},
            )
        )

    for cable in records.cables:
        operations.append(
            NetBoxOperation(
                resource="cable",
                key={
                    "a": f"{cable.device_a}:{cable.interface_a}",
                    "b": f"{cable.device_b}:{cable.interface_b}",
                },
                fields={},
            )
        )

    return tuple(operations)


def format_plan(records: NetBoxRecords) -> str:
    """Render :func:`describe_writes`' plan as human-readable text.

    Grouped by resource, one line per operation, with a leading summary
    count -- what a caller (or a future ``nettools netbox --dry-run`` CLI
    surface, not built by this task) prints to review a run before it
    writes anything.
    """

    operations = describe_writes(records)
    counts = {
        "device": len(records.devices),
        "interface": len(records.interfaces),
        "ip_address": len(records.ip_addresses),
        "cable": len(records.cables),
    }

    lines = [
        "=== NetBox write plan (dry run -- nothing sent) ===",
        f"devices={counts['device']} interfaces={counts['interface']} "
        f"ip_addresses={counts['ip_address']} cables={counts['cable']}",
    ]
    for resource in ("device", "interface", "ip_address", "cable"):
        lines.append(f"\n{resource} ({counts[resource]}):")
        matching = [op for op in operations if op.resource == resource]
        if not matching:
            lines.append("  none")
        for op in matching:
            key_text = ", ".join(f"{k}={v}" for k, v in op.key.items())
            lines.append(f"  upsert {resource}: {key_text}")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# write_records -- the thin half. Imports pynetbox lazily; nothing above this
# line in the module does, or ever should.
# --------------------------------------------------------------------------- #

# Fixed, declared, deployment-level constants -- see the module docstring's
# "What NetBox's own schema needs that evidence cannot supply" section. Never
# per-device data, never read from an environment variable that could drift
# device-to-device -- if that ever changes, it is a new field on
# NetBoxDevice, derived from evidence, not a widening of these constants.
DEFAULT_SITE_SLUG = "lab"
DEFAULT_SITE_NAME = "Lab"
DEFAULT_MANUFACTURER_SLUG = "cisco"
DEFAULT_MANUFACTURER_NAME = "Cisco"
DEFAULT_DEVICE_ROLE_SLUG = "network-device"
DEFAULT_DEVICE_ROLE_NAME = "Network Device"
# UNCLASSIFIED_DEVICE_TYPE is used when facts' own hardware line did not
# parse -- NetBox requires a device type FK; "unknown" is honest, a guessed
# model number would not be.
UNCLASSIFIED_DEVICE_TYPE = "unknown"

# NetBox interface `type` slugs, by interface_kind.InterfaceKind.value. This
# evidence set has no SFP/media information at all (`show interfaces brief`
# reports state, not optics), so PHYSICAL deliberately maps to NetBox's
# generic "other" rather than guessing a specific speed/media type (e.g.
# "1000base-t") this fabric's evidence never actually reported.
_INTERFACE_TYPE_BY_KIND: dict[str, str] = {
    "physical": "other",
    "subinterface": "virtual",
    "aggregate": "lag",
    "virtual": "virtual",
    "management": "other",
    "unknown": "other",
}

# Environment variables write_records reads from. No default is offered for
# the URL or the token -- guessing either wrong means silently writing to (or
# reading "success" from) the wrong NetBox, which is worse than refusing to
# run. Mirrors graph.py's NEO4J_* convention (same reasoning; named with the
# ``*_ENV`` suffix this codebase's own convention uses -- see
# ``settings.py``'s module docstring -- so ``tests/test_settings.py``'s
# source scan discovers these, and declaring them in ``settings.SETTINGS``
# stays in sync with what the code actually reads).
NETBOX_URL_ENV = "NETBOX_URL"
NETBOX_TOKEN_ENV = "NETBOX_TOKEN"

# The second, independent gate a real write needs, on top of `dry_run=False`
# -- see the module docstring. Only these exact spellings enable it; every
# other value, including a typo, stays disabled (the same
# `unknown_bool_disables` convention `settings.py` declares for
# NETTOOLS_ENABLE_AGENT/NETTOOLS_MCP_ALLOW_ACTIVE_PROBES, and the same
# literal truthy set `cli.py`'s `_ENABLE_AGENT_TRUTHY` uses) -- a setting
# whose whole reason for existing is to keep something off by default must
# not reopen on a typo.
NETBOX_WRITE_ENABLED_ENV = "NETTOOLS_NETBOX_WRITE_ENABLED"
_WRITE_ENABLED_TRUTHY = frozenset({"1", "true", "yes", "on"})


def write_enabled() -> bool:
    """Whether ``NETTOOLS_NETBOX_WRITE_ENABLED`` is set to a recognized truthy value.

    Exposed as a function (not just an internal check) so a caller -- a
    future CLI surface, a test -- can ask the same question
    :func:`write_records` asks internally, without duplicating the env var
    name or its truthy spellings.
    """

    return os.environ.get(NETBOX_WRITE_ENABLED_ENV, "0").strip().lower() in _WRITE_ENABLED_TRUTHY


@dataclass(frozen=True)
class NetBoxWriteResult:
    """What :func:`write_records` did (or, on a dry run, would do)."""

    dry_run: bool
    applied: bool
    operations: tuple[NetBoxOperation, ...]
    counts: dict[str, int]


def _counts(records: NetBoxRecords) -> dict[str, int]:
    return {
        "device": len(records.devices),
        "interface": len(records.interfaces),
        "ip_address": len(records.ip_addresses),
        "cable": len(records.cables),
    }


def _get_or_create(
    endpoint: Any,
    key: dict[str, Any],
    fields: dict[str, Any],
    create_key: dict[str, Any] | None = None,
) -> Any:
    """Upsert-by-natural-key against one pynetbox endpoint.

    ``key`` is looked up with ``endpoint.get(**key)`` *alone* -- never mixed
    with ``fields`` -- and this split is load-bearing (NB7, mutation-tested
    below): if a non-key field's *value* changed since the last run (an
    interface flipping ``enabled``, a device's ``software_version`` bumping
    after an upgrade), filtering the lookup on that field too would make the
    old record stop matching its own new value and this would silently
    create a second object next to it instead of updating the first --
    exactly the duplication "idempotent" promises there is none of. An
    existing match found by ``key`` alone has **every** field in ``fields``
    applied to it (pynetbox's ``Record.update()``, itself a ``PATCH``); no
    match creates one with both ``key`` and ``fields`` merged. Either way the
    object that now exists in NetBox is returned.
    """

    existing = endpoint.get(**key)
    if existing is not None:
        existing.update(fields)
        return existing
    # `create_key` exists because NetBox's FILTER syntax and its WRITE syntax
    # disagree about related objects: `get(device="P1")` is a valid lookup,
    # while `create(device="P1")` is rejected with
    # `Related objects must be referenced by numeric ID`. The natural key we
    # search by is therefore not always the payload we may create with, and
    # collapsing the two is what shipped three separate 400s on the first real
    # write (platform, then interface.device -- 2026-08-19, OBS-193).
    return endpoint.create(**{**(create_key if create_key is not None else key), **fields})


def _apply(client: Any, records: NetBoxRecords) -> dict[str, int]:
    """Upsert ``records`` into a real (or fake, for tests) pynetbox client.

    Not independently mutation-tested against a live NetBox as part of this
    task -- see the task report's "what I chose not to build" for why (no
    confirmed API token was available in this environment). Exercised here
    only against the fake client the test suite builds, the same
    ``driver_factory=`` seam convention ``graph.write_graph`` uses.
    """

    counts = {"device": 0, "interface": 0, "ip_address": 0, "cable": 0}

    site = _get_or_create(
        client.dcim.sites, {"slug": DEFAULT_SITE_SLUG}, {"name": DEFAULT_SITE_NAME}
    )
    manufacturer = _get_or_create(
        client.dcim.manufacturers,
        {"slug": DEFAULT_MANUFACTURER_SLUG},
        {"name": DEFAULT_MANUFACTURER_NAME},
    )
    role = _get_or_create(
        client.dcim.device_roles,
        {"slug": DEFAULT_DEVICE_ROLE_SLUG},
        {"name": DEFAULT_DEVICE_ROLE_NAME},
    )

    device_type_cache: dict[str, Any] = {}
    interface_cache: dict[tuple[str, str], Any] = {}
    # NetBox resolves related objects by numeric ID or by an attribute dict --
    # never by a bare name. `site`, `role` and `device_type` above are already
    # resolved through `_get_or_create`; `platform` was not, and was sent as the
    # raw string "cisco_xr", which a live NetBox rejects with
    # `400 ... Received an unrecognized value: cisco_xr`. The fake client the
    # test suite builds accepts any value, so this shape was never exercised
    # until the first real write (2026-08-19, OBS-193). Same get-or-create
    # treatment as its three siblings, cached the same way.
    platform_cache: dict[str, Any] = {}
    device_cache: dict[str, Any] = {}

    for device in records.devices:
        type_name = device.device_type or UNCLASSIFIED_DEVICE_TYPE
        device_type = device_type_cache.get(type_name)
        if device_type is None:
            device_type = _get_or_create(
                client.dcim.device_types,
                {"slug": _slugify(type_name)},
                {"model": type_name, "manufacturer": manufacturer.id},
            )
            device_type_cache[type_name] = device_type

        platform_id = None
        if device.platform:
            platform = platform_cache.get(device.platform)
            if platform is None:
                platform = _get_or_create(
                    client.dcim.platforms,
                    {"slug": _slugify(device.platform)},
                    {"name": device.platform},
                )
                platform_cache[device.platform] = platform
            platform_id = platform.id

        device_obj = _get_or_create(
            client.dcim.devices,
            {"name": device.name},
            {
                "site": site.id,
                "role": role.id,
                "device_type": device_type.id,
                "platform": platform_id,
                "custom_fields": {
                    "configured_hostname": device.configured_hostname,
                    "software_version": device.software_version,
                    "bgp_vpnv4_peers": device.bgp_vpnv4_peers,
                },
            },
        )
        device_cache[device.name] = device_obj
        counts["device"] += 1

    for interface in records.interfaces:
        parent = device_cache.get(interface.device)
        if parent is None:
            # NB6's sibling: an interface whose device this run never upserted
            # cannot be created, and guessing an ID would attach it to whatever
            # happened to hold that number.
            continue
        obj = _get_or_create(
            client.dcim.interfaces,
            {"device": interface.device, "name": interface.name},
            {
                "type": _INTERFACE_TYPE_BY_KIND.get(interface.kind, "other"),
                "enabled": interface.enabled,
                "mtu": interface.mtu,
            },
            create_key={"device": parent.id, "name": interface.name},
        )
        # Keyed by CANONICAL name. The interface records come from
        # `show interfaces brief` and spell it `Gi0/0/0/0`; LLDP spells the same
        # port `GigabitEthernet0/0/0/0`, so an exact-match key missed on both
        # ends of every cable and the NB6 guard below skipped all of them --
        # silently, because "an interface this run never upserted" is a correct
        # guard firing on a false premise (2026-08-19, OBS-193; third recurrence
        # of the long-vs-short mismatch, after OBS-178). `canonical` is valid as
        # a key here precisely because the tuple is scoped to one device, which
        # is the condition its own docstring sets.
        interface_cache[(interface.device, canonical(interface.name))] = obj
        counts["interface"] += 1

    for ip in records.ip_addresses:
        _get_or_create(
            client.ipam.ip_addresses,
            {"address": ip.address},
            {"description": f"{ip.device} -- {ip.source} (interface unknown from parsed evidence)"},
        )
        counts["ip_address"] += 1

    for cable in records.cables:
        end_a = interface_cache.get((cable.device_a, canonical(cable.interface_a)))
        end_b = interface_cache.get((cable.device_b, canonical(cable.interface_b)))
        if end_a is None or end_b is None:
            continue  # NB6: an interface this run never upserted cannot be cabled.
        if getattr(end_a, "cable", None) or getattr(end_b, "cable", None):
            continue  # Already cabled -- NetBox allows one Cable per termination; leave it.
        client.dcim.cables.create(
            a_terminations=[{"object_type": "dcim.interface", "object_id": end_a.id}],
            b_terminations=[{"object_type": "dcim.interface", "object_id": end_b.id}],
        )
        counts["cable"] += 1

    return counts


def _slugify(text: str) -> str:
    """A conservative NetBox-safe slug: lowercase, non-alphanumerics to hyphens."""

    out = []
    previous_hyphen = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            previous_hyphen = False
        elif not previous_hyphen:
            out.append("-")
            previous_hyphen = True
    return "".join(out).strip("-") or UNCLASSIFIED_DEVICE_TYPE


def write_records(
    records: NetBoxRecords,
    *,
    dry_run: bool = True,
    url: str | None = None,
    token: str | None = None,
    client_factory: Callable[[str, str], Any] | None = None,
) -> NetBoxWriteResult:
    """Upsert ``records`` into NetBox, or -- by default -- describe what that would do.

    ``dry_run=True`` (the default) never touches the network and never
    imports ``pynetbox``: it returns the same plan :func:`describe_writes`
    computes, wrapped with ``applied=False``. This is the first gate --
    importing this module, or calling this function with no arguments beyond
    ``records``, can never mutate anything.

    A real write additionally requires ``NETTOOLS_NETBOX_WRITE_ENABLED`` to
    be set to a recognized truthy value (:func:`write_enabled`) -- the second,
    independent gate. ``dry_run=False`` alone is not enough; a caller has to
    clear both. Credentials are read from ``NETBOX_URL``/``NETBOX_TOKEN``
    when not passed explicitly, and only from there -- never a hardcoded
    fallback, never logged, never included in any exception message this
    function raises (mirroring ``graph.write_graph``'s G4: a missing
    variable is named, its value never is).

    ``client_factory``, if given, replaces ``pynetbox.api`` -- the seam the
    test suite uses to exercise this function's upsert logic against a fake
    client with **no pynetbox package installed and no NetBox reachable**
    (the same ``driver_factory=`` convention ``graph.write_graph`` already
    uses for neo4j). Leave it unset to talk to a real NetBox.
    """

    operations = describe_writes(records)
    counts = _counts(records)

    if dry_run:
        return NetBoxWriteResult(dry_run=True, applied=False, operations=operations, counts=counts)

    if not write_enabled():
        raise RuntimeError(
            f"write_records(dry_run=False) refused: {NETBOX_WRITE_ENABLED_ENV} is not set to a "
            "recognized truthy value (1/true/yes/on). This is the deliberate second gate on "
            "top of dry_run=False -- set it explicitly to allow a real write."
        )

    resolved_url = url or os.environ.get(NETBOX_URL_ENV)
    resolved_token = token or os.environ.get(NETBOX_TOKEN_ENV)
    missing = [
        name
        for name, value in ((NETBOX_URL_ENV, resolved_url), (NETBOX_TOKEN_ENV, resolved_token))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "write_records needs NetBox credentials; missing environment variable(s): "
            + ", ".join(missing)
        )

    if client_factory is not None:
        make_client = client_factory
    else:
        # Imported here, not at module scope: this is the only line in the
        # module that touches the `pynetbox` package, so the pure half
        # (build_records, describe_writes, format_plan, and everything they
        # call) never requires the optional `netbox` extra to be installed.
        import pynetbox

        def make_client(target_url: str, api_token: str) -> Any:
            return pynetbox.api(target_url, token=api_token)

    client = make_client(resolved_url, resolved_token)
    applied_counts = _apply(client, records)
    return NetBoxWriteResult(dry_run=False, applied=True, operations=operations, counts=applied_counts)
