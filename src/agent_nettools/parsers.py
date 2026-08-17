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

Line accounting (B-404) -- BUILD-PLAN.md section 0.10
-------------------------------------------------------
These six parsers predate the completeness rule ``template_parsers.py`` was
built with, and section 0.10 explicitly deferred them rather than have them
rewritten as a side effect of an unrelated task ("It does **not** apply
retroactively to the six hand-written parsers in ``parsers.py`` -- do not
rewrite working, tested code. Log a finding..."). B-404 is that deliberately
scheduled retrofit.

Every parser below now accounts for every non-blank line of its command's
output as exactly one of: turned into a record or a ``meta`` field, or matched
by a declared ``<INTENT>_IGNORES`` rule -- and, exactly as
``template_parsers.py`` does it, using the *same* ``IgnoreRule``/``IgnoreKind``/
``XR_COMMON_IGNORES``/``account_lines``/``finalize`` machinery, imported from
there rather than re-declared here. Anything neither captured nor declared
surfaces in ``meta["unaccounted_lines"]`` instead of silently vanishing.

This is additive only: no record shape and no existing meta field's value
changes here. ``meta["unparsed_rows"]`` (previously present on ``bgp`` only,
and only when non-zero) is now present on every intent's meta, defaulting to
``0`` like ``template_parsers.finalize`` already does everywhere else --
the one deliberate normalisation, not a change to what any row actually does.

A note on the resulting import cycle
-------------------------------------
Reusing ``template_parsers.py``'s accounting primitives here, while
``template_parsers.py`` already imports this module's status vocabulary
(``PARSE_OK``/``PARSE_UNAVAILABLE``/``PARSE_FAILED``/``ParseError`` -- "one
status vocabulary in this package, not two", per that module's docstring)
means the two modules now import each other. That is only safe because of
where each cross-import sits: this module defines ``ParseError`` and the three
``PARSE_*`` constants *before* reaching into ``template_parsers``, and
``template_parsers.py`` defines ``IgnoreKind``/``IgnoreRule``/
``XR_COMMON_IGNORES``/``account_lines``/``finalize`` *before* reaching back
into this module -- so whichever of the two a caller imports first, the names
it needs from the other are already bound by the time it asks for them. See
the matching comments at both import sites.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .normalize import strip_preamble

# Parse outcome recorded alongside the data. Defined early -- along with
# ParseError just below -- so both are available before the cross-import a
# few lines down needs them. See the module docstring's note on the import
# cycle this creates with template_parsers.py.
PARSE_OK = "ok"
PARSE_UNAVAILABLE = "unavailable"  # no parser for this platform/intent
PARSE_FAILED = "failed"  # parser could not read non-empty output


class ParseError(ValueError):
    """Raised by a parser that cannot make sense of non-empty output."""


# B-404: reuse template_parsers' section-0.10 accounting machinery rather than
# re-declare it here ("one accounting mechanism, not two" -- the same reasoning
# that keeps the PARSE_* vocabulary singular). Safe only because everything
# template_parsers.py needs from this module (above) is already bound, and
# everything imported here is defined in template_parsers.py *before* that
# module reaches back into this one -- see the module docstring.
from .template_parsers import (  # noqa: E402 - see the ordering note in the module docstring
    IgnoreKind,
    IgnoreRule,
    finalize,
)

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

# Section 0.10 accounting for ``show running-config hostname`` + ``show
# version``. Surveyed across all 36 committed fixtures: the "Build
# Information:" block (a fixed six field: value lines) and the surrounding
# banners are byte-identical on every device and every label -- this is a
# single containerised XRd image, not per-device hardware -- so a handful of
# narrow, literal-shaped rules covers all of it.
FACTS_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^Copyright \(c\) \d{4}-\d{4} by Cisco Systems, Inc\.$",
        "copyright banner",
    ),
    IgnoreRule(r"^Build Information:$", "build-info section header"),
    IgnoreRule(
        r"^Built By\s+:\s+\S+$",
        "XRd image build-info field (who built the image), not required by the schema",
    ),
    IgnoreRule(
        r"^Built On\s+:\s+.+$",
        "XRd image build-info field (build timestamp), not required by the schema",
    ),
    IgnoreRule(
        r"^Build Host\s+:\s+\S+$",
        "XRd image build-info field (build host), not required by the schema",
    ),
    IgnoreRule(
        r"^Workspace\s+:\s+\S+$",
        "XRd image build-info field (build workspace path), not required by the schema",
    ),
    IgnoreRule(
        r"^Version\s+:\s+\S+$",
        "XRd image build-info field restating the software version already captured from the "
        "'Cisco IOS XR Software, Version' line, not required by the schema",
    ),
    IgnoreRule(
        r"^Label\s+:\s+\S+$",
        "XRd image build-info field (release label, same value as version in this lab), "
        "not required by the schema",
    ),
    IgnoreRule(
        r"^cisco XRd Control Plane$",
        "platform-family banner line; the following 'cisco ... processor ...' line is "
        "captured as hardware instead",
    ),
    IgnoreRule(
        r"^XRd Control Plane Container$",
        "trailing container-runtime banner, not required by the schema",
    ),
)


def parse_xr_facts(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show running-config hostname`` + ``show version``."""

    meta: dict[str, Any] = {}
    consumed: list[str] = []
    for text in outputs.values():
        for line in _nonblank_lines(text):
            stripped = line.strip()
            if match := _XR_HOSTNAME.match(stripped):
                meta["hostname"] = match["hostname"]
                consumed.append(stripped)
            elif match := _XR_VERSION.match(stripped):
                meta["version"] = match["version"]
                if match["flavor"]:
                    meta["flavor"] = match["flavor"]
                consumed.append(stripped)
            elif match := _XR_UPTIME.match(stripped):
                meta.setdefault("hostname", match["host"])
                meta["uptime"] = match["uptime"]
                consumed.append(stripped)
            elif stripped.startswith("cisco ") and "processor" in stripped:
                meta["hardware"] = stripped
                consumed.append(stripped)

    if not meta:
        raise ParseError("no hostname, version, or uptime found")

    # Two commands, two outputs -- account_lines wants one string, so the raw
    # texts are joined in the same order collect_evidence renders them in
    # (``outputs`` is built in command order). Each still carries its own
    # IOS-XR timestamp banner, which XR_COMMON_IGNORES matches independently
    # per occurrence.
    raw = "\n".join(outputs.values())
    return finalize(raw=raw, meta=meta, records=[], consumed=consumed, ignores=FACTS_IGNORES)


# Section 0.10 accounting for ``show interfaces brief``. The two-line column
# header and its dashes separator sit *before* the anchor `_after_header`
# matches on, so none of the three is ever touched by the row loop below.
INTERFACES_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^Intf\s+Intf\s+LineP\s+Encap\s+MTU\s+BW$",
        "first line of the two-line column header, decorative",
    ),
    IgnoreRule(
        r"^Name\s+State\s+State\s+Type\s+\(byte\)\s+\(Kbps\)$",
        "second line of the two-line column header, decorative",
    ),
    IgnoreRule(r"^-+$", "dashes separating the column header from the interface rows"),
)


def parse_xr_interfaces(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show interfaces brief``.

    Columns: Intf Name / Intf State / LineP State / Encap Type / MTU / BW.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)
    rows = _after_header(lines, lambda line: set(line.strip()) == {"-"})

    records = []
    consumed: list[str] = []
    unparsed_rows = 0
    for line in rows:
        stripped = line.strip()
        fields = stripped.split()
        if len(fields) != 6:
            # A known row shape that did not fit -- not seen in any committed
            # fixture, but still consumed rather than left to surface as
            # unaccounted, matching how the row-based intents below all treat
            # a malformed row of an otherwise-recognised table.
            unparsed_rows += 1
            consumed.append(stripped)
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
        consumed.append(stripped)

    if not records:
        raise ParseError("no interface rows found")

    return finalize(
        raw=text,
        meta={"interface_count": len(records)},
        records=records,
        consumed=consumed,
        ignores=INTERFACES_IGNORES,
        unparsed_rows=unparsed_rows,
    )


_XR_BGP_META = re.compile(
    r"^BGP router identifier (?P<router_id>\S+), local AS number (?P<local_as>\d+)"
)
# A router with no BGP process configured (the P-routers in this lab, which only
# speak IS-IS) answers with this message instead of the usual summary block. That
# is a real, legitimate device state -- not a parse failure.
_XR_BGP_INACTIVE = re.compile(r"^% BGP instance '(?P<instance>[^']*)' not active")

# Section 0.10 accounting for ``show bgp summary``. Everything here is process-
# or table-level bookkeeping the 10-column neighbour table and the router-id
# line above it do not already carry. ``BGP table state`` is the one line
# marked deferred rather than permanent: every fixture on disk shows "Active",
# but a non-Active value would be exactly the kind of process-health signal
# nothing currently reads.
BGP_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^BGP generic scan interval \d+ secs$",
        "configured scan-interval setting, not required by the schema",
    ),
    IgnoreRule(r"^Non-stop routing is enabled$", "NSR flag, not required by the schema"),
    IgnoreRule(
        r"^BGP table state: \S+$",
        "table-level state flag; only 'Active' has been observed in this lab, but a "
        "non-Active value would be a real process-health signal nothing currently reads",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Table ID: \S+\s+RD version: \d+$",
        "internal table identifier and RD-version counter, not required by the schema",
    ),
    IgnoreRule(
        r"^BGP table nexthop route policy:$",
        "configured nexthop route-policy name, empty in this lab",
    ),
    IgnoreRule(
        r"^BGP main routing table version \d+$",
        "table-version bookkeeping counter, not required by the schema",
    ),
    IgnoreRule(
        r"^BGP NSR Initial initsync version \d+ \(\S+\)$",
        "non-stop-routing initial-sync bookkeeping, not required by the schema",
    ),
    IgnoreRule(
        r"^BGP NSR/ISSU Sync-Group versions \S+$",
        "non-stop-routing/ISSU sync-group bookkeeping, not required by the schema",
    ),
    IgnoreRule(
        r"^BGP scan interval \d+ secs$",
        "configured scan-interval setting, not required by the schema",
    ),
    IgnoreRule(
        r"^BGP is operating in \S+ mode\.$",
        "redundancy-mode banner, not required by the schema",
    ),
    IgnoreRule(
        r"^Process\s+RcvTblVer\s+bRIB/RIB\s+LabelVer\s+ImportVer\s+SendTblVer\s+StandbyVer$",
        "per-process table-version column header, decorative",
    ),
    IgnoreRule(
        r"^Speaker(?:\s+\d+){6}$",
        "per-process table-version counters, mirroring 'BGP main routing table version'; "
        "not required by the schema",
    ),
    IgnoreRule(
        r"^Neighbor\s+Spk\s+AS\s+MsgRcvd\s+MsgSent\s+TblVer\s+InQ\s+OutQ\s+Up/Down\s+St/PfxRcd$",
        "neighbour-table column header, decorative",
    ),
)


def _split_state_pfx_rcd(value: str) -> dict[str, Any]:
    """``St/PfxRcd`` into the two fields it actually holds (B-460).

    A numeric value means the session is Established and the number is the
    prefix count -- that is what the column means, and it is the discriminator
    every consumer was re-deriving with `_is_numeric`.

    `prefixes_received` is **absent, not zero**, when the session is not
    Established. Same rule Phase 3 applies to `router_id`/`local_as` for a
    device with no BGP process, and for the same reason: zero is a measurement,
    absence is not one.
    """

    text = (value or "").strip()
    if text.isdigit():
        return {"session_state": "Established", "prefixes_received": int(text)}
    return {"session_state": text}


def parse_xr_bgp(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show bgp summary``.

    Only well-formed 10-column neighbour rows are captured. IOS-XR wraps long
    (IPv6) neighbour addresses onto their own line; such rows are counted as
    skipped rather than silently dropped, so a partial parse is visible.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)

    meta: dict[str, Any] = {}
    consumed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if match := _XR_BGP_META.match(stripped):
            meta["router_id"] = match["router_id"]
            meta["local_as"] = match["local_as"]
            consumed.append(stripped)
            break
        if match := _XR_BGP_INACTIVE.match(stripped):
            meta["active"] = False
            meta["instance"] = match["instance"]
            consumed.append(stripped)
            break

    rows = _after_header(lines, lambda line: line.strip().startswith("Neighbor "))
    records = []
    skipped = 0
    for line in rows:
        stripped = line.strip()
        fields = stripped.split()
        if len(fields) != 10:
            skipped += 1
            consumed.append(stripped)
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
                # `St/PfxRcd` holds **either** a prefix count **or** a session
                # state, and which one depends on the state (B-460). A field
                # whose *type* depends on its value cannot be checked, diffed or
                # compared without every consumer re-deriving the discriminator
                # the CLI threw away -- `checks.py` did exactly that in three
                # places, and `diff_evidence` recorded a session going down as
                # `"0"` changing to `"Idle"`: one field, one value change. The
                # flap detector reads those same diffs, so a session bouncing
                # Established/Idle was counted as *a string oscillating* rather
                # than *a session flapping*.
                #
                # Split here, where the discriminator is still available.
                **_split_state_pfx_rcd(fields[9]),
            }
        )
        consumed.append(stripped)

    if not meta and not records:
        raise ParseError("no BGP router identifier and no neighbour rows found")

    meta["neighbor_count"] = len(records)

    return finalize(
        raw=text,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=BGP_IGNORES,
        unparsed_rows=skipped,
    )


# Section 0.10 accounting for ``show lldp neighbors``. "Total entries
# displayed: N" is the one line marked deferred: it is the device's own
# reported neighbour count, a real cross-check against ``neighbor_count``
# (derived by counting records) that nothing currently reads.
LLDP_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^Capability codes:$", "legend section header"),
    IgnoreRule(
        r"^\(R\) Router, \(B\) Bridge, \(T\) Telephone, \(C\) DOCSIS Cable Device$",
        "capability-code legend line",
    ),
    IgnoreRule(
        r"^\(W\) WLAN Access Point, \(P\) Repeater, \(S\) Station, \(O\) Other$",
        "capability-code legend line",
    ),
    IgnoreRule(
        r"^Device\s+ID\s+Local\s+Intf\s+Hold-time\s+Capability\s+Port\s+ID$",
        "column header, decorative",
    ),
    IgnoreRule(
        r"^Total entries displayed: \d+$",
        "the device's own reported neighbor count; neighbor_count is derived by counting "
        "records instead, and nothing currently cross-checks the two",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
)


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
    consumed: list[str] = []
    unparsed_rows = 0
    for line in rows:
        stripped = line.strip()
        if stripped.startswith("Total entries"):
            break
        fields = stripped.split()
        if len(fields) != 5:
            unparsed_rows += 1
            consumed.append(stripped)
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
        consumed.append(stripped)

    return finalize(
        raw=text,
        meta={"neighbor_count": len(records)},
        records=records,
        consumed=consumed,
        ignores=LLDP_IGNORES,
        unparsed_rows=unparsed_rows,
    )


# Section 0.10 accounting for ``show isis neighbors``. The column header is the
# only line the meta-capturing loop and the row loop both leave untouched --
# "IS-IS ... neighbors:" and "Total neighbor count: N" are already matched
# into meta by the first loop below.
ISIS_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^System\s+Id\s+Interface\s+SNPA\s+State\s+Holdtime\s+Type\s+IETF-NSF$",
        "column header, decorative",
    ),
)


def parse_xr_isis(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show isis neighbors``.

    Columns: System Id / Interface / SNPA / State / Holdtime / Type / IETF-NSF.
    """

    text = next(iter(outputs.values()), "")
    lines = _nonblank_lines(text)

    meta: dict[str, Any] = {}
    consumed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if match := re.match(r"^IS-IS (?P<instance>\S+) neighbors:", stripped):
            meta["instance"] = match["instance"]
            consumed.append(stripped)
        elif match := re.match(r"^Total neighbor count: (?P<count>\d+)", stripped):
            meta["reported_neighbor_count"] = match["count"]
            consumed.append(stripped)

    header_found = any(line.strip().startswith("System Id") for line in lines)
    if not header_found:
        # As with LLDP above: a real zero-neighbour capture still prints the
        # "System Id" column header, so its absence means unrecognized input.
        raise ParseError("no IS-IS neighbor table header found")

    rows = _after_header(lines, lambda line: line.strip().startswith("System Id"))
    records = []
    unparsed_rows = 0
    for line in rows:
        stripped = line.strip()
        if stripped.startswith("Total neighbor count"):
            break
        fields = stripped.split()
        if len(fields) != 7:
            unparsed_rows += 1
            consumed.append(stripped)
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
        consumed.append(stripped)

    meta["neighbor_count"] = len(records)
    return finalize(
        raw=text,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=ISIS_IGNORES,
        unparsed_rows=unparsed_rows,
    )


_XR_SR_HEADER = re.compile(r"^Color: (?P<color>\d+), End-point: (?P<endpoint>\S+)")
_XR_SR_STATUS = re.compile(
    r"^Admin: (?P<admin>\S+)\s+Operational: (?P<operational>\S+)"
    r"(?: for (?P<duration>\S+))?(?: \(since (?P<since>[^)]+)\))?"
)

# Section 0.10 accounting for ``show segment-routing traffic-eng policy``.
# Surveyed against the only fixtures on disk that ever carry a policy (PE1,
# all four labels -- every other device's SR-TE database is empty). Three
# lines are marked deferred: each carries a real per-candidate-path diagnostic
# (why a dynamic path is down, its computed SID stack, whether an explicit
# segment-list resolved) that nothing currently reads -- the SR-TE analogue of
# bgp_neighbor's state_reason/BFD-detail treatment. Everything else here is
# static configuration (protection type, steering flags, capability bits) or a
# section header/separator that never varies across the fixtures on disk.
SR_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^SR-TE policy database$", "database section header, decorative"),
    IgnoreRule(r"^-+$", "dashes separating the header from the first policy"),
    IgnoreRule(
        r"^Status:$",
        "status section header, decorative -- the 'Admin: ... Operational: ...' line under "
        "it is captured",
    ),
    IgnoreRule(r"^Candidate-paths:$", "candidate-paths section header, decorative"),
    IgnoreRule(
        r"^Preference: \d+ \(configuration\) \((?:active|inactive)\)$",
        "per-candidate-path preference value and active/inactive state; the policy-level "
        "admin/operational state is captured instead, not this per-path detail",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Requested BSID: \S+$",
        "per-path binding-SID request mode, not required by the schema",
    ),
    IgnoreRule(r"^Constraints:$", "path-constraints section header, decorative"),
    IgnoreRule(
        r"^Protection Type: \S+$",
        "configured path-protection type, not required by the schema",
    ),
    IgnoreRule(
        r"^Maximum SID Depth: \d+$",
        "configured max-SID-depth constraint, not required by the schema",
    ),
    IgnoreRule(
        r"^Explicit: segment-list \S+ \((?:valid|invalid)\)$",
        "explicit segment-list name and its validity; a real per-path diagnostic with no "
        "consumer yet",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Weight: \d+, Metric Type: \S+$",
        "path weight and configured metric type, not required by the schema",
    ),
    IgnoreRule(
        r"^SID\[\d+\]: \d+$",
        "one label of the explicit path's SID stack; real per-path diagnostic content with "
        "no consumer yet",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Dynamic \(inactive\)$",
        "a dynamic (computed, not explicit) candidate path that is currently inactive -- "
        "real diagnostic content with no consumer yet",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Last error: .+$",
        "the reason a dynamic path computation failed -- real diagnostic content with no "
        "consumer yet, the SR-TE analogue of bgp_neighbor's state_reason",
        kind=IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(
        r"^Metric Type: \S+,\s+Path Accumulated Metric: \d+$",
        "computed path metric detail, not required by the schema",
    ),
    IgnoreRule(r"^Attributes:$", "attributes section header, decorative"),
    IgnoreRule(r"^Forward Class: .+$", "configured forwarding class, not required by the schema"),
    IgnoreRule(
        r"^Steering labeled-services disabled: \S+$",
        "steering-policy flag, not required by the schema",
    ),
    IgnoreRule(
        r"^Steering BGP disabled: \S+$",
        "steering-policy flag, not required by the schema",
    ),
    IgnoreRule(r"^IPv6 caps enable: \S+$", "capability flag, not required by the schema"),
    IgnoreRule(
        r"^Invalidation drop enabled: \S+$",
        "configured invalidation-drop flag, not required by the schema",
    ),
    IgnoreRule(
        r"^Max Install Standby Candidate Paths: \d+$",
        "configured standby-path limit, not required by the schema",
    ),
)


def parse_xr_sr(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show segment-routing traffic-eng policy``.

    The output is a nested block per policy rather than a table. ``since`` is an
    absolute timestamp and is kept as the stable signal; the ``up for``/``down
    for`` duration beside it is volatile and excluded from diffing.
    """

    text = next(iter(outputs.values()), "")
    records: list[dict[str, Any]] = []
    consumed: list[str] = []
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
            consumed.append(line)
        elif current is None:
            continue
        elif line.startswith("Name: "):
            current.setdefault("name", line[len("Name: ") :])
            consumed.append(line)
        elif match := _XR_SR_STATUS.match(line):
            current["admin_state"] = match["admin"]
            current["operational_state"] = match["operational"]
            if match["duration"]:
                current["operational_duration"] = match["duration"]
            if match["since"]:
                current["operational_since"] = match["since"]
            consumed.append(line)
        elif line.startswith("Binding SID: "):
            current.setdefault("binding_sid", line[len("Binding SID: ") :])
            consumed.append(line)

    # An empty SR-TE database is legitimate: no policies configured. Measured
    # against real captures, a device with none skips the header entirely and
    # answers with nothing but the timestamp preamble -- so "empty" is judged
    # after stripping that preamble, not by requiring the header to be present.
    if not records:
        body = strip_preamble(text, platform="cisco_xr").strip()
        if body and "SR-TE policy database" not in body:
            raise ParseError("no SR-TE policy database header and no policies found")

    return finalize(
        raw=text,
        meta={"policy_count": len(records)},
        records=records,
        consumed=consumed,
        ignores=SR_IGNORES,
    )


PARSERS: dict[tuple[str, str], Callable[[dict[str, str]], dict[str, Any]]] = {
    ("cisco_xr", "facts"): parse_xr_facts,
    ("cisco_xr", "interfaces"): parse_xr_interfaces,
    ("cisco_xr", "bgp"): parse_xr_bgp,
    ("cisco_xr", "lldp"): parse_xr_lldp,
    ("cisco_xr", "isis"): parse_xr_isis,
    ("cisco_xr", "sr"): parse_xr_sr,
}


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
