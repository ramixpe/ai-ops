"""Per-platform command definitions: the allowlist, keyed by vendor.

This module is the single source of truth for what may be sent to a device. The
structure is **platform-major** so that everything the tool will ever send to one
vendor is reviewable in a single block, and adding a vendor is one new key:

    PLATFORM_INTENTS[platform][intent] -> tuple of commands

An *intent* is a vendor-neutral name for a question ("bgp", "isis"). The same
intent maps to different syntax per vendor -- ``show bgp summary`` on IOS-XR,
``show ip bgp summary`` on IOS-XE -- which is what lets one check run across a
mixed fabric. Intent names are the vocabulary used by the CLI subcommands, the
fabric runner, the MCP tools, and the evidence sections; there is exactly one
spelling of each.

Not every platform supports every intent. A platform simply omits the intents it
cannot answer, and callers get ``status: "unsupported"`` rather than an error --
a Junos box without SR-TE policies is not a failure.

Safety invariant: ``APPROVED_COMMANDS[platform]`` is an exact-match frozenset,
derived from this table. Membership is checked before credentials are loaded or a
socket is opened. Nothing here takes an argument or interpolates a value, so
there is no injection surface.

Verification status: ``cisco_xr`` is exercised against the live lab and captured
in ``tests/fixtures/``. The other platforms are declared from vendor
documentation but have **no live device to verify against yet**; treat their
command strings as unconfirmed until a device exists. Their presence is what
keeps the abstraction honest -- ``juniper_junos`` in particular shares no command
words with IOS-XR for the same intents.

Phase 5 adds a second, narrower allowlist for read-only commands that *do*
take one caller-supplied value -- ``show route <prefix>``, ``show bgp
neighbor <ip>``, ``ping``, and the like. Those live in ``templates.py`` as
``PLATFORM_TEMPLATES[platform][template_name] -> Template``, re-exported here
so this module stays the single place a reviewer looks to see everything that
may ever be sent to a device. Unlike the static table above, a template's
parameter is never passed through as text: it is parsed into a typed object
(an IPv4 address/network, a bounded integer, or a regex-validated interface
name) and the command is rendered from that object's own canonical string
form. See ``templates.py``'s module docstring for the full "canonicalize by
reconstruction" security model and its five layered defenses.
"""

from __future__ import annotations

from typing import Any

from .templates import (  # noqa: F401 - re-exported so this module stays the single authority
    PLATFORM_TEMPLATES,
    VERB_ALLOWLIST,
    BoundedIntParam,
    InterfaceNameParam,
    IPv4AddressParam,
    IPv4PrefixParam,
    ParamType,
    Template,
    TemplateValidationError,
    UnknownTemplateError,
    is_safe_rendered_command,
    known_platform_templates,
    render_command,
    supports_template,
    template_for,
)

# Ordered so evidence sections come out in a sensible narrative: what the device
# is, then its links, then its protocols, then its traffic engineering.
# "ldp"/"ldp_discovery" (B-109) sit between "isis" and "sr" -- both are MPLS
# control-plane facts, narratively following the IGP that carries their
# reachability and preceding the traffic-engineering layer built on top of MPLS.
INTENT_ORDER = ("facts", "interfaces", "bgp", "lldp", "isis", "ldp", "ldp_discovery", "sr")

PLATFORM_INTENTS: dict[str, dict[str, tuple[str, ...]]] = {
    # Verified against the live lab (XRd 7.11.2) and captured in tests/fixtures.
    "cisco_xr": {
        "facts": ("show running-config hostname", "show version"),
        "interfaces": ("show interfaces brief",),
        "bgp": ("show bgp summary",),
        "lldp": ("show lldp neighbors",),
        "isis": ("show isis neighbors",),
        # B-109. Two intents, not one: "ldp" is the session-level FSM state
        # (`show mpls ldp neighbor`), "ldp_discovery" is the Hello-level
        # adjacency that genuinely gates it (`show mpls ldp discovery`) -- a
        # real, in-protocol precondition, not a merely-correlated signal (see
        # the `ldp_session_up` docstring in checks.py). Verified against every
        # live lab device (all nine currently run LDP).
        "ldp": ("show mpls ldp neighbor",),
        "ldp_discovery": ("show mpls ldp discovery",),
        "sr": ("show segment-routing traffic-eng policy",),
    },
    # Unverified: no IOS-XE device in the lab. The hostname is not fetched
    # separately because it appears in `show version` output on IOS-XE.
    "cisco_iosxe": {
        "facts": ("show version",),
        "interfaces": ("show ip interface brief",),
        "bgp": ("show ip bgp summary",),
        "lldp": ("show lldp neighbors",),
        "isis": ("show isis neighbors",),
        # No "sr": IOS-XE SR-TE policy output is not equivalent to the IOS-XR
        # command, so it is left unsupported rather than guessed at.
    },
    # Unverified: no Junos device in the lab. Deliberately included because its
    # syntax diverges most -- "show isis adjacency" and "show interfaces terse"
    # share no words with the IOS-XR commands for the same intents, so it proves
    # the abstraction is real and not IOS-XR with a lookup table.
    "juniper_junos": {
        "facts": ("show version",),
        "interfaces": ("show interfaces terse",),
        "bgp": ("show bgp summary",),
        "lldp": ("show lldp neighbors",),
        "isis": ("show isis adjacency",),
    },
}

# The platform used when a device record does not declare one. Kept so a
# single-vendor deployment needs no inventory changes.
DEFAULT_PLATFORM = "cisco_xr"

# Exact-match allowlist per platform, derived so it can never drift from the
# table above.
APPROVED_COMMANDS: dict[str, frozenset[str]] = {
    platform: frozenset(
        command for commands in intents.values() for command in commands
    )
    for platform, intents in PLATFORM_INTENTS.items()
}

# Every command this tool may ever send, across all platforms. For safety review
# and doc generation only -- never use it to authorize a command, because that
# would let an IOS-XE command through to an IOS-XR device.
ALL_APPROVED_COMMANDS: frozenset[str] = frozenset(
    command for commands in APPROVED_COMMANDS.values() for command in commands
)


class UnsupportedIntentError(LookupError):
    """Raised when a platform has no commands for an intent."""


class UnknownPlatformError(LookupError):
    """Raised when a device declares a platform this tool has no definitions for."""


def known_platforms() -> tuple[str, ...]:
    """Return every platform with command definitions, in declaration order."""

    return tuple(PLATFORM_INTENTS)


def all_intents() -> tuple[str, ...]:
    """Return every intent any platform supports, in narrative order."""

    seen = {intent for intents in PLATFORM_INTENTS.values() for intent in intents}
    ordered = [intent for intent in INTENT_ORDER if intent in seen]
    # Defensive: an intent added to the table but not to INTENT_ORDER still shows up.
    ordered.extend(sorted(seen - set(INTENT_ORDER)))
    return tuple(ordered)


def intents_for(platform: str) -> tuple[str, ...]:
    """Return the intents one platform supports, in narrative order."""

    if platform not in PLATFORM_INTENTS:
        raise UnknownPlatformError(
            f"No command definitions for platform: {platform}. "
            f"Known platforms: {', '.join(known_platforms())}."
        )
    supported = PLATFORM_INTENTS[platform]
    return tuple(intent for intent in all_intents() if intent in supported)


def supports(platform: str, intent: str) -> bool:
    """Return whether a platform can answer an intent."""

    return intent in PLATFORM_INTENTS.get(platform, {})


def commands_for(platform: str, intent: str) -> tuple[str, ...]:
    """Return the approved commands for one intent on one platform.

    Raises ``UnknownPlatformError`` for an undefined platform and
    ``UnsupportedIntentError`` when the platform cannot answer the intent.
    Callers that want a structured result instead of an exception should check
    ``supports()`` first.
    """

    if platform not in PLATFORM_INTENTS:
        raise UnknownPlatformError(
            f"No command definitions for platform: {platform}. "
            f"Known platforms: {', '.join(known_platforms())}."
        )
    intents = PLATFORM_INTENTS[platform]
    if intent not in intents:
        raise UnsupportedIntentError(
            f"Platform {platform} does not support intent: {intent}. "
            f"Supported: {', '.join(intents_for(platform))}."
        )
    return intents[intent]


def is_approved(platform: str, command: str) -> bool:
    """Return whether a command is on this platform's exact-match allowlist.

    An unknown platform approves nothing, so a device with a typo'd platform
    fails closed.
    """

    return command in APPROVED_COMMANDS.get(platform, frozenset())


def device_platform(device: dict[str, Any]) -> str:
    """Return a device record's platform, falling back to the default."""

    return str(device.get("platform") or DEFAULT_PLATFORM)
