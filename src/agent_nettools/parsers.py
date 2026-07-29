"""Turn raw command output into structured records.

Why local parsers instead of ntc-templates / TextFSM
----------------------------------------------------
Measured against the committed fixtures (real XRd 7.11.2 output), ntc-templates
handles 3 of the 7 approved IOS-XR commands:

===========================================  ==================================
command                                      ntc-templates result
===========================================  ==================================
``show running-config hostname``             parses
``show lldp neighbors``                      parses
``show isis neighbors``                      parses
``show version``                             TextFSMError on XRd banner
``show bgp summary``                         TextFSMError on nexthop policy line
``show segment-routing traffic-eng policy``  no template exists
``show interfaces brief``                    **no error, zero records**
===========================================  ==================================

The last row is the reason this module exists rather than wrapping the library.
A parser that returns nothing from non-empty output, without raising, is worse
than one that fails: a health rule reading "no interfaces" concludes "nothing is
down". Two of the failures come from XRd's containerised output differing from
the hardware IOS-XR the templates target, so the coverage gap is not incidental.

These parsers are small, deliberately strict, and tested against real captured
output. A parse that yields neither records nor meta from non-empty input is
reported as a **failure**, never as an empty success.

Contract
--------
``parse_intent(platform, intent, outputs) -> ParseResult | None`` returns None
when no parser is defined for that platform and intent, which is not an error --
callers fall back to normalized text (see ``normalize.py``).

Each parser returns ``{"meta": {...}, "records": [...]}``. ``meta`` holds scalar
facts about the device or protocol instance; ``records`` holds table rows.
``RECORD_KEYS`` names the field identifying a record so diffing can match rows
across snapshots, and ``VOLATILE_FIELDS`` names fields that move on their own and
must be excluded from change detection.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .normalize import strip_preamble

# Fields that change without anything happening. Measured from the t0/t1 fixture
# pair rather than assumed -- notably LLDP "hold_time" is NOT here: it is the
# advertised TTL and stays at 120, so excluding it would discard real signal.
VOLATILE_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("cisco_xr", "facts"): frozenset({"uptime"}),
    ("cisco_xr", "bgp"): frozenset({"msg_rcvd", "msg_sent", "up_down"}),
    ("cisco_xr", "isis"): frozenset({"holdtime"}),
    ("cisco_xr", "sr"): frozenset({"operational_duration"}),
    ("cisco_xr", "interfaces"): frozenset(),
    ("cisco_xr", "lldp"): frozenset(),
}

# The field identifying a record within an intent, so two snapshots' rows can be
# matched up. None means the intent has no table, only meta.
RECORD_KEYS: dict[tuple[str, str], str | None] = {
    ("cisco_xr", "facts"): None,
    ("cisco_xr", "interfaces"): "interface",
    ("cisco_xr", "bgp"): "neighbor",
    ("cisco_xr", "lldp"): "local_interface",
    ("cisco_xr", "isis"): "system_id",
    ("cisco_xr", "sr"): "policy",
}


class ParseError(ValueError):
    """Raised by a parser that cannot make sense of non-empty output."""


def _nonblank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _after_header(lines: list[str], header_match: Callable[[str], bool]) -> list[str]:
    """Return the lines following the first line matching ``header_match``.

    Also skips an immediately following separator line of dashes, which IOS-XR
    uses under some table headers but not others.
    """

    for index, line in enumerate(lines):
        if header_match(line):
            rest = lines[index + 1 :]
            if rest and set(rest[0].strip()) <= {"-"} and rest[0].strip():
                rest = rest[1:]
            return rest
    return []


# --------------------------------------------------------------------------- #
# cisco_xr parsers
# --------------------------------------------------------------------------- #

_XR_VERSION = re.compile(r"^Cisco IOS XR Software, Version (?P<version>\S+)(?: (?P<flavor>\S+))?")
_XR_UPTIME = re.compile(r"^(?P<host>\S+) uptime is (?P<uptime>.+)$")
_XR_HOSTNAME = re.compile(r"^hostname (?P<hostname>\S+)")


def parse_xr_facts(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show running-config hostname`` + ``show version``."""

    meta: dict[str, Any] = {}
    for text in outputs.values():
        for line in _nonblank_lines(text):
            stripped = line.strip()
            if match := _XR_HOSTNAME.match(stripped):
                meta["hostname"] = match["hostname"]
            elif match := _XR_VERSION.match(stripped):
                meta["version"] = match["version"]
                if match["flavor"]:
                    meta["flavor"] = match["flavor"]
            elif match := _XR_UPTIME.match(stripped):
                meta.setdefault("hostname", match["host"])
                meta["uptime"] = match["uptime"]
            elif stripped.startswith("cisco ") and "processor" in stripped:
                meta["hardware"] = stripped

    if not meta:
        raise ParseError("no hostname, version, or uptime found")
    return {"meta": meta, "records": []}


def parse_xr_interfaces(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show interfaces brief``.

    Columns: Intf Name / Intf State / LineP State / Encap Type / MTU / BW.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)
    rows = _after_header(lines, lambda line: set(line.strip()) == {"-"})

    records = []
    for line in rows:
        fields = line.split()
        if len(fields) != 6:
            continue
        name, state, line_protocol, encapsulation, mtu, bandwidth = fields
        records.append(
            {
                "interface": name,
                "admin_state": state,
                "line_protocol": line_protocol,
                "encapsulation": encapsulation,
                "mtu": mtu,
                "bandwidth_kbps": bandwidth,
            }
        )

    if not records:
        raise ParseError("no interface rows found")
    return {"meta": {"interface_count": len(records)}, "records": records}


_XR_BGP_META = re.compile(
    r"^BGP router identifier (?P<router_id>\S+), local AS number (?P<local_as>\d+)"
)
# A router with no BGP process configured (the P-routers in this lab, which only
# speak IS-IS) answers with this message instead of the usual summary block. That
# is a real, legitimate device state -- not a parse failure.
_XR_BGP_INACTIVE = re.compile(r"^% BGP instance '(?P<instance>[^']*)' not active")


def parse_xr_bgp(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show bgp summary``.

    Only well-formed 10-column neighbour rows are captured. IOS-XR wraps long
    (IPv6) neighbour addresses onto their own line; such rows are counted as
    skipped rather than silently dropped, so a partial parse is visible.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)

    meta: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if match := _XR_BGP_META.match(stripped):
            meta["router_id"] = match["router_id"]
            meta["local_as"] = match["local_as"]
            break
        if match := _XR_BGP_INACTIVE.match(stripped):
            meta["active"] = False
            meta["instance"] = match["instance"]
            break

    rows = _after_header(lines, lambda line: line.strip().startswith("Neighbor "))
    records = []
    skipped = 0
    for line in rows:
        fields = line.split()
        if len(fields) != 10:
            skipped += 1
            continue
        records.append(
            {
                "neighbor": fields[0],
                "spk": fields[1],
                "remote_as": fields[2],
                "msg_rcvd": fields[3],
                "msg_sent": fields[4],
                "table_version": fields[5],
                "in_q": fields[6],
                "out_q": fields[7],
                "up_down": fields[8],
                "state_pfx_rcd": fields[9],
            }
        )

    if not meta and not records:
        raise ParseError("no BGP router identifier and no neighbour rows found")

    meta["neighbor_count"] = len(records)
    if skipped:
        meta["unparsed_rows"] = skipped
    return {"meta": meta, "records": records}


def parse_xr_lldp(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show lldp neighbors``.

    Columns: Device ID / Local Intf / Hold-time / Capability / Port ID. The
    capability-code legend above the table contains tabs and is skipped by
    anchoring on the "Device ID" header.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)
    header_found = any(line.strip().startswith("Device ID") for line in lines)
    if not header_found:
        # Zero records from unrecognized text is exactly the ntc-templates
        # failure mode this module exists to avoid (see the module docstring) --
        # a real zero-neighbour capture still prints the "Device ID" header.
        raise ParseError("no LLDP neighbor table header found")

    rows = _after_header(lines, lambda line: line.strip().startswith("Device ID"))
    records = []
    for line in rows:
        if line.strip().startswith("Total entries"):
            break
        fields = line.split()
        if len(fields) != 5:
            continue
        device_id, local_intf, hold_time, capability, port_id = fields
        records.append(
            {
                "local_interface": local_intf,
                "neighbor": device_id,
                "hold_time": hold_time,
                "capability": capability,
                "neighbor_interface": port_id,
            }
        )

    return {"meta": {"neighbor_count": len(records)}, "records": records}


def parse_xr_isis(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show isis neighbors``.

    Columns: System Id / Interface / SNPA / State / Holdtime / Type / IETF-NSF.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)

    meta: dict[str, Any] = {}
    for line in lines:
        if match := re.match(r"^IS-IS (?P<instance>\S+) neighbors:", line.strip()):
            meta["instance"] = match["instance"]
        elif match := re.match(r"^Total neighbor count: (?P<count>\d+)", line.strip()):
            meta["reported_neighbor_count"] = match["count"]

    header_found = any(line.strip().startswith("System Id") for line in lines)
    if not header_found:
        # As with LLDP above: a real zero-neighbour capture still prints the
        # "System Id" column header, so its absence means unrecognized input.
        raise ParseError("no IS-IS neighbor table header found")

    rows = _after_header(lines, lambda line: line.strip().startswith("System Id"))
    records = []
    for line in rows:
        if line.strip().startswith("Total neighbor count"):
            break
        fields = line.split()
        if len(fields) != 7:
            continue
        system_id, interface, snpa, state, holdtime, kind, nsf = fields
        records.append(
            {
                "system_id": system_id,
                "interface": interface,
                "snpa": snpa,
                "state": state,
                "holdtime": holdtime,
                "type": kind,
                "ietf_nsf": nsf,
            }
        )

    meta["neighbor_count"] = len(records)
    return {"meta": meta, "records": records}


_XR_SR_HEADER = re.compile(r"^Color: (?P<color>\d+), End-point: (?P<endpoint>\S+)")
_XR_SR_STATUS = re.compile(
    r"^Admin: (?P<admin>\S+)\s+Operational: (?P<operational>\S+)"
    r"(?: for (?P<duration>\S+))?(?: \(since (?P<since>[^)]+)\))?"
)


def parse_xr_sr(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show segment-routing traffic-eng policy``.

    The output is a nested block per policy rather than a table. ``since`` is an
    absolute timestamp and is kept as the stable signal; the ``up for``/``down
    for`` duration beside it is volatile and excluded from diffing.
    """

    text = next(iter(outputs.values()), "")
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if match := _XR_SR_HEADER.match(line):
            current = {
                "policy": f"{match['color']}:{match['endpoint']}",
                "color": match["color"],
                "endpoint": match["endpoint"],
            }
            records.append(current)
        elif current is None:
            continue
        elif line.startswith("Name: "):
            current.setdefault("name", line[len("Name: ") :])
        elif match := _XR_SR_STATUS.match(line):
            current["admin_state"] = match["admin"]
            current["operational_state"] = match["operational"]
            if match["duration"]:
                current["operational_duration"] = match["duration"]
            if match["since"]:
                current["operational_since"] = match["since"]
        elif line.startswith("Binding SID: "):
            current.setdefault("binding_sid", line[len("Binding SID: ") :])

    # An empty SR-TE database is legitimate: no policies configured. Measured
    # against real captures, a device with none skips the header entirely and
    # answers with nothing but the timestamp preamble -- so "empty" is judged
    # after stripping that preamble, not by requiring the header to be present.
    if not records:
        body = strip_preamble(text, platform="cisco_xr").strip()
        if body and "SR-TE policy database" not in body:
            raise ParseError("no SR-TE policy database header and no policies found")
    return {"meta": {"policy_count": len(records)}, "records": records}


PARSERS: dict[tuple[str, str], Callable[[dict[str, str]], dict[str, Any]]] = {
    ("cisco_xr", "facts"): parse_xr_facts,
    ("cisco_xr", "interfaces"): parse_xr_interfaces,
    ("cisco_xr", "bgp"): parse_xr_bgp,
    ("cisco_xr", "lldp"): parse_xr_lldp,
    ("cisco_xr", "isis"): parse_xr_isis,
    ("cisco_xr", "sr"): parse_xr_sr,
}

# Parse outcome recorded alongside the data.
PARSE_OK = "ok"
PARSE_UNAVAILABLE = "unavailable"  # no parser for this platform/intent
PARSE_FAILED = "failed"  # parser could not read non-empty output


def has_parser(platform: str, intent: str) -> bool:
    return (platform, intent) in PARSERS


def volatile_fields(platform: str, intent: str) -> frozenset[str]:
    return VOLATILE_FIELDS.get((platform, intent), frozenset())


def record_key(platform: str, intent: str) -> str | None:
    return RECORD_KEYS.get((platform, intent))


def parse_intent(
    platform: str,
    intent: str,
    outputs: dict[str, str],
) -> tuple[dict[str, Any] | None, str]:
    """Parse one intent's command outputs.

    Returns ``(parsed, status)``. ``status`` is one of ``PARSE_OK``,
    ``PARSE_UNAVAILABLE`` (no parser defined), or ``PARSE_FAILED``. A parser that
    raises, or that returns neither records nor meta from non-empty output, is a
    failure -- never a silent empty success.
    """

    parser = PARSERS.get((platform, intent))
    if parser is None:
        return None, PARSE_UNAVAILABLE
    if not any(text.strip() for text in outputs.values()):
        return None, PARSE_FAILED

    try:
        parsed = parser(dict(outputs))
    except ParseError:
        return None, PARSE_FAILED
    except Exception:  # noqa: BLE001 - a parser bug must not break collection.
        return None, PARSE_FAILED

    if not parsed.get("records") and not parsed.get("meta"):
        return None, PARSE_FAILED
    return parsed, PARSE_OK
