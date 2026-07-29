"""Deterministic health verdicts over collected evidence.

Why this module exists
-----------------------
``status: "success"`` in every other tool in this package reflects only
*transport* success: the SSH session opened and the commands ran. A device
whose every BGP peer is down still reports ``success`` -- there is nothing
today that answers "is this device healthy?". At fleet scale that question
must be answered by cheap, deterministic rules *before* any LLM ever looks at
the evidence, so the reasoning layer only ever sees the anomalies, not all
nine (or nine thousand) devices' worth of noise.

Two independent rule classes -- read this before adding a rule
----------------------------------------------------------------
``inventory/lab.yaml``'s ``expected:`` blocks are *derived* from this fabric's
own current state (see ``topology.py``), and this fabric is partly broken:
the file literally records ``PE2: isis_adjacencies: 0`` and
``PE4: isis_adjacencies: 0``. A rule that only compares observed counts
against that baseline would therefore call both devices healthy -- the
baseline blesses the very brokenness it was measured from. So health rules
come in two kinds, kept in separate tables below, and a change should know
which one it is:

1. **Role invariants** (``ROLE_INVARIANT_RULES``) -- what must be true of a
   router in this fabric *given its role*, independent of anything recorded
   in the inventory. These are what catch baked-in brokenness that the
   baseline would otherwise bless: "every router must have at least one IS-IS
   adjacency" does not care that the baseline says zero is expected.
2. **Baseline rules** (``BASELINE_RULES``) -- compare an observed count
   against the inventory's recorded ``expected:`` value. These catch *drift*
   from the last known-derived state, which role invariants cannot see (a
   device dropping from 4 BGP peers to 3 is not a role violation, but it is a
   change worth flagging).

A third, single ``META_RULES`` entry (``suspicious_baseline``) makes the tool
honest about its own baseline: when the recorded expectation is itself a
value a role invariant would call unhealthy (``isis_adjacencies == 0``), that
is flagged directly, so nobody mistakes "matches the baseline" for "is
healthy" on a device whose baseline was learned from a broken fabric.

Unevaluated, not silently "ok"
-------------------------------
A rule must never read a missing or failed intent as healthy. If an intent's
parse did not succeed (``parsers.PARSE_OK``) -- because the platform command
errored, is ``unsupported`` on this platform, or the parser itself failed --
every rule reading that intent is skipped for that device and the intent name
is listed in the verdict's ``unevaluated`` list instead. Silence from a failed
collection must never look like health.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import parsers
from .inventory_model import Device, load_inventory_file
from .network_tools import STATUS_ERROR, STATUS_UNSUPPORTED
from .platforms import DEFAULT_PLATFORM

# Ordered weakest to strongest so "max severity" is a simple index comparison.
SEVERITY_ORDER: tuple[str, ...] = ("ok", "info", "warning", "critical")

# The four intents any health rule reads. Kept as one tuple so the
# "unevaluated" bookkeeping in evaluate_device stays in lockstep with the
# rules below -- add an intent here (and to _build_context) before writing a
# rule that reads it.
_HEALTH_INTENTS: tuple[str, ...] = ("isis", "bgp", "interfaces", "sr")


def severity_rank(severity: str) -> int:
    """Return a severity's position in ``SEVERITY_ORDER`` (higher is worse)."""

    return SEVERITY_ORDER.index(severity)


def _max_severity(a: str, b: str) -> str:
    return a if severity_rank(a) >= severity_rank(b) else b


def exit_code_for_severity(severity: str) -> int:
    """Map a verdict's severity to a process exit code for CI/cron gating.

    ``0`` for ok/info (nothing actionable), ``1`` for warning, ``2`` for
    critical -- so a scheduled ``nettools health --all`` can fail a pipeline
    only when something actually needs attention.
    """

    if severity == "critical":
        return 2
    if severity == "warning":
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Evidence extraction: turn one device's evidence into a rule-ready context.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleContext:
    """One device's evidence, pre-extracted so every rule reads the same shape.

    ``*_ok`` records whether that intent's parse succeeded -- rules must check
    it before trusting the paired records/meta, never infer health from an
    empty list, since "no records" and "not evaluated" are different facts.
    """

    device: Device
    isis_ok: bool
    isis_records: list[dict[str, Any]]
    bgp_ok: bool
    bgp_records: list[dict[str, Any]]
    bgp_meta: dict[str, Any]
    interfaces_ok: bool
    interface_records: list[dict[str, Any]]
    sr_ok: bool
    sr_records: list[dict[str, Any]]


# Why an intent could not be evaluated. The distinction drives severity: a
# vendor that has no such command is benign, while an unreachable device or
# unreadable output means we are flying blind and must not report health.
UNEVAL_UNSUPPORTED = "unsupported"
UNEVAL_COLLECTION_FAILED = "collection_failed"
UNEVAL_PARSE_FAILED = "parse_failed"


def _intent_records(
    evidence: dict[str, Any], intent: str
) -> tuple[list[dict[str, Any]] | None, dict[str, Any], bool]:
    """Return ``(records, meta, ok)`` for one intent's section of evidence.

    ``ok`` is False whenever the section did not reach ``parsers.PARSE_OK`` --
    a health rule has nothing trustworthy to read in that case. Use
    ``_uneval_reason`` to find out *why*, which is what severity depends on.
    """

    section = evidence.get(intent)
    data = section.get("data", {}) if isinstance(section, dict) else {}
    if data.get("parse_status") != parsers.PARSE_OK:
        return None, {}, False
    parsed = data.get("parsed") or {}
    return list(parsed.get("records") or []), dict(parsed.get("meta") or {}), True


def _uneval_reason(evidence: dict[str, Any], intent: str) -> str | None:
    """Classify why an intent yielded nothing trustworthy, or None if it did.

    Order matters: a section whose commands never ran is a collection failure
    regardless of what parse status it carries.
    """

    section = evidence.get(intent)
    if not isinstance(section, dict):
        return UNEVAL_COLLECTION_FAILED

    status = section.get("status")
    if status == STATUS_UNSUPPORTED:
        return UNEVAL_UNSUPPORTED
    if status == STATUS_ERROR:
        return UNEVAL_COLLECTION_FAILED

    parse_status = section.get("data", {}).get("parse_status")
    if parse_status == parsers.PARSE_OK:
        return None
    if parse_status == parsers.PARSE_UNAVAILABLE:
        # No parser for this platform. Not the device's fault, but we still
        # cannot assess it -- treated as unsupported for severity purposes.
        return UNEVAL_UNSUPPORTED
    return UNEVAL_PARSE_FAILED


def _build_context(evidence: dict[str, Any], device: Device) -> RuleContext:
    isis_records, _, isis_ok = _intent_records(evidence, "isis")
    bgp_records, bgp_meta, bgp_ok = _intent_records(evidence, "bgp")
    interface_records, _, interfaces_ok = _intent_records(evidence, "interfaces")
    sr_records, _, sr_ok = _intent_records(evidence, "sr")
    return RuleContext(
        device=device,
        isis_ok=isis_ok,
        isis_records=isis_records or [],
        bgp_ok=bgp_ok,
        bgp_records=bgp_records or [],
        bgp_meta=bgp_meta,
        interfaces_ok=interfaces_ok,
        interface_records=interface_records or [],
        sr_ok=sr_ok,
        sr_records=sr_records or [],
    )


def _is_numeric(value: Any) -> bool:
    return isinstance(value, str) and value.strip().isdigit()


# --------------------------------------------------------------------------- #
# Role invariants: what must be true of a router in this fabric, given its
# role, independent of the (possibly broken) recorded baseline.
# --------------------------------------------------------------------------- #


def _isis_isolated(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a router with zero IS-IS adjacencies.

    Every router in this fabric must have at least one -- this must fire for
    PE2 and PE4 even though ``inventory/lab.yaml`` records their baseline as
    exactly zero (see the module docstring): the baseline describes a broken
    fabric, not a target.
    """

    if not ctx.isis_ok or ctx.isis_records:
        return []
    return [
        {
            "intent": "isis",
            "message": "No IS-IS adjacencies; device is isolated at the IGP layer.",
            "expected": ">=1",
            "actual": 0,
            "subject": None,
        }
    ]


def _isis_adjacency_not_up(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an IS-IS adjacency stuck in a non-Up state (one finding per adjacency)."""

    if not ctx.isis_ok:
        return []
    return [
        {
            "intent": "isis",
            "message": f"IS-IS adjacency to {record.get('system_id')} is "
            f"{record.get('state')}, not Up.",
            "expected": "Up",
            "actual": record.get("state"),
            "subject": record.get("system_id"),
        }
        for record in ctx.isis_records
        if record.get("state") != "Up"
    ]


def _bgp_session_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a BGP peer not in the Established state.

    IOS-XR reuses the ``St/PfxRcd`` summary column for a state word (``Idle``,
    ``Active``, ``Connect``, ...) whenever a session is not Established; a
    plain number in that column *is* the received-prefix count and means the
    session is up. Non-numeric there is the only reliable "session down"
    signal in this output.
    """

    if not ctx.bgp_ok:
        return []
    findings = []
    for record in ctx.bgp_records:
        state = record.get("state_pfx_rcd", "")
        if not _is_numeric(state):
            findings.append(
                {
                    "intent": "bgp",
                    "message": f"BGP session to {record.get('neighbor')} is not "
                    f"Established (state: {state}).",
                    "expected": "Established",
                    "actual": state,
                    "subject": record.get("neighbor"),
                }
            )
    return findings


def _bgp_process_absent(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an edge or route-reflector device with no active BGP process.

    A core P-router legitimately runs no BGP at all in this fabric (it only
    speaks IS-IS), so this must never fire for role "core" -- only edge and
    route-reflector roles are expected to run BGP.
    """

    if not ctx.bgp_ok or ctx.device.role == "core":
        return []
    # The parser only sets "active" (to False) on the inactive-process reply;
    # its absence means a normal, active process was parsed instead.
    if ctx.bgp_meta.get("active", True) is False:
        return [
            {
                "intent": "bgp",
                "message": f"No active BGP process; role {ctx.device.role!r} requires one.",
                "expected": "active",
                "actual": "inactive",
                "subject": None,
            }
        ]
    return []


def _bgp_no_prefixes(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an Established BGP peer carrying zero received prefixes.

    Legitimate in an empty lab with no routes advertised anywhere, so this is
    informational only -- never a warning or critical, unlike a session that
    is not Established at all (see ``bgp_session_down``).
    """

    if not ctx.bgp_ok:
        return []
    findings = []
    for record in ctx.bgp_records:
        state = record.get("state_pfx_rcd", "")
        if _is_numeric(state) and int(state) == 0:
            findings.append(
                {
                    "intent": "bgp",
                    "message": f"BGP peer {record.get('neighbor')} is Established "
                    "with 0 prefixes received.",
                    "expected": ">0",
                    "actual": 0,
                    "subject": record.get("neighbor"),
                }
            )
    return findings


def _interface_admin_up_line_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an interface administratively up whose line protocol is not up.

    An admin-down interface is intentional and must never fire this rule --
    only a link the operator meant to be usable but that is not actually
    passing traffic.
    """

    if not ctx.interfaces_ok:
        return []
    return [
        {
            "intent": "interfaces",
            "message": f"Interface {record.get('interface')} is admin up but line "
            f"protocol is {record.get('line_protocol')}.",
            "expected": "up",
            "actual": record.get("line_protocol"),
            "subject": record.get("interface"),
        }
        for record in ctx.interface_records
        if record.get("admin_state") == "up" and record.get("line_protocol") != "up"
    ]


def _sr_policy_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an SR-TE policy that is not operationally up.

    Traffic steered onto a down policy has either fallen back to best-effort
    routing or has no path at all -- worth a warning, not a critical, since
    IGP forwarding underneath may still be fine.
    """

    if not ctx.sr_ok:
        return []
    return [
        {
            "intent": "sr",
            "message": f"SR-TE policy {record.get('policy')} is not operationally up "
            f"(state: {record.get('operational_state')}).",
            "expected": "up",
            "actual": record.get("operational_state"),
            "subject": record.get("policy"),
        }
        for record in ctx.sr_records
        if record.get("operational_state") != "up"
    ]


# --------------------------------------------------------------------------- #
# Baseline rules: observed counts vs. the inventory's recorded `expected:`.
# --------------------------------------------------------------------------- #


def _isis_adjacency_count_drift(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: observed IS-IS adjacency count differs from the learned baseline.

    Skipped entirely when no baseline was ever derived (``expected`` absent) --
    there is nothing to drift from.
    """

    if not ctx.isis_ok or ctx.device.expected is None:
        return []
    expected = ctx.device.expected.isis_adjacencies
    if expected is None:
        return []
    actual = len(ctx.isis_records)
    if actual == expected:
        return []
    return [
        {
            "intent": "isis",
            "message": f"IS-IS adjacency count {actual} differs from the recorded "
            f"baseline {expected}.",
            "expected": expected,
            "actual": actual,
            "subject": None,
        }
    ]


def _bgp_peer_count_drift(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: observed BGP peer count differs from the learned baseline.

    Skipped for a device with no ``bgp_peers`` baseline at all -- a device
    with no BGP process (never given a baseline, see ``topology.py``) has
    nothing to compare.
    """

    if not ctx.bgp_ok or ctx.device.expected is None:
        return []
    expected = ctx.device.expected.bgp_peers
    if expected is None:
        return []
    actual = len(ctx.bgp_records)
    if actual == expected:
        return []
    return [
        {
            "intent": "bgp",
            "message": f"BGP peer count {actual} differs from the recorded baseline "
            f"{expected}.",
            "expected": expected,
            "actual": actual,
            "subject": None,
        }
    ]


# --------------------------------------------------------------------------- #
# Meta rule: keep the tool honest about its own (possibly broken) baseline.
# --------------------------------------------------------------------------- #


def _suspicious_baseline(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a recorded baseline that a role invariant would itself call unhealthy.

    Concretely, ``expected.isis_adjacencies == 0``: that value was learned
    from a fabric that was already broken when ``learn-topology`` ran (see
    ``inventory/lab.yaml``'s caution comment). This never depends on freshly
    observed evidence -- it is a statement about the inventory itself -- so it
    is not gated on any intent's parse status.
    """

    expected = ctx.device.expected
    if expected is None or expected.isis_adjacencies != 0:
        return []
    return [
        {
            "intent": None,
            "message": (
                f"Recorded baseline for {ctx.device.name} encodes "
                "isis_adjacencies=0, a broken state captured at learn-topology "
                "time -- do not treat it as a healthy target."
            ),
            "expected": ">0 (role invariant)",
            "actual": 0,
            "subject": None,
        }
    ]


# --------------------------------------------------------------------------- #
# The rule table itself. Adding a rule is: write a small function above
# returning finding fragments (message/expected/actual/subject/intent), then
# add one line below naming it, its severity, and which table it belongs to.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    fn: Callable[[RuleContext], list[dict[str, Any]]]


ROLE_INVARIANT_RULES = (
    Rule("isis_isolated", "critical", _isis_isolated),
    Rule("isis_adjacency_not_up", "critical", _isis_adjacency_not_up),
    Rule("bgp_session_down", "critical", _bgp_session_down),
    Rule("bgp_process_absent", "warning", _bgp_process_absent),
    Rule("bgp_no_prefixes", "info", _bgp_no_prefixes),
    Rule("interface_admin_up_line_down", "warning", _interface_admin_up_line_down),
    Rule("sr_policy_down", "warning", _sr_policy_down),
)

BASELINE_RULES = (
    Rule("isis_adjacency_count_drift", "warning", _isis_adjacency_count_drift),
    Rule("bgp_peer_count_drift", "warning", _bgp_peer_count_drift),
)

META_RULES: tuple[Rule, ...] = (Rule("suspicious_baseline", "warning", _suspicious_baseline),)

ALL_RULES: tuple[Rule, ...] = ROLE_INVARIANT_RULES + BASELINE_RULES + META_RULES


# --------------------------------------------------------------------------- #
# Evaluation: one device, then a whole fabric roll-up.
# --------------------------------------------------------------------------- #


def evaluate_device(evidence: dict[str, Any], device: Device) -> dict[str, Any]:
    """Evaluate one device's collected evidence into a health verdict.

    Runs every rule in ``ALL_RULES`` against the same ``RuleContext``; each
    rule already guards its own required intent(s), so a failed/unsupported
    intent simply yields no findings for the rules that need it -- it is
    listed in ``unevaluated`` instead of being silently read as healthy.
    """

    ctx = _build_context(evidence, device)

    findings: list[dict[str, Any]] = []
    for rule in ALL_RULES:
        for raw in rule.fn(ctx):
            findings.append(
                {
                    "rule": rule.name,
                    "severity": rule.severity,
                    "intent": raw.get("intent"),
                    "message": raw["message"],
                    "expected": raw.get("expected"),
                    "actual": raw.get("actual"),
                    "subject": raw.get("subject"),
                }
            )

    # Classify why each health intent could not be evaluated, then let genuine
    # failures raise severity. Without this a totally unreachable device scores
    # "ok" with exit code 0 -- every rule guards its own intent, so an empty
    # collection simply produces no findings, and silence reads as health. That
    # is the single most dangerous outcome this module can produce.
    reasons = {intent: _uneval_reason(evidence, intent) for intent in _HEALTH_INTENTS}
    unsupported = sorted(i for i, r in reasons.items() if r == UNEVAL_UNSUPPORTED)
    failed_collection = sorted(i for i, r in reasons.items() if r == UNEVAL_COLLECTION_FAILED)
    failed_parse = sorted(i for i, r in reasons.items() if r == UNEVAL_PARSE_FAILED)
    unevaluated = sorted(failed_collection + failed_parse)

    if failed_collection and not any(reasons[i] is None for i in _HEALTH_INTENTS):
        # Nothing at all could be read: treat the device as down, not healthy.
        findings.append(
            {
                "rule": "device_unreachable",
                "severity": "critical",
                "intent": None,
                "message": (
                    "no intent could be collected; the device is unreachable or "
                    "every command failed, so its health is unknown -- not ok"
                ),
                "expected": "at least one intent collected",
                "actual": f"all {len(_HEALTH_INTENTS)} health intents failed",
                "subject": None,
            }
        )
    else:
        for intent in failed_collection:
            findings.append(
                {
                    "rule": "intent_collection_failed",
                    "severity": "warning",
                    "intent": intent,
                    "message": f"{intent} could not be collected, so it was not assessed",
                    "expected": "collected",
                    "actual": "error",
                    "subject": None,
                }
            )
    for intent in failed_parse:
        findings.append(
            {
                "rule": "intent_unparsed",
                "severity": "warning",
                "intent": intent,
                "message": f"{intent} returned output that could not be parsed, so it was not assessed",
                "expected": "parseable output",
                "actual": "parse failed",
                "subject": None,
            }
        )

    counts = {"critical": 0, "warning": 0, "info": 0}
    severity = "ok"
    for finding in findings:
        severity = _max_severity(severity, finding["severity"])
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1

    platform = str(evidence.get("platform") or device.platform or DEFAULT_PLATFORM)
    return {
        "device": device.name,
        "role": device.role,
        "platform": platform,
        "severity": severity,
        "findings": findings,
        "counts": counts,
        # Split deliberately: "unevaluated" is what we failed to assess and is
        # reflected in severity; "unsupported" is what this platform has no
        # command for, which is benign and must never move severity.
        "unevaluated": unevaluated,
        "unsupported": unsupported,
    }


def evaluate_fabric(
    evidence_by_device: dict[str, dict[str, Any]],
    devices: list[Device] | None = None,
) -> dict[str, Any]:
    """Evaluate every device with evidence, and roll up to one fabric severity.

    ``devices`` defaults to the resolved inventory (``load_inventory_file()``),
    so the common case needs only an evidence dict. Devices are visited in
    inventory order for deterministic output; a device with evidence but no
    matching inventory entry (e.g. one already removed from the YAML) is
    skipped rather than guessed at.
    """

    if devices is None:
        devices = load_inventory_file().devices

    device_verdicts: dict[str, Any] = {}
    fabric_severity = "ok"
    for device in devices:
        evidence = evidence_by_device.get(device.name)
        if evidence is None:
            continue
        verdict = evaluate_device(evidence, device)
        device_verdicts[device.name] = verdict
        fabric_severity = _max_severity(fabric_severity, verdict["severity"])

    # Fabric-level roll-up: how many devices sit at each severity, and how many
    # had an intent that could not be evaluated. At fleet scale the per-device
    # list is unreadable, so this line is what an operator or a cron job reads.
    by_severity: dict[str, int] = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    for verdict in device_verdicts.values():
        by_severity[verdict["severity"]] += 1

    return {
        "severity": fabric_severity,
        "counts": {
            "devices": len(device_verdicts),
            "by_severity": by_severity,
            "unevaluated_devices": sorted(
                name for name, verdict in device_verdicts.items() if verdict["unevaluated"]
            ),
        },
        "devices": device_verdicts,
    }
