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

from pathlib import Path
from typing import Any

import yaml

from . import parsers

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
