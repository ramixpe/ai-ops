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
    "ROUTE_IGNORES",
    "TEMPLATE_PARSERS",
    "TEMPLATE_RECORD_KEYS",
    "TEMPLATE_VOLATILE_FIELDS",
    "XR_COMMON_IGNORES",
    "IgnoreRule",
    "ParseError",
    "account_lines",
    "finalize",
    "has_template_parser",
    "parse_template_output",
    "parse_xr_bgp_neighbor",
    "parse_xr_route",
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
_STATE = re.compile(r"^BGP state = (?P<state>[A-Za-z]+)(?:, up for (?P<up_for>\S+))?$")
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
    IgnoreRule(r"^Socket not armed for io, armed for read, armed for write$", "socket bookkeeping"),
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
            if match["up_for"]:
                meta["up_for"] = match["up_for"]
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
# The registry
# --------------------------------------------------------------------------- #

# Keyed by (platform, template_name), mirroring parsers.PARSERS' (platform,
# intent) shape. A template parser takes the single rendered command's output.
TEMPLATE_PARSERS: dict[tuple[str, str], Callable[[str], dict[str, Any]]] = {
    ("cisco_xr", "bgp_neighbor"): parse_xr_bgp_neighbor,
    ("cisco_xr", "route"): parse_xr_route,
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
}

# The field identifying a record across two captures. ``None`` means records
# are positional and cannot be matched by identity.
TEMPLATE_RECORD_KEYS: dict[tuple[str, str], str | None] = {
    ("cisco_xr", "bgp_neighbor"): "address_family",
    ("cisco_xr", "route"): "next_hop",
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
