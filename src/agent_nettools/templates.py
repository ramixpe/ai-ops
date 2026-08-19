"""Validated, parameterized command templates: canonicalize by reconstruction.

Phase 0-4's allowlist (``platforms.APPROVED_COMMANDS``) is a flat set of exact
strings, so it can only express zero-argument commands. ``show route
<prefix>``, ``show bgp neighbor <ip>``, ``show interfaces <name>``, ``ping``,
and ``traceroute`` are all unreachable even though they are exactly the
follow-up questions an operator -- or an agent that just read "peer 10.255.0.31
is Idle" -- needs to ask next. This module adds a second, narrower allowlist
that can accept one caller-supplied value per command while remaining just as
hard to escape as the static one.

THE SECURITY MODEL: reconstruction, never pass-through
=======================================================
Caller-supplied text is never substituted into a command string. Every
parameter is first parsed into a typed Python object -- ``ipaddress
.IPv4Address``, ``ipaddress.IPv4Network``, a range-checked ``int``, or a
regex-validated interface name -- and the command is rendered from *that
object's own canonical string form* (``str(parsed)``), never from the
original text. This is what makes "weird but technically parseable" input
harmless: ``ipaddress.IPv4Address("01.1.1.1")`` simply raises (leading zeros
are ambiguous octal/decimal and CPython rejects them), so there is no
representation of that string that could ever reach ``str.format``.
"Validate-then-pass-through" -- checking the original text with a regex and
then interpolating the text itself -- is explicitly NOT what happens here,
because a regex broad enough to accept every legitimate value is also broad
enough to admit a lookalike the author did not anticipate. Reconstruction
sidesteps that: the rendered value can never be anything except what a
strict, well-tested stdlib parser (or, for interfaces, a fully anchored
regex over a closed charset) is willing to produce as its OWN output.

Five layered, deliberately redundant defenses:

 1. Reject any non-ASCII codepoint before anything else. This alone kills
    homoglyph substitution (Cyrillic "а" for Latin "a") and fullwidth-digit
    normalization tricks -- both encode to *something* an over-permissive
    regex might accept, but neither is ASCII.
 2. Reject control characters, whitespace, and an explicit forbidden set
    (``| ; & > < ` $ { } \\n \\r \\t \\0``) with a clear message -- even
    though the typed parsers below already exclude all of this implicitly.
    Purely defense in depth, and it produces a far better error message than
    "not a valid IPv4 address" would for a caller who typed a pipe.
 3. A hard per-parameter length bound, checked before any parser runs.
 4. After rendering, re-validate the *assembled* command: it must contain no
    forbidden character, and it must match the shape the template itself
    promises (its literal text, verbatim, with placeholders substituted and
    nothing else). This is the one check that is not about the input -- it
    is about catching a template that was itself written wrong.
 5. The rendered command's first word must be a member of the explicit
    read-only verb allowlist, ``{"show", "ping", "traceroute"}``. Nothing
    else may ever be rendered, by any template, for any platform, ever.

Why ``|`` gets its own callout: IOS-XR's CLI supports piping a ``show``
command's output to ``| file disk0:/...``, which *writes a file to the
device*. A pipe reaching the device is a state change, not merely an
information leak -- so it must be structurally impossible, not just
discouraged by convention. It appears in ``FORBIDDEN_CHARACTERS`` and every
layer above independently blocks it (typed parsers never produce a ``|``;
the raw-text check rejects it explicitly; the post-render check rejects it
again; and even if all of that failed, the rendered string still could not
satisfy a template's shape regex without an operator supplying it, which
layer 2 already refused).

``platforms.py`` re-exports this module's public names and stays the single
place a reviewer needs to look to see everything that can ever be sent to a
device -- both the static allowlist and every template.
"""

from __future__ import annotations

import functools
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Mapping

# The only verbs a rendered template command may ever start with. "ping" and
# "traceroute" generate traffic but change no device state; "show" is pure
# inspection. Nothing else -- no "clear", no "debug", no "monitor" -- is ever
# added here without a corresponding rewrite of this whole module's threat
# model.
VERB_ALLOWLIST: frozenset[str] = frozenset({"show", "ping", "traceroute"})

# Characters that would let one parameter value carry a second command, an
# output redirect, a pipe modifier (see the module docstring on why "|"
# specifically matters), or a shell/CLI escape. Mirrors
# tests/test_safety.py's FORBIDDEN_CHARACTERS for the static allowlist, plus
# whitespace-shaped control characters that a single-token allowlist entry
# never needed to worry about.
FORBIDDEN_CHARACTERS: frozenset[str] = frozenset("|;&><`${}\n\r\t\0")

# Generous but bounded: no legitimate IPv4 address, prefix, interface name, or
# small integer is anywhere close to this long. Checked before any typed
# parser runs, so a pathological-length input fails cheaply and up front.
MAX_PARAM_LENGTH = 128

# Interface names across IOS-XR (and IOS-XE/Junos) use letters, digits, and a
# small punctuation set for slot/port separators (`/`), sub-interfaces (`.`),
# and named/bundle interfaces (`-`, `_`): `Gi0/0/0/2.300`, `Mg0/RP0/CPU0/0`,
# `Bundle-Ether23`, `Loopback0`, `srte_c_10_ep`, `Nu0`, `BV200`. No stdlib
# parser exists for this, so the guarantee is a fully anchored regex (no
# unanchored `re.search` anywhere near this) over a closed charset, plus a
# length bound -- not "looks roughly right".
_INTERFACE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]{0,62}$")

# A plain, optionally-signed decimal integer -- nothing else. Rejects
# "0x10" (hex), "1e3" (exponent), "1.5" (float), and "1_0" (Python's
# underscore-grouping, which bare int() would otherwise silently accept) all
# before int() is ever called, which is stricter than the spec's minimum
# ("int(value) then range-check") but costs nothing and rules out a whole
# class of "technically an int()-parseable string" surprises.
_PLAIN_INTEGER_RE = re.compile(r"^-?[0-9]+$")

# Matches a `{name}` placeholder in a template's format string.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class TemplateValidationError(ValueError):
    """Raised when a template parameter or a rendered command fails validation.

    Never raised with partial state: every parameter is validated before
    ``str.format`` is ever called, so rejection always means nothing was
    rendered at all -- there is no such thing as a half-assembled command.
    """


class UnknownTemplateError(LookupError):
    """Raised when a platform has no template registered under a given name."""


def _reject_unsafe_text(name: str, value: object) -> str:
    """Layers 1-3: type, length, ASCII-only, control/whitespace/forbidden chars.

    Applied to every parameter's raw text before any type-specific parser
    (``ipaddress.*``, the interface regex, the integer parser) ever sees it.
    """

    if not isinstance(value, str):
        raise TemplateValidationError(
            f"{name}: expected a string, got {type(value).__name__}"
        )
    if not value:
        raise TemplateValidationError(f"{name}: must not be empty")
    if len(value) > MAX_PARAM_LENGTH:
        raise TemplateValidationError(
            f"{name}: exceeds the maximum length of {MAX_PARAM_LENGTH} characters"
        )
    if not value.isascii():
        raise TemplateValidationError(f"{name}: non-ASCII characters are not allowed")
    for char in value:
        if char.isspace():
            raise TemplateValidationError(f"{name}: whitespace is not allowed: {value!r}")
        if ord(char) < 0x20 or ord(char) == 0x7F:
            raise TemplateValidationError(
                f"{name}: contains a control character: {value!r}"
            )
        if char in FORBIDDEN_CHARACTERS:
            raise TemplateValidationError(
                f"{name}: contains a forbidden character {char!r}: {value!r}"
            )
    return value


def _parse_ipv4_address(name: str, value: str) -> str:
    _reject_unsafe_text(name, value)
    try:
        parsed = ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise TemplateValidationError(f"{name}: not a valid IPv4 address: {value!r}") from exc
    return str(parsed)


def _parse_ipv4_prefix(name: str, value: str) -> str:
    _reject_unsafe_text(name, value)
    try:
        parsed = ipaddress.IPv4Network(value, strict=False)
    except ValueError as exc:
        raise TemplateValidationError(f"{name}: not a valid IPv4 prefix: {value!r}") from exc
    return str(parsed)


def _parse_interface_name(name: str, value: str) -> str:
    _reject_unsafe_text(name, value)
    if not _INTERFACE_NAME_RE.fullmatch(value):
        raise TemplateValidationError(f"{name}: not a valid interface name: {value!r}")
    # "." and "/" have to be allowed -- real names need them ("Gi0/0/0/2.300",
    # "Mg0/RP0/CPU0/0") -- which means the charset alone would admit "Gi../../x".
    # That is not exploitable: an interface name is never used in a filesystem
    # context, so the device simply rejects it as an invalid interface. But ".."
    # is meaningless in every real interface name, so refusing it costs nothing
    # and keeps the accepted set free of path-shaped values.
    if ".." in value:
        raise TemplateValidationError(f"{name}: not a valid interface name: {value!r}")
    # fullmatch already proves the whole string is exactly the safe charset;
    # there is no separate "parsed object" to reconstruct from, so the
    # validated text *is* its own canonical form.
    return value


def _parse_bounded_int(name: str, value: str, *, minimum: int, maximum: int) -> str:
    _reject_unsafe_text(name, value)
    if not _PLAIN_INTEGER_RE.fullmatch(value):
        raise TemplateValidationError(f"{name}: not a plain integer: {value!r}")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise TemplateValidationError(
            f"{name}: must be between {minimum} and {maximum}, got {parsed}"
        )
    return str(parsed)


@dataclass(frozen=True)
class ParamType:
    """Base type for a template parameter. Subclasses implement ``parse``.

    ``parse`` must either return the parameter's *canonical* string form
    (never the raw input) or raise ``TemplateValidationError`` -- there is no
    third outcome.
    """

    def parse(self, name: str, value: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(frozen=True)
class IPv4AddressParam(ParamType):
    """A single IPv4 host address, e.g. a BGP neighbor or ping target."""

    def parse(self, name: str, value: str) -> str:
        return _parse_ipv4_address(name, value)


@dataclass(frozen=True)
class IPv4PrefixParam(ParamType):
    """An IPv4 address or network, e.g. a route lookup target.

    ``strict=False`` so a host address with trailing host bits (a common way
    to mean "the route covering this address") is accepted like
    ``ipaddress.IPv4Network`` already allows, rather than requiring a
    caller to zero the host bits themselves.
    """

    def parse(self, name: str, value: str) -> str:
        return _parse_ipv4_prefix(name, value)


@dataclass(frozen=True)
class InterfaceNameParam(ParamType):
    """An interface name, validated by anchored regex over a closed charset."""

    def parse(self, name: str, value: str) -> str:
        return _parse_interface_name(name, value)


@dataclass(frozen=True)
class BoundedIntParam(ParamType):
    """An integer parameter restricted to an inclusive ``[minimum, maximum]``."""

    minimum: int
    maximum: int

    def parse(self, name: str, value: str) -> str:
        return _parse_bounded_int(name, value, minimum=self.minimum, maximum=self.maximum)


@dataclass(frozen=True)
class Template:
    """One parameterized, validated command template.

    ``format_string`` uses ``str.format`` placeholders (``{name}``) that must
    match ``params`` exactly -- one placeholder per declared parameter, no
    more and no fewer (``tests/test_safety.py`` pins this across every
    platform and template, rather than trusting each registration by eye).

    ``active_probe`` marks ``ping``/``traceroute``: they generate traffic
    (unlike every ``show`` template) even though they change no device
    state, and are gated separately -- see ``network_tools.run_template`` and
    ``NETTOOLS_ALLOW_ACTIVE_PROBES``.

    ``read_timeout`` is a hint to the transport layer: ``show`` output
    returns quickly, but ``ping``/``traceroute`` can legitimately take much
    longer (a traceroute across several hops, or a ping with default probe
    count), so batching them under one short universal timeout would report
    a slow-but-successful probe as a failure.
    """

    name: str
    format_string: str
    params: Mapping[str, ParamType] = field(default_factory=dict)
    active_probe: bool = False
    read_timeout: float = 10.0


# PLATFORM_TEMPLATES[platform][template_name] -> Template. Structured
# platform-major, exactly like platforms.PLATFORM_INTENTS, so that reviewing
# what one vendor can ever be asked (in parameterized form) is one block.
PLATFORM_TEMPLATES: dict[str, dict[str, Template]] = {
    # Verified against the live lab (XRd 7.11.2).
    "cisco_xr": {
        "route": Template(
            name="route",
            format_string="show route {prefix}",
            params={"prefix": IPv4PrefixParam()},
        ),
        "bgp_neighbor": Template(
            name="bgp_neighbor",
            format_string="show bgp neighbor {address}",
            params={"address": IPv4AddressParam()},
        ),
        "interface": Template(
            name="interface",
            format_string="show interfaces {interface}",
            params={"interface": InterfaceNameParam()},
        ),
        "logging": Template(
            name="logging",
            format_string="show logging last {count}",
            params={"count": BoundedIntParam(minimum=1, maximum=500)},
        ),
        "ping": Template(
            name="ping",
            format_string="ping {address}",
            params={"address": IPv4AddressParam()},
            active_probe=True,
            read_timeout=30.0,
        ),
        "traceroute": Template(
            name="traceroute",
            format_string="traceroute {address}",
            params={"address": IPv4AddressParam()},
            active_probe=True,
            read_timeout=60.0,
        ),
        # B-104: the config axis (D16). Section-scoped, never the whole
        # configuration -- `show running-config` with no qualifier is not on
        # this table and never will be; see `test_no_unqualified_running_
        # config_command_exists` in tests/test_config_section.py, which polices
        # exactly that absence across both this table and `platforms.py`'s
        # static one. Two sections only, chosen against what the descent
        # measurably cannot explain today rather than against the LLD's larger
        # illustrative set (`config_bgp`/`config_bgp_neighbor` are deliberately
        # NOT added -- see config_section.py's module docstring for why):
        #
        # `config_isis` -- B-496's PE3<->P2 break (fixture label
        # "isis-broken") is IS-IS's own worked example of the gap: the
        # interface rung reads healthy, the isis rung reads broken, and the
        # walk's honest answer today is `cause_not_localised` because nothing
        # reads whether IS-IS is even enabled on that interface, what area/NET
        # it belongs to, or whether authentication or network-type differs
        # from the far end -- exactly the causes `flows.py`'s own
        # `adjacency_not_up` finding text names and B-104 was opened against.
        # IOS-XR configures per-interface IS-IS participation *inside* this
        # same section (`router isis <tag> / interface <name> / ...`), so one
        # unscoped section answers all of it without a second, narrower
        # template.
        #
        # `config_interface` -- the bottom rung every flow's descent shares
        # (`bgp_session`, `isis_adjacency`, `ldp_session` all terminate at
        # `interface_line_down`). The status-side `interface` template already
        # answers "is it down"; only the config side answers "was it put there
        # on purpose" (`shutdown`) versus a real fault, which is the
        # observed-vs-intended distinction D16 exists to build.
        "config_isis": Template(
            name="config_isis",
            format_string="show running-config router isis",
        ),
        "config_interface": Template(
            name="config_interface",
            format_string="show running-config interface {interface}",
            params={"interface": InterfaceNameParam()},
        ),
        # B-515: the SR-TE policy detail a model needs to name WHICH SID or
        # segment list is involved in a down policy, not just that no
        # candidate path resolves (MCP §14b) -- `check_lab_sr_policies`
        # (the static `sr` intent) reports policy-level state only. Two
        # EXISTING param types, not a new "policy id" one: a color is a
        # `BoundedIntParam` (SR-TE color is a 32-bit value, RFC 8402/Cisco
        # convention) and an endpoint is the `IPv4AddressParam` every other
        # address-shaped template here already uses -- reusing both is what
        # keeps `tests/test_template_security.py`'s FROZEN `_VALID_BY_TYPE`
        # table (keyed by ParamType *class*) covering this template
        # automatically, with no edit to that frozen file. The single
        # caller-facing "colour:endpoint" identifier
        # `check_lab_sr_policies`'s own `policy` field already reports (e.g.
        # "20:10.255.0.13") is split into these two BEFORE it reaches this
        # template -- see `mcp_server/server.py`'s `get_lab_sr_policy_detail`
        # and `cli.py`'s `_cmd_sr_policy`, both of which I own; this file
        # never sees the composite string. "detail" is kept in the rendered
        # command even though a bare color+endpoint filter already prints
        # candidate-path/SID detail for a DOWN policy (verified live,
        # 2026-08-19) -- for an UP one it is NOT a no-op: it additionally
        # prints the programmed LSP's own State, confirming the forwarding
        # plane (not just the control plane) has the path.
        "sr_policy_detail": Template(
            name="sr_policy_detail",
            format_string=(
                "show segment-routing traffic-eng policy color {color} "
                "endpoint ipv4 {endpoint} detail"
            ),
            params={
                "color": BoundedIntParam(minimum=0, maximum=4294967295),
                "endpoint": IPv4AddressParam(),
            },
        ),
    },
    # Unverified: no IOS-XE device in the lab (see platforms.py). Included to
    # prove the template abstraction, like PLATFORM_INTENTS, holds across a
    # vendor whose syntax genuinely differs ("show ip route" / "show ip bgp
    # neighbors", not "show route" / "show bgp neighbor").
    "cisco_iosxe": {
        "route": Template(
            name="route",
            format_string="show ip route {prefix}",
            params={"prefix": IPv4PrefixParam()},
        ),
        "bgp_neighbor": Template(
            name="bgp_neighbor",
            format_string="show ip bgp neighbors {address}",
            params={"address": IPv4AddressParam()},
        ),
    },
}


def known_platform_templates(platform: str) -> tuple[str, ...]:
    """Return every template name a platform declares, in declaration order."""

    return tuple(PLATFORM_TEMPLATES.get(platform, {}))


def supports_template(platform: str, template_name: str) -> bool:
    """Return whether a platform has a template registered under this name."""

    return template_name in PLATFORM_TEMPLATES.get(platform, {})


def template_for(platform: str, template_name: str) -> Template:
    """Return one platform's template definition.

    Raises ``UnknownTemplateError`` when the platform exists but has no such
    template. Callers that want a structured result instead of an exception
    (``network_tools.run_template``) should check ``supports_template()``
    first, exactly like ``platforms.supports()``/``commands_for()``.
    """

    templates = PLATFORM_TEMPLATES.get(platform, {})
    if template_name not in templates:
        known = ", ".join(known_platform_templates(platform)) or "(none)"
        raise UnknownTemplateError(
            f"Platform {platform} has no template: {template_name}. Known: {known}."
        )
    return templates[template_name]


def split_sr_policy_id(policy_id: str) -> tuple[str, str]:
    """Split a caller-facing SR-TE policy id (``"<color>:<endpoint>"``, e.g.
    ``"20:10.255.0.13"`` -- the same shape ``parsers.parse_xr_sr``'s own
    ``policy`` field already reports) into the ``color``/``endpoint`` values
    the ``sr_policy_detail`` template's two declared params expect (B-515).

    **This is not a third validation layer.** ``render_command`` still runs
    ``BoundedIntParam``/``IPv4AddressParam`` against whatever this returns --
    a malformed half (e.g. an endpoint carrying a forbidden character) is
    still refused there, safely, regardless of what this function does with
    it. This exists only so a caller holds ONE identifier -- the one
    ``check_lab_sr_policies`` already hands back -- rather than having to
    know the template splits it into two internally.

    Raises ``TemplateValidationError`` for a policy id with no ``:``
    separator or an empty half -- the one shape check this function makes,
    so a caller sees "not a valid policy id" rather than a confusing
    downstream error about a garbled endpoint.
    """

    color, sep, endpoint = (policy_id or "").partition(":")
    if not sep or not color or not endpoint:
        raise TemplateValidationError(
            f"sr_policy_detail: not a valid policy id (expected "
            f"colour:endpoint, e.g. '20:10.255.0.13'): {policy_id!r}"
        )
    return color, endpoint


@functools.lru_cache(maxsize=None)
def _shape_pattern(format_string: str) -> re.Pattern[str]:
    """Compile a regex that any correctly-rendered instance of a template must
    fullmatch: every placeholder becomes a generous wildcard and every other
    character is matched literally.

    This is layer 4's "matches the template's expected shape" check. It is
    deliberately not just "re-run ``str.format`` and compare" -- the point is
    to catch a *template* that was written wrong (e.g. one whose literal text
    doesn't actually start with an allowed verb, or that a future refactor
    accidentally concatenates something onto), not to re-validate parameters
    that were already validated by their ``ParamType``.
    """

    pieces: list[str] = []
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(format_string):
        pieces.append(re.escape(format_string[cursor : match.start()]))
        pieces.append(r".+")
        cursor = match.end()
    pieces.append(re.escape(format_string[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


def is_safe_rendered_command(command: str) -> bool:
    """Minimal, template-agnostic safety check on an already-rendered command.

    Deliberately independent of any specific ``Template``: this is the last
    gate immediately before a rendered command reaches the transport layer,
    mirroring how ``platforms.is_approved()`` is re-checked right before a
    static command is sent even though every caller is supposed to have
    filtered already. Checks the two invariants that must hold no matter how
    the string was produced: no forbidden/control character, and a verb from
    the read-only allowlist.
    """

    if not command or not command.isascii():
        return False
    for char in command:
        if ord(char) < 0x20 or char in FORBIDDEN_CHARACTERS:
            return False
    verb = command.split(" ", 1)[0]
    return verb in VERB_ALLOWLIST


def _validate_rendered_command(command: str, template: Template) -> None:
    """Layer 4 and 5: re-validate the fully assembled command."""

    if not is_safe_rendered_command(command):
        raise TemplateValidationError(
            f"rendered command failed the post-render safety check: {command!r}"
        )
    if not _shape_pattern(template.format_string).fullmatch(command):
        raise TemplateValidationError(
            f"rendered command does not match template {template.name!r}'s expected shape: "
            f"{command!r}"
        )


def render_command(platform: str, template_name: str, **params: str) -> str:
    """Render one validated command from a template and caller-supplied parameters.

    Every parameter is parsed into a typed object and the command is built
    from that object's canonical string form -- never from the caller's raw
    text (see the module docstring, "canonicalize by reconstruction"). Every
    parameter is validated *before* ``str.format`` is called even once, so a
    rejection always means nothing was rendered at all: there is no partially
    assembled command to leak.

    Raises ``UnknownTemplateError`` for an unknown platform/template pair and
    ``TemplateValidationError`` for any parameter or post-render violation.
    """

    template = template_for(platform, template_name)

    declared = set(template.params)
    supplied = set(params)
    if declared != supplied:
        missing = sorted(declared - supplied)
        extra = sorted(supplied - declared)
        raise TemplateValidationError(
            f"{template_name}: parameter mismatch (missing={missing}, unexpected={extra})"
        )

    # Every parameter is validated in this loop -- and only after every one of
    # them succeeds is `.format()` ever called. A single bad parameter among
    # several therefore still renders nothing at all.
    rendered_params: dict[str, str] = {
        name: template.params[name].parse(name, value) for name, value in params.items()
    }

    command = template.format_string.format(**rendered_params)
    _validate_rendered_command(command, template)
    return command
