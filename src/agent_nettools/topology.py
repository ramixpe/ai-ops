"""Derive expected per-device topology counts from parsed evidence, and report anomalies.

``nettools learn-topology`` is the only writer of the ``expected:`` blocks in
``inventory/lab.yaml``. Everything in that block is *derived* -- counted from a
real collection, never invented -- because a hand-typed expectation drifts the
moment the fabric changes and nobody notices.

This module also builds an anomaly report over the same evidence. The fabric's
real LLDP/IS-IS data is genuinely inconsistent (verified against the committed
t0 fixtures, not hypothetical):

- PE2 reports 0 LLDP neighbours and 0 IS-IS adjacencies while still carrying a
  BGP router-id and one (idle) BGP peer -- it is isolated at the link layer but
  not absent from BGP.
- LLDP disagrees with itself: P1 reports Gi0/0/0/0 facing P2's Gi0/0/0/0, while
  P2 reports that very port facing ``LEAF05_DHCP_SERVER`` instead.
- Several LLDP neighbours (``Lab-leaf01``, ``LEAF05_DHCP_SERVER``,
  ``SDWAN-Edge01``) are not devices this inventory manages at all.

Only per-device *counts* are written back to the inventory (see the comment in
``inventory/lab.yaml``) precisely because link-level topology cannot be stated
truthfully here -- the report below is how those specifics stay visible instead
of being silently dropped.
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

    disagreements: list[dict[str, Any]] = []
    for device_name, evidence in evidence_by_device.items():
        for record in _lldp_records(evidence):
            neighbor = record["neighbor"]
            if neighbor not in evidence_by_device:
                continue  # Reported separately: not a disagreement, an unknown neighbor.

            mirrors_back = any(
                other["neighbor"] == device_name
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
                    "claims_neighbor": neighbor,
                    "claims_neighbor_interface": record["neighbor_interface"],
                    "neighbor_actually_reports": neighbor_actually_sees,
                }
            )
    return disagreements


def find_neighbors_not_in_inventory(
    evidence_by_device: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return ``{neighbor_name: ["device:interface", ...]}`` for LLDP peers this inventory does not manage."""

    unknown: dict[str, list[str]] = {}
    for device_name, evidence in evidence_by_device.items():
        for record in _lldp_records(evidence):
            neighbor = record["neighbor"]
            if neighbor not in evidence_by_device:
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
