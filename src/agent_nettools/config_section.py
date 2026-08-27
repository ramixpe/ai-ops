"""B-104 -- the config axis (D16). Parsers for section-scoped configuration.

Every rung the deterministic descent (`descent.py`) can walk today reads
**operational state only** -- nine `show` intents, none of them
configuration. That is why a real fault regularly bottoms out at
`flows.CAUSE_NOT_LOCALISED`: the remaining candidate causes are all on the
config axis, and this build read none of it. B-496's "isis-broken" fixture
(PE3 <-> P2) is the worked example: the interface rung reads healthy, the
isis rung reads broken, and `flows.py`'s own `adjacency_not_up` finding text
names exactly the three things nothing here could check -- "area,
authentication, network type, or whether IS-IS is enabled on this interface
at all." This module is what lets a human (B-105/B-106 later let a *model*)
read that.

Three templates only -- section set and why
-------------------------------------------
`templates.py`'s `PLATFORM_TEMPLATES["cisco_xr"]` contains the bounded
entries `config_isis`, `config_interface`, and `config_ldp`. The LLD's illustrative table
(`docs/design/lld-investigation-layer.md` S5.5) also lists `config_bgp` and
`config_bgp_neighbor`; they are deliberately NOT added here.

* **`config_isis`** (`show running-config router isis`, no parameter) is
  B-496's own worked example, closed. IOS-XR configures per-interface IS-IS
  participation *inside* this section (`router isis <tag> / interface
  <name> / ...`), not under the interface's own configuration -- so one
  unscoped section answers "is IS-IS enabled on this interface at all",
  "what area/NET", "what network type" (point-to-point vs the broadcast
  default) and "is authentication configured", all at once, without a
  second, narrower template. Measured against five live devices (PE1, PE3,
  P1, P2, RR1) rather than assumed: the section is small (under 60 lines
  on the busiest device in this lab) and structurally identical across all
  five, which is what makes one unscoped template defensible instead of
  something objection-worthy under D16's "bound the config axis" rule.

* **`config_interface`** (`show running-config interface {interface}`,
  `InterfaceNameParam`) is the rung every flow's descent shares at the
  bottom -- `bgp_session`, `isis_adjacency` and `ldp_session` all terminate
  at `interface_line_down` with no way to say whether that was deliberate.
  The status-side `interface` template already answers "is it down"; only
  the config side answers "was it put there on purpose" (`shutdown`) versus
  a real fault -- the observed-vs-intended distinction D16 exists to build.

* **`config_ldp`** (`show running-config mpls ldp`, no parameter) answers the
    LDP acceptance gap directly: when an up interface has neither discovery nor
    a session, the flow can distinguish a deliberately absent LDP interface
    stanza from a configured interface whose LDP control plane is silent. Its
    parser retains only the configured interface names.

**`config_bgp`/`config_bgp_neighbor` refused for now.** `bgp_session`'s own
unexplained finding (`peer_idle_transport_blocked` -- "an ACL, a
control-plane policy, or an administratively shut neighbour") is real and
config-axis-shaped in exactly the same way. It is left out because B-104's
brief is "a small, defensible starting set... more is not better", and
unlike IS-IS this build has no live-captured fixture demonstrating the BGP
config actually being the blocker the way B-496 demonstrates it for IS-IS --
adding it now would be scope grown from analogy, not from evidence. Add it
against a real case, the same way `config_isis` was added against this one.

Never `show running-config` unqualified
------------------------------------------
`templates.VERB_ALLOWLIST` and `tests/test_safety.py`'s frozen suite already
forbid every state-changing verb and every shell metacharacter in a
template's literal text; neither one polices "this literal text happens to
be the single most dangerous read command available" (bounded, but the
*whole* configuration, credentials included, on every call). That is what
`tests/test_config_section.py::test_no_unqualified_running_config_command_exists`
exists to check, over both `platforms.PLATFORM_INTENTS` and
`templates.PLATFORM_TEMPLATES`, on every platform -- not just `cisco_xr` --
so a future addition to *either* table cannot reintroduce it unnoticed.

Never a secret in a structured field
---------------------------------------
Neither parser below ever stores the text that follows an
`authentication`/`password`/`secret`-shaped keyword -- only whether the
keyword was present (`authentication_configured: bool`). Presence is a fact
about the topology worth diffing; the key material itself is exactly what
`fixtures.scrub_output`'s `SCRUB_PATTERNS` exist to remove from anything
captured to disk, and what this module refuses to promote into a `meta` or
`records` field in the first place -- belt and braces, not a substitute for
scrubbing captured fixtures (see `tests/test_config_section.py`'s scrubbing
tests, which exercise `scrub_output` against a synthetic line shaped like
what an IS-IS/interface authentication config line would look like, since no
live device in this lab configures one to capture for real).

Line accounting and projection
---------------------------------
Both parsers go through `finalize()`/`account_lines()` exactly like every
other template parser (S0.10) -- `meta["unaccounted_lines"]` is the escape
hatch for anything this module does not recognise, never a silent drop. That
field, plus `data.commands` (the rendered command's raw output), is already
withheld to a bare count (`..._withheld`) by `model_egress.project_envelope`
and `mcp_server.boundary.sanitize` for *every* envelope shape in this
package -- `RAW_TEXT_KEYS = {"commands", "unaccounted_lines"}` is
content-agnostic, so a config envelope inherits invariant 4's guarantee with
no new code at either egress point. The one field genuinely worth a model's
attention that is also genuinely free text -- `config_interface`'s
`description`, an operator-authored string (`"TEST-LAB-CHANGE"` is a real
one, captured live on PE3's Gi0/0/0/0 during this task) -- is added to
`model_egress.FREE_TEXT_FIELDS` as `("config_interface", "description")`,
which `mcp_server.boundary` inherits by import rather than by a second
table. Nothing else in either parsed shape is free text: interface/AS/area
identifiers, booleans and short enumerated tokens pass through unquoted,
exactly as D16 says the model should mostly be reading -- structure, not
documents.

Registration
--------------
The two parser functions below are merged into
`template_parsers.TEMPLATE_PARSERS`/`TEMPLATE_RECORD_KEYS`/
`TEMPLATE_VOLATILE_FIELDS` at the bottom of `template_parsers.py`, imported
*after* that module's own `IgnoreRule`/`IgnoreKind`/`finalize`/
`account_lines`/`XR_COMMON_IGNORES`/`PARSE_*`/`ParseError` are already bound
-- the same ordering `parsers.py` already relies on for its own late
cross-import of those same primitives (see that module's docstring). This
file imports from `template_parsers` at module scope and is safe to import
from there specifically because nothing above that point in
`template_parsers.py` needs anything from here.
"""

from __future__ import annotations

from typing import Any

from .template_parsers import (
    IgnoreKind,
    IgnoreRule,
    ParseError,
    finalize,
)

__all__ = [
    "CONFIG_INTERFACE_IGNORES",
    "CONFIG_LDP_IGNORES",
    "CONFIG_ISIS_IGNORES",
    "CONFIG_RECORD_KEYS",
    "CONFIG_TEMPLATE_PARSERS",
    "CONFIG_VOLATILE_FIELDS",
    "parse_xr_config_interface",
    "parse_xr_config_ldp",
    "parse_xr_config_isis",
]


def _nonblank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _indent(raw_line: str) -> int:
    """Leading-space count. IOS-XR's `show running-config` pretty-printer
    uses exactly one space per nesting level -- measured against five live
    devices (PE1, PE3, P1, P2, RR1), not assumed."""

    return len(raw_line) - len(raw_line.lstrip(" "))


# --------------------------------------------------------------------------- #
# config_isis
# --------------------------------------------------------------------------- #

# Section 0.10 accounting for `show running-config router isis`. Every one of
# these is real, declared content this build's checks do not need yet
# (IgnoreKind.NOT_NEEDED_YET) -- not decoration. Measured against PE1, PE3,
# P1, P2 and RR1's live config: identical shape on every one of the five.
# Content-matched, not depth-matched, which is safe here because the one
# token that appears at two different scopes ("address-family ipv4/ipv6
# unicast") is always *consumed* directly by the per-interface extraction
# code below before this table is ever consulted for it -- see
# `parse_xr_config_isis`'s docstring.
CONFIG_ISIS_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^log adjacency changes$", "operational logging toggle, not a dependency", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^lsp-gen-interval maximum-wait \d+$", "LSP pacing timer, not a dependency", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^metric-style wide level \d+$", "global AF metric encoding, not an adjacency dependency", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^mpls traffic-eng level-2-only$", "global AF traffic-engineering toggle", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^segment-routing mpls$", "global AF segment-routing toggle", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(
        r"^address-family (ipv4|ipv6) unicast$",
        "global-scope AF opener -- the per-interface occurrence of this same "
        "line is consumed directly by the interface-record extraction below, "
        "never reaches this table",
        IgnoreKind.NOT_NEEDED_YET,
    ),
    IgnoreRule(r"^flex-algo \d+$", "flexible-algorithm definition, unrelated to adjacency formation", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^metric-type delay$", "flex-algo metric type", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^advertise-definition$", "flex-algo advertisement toggle", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^prefix-sid index \d+$", "SR prefix-SID index, traffic-engineering detail", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^prefix-sid algorithm \d+ index \d+$", "SR flex-algo prefix-SID index", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^bfd minimum-interval \d+$", "BFD timer, not an IS-IS adjacency dependency", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^bfd multiplier \d+$", "BFD timer", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^hello-padding disable$", "Hello PDU padding toggle", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^fast-reroute per-prefix$", "FRR toggle, traffic-engineering detail", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^fast-reroute per-prefix ti-lfa$", "FRR TI-LFA toggle", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^!$", "block-end marker at any nesting depth", IgnoreKind.NO_EXTRACTABLE_FIELD),
)

_CONFIG_ISIS_META_KEYS: tuple[str, ...] = ("instance", "is_type", "net", "authentication_configured")


def _empty_config_isis_meta() -> dict[str, Any]:
    meta: dict[str, Any] = dict.fromkeys(_CONFIG_ISIS_META_KEYS)
    meta["authentication_configured"] = False
    return meta


def parse_xr_config_isis(output: str) -> dict[str, Any]:
    """Parse `show running-config router isis`.

    One meta block (process tag, level, NET, whether authentication is
    configured anywhere at global scope) plus one record per `interface`
    stanza -- `adjacency_not_up`'s own three named causes, structured:
    `interface` is present in `records` at all answers "is IS-IS enabled on
    this interface"; `point_to_point` answers "network type"; `net` (meta)
    is the area/system-id identity to compare across two devices;
    `authentication_configured` (meta and per-record) answers the third.

    Never captures what follows an `authentication ...` line -- only that
    one was seen. See the module docstring's "never a secret" section.

    Scope tracking is minimal on purpose: the only token that means two
    different things depending on where it sits is `address-family ipv4/ipv6
    unicast` (global toggle vs. "IS-IS runs this AF on this interface"), so
    the walk below tracks exactly one thing -- whether the current line is a
    *direct* child of an open `interface` stanza (one level of indent below
    the line that opened it) -- rather than a general nested-block parser.
    Everything else is content-matched by `CONFIG_ISIS_IGNORES` regardless of
    depth, which is safe only because none of those tokens collide in
    meaning across scopes; if a future capture ever shows one that does, that
    is a real design signal, not a shortcut to paper over here.

    Raises `ParseError` when no `router isis <tag>` header line is found at
    all -- unrecognised output, not a "no process configured" state. Whether
    a P-router with genuinely no ISIS process gives a *different*,
    recognisable response is not measured in this lab (every device here
    runs IS-IS); until it is, that case reports `PARSE_FAILED` rather than a
    guessed `PARSE_OK` shape -- a documented limit, not a state (BUILD-PLAN
    S0.13).
    """

    lines = _nonblank_lines(output)
    meta = _empty_config_isis_meta()
    records: list[dict[str, Any]] = []
    consumed: list[str] = []

    current: dict[str, Any] | None = None
    interface_depth: int | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        indent = _indent(raw_line)

        # Close the open interface record once we return to its own depth
        # (a "!" at that same indent) or move to a shallower line entirely.
        if current is not None and interface_depth is not None and indent <= interface_depth:
            records.append(current)
            current = None
            interface_depth = None

        if stripped.startswith("router isis "):
            meta["instance"] = stripped[len("router isis "):].strip()
            consumed.append(stripped)
            continue

        if current is not None and indent == interface_depth + 1:
            # A direct child of the open interface stanza.
            if stripped == "passive":
                current["passive"] = True
                consumed.append(stripped)
                continue
            if stripped == "point-to-point":
                current["point_to_point"] = True
                consumed.append(stripped)
                continue
            if stripped.startswith("authentication"):
                current["authentication_configured"] = True
                consumed.append(stripped)
                continue
            if stripped.startswith("address-family "):
                af = stripped.split()[1]
                if af not in current["address_families"]:
                    current["address_families"].append(af)
                consumed.append(stripped)
                continue
            # Falls through to CONFIG_ISIS_IGNORES (bfd/hello-padding) or,
            # unrecognised, to unaccounted_lines -- never silently dropped.
            continue

        if current is None and indent == 1 and stripped.startswith("interface "):
            name = stripped[len("interface "):].strip()
            current = {
                "interface": name,
                "passive": False,
                "point_to_point": False,
                "authentication_configured": False,
                "address_families": [],
            }
            interface_depth = indent
            consumed.append(stripped)
            continue

        if current is None and indent == 1:
            if stripped.startswith("is-type "):
                meta["is_type"] = stripped[len("is-type "):].strip()
                consumed.append(stripped)
                continue
            if stripped.startswith("net "):
                meta["net"] = stripped[len("net "):].strip()
                consumed.append(stripped)
                continue
            if stripped.startswith("authentication"):
                meta["authentication_configured"] = True
                consumed.append(stripped)
                continue

        # Everything else (global AF/flex-algo internals, nested AF details
        # inside an interface, bare "!" at any depth) is content-matched by
        # CONFIG_ISIS_IGNORES via account_lines() below, or -- if genuinely
        # unrecognised -- surfaced in unaccounted_lines rather than dropped.

    if current is not None:
        records.append(current)

    if meta["instance"] is None:
        raise ParseError("no 'router isis <tag>' header line found")

    return finalize(
        raw=output,
        meta=meta,
        records=records,
        consumed=consumed,
        ignores=CONFIG_ISIS_IGNORES,
    )


# --------------------------------------------------------------------------- #
# config_interface
# --------------------------------------------------------------------------- #

# Nothing observed in five live samples (PE3 Gi0/0/0/0 and Gi0/0/0/1, P2
# Gi0/0/0/4, PE2 BVI200, PE1 Lo0 and Gi0/0/0/2.300) needs a declared ignore --
# every non-structural line seen so far is extracted. Kept as an explicit
# (empty) table, not omitted, so the next line shape this build meets has an
# obvious place to be declared rather than silently added to `consumed`
# in-line.
CONFIG_INTERFACE_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^!$", "block-end marker", IgnoreKind.NO_EXTRACTABLE_FIELD),
)

_CONFIG_INTERFACE_META_KEYS: tuple[str, ...] = (
    "interface", "description", "shutdown", "vrf", "ipv4_address", "ipv4_netmask",
)


def _empty_config_interface_meta() -> dict[str, Any]:
    meta: dict[str, Any] = dict.fromkeys(_CONFIG_INTERFACE_META_KEYS)
    meta["shutdown"] = False
    meta["ipv6_addresses"] = []
    return meta


def parse_xr_config_interface(output: str) -> dict[str, Any]:
    """Parse `show running-config interface {interface}`.

    Meta-only, like `template_parsers.parse_xr_config_isis`'s sibling status
    template `interface` -- one subject, nothing to key a `records` table by
    (`TEMPLATE_RECORD_KEYS[("cisco_xr", "config_interface")]` is `None` for
    that reason, matching `facts`/`ping`'s precedent). `description` is the
    one genuinely free-text field (an operator-authored string -- captured
    live on PE3's Gi0/0/0/0 during this task: `"TEST-LAB-CHANGE"`, on an
    interface that turned out to carry no `ipv4 address` at all despite
    being enabled for IS-IS -- see `model_egress.FREE_TEXT_FIELDS`'s
    `("config_interface", "description")` entry, which is what keeps that
    string quoted and budgeted rather than passed to a model unmarked).

    `shutdown` is declared from IOS-XR's well-known, single-token config
    grammar (a bare `shutdown` line) rather than measured against a live
    sample -- no interface in this lab is currently administratively shut,
    so there is nothing to capture it from. Covered instead by a hand-typed
    synthetic case in `tests/test_config_section.py`, the same pattern
    `test_garbage_input_raises_parse_error_and_reports_parse_failed`
    already uses elsewhere in this package for a case the fixture corpus
    cannot supply. If real IOS-XR syntax ever differs from this, an
    unrecognised line surfaces in `unaccounted_lines` -- it is never
    silently misread as "not shut".

    `mtu <n>` overrides are NOT extracted: no live sample shows one, and
    guessing the field's exact position/spelling would be assumed, not
    measured. A device using one shows up as an unaccounted-lines count at
    the model boundary, not a silent gap -- a documented limit, not a state
    (BUILD-PLAN S0.13).

    Raises `ParseError` only when no `interface <name>` header line is
    found at all.
    """

    lines = _nonblank_lines(output)
    meta = _empty_config_interface_meta()
    consumed: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()

        if stripped.startswith("interface "):
            meta["interface"] = stripped[len("interface "):].strip()
            consumed.append(stripped)
            continue
        if stripped.startswith("description "):
            meta["description"] = stripped[len("description "):].strip()
            consumed.append(stripped)
            continue
        if stripped == "shutdown":
            meta["shutdown"] = True
            consumed.append(stripped)
            continue
        if stripped.startswith("vrf "):
            meta["vrf"] = stripped[len("vrf "):].strip()
            consumed.append(stripped)
            continue
        if stripped.startswith("ipv4 address "):
            parts = stripped[len("ipv4 address "):].split()
            if len(parts) == 2:
                meta["ipv4_address"], meta["ipv4_netmask"] = parts
            consumed.append(stripped)
            continue
        if stripped.startswith("ipv6 address "):
            meta["ipv6_addresses"].append(stripped[len("ipv6 address "):].strip())
            consumed.append(stripped)
            continue

        # Unrecognised -- surfaced via unaccounted_lines below, never dropped.

    if meta["interface"] is None:
        raise ParseError("no 'interface <name>' header line found")

    return finalize(
        raw=output,
        meta=meta,
        records=[],
        consumed=consumed,
        ignores=CONFIG_INTERFACE_IGNORES,
    )


# --------------------------------------------------------------------------- #
# config_ldp
# --------------------------------------------------------------------------- #

CONFIG_LDP_IGNORES: tuple[IgnoreRule, ...] = (
    IgnoreRule(r"^address-family (ipv4|ipv6)$", "address-family grouping", IgnoreKind.NOT_NEEDED_YET),
    IgnoreRule(r"^!$", "block-end marker", IgnoreKind.NO_EXTRACTABLE_FIELD),
)


def parse_xr_config_ldp(output: str) -> dict[str, Any]:
    """Parse `show running-config mpls ldp` into configured interface records."""

    records: list[dict[str, str]] = []
    consumed: list[str] = []
    found_header = False

    for raw_line in _nonblank_lines(output):
        stripped = raw_line.strip()
        if stripped == "mpls ldp":
            found_header = True
            consumed.append(stripped)
            continue
        if found_header and stripped.startswith("interface "):
            records.append({"interface": stripped[len("interface "):].strip()})
            consumed.append(stripped)

    if not found_header:
        raise ParseError("no 'mpls ldp' header line found")

    return finalize(
        raw=output,
        meta={},
        records=records,
        consumed=consumed,
        ignores=CONFIG_LDP_IGNORES,
    )


# --------------------------------------------------------------------------- #
# Registration -- merged into template_parsers.py's registries at the bottom
# of that file. See this module's docstring for the import-ordering note.
# --------------------------------------------------------------------------- #

CONFIG_TEMPLATE_PARSERS: dict[tuple[str, str], Any] = {
    ("cisco_xr", "config_isis"): parse_xr_config_isis,
    ("cisco_xr", "config_interface"): parse_xr_config_interface,
    ("cisco_xr", "config_ldp"): parse_xr_config_ldp,
}

# Configuration does not drift between two captures of an unchanged device
# the way operational counters do -- there is no equivalent of "up_for" or a
# packet counter here. Empty on purpose, not an oversight.
CONFIG_VOLATILE_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("cisco_xr", "config_isis"): frozenset(),
    ("cisco_xr", "config_interface"): frozenset(),
    ("cisco_xr", "config_ldp"): frozenset(),
}

CONFIG_RECORD_KEYS: dict[tuple[str, str], str | None] = {
    ("cisco_xr", "config_isis"): "interface",
    # Meta-only shape -- see parse_xr_config_interface's docstring.
    ("cisco_xr", "config_interface"): None,
    ("cisco_xr", "config_ldp"): "interface",
}


# When this module was imported before template_parsers, the latter completed
# its base registries before we reached these exports. Merge now so both import
# orders expose the same config templates.
from . import template_parsers as _template_parsers  # noqa: E402

if hasattr(_template_parsers, "TEMPLATE_PARSERS"):
    _template_parsers.TEMPLATE_PARSERS.update(CONFIG_TEMPLATE_PARSERS)
    _template_parsers.TEMPLATE_VOLATILE_FIELDS.update(CONFIG_VOLATILE_FIELDS)
    _template_parsers.TEMPLATE_RECORD_KEYS.update(CONFIG_RECORD_KEYS)
