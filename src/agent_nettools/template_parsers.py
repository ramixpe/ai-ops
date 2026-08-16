"""Parsers for parameterized template output.

The companion to :mod:`parsers`, which handles the static intents. This module
handles :mod:`templates` output -- ``show bgp neighbor <ip>``,
``show route <prefix>``, ``show interfaces <name>``, ``show logging last <n>``,
``ping`` and ``traceroute``.

Why a second module rather than more entries in ``parsers.PARSERS``
-------------------------------------------------------------------
The two are keyed differently and shaped differently. ``parsers`` is keyed by
``(platform, intent)`` and receives a **dict of command -> output**, because one
intent can run several commands (``facts`` runs two). A template always renders
to exactly **one** command, so a template parser receives a single string. That
difference is small but it runs through every signature, and merging the two
would mean one of them carrying a shape it never needs.

Everything else is deliberately identical, and imported rather than
re-declared: :class:`parsers.ParseError`, :data:`parsers.PARSE_OK`,
:data:`parsers.PARSE_UNAVAILABLE`, :data:`parsers.PARSE_FAILED`. There is one
status vocabulary in this package, not two.

The rules inherited from ``parsers`` without exception
------------------------------------------------------
* **A parse yielding neither records nor meta from non-empty input is a
  failure**, never a silent empty success. This is the defect that got
  ntc-templates rejected (see ``parsers``' module docstring); it is not
  re-litigated here.
* **A parser exception never propagates.** :func:`parse_template_output` guards
  every call, exactly as ``parse_intent`` does, because a parser bug must not
  turn into a failed collection.
* ``record_key`` and ``volatile_fields`` are declared per template so
  ``diff_evidence`` and ``detect_flaps`` work on template output too.

Line accounting -- BUILD-PLAN.md section 0.10
----------------------------------------------
A template that extracts three fields and ignores the rest fails silently when
the vendor adds a fourth, and makes its own coverage invisible: nobody can tell
whether a missing value means the device did not report it or the template did
not look.

**No parsing library provides this.** TTP's result contains only what matched;
there is no channel through which it reports what it skipped (measured at
T-008, recorded as OBS-030). So the accounting is ours, and it lives here so it
is written once rather than six times.

Every parser must account for **every non-blank line** as exactly one of:

1. matched by the template, becoming a record or a ``meta`` field; or
2. matched by a **declared** :class:`IgnoreRule` -- a named, commented pattern.

Anything else is *unaccounted*, and unaccounted lines are surfaced, never
dropped. Use :func:`finalize` to build the result and the accounting together;
it is the only supported way to satisfy the contract.

``unaccounted_lines`` and ``unparsed_rows`` are different failures and stay
separate keys. The first means "the template does not know what this line is".
The second means "the template knows what this line should be and it did not
fit". Collapsing them would hide a vendor output change behind a
malformed-row count.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ttp import ttp

from .parsers import (
    PARSE_FAILED,
    PARSE_OK,
    PARSE_UNAVAILABLE,
    ParseError,
)

__all__ = [
    "PARSE_FAILED",
    "PARSE_OK",
    "PARSE_UNAVAILABLE",
    "BGP_NEIGHBOR_IGNORES",
    "INTERFACE_IGNORES",
    "LOGGING_IGNORES",
    "PING_IGNORES",
    "ROUTE_IGNORES",
    "TEMPLATE_PARSERS",
    "TEMPLATE_RECORD_KEYS",
    "TEMPLATE_VOLATILE_FIELDS",
    "TRACEROUTE_IGNORES",
    "XR_COMMON_IGNORES",
    "IgnoreRule",
    "ParseError",
    "account_lines",
    "finalize",
    "has_template_parser",
    "parse_template_output",
    "parse_xr_bgp_neighbor",
    "parse_xr_interface",
    "parse_xr_logging",
    "parse_xr_ping",
    "parse_xr_route",
    "parse_xr_traceroute",
    "template_record_key",
    "template_volatile_fields",
]


# --------------------------------------------------------------------------- #
# Line accounting (section 0.10)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IgnoreRule:
    """A declared reason for a line to be absent from the parsed result.

    ``reason`` is not decoration. Section 0.10 requires that a reviewer can see
    the full accounting in one place and tell what each pattern is for; an
    unexplained regex that quietly swallows unrecognised lines defeats the
    entire mechanism.
    """

    pattern: str
    reason: str

    def matches(self, line: str) -> bool:
        return re.match(self.pattern, line) is not None


# Present in essentially every IOS-XR `show` response. Declared once here so a
# parser lists only what is specific to its own command -- the point of 0.10 is
# that ignores are visible, not that they are repeated.
XR_COMMON_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^\s*$", "blank line"),
    # Every IOS-XR show command prefixes its output with the current time.
    # This is the field that made whole-output diffing useless before Phase 2.
    IgnoreRule(
        r"^\w{3} \w{3}\s+\d+ \d{2}:\d{2}:\d{2}\.\d+ \w+$",
        "IOS-XR timestamp banner",
    ),
)


def account_lines(
    raw: str,
    *,
    consumed: Iterable[str] = (),
    ignores: Sequence[IgnoreRule] = (),
    include_common: bool = True,
) -> list[str]:
    """Return the non-blank lines of ``raw`` that nothing claimed.

    ``consumed`` is the set of lines the template actually turned into records
    or meta. Comparison is on the stripped line, so trailing whitespace in
    captured fixture output never creates a phantom unaccounted line.

    An empty return value is the passing state, and every parser's test asserts
    it. A non-empty one means the device said something the template does not
    recognise -- which is a finding, not a crash.
    """

    rules = (*XR_COMMON_IGNORES, *ignores) if include_common else tuple(ignores)
    consumed_set = {line.strip() for line in consumed}

    unaccounted: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in consumed_set:
            continue
        if any(rule.matches(line) or rule.matches(stripped) for rule in rules):
            continue
        unaccounted.append(stripped)
    return unaccounted


def finalize(
    *,
    raw: str,
    meta: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    consumed: Iterable[str] = (),
    ignores: Sequence[IgnoreRule] = (),
    unparsed_rows: int = 0,
) -> dict[str, Any]:
    """Assemble a parser result with its section 0.10 accounting attached.

    Every parser returns through here. That is what makes the accounting a
    contract rather than a convention: a parser cannot produce a well-formed
    result *without* declaring what it ignored, because this is the only
    function that builds one.
    """

    return {
        "meta": {
            **(meta or {}),
            "unaccounted_lines": account_lines(raw, consumed=consumed, ignores=ignores),
            "unparsed_rows": unparsed_rows,
        },
        "records": list(records or []),
    }


# --------------------------------------------------------------------------- #
# cisco_xr: bgp_neighbor  (T-012)
# --------------------------------------------------------------------------- #
#
# ``show bgp neighbor <ip>`` answers one of three shapes, all legitimate:
#
# 1. A full ~150-line neighbor block (session state, timers, capabilities,
#    then one repeated section per negotiated address family).
# 2. The entire body is ``% BGP instance 'default' not active`` -- this
#    device has no BGP process at all (the P-routers in this lab).
# 3. The entire body is ``% Neighbor not found`` -- BGP is active but this
#    peer is not configured here.
#
# Cases 2 and 3 are the device answering correctly, not a parse failure:
# ``found=False`` with a ``reason`` records *that*, distinguishably from a
# parser that could not read the output at all (PARSE_FAILED).

_BGP_NOT_ACTIVE = re.compile(r"^% BGP instance '[^']*' not active$")
_NEIGHBOR_NOT_FOUND = re.compile(r"^% Neighbor not found$")

_NEIGHBOR_IS = re.compile(r"^BGP neighbor is (?P<neighbor>\S+)$")
_REMOTE_LOCAL_AS = re.compile(r"^Remote AS (?P<remote_as>\d+), local AS (?P<local_as>\d+), \S+ link$")
_ROUTER_ID = re.compile(r"^Remote router ID (?P<router_id>\S+)$")
# Two real forms, both captured:
#   "BGP state = Established, up for 2d12h"
#   "BGP state = Idle (No route to multi-hop neighbor)"
# The parenthetical appears only on a down session and is the single most
# diagnostic field in the whole command -- it says *why* -- so it is captured
# as `state_reason` rather than ignored. Found by the `broken` label; no
# healthy fixture can produce it.
_STATE = re.compile(
    r"^BGP state = (?P<state>[A-Za-z]+)"
    r"(?:, up for (?P<up_for>\S+))?"
    r"(?: \((?P<state_reason>[^)]+)\))?$"
)
_PREVIOUS_STATE = re.compile(r"^Previous State: (?P<previous_state>.+)$")
_HOLD_KEEPALIVE = re.compile(
    r"^Hold time is (?P<hold_time>\d+), keepalive interval is (?P<keepalive>\d+) seconds$"
)
_RECEIVED = re.compile(r"^Received (?P<messages_received>\d+) messages, \d+ notifications, \d+ in queue$")
_SENT = re.compile(r"^Sent (?P<messages_sent>\d+) messages, \d+ notifications, \d+ in queue$")
# Split deliberately. IOS-XR emits "Last reset 1d23h, due to <reason>", and
# folding the duration into the reason would make the field change on every
# capture of an unchanged device -- a false diff of exactly the kind that made
# whole-output comparison useless before Phase 2. Keeping them apart lets the
# duration be declared volatile while a genuine change of *reason* still shows
# up as a real difference, which is the signal worth having.
_LAST_RESET = re.compile(
    r"^Last reset (?P<last_reset_ago>\S+?),\s*due to\s+(?P<last_reset_reason>.+)$"
)
_LAST_RESET_FALLBACK = re.compile(r"^Last reset (?P<last_reset_ago>.+)$")

# TTP template for the repeated "For Address Family: <afi>" sections -- a
# shape TTP suits well precisely because it repeats identically five times per
# fixture. ``best_paths`` is captured only so the matched line can be
# reconstructed verbatim for the 0.10 accounting below; it is not part of the
# record schema and is dropped before a record is returned.
_AF_TEMPLATE = """
<group name="records*">
 For Address Family: {{ address_family | ORPHRASE }}
  BGP neighbor version {{ neighbor_version | DIGIT }}
  Route-Reflector Client{{ route_reflector_client | set(True) }}
  Policy for incoming advertisements is {{ policy_in | WORD }}
  Policy for outgoing advertisements is {{ policy_out | WORD }}
  {{ accepted_prefixes | DIGIT }} accepted prefixes, {{ best_paths | DIGIT }} are bestpaths
</group>
"""

# Section 0.10 accounting for everything the meta/record extraction above does
# not itself capture. Grouped by what the lines are, per BUILD-PLAN.md 0.10's
# instruction to keep the accounting reviewable rather than one broad
# catch-all.
BGP_NEIGHBOR_IGNORES: tuple[IgnoreRule, ...] = (
    # Session-header bookkeeping not in the required meta schema.
    IgnoreRule(r"^Cluster ID \S+$", "route-reflector cluster ID, present only on the RR's own view of a client"),
    IgnoreRule(r"^Last Received Message: \S+$", "last BGP message type received, not required by the schema"),
    IgnoreRule(r"^NSR State: .+$", "non-stop routing state detail, not required by the schema"),
    IgnoreRule(r"^BFD enabled \(.+\)$", "BFD session detail, not required by the schema"),
    IgnoreRule(r"^Last read \S+, Last read before reset \S+$", "read-activity timestamps, volatile bookkeeping"),
    IgnoreRule(
        r"^Configured hold time: \d+, keepalive: \d+, min acceptable hold time: \d+$",
        "configured (not negotiated) timers restated; the negotiated 'Hold time is' line is captured instead",
    ),
    # Write-pulse bookkeeping: several generations of internal "last write"
    # diagnostics IOS-XR logs for the TCP session, none needed by the schema.
    IgnoreRule(r"^Last write \S+, attempted \d+, written \d+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Second last write \S+, attempted \d+, written \d+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Last write before reset \S+, attempted \d+, written \d+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Second last write before reset \S+, attempted \d+, written \d+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Last write pulse rcvd .*pulse count \d+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Last write pulse rcvd before reset \S+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Last write thread event before reset \S+, second last \S+$", "write-pulse bookkeeping"),
    IgnoreRule(r"^Last KA expiry before reset \S+, second last \S+$", "keepalive-timer bookkeeping"),
    IgnoreRule(r"^Last KA error before reset \S+, KA not sent \S+$", "keepalive-timer bookkeeping"),
    IgnoreRule(r"^Last KA start before reset \S+, second last \S+$", "keepalive-timer bookkeeping"),
    IgnoreRule(r"^Precedence: \S+$", "IP precedence of the TCP session, not required by the schema"),
    IgnoreRule(r"^Non-stop routing is enabled$", "NSR flag, not required by the schema"),
    IgnoreRule(r"^Multi-protocol capability received$", "capability summary line"),
    # Capability lines: one header plus one line per negotiated capability,
    # including the per-AF "Address family <X>: advertised and received"
    # capability line -- distinct from the " For Address Family:" record
    # header the TTP template above matches (lowercase "family", no "For").
    IgnoreRule(r"^Neighbor capabilities:$", "capability list header"),
    IgnoreRule(r"^Route refresh: .+$", "negotiated capability, not required by the schema"),
    IgnoreRule(r"^4-byte AS: .+$", "negotiated capability, not required by the schema"),
    IgnoreRule(
        r"^Address family [\w /-]+: advertised.*$",
        "negotiated per-AF capability line (lowercase 'family'), not the address-family record header",
    ),
    IgnoreRule(r"^Minimum time between advertisement runs is \d+ secs$", "advertisement pacing, not in the schema"),
    # Message-logging lines: whether inbound/outbound BGP message logging is
    # enabled and how many messages are buffered.
    IgnoreRule(r"^Inbound message logging enabled, \d+ messages buffered$", "message-logging bookkeeping"),
    IgnoreRule(r"^Outbound message logging enabled, \d+ messages buffered$", "message-logging bookkeeping"),
    # Per-address-family detail beyond the six required record fields.
    IgnoreRule(r"^Update group: \S+ Filter-group: \S+.*$", "update-group bookkeeping"),
    IgnoreRule(r"^NEXT_HOP is always this router$", "next-hop-self policy detail, not required by the schema"),
    IgnoreRule(r"^Extended Nexthop Encoding: .+$", "negotiated capability, not required by the schema"),
    IgnoreRule(r"^Route refresh request: received \d+, sent \d+$", "route-refresh counters, not in the schema"),
    IgnoreRule(r"^Exact no\. of prefixes denied\s*:\s*\d+\.$", "prefix-denial counter, not required by the schema"),
    IgnoreRule(r"^Cumulative no\. of prefixes denied:\s*\d+\.$", "prefix-denial counter, not required by the schema"),
    IgnoreRule(r"^Prefix advertised \d+, suppressed \d+, withdrawn \d+$", "advertised-prefix counters, not in the schema"),
    IgnoreRule(r"^AIGP is enabled$", "AIGP attribute flag, not required by the schema"),
    IgnoreRule(r"^An EoR was( not)? received during read-only mode$", "end-of-RIB marker, not required by the schema"),
    IgnoreRule(r"^Last ack version \d+, Last synced ack version \d+$", "version bookkeeping, not required by the schema"),
    IgnoreRule(r"^Outstanding version objects: current \d+, max \d+, refresh \d+$", "version bookkeeping"),
    IgnoreRule(r"^Additional-paths operation: \S+$", "add-path setting, not required by the schema"),
    IgnoreRule(r"^Send Multicast Attributes$", "capability flag, not required by the schema"),
    IgnoreRule(
        r"^Advertise routes with local-label via Unicast SAFI$",
        "label-advertisement flag (IPv4 Unicast only), not required by the schema",
    ),
    IgnoreRule(r"^Slow Peer State: \S+$", "slow-peer detection header, not required by the schema"),
    IgnoreRule(r"^Detected state: \S+, Detection threshold: \d+$", "slow-peer detection detail"),
    IgnoreRule(r"^Detection Count: \d+, Recovery Count: \d+$", "slow-peer detection detail"),
    # Tail bookkeeping after the last address-family section.
    IgnoreRule(r"^Connections established \d+; dropped \d+$", "connection-attempt counters, not in the schema"),
    IgnoreRule(r"^Local host: \S+, Local port: \d+, IF Handle: \S+$", "local TCP endpoint detail, not in the schema"),
    IgnoreRule(r"^Foreign host: \S+, Foreign port: \d+$", "remote TCP endpoint detail, not in the schema"),
    IgnoreRule(
        r"^Peer reset reason: .+$",
        "reset-reason detail beyond the required last_reset_reason summary, present only after a remote-initiated reset",
    ),
    # --- down-session-only lines. None can appear on an established session,
    # so they were unreachable until the `broken` label was captured. Declared
    # rather than extracted: `state_reason` already carries the diagnostic that
    # matters ("No route to multi-hop neighbor"), and widening the schema
    # mid-stream to chase adjacent detail is how a contract stops being
    # reviewable.
    IgnoreRule(
        r"^Error Code: .+$",
        "error code from the last BGP notification; state_reason carries the current cause",
    ),
    IgnoreRule(r"^Notification data sent:$", "header for the notification payload dump"),
    IgnoreRule(r"^None$", "the notification payload itself, empty in every observed case"),
    IgnoreRule(
        r"^Time since last notification sent to neighbor: \S+$",
        "volatile notification bookkeeping",
    ),
)

# B-432. Promoted from two `IgnoreRule`s ("socket bookkeeping") to a parsed
# field, because it is the one line in this output that reports the **TCP
# layer** rather than the BGP state machine.
#
# Rung 1 reads the session state from `show bgp summary`; rung 2 read
# `connection_state` from here -- two commands reporting the same FSM, which is
# why `cause_not_localised` was unreachable (OBS-092). The socket's arming is a
# different subsystem: whether the stack is polling a socket for this peer at
# all.
#
# Measured across every committed fixture: armed on 14 of 14 Established
# sessions, not armed on 2 of 2 Idle ones.
_BGP_SOCKET = re.compile(
    r"^Socket (?P<io>not armed|armed) for io, "
    r"(?P<read>not armed|armed) for read, "
    r"(?P<write>not armed|armed) for write$"
)

_BGP_NEIGHBOR_META_KEYS: tuple[str, ...] = (
    "found",
    "reason",
    "neighbor",
    "state",
    "connection_state",
    "previous_state",
    "last_reset_reason",
    "last_reset_ago",
    "state_reason",
    "socket_armed_read",
    "socket_armed_write",
    "hold_time",
    "keepalive",
    "local_as",
    "remote_as",
    "router_id",
    "up_for",
    "messages_received",
    "messages_sent",
)


def _empty_bgp_neighbor_meta() -> dict[str, Any]:
    """Every key present, ``None`` unless known -- never absent.

    A consumer must never have to distinguish "missing" from "not
    applicable"; cases 2 and 3 (no active BGP process / peer not configured)
    carry every key with a ``None`` value rather than a smaller dict.
    """

    meta: dict[str, Any] = dict.fromkeys(_BGP_NEIGHBOR_META_KEYS)
    meta["found"] = False
    return meta


def _parse_bgp_neighbor_address_families(output: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the address-family records and the raw lines they consumed."""

    ttp_parser = ttp(data=output, template=_AF_TEMPLATE)
    ttp_parser.parse()
    result = ttp_parser.result()
    raw_records: list[dict[str, Any]] = []
    if result and result[0]:
        raw_records = result[0][0].get("records", [])

    records: list[dict[str, Any]] = []
    consumed: list[str] = []
    for raw in raw_records:
        address_family = raw.get("address_family")
        neighbor_version = raw.get("neighbor_version")
        route_reflector_client = bool(raw.get("route_reflector_client", False))
        policy_in = raw.get("policy_in")
        policy_out = raw.get("policy_out")
        accepted_prefixes = raw.get("accepted_prefixes")
        best_paths = raw.get("best_paths")

        records.append(
            {
                "address_family": address_family,
                "neighbor_version": neighbor_version,
                "policy_in": policy_in,
                "policy_out": policy_out,
                "accepted_prefixes": accepted_prefixes,
                "route_reflector_client": route_reflector_client,
            }
        )

        # Reconstructed verbatim from the same literal IOS-XR phrasing the TTP
        # template above matches, so account_lines (which compares stripped
        # text) recognises these as claimed.
        consumed.append(f"For Address Family: {address_family}")
        if neighbor_version is not None:
            consumed.append(f"BGP neighbor version {neighbor_version}")
        if route_reflector_client:
            consumed.append("Route-Reflector Client")
        if policy_in is not None:
            consumed.append(f"Policy for incoming advertisements is {policy_in}")
        if policy_out is not None:
            consumed.append(f"Policy for outgoing advertisements is {policy_out}")
        if accepted_prefixes is not None and best_paths is not None:
            consumed.append(f"{accepted_prefixes} accepted prefixes, {best_paths} are bestpaths")

    return records, consumed


def parse_xr_bgp_neighbor(output: str) -> dict[str, Any]:
    """Parse ``show bgp neighbor <ip>``.

    Three legitimate shapes -- see the section comment above. Raises
    :class:`ParseError` only when the output is none of them: not a full
    neighbor block, not "BGP instance ... not active", not "Neighbor not
    found". That is genuinely unrecognised output, not a lab-topology detail.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    for line in lines:
        if _BGP_NOT_ACTIVE.match(line):
            meta = _empty_bgp_neighbor_meta()
            meta["reason"] = "bgp_not_active"
            return finalize(raw=output, meta=meta, records=[], consumed=[line], ignores=BGP_NEIGHBOR_IGNORES)
        if _NEIGHBOR_NOT_FOUND.match(line):
            meta = _empty_bgp_neighbor_meta()
            meta["reason"] = "neighbor_not_found"
            return finalize(raw=output, meta=meta, records=[], consumed=[line], ignores=BGP_NEIGHBOR_IGNORES)

    meta = _empty_bgp_neighbor_meta()
    consumed: list[str] = []
    found_neighbor = False

    for line in lines:
        if match := _NEIGHBOR_IS.match(line):
            meta["neighbor"] = match["neighbor"]
            found_neighbor = True
            consumed.append(line)
        elif match := _REMOTE_LOCAL_AS.match(line):
            meta["remote_as"] = match["remote_as"]
            meta["local_as"] = match["local_as"]
            consumed.append(line)
        elif match := _ROUTER_ID.match(line):
            meta["router_id"] = match["router_id"]
            consumed.append(line)
        elif match := _STATE.match(line):
            meta["state"] = match["state"]
            meta["connection_state"] = match["state"]
            meta["state_reason"] = match["state_reason"]
            if match["up_for"]:
                meta["up_for"] = match["up_for"]
            consumed.append(line)
        elif match := _BGP_SOCKET.match(line):
            meta["socket_armed_read"] = match["read"] == "armed"
            meta["socket_armed_write"] = match["write"] == "armed"
            consumed.append(line)
        elif match := _PREVIOUS_STATE.match(line):
            meta["previous_state"] = match["previous_state"]
            consumed.append(line)
        elif match := _HOLD_KEEPALIVE.match(line):
            meta["hold_time"] = match["hold_time"]
            meta["keepalive"] = match["keepalive"]
            consumed.append(line)
        elif match := _RECEIVED.match(line):
            meta["messages_received"] = match["messages_received"]
            consumed.append(line)
        elif match := _SENT.match(line):
            meta["messages_sent"] = match["messages_sent"]
            consumed.append(line)
        elif match := _LAST_RESET.match(line):
            meta["last_reset_reason"] = match["last_reset_reason"]
            meta["last_reset_ago"] = match["last_reset_ago"]
            consumed.append(line)
        elif match := _LAST_RESET_FALLBACK.match(line):
            # "Last reset <duration>" with no "due to" clause -- the duration is
            # still worth recording, and the line must still be consumed rather
            # than surfacing as unaccounted.
            meta["last_reset_ago"] = match["last_reset_ago"]
            consumed.append(line)

    if not found_neighbor:
        raise ParseError(
            "output is neither a BGP neighbor block, '% BGP instance ... not active', "
            "nor '% Neighbor not found'"
        )

    meta["found"] = True

    records, af_consumed = _parse_bgp_neighbor_address_families(output)
    consumed.extend(af_consumed)

    return finalize(raw=output, meta=meta, records=records, consumed=consumed, ignores=BGP_NEIGHBOR_IGNORES)


# --------------------------------------------------------------------------- #
# cisco_xr: route  (T-013)
# --------------------------------------------------------------------------- #
#
# ``show route <prefix>`` answers one of two shapes, both legitimate:
#
# 1. A routing entry: the top-level "Known via" line, an optional local
#    label, an installed-since duration, then one "Routing Descriptor
#    Blocks" section with one block per installed path.
# 2. The entire body is ``% Network not in table`` -- the device correctly
#    reporting that no route exists for this prefix, not a parse failure.
#
# Case 2 is the branch ``checks.route_present`` reads to decide ``broken``:
# it must come back ``found=False`` with ``PARSE_OK``, exactly as the
# not-active / not-found cases do for bgp_neighbor.

_NOT_IN_TABLE = re.compile(r"^% Network not in table$")

_ROUTING_ENTRY = re.compile(r"^Routing entry for (?P<prefix>\S+)$")
# Two "Known via" shapes are on disk: a routed protocol ("isis CORE", trailer
# ", labeled SR") and a directly connected local route ("local", trailer
# " (connected)", seen on PE4 for its own loopback). distance/metric are
# extracted identically either way; only the trailing clause differs.
_KNOWN_VIA = re.compile(
    r'^Known via "(?P<protocol>[^"]*)", distance (?P<distance>\d+), metric (?P<metric>\d+)'
    r"(?:, labeled SR| \(connected\))?$"
)
_LOCAL_LABEL = re.compile(r"^Local Label (?P<local_label>\d+), type \S+$")
_INSTALLED = re.compile(r"^Installed \S+ \S+ \S+ for (?P<installed_ago>\S+)$")

# One routing descriptor block per installed path. Two line shapes are on
# disk: a routed next hop ("<ip>, from <ip>, via <interface>[, <role>]") and
# a directly connected route ("directly connected, via <interface>", seen
# only on PE4's own loopback). The latter has no next-hop IP and no "from"
# address to report -- both are recorded as ``None`` and ``next_hop`` carries
# the literal string "directly connected" instead, which is exactly as
# stable an identifier across two captures of an unchanged device as a real
# next-hop IP would be.
_DESCRIPTOR_BLOCK = re.compile(
    r"^(?P<next_hop>[^,]+), from (?P<from>[^,]+), via (?P<interface>[^,]+)(?:, (?P<path_role>.+))?$"
)
_DIRECTLY_CONNECTED = re.compile(r"^directly connected, via (?P<interface>[^,]+)$")
_ROUTE_METRIC = re.compile(r"^Route metric is (?P<route_metric>\d+)$")

# Section 0.10 accounting for everything the meta/record extraction above
# does not itself capture. A handful of rules, each explained, per
# BUILD-PLAN.md 0.10's instruction against one broad catch-all.
ROUTE_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^Routing Descriptor Blocks$", "descriptor-block section header, not itself a per-path record"),
    IgnoreRule(
        r"^No advertising protos\.$",
        "tail line noting the route is not being re-advertised, not required by the schema",
    ),
    IgnoreRule(
        r"^Repair Node\(s\): \S+$",
        "TI-LFA repair-node detail for a backup path, not required by the schema",
    ),
    IgnoreRule(
        r"^Redist Advertisers:$",
        "redistribution-advertiser section header, present only on a locally originated/connected route",
    ),
    IgnoreRule(
        r"^\d+ \(protoid=\d+, clientid=\d+\)$",
        "redistribution-advertiser detail line (count plus internal protocol/client IDs), not in the schema",
    ),
)

_ROUTE_META_KEYS: tuple[str, ...] = (
    "found",
    "prefix",
    "protocol",
    "distance",
    "metric",
    "local_label",
    "installed_ago",
    "path_count",
)


def _empty_route_meta() -> dict[str, Any]:
    """Every key present, ``None`` unless known -- never absent.

    ``path_count`` is the one exception: per the T-013 spec's meta table it
    is always a count, ``"0"`` when there are no records, never ``None``.
    """

    meta: dict[str, Any] = dict.fromkeys(_ROUTE_META_KEYS)
    meta["found"] = False
    meta["path_count"] = "0"
    return meta


def parse_xr_route(output: str) -> dict[str, Any]:
    """Parse ``show route <prefix>``.

    Two legitimate shapes -- see the section comment above. Raises
    :class:`ParseError` only when the output is neither a routing entry nor
    ``% Network not in table``: genuinely unrecognised output.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    for line in lines:
        if _NOT_IN_TABLE.match(line):
            meta = _empty_route_meta()
            return finalize(raw=output, meta=meta, records=[], consumed=[line], ignores=ROUTE_IGNORES)

    meta = _empty_route_meta()
    records: list[dict[str, Any]] = []
    consumed: list[str] = []
    current_record: dict[str, Any] | None = None
    found_entry = False

    for line in lines:
        if match := _ROUTING_ENTRY.match(line):
            meta["prefix"] = match["prefix"]
            found_entry = True
            consumed.append(line)
        elif match := _KNOWN_VIA.match(line):
            meta["protocol"] = match["protocol"]
            meta["distance"] = match["distance"]
            meta["metric"] = match["metric"]
            consumed.append(line)
        elif match := _LOCAL_LABEL.match(line):
            meta["local_label"] = match["local_label"]
            consumed.append(line)
        elif match := _INSTALLED.match(line):
            meta["installed_ago"] = match["installed_ago"]
            consumed.append(line)
        elif match := _DESCRIPTOR_BLOCK.match(line):
            current_record = {
                "next_hop": match["next_hop"],
                "from": match["from"],
                "interface": match["interface"],
                "path_role": match["path_role"],
                "route_metric": None,
                "directly_connected": False,
            }
            records.append(current_record)
            consumed.append(line)
        elif match := _DIRECTLY_CONNECTED.match(line):
            current_record = {
                # The sentinel keeps `next_hop` usable as TEMPLATE_RECORD_KEYS'
                # identity field -- it is as stable across two captures as a
                # real next-hop address. But it makes the field polymorphic,
                # and a consumer calling ipaddress.ip_address() on it would
                # crash. `directly_connected` is the machine-checkable form of
                # the same fact, so nothing downstream has to string-match a
                # sentinel to find out what kind of path this is.
                "next_hop": "directly connected",
                "from": None,
                "interface": match["interface"],
                "path_role": None,
                "route_metric": None,
                "directly_connected": True,
            }
            records.append(current_record)
            consumed.append(line)
        elif (match := _ROUTE_METRIC.match(line)) and current_record is not None:
            current_record["route_metric"] = match["route_metric"]
            consumed.append(line)

    if not found_entry:
        raise ParseError("output is neither a routing entry nor '% Network not in table'")

    meta["found"] = True
    meta["path_count"] = str(len(records))

    return finalize(raw=output, meta=meta, records=records, consumed=consumed, ignores=ROUTE_IGNORES)


# --------------------------------------------------------------------------- #
# cisco_xr: interface  (T-014)
# --------------------------------------------------------------------------- #
#
# ``show interfaces <name>`` answers one of three shapes on this fabric, all
# legitimate:
#
# 1. A physical interface (GigabitEthernet): header, hardware/description/
#    address block, then a full counter block -- input/output packet and
#    byte counts, drop/error/quality counters, and carrier transitions.
# 2. A Loopback: the same header and hardware/address block, but IOS-XR
#    reports no rate, no duplex/flow-control/ARP detail, and **no counter
#    block at all** -- ``records`` is legitimately empty, never a parse
#    failure.
# 3. A VLAN subinterface: the only line-protocol-down case in this fabric's
#    fixtures. It *does* carry a counter block, but a **shorter** one than a
#    physical interface's -- only the six input/output packet-byte-drop
#    counters are present; the quality/error counters (runts/giants/
#    throttles/parity, input errors/CRC/frame/overrun/ignored/abort, output
#    errors/underruns/applique/resets, output buffer failures) and carrier
#    transitions are not emitted by the device at all while the line is
#    down. This is a real shape the initial task spec did not anticipate
#    (it described only "the counter block" as if it were uniform); handled
#    here by matching each counter line independently rather than assuming
#    the full block is always present, exactly as ``parse_xr_route`` already
#    treats a directly-connected path as a distinct, independently-matched
#    shape rather than a variant requiring special-casing.
#
# Unlike ``bgp_neighbor``/``route``, there is no fixture of ``show
# interfaces`` answering with a device-level error (e.g. an unknown
# interface name), so no "not found" meta shape is modelled here -- output
# that does not contain a recognisable header line raises ``ParseError``,
# exactly like any other genuinely unrecognised output.

_HEADER = re.compile(
    r"^(?P<interface>\S+) is (?P<admin_state>administratively down|up|down), "
    # line protocol reads "administratively down" too when the interface is
    # shut -- not just "down". Found by the `broken` label; every healthy
    # fixture is "up", so this branch was unreachable until PE2 was isolated.
    r"line protocol is (?P<line_state>administratively down|up|down)$"
)
_STATE_TRANSITIONS = re.compile(r"^Interface state transitions: (?P<n>\d+)$")
# Three ``Hardware is`` shapes are on disk: a physical interface (hardware
# type, then an address with a redundant "(bia <mac>)" burned-in-address
# clause), a Loopback (hardware type only, no address at all), and a VLAN
# subinterface (hardware type and an address, but no "(bia ...)" clause).
# One pattern covers all three via optional groups.
_HARDWARE = re.compile(
    r"^Hardware is (?P<hardware_type>[^,]+)(?:, address is (?P<mac>\S+)(?: \(bia \S+\))?)?$"
)
_DESCRIPTION = re.compile(r"^Description: (?P<description>.+)$")
_INTERNET_ADDRESS = re.compile(r"^Internet address is (?P<ip>\S+)$")
_MTU_BW = re.compile(r"^MTU (?P<mtu>\d+) bytes, BW (?P<bw>\d+) Kbit(?: \(Max: \d+ Kbit\))?$")
# Three encapsulation shapes are on disk: a physical interface's "Encapsulation
# ARPA," with "loopback not set," reported as its own separate line further
# down, and Loopback/VLAN subinterfaces, which fold "  loopback not set,"
# onto the same line instead. The trailing clause carries no information a
# physical interface's separate line doesn't already carry, so it is matched
# here and discarded rather than captured into a field.
_ENCAPSULATION = re.compile(r"^Encapsulation (?P<encap>ARPA|Loopback|802\.1Q Virtual LAN),(?:\s+loopback not set,)?$")
_LAST_LINK_FLAPPED = re.compile(r"^Last link flapped (?P<flap>\S+)$")

# Counter-block lines. Each is matched independently -- see the shape note
# above -- so a fixture missing some of them (Loopback: none; a down VLAN
# subinterface: only the first two) still round-trips clean; whichever
# counters are actually present in the output are exactly the records
# produced, in the document order the lines appear.
_PACKETS_INPUT = re.compile(
    r"^(?P<packets_input>\d+) packets input, (?P<bytes_input>\d+) bytes, "
    r"(?P<total_input_drops>\d+) total input drops$"
)
_PACKETS_OUTPUT = re.compile(
    r"^(?P<packets_output>\d+) packets output, (?P<bytes_output>\d+) bytes, "
    r"(?P<total_output_drops>\d+) total output drops$"
)
_RUNTS = re.compile(
    r"^(?P<runts>\d+) runts, (?P<giants>\d+) giants, (?P<throttles>\d+) throttles, (?P<parity>\d+) parity$"
)
_INPUT_ERRORS = re.compile(
    r"^(?P<input_errors>\d+) input errors, (?P<crc>\d+) CRC, (?P<frame>\d+) frame, "
    r"(?P<overrun>\d+) overrun, (?P<ignored>\d+) ignored, (?P<abort>\d+) abort$"
)
_OUTPUT_ERRORS = re.compile(
    r"^(?P<output_errors>\d+) output errors, (?P<underruns>\d+) underruns, "
    r"(?P<applique>\d+) applique, (?P<resets>\d+) resets$"
)
# "output buffers swapped out" shares this line with the required
# output_buffer_failures counter but is not itself part of the schema, so the
# whole line is matched (and consumed) while only the first count becomes a
# record -- the same "one regex, partial field use" pattern _KNOWN_VIA uses
# above for the route parser's trailing clause.
_OUTPUT_BUFFER_FAILURES = re.compile(
    r"^(?P<output_buffer_failures>\d+) output buffer failures, \d+ output buffers swapped out$"
)
_CARRIER_TRANSITIONS = re.compile(r"^(?P<carrier_transitions>\d+) carrier transitions$")

# Ordered (regex, counter names) pairs, walked in the order the lines appear
# in real output so records come out in document order. A tuple of names
# because three of these lines pack more than one counter.
_COUNTER_LINES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (_PACKETS_INPUT, ("packets_input", "bytes_input", "total_input_drops")),
    (_RUNTS, ("runts", "giants", "throttles", "parity")),
    (_INPUT_ERRORS, ("input_errors", "crc", "frame", "overrun", "ignored", "abort")),
    (_PACKETS_OUTPUT, ("packets_output", "bytes_output", "total_output_drops")),
    (_OUTPUT_ERRORS, ("output_errors", "underruns", "applique", "resets")),
    (_OUTPUT_BUFFER_FAILURES, ("output_buffer_failures",)),
    (_CARRIER_TRANSITIONS, ("carrier_transitions",)),
)

# Section 0.10 accounting for everything the meta/record extraction above
# does not itself capture. Anchored and specific per BUILD-PLAN.md 0.10's
# instruction against a broad catch-all; each carries the line shape it
# covers and why it is not part of the schema.
INTERFACE_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^reliability (?:\d+/\d+|Unknown), txload (?:\d+/\d+|Unknown), rxload (?:\d+/\d+|Unknown)$",
        "link-quality/load snapshot, not required by the schema",
    ),
    IgnoreRule(
        r"^loopback not set,$",
        "loopback-test state flag on a physical interface; folded onto the Encapsulation "
        "line instead for Loopback/VLAN shapes, not required by the schema",
    ),
    IgnoreRule(
        r"^Full-duplex, \S+, \S+, link type is \S+$",
        "duplex/speed/link-type summary, not required by the schema",
    ),
    IgnoreRule(
        r"^output flow control is \S+, input flow control is \S+$",
        "flow-control negotiation state, not required by the schema",
    ),
    IgnoreRule(
        r"^Carrier delay \(up\) is \d+ msec$",
        "carrier-delay timer configuration, not required by the schema",
    ),
    IgnoreRule(
        r"^ARP type ARPA, ARP timeout \d{2}:\d{2}:\d{2}$",
        "ARP encapsulation/timeout setting, not required by the schema",
    ),
    IgnoreRule(
        r"^Last input (?:never|Unknown|\d{2}:\d{2}:\d{2}), output (?:never|Unknown|\d{2}:\d{2}:\d{2})$",
        "last-input/output activity timestamps; last_link_flapped is the field captured instead",
    ),
    IgnoreRule(
        r'^Last clearing of "show interface" counters (?:never|Unknown)$',
        "counter-clear timestamp, not required by the schema",
    ),
    IgnoreRule(
        r"^\d+ minute input rate \d+ bits/sec, \d+ packets/sec$",
        "5-minute smoothed input rate; the raw packet/byte counters are captured instead",
    ),
    IgnoreRule(
        r"^\d+ minute output rate \d+ bits/sec, \d+ packets/sec$",
        "5-minute smoothed output rate; the raw packet/byte counters are captured instead",
    ),
    IgnoreRule(
        r"^Input/output data rate is disabled\.$",
        "loopback rate-disabled notice -- a Loopback has no counter block at all -- "
        "not required by the schema",
    ),
    IgnoreRule(
        r"^\d+ drops for unrecognized upper-level protocol$",
        "unrecognized-protocol drop counter, not required by the schema",
    ),
    IgnoreRule(
        r"^Received \d+ broadcast packets, \d+ multicast packets$",
        "input broadcast/multicast packet counts, not required by the schema",
    ),
    IgnoreRule(
        r"^Output \d+ broadcast packets, \d+ multicast packets$",
        "output broadcast/multicast packet counts, not required by the schema",
    ),
)

_INTERFACE_META_KEYS: tuple[str, ...] = (
    "interface",
    "admin_state",
    "line_state",
    "description",
    "mtu",
    "bandwidth_kbps",
    "mac_address",
    "encapsulation",
    "ip_address",
    "state_transitions",
    "last_link_flapped",
    "hardware_type",
)


def parse_xr_interface(output: str) -> dict[str, Any]:
    """Parse ``show interfaces <name>``.

    Three legitimate shapes -- see the section comment above. Raises
    :class:`ParseError` only when no recognisable header line
    (``<name> is <admin_state>, line protocol is <line_state>``) is found:
    genuinely unrecognised output.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    meta: dict[str, Any] = dict.fromkeys(_INTERFACE_META_KEYS)
    records: list[dict[str, Any]] = []
    consumed: list[str] = []
    found_interface = False

    for line in lines:
        if match := _HEADER.match(line):
            meta["interface"] = match["interface"]
            raw_admin_state = match["admin_state"]
            # IOS-XR's own wording is "administratively down"; normalised to
            # the compact "admin-down" the spec's admin_state vocabulary uses.
            meta["admin_state"] = (
                "admin-down" if raw_admin_state == "administratively down" else raw_admin_state
            )
            # Normalised the same way as admin_state, so a consumer sees one
            # vocabulary -- "admin-down" -- rather than two spellings of it.
            raw_line_state = match["line_state"]
            meta["line_state"] = (
                "admin-down" if raw_line_state == "administratively down" else raw_line_state
            )
            found_interface = True
            consumed.append(line)
            continue
        if match := _STATE_TRANSITIONS.match(line):
            meta["state_transitions"] = match["n"]
            consumed.append(line)
            continue
        if match := _HARDWARE.match(line):
            meta["hardware_type"] = match["hardware_type"]
            meta["mac_address"] = match["mac"]
            consumed.append(line)
            continue
        if match := _DESCRIPTION.match(line):
            meta["description"] = match["description"]
            consumed.append(line)
            continue
        if match := _INTERNET_ADDRESS.match(line):
            ip = match["ip"]
            meta["ip_address"] = None if ip == "Unknown" else ip
            consumed.append(line)
            continue
        if match := _MTU_BW.match(line):
            meta["mtu"] = match["mtu"]
            meta["bandwidth_kbps"] = match["bw"]
            consumed.append(line)
            continue
        if match := _ENCAPSULATION.match(line):
            meta["encapsulation"] = match["encap"]
            consumed.append(line)
            continue
        if match := _LAST_LINK_FLAPPED.match(line):
            meta["last_link_flapped"] = match["flap"]
            consumed.append(line)
            continue

        for pattern, counter_names in _COUNTER_LINES:
            if match := pattern.match(line):
                for counter_name in counter_names:
                    records.append({"counter": counter_name, "value": match[counter_name]})
                consumed.append(line)
                break

    if not found_interface:
        raise ParseError(
            "output does not contain a recognisable 'show interfaces' header line "
            "('<name> is <admin_state>, line protocol is <line_state>')"
        )

    return finalize(raw=output, meta=meta, records=records, consumed=consumed, ignores=INTERFACE_IGNORES)


# --------------------------------------------------------------------------- #
# cisco_xr: logging  (T-015)
# --------------------------------------------------------------------------- #
#
# ``show logging last <n>`` answers one shape on this fabric: an eight-line
# header block, then <n> entries from "Log Buffer". Surveyed across all 9
# healthy fixtures (1800 entries total): every single one matches one strict
# pattern with zero exceptions --
#
#   RP/0/RP0/CPU0:Aug 14 08:21:51.298 UTC: ssh_syslog_proxy[1191]: %SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL : sshd[55923]: Accepted authentication for clab from 172.20.250.6 port 38466 ssh2
#
# node : timestamp : process[pid] : %MNEMONIC : text -- note the space
# before the colon after the mnemonic, which is real IOS-XR framing, not a
# typo to normalise away. node is always "RP/0/RP0/CPU0" on this fabric, so
# it is matched literally rather than with a wildcard.
#
# The mnemonic is the field this parser exists for. discovery-loki.md (T-004)
# found it present on 100% of lines in the live Loki corpus too, in the same
# FACILITY-SEVERITY-CODE shape -- and it is what lets Stage 2 route an event
# to a flow by table lookup rather than a model judgement (D5). Splitting on
# the *last two* hyphens (``str.rsplit("-", 2)``) rather than a fixed-arity
# regex is deliberate: the facility half itself contains hyphens
# (``PKT_INFRA-PQMON``, ``SECURITY-SSHD_SYSLOG_PRX``), but the code half does
# not in any observed sample, so anchoring from the right is the one split
# that is correct for every mnemonic on disk -- and it makes both a
# full-mnemonic lookup table and a facility+code lookup table possible,
# which is the whole point of extracting the split at all.
#
# The device's own embedded timestamp -- not the "Sat Aug 15 ... UTC" banner
# IOS-XR prefixes the whole response with, and not any ingest time -- is the
# only true event time: discovery-loki.md found that syslog-ng stamps Loki's
# copy with ingest time (``timestamp("current")``), so the in-body timestamp
# is authoritative and is kept as the device's literal string, never
# reformatted or parsed into a datetime.
#
# No "device answered with an error" shape has been observed for this
# command (unlike bgp_neighbor's "not active" / route's "not in table"), so
# -- exactly as parse_xr_interface already does -- output with no
# recognisable header line raises ParseError rather than modelling a
# not-found meta shape with no fixture to justify it.

_SYSLOG_LOGGING = re.compile(
    r"^Syslog logging: (?P<enabled>enabled|disabled) "
    r"\((?P<dropped>\d+) messages dropped, \d+ flushes, \d+ overruns\)$"
)
# Console/Monitor/Trap/Buffer logging share one line shape; only the level
# feeds a different meta key. "Trap logging" is the OBS-041 field -- the
# evidence that the drop diagnosed there is downstream of the device, not on
# it, because the trap level is "informational" while only severities 3 and
# 4 ever reach the log collector (discovery-loki.md, section 6.1).
# The message count is captured, not discarded: `Buffer logging: level
# debugging, 593 messages logged` against 200 records returned is the exact,
# unambiguous statement that this window is count-limited and 393 buffered
# messages were not retrieved. That is a coverage fact (evidence-reduction.md
# §7) and inferring it from `returned >= requested` would be a guess where the
# device states it.
_LEVEL_LOGGING = re.compile(
    r"^(?P<kind>Console|Monitor|Trap|Buffer) logging: level (?P<level>\S+), "
    r"(?P<logged>\d+) messages logged$"
)
_LOGGING_TO = re.compile(r"^Logging to (?P<address>\S+), \d+ message lines logged$")
_LOG_BUFFER_SIZE = re.compile(r"^Log Buffer \((?P<size>\d+) bytes\):$")

_LOG_ENTRY = re.compile(
    r"^(?P<node>RP/0/RP0/CPU0):(?P<timestamp>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+\s+\w+): "
    r"(?P<process>[A-Za-z0-9_]+)\[(?P<pid>\d+)\]: %(?P<mnemonic>[A-Za-z0-9_-]+) : (?P<text>.*)$"
)

_LEVEL_META_KEY: dict[str, str] = {
    "Console": "console_level",
    "Monitor": "monitor_level",
    "Trap": "trap_level",
    "Buffer": "buffer_level",
}

_LEVEL_COUNT_KEY: dict[str, str] = {
    "Console": "console_messages_logged",
    "Monitor": "monitor_messages_logged",
    "Trap": "trap_messages_logged",
    "Buffer": "buffer_messages_logged",
}

# Section 0.10 accounting: deliberately empty. Every non-blank line the
# device emits for this command is either the IOS-XR timestamp banner /
# blank separator (XR_COMMON_IGNORES) or is itself consumed into meta/records
# below -- the header block has no decorative line that carries no
# extractable field, and every one of the 1800 surveyed entries matches
# ``_LOG_ENTRY``. Kept as a named, exported constant (rather than omitted)
# so the pattern of "one IGNORES constant per template" holds even when a
# template happens to need none, and so a future line shape that genuinely
# needs ignoring has an obvious place to go.
LOGGING_IGNORES: tuple[IgnoreRule, ...] = ()

_LOGGING_META_KEYS: tuple[str, ...] = (
    "lines",
    "window_start",
    "window_end",
    "syslog_enabled",
    "messages_dropped",
    "console_level",
    "console_messages_logged",
    "monitor_level",
    "monitor_messages_logged",
    "trap_level",
    "trap_messages_logged",
    "buffer_level",
    "buffer_messages_logged",
    "logging_to",
    "buffer_size_bytes",
)


def _empty_logging_meta() -> dict[str, Any]:
    """Every key present, ``None`` unless known -- never absent.

    ``lines`` is the one exception: per the T-015 spec's meta table it is
    always a count, ``"0"`` when there are no entries, never ``None``.
    """

    meta: dict[str, Any] = dict.fromkeys(_LOGGING_META_KEYS)
    meta["lines"] = "0"
    return meta


def parse_xr_logging(output: str) -> dict[str, Any]:
    """Parse ``show logging last <n>``.

    One legitimate shape -- see the section comment above -- plus a
    truncated variant (header only, zero entries) that is not an error: the
    device answered correctly, there is simply nothing to report yet.
    Raises :class:`ParseError` only when no recognisable header line
    (``Syslog logging: enabled|disabled ...``) is found at all: genuinely
    unrecognised output.

    A log entry line whose outer shape matches but whose mnemonic does not
    split cleanly into facility/severity/code counts as one malformed row
    (``meta["unparsed_rows"]``) rather than vanishing -- it is still
    *consumed*, so it does not also show up in ``unaccounted_lines``. The
    two counters mean different things: one line cannot be both "unknown
    shape" and "known shape, malformed content".
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    meta = _empty_logging_meta()
    consumed: list[str] = []
    records: list[dict[str, Any]] = []
    unparsed_rows = 0
    found_header = False

    for line in lines:
        if match := _SYSLOG_LOGGING.match(line):
            meta["syslog_enabled"] = match["enabled"] == "enabled"
            meta["messages_dropped"] = match["dropped"]
            found_header = True
            consumed.append(line)
        elif match := _LEVEL_LOGGING.match(line):
            meta[_LEVEL_META_KEY[match["kind"]]] = match["level"]
            meta[_LEVEL_COUNT_KEY[match["kind"]]] = match["logged"]
            consumed.append(line)
        elif match := _LOGGING_TO.match(line):
            meta["logging_to"] = match["address"]
            consumed.append(line)
        elif match := _LOG_BUFFER_SIZE.match(line):
            meta["buffer_size_bytes"] = match["size"]
            consumed.append(line)
        elif match := _LOG_ENTRY.match(line):
            mnemonic = match["mnemonic"]
            parts = mnemonic.rsplit("-", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                unparsed_rows += 1
                consumed.append(line)
                continue
            facility, severity, code = parts
            records.append(
                {
                    "timestamp": match["timestamp"],
                    "node": match["node"],
                    "process": match["process"],
                    "pid": match["pid"],
                    "mnemonic": mnemonic,
                    "facility": facility,
                    "severity": severity,
                    "code": code,
                    "text": match["text"],
                }
            )
            consumed.append(line)

    if not found_header:
        raise ParseError(
            "output does not contain a recognisable 'show logging' header line "
            "('Syslog logging: enabled|disabled ...')"
        )

    meta["lines"] = str(len(records))
    if records:
        meta["window_start"] = records[0]["timestamp"]
        meta["window_end"] = records[-1]["timestamp"]

    return finalize(
        raw=output,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=LOGGING_IGNORES,
        unparsed_rows=unparsed_rows,
    )


# --------------------------------------------------------------------------- #
# cisco_xr: ping  (T-016)
# --------------------------------------------------------------------------- #
#
# ``ping <address>`` answers one of two shapes on this fabric, both
# legitimate, surveyed across all 10 committed fixtures (9 success, 1 total
# failure):
#
#   Type escape sequence to abort.
#   Sending 5, 100-byte ICMP Echos to 10.255.0.31 timeout is 2 seconds:
#   !!!!!
#   Success rate is 100 percent (5/5), round-trip min/avg/max = 2/2/3 ms
#
# and, at 0% success (PE1/healthy/ping-192-0-2-1.txt):
#
#   Type escape sequence to abort.
#   Sending 5, 100-byte ICMP Echos to 192.0.2.1 timeout is 2 seconds:
#   .....
#   Success rate is 0 percent (0/5)
#
# **The critical difference is the last line.** At 0% success IOS-XR omits
# the "round-trip min/avg/max" clause entirely -- it is not zeroed and not
# present with empty values, the clause is simply absent from the line. One
# regex with an optional trailing group (the same "one regex, optional
# clause" shape ``_KNOWN_VIA``/``_STATE`` already use above) captures both:
# when the clause does not participate, its three named groups are ``None``
# by construction, which is exactly the "rtt_min/avg/max must be ``None``,
# never ``'0'``" contract this template exists to prove.
#
# The reply-pattern line ("!!!!!" / "....." / a real fabric's plausible
# "!!.!!") is parsed generically as a string of "!" and "." characters
# rather than special-cased on the two literal values observed, since a
# partial result is a real device behaviour this fabric's fixtures simply
# never happened to capture.
#
# Unlike bgp_neighbor/route, there is no "device answered with an error"
# shape for ping (no fixture shows one) -- the line that establishes this is
# recognisable ping output at all is the "Sending N, ..." request line, the
# first ping-specific content the device prints, echoing the header-line
# convention every other parser in this module uses. Output that never gets
# that far -- genuinely unrecognised input, or a capture cut off before even
# that line arrived -- raises :class:`ParseError`. A capture cut off *after*
# it (no reply line, no summary yet) is not an error: the device is still
# mid-response, exactly as ``parse_xr_logging``'s header-only truncation is
# not an error either.
#
# ``records`` is always ``[]``. BUILD-PLAN.md's T-016 table gives no record
# shape for ping -- the whole result is one summary, not a set of named
# per-probe objects, so there is nothing to build records from without
# inventing structure the device never reported.

_SENDING = re.compile(
    r"^Sending (?P<count>\d+), (?P<size_bytes>\d+)-byte ICMP Echos to (?P<target>\S+) "
    r"timeout is (?P<timeout_seconds>\d+) seconds:$"
)
_RESULT_STRING = re.compile(r"^(?P<result>[!.]+)$")
# The round-trip clause is optional -- see the shape note above -- so its
# three named groups are ``None`` whenever it does not participate, which is
# the 0%-success case's entire reason for being parsed this way rather than
# with two separate patterns.
_SUCCESS_RATE = re.compile(
    r"^Success rate is (?P<success_pct>\d+) percent \((?P<received>\d+)/(?P<sent>\d+)\)"
    r"(?:, round-trip min/avg/max = (?P<rtt_min>\d+)/(?P<rtt_avg>\d+)/(?P<rtt_max>\d+) ms)?$"
)

# Section 0.10 accounting. A single, anchored, specific rule per
# BUILD-PLAN.md 0.10's instruction against a broad catch-all.
PING_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^Type escape sequence to abort\.$",
        "operator hint IOS-XR prints before every ping/traceroute; not ping-specific data",
    ),
)

_PING_META_KEYS: tuple[str, ...] = (
    "target",
    "sent",
    "received",
    "success_pct",
    "loss_pct",
    "size_bytes",
    "timeout_seconds",
    "result_string",
    "rtt_min",
    "rtt_avg",
    "rtt_max",
)


def parse_xr_ping(output: str) -> dict[str, Any]:
    """Parse ``ping <address>``.

    Two legitimate shapes -- see the section comment above. Raises
    :class:`ParseError` only when no recognisable "Sending N, ...-byte ICMP
    Echos to ... timeout is ... seconds:" request line is found: genuinely
    unrecognised output, including a capture too short to contain even that
    much ping-specific content.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    meta: dict[str, Any] = dict.fromkeys(_PING_META_KEYS)
    consumed: list[str] = []
    found_request = False

    for line in lines:
        if match := _SENDING.match(line):
            meta["target"] = match["target"]
            meta["size_bytes"] = match["size_bytes"]
            meta["timeout_seconds"] = match["timeout_seconds"]
            found_request = True
            consumed.append(line)
        elif match := _SUCCESS_RATE.match(line):
            meta["success_pct"] = match["success_pct"]
            meta["received"] = match["received"]
            meta["sent"] = match["sent"]
            # The device does not print loss percentage itself; derived
            # rather than parsed, and kept a string like every other value.
            meta["loss_pct"] = str(100 - int(match["success_pct"]))
            meta["rtt_min"] = match["rtt_min"]
            meta["rtt_avg"] = match["rtt_avg"]
            meta["rtt_max"] = match["rtt_max"]
            consumed.append(line)
        elif match := _RESULT_STRING.match(line):
            meta["result_string"] = match["result"]
            consumed.append(line)

    if not found_request:
        raise ParseError(
            "output does not contain a recognisable 'Sending N, ...-byte ICMP Echos to ... "
            "timeout is ... seconds:' request line"
        )

    return finalize(raw=output, meta=meta, records=[], consumed=consumed, ignores=PING_IGNORES)


# --------------------------------------------------------------------------- #
# cisco_xr: traceroute  (T-017)
# --------------------------------------------------------------------------- #
#
# ``traceroute <address>`` answers one shape on this fabric, surveyed across
# all 9 committed fixtures: a two-line header, then one hop line per hop --
#
#   Type escape sequence to abort.
#   Tracing the route to 10.255.0.31
#
#    1  10.0.0.5 [MPLS: Label 24008 Exp 0] 2 msec  2 msec  2 msec
#    2  10.0.1.18 2 msec  *  2 msec
#
# Three probes per hop, each either "<N> msec" or "*" (no reply); the
# "[MPLS: Label <N> Exp <N>]" clause is optional per hop. Every one of the 9
# fixtures has a ``*`` somewhere in its final hop -- partial loss is the
# normal case on this fabric's MPLS core, not an anomaly.
#
# **``completed`` is deliberately NOT "the last hop's address equals the
# target".** Every trace to 10.255.0.31 in these fixtures ends at 10.0.1.16
# or 10.0.1.18 -- both are RR1's own interface addresses (verified against
# RR1/healthy/show-interfaces-gi0-0-0-0.txt: "Internet address is
# 10.0.1.16/31", and -gi0-0-0-1.txt: "10.0.1.18/31"), because the
# destination replies from whichever interface the probe arrived on, not
# from its loopback. The target address therefore never appears in a
# completed trace on this fabric, and a "last_hop == target" definition
# would call all nine successful traces incomplete -- wrong on 100% of the
# fixtures despite looking correct. A parser reads one command's text and
# has no inventory access -- it cannot know whether the destination was
# reached, since that requires resolving which router owns an interface
# address, and that resolution is ``descent.py``'s job, not a parser's. So
# ``completed`` is defined as exactly what the text supports: the final hop
# line returned at least one non-``*`` probe. This is NOT a claim that the
# destination was reached -- only that the trace did not go completely dark
# at its last hop. A consumer needing the stronger claim must resolve the
# address against the inventory itself.
#
# ``records``' shape deviates from the LLD's ``{hop, address, rtt}`` in one
# respect: ``rtt`` is generalised to ``rtt_msec``, and it is a **list** of
# three entries (one per probe, ``None`` for a ``*``), not a single number --
# collapsing three measured probes into one value would discard data the
# device actually reported, the same "don't fold repeated structure into one
# field" reasoning that keeps bgp_neighbor's address families as separate
# records rather than one summary.
#
# No "device answered with an error" shape has been observed for this
# command (no fixture shows one) -- exactly like ping, the line establishing
# recognisable traceroute output is the "Tracing the route to ..." line, the
# first traceroute-specific content the device prints. Output that never
# gets that far raises ParseError; a capture cut off after it (no hop lines
# yet) is not an error, exactly as parse_xr_logging's and parse_xr_ping's
# header-only truncations are not errors either.

_TRACING = re.compile(r"^Tracing the route to (?P<target>\S+)$")

# The strict hop-line shape: a hop number, an address, an optional MPLS
# clause, then exactly three probes (each "<N> msec" or "*"). ``\s+`` rather
# than literal single/double spaces because the device pads the probe
# separators with two spaces but the hop-number/address gap with one --
# matching on whitespace class is simpler and no less specific than encoding
# both counts literally.
_HOP_LINE = re.compile(
    r"^(?P<hop>\d+)\s+(?P<address>\S+)"
    r"(?:\s+\[MPLS: Label (?P<mpls_label>\d+) Exp (?P<mpls_exp>\d+)\])?"
    r"\s+(?P<probe1>\d+ msec|\*)\s+(?P<probe2>\d+ msec|\*)\s+(?P<probe3>\d+ msec|\*)$"
)
# A broader "this looks like a hop line" check, used only to classify a line
# that fails _HOP_LINE as a malformed row (unparsed_rows) rather than letting
# it fall through to unaccounted_lines -- the same "known shape, malformed
# content" vs. "unknown shape" distinction parse_xr_logging draws for a
# mnemonic that will not split.
_HOP_LOOSE = re.compile(r"^\d+\s+\S")

# Section 0.10 accounting. A single, anchored, specific rule -- the same
# operator-hint line PING_IGNORES declares, since IOS-XR prints it before
# both ping and traceroute.
TRACEROUTE_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        r"^Type escape sequence to abort\.$",
        "operator hint IOS-XR prints before every ping/traceroute; not traceroute-specific data",
    ),
)

_TRACEROUTE_META_KEYS: tuple[str, ...] = (
    "target",
    "hops",
    "completed",
    "max_hop_reached",
)


def _empty_traceroute_meta() -> dict[str, Any]:
    """Every key present, ``None`` unless known -- never absent.

    ``hops`` is the one exception: like ``route``'s ``path_count`` and
    ``logging``'s ``lines``, it is always a count, ``"0"`` with no hop lines,
    never ``None``. ``completed`` defaults ``False`` for the same reason: a
    trace with zero hop lines returned nothing, let alone a reply.
    """

    meta: dict[str, Any] = dict.fromkeys(_TRACEROUTE_META_KEYS)
    meta["hops"] = "0"
    meta["completed"] = False
    return meta


def parse_xr_traceroute(output: str) -> dict[str, Any]:
    """Parse ``traceroute <address>``.

    One legitimate shape -- see the section comment above -- plus a
    truncated variant (header only, zero hop lines) that is not an error:
    the device is still mid-response, exactly as ``parse_xr_logging``'s and
    ``parse_xr_ping``'s header-only truncations are not errors either.
    Raises :class:`ParseError` only when no recognisable "Tracing the route
    to ..." header line is found at all: genuinely unrecognised output.

    A hop line that looks like a hop (starts with a hop number followed by
    more content) but whose probe fields do not fit the strict three-probe
    shape counts as one malformed row (``meta["unparsed_rows"]``) rather
    than vanishing -- it is still *consumed*, so it does not also show up in
    ``unaccounted_lines``.

    ``completed`` is the final hop line having returned at least one
    non-``*`` probe -- see the section comment above for why this is
    deliberately *not* a claim that the destination itself was reached.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]

    meta = _empty_traceroute_meta()
    consumed: list[str] = []
    records: list[dict[str, Any]] = []
    unparsed_rows = 0
    found_header = False
    max_hop = 0

    for line in lines:
        if match := _TRACING.match(line):
            meta["target"] = match["target"]
            found_header = True
            consumed.append(line)
            continue
        if match := _HOP_LINE.match(line):
            probes = [match["probe1"], match["probe2"], match["probe3"]]
            rtt_msec = [None if probe == "*" else probe.split()[0] for probe in probes]
            records.append(
                {
                    "hop": match["hop"],
                    "address": match["address"],
                    "mpls_label": match["mpls_label"],
                    "mpls_exp": match["mpls_exp"],
                    "rtt_msec": rtt_msec,
                    "probes_sent": str(len(probes)),
                    "probes_lost": str(sum(1 for probe in probes if probe == "*")),
                }
            )
            max_hop = max(max_hop, int(match["hop"]))
            consumed.append(line)
            continue
        if _HOP_LOOSE.match(line):
            unparsed_rows += 1
            consumed.append(line)
            continue

    if not found_header:
        raise ParseError(
            "output does not contain a recognisable 'Tracing the route to ...' header line"
        )

    meta["hops"] = str(len(records))
    if records:
        meta["max_hop_reached"] = str(max_hop)
        meta["completed"] = any(probe is not None for probe in records[-1]["rtt_msec"])

    return finalize(
        raw=output,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=TRACEROUTE_IGNORES,
        unparsed_rows=unparsed_rows,
    )


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

# Keyed by (platform, template_name), mirroring parsers.PARSERS' (platform,
# intent) shape. A template parser takes the single rendered command's output.
TEMPLATE_PARSERS: dict[tuple[str, str], Callable[[str], dict[str, Any]]] = {
    ("cisco_xr", "bgp_neighbor"): parse_xr_bgp_neighbor,
    ("cisco_xr", "route"): parse_xr_route,
    ("cisco_xr", "interface"): parse_xr_interface,
    ("cisco_xr", "logging"): parse_xr_logging,
    ("cisco_xr", "ping"): parse_xr_ping,
    ("cisco_xr", "traceroute"): parse_xr_traceroute,
}

# Fields that move on their own between two captures of an unchanged device --
# uptimes, counters, timestamps. Excluded from comparison by diff_evidence and
# detect_flaps, exactly as parsers.VOLATILE_FIELDS is.
TEMPLATE_VOLATILE_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    # last_reset_ago is volatile; last_reset_reason deliberately is NOT -- a
    # change of reason ("Address family activated" -> "Peer closing down the
    # session") is a real signal worth diffing, and folding the two together
    # would have discarded it along with the noise.
    ("cisco_xr", "bgp_neighbor"): frozenset(
        {"up_for", "messages_received", "messages_sent", "last_reset_ago"}
    ),
    # installed_ago changes on every capture of an unchanged device -- the
    # same false-diff class CLAUDE.md records from before Phase 2. metric and
    # distance are deliberately NOT volatile: a change in either is a real
    # routing event, not noise.
    ("cisco_xr", "route"): frozenset({"installed_ago"}),
    # Split deliberately, unlike bgp_neighbor/route's single volatile set.
    # last_link_flapped and the four raw traffic counters grow/move on any
    # live, healthy link and are pure noise. The error/quality counters
    # (input_errors, crc, frame, overrun, ignored, abort, output_errors,
    # underruns, carrier_transitions) are the opposite: they sit at zero on a
    # healthy link, and a change in any of them is exactly the signal
    # interface_state (T-020) exists to catch -- so they are deliberately
    # left OUT of this set. Do not "tidy" this into an all-or-nothing
    # volatile set; the asymmetry between noisy and error counters is the
    # entire point.
    ("cisco_xr", "interface"): frozenset(
        {
            "last_link_flapped",
            "packets_input",
            "bytes_input",
            "total_input_drops",
            "packets_output",
            "bytes_output",
            "total_output_drops",
        }
    ),
    # The log window itself moves on every capture of a healthy device: more
    # entries arrive, the oldest ones scroll out of the last-N buffer, and
    # the dropped-message counter is live device state -- none of the three
    # are a signal about what changed in the *content* of the log.
    ("cisco_xr", "logging"): frozenset({"lines", "window_start", "window_end", "messages_dropped"}),
    # rtt_min/avg/max move on every capture of an unchanged, healthy path --
    # ordinary jitter, not a signal -- and result_string ("!!!!!" vs a
    # plausible "!!.!!") is exactly as noisy: it can vary run to run without
    # the network having changed. success_pct, loss_pct, sent and received
    # are deliberately NOT volatile: a ping going from 100% to 0% is the
    # entire signal this template exists to produce, and diff_evidence
    # masking it out would discard the one thing worth diffing -- the same
    # trap bgp_neighbor's last_reset_reason avoids above by staying out of
    # that template's volatile set.
    ("cisco_xr", "ping"): frozenset({"rtt_min", "rtt_avg", "rtt_max", "result_string"}),
    # rtt_msec and probes_lost move on every capture of an unchanged path --
    # ordinary jitter and transient probe loss, not a signal. address, hops,
    # completed and mpls_label are deliberately NOT volatile: a path
    # changing length, a hop's address changing (the path itself moved), a
    # trace ceasing to complete, or a label changing are all real events
    # worth diffing -- masking any of them out would hide exactly the change
    # this template exists to surface, the same trap bgp_neighbor's
    # last_reset_reason and ping's success_pct/loss_pct avoid above.
    ("cisco_xr", "traceroute"): frozenset({"rtt_msec", "probes_lost"}),
}

# The field identifying a record across two captures. ``None`` means records
# are positional and cannot be matched by identity.
TEMPLATE_RECORD_KEYS: dict[tuple[str, str], str | None] = {
    ("cisco_xr", "bgp_neighbor"): "address_family",
    ("cisco_xr", "route"): "next_hop",
    ("cisco_xr", "interface"): "counter",
    # Deliberately None, not "timestamp". A log entry has no stable identity
    # across two captures: it is an append-only stream, not a set of named
    # objects like an address family or a next hop. Two captures of the same
    # device share history but the set of entries only grows -- there is no
    # meaningful way to match "this record in capture A" to "this record in
    # capture B" by any field, timestamp included (a duplicate device
    # timestamp is not even guaranteed unique, per discovery-loki.md's
    # duplication finding). None is this contract's documented way to say
    # "positional, cannot be matched by identity" -- do not "fix" this to
    # "timestamp"; that would make diff_evidence produce nonsense (every
    # entry in a newer, longer window would misalign against the older one).
    ("cisco_xr", "logging"): None,
    # Deliberately None, and for a different reason than logging's: this
    # template has no records at all (see the section comment above), so
    # there is no per-record field for an identity key to name in the first
    # place -- not "no stable identity among several records" (logging's
    # case) but "there is nothing to identify".
    ("cisco_xr", "ping"): None,
    # The hop *number* is the stable identity across two captures of an
    # unchanged path -- the address at a given hop can legitimately change
    # when the path moves, and that is a difference worth seeing rather than
    # an identity change that hides it. Unlike route's next_hop, the address
    # here is deliberately NOT the identity key: a routing entry's paths are
    # identified by their next hop, but a traceroute's hops are identified
    # by their position in the path.
    ("cisco_xr", "traceroute"): "hop",
}


def has_template_parser(platform: str, template: str) -> bool:
    return (platform, template) in TEMPLATE_PARSERS


def template_volatile_fields(platform: str, template: str) -> frozenset[str]:
    return TEMPLATE_VOLATILE_FIELDS.get((platform, template), frozenset())


def template_record_key(platform: str, template: str) -> str | None:
    return TEMPLATE_RECORD_KEYS.get((platform, template))


def parse_template_output(
    platform: str,
    template: str,
    output: str,
) -> tuple[dict[str, Any] | None, str]:
    """Parse one rendered template command's output.

    Returns ``(parsed, status)`` with ``status`` one of ``PARSE_OK``,
    ``PARSE_UNAVAILABLE`` (no parser for this platform/template) or
    ``PARSE_FAILED``. Mirrors ``parsers.parse_intent`` exactly, including its
    two strictness rules: a parser that raises is a failure, and a parser that
    returns neither records nor meta from non-empty output is a failure rather
    than a silent empty success.

    One rule this adds on top: a result missing its section 0.10 accounting is
    ``PARSE_FAILED`` too. A parser that bypassed :func:`finalize` has no
    measurable coverage, and evidence whose completeness is unknown must not be
    presented as parsed -- that is the same reasoning ``health.py`` applies with
    ``unevaluated``.
    """

    parser = TEMPLATE_PARSERS.get((platform, template))
    if parser is None:
        return None, PARSE_UNAVAILABLE
    if not output or not output.strip():
        return None, PARSE_FAILED

    try:
        parsed = parser(output)
    except ParseError:
        return None, PARSE_FAILED
    except Exception:  # noqa: BLE001 - a parser bug must not break collection.
        return None, PARSE_FAILED

    if not isinstance(parsed, dict):
        return None, PARSE_FAILED
    if not parsed.get("records") and not parsed.get("meta"):
        return None, PARSE_FAILED

    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None, PARSE_FAILED
    if "unaccounted_lines" not in meta or "unparsed_rows" not in meta:
        return None, PARSE_FAILED

    return parsed, PARSE_OK
