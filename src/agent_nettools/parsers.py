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

Line accounting (B-404) -- PROCESS.md section 0.10
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
    # Same table shape as "bgp", same volatile columns -- the message counters
    # and Up/Down timer move on an unchanged session exactly as they do there.
    ("cisco_xr", "bgp_vpnv4"): frozenset({"msg_rcvd", "msg_sent", "up_down"}),
    ("cisco_xr", "isis"): frozenset({"holdtime"}),
    ("cisco_xr", "sr"): frozenset({"operational_duration"}),
    ("cisco_xr", "interfaces"): frozenset(),
    ("cisco_xr", "lldp"): frozenset(),
    # B-109. "up_time" and the message counters move on a healthy, unchanged
    # session exactly as bgp's do; "state"/"discovery_interfaces" are the
    # signal a diff should actually surface.
    ("cisco_xr", "ldp"): frozenset({"msgs_sent", "msgs_rcvd", "up_time"}),
    # "established_ago" is the human-relative half of the Established
    # timestamp ("5d07h ago") and changes every capture; "established" (the
    # absolute timestamp) is the stable signal, same split as sr's
    # since/duration pair.
    ("cisco_xr", "ldp_discovery"): frozenset({"established_ago"}),
}

#: B-443. The address family each BGP summary parser reads, stamped onto every
#: record and its meta so a neighbour's identity is complete without knowing
#: which intent fetched it. **Declared, not parsed**: `show bgp summary` and
#: `show bgp vpnv4 unicast summary` do not name their own AF anywhere in the
#: output -- the AF is in the *command*. Reading it back out of the text would
#: be inventing a field the device never printed.
AFI_SAFI_IPV4_UNICAST = "ipv4 unicast"
AFI_SAFI_VPNV4_UNICAST = "vpnv4 unicast"


# The field identifying a record within an intent, so two snapshots' rows can be
# matched up. None means the intent has no table, only meta.
RECORD_KEYS: dict[tuple[str, str], str | None] = {
    ("cisco_xr", "facts"): None,
    ("cisco_xr", "interfaces"): "interface",
    ("cisco_xr", "bgp"): "neighbor",
    ("cisco_xr", "bgp_vpnv4"): "neighbor",
    ("cisco_xr", "lldp"): "local_interface",
    ("cisco_xr", "isis"): "system_id",
    ("cisco_xr", "sr"): "policy",
    ("cisco_xr", "ldp"): "peer_id",
    ("cisco_xr", "ldp_discovery"): "interface",
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
                # B-443 -- see `parse_xr_bgp_vpnv4` for the measurement and the
                # reasoning. Both AFs of one session are self-describing now,
                # rather than distinguished only by which intent fetched them.
                "afi_safi": AFI_SAFI_IPV4_UNICAST,
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
    meta["afi_safi"] = AFI_SAFI_IPV4_UNICAST  # B-443

    return finalize(
        raw=text,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=BGP_IGNORES,
        unparsed_rows=skipped,
    )


# Section 0.10 accounting for ``show bgp vpnv4 unicast summary``. Line-for-line
# identical to BGP_IGNORES except the table-identifier line: the default AF's
# "Table ID: 0xe0000000   RD version: 6" becomes "Table ID: 0x0" here -- no RD
# counter, measured live on every device that has BGP configured (protocol
# coverage sweep, 2026-08-19). A distinct constant rather than a shared one
# because these are two different commands' output and the two shapes should
# not be silently reconciled into one regex that happens to match both today.
BGP_VPNV4_IGNORES: tuple[IgnoreRule, ...] = (
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
        r"^Table ID: \S+$",
        "internal table identifier, not required by the schema; the VPNv4 AF's summary "
        "carries no RD-version counter the way the default AF's does",
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


def parse_xr_bgp_vpnv4(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show bgp vpnv4 unicast summary``.

    Same shape as :func:`parse_xr_bgp` -- the router-id/local-AS preamble, the
    "% BGP instance '...' not active" fallback for a device with no BGP
    process, and the 10-column neighbour table with the same
    :func:`_split_state_pfx_rcd` split for the ``St/PfxRcd`` column -- because
    it is the *same command family* the device implements identically for
    every address family. Not merged into :func:`parse_xr_bgp` because the two
    commands read two different AFs of the same session: the default AF is
    empty on this fabric (every session shows 0 prefixes -- see
    ``inventory/lab.yaml``), while the VPNv4 AF, measured live on every
    BGP-speaking device, carries a real, non-zero prefix count per neighbour.
    Reusing ``_XR_BGP_META``/``_XR_BGP_INACTIVE``/``_split_state_pfx_rcd``
    keeps the two parsers from drifting on that shared machinery while keeping
    them as two records a caller can tell apart.
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
                # B-443. The same neighbour appears in BOTH this parser's
                # output and `parse_xr_bgp`'s, with genuinely different data --
                # measured on RR1/t0, 10.255.0.11 reads prefixes_received 0
                # under the default AF and 3 under VPNv4. Until now nothing in
                # the record said which AF it came from: the two were told
                # apart only because they arrive under different *intent*
                # keys, so the intent name was doing the AFI's job implicitly.
                # Nothing misread them (`diff_evidence` compares per intent),
                # but the first consumer to key on (device, neighbour) alone
                # would have collapsed two different objects silently. Stated
                # in the record now, so the identity does not depend on the
                # caller's indexing choice.
                "afi_safi": AFI_SAFI_VPNV4_UNICAST,
                **_split_state_pfx_rcd(fields[9]),
            }
        )
        consumed.append(stripped)

    if not meta and not records:
        raise ParseError("no BGP router identifier and no neighbour rows found")

    meta["neighbor_count"] = len(records)
    meta["afi_safi"] = AFI_SAFI_VPNV4_UNICAST

    return finalize(
        raw=text,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=BGP_VPNV4_IGNORES,
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


# --------------------------------------------------------------------------- #
# ldp / ldp_discovery -- B-109
# --------------------------------------------------------------------------- #
#
# Two commands, two parsers, deliberately not merged into one intent: "ldp"
# (`show mpls ldp neighbor`) is the session-level FSM state, one block per
# peer; "ldp_discovery" (`show mpls ldp discovery`) is the Hello-level
# adjacency, one block per local interface -- a different key (peer_id vs
# interface), a different natural record shape, and (per checks.ldp_session_up)
# a genuinely separate rung's evidence. Bundling them the way "facts" bundles
# hostname+version would have forced one artificial record_key on two
# differently-keyed tables.
#
# Both grammars are walked procedurally rather than with a header/rows split
# like isis/bgp: each command's output is a sequence of small, ordered
# sub-blocks (TCP connection / Graceful Restart / State / discovery sources /
# bound addresses for "ldp"; VRF / LDP Id / Hold time / Established for
# "ldp_discovery") rather than a single table, so every line is consumed by an
# explicit state-machine step and appended to ``consumed`` as it is read --
# there is no need for a separate declared ``IgnoreRule`` table beyond
# ``XR_COMMON_IGNORES`` (blank lines, the timestamp banner), because nothing
# here is skipped; everything recognised is captured into a field.
#
# Measured live against the lab (2026-08-19, all nine devices): every device
# runs LDP. P2's Gi0/0/0/4 (the live OBS-159/B-496 P2<->PE3 defect) is the one
# real "sends Hello, never receives one back" case on the fabric right now --
# `show mpls ldp discovery` prints it as an interface block with a direction of
# "xmit" only and no "LDP Id:"/"Hold time:"/"Established:" lines at all, which
# is exactly the shape the ``peer_id`` (nullable) design below exists for.

_LDP_PEER_RE = re.compile(r"^Peer LDP Identifier: (?P<peer>\S+):0$")
_LDP_TCP_RE = re.compile(r"^TCP connection: (?P<left>\S+) - (?P<right>\S+)$")
_LDP_GR_RE = re.compile(r"^Graceful Restart: (?P<gr>\S+)$")
_LDP_HOLDTIME_RE = re.compile(r"^Session Holdtime: (?P<ht>\d+) sec$")
_LDP_STATE_RE = re.compile(
    r"^State: (?P<state>[^;]+); Msgs sent/rcvd: (?P<sent>\d+)/(?P<rcvd>\d+); (?P<mode>.+)$"
)
_LDP_UPTIME_RE = re.compile(r"^Up time: (?P<up>.+)$")
_LDP_IPV4_COUNT_RE = re.compile(r"^IPv4: \((?P<n>\d+)\)$")
_LDP_IPV6_COUNT_RE = re.compile(r"^IPv6: \((?P<n>\d+)\)$")
_IPV4_TOKEN_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def parse_xr_ldp_neighbor(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show mpls ldp neighbor``.

    One block per peer: TCP connection, Graceful Restart, Session Holdtime,
    State (FSM state, message counters, label-advertisement mode), Up time,
    the local interface(s) that discovered this peer ("LDP Discovery
    Sources"), and the peer's own bound addresses. ``discovery_interfaces`` is
    what :func:`agent_nettools.checks.ldp_session_up` matches a rung's subject
    interface against; ``bound_addresses`` is captured for completeness
    (section 0.10 line accounting) but has no reader yet.
    """

    text = next(iter(outputs.values()), "")
    lines = text.splitlines()

    records: list[dict[str, Any]] = []
    consumed: list[str] = []
    current: dict[str, Any] | None = None
    section: str | None = None  # None | "discovery" | "addresses"
    remaining = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if match := _LDP_PEER_RE.match(line):
            current = {
                "peer_id": match["peer"],
                "discovery_interfaces": [],
                "bound_addresses": [],
            }
            records.append(current)
            consumed.append(line)
            section = None
            remaining = 0
            continue

        if current is None:
            continue  # preamble/banner, handled by XR_COMMON_IGNORES

        if match := _LDP_TCP_RE.match(line):
            left, right = match["left"], match["right"]
            if left.split(":")[0] == current["peer_id"]:
                current["peer_tcp"], current["local_tcp"] = left, right
            else:
                current["peer_tcp"], current["local_tcp"] = right, left
            consumed.append(line)
            continue

        if match := _LDP_GR_RE.match(line):
            current["graceful_restart"] = match["gr"]
            consumed.append(line)
            continue

        if match := _LDP_HOLDTIME_RE.match(line):
            current["session_holdtime"] = match["ht"]
            consumed.append(line)
            continue

        if match := _LDP_STATE_RE.match(line):
            current["state"] = match["state"].strip()
            current["msgs_sent"] = match["sent"]
            current["msgs_rcvd"] = match["rcvd"]
            current["label_distribution"] = match["mode"].strip()
            consumed.append(line)
            continue

        if match := _LDP_UPTIME_RE.match(line):
            current["up_time"] = match["up"].strip()
            consumed.append(line)
            continue

        if line == "LDP Discovery Sources:":
            section = "discovery"
            consumed.append(line)
            continue

        if line == "Addresses bound to this peer:":
            section = "addresses"
            consumed.append(line)
            continue

        if match := _LDP_IPV4_COUNT_RE.match(line):
            remaining = int(match["n"])
            consumed.append(line)
            continue

        if _LDP_IPV6_COUNT_RE.match(line):
            # Always (0) on this fabric -- IPv4 MPLS only. A non-zero count
            # would leave its interface/address lines unmatched below, which
            # is the point: surfaced as `unaccounted_lines`, not swallowed.
            remaining = 0
            consumed.append(line)
            continue

        if section == "discovery" and remaining > 0:
            current["discovery_interfaces"].append(line)
            consumed.append(line)
            remaining -= 1
            continue

        if section == "addresses":
            tokens = line.split()
            if tokens and all(_IPV4_TOKEN_RE.match(token) for token in tokens):
                current["bound_addresses"].extend(tokens)
                consumed.append(line)
                continue

        # Anything else is genuinely unrecognised and falls through to
        # `unaccounted_lines` via `finalize`/`account_lines`.

    for record in records:
        record["discovery_interface_count"] = len(record["discovery_interfaces"])
        record["bound_address_count"] = len(record["bound_addresses"])

    if not records:
        # An empty peer table is legitimate -- a device with LDP enabled but
        # no peers yet -- exactly parse_xr_sr's "empty database" reasoning.
        body = strip_preamble(text, platform="cisco_xr").strip()
        if body:
            raise ParseError("no LDP peer blocks found in non-empty output")

    return finalize(
        raw=text,
        meta={"peer_count": len(records)},
        records=records,
        consumed=consumed,
    )


_LDP_LOCAL_ID_RE = re.compile(r"^Local LDP Identifier: (?P<id>\S+):0$")
_LDP_IFACE_RE = re.compile(r"^(?P<interface>\S+) : (?P<direction>\S+)$")
_LDP_VRF_RE = re.compile(r"^VRF: '(?P<vrf>[^']+)' \((?P<vrf_id>0x[0-9a-fA-F]+)\)$")
_LDP_PEER_ID_RE = re.compile(
    r"^LDP Id: (?P<peer>\S+):0, Transport address: (?P<transport>\S+)$"
)
_LDP_HOLD_RE = re.compile(
    r"^Hold time: (?P<hold>\d+) sec \(local:(?P<local>\d+) sec, peer:(?P<peer_hold>\d+) sec\)$"
)
_LDP_ESTABLISHED_RE = re.compile(r"^Established: (?P<ts>.+) \((?P<ago>.+) ago\)$")


def parse_xr_ldp_discovery(outputs: dict[str, str]) -> dict[str, Any]:
    """Parse ``show mpls ldp discovery``.

    One block per local interface enabled for LDP. ``peer_id`` and
    ``transport_address`` are ``None`` when Hello has been sent but no peer has
    replied -- a real, distinct state (measured live: P2's Gi0/0/0/4, direction
    ``xmit`` only, no ``LDP Id:`` line at all) that :func:`checks.ldp_session_up`
    reads to tell "never discovered" apart from "discovered, session still not
    Oper".
    """

    text = next(iter(outputs.values()), "")
    lines = text.splitlines()

    meta: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    consumed: list[str] = []
    current: dict[str, Any] | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if match := _LDP_LOCAL_ID_RE.match(line):
            meta["local_ldp_identifier"] = match["id"]
            consumed.append(line)
            continue

        if line in ("Discovery Sources:", "Interfaces:"):
            consumed.append(line)
            continue

        if match := _LDP_IFACE_RE.match(line):
            current = {
                "interface": match["interface"],
                "direction": match["direction"],
                "peer_id": None,
                "transport_address": None,
            }
            records.append(current)
            consumed.append(line)
            continue

        if current is None:
            continue  # preamble/banner, handled by XR_COMMON_IGNORES

        if match := _LDP_VRF_RE.match(line):
            current["vrf"] = match["vrf"]
            consumed.append(line)
            continue

        if match := _LDP_PEER_ID_RE.match(line):
            current["peer_id"] = match["peer"]
            current["transport_address"] = match["transport"]
            consumed.append(line)
            continue

        if match := _LDP_HOLD_RE.match(line):
            current["hold_time"] = match["hold"]
            current["hold_time_local"] = match["local"]
            current["hold_time_peer"] = match["peer_hold"]
            consumed.append(line)
            continue

        if match := _LDP_ESTABLISHED_RE.match(line):
            current["established"] = match["ts"]
            current["established_ago"] = match["ago"]
            consumed.append(line)
            continue

        # Anything else falls through to `unaccounted_lines`.

    meta["interface_count"] = len(records)
    meta["discovered_peer_count"] = sum(1 for record in records if record["peer_id"])

    if "local_ldp_identifier" not in meta:
        body = strip_preamble(text, platform="cisco_xr").strip()
        if body:
            raise ParseError("no 'Local LDP Identifier' line found in non-empty output")

    return finalize(
        raw=text,
        meta=meta,
        records=records,
        consumed=consumed,
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
    ("cisco_xr", "bgp_vpnv4"): parse_xr_bgp_vpnv4,
    ("cisco_xr", "lldp"): parse_xr_lldp,
    ("cisco_xr", "isis"): parse_xr_isis,
    ("cisco_xr", "ldp"): parse_xr_ldp_neighbor,
    ("cisco_xr", "ldp_discovery"): parse_xr_ldp_discovery,
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
