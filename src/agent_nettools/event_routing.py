"""Event → investigation routing: a pure function, deliberately not a daemon. B-480.

This module closes B-202's core: *flow selection for event-driven runs is a
table lookup, not a model judgement.* It maps one inbound event — an
Alertmanager webhook payload or a raw IOS-XR syslog line — to a typed
:class:`RoutingDecision` naming the flow, device and subject an investigation
would need, or a stated reason none applies.

It listens on nothing, calls nothing, retries nothing
------------------------------------------------------
The receiving process is the operator's infrastructure choice — n8n, a
ten-line systemd service, the platform stack's existing relay container.
T-005 (`discovery-alerting.md`) measured Alertmanager already doing grouping,
deduplication, inhibition and repeat-suppression natively and concluded a
workflow engine is not needed for the trigger; this repo therefore ships the
*decision* and `examples/`, never the listener. And per `OPS-WAVE-PLAN.md`'s
boundary rule: an orchestrator may trigger `nettools` and route its JSON — it
may never touch a device, build a prompt, or hold diagnostic logic. A pure
function is the shape that makes that rule structural.

The device never comes from message text
------------------------------------------
Subjects (a peer address, an interface name) are extracted from the event's
*message* and syntactically validated before use. The **device** is different:
it comes only from the event's origin metadata — an Alertmanager ``device``
label, or the explicit ``--device`` a syslog receiver supplies from its
transport source. Message text is device-authored and, transitively,
attacker-influenceable (B-467: a failed login embeds the attacker's chosen
username in the log). An event that names no device through metadata is
**unroutable**, never guessed.

Unroutable is a success, not an error
---------------------------------------
The table refusing to route is the deterministic behaviour B-202 wanted in
place of a model judgement. Every decision carries ``reason`` either way, so
an orchestrator can log *why* nothing fired without parsing prose.

A recovery is not a fault (B-711)
------------------------------------
Some mnemonics carry a direction — ``neighbor X Down`` vs. ``neighbor X Up``,
``Interface X, changed state to Down`` vs. ``... to Up``. Investigating an
``Up`` spends the window on a condition that is already fine, so
``RoutingDecision.transition`` records what the text said (``"down"``,
``"up"``, or ``"unknown"`` if the mnemonic has a direction concept but this
line's text did not parse one out) and a recovery is routed as
``routable=False`` with a ``reason`` that says so plainly — never dropped
silently, and never treated as equivalent to a fault. ``transition`` is
``None`` only for a mnemonic with no direction concept at all; that is a
different fact from ``"unknown"`` and the two are never conflated.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .inventory_model import load_inventory_file
from .template_parsers import _LOG_ENTRY  # the one syslog line format, defined once

__all__ = [
    "MNEMONIC_FLOW_TABLE",
    "ALERTNAME_FLOW_TABLE",
    "RoutingDecision",
    "route_alertmanager",
    "route_event",
    "route_syslog_line",
]


@dataclass(frozen=True)
class RoutingDecision:
    """One event's routing verdict. ``reason`` is always populated."""

    routable: bool
    flow: str | None = None
    device: str | None = None
    subject: str | None = None
    reason: str = ""
    #: "alertmanager" | "syslog" | "unknown"
    source_kind: str = "unknown"
    #: The mnemonic or alertname that decided this, for the orchestrator's log.
    matched: str | None = None
    #: "up" | "down" | "unknown" | None (B-711). ``None`` means this mnemonic
    #: has no direction concept at all -- it is not in `_TRANSITION_EXTRACTORS`
    #: because nothing in its message text ever encodes a direction. That is
    #: a *different* fact from "unknown", which means a mnemonic that DOES
    #: have a direction concept had text this run could not parse a
    #: direction out of. Collapsing the two into one falsy value would let a
    #: genuine parse failure masquerade as "no concept to worry about" --
    #: exactly the silent-absence shape this project's `unevaluated`/
    #: `Coverage.gaps()` discipline exists to rule out elsewhere.
    transition: str | None = None

    def suggested_command(self) -> list[str] | None:
        """The exact ``nettools`` argv this decision suggests, or ``None``.

        A **list**, never a shell string: an orchestrator that joins these
        itself into a shell line re-opens the quoting-injection surface the
        typed-parameter design exists to close. Every element here is either
        a literal or a value that will be re-validated by ``render_command``
        when the command actually runs — the suggestion carries no authority.
        """

        if not self.routable:
            return None
        return ["nettools", "investigate", str(self.device), str(self.subject),
                "--flow", str(self.flow)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "routable": self.routable, "flow": self.flow, "device": self.device,
            "subject": self.subject, "reason": self.reason,
            "source_kind": self.source_kind, "matched": self.matched,
            "transition": self.transition,
            "suggested_command": self.suggested_command(),
        }


# --------------------------------------------------------------------------- #
# Subject extractors: message text -> a validated subject, or None.
# --------------------------------------------------------------------------- #

#: The direction word is captured (group 2), not just matched, so a
#: transition extractor can read it -- B-711: it used to be matched and
#: thrown away, which is exactly why a recovery and a fault produced the
#: same `RoutingDecision`. Subject extraction below only ever reads group 1;
#: capturing group 2 changes nothing about which text this regex matches or
#: what `_ipv4_subject` returns.
_NEIGHBOR = re.compile(r"neighbor\s+(\S+)\s+(Down|Up)", re.IGNORECASE)
_INTERFACE = re.compile(r"Interface\s+([A-Za-z][A-Za-z0-9_./-]{0,62}),")
_INTERFACE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{0,62}")

#: A *separate* regex for the interface direction, not a second capture
#: group bolted onto `_INTERFACE` above -- `_INTERFACE` only promises to
#: match up to the interface name (verified against both
#: `PKT_INFRA-LINK-3-UPDOWN`'s "Interface X, changed state to Down" and
#: `PKT_INFRA-LINEPROTO-5-UPDOWN`'s "Line protocol on Interface X, changed
#: state to Down" -- both real fixture lines, `tests/fixtures/cisco_xr/PE2/
#: broken/show-logging-last-200.txt`). Keeping subject extraction unchanged
#: is what makes requirement 4 (a Down decision is byte-for-byte what it was)
#: provable instead of merely plausible.
_INTERFACE_TRANSITION = re.compile(
    r"Interface\s+[A-Za-z][A-Za-z0-9_./-]{0,62},\s*changed state to\s+(Down|Up)",
    re.IGNORECASE,
)


def _ipv4_subject(text: str) -> str | None:
    """An IPv4 peer from an adjacency-change message, validated by parsing.

    ``ipaddress.IPv4Address`` — the same canonicalise-by-reconstruction rule
    the templates use. A v6 peer or a malformed token returns ``None``: the
    only implemented flow subjects are IPv4 today, and a subject that does not
    parse is not a subject (never "close enough").
    """

    match = _NEIGHBOR.search(text)
    if not match:
        return None
    try:
        return str(ipaddress.IPv4Address(match.group(1)))
    except ValueError:
        return None


def _interface_subject(text: str) -> str | None:
    """An interface name, matched against the same anchored charset
    ``InterfaceNameParam`` enforces — re-validated again at run time anyway."""

    match = _INTERFACE.search(text)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# Transition extractors (B-711): message text -> "up" | "down" | "unknown".
#
# Never ``None`` -- these are only ever consulted for a mnemonic already
# known (via `_TRANSITION_EXTRACTORS` below) to *have* a direction concept,
# so a failed parse here is "we could not tell", not "there is nothing to
# tell". ``None`` on `RoutingDecision.transition` is reserved for a mnemonic
# with no entry in `_TRANSITION_EXTRACTORS` at all -- see that dataclass
# field's docstring for why the two must never collapse into one value.
# --------------------------------------------------------------------------- #


def _bgp_transition(text: str) -> str:
    match = _NEIGHBOR.search(text)
    return match.group(2).lower() if match else "unknown"


def _interface_transition(text: str) -> str:
    match = _INTERFACE_TRANSITION.search(text)
    return match.group(1).lower() if match else "unknown"


#: mnemonic -> its transition extractor. Deliberately a separate table from
#: `MNEMONIC_FLOW_TABLE` rather than a fourth tuple element on it:
#: `event_watch.py` unpacks that table's entries as exactly
#: ``mnemonic, flow, extract`` in more than one place (`_flow_and_extractor_
#: for`, `validate_trigger_table`), and widening the tuple would break that
#: unpacking for a module this change has no reason to touch. A mnemonic
#: absent from this dict has no direction concept -- `_transition_for`
#: returns ``None`` for it, never "unknown".
_TRANSITION_EXTRACTORS: dict[str, Callable[[str], str]] = {
    "ROUTING-BGP-5-ADJCHANGE": _bgp_transition,
    "PKT_INFRA-LINK-3-UPDOWN": _interface_transition,
    "PKT_INFRA-LINEPROTO-5-UPDOWN": _interface_transition,
}


def _transition_for(mnemonic: str, text: str) -> str | None:
    extractor = _TRANSITION_EXTRACTORS.get(mnemonic)
    return extractor(text) if extractor else None


# --------------------------------------------------------------------------- #
# The tables. Declared, reviewed, ordered — first match wins.
# --------------------------------------------------------------------------- #

#: mnemonic (exact, post-``%``) → (flow, subject extractor). Seeded for the
#: flows that exist today — ``bgp_session`` and ``interface`` — from mnemonics
#: measured in this fabric's own fixtures, not imagined. Everything absent
#: routes to "not in the table", which is the correct answer, loudly.
MNEMONIC_FLOW_TABLE: tuple[tuple[str, str, Callable[[str], str | None]], ...] = (
    ("ROUTING-BGP-5-ADJCHANGE", "bgp_session", _ipv4_subject),
    ("PKT_INFRA-LINK-3-UPDOWN", "interface", _interface_subject),
    ("PKT_INFRA-LINEPROTO-5-UPDOWN", "interface", _interface_subject),
)

#: Alertmanager ``alertname`` label → flow. The subject must arrive as a
#: ``subject`` label on the alert — T-005's finding is that the current rules
#: carry neither device nor subject labels, and adding them is the operator's
#: rule-authoring task (documented in ``examples/README.md``).
ALERTNAME_FLOW_TABLE: tuple[tuple[str, str], ...] = (
    ("BgpSessionDown", "bgp_session"),
    ("InterfaceDown", "interface"),
)


def _known_devices() -> set[str]:
    try:
        return {d.name for d in load_inventory_file().devices}
    except Exception:  # noqa: BLE001 -- no inventory readable: validate nothing as known.
        return set()


def _validated_subject(value: object) -> str | None:
    """A subject validated by reconstruction, or ``None`` (2026-08-18 P0).

    The syslog path validates its extracted subject; the Alertmanager path did
    not, so a `subject` label of `"10.0.0.1; touch /tmp/x #"` flowed straight
    into `suggested_command`. A subject is a valid IPv4 or a valid interface
    name and nothing else -- never a string carrying shell metacharacters.
    Defence in depth: `render_command` re-validates at run time too, but a
    suggestion an orchestrator might shell-join must not carry an unvalidated
    value in the first place.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        pass
    return text if _INTERFACE_NAME.fullmatch(text) else None


def _validated_device(name: str | None, *, source_kind: str) -> tuple[str | None, str | None]:
    """``(device, problem)`` — an unknown device is a stated refusal, not a guess."""

    if not isinstance(name, str) or not name:
        return None, (
            "no usable device in the event's metadata; the device never comes "
            "from message text (see the module docstring), so this event is "
            "unroutable"
        )
    if name not in _known_devices():
        return None, f"device {name!r} is not in the inventory"
    return name, None


# --------------------------------------------------------------------------- #
# Routers.
# --------------------------------------------------------------------------- #


def route_syslog_line(line: str, *, device: str | None = None) -> RoutingDecision:
    """Route one raw IOS-XR syslog line.

    ``device`` is the origin the *receiver* knows from transport metadata
    (source address, syslog hostname field) — required, because the line's own
    text never supplies it.

    The line format is `template_parsers._LOG_ENTRY` — the one place the
    format is defined; a second regex here would drift from the parser the
    descent itself trusts.
    """

    match = _LOG_ENTRY.match(line.strip())
    if not match:
        return RoutingDecision(
            routable=False, source_kind="syslog",
            reason="not an IOS-XR log line this fabric's parser recognises",
        )

    mnemonic = match["mnemonic"]
    text = match["text"]

    entry = next((e for e in MNEMONIC_FLOW_TABLE if e[0] == mnemonic), None)
    if entry is None:
        return RoutingDecision(
            routable=False, source_kind="syslog", matched=mnemonic,
            reason=f"mnemonic {mnemonic!r} is not in MNEMONIC_FLOW_TABLE — "
                   "deliberately unrouted, not unrecognised",
        )
    _, flow, extract = entry
    # Computed here, before device/subject resolution can short-circuit,
    # so every decision returned from this point on -- routable or not --
    # carries it. B-711's "absence is never zero": a human reading a refusal
    # for an unknown device should still be able to see this was a Down (or
    # an Up), not just that it was refused.
    transition = _transition_for(mnemonic, text)

    resolved, problem = _validated_device(device, source_kind="syslog")
    if problem:
        return RoutingDecision(routable=False, source_kind="syslog",
                               matched=mnemonic, flow=flow, reason=problem,
                               transition=transition)

    subject = extract(text)
    if subject is None:
        return RoutingDecision(
            routable=False, source_kind="syslog", matched=mnemonic, flow=flow,
            device=resolved, transition=transition,
            reason="no valid subject could be extracted from the message text "
                   "(a subject that does not parse is not a subject)",
        )

    if transition == "up":
        return RoutingDecision(
            routable=False, flow=flow, device=resolved, subject=subject,
            source_kind="syslog", matched=mnemonic, transition=transition,
            reason=f"{mnemonic} is a recovery (transition=up), not a fault — "
                   "deliberately not routed for investigation (B-711: "
                   "investigating a recovery spends the window on nothing)",
        )
    if transition == "unknown":
        return RoutingDecision(
            routable=False, flow=flow, device=resolved, subject=subject,
            source_kind="syslog", matched=mnemonic, transition=transition,
            reason=f"{mnemonic} has a direction concept but it could not be "
                   "determined from this message text — refusing rather "
                   "than guessing whether this is a fault or a recovery",
        )

    return RoutingDecision(
        routable=True, flow=flow, device=resolved, subject=subject,
        source_kind="syslog", matched=mnemonic, transition=transition,
        reason=f"{mnemonic} routes to the {flow} flow",
    )


def route_alertmanager(payload: dict) -> list[RoutingDecision]:
    """Route an Alertmanager webhook payload — one decision per alert.

    The payload shape is the standard webhook JSON T-005 recorded: ``alerts[]``
    each carrying ``status``/``labels``/``annotations``. A ``resolved`` alert
    is unroutable by design: investigating a fixed thing spends the window on
    nothing and drops a confusing healthy report into the timeline right where
    someone is reading about the fault.
    """

    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return [RoutingDecision(
            routable=False, source_kind="alertmanager",
            reason="no alerts[] in the payload",
        )]

    decisions: list[RoutingDecision] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            decisions.append(RoutingDecision(
                routable=False, source_kind="alertmanager",
                reason="alert entry is not an object"))
            continue
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        alertname = labels.get("alertname")

        if alert.get("status") == "resolved":
            decisions.append(RoutingDecision(
                routable=False, source_kind="alertmanager", matched=alertname,
                reason="resolution event — a resolved alert must not trigger "
                       "an investigation",
            ))
            continue

        flow = next((f for n, f in ALERTNAME_FLOW_TABLE if n == alertname), None)
        if flow is None:
            decisions.append(RoutingDecision(
                routable=False, source_kind="alertmanager", matched=alertname,
                reason=f"alertname {alertname!r} is not in ALERTNAME_FLOW_TABLE",
            ))
            continue

        device, problem = _validated_device(
            labels.get("device") or labels.get("instance"), source_kind="alertmanager"
        )
        if problem:
            decisions.append(RoutingDecision(
                routable=False, source_kind="alertmanager", matched=alertname,
                flow=flow,
                reason=f"{problem} — T-005: the alert rules must carry a "
                       "device label; see examples/README.md",
            ))
            continue

        subject = _validated_subject(labels.get("subject"))
        if subject is None:
            raw = labels.get("subject")
            reason = ("no subject label on the alert — rule-authoring gap, "
                      "same as the device label (T-005)") if not raw else (
                      f"subject label {raw!r} is not a valid IPv4 or interface "
                      "name; refusing to route an unvalidated subject")
            decisions.append(RoutingDecision(
                routable=False, source_kind="alertmanager", matched=alertname,
                flow=flow, device=device, reason=reason,
            ))
            continue

        decisions.append(RoutingDecision(
            routable=True, flow=flow, device=device, subject=subject,
            source_kind="alertmanager", matched=alertname,
            reason=f"{alertname} routes to the {flow} flow",
        ))
    return decisions


def route_event(raw: str, *, device: str | None = None) -> list[RoutingDecision]:
    """Sniff JSON-vs-line and dispatch. The CLI's single entry point."""

    text = raw.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return [RoutingDecision(routable=False, reason="unparseable JSON")]
        if isinstance(payload, dict) and "alerts" in payload:
            return route_alertmanager(payload)
        return [RoutingDecision(
            routable=False, source_kind="unknown",
            reason="JSON without an alerts[] key — not an Alertmanager webhook",
        )]
    if not text:
        return [RoutingDecision(routable=False, reason="empty input")]
    return [route_syslog_line(text, device=device)]
