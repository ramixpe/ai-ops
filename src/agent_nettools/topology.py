"""Derive expected per-device topology counts from parsed evidence, and report anomalies.

``nettools learn-topology`` is the only writer of the ``expected:`` blocks in
``inventory/lab.yaml``. Everything in that block is *derived* -- counted from a
real collection, never invented -- because a hand-typed expectation drifts the
moment the fabric changes and nobody notices.

This module also builds an anomaly report over the same evidence.

**Corrected 2026-08-17 (B-435, OBS-103).** This docstring said the fabric's LLDP
data was *"genuinely inconsistent"* and gave two examples. **One of them was not
an inconsistency and neither was the class it belonged to.**

At the ``t0``/``t1`` captures three devices ran configured hostnames that differ
from their inventory labels -- P1 was ``LEAF05_DHCP_SERVER``, P3 ``Lab-leaf01``,
PE4 ``SDWAN-Edge01``. So P1 reporting its Gi0/0/0/0 facing P2, and P2 reporting
that same port facing ``LEAF05_DHCP_SERVER``, are **the same statement**. LLDP
was correct at both ends; the disagreement was between LLDP's device-reported
names and this inventory's labels, and the comparison was reading one against
the other.

:func:`hostname_map` resolves a device-reported name to its inventory name using
the ``facts`` intent's parsed hostname, which was already being collected. The
anomaly classes are unchanged and still worth reporting -- **a genuine LLDP
disagreement and a genuinely unknown neighbour both still exist as concepts**,
and after this they mean what they say.

What remains true of this fabric:

- PE2 reports 0 LLDP neighbours and 0 IS-IS adjacencies while still carrying a
  BGP router-id and one (idle) BGP peer -- isolated at the link layer but not
  absent from BGP.
- The ``healthy`` and ``broken`` captures have hostnames aligned to inventory
  labels, so they exercise the resolved path trivially; ``t0``/``t1`` are the
  labels that exercise it for real, and they are frozen.

Only per-device *counts* are written back to the inventory (see the comment in
``inventory/lab.yaml``). **That rule survives the correction and its reason
changes**: not "link topology cannot be stated truthfully", which is no longer
the case, but that a count survives a naming disagreement a link claim has to
take a side on.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from . import parsers
from .interface_kind import same_interface

# --------------------------------------------------------------------------- #
# Derivation: expected per-device counts, written back to the inventory.
# --------------------------------------------------------------------------- #


def _parsed_records(evidence: dict[str, Any], intent: str) -> list[dict[str, Any]] | None:
    """Return one intent's parsed records, or ``None`` if the parse was not clean."""

    section = evidence.get(intent, {})
    data = section.get("data", {}) if isinstance(section, dict) else {}
    if data.get("parse_status") != parsers.PARSE_OK:
        return None
    return list((data.get("parsed") or {}).get("records") or [])


def _bgp_meta(evidence: dict[str, Any]) -> dict[str, Any] | None:
    data = evidence.get("bgp", {}).get("data", {})
    if data.get("parse_status") != parsers.PARSE_OK:
        return None
    return dict((data.get("parsed") or {}).get("meta") or {})


def derive_device_expected(evidence: dict[str, Any]) -> dict[str, int] | None:
    """Derive one device's ``expected`` block from its evidence, or ``None`` if it cannot be.

    ``isis_adjacencies`` is the count of IS-IS neighbor records. ``bgp_peers``
    is the count of BGP neighbor records, but is *omitted* -- never written as
    zero -- when the device has no active BGP process at all (``meta["active"]
    is False``, IOS-XR's "% BGP instance not active" reply): a missing key
    means "no BGP process runs here", zero would wrongly claim a process that
    simply has no peers.
    """

    isis_records = _parsed_records(evidence, "isis")
    if isis_records is None:
        return None  # No clean IS-IS parse: nothing safe to derive.

    expected: dict[str, int] = {"isis_adjacencies": len(isis_records)}

    bgp_meta = _bgp_meta(evidence)
    if bgp_meta is not None and bgp_meta.get("active", True):
        expected["bgp_peers"] = int(bgp_meta.get("neighbor_count", 0))

    return expected


def derive_expected(evidence_by_device: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Derive ``expected`` blocks for every device with usable evidence."""

    derived: dict[str, dict[str, int]] = {}
    for name, evidence in evidence_by_device.items():
        expected = derive_device_expected(evidence)
        if expected is not None:
            derived[name] = expected
    return derived


# --------------------------------------------------------------------------- #
# Anomaly report: what learn-topology must never paper over.
# --------------------------------------------------------------------------- #


def _lldp_records(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return _parsed_records(evidence, "lldp") or []


def configured_hostname(evidence: dict[str, Any]) -> str | None:
    """The hostname the device reports for itself, or ``None``.

    Read from the ``facts`` intent's parsed meta -- already collected, so this
    costs no extra command. ``None`` when the section errored or did not parse,
    which is deliberately distinct from "the hostname matches the label": a
    device whose `facts` failed must not silently look aligned.
    """

    if not isinstance(evidence, dict):
        return None
    section = evidence.get("facts")
    if not isinstance(section, dict):
        return None
    data = section.get("data")
    if not isinstance(data, dict) or data.get("parse_status") != parsers.PARSE_OK:
        return None
    parsed = data.get("parsed")
    if not isinstance(parsed, dict):
        return None
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None
    hostname = meta.get("hostname")
    return hostname if isinstance(hostname, str) and hostname.strip() else None


def hostname_map(evidence_by_device: dict[str, dict[str, Any]]) -> dict[str, str]:
    """``{name a device calls itself: inventory name}``, for every device.

    Both spellings map to the inventory name, so a lookup works whether LLDP
    reported the configured hostname or the label. Comparison is
    case-insensitive on the *key* only -- IOS-XR hostnames are not
    case-sensitive in practice and LLDP has been observed to alter case, while
    the value returned is always the inventory's exact spelling.

    **A device whose `facts` did not parse contributes only its inventory
    name.** It cannot be resolved, so an LLDP peer naming its configured
    hostname stays unknown and is reported as such -- which is correct. The
    alternative, assuming the hostname equals the label, would silently
    manufacture the agreement this function exists to stop assuming.
    """

    mapping: dict[str, str] = {}
    for name, evidence in evidence_by_device.items():
        mapping[name.casefold()] = name
        hostname = configured_hostname(evidence)
        if hostname:
            mapping.setdefault(hostname.casefold(), name)
    return mapping


def resolve_device(name: str, mapping: dict[str, str]) -> str | None:
    """An LLDP-reported device ID to its inventory name, or ``None`` if unknown."""

    if not isinstance(name, str):
        return None
    return mapping.get(name.strip().casefold())


def find_lldp_disagreements(evidence_by_device: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return LLDP links where the two ends disagree about what they see.

    For a claim "``device`` port ``local_interface`` faces ``neighbor`` port
    ``claimed_neighbor_interface``" where ``neighbor`` is itself a device in
    this evidence set, the neighbor is expected to report the mirror image
    back. When it reports something else on that port instead (or nothing),
    that is a real, verified inconsistency in the fabric's LLDP data -- not a
    parsing artifact -- and must be surfaced, not silently resolved one way or
    the other.
    """

    mapping = hostname_map(evidence_by_device)
    disagreements: list[dict[str, Any]] = []
    for device_name, evidence in evidence_by_device.items():
        for record in _lldp_records(evidence):
            # B-435: resolve what the neighbour calls itself to what this
            # inventory calls it, before comparing. Without this a device
            # running a hostname that differs from its label makes every one of
            # its links look like a disagreement, and the two ends were saying
            # the same thing.
            neighbor = resolve_device(record["neighbor"], mapping)
            if neighbor is None:
                continue  # Reported separately: not a disagreement, an unknown neighbor.

            mirrors_back = any(
                resolve_device(other["neighbor"], mapping) == device_name
                and other["local_interface"] == record["neighbor_interface"]
                for other in _lldp_records(evidence_by_device[neighbor])
            )
            if mirrors_back:
                continue

            neighbor_actually_sees = [
                {"neighbor": other["neighbor"], "local_interface": other["local_interface"]}
                for other in _lldp_records(evidence_by_device[neighbor])
                if other["local_interface"] == record["neighbor_interface"]
            ]
            disagreements.append(
                {
                    "device": device_name,
                    "local_interface": record["local_interface"],
                    # The name as reported, plus what it resolved to. A reader
                    # comparing this against a device console needs the string
                    # the device actually emitted.
                    "claims_neighbor": neighbor,
                    "claims_neighbor_as_reported": record["neighbor"],
                    "claims_neighbor_interface": record["neighbor_interface"],
                    "neighbor_actually_reports": neighbor_actually_sees,
                }
            )
    return disagreements


def find_neighbors_not_in_inventory(
    evidence_by_device: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return ``{neighbor_name: ["device:interface", ...]}`` for LLDP peers this inventory does not manage."""

    mapping = hostname_map(evidence_by_device)
    unknown: dict[str, list[str]] = {}
    for device_name, evidence in evidence_by_device.items():
        for record in _lldp_records(evidence):
            neighbor = record["neighbor"]
            # B-435: a peer naming a managed device by its *configured
            # hostname* is not unknown. Before this, three of this fabric's own
            # devices were reported as foreign for the life of the project.
            if resolve_device(neighbor, mapping) is None:
                unknown.setdefault(neighbor, []).append(f"{device_name}:{record['local_interface']}")
    return unknown


def find_zero_adjacency_devices(
    evidence_by_device: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return devices reporting zero LLDP neighbours or zero IS-IS adjacencies.

    Both counts are reported side by side deliberately: PE2 is zero on both
    (fully isolated at the link layer) while PE4 has an LLDP neighbour but zero
    IS-IS adjacencies -- two different failure shapes that a single "isolated"
    flag would conflate.
    """

    zero: list[dict[str, Any]] = []
    for device_name, evidence in evidence_by_device.items():
        lldp_count = len(_lldp_records(evidence))
        isis_records = _parsed_records(evidence, "isis")
        isis_count = len(isis_records) if isis_records is not None else None
        if lldp_count == 0 or isis_count == 0:
            zero.append(
                {
                    "device": device_name,
                    "lldp_neighbors": lldp_count,
                    "isis_adjacencies": isis_count,
                }
            )
    return zero


def build_anomaly_report(evidence_by_device: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the full anomaly report over one fabric-wide evidence collection."""

    return {
        "lldp_disagreements": find_lldp_disagreements(evidence_by_device),
        "neighbors_not_in_inventory": find_neighbors_not_in_inventory(evidence_by_device),
        "zero_adjacency_devices": find_zero_adjacency_devices(evidence_by_device),
    }


def format_anomaly_report(report: dict[str, Any]) -> str:
    """Render the anomaly report as human-readable text for the CLI to print.

    Deliberately loud and impossible to miss (its own headed sections, printed
    even when everything else about the run succeeds) -- ``learn-topology``
    always exits 0, so this text is the only signal an operator gets that the
    fabric is not internally consistent.
    """

    lines = ["=== Topology anomaly report ==="]

    disagreements = report["lldp_disagreements"]
    lines.append(f"\nLLDP disagreements ({len(disagreements)}):")
    if not disagreements:
        lines.append("  none")
    for item in disagreements:
        lines.append(
            f"  {item['device']}:{item['local_interface']} claims neighbor "
            f"{item['claims_neighbor']}:{item['claims_neighbor_interface']}, but "
            f"{item['claims_neighbor']} reports {item['neighbor_actually_reports'] or 'nothing'} "
            f"on that port instead"
        )

    unknown = report["neighbors_not_in_inventory"]
    lines.append(f"\nLLDP neighbors not in inventory ({len(unknown)}):")
    if not unknown:
        lines.append("  none")
    for neighbor, seen_from in sorted(unknown.items()):
        lines.append(f"  {neighbor}: seen from {', '.join(sorted(seen_from))}")

    zero = report["zero_adjacency_devices"]
    lines.append(f"\nDevices with zero adjacencies ({len(zero)}):")
    if not zero:
        lines.append("  none")
    for item in zero:
        lines.append(
            f"  {item['device']}: lldp_neighbors={item['lldp_neighbors']} "
            f"isis_adjacencies={item['isis_adjacencies']}"
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Writing derived counts back into the inventory file.
# --------------------------------------------------------------------------- #


def update_expected_in_yaml(
    source_path: Path,
    derived: dict[str, dict[str, int]],
    *,
    out_path: Path | None = None,
) -> None:
    """Read one inventory YAML file, replace its ``expected:`` blocks, write it out.

    Reads ``source_path`` and writes to ``out_path`` (``source_path`` itself
    when ``out_path`` is omitted), so a caller can derive-and-preview to a
    scratch file without touching the source. Round-trips the whole document
    through PyYAML: load, replace ``expected`` for devices present in
    ``derived`` (dropping it for devices no longer derivable), dump. This does
    **not** preserve hand-written comments -- PyYAML has no such mode, and the
    dependency set deliberately stays at PyYAML rather than pulling in a
    comment-preserving library (ruamel.yaml) for a single-lab tool. Fine for a
    machine-maintained subsection of a lab-scale file; running this against
    ``inventory/lab.yaml`` in place would strip the explanatory comments at the
    top of that file, which is why this project's committed copy is
    hand-maintained rather than generated by this function.
    """

    raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    for device in raw.get("devices", []):
        name = device.get("name")
        if name in derived:
            device["expected"] = derived[name]
        else:
            device.pop("expected", None)

    destination = out_path or source_path
    destination.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Relationship-aware projection over a bounded topology neighbourhood (B-417,
# docs/design/evidence-reduction.md §5).
#
# Projection by identifier match is too narrow: filtering a device's log
# window to records naming the *subject* retains 0 of 28 records on the
# `broken` capture, because the cause sits upstream of the subject in the
# dependency stack and the events that explain it name a different object by
# construction (interfaces, IS-IS neighbours) -- never the subject itself.
# The generalisation is an *evidence neighbourhood*, grown outward from the
# subject through the topology it actually depends on:
#
#   depth 0   the subject's own device
#   depth 1   the interfaces and IS-IS adjacencies its session depends on
#   depth 2   the devices those interfaces terminate on
#
# This is arithmetic over the inventory and the fabric's own protocol state --
# deterministic, never a model inference -- the same discipline
# `investigation.inventory_resolver` and `descent.resolve_devices` already
# apply for `DeviceScope.SUBJECT`/`PATH`. It is placed beside them in spirit,
# not by import: this module owns no dependency on `investigation.py` or
# `flows.py`, and nothing here is wired into the descent -- `evidence_
# neighborhood` is a resolver a future caller may consume, not a consumer.
#
# One design point worth stating plainly, because it is not obvious from the
# depth table above: **the topology evidence this resolves over must not be
# the incident window's own evidence when that window is the broken one.**
# Measured on the `broken` capture itself: PE2 reports zero LLDP neighbours
# and zero IS-IS adjacencies *at that moment*, because the isolation is the
# fault under investigation -- deriving "who is PE2 next to" from PE2's own
# broken-window evidence would find nobody, for the same reason asking a
# patient mid-seizure to name their doctor finds nobody. The neighbourhood
# has to come from topology evidence that describes the fabric's declared
# wiring independent of this incident (a recent healthy capture, a dedicated
# `learn-topology` pass) -- this function does not care which, it takes
# whatever `evidence_by_device` it is given and treats it as the topology
# source, but a caller handing it the very window it is trying to explain
# will get an honestly empty answer back, not a wrong one.
# --------------------------------------------------------------------------- #


class NeighborhoodStatus(Enum):
    """Why a :class:`Neighborhood` looks the way it does -- absence is never zero.

    Three states, each a different reason a neighbourhood can come back
    small, and each must stay distinguishable in the result (OBS-181's own
    fabric-wide rule, restated for this resolver): a genuinely bounded
    result at a shallow depth, a subject that never named a device at all,
    and topology evidence that could not be read are three different facts
    and must never render the same.
    """

    #: The subject resolved to a device and (if `depth >= 1`) its topology
    #: evidence parsed cleanly. `interfaces`/`devices` may still be genuinely
    #: empty beyond the depth-0 device -- e.g. a device with no IS-IS
    #: adjacency on the interface asked about -- and that is a real, resolved
    #: fact, not a failure.
    RESOLVED = "resolved"

    #: `subject` looked like a peer address (parsed as IPv4) but no device in
    #: the inventory owns it. **Not silently treated as a local interface
    #: name on `device`** -- doing so would misattribute a remote peer's
    #: session to the local device's own topology, the exact wrong-device
    #: reading `investigation.inventory_resolver`'s docstring refuses (Q-013).
    SUBJECT_UNRESOLVED = "subject_unresolved"

    #: The depth-0 device has no evidence in this collection at all, or its
    #: `isis` section did not parse cleanly. Distinct from a clean parse that
    #: found zero adjacencies -- that is `RESOLVED` with empty `interfaces`.
    TOPOLOGY_UNAVAILABLE = "topology_unavailable"


@dataclass(frozen=True)
class Neighborhood:
    """A bounded evidence neighbourhood, grown outward from one subject.

    ``depth`` is always the bound that was *requested*, recorded here even
    when the neighbourhood turns out smaller than the bound allows -- an
    empty result at depth 1 ("nothing beyond the subject's own device, one
    hop out") and an empty result at depth 3 ("nothing beyond it after three
    hops") are different claims about the fabric, and a reader cannot tell
    them apart without knowing which bound produced the empty answer.
    """

    #: `device` as the caller passed it -- where the investigation is
    #: anchored. Not necessarily the device the neighbourhood is centred on;
    #: see `subject_device`.
    anchor_device: str
    subject: str
    #: What `subject` resolves to (depth 0), or ``None`` when
    #: `status is SUBJECT_UNRESOLVED` and there is nothing to centre on.
    subject_device: str | None
    depth: int
    status: NeighborhoodStatus
    #: Every device in the neighbourhood, depth 0 through `depth`, inclusive.
    #: Contains only `subject_device` below depth 2 -- `flows.py`'s own
    #: `DeviceScope.SUBJECT` reaches exactly this device for a `bgp_session`
    #: investigation's `igp_adjacency` rung, and depth 1 here answers the
    #: identical question this resolver was built to generalise beyond.
    devices: frozenset[str]
    #: The depth-0 device's own interfaces its session depends on (depth >=
    #: 1 only) -- IS-IS-declared, in the device's own spelling. Empty at
    #: depth 0 by definition, and may be empty at depth 1 for a real reason
    #: (see `RESOLVED`'s docstring) rather than an unread one.
    interfaces: tuple[str, ...]
    #: The raw IS-IS adjacency records `interfaces` was read from, so a
    #: caller (or a test) can verify a neighbour name against the evidence
    #: directly rather than trusting this function's own arithmetic blind.
    adjacency_records: tuple[dict[str, Any], ...]
    #: Whether resolving a device at depth >= 2 required including a
    #: candidate this fabric's IS-IS and LLDP disagreed about, rather than
    #: silently trusting one source over the other. ``False`` does not mean
    #: no disagreement was checked -- it means none was found.
    widened: bool
    #: What was disagreed about, when `widened` is ``True``. Two shapes,
    #: tagged by `"kind"`: `"isis_lldp_mismatch"` (IS-IS and LLDP name a
    #: different neighbour, or only one of them names one, on the same
    #: interface) and `"lldp_self_disagreement"` (the far end's own LLDP does
    #: not mirror this device's claim -- `find_lldp_disagreements`' class).
    disagreements: tuple[dict[str, Any], ...]


def _subject_shape_device(device: str, subject: str) -> tuple[str | None, NeighborhoodStatus, bool]:
    """``(depth-0 device, status, device_wide)`` -- arithmetic subject resolution.

    A subject that parses as an IPv4 address is a peer/router-id reference --
    the `bgp_session` flow's vocabulary, `DeviceScope.SUBJECT`'s shape -- and
    MUST resolve through the inventory's own `router_id` field before it
    names a device; silently falling back to `device` would produce the
    wrong-device reading `SUBJECT_UNRESOLVED`'s docstring refuses. Anything
    else is already local to `device` -- the `isis_adjacency`/`ldp_session`/
    `interface` flows' vocabulary, `DeviceScope.LOCAL`'s shape -- so no
    lookup is needed and this step cannot fail for it.

    ``device_wide`` tells the caller which depth-1 traversal applies:
    ``True`` for a peer address (the session depends on the device's IGP
    standing broadly -- `flows.py`'s own `SubjectRule.DEVICE_WIDE` choice for
    this exact shape), ``False`` for a local interface name (narrow to that
    one interface -- `SubjectRule.AS_IS`).
    """

    try:
        ipaddress.IPv4Address(subject)
    except ValueError:
        return device, NeighborhoodStatus.RESOLVED, False

    from .inventory_model import load_inventory_file

    for entry in load_inventory_file().devices:
        if getattr(entry, "router_id", None) == subject:
            return entry.name, NeighborhoodStatus.RESOLVED, True
    return None, NeighborhoodStatus.SUBJECT_UNRESOLVED, False


def _session_interfaces(
    evidence: dict[str, Any], device_wide: bool, subject: str | None
) -> tuple[list[str] | None, list[dict[str, Any]] | None]:
    """``(interfaces, adjacency records)`` the session depends on, or ``(None, None)``.

    ``None`` only when `isis` did not parse cleanly -- `TOPOLOGY_UNAVAILABLE`'s
    case. A clean parse that simply has no matching record returns ``([],
    [])``, genuinely empty rather than unread.
    """

    records = _parsed_records(evidence, "isis")
    if records is None:
        return None, None
    if not device_wide:
        records = [r for r in records if same_interface(r.get("interface", ""), subject or "")]
    interfaces: list[str] = []
    for record in records:
        interface = record.get("interface")
        if interface and not any(same_interface(interface, seen) for seen in interfaces):
            interfaces.append(interface)
    return interfaces, records


def _widen_scope_interfaces(
    evidence: dict[str, Any], device_wide: bool, subject: str | None, isis_interfaces: list[str]
) -> list[str]:
    """Interfaces to cross-check at the next hop -- wider than `interfaces` on purpose.

    For a local-interface subject this is always ``[subject]``, regardless of
    whether IS-IS has a record for it -- **and that "regardless" is the whole
    point.** Measured on the `isis-broken` fixture: PE3's Gi0/0/0/0 carries an
    LLDP neighbour (P2) and no IS-IS adjacency at all (B-496 -- the interface
    has no IPv4 address, so no adjacency can form). Scoping the widen check to
    IS-IS's own interface list would never look at that port and P2 would be
    silently dropped rather than surfaced as a disagreement -- exactly what
    the caution against picking a side exists to prevent.

    For a device-wide subject this is IS-IS's interfaces unioned with
    whatever LLDP names on top, canonicalised (`interface_kind.same_interface`)
    so `Gi0/0/0/0` (IS-IS's spelling) and `GigabitEthernet0/0/0/0` (LLDP's)
    compare equal -- both read from the same device, which is this function's
    precondition per `canonical`'s own docstring.
    """

    if not device_wide:
        return [subject] if subject else []
    scope = list(isis_interfaces)
    for record in _lldp_records(evidence):
        local = record.get("local_interface")
        if local and not any(same_interface(local, seen) for seen in scope):
            scope.append(local)
    return scope


def _widened_neighbors(
    evidence: dict[str, Any],
    mapping: dict[str, str],
    from_device: str,
    scope_interfaces: list[str],
    fabric_disagreements: list[dict[str, Any]],
) -> tuple[set[str], list[dict[str, Any]]]:
    """``(candidate devices, disagreements)`` for one hop out of `from_device`.

    Both IS-IS's and LLDP's resolved neighbour are added when they differ --
    union, never a pick -- because `graph.py` walks the two protocols
    independently for the same reason this does: an interface can carry a
    clean LLDP edge and no IS-IS edge, or the reverse, and collapsing them
    into one silently-chosen answer erases exactly the disagreement worth
    having a neighbourhood resolver for.

    `fabric_disagreements` (`find_lldp_disagreements`'s own output, passed in
    rather than recomputed per hop) adds a second, independent trigger: even
    when this device's own IS-IS and LLDP agree, the far end's LLDP may not
    mirror this device's claim, which makes this device's own LLDP candidate
    itself suspect. Widening there adds *both* what this device claims and
    what the far end actually reports instead, rather than trusting either.
    """

    isis_records = _parsed_records(evidence, "isis") or []
    lldp_records = _lldp_records(evidence)

    candidates: set[str] = set()
    disagreements: list[dict[str, Any]] = []

    for interface in scope_interfaces:
        isis_match = next(
            (r for r in isis_records if same_interface(r.get("interface", ""), interface)), None
        )
        lldp_match = next(
            (r for r in lldp_records if same_interface(r.get("local_interface", ""), interface)), None
        )
        isis_neighbor = resolve_device(isis_match.get("system_id"), mapping) if isis_match else None
        lldp_neighbor = resolve_device(lldp_match.get("neighbor"), mapping) if lldp_match else None
        if isis_neighbor:
            candidates.add(isis_neighbor)
        if lldp_neighbor:
            candidates.add(lldp_neighbor)
        if isis_neighbor != lldp_neighbor and (isis_neighbor or lldp_neighbor):
            disagreements.append(
                {
                    "kind": "isis_lldp_mismatch",
                    "device": from_device,
                    "interface": interface,
                    "isis_neighbor": isis_neighbor,
                    "lldp_neighbor": lldp_neighbor,
                }
            )

    for entry in fabric_disagreements:
        if entry["device"] != from_device:
            continue
        if not any(same_interface(entry["local_interface"], interface) for interface in scope_interfaces):
            continue
        candidates.add(entry["claims_neighbor"])
        for other in entry["neighbor_actually_reports"]:
            resolved = resolve_device(other.get("neighbor"), mapping)
            if resolved:
                candidates.add(resolved)
        disagreements.append({"kind": "lldp_self_disagreement", **entry})

    return candidates, disagreements


def evidence_neighborhood(
    evidence_by_device: dict[str, dict[str, Any]], device: str, subject: str, depth: int
) -> Neighborhood:
    """The bounded evidence neighbourhood around `subject`, `depth` hops out.

    Deterministic arithmetic over `evidence_by_device`'s IS-IS/LLDP records
    and the inventory's `router_id`s -- no model call, ever; see this
    section's own module-level comment for why `evidence_by_device` must
    describe the fabric's declared topology rather than the incident window
    under investigation when the two differ. `depth` is bounded (never walks
    past what it is told to) and always recorded on the result, per B-417's
    binding rule that an empty answer at one depth must not be mistaken for
    one at another.

    Raises ``ValueError`` for a caller error only: a negative depth, or an
    `device` with no evidence entry in this collection at all -- the same
    refusal `descent.resolve_devices` makes for a missing resolver, because
    there is nothing here to resolve *from*. Every other way this can come up
    empty is represented in the returned `Neighborhood.status`, not raised.
    """

    if depth < 0:
        raise ValueError(f"depth must be a non-negative integer, got {depth!r}")
    if device not in evidence_by_device:
        raise ValueError(
            f"{device!r} has no evidence in this collection; refusing to resolve a "
            "neighbourhood with nothing to resolve it from"
        )

    subject_device, status, device_wide = _subject_shape_device(device, subject)
    if status is NeighborhoodStatus.SUBJECT_UNRESOLVED:
        return Neighborhood(
            anchor_device=device, subject=subject, subject_device=None, depth=depth, status=status,
            devices=frozenset(), interfaces=(), adjacency_records=(), widened=False, disagreements=(),
        )
    assert subject_device is not None  # RESOLVED always carries a device

    if subject_device not in evidence_by_device:
        return Neighborhood(
            anchor_device=device, subject=subject, subject_device=subject_device, depth=depth,
            status=NeighborhoodStatus.TOPOLOGY_UNAVAILABLE, devices=frozenset({subject_device}),
            interfaces=(), adjacency_records=(), widened=False, disagreements=(),
        )

    devices = {subject_device}
    if depth == 0:
        return Neighborhood(
            anchor_device=device, subject=subject, subject_device=subject_device, depth=0,
            status=NeighborhoodStatus.RESOLVED, devices=frozenset(devices),
            interfaces=(), adjacency_records=(), widened=False, disagreements=(),
        )

    interfaces, records = _session_interfaces(evidence_by_device[subject_device], device_wide, subject)
    if interfaces is None:
        return Neighborhood(
            anchor_device=device, subject=subject, subject_device=subject_device, depth=depth,
            status=NeighborhoodStatus.TOPOLOGY_UNAVAILABLE, devices=frozenset(devices),
            interfaces=(), adjacency_records=(), widened=False, disagreements=(),
        )

    if depth == 1:
        return Neighborhood(
            anchor_device=device, subject=subject, subject_device=subject_device, depth=1,
            status=NeighborhoodStatus.RESOLVED, devices=frozenset(devices),
            interfaces=tuple(interfaces), adjacency_records=tuple(records),
            widened=False, disagreements=(),
        )

    # depth >= 2: BFS outward one hop per depth level, from the depth-1
    # session-dependent interfaces onward. Every hop after the first is a
    # plain device-wide graph expansion (`device_wide=True`, no `subject`) --
    # depth 1's own subject-shaped restriction only ever applies to the
    # subject's own device.
    mapping = hostname_map(evidence_by_device)
    fabric_disagreements = find_lldp_disagreements(evidence_by_device)
    disagreements: list[dict[str, Any]] = []
    frontier: dict[str, tuple[bool, str | None, list[str]]] = {
        subject_device: (device_wide, subject, interfaces)
    }
    hop = 1
    while hop < depth and frontier:
        hop += 1
        next_frontier: dict[str, tuple[bool, str | None, list[str]]] = {}
        for from_device, (from_wide, from_subject, from_interfaces) in frontier.items():
            from_evidence = evidence_by_device.get(from_device)
            if from_evidence is None:
                continue
            scope = _widen_scope_interfaces(from_evidence, from_wide, from_subject, from_interfaces)
            candidates, hop_disagreements = _widened_neighbors(
                from_evidence, mapping, from_device, scope, fabric_disagreements
            )
            disagreements.extend(hop_disagreements)
            for candidate in candidates - devices:
                devices.add(candidate)
                candidate_evidence = evidence_by_device.get(candidate)
                if candidate_evidence is None:
                    continue
                candidate_interfaces, _ = _session_interfaces(candidate_evidence, True, None)
                if candidate_interfaces:
                    next_frontier[candidate] = (True, None, candidate_interfaces)
        frontier = next_frontier

    return Neighborhood(
        anchor_device=device, subject=subject, subject_device=subject_device, depth=depth,
        status=NeighborhoodStatus.RESOLVED, devices=frozenset(devices),
        interfaces=tuple(interfaces), adjacency_records=tuple(records),
        widened=bool(disagreements), disagreements=tuple(disagreements),
    )
