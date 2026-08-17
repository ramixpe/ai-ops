"""Render a result payload as JSON (default), a table, or a one-line summary.

Why this exists
-----------------
Every CLI command through Phase 7 does ``print(json.dumps(result, indent=2))``.
That is fine for one device, and unreadable for a thousand: a fabric-wide
``nettools fabric bgp`` or ``nettools health --all`` against a real fleet
returns one JSON object per device, and a human skimming a terminal wants a
table of outcomes, not a multi-thousand-line document.

**This module never invents or softens data.** It only reformats whatever the
underlying tool/health call already returned; the *exit code* a CLI command
returns is always computed from the full, unfiltered result object before
rendering (see ``cli.py``), never from a table/summary view -- so
``--format summary`` on a critical fabric still exits non-zero, exactly as
``--format json`` would. Nothing here decides severity, drops an error, or
rounds a status into something friendlier; it only chooses how many columns
of the same facts to print.

Recognized shapes
--------------------
The result payloads this package produces come in a small number of
recurring shapes, listed here so ``render_table``/``render_summary`` stay
readable as a short dispatch rather than one large special-cased blob:

- a **single-device tool envelope**: ``{tool, device, status, timestamp,
  data, errors}`` (every function in ``network_tools.py``).
- a **fabric envelope**: a tool envelope whose ``data`` carries a
  ``devices: {name: <tool envelope>}`` map (``check_fabric``).
- a **single-device health verdict**: ``{device, role, platform, severity,
  findings, counts, unevaluated, unsupported}`` (``health.evaluate_device``).
- a **fabric health result**: ``{severity, counts, devices: {name: <verdict>},
  ...}`` (``health.evaluate_fabric``, as shaped by ``cli._cmd_health``).
- a **diff result**: carries ``changed``/``unchanged``/``added``/``removed``/
  ``failed``/``recovered``/``unsupported`` lists (``diff_evidence``).
- an **inventory listing**: ``data.devices`` is a *list* of device dicts,
  not a map (``list_devices``).

Anything that matches none of these falls back to a flat key/value rendering
-- still a faithful, mechanical reformat of the same dict, never a guess at
structure that is not there.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

Format = str  # "json" | "table" | "summary"

FORMATS: tuple[str, ...] = ("json", "table", "summary")

# Worst-first, matching health.SEVERITY_ORDER's meaning but printed worst-first
# for a summary line, since that is what a human scanning output cares about.
SEVERITY_ORDER: tuple[str, ...] = ("critical", "warning", "info", "ok")


def render(payload: dict[str, Any], fmt: Format) -> str:
    """Render one payload in the requested format. ``fmt`` must be one of ``FORMATS``."""

    if fmt == "json":
        return json.dumps(payload, indent=2)
    if fmt == "table":
        return render_table(payload)
    if fmt == "summary":
        return render_summary(payload)
    raise ValueError(f"Unknown output format: {fmt!r}. Choose from {', '.join(FORMATS)}.")


# --------------------------------------------------------------------------- #
# Shape recognition, shared by both renderers.
# --------------------------------------------------------------------------- #


def _is_investigation(payload: dict[str, Any]) -> bool:
    """An `investigate` result: a descent, its rungs, and the model's work."""

    return payload.get("tool") == "investigate" and isinstance(payload.get("rungs"), list)


def _fabric_device_map(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A tool-envelope-shaped fabric result: ``data.devices`` is a *map*."""

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("devices"), dict):
        return data["devices"]
    return None


def _health_fabric_map(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A fabric health result: top-level ``devices`` map alongside ``severity``."""

    devices = payload.get("devices")
    if "severity" in payload and isinstance(devices, dict):
        return devices
    return None


def _is_single_health_verdict(payload: dict[str, Any]) -> bool:
    return "severity" in payload and "findings" in payload


def _is_diff_result(payload: dict[str, Any]) -> bool:
    return {"changed", "unchanged", "added", "removed"} <= payload.keys()


def _inventory_device_list(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("devices"), list):
        return data["devices"]
    return None


# --------------------------------------------------------------------------- #
# Table rendering: plain padded columns, no external dependency.
# --------------------------------------------------------------------------- #


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    lines = [format_row(headers), format_row(["-" * width for width in widths])]
    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def _compact(value: Any, limit: int = 80) -> str:
    """Render a nested value compactly for a table cell, truncated to stay one line."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_table(payload: dict[str, Any]) -> str:
    if _is_investigation(payload):
        return _render_investigation_table(payload)

    fabric_map = _fabric_device_map(payload)
    if fabric_map is not None:
        return _render_tool_device_table(fabric_map)

    health_map = _health_fabric_map(payload)
    if health_map is not None:
        return _render_health_device_table(health_map)

    if _is_single_health_verdict(payload):
        return _render_health_device_table({str(payload.get("device", "?")): payload})

    if _is_diff_result(payload):
        return _render_diff_table(payload)

    device_list = _inventory_device_list(payload)
    if device_list is not None:
        return _render_inventory_table(device_list)

    return _render_flat_table(payload)


def _render_tool_device_table(devices: dict[str, Any]) -> str:
    headers = ["DEVICE", "STATUS", "ERRORS"]
    rows = []
    for name, envelope in sorted(devices.items()):
        status = str(envelope.get("status", "?")) if isinstance(envelope, dict) else "?"
        errors = envelope.get("errors") if isinstance(envelope, dict) else None
        rows.append([name, status, _compact("; ".join(errors) if errors else "")])
    return _format_table(headers, rows)


def _render_health_device_table(verdicts: dict[str, Any]) -> str:
    headers = ["DEVICE", "SEVERITY", "FINDINGS", "UNEVALUATED"]
    rows = []
    for name, verdict in sorted(verdicts.items()):
        severity = str(verdict.get("severity", "?")).upper()
        findings = len(verdict.get("findings") or [])
        unevaluated = ",".join(verdict.get("unevaluated") or []) or "-"
        rows.append([name, severity, str(findings), unevaluated])
    return _format_table(headers, rows)


def _render_diff_table(payload: dict[str, Any]) -> str:
    headers = ["BUCKET", "INTENTS"]
    buckets = ("changed", "unchanged", "added", "removed", "failed", "recovered", "unsupported")
    rows = [
        [bucket.upper(), ", ".join(payload.get(bucket) or []) or "-"]
        for bucket in buckets
        if bucket in payload
    ]
    return _format_table(headers, rows)


def _render_inventory_table(devices: list[dict[str, Any]]) -> str:
    headers = ["NAME", "HOSTNAME", "PLATFORM"]
    rows = [
        [str(d.get("name", "?")), str(d.get("hostname", "?")), str(d.get("platform", "?"))]
        for d in devices
    ]
    return _format_table(headers, rows)


def _render_flat_table(payload: dict[str, Any]) -> str:
    """Fallback: one row per top-level key. Still a faithful, mechanical reformat."""

    headers = ["KEY", "VALUE"]
    rows = [[str(key), _compact(value)] for key, value in payload.items()]
    return _format_table(headers, rows)


# --------------------------------------------------------------------------- #
# Summary rendering: one line, still traceable back to the same facts.
# --------------------------------------------------------------------------- #


def render_summary(payload: dict[str, Any]) -> str:
    if _is_investigation(payload):
        return _render_investigation_summary(payload)

    health_map = _health_fabric_map(payload)
    if health_map is not None:
        by_severity = payload.get("counts", {}).get("by_severity", {})
        device_count = payload.get("counts", {}).get("devices", len(health_map))
        parts = " ".join(f"{severity}={by_severity.get(severity, 0)}" for severity in SEVERITY_ORDER)
        return (
            f"Fabric severity: {payload.get('severity', '?').upper()} "
            f"({device_count} device(s): {parts})"
        )

    if _is_single_health_verdict(payload):
        findings = len(payload.get("findings") or [])
        unevaluated = payload.get("unevaluated") or []
        suffix = f"; unevaluated={','.join(unevaluated)}" if unevaluated else ""
        return (
            f"{payload.get('device', '?')}: {payload.get('severity', '?').upper()} "
            f"({findings} finding(s){suffix})"
        )

    fabric_map = _fabric_device_map(payload)
    if fabric_map is not None:
        by_status = Counter(
            envelope.get("status", "?") if isinstance(envelope, dict) else "?"
            for envelope in fabric_map.values()
        )
        parts = ", ".join(f"{count} {status}" for status, count in sorted(by_status.items()))
        check = payload.get("data", {}).get("check", payload.get("tool", "fabric"))
        return f"{check}: {len(fabric_map)} device(s) -> {parts}"

    if _is_diff_result(payload):
        counts = {
            bucket: len(payload.get(bucket) or [])
            for bucket in (
                "changed",
                "unchanged",
                "added",
                "removed",
                "failed",
                "recovered",
                "unsupported",
            )
        }
        parts = ", ".join(f"{value} {key}" for key, value in counts.items())
        return f"{payload.get('device', '?')}: {parts}"

    device_list = _inventory_device_list(payload)
    if device_list is not None:
        return f"{len(device_list)} device(s) in inventory"

    if "status" in payload:
        errors = payload.get("errors") or []
        suffix = f" ({len(errors)} error(s))" if errors else ""
        return f"{payload.get('tool', '?')} {payload.get('device', '?')}: {payload['status']}{suffix}"

    return json.dumps(payload, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# The investigation result. The causal chain is the product, so both renderers
# put it first -- a finding without its chain is an assertion where the descent
# produced an argument (`prompts/README.md`).
# --------------------------------------------------------------------------- #


def _render_investigation_table(payload: dict[str, Any]) -> str:
    """The rungs top-down, then what happened to the model's work.

    Deliberately the *whole* ladder rather than just the broken rungs. A
    healthy rung below a broken one is what tells a reader the descent went
    past it and found nothing -- omitting it would make "the cause is here"
    look like "we stopped looking here".
    """

    cause = payload.get("cause") or {}
    rows = []
    for rung in payload.get("rungs") or []:
        marker = "<-- CAUSE" if (
            rung.get("rung") == cause.get("rung")
            and rung.get("device") == cause.get("device")
        ) else ""
        rows.append([
            str(rung.get("rung", "?")),
            str(rung.get("device", "?")),
            str(rung.get("status", "?")).upper(),
            _compact(str(rung.get("reason") or "")),
            marker,
        ])

    table = _format_table(["RUNG", "DEVICE", "STATUS", "REASON", ""], rows)

    lines = [
        f"{payload.get('flow', '?')}: {payload.get('device', '?')} -> "
        f"{payload.get('subject', '?')}",
        f"FINDING: {payload.get('finding', '?')}"
        + (f" on {cause['device']}" if cause.get("device") else ""),
        "",
        table,
    ]

    off_path = payload.get("off_path") or []
    if off_path:
        lines.append("")
        lines.append(
            "NO FAULT ON THE PATH between "
            f"{payload.get('device', '?')} and {payload.get('subject', '?')}. "
            f"{len(off_path)} broken rung(s) recorded as observations, not as a cause:"
        )
        for entry in off_path:
            lines.append(
                f"  - {entry.get('rung', '?')} on {entry.get('device', '?')}: "
                f"{_compact(str(entry.get('reason') or ''))}"
            )

    report = payload.get("report") or {}
    correlation = payload.get("correlation") or {}
    lines.append("")
    usage = payload.get("usage")
    if usage:
        lines.append(
            f"model:       {usage.get('calls', 0)} call(s), "
            + (
                f"{usage.get('total_tokens', 0)} tokens "
                f"({usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out)"
                if usage.get("reported")
                else "usage not reported by the provider"
            )
        )
    lines.append(f"report:      {report.get('status', '?')} (rendered from the descent)")
    lines.append(f"correlation: {correlation.get('status', '?')} (rendered from the descent)")
    # A model restatement no longer changes the exit code (B-439), so a rejected
    # one has to be legible here or it is not legible anywhere a person looks.
    for label, block in (("report", report), ("timeline", correlation)):
        para = block.get("paraphrase") or {}
        if para.get("status") and para["status"] != "not_attempted":
            lines.append(f"  {label} paraphrase: {para['status']} (non-authoritative)")
    if correlation.get("caveat"):
        lines.append(f"  caveat: {_compact(correlation['caveat'])}")
    coherence = payload.get("coherence") or {}
    if coherence.get("caveat"):
        lines.append(f"  caveat: {_compact(coherence['caveat'])}")
    if not payload.get("trustworthy", True):
        lines.append("  NOT TRUSTWORTHY -- no answer was produced (exit 2)")
    return "\n".join(lines)


def _render_investigation_summary(payload: dict[str, Any]) -> str:
    """One line, and the chain survives it.

    A summary that printed only the finding would be the exact output
    `prompts/README.md` rules out, in the format most likely to be pasted into
    a ticket. The chain is rendered as arrows so it stays one line.
    """

    cause = payload.get("cause") or {}
    chain = payload.get("causal_chain") or []
    where = f" on {cause['device']}" if cause.get("device") else ""

    head = (
        f"{payload.get('device', '?')} -> {payload.get('subject', '?')} "
        f"({payload.get('flow', '?')}): {payload.get('finding', '?')}{where}"
    )
    if chain and cause:
        links = " <- ".join(str(link.get("rung", "?")) for link in chain)
        head += f" [{links} <- {cause.get('rung', '?')}]"
    elif chain:
        # No localised cause: the chain is what broke, not an explanation of it.
        head += " [broken: " + ", ".join(str(link.get("rung", "?")) for link in chain) + "]"

    off_path = payload.get("off_path") or []
    if off_path:
        # Never render this as "nothing found". Something IS broken; it is not
        # between these two endpoints, and saying only the first half would be
        # the opposite of the false positive B-428 removed.
        head += (
            f" [{len(off_path)} broken rung(s) off the path: "
            + ", ".join(f"{e.get('rung', '?')} on {e.get('device', '?')}" for e in off_path)
            + "]"
        )

    correlation = payload.get("correlation") or {}
    para = (correlation.get("paraphrase") or {}).get("status")
    if para == "coverage_limited":
        head += " (timeline coverage_limited; finding unaffected)"
    elif para == "withheld":
        head += " (timeline paraphrase withheld; finding unaffected)"
    if (payload.get("coherence") or {}).get("status") == "window_limited":
        head += " (window_limited; read as true at both ends, not throughout)"
    if not payload.get("trustworthy", True):
        head += " -- NOT TRUSTWORTHY"
    return head
