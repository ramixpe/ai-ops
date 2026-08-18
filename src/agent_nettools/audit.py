"""`nettools audit` — the fabric judged against itself. B-477.

`health.py`'s rules judge one device against its **role**. This module judges
the fabric against its own **consistency**: two devices claiming the same
router-ID, two ends of a link disagreeing about MTU, a device whose BGP is
configured but whose sessions are all down. Cross-device comparison is the new
information — none of these is visible from any single device's evidence.

Operational state only, deliberately. Config-hygiene auditing (is NTP
configured, does the ACL match the standard) needs the config axis, which is
B-104 and Part 2; an audit built on `show running-config` before that axis
exists would be a second, weaker config reader. Everything here reads the same
parsed operational evidence the descent trusts.

Two rules that belong here and are NOT implemented, named rather than faked:

* **BGP timer asymmetry** — the `bgp` summary parse carries no hold/keepalive;
  per-peer timers live in `show bgp neighbor` output, which a fabric evidence
  collection does not include. Needs a manifest extension first.
* **IS-IS metric asymmetry** — the `isis` neighbor parse carries no metric
  field; metrics live in `show isis interface`, a Tier-I addition.

Inventing a parser inside an audit rule would put a second, unreviewed reader
beside the one the descent trusts — the duplication face (§0.13).

Unevaluated discipline, inherited whole from `checks.py`: a rule must never
read a missing or failed parse as consistent. A device whose needed intent did
not reach ``PARSE_OK`` lands in ``unevaluated`` for that rule, loudly, and
contributes no finding and no clean bill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import parsers
from .interface_kind import canonical as canonical_interface
from .topology import configured_hostname, hostname_map, resolve_device

__all__ = ["AUDIT_RULES", "AuditRule", "run_audit"]

SEVERITY_ORDER = ("ok", "info", "warning", "critical")


@dataclass(frozen=True)
class AuditRule:
    name: str
    severity: str
    #: "device" (one device's own inconsistency), "protocol" (two ends of one
    #: adjacency/session), "fabric" (a property of the whole set).
    category: str
    fn: Callable[[dict[str, dict[str, Any]]], tuple[list[dict], list[str]]]


def _parsed(evidence: dict[str, Any], intent: str) -> dict[str, Any] | None:
    """One device's parsed section, or ``None`` unless it reached PARSE_OK.

    A non-dict `evidence` (a malformed collection) returns ``None`` -- so that
    device lands in `unevaluated`, never crashing the whole fabric audit. The
    module's own resilience discipline, applied to its own input
    (2026-08-18 review).
    """

    if not isinstance(evidence, dict):
        return None
    section = evidence.get(intent)
    if not isinstance(section, dict):
        return None
    data = section.get("data") or {}
    if data.get("parse_status") != parsers.PARSE_OK:
        return None
    return data.get("parsed") or {}


def _split_ok(
    evidence_by_device: dict[str, dict[str, Any]], intent: str
) -> tuple[dict[str, dict], list[str]]:
    """``(parsed_by_device, unevaluated_names)`` for one intent."""

    ok: dict[str, dict] = {}
    unevaluated: list[str] = []
    for name, evidence in evidence_by_device.items():
        parsed = _parsed(evidence, intent)
        if parsed is None:
            unevaluated.append(name)
        else:
            ok[name] = parsed
    return ok, unevaluated


# --------------------------------------------------------------------------- #
# Rules. Each returns (findings, unevaluated_devices).
# --------------------------------------------------------------------------- #


def _duplicate_router_id(by_device: dict[str, dict[str, Any]]):
    """Two devices announcing the same BGP router-ID.

    The classic silent killer: sessions flap or never establish, and every
    single-device view looks locally plausible. Only the fabric-wide set can
    see it — which is this module's whole reason to exist.
    """

    parsed, unevaluated = _split_ok(by_device, "bgp")
    owners: dict[str, list[str]] = {}
    for name, p in parsed.items():
        rid = (p.get("meta") or {}).get("router_id")
        if rid:
            owners.setdefault(str(rid), []).append(name)

    findings = [
        {
            "message": f"router-ID {rid} is claimed by {len(names)} devices: "
                       f"{', '.join(sorted(names))}",
            "devices": sorted(names),
            "evidence": "bgp:meta.router_id",
        }
        for rid, names in owners.items() if len(names) > 1
    ]
    return findings, unevaluated


def _mtu_mismatch_on_adjacency(by_device: dict[str, dict[str, Any]]):
    """The two ends of an LLDP adjacency disagreeing about MTU.

    IS-IS forms an adjacency and then blackholes large packets — the fault
    that passes every ping and fails the transfer. Adjacencies come from LLDP;
    device IDs resolve through `topology.hostname_map` (B-435's resolver), so
    a renamed device does not read as a foreign one.
    """

    lldp, un_lldp = _split_ok(by_device, "lldp")
    intf, un_intf = _split_ok(by_device, "interfaces")
    unevaluated = sorted(set(un_lldp) | set(un_intf))

    mapping = hostname_map(by_device)

    def mtu_of(device: str, interface: str) -> str | None:
        # LLDP prints `GigabitEthernet0/0/0/0`; the interface table prints
        # `Gi0/0/0/0`. The same two-spelling join `descent._path_members`
        # documents -- matched through `interface_kind.canonical`, the one
        # declared resolver, or this rule silently never fires (which is
        # exactly how the first draft failed its own synthetic test).
        wanted = canonical_interface(interface)
        for record in (intf.get(device) or {}).get("records") or []:
            if canonical_interface(record.get("interface", "")) == wanted:
                return record.get("mtu")
        return None

    findings, seen = [], set()
    for device, p in lldp.items():
        for record in p.get("records") or []:
            neighbor = resolve_device(record.get("neighbor", ""), mapping)
            if neighbor is None or neighbor not in intf or device not in intf:
                continue
            key = tuple(sorted([(device, record.get("local_interface")),
                                (neighbor, record.get("neighbor_interface"))]))
            if key in seen:
                continue  # each link once, whichever end reported it first
            seen.add(key)
            local_mtu = mtu_of(device, record.get("local_interface", ""))
            remote_mtu = mtu_of(neighbor, record.get("neighbor_interface", ""))
            if local_mtu and remote_mtu and local_mtu != remote_mtu:
                findings.append({
                    "message": f"MTU mismatch on {device}:{record['local_interface']} "
                               f"({local_mtu}) <-> {neighbor}:"
                               f"{record['neighbor_interface']} ({remote_mtu})",
                    "devices": sorted([device, neighbor]),
                    "evidence": "lldp + interfaces:mtu",
                })
    return findings, unevaluated


def _hostname_inventory_drift(by_device: dict[str, dict[str, Any]]):
    """Configured hostname differs from the inventory name.

    ``info``, not ``warning`` — the t0 fixtures prove it can be benign
    (OBS-103) — but it is the precondition for every naming confusion this
    project has had, so it is worth one line while it is cheap to fix.
    """

    findings, unevaluated = [], []
    for name, evidence in by_device.items():
        hostname = configured_hostname(evidence) if isinstance(evidence, dict) else None
        if hostname is None:
            unevaluated.append(name)
        elif hostname.casefold() != name.casefold():
            findings.append({
                "message": f"{name} is configured as {hostname!r} — LLDP peers "
                           "will report that name (B-435 resolves it, humans may not)",
                "devices": [name],
                "evidence": "facts:meta.hostname",
            })
    return findings, unevaluated


def _isolated_but_configured(by_device: dict[str, dict[str, Any]]):
    """BGP configured (router-ID present) with zero established sessions.

    Configured intent with no operation — either a fault, or debris worth
    removing. Either way, invisible to role rules: a P-router with no BGP is
    healthy, a PE whose BGP is all down already pages, but a device whose BGP
    exists and is *entirely* down sits exactly between the two.
    """

    parsed, unevaluated = _split_ok(by_device, "bgp")
    findings = []
    for name, p in parsed.items():
        meta = p.get("meta") or {}
        if not meta.get("router_id"):
            continue  # no BGP process: nothing configured, nothing to audit
        records = p.get("records") or []
        # Zero configured sessions is IN scope -- the docstring's clearest
        # "debris" case -- and the first draft's `if records and ...` silently
        # excluded exactly it (2026-08-18 review). A router-id with no session
        # is BGP configured to do nothing.
        if not any(r.get("session_state") == "Established" for r in records):
            n = len(records)
            detail = (f"{n} configured session(s), none Established" if n
                      else "no configured sessions at all")
            findings.append({
                "message": f"{name} has an active BGP process ({p.get('meta', {}).get('router_id')}) "
                           f"but {detail}",
                "devices": [name],
                "evidence": "bgp:records.session_state",
            })
    return findings, unevaluated


def _local_as_mix(by_device: dict[str, dict[str, Any]]):
    """More than one local AS across the fabric. ``info``: legitimate in an
    eBGP design, worth one line in an iBGP one — the reader knows which
    fabric they run."""

    parsed, unevaluated = _split_ok(by_device, "bgp")
    by_as: dict[str, list[str]] = {}
    for name, p in parsed.items():
        asn = (p.get("meta") or {}).get("local_as")
        if asn:
            by_as.setdefault(str(asn), []).append(name)
    if len(by_as) > 1:
        detail = "; ".join(f"AS {a}: {', '.join(sorted(n))}" for a, n in sorted(by_as.items()))
        return ([{"message": f"multiple local AS numbers observed — {detail}",
                  "devices": sorted(n for ns in by_as.values() for n in ns),
                  "evidence": "bgp:meta.local_as"}], unevaluated)
    return [], unevaluated


AUDIT_RULES: tuple[AuditRule, ...] = (
    AuditRule("duplicate_router_id", "critical", "fabric", _duplicate_router_id),
    AuditRule("mtu_mismatch_on_adjacency", "warning", "protocol", _mtu_mismatch_on_adjacency),
    AuditRule("isolated_but_configured", "warning", "device", _isolated_but_configured),
    AuditRule("hostname_inventory_drift", "info", "device", _hostname_inventory_drift),
    AuditRule("local_as_mix", "info", "fabric", _local_as_mix),
)


def run_audit(evidence_by_device: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Run every audit rule over one fabric-wide evidence collection."""

    findings: list[dict[str, Any]] = []
    unevaluated: list[dict[str, Any]] = []

    for rule in AUDIT_RULES:
        rule_findings, rule_unevaluated = rule.fn(evidence_by_device)
        for f in rule_findings:
            findings.append({"rule": rule.name, "severity": rule.severity,
                             "category": rule.category, **f})
        if rule_unevaluated:
            unevaluated.append({"rule": rule.name, "devices": sorted(rule_unevaluated)})

    severity = "ok"
    for f in findings:
        if SEVERITY_ORDER.index(f["severity"]) > SEVERITY_ORDER.index(severity):
            severity = f["severity"]

    return {
        "tool": "audit",
        "devices_examined": sorted(evidence_by_device),
        "severity": severity,
        "findings": findings,
        "categories": {
            c: [f for f in findings if f["category"] == c]
            for c in ("device", "protocol", "fabric")
        },
        "unevaluated": unevaluated,
    }
