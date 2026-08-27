"""Pure predicates over parsed evidence, one per rung of a descent.

A check answers one question about one object — "is this BGP session
established", "does this device have a route to that prefix" — and returns a
:class:`CheckResult`. It is the deterministic half of the investigation layer:
**no model is involved in any check, ever.** The descent's central claim is
that finding the lowest broken layer is parse-and-compare, and this module is
where that claim is either true or false.

What a check may touch
----------------------
Parsed records, and nothing else. No I/O, no device access, no inventory
reads, no clock, no environment. A check receives an evidence dict that
somebody else collected and returns a verdict about it. That is what makes
every rung of a descent reproducible from a fixture, and it is why
``tests/test_checks.py`` can cover all three outcomes without a lab.

The rule this module exists to enforce
--------------------------------------
**A check may only answer ``healthy`` about a field it actually read. Absence
is ``unevaluated``.**

Not ``healthy`` — nothing was verified. Not ``broken`` — nothing was observed.
``unevaluated`` is the only honest answer when the evidence needed to decide
was not there, and it is a first-class outcome rather than an error.

This is adopted verbatim from ``health.py``, whose docstring puts the same rule
as "zero IS-IS records because the command failed must never look the same as
zero IS-IS records because the device is actually isolated". It is restated
here because the same failure shape has now bitten this project three times in
three different layers, and each time it looked like success:

* **OBS-006** — the model API returned empty content with
  ``finish_reason: "length"`` and no error. "The model never got to answer"
  was indistinguishable from "the model answered with nothing".
* **OBS-043** — a read timeout returned partial device output with
  ``status: "success"`` and ``errors: []``. "The device said this" was
  indistinguishable from "we stopped listening". Filed as **B-411**.
* **OBS-044** — a line-down interface omits its error counters entirely. A
  check reading ``input_errors``, finding nothing, and concluding "0 errors,
  therefore healthy" would report an interface whose error state is unknowable
  as clean.

The pattern is the same every time: **silent degradation behind a green flag**.
It is the worst failure mode available to this system, because nothing
downstream can detect it — a wrong verdict that announces itself is a bug, and
a wrong verdict that looks right is a liability. So the rule is not a style
preference. A check that cannot see a field says so.

The mechanical form of it: if the intent or template a check depends on did not
reach ``parsers.PARSE_OK``, the check returns ``unevaluated`` before looking at
any record. :func:`require_parsed` does that in one call, and every check
should start with it.

A second, narrower form of the same rule governs a *named* subject: a peer or
interface simply absent from otherwise-parsed evidence is ``unevaluated``, not
``broken``. "The session is down" and "I have no record of that session" are
different answers, and only one of them is a fault -- conflating them would
make every typo'd peer address or not-yet-collected interface look like an
outage.

Evidence key convention (T-020, read before T-022)
---------------------------------------------------
A check receives one ``evidence`` dict that may cover many objects at once.
Two different kinds of section live in it side by side, keyed differently:

* **Intent sections are keyed by intent name** -- ``evidence["bgp"]``,
  ``evidence["isis"]``, ``evidence["interfaces"]`` -- because
  ``collect_evidence`` runs each intent's command(s) exactly once per
  collection.
* **Template results are keyed** ``"<template>:<parameter-value>"`` --
  ``evidence["bgp_neighbor:10.255.0.12"]``, ``evidence["route:10.255.0.31/32"]``,
  ``evidence["interface:Gi0/0/0/0"]`` -- because one descent runs the same
  template repeatedly with different parameters (a different peer, a
  different prefix, a different interface), and every one of those results
  needs its own slot rather than overwriting the last.

Both kinds share one shape, ``{"data": {"parsed": ..., "parse_status": ...},
...}``, which is exactly what :func:`require_parsed` and :func:`parsed_records`
already read -- neither function needs to know or care which kind of key
produced the section it was handed. This module only ever *consumes* the
convention; building the merged dict from ``collect_evidence``/``run_template``
output is T-022's job.

Relationship to ``health.py``
-----------------------------
They coexist deliberately and are not merged. ``health.py``'s rules are
per-*device* and produce severities; checks are per-*object* and produce rung
verdicts. ``tests/test_checks_agree_with_health.py`` (T-021) is the guardrail
that lets both exist: neither may call something ``healthy`` that the other
calls ``broken``. One may be ``unevaluated`` where the other is not — that is
allowed, and is exactly the asymmetry this rule creates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import metrics, parsers
from .inventory_model import Device, load_inventory_file
from .platforms import DEFAULT_PLATFORM
from .status import STATUS_ERROR, STATUS_UNSUPPORTED

__all__ = [
    "BROKEN",
    "HEALTHY",
    "STATUSES",
    "UNEVALUATED",
    "CheckResult",
    "bgp_session_state",
    "bgp_transport",
    "broken",
    "evidence_key",
    "healthy",
    "interface_state",
    "isis_adjacency",
    "isis_neighbor_up",
    "parsed_records",
    "require_parsed",
    "route_present",
    "unevaluated",
]

# The three outcomes. A check returns one of these and nothing else -- there is
# no "unknown", no "degraded", no None. Three states, closed.
HEALTHY = "healthy"
BROKEN = "broken"
UNEVALUATED = "unevaluated"

STATUSES: frozenset[str] = frozenset({HEALTHY, BROKEN, UNEVALUATED})


@dataclass(frozen=True)
class CheckResult:
    """One rung's verdict about one object.

    Frozen because a verdict is a record of what was observed. A caller that
    could mutate one could quietly turn an ``unevaluated`` into a ``healthy``
    several frames away from the evidence, and the grounding check downstream
    would have no way to notice.
    """

    status: str
    reason: str | None = None
    subject: str | None = None
    evidence_keys: tuple[str, ...] = field(default_factory=tuple)
    #: The far end's own words, quoted verbatim and kept structurally apart
    #: from ``reason``'s code-authored prose (B-692, the OBS-691 residual).
    #: ``reason`` still SAYS what a fragment here also holds -- the two
    #: checks that ever quote a device (``bgp_transport`` via
    #: ``_state_note``/``_last_reset_note``, ``route_present``'s fixed
    #: "% Network not in table" reply) build both from the SAME read at the
    #: same call site, never one by regexing the other, so a consumer that
    #: wants the untrusted substring alone (``ticket_read``'s containment)
    #: can wrap exactly it without parsing this project's own sentence.
    #:
    #: ``None`` means this check never quoted the device at all. A non-``None``
    #: value is always a non-empty tuple of non-empty strings -- enforced
    #: below -- so "never asked" and "asked, quote came back empty" cannot
    #: collapse onto the same ``()``/``""`` the module rule above already
    #: refuses to let stand in for absence.
    device_text: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"CheckResult.status must be one of {sorted(STATUSES)}, got {self.status!r}"
            )
        # A verdict that cites nothing cannot be grounded, and D20 requires
        # every claim in a report to cite an evidence key. `unevaluated` is the
        # one exception: there may genuinely have been nothing to read.
        if self.status in (HEALTHY, BROKEN) and not self.evidence_keys:
            raise ValueError(
                f"a {self.status!r} CheckResult must cite at least one evidence key; "
                "only 'unevaluated' may cite none"
            )
        # B-692's own rule, restated as code: absence is `None`, never a zero
        # shaped like `()`. A quote that came back genuinely empty and a check
        # that never asked must never be able to look identical -- so a
        # `device_text` that is present at all must carry at least one real,
        # non-empty fragment.
        if self.device_text is not None:
            if not isinstance(self.device_text, tuple) or not self.device_text:
                raise ValueError(
                    "CheckResult.device_text must be a non-empty tuple of "
                    "device-authored fragments, or None if the check never "
                    "quoted the device -- never () (absence is never zero)"
                )
            if any(not isinstance(f, str) or not f for f in self.device_text):
                raise ValueError("CheckResult.device_text entries must be non-empty strings")

    @property
    def is_conclusive(self) -> bool:
        """Whether this verdict settles the rung. ``unevaluated`` does not."""

        return self.status in (HEALTHY, BROKEN)


def evidence_key(device: str, intent_or_template: str, subject: str | None = None) -> str:
    """Build the citation a report will later have to resolve.

    Keyed device-first, matching the operational-memory convention
    ``glossary.md`` pins (``PE2:GigabitEthernet0/0/0/1``,
    ``PE2:bgp:10.255.0.31``), so a future D14 event store needs no second
    vocabulary for the same idea.
    """

    return f"{device}:{intent_or_template}" + (f":{subject}" if subject else "")


def healthy(
    *,
    subject: str | None,
    evidence_keys: tuple[str, ...],
    reason: str | None = None,
    device_text: tuple[str, ...] | None = None,
):
    return CheckResult(
        HEALTHY, reason=reason, subject=subject, evidence_keys=evidence_keys,
        device_text=device_text,
    )


def broken(
    *,
    reason: str,
    subject: str | None,
    evidence_keys: tuple[str, ...],
    device_text: tuple[str, ...] | None = None,
):
    return CheckResult(
        BROKEN, reason=reason, subject=subject, evidence_keys=evidence_keys,
        device_text=device_text,
    )


def unevaluated(
    *,
    reason: str,
    subject: str | None = None,
    evidence_keys: tuple[str, ...] = (),
    device_text: tuple[str, ...] | None = None,
):
    """The honest answer when the evidence needed to decide was not there.

    Always carries a reason. "Unevaluated" with no explanation is only
    marginally better than a wrong answer, because the operator still cannot
    tell whether to re-collect, fix a parser, or look elsewhere.
    """

    return CheckResult(
        UNEVALUATED, reason=reason, subject=subject, evidence_keys=evidence_keys,
        device_text=device_text,
    )


def parsed_records(section: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Records from an evidence section, or ``[]`` if there are none.

    Deliberately *not* a substitute for :func:`require_parsed`. This returns
    ``[]`` for both "parsed fine, no records" and "never parsed", so calling it
    without checking the parse status first is precisely how absence gets read
    as zero.
    """

    if not isinstance(section, dict):
        return []
    parsed = section.get("data", {}).get("parsed")
    if not isinstance(parsed, dict):
        return []
    records = parsed.get("records")
    return records if isinstance(records, list) else []


def require_parsed(
    evidence: dict[str, Any],
    key: str,
    *,
    subject: str | None = None,
) -> tuple[dict[str, Any] | None, CheckResult | None]:
    """Gate every check on its evidence actually having parsed.

    Returns ``(section, None)`` when the section reached ``PARSE_OK``, or
    ``(None, CheckResult)`` carrying an ``unevaluated`` verdict when it did
    not. The intended shape at the top of every check is::

        section, bail = require_parsed(evidence, "bgp", subject=peer)
        if bail is not None:
            return bail

    Three distinct failures all land here, and each gets its own reason so the
    operator knows which one happened: the section is missing entirely, the
    platform does not support it (``PARSE_UNAVAILABLE``), or the parser could
    not read what came back (``PARSE_FAILED``). Collapsing them into one
    message would throw away the only clue about what to do next.
    """

    section = evidence.get(key)
    if not isinstance(section, dict):
        return None, unevaluated(
            reason=f"no {key!r} section in the evidence", subject=subject
        )

    parse_status = section.get("data", {}).get("parse_status")
    if parse_status == parsers.PARSE_OK:
        return section, None

    if parse_status == parsers.PARSE_UNAVAILABLE:
        detail = f"{key!r} is not available on this platform"
    elif parse_status == parsers.PARSE_FAILED:
        detail = f"{key!r} did not parse"
    else:
        detail = f"{key!r} has no parse status ({parse_status!r})"

    return None, unevaluated(reason=detail, subject=subject)


def _is_numeric(value: Any) -> bool:
    """True for a plain received-prefix count; false for a BGP state word.

    IOS-XR reuses the ``St/PfxRcd`` summary column for a state word (``Idle``,
    ``Active``, ``Connect``, ...) whenever a session is not Established; a
    plain number in that column *is* the received-prefix count and means the
    session is up. Deliberately re-declared rather than imported from
    ``health.py`` -- the two rule sets coexist on purpose (see the module
    docstring) and neither should have to import the other to share one
    three-line predicate.
    """

    return isinstance(value, str) and value.strip().isdigit()


# --------------------------------------------------------------------------- #
# bgp_session_state -- the BGP summary's own view of a peer (T-020)
# --------------------------------------------------------------------------- #


def bgp_session_state(evidence: dict[str, Any], peer: str) -> CheckResult:
    """Is ``peer`` Established, per ``show bgp summary``'s ``St/PfxRcd`` column?

    ``broken`` when the column holds a state word instead of a number
    (``Idle``/``Active``/``Connect``/...). A peer not present in an
    otherwise-parsed ``bgp`` section is ``unevaluated`` -- it may simply not be
    configured on this device, which is not the same fact as "configured and
    down".
    """

    section, bail = require_parsed(evidence, "bgp", subject=peer)
    if bail is not None:
        return bail

    device = str(evidence.get("device", "unknown"))
    key = evidence_key(device, "bgp", peer)

    for record in parsed_records(section):
        if record.get("neighbor") != peer:
            continue
        # `session_state` and `prefixes_received` rather than one field whose
        # type depends on its value (B-460). The `_is_numeric` re-derivation
        # this replaced was the discriminator the CLI discards, reconstructed
        # here and in two other places.
        state = record.get("session_state", "")
        prefixes = record.get("prefixes_received")
        if state == "Established":
            return healthy(
                subject=peer,
                evidence_keys=(key,),
                reason=f"BGP session to {peer} is Established ({prefixes} prefixes received)",
            )
        return broken(
            reason=f"BGP session to {peer} is not Established (state: {state})",
            subject=peer,
            evidence_keys=(key,),
        )

    return unevaluated(reason=f"no record of BGP peer {peer} in 'bgp'", subject=peer)


# --------------------------------------------------------------------------- #
# bgp_transport -- one peer's own TCP/BGP session state (T-020)
# --------------------------------------------------------------------------- #


def bgp_transport(evidence: dict[str, Any], peer: str) -> CheckResult:
    """Is ``peer``'s own ``show bgp neighbor <peer>`` connection Established?

    Reads ``bgp_neighbor:<peer>``, not ``bgp`` -- this is the peer's own
    detailed view, independent confirmation of :func:`bgp_session_state`'s
    summary-table answer. ``meta.found`` distinguishes a real down session
    (``connection_state`` present, not ``Established``) from a peer this
    device has no process/configuration for at all
    (``bgp_not_active``/``neighbor_not_found``, ``found`` is False) -- the
    latter is ``unevaluated``, not ``broken``, per the module rule.
    """

    key_name = f"bgp_neighbor:{peer}"
    section, bail = require_parsed(evidence, key_name, subject=peer)
    if bail is not None:
        return bail

    device = str(evidence.get("device", "unknown"))
    key = evidence_key(device, key_name)
    meta = (section.get("data", {}).get("parsed") or {}).get("meta") or {}

    if not meta.get("found", False):
        return unevaluated(
            reason=f"no record of BGP peer {peer} ({meta.get('reason') or 'not found'})",
            subject=peer,
        )

    # B-432. The socket first, `connection_state` only as a fallback.
    #
    # `connection_state` is the BGP finite state machine, which is what rung 1
    # already reads from `show bgp summary` -- two commands reporting the same
    # thing, so rung 2 could never disagree with rung 1 and `cause_not_localised`
    # was unreachable in practice (OBS-092). The socket's arming is a different
    # subsystem: whether the stack is polling a socket for this peer at all.
    #
    # Measured across every committed fixture: armed on 14 of 14 Established
    # sessions, not armed on 2 of 2 Idle ones. That the two agree on this corpus
    # is not evidence they are the same field -- the corpus contains no fault
    # that separates them, which is precisely the gap this change opens.
    # ROUND 8b, 2026-08-18 -- the gap the comment above predicted, measured.
    #
    # 131 samples separated the two fields. **127 of them were in `Connect`**,
    # which RFC 4271 defines as *waiting for the TCP connection to be
    # completed* -- TCP is NOT established there -- and the socket line in
    # those samples is byte-identical to a healthy session's
    # (`Socket not armed for io, armed for read, armed for write`).
    #
    # So `socket_armed_read` does not mean *the transport is established*. It
    # means *the BGP stack has a socket armed for read events*, which it does
    # while a connection is still being attempted. Read unqualified, this rung
    # reported "TCP transport is up" during connect-retry while TCP was down --
    # and a filtered TCP 179 cycles Idle -> Connect -> Idle, so a genuine
    # transport fault would be cleared here and blamed on the rung above. That
    # is the one failure a dependency descent exists to prevent (B-497).
    #
    # The other 4 separations were in `OpenSent`, where the OPEN has been sent
    # over an established TCP session: armed-and-not-Established is then a true
    # separation and this rung is right. The fix is therefore to qualify the
    # socket by the state, not to abandon it.
    #
    # Only these states guarantee an established TCP session underneath.
    _TCP_UP_STATES = frozenset({"OpenSent", "OpenConfirm", "Established"})

    armed = meta.get("socket_armed_read")
    fsm = meta.get("connection_state") or meta.get("state")
    if armed is not None:
        # Read once, used two ways below: `*_suffix` is folded into `reason`
        # exactly as before B-692, `*_fragment` is the same device string
        # handed to `device_text` unparsed -- see `_state_note`'s docstring.
        state_suffix, state_fragment = _state_note(meta)
        if armed and fsm in _TCP_UP_STATES:
            return healthy(
                subject=peer,
                evidence_keys=(key,),
                reason=(
                    f"TCP transport to {peer} is up (the socket is armed for read"
                    f", session state {fsm})"
                    + state_suffix
                ),
                device_text=(state_fragment,) if state_fragment else None,
            )
        if armed:
            # Armed, but the FSM is not in a state that implies an established
            # TCP session -- `Connect`/`Active` are mid-attempt, `Idle` is not
            # trying. The evidence is genuinely ambiguous: it cannot tell a
            # transport fault from a BGP-layer fault, so it says so and the
            # walk stops rather than clearing a layer it did not verify.
            reset_suffix, reset_fragment = _last_reset_note(meta)
            return unevaluated(
                reason=(
                    f"cannot judge TCP transport to {peer}: the socket is armed for "
                    f"read but the session is in {fsm or 'an unreported state'}, "
                    "which does not imply an established TCP connection (B-497)"
                    + state_suffix + reset_suffix
                ),
                subject=peer,
                evidence_keys=(key,),
                device_text=tuple(f for f in (state_fragment, reset_fragment) if f) or None,
            )
        reset_suffix, reset_fragment = _last_reset_note(meta)
        return broken(
            reason=(
                f"no TCP transport to {peer} (the socket is not armed for read)"
                + state_suffix + reset_suffix
            ),
            subject=peer,
            evidence_keys=(key,),
            device_text=tuple(f for f in (state_fragment, reset_fragment) if f) or None,
        )

    # No socket line: an older XR release, or output shaped differently. Fall
    # back to the FSM rather than returning `unevaluated` -- a weaker signal is
    # still a signal, and the reason says which one was used so a reader is not
    # misled about what was observed.
    connection_state = meta.get("connection_state")
    if connection_state == "Established":
        return healthy(
            subject=peer,
            evidence_keys=(key,),
            reason=(
                f"BGP transport session to {peer} is Established "
                f"(no socket state reported; read from the session state)"
            ),
        )
    reset_suffix, reset_fragment = _last_reset_note(meta)
    return broken(
        reason=(
            f"BGP transport session to {peer} is not Established "
            f"(connection_state: {connection_state}; no socket state reported)"
            + reset_suffix
        ),
        subject=peer,
        evidence_keys=(key,),
        device_text=(reset_fragment,) if reset_fragment else None,
    )


def _state_note(meta: dict[str, Any]) -> tuple[str, str | None]:
    """The device's parenthesised reason for the current BGP state, if any --
    returned as a ``(reason-suffix, fragment)`` pair, B-692.

    The suffix is the exact text `bgp_transport` has always appended to its
    `reason` sentence; the fragment is the SAME read of
    ``meta["state_reason"]``, handed back separately rather than recovered by
    parsing the suffix, so a caller can populate `CheckResult.device_text`
    from the identical source the sentence itself came from -- one read, two
    outputs, never one derived from the other.

    `BGP state = Idle (No route to multi-hop neighbor)` -- the device naming the
    layer beneath it. Distinct from `_last_reset_note`, which is history: this
    is *current*, so it can be stated without a staleness caveat.
    """

    reason = meta.get("state_reason")
    if not reason:
        return "", None
    return f"; the device reports the session state as {reason!r}", reason


def _last_reset_note(meta: dict[str, Any]) -> tuple[str, str | None]:
    """The device's own account of why the session last went down (B-430) --
    returned as a ``(reason-suffix, fragment)`` pair; see `_state_note` for
    why the pair shape exists (B-692).

    Round 3 is why this exists. The fault was a BGP neighbour administratively
    shut on the far end; the check read ``connection_state`` and reported
    ``transport_blocked``, which is true. **The same parsed record carried**

        last_reset_reason: BGP Notification received: administrative shutdown

    The far end had said why, the parser captured it, and the check read the
    field beside it. The diagnostician logged into the far device to learn what
    the local device had already reported (OBS-092, silent-failure shape 7).

    **It qualifies the finding; it never becomes one.** ``last_reset_reason`` is
    *history* and it is present on healthy sessions too -- measured across this
    corpus, seven Established sessions carry ``'Peer closing down the session'``
    and seven carry ``'Address family activated'``. A reset reason from an hour
    ago says nothing certain about a session that is down now, so promoting it
    to a verdict would trade a shape-7 under-report for a confident wrong
    answer, which is the worse trade.

    So: appended to the reason, marked as history, and paired with
    ``last_reset_ago`` where the device gives it, so the reader can judge
    staleness rather than being asked to trust it.
    """

    reason = meta.get("last_reset_reason")
    if not reason:
        return "", None
    ago = meta.get("last_reset_ago")
    when = f" {ago} ago" if ago else ""
    return (
        f"; the device last recorded a reset{when} with reason {reason!r} "
        f"(history, not current state)",
        reason,
    )


# --------------------------------------------------------------------------- #
# route_present -- does this device have a route to a prefix at all (T-020)
# --------------------------------------------------------------------------- #


#: The device's own reply when a prefix has no installed route --
#: `template_parsers._NOT_IN_TABLE`'s exact matched line, re-declared here
#: (not imported) for the same reason `_is_numeric` is re-declared rather
#: than shared: this module reads parsed evidence only, and importing a
#: parser-internal pattern would blur that line. One constant, used both in
#: `reason`'s prose below and in `device_text`, so the two can never drift
#: apart -- B-692.
_ROUTE_NOT_IN_TABLE = "% Network not in table"


def route_present(evidence: dict[str, Any], prefix: str) -> CheckResult:
    """Does ``show route <prefix>`` find an installed route?

    Reads ``route:<prefix>``. Unlike :func:`bgp_transport`, ``meta.found`` has
    only one false shape here (``% Network not in table``, see
    ``template_parsers.parse_xr_route``'s module comment) -- it is the device
    unambiguously answering "no", a real fact rather than an absence of
    evidence, so it is ``broken``, not ``unevaluated``.
    """

    key_name = f"route:{prefix}"
    section, bail = require_parsed(evidence, key_name, subject=prefix)
    if bail is not None:
        return bail

    device = str(evidence.get("device", "unknown"))
    key = evidence_key(device, key_name)
    meta = (section.get("data", {}).get("parsed") or {}).get("meta") or {}

    if meta.get("found", False):
        return healthy(
            subject=prefix,
            evidence_keys=(key,),
            reason=f"route to {prefix} is present ({meta.get('path_count', '0')} path(s))",
        )
    return broken(
        reason=f"no route to {prefix} (device reports {_ROUTE_NOT_IN_TABLE!r})",
        subject=prefix,
        evidence_keys=(key,),
        device_text=(_ROUTE_NOT_IN_TABLE,),
    )


# --------------------------------------------------------------------------- #
# isis_adjacency -- IGP reachability, fabric-wide or to one interface (T-020)
# --------------------------------------------------------------------------- #


def isis_adjacency(evidence: dict[str, Any], interface: str | None = None) -> CheckResult:
    """Does this device have IS-IS adjacencies, or one on ``interface`` in state Up?

    With no ``interface``, ``broken`` on zero adjacencies (a router isolated at
    the IGP layer) or on any adjacency stuck off ``Up``. With ``interface``
    given, only that one adjacency is judged; an interface with no adjacency
    record at all in an otherwise-parsed ``isis`` section is ``unevaluated``,
    not ``broken`` -- it may simply not be an IS-IS-enabled link.
    """

    section, bail = require_parsed(evidence, "isis", subject=interface)
    if bail is not None:
        return bail

    device = str(evidence.get("device", "unknown"))
    key = evidence_key(device, "isis", interface)
    records = parsed_records(section)

    if interface is None:
        if not records:
            return broken(
                reason="no IS-IS adjacencies; device is isolated at the IGP layer",
                subject=None,
                evidence_keys=(key,),
            )
        not_up = [record for record in records if record.get("state") != "Up"]
        if not_up:
            detail = ", ".join(
                f"{record.get('system_id')} ({record.get('state')})" for record in not_up
            )
            return broken(
                reason=f"IS-IS adjacency not Up: {detail}",
                subject=None,
                evidence_keys=(key,),
            )
        return healthy(
            subject=None,
            evidence_keys=(key,),
            reason=f"{len(records)} IS-IS adjacency(ies), all Up",
        )

    for record in records:
        if record.get("interface") != interface:
            continue
        if record.get("state") == "Up":
            return healthy(
                subject=interface,
                evidence_keys=(key,),
                reason=f"IS-IS adjacency on {interface} to {record.get('system_id')} is Up",
            )
        return broken(
            reason=f"IS-IS adjacency on {interface} is {record.get('state')}, not Up",
            subject=interface,
            evidence_keys=(key,),
        )

    return unevaluated(
        reason=f"no IS-IS adjacency record for interface {interface!r}", subject=interface
    )


# --------------------------------------------------------------------------- #
# isis_neighbor_up -- the isis_adjacency flow's top rung (B-107)
# --------------------------------------------------------------------------- #


def isis_neighbor_up(evidence: dict[str, Any], interface: str) -> CheckResult:
    """Is there an Up IS-IS adjacency on ``interface``?

    Deliberately **not** :func:`isis_adjacency`'s ``interface=`` branch, though
    it reads the same ``isis`` section the same way when a record for
    ``interface`` exists. The two diverge on *absence*, and the divergence is
    the point.

    ``isis_adjacency`` is an intermediate rung embedded in another flow's
    ladder (``bgp_session``'s ``igp_adjacency``), where the interface it is
    asked about is derived from a route and may genuinely not be an IS-IS link
    at all -- absence there is honestly ``unevaluated``. This function is the
    *top* rung of a flow whose entire premise is "investigate the IS-IS
    adjacency on this interface", so absence here gets corroborated against
    two other, independent sources before it is allowed to stay ambiguous --
    reading it as ``unevaluated`` regardless would report "cannot determine"
    about cases this build can, in fact, determine, and both are measured
    against real fixtures rather than assumed:

    1. **LLDP**, a distinct subsystem read from the same device (the same
       reasoning :func:`bgp_transport` uses the TCP socket for, B-432).
       Present in ``lldp`` on this interface and absent from ``isis`` is a
       positive fact -- a real, physically-connected neighbor with no
       adjacency formed -- exactly as :func:`route_present`'s ``% Network not
       in table`` is a definite negative rather than silence. Measured on the
       ``isis-broken`` fixture (B-496): PE3's ``Gi0/0/0/0`` has no IS-IS
       record while P2 is plainly cabled there, from both ends.
    2. **The interface's own admin/line state**, checked only once LLDP has
       *also* stayed silent. A shut interface explains silence in both other
       protocols at once -- neither can be heard over a link not passing
       traffic -- and it is a *stronger* corroboration than LLDP's, since an
       admin-down port cannot run either. Measured on the ``broken`` fixture:
       PE2's ``Gi0/0/0/0`` is admin-down, and both ``isis`` and ``lldp`` are
       empty tables. Without this, the walk stopped ``unevaluated`` one rung
       above a fault the very next rung would have found -- caught by running
       this check against that fixture, not anticipated in the design.

    Absent from all three stays ``unevaluated``: the interface is up, has no
    LLDP neighbor and no IS-IS record, which may simply mean it is not an
    IS-IS-enabled link at all -- the case the module rule protects.
    """

    section, bail = require_parsed(evidence, "isis", subject=interface)
    if bail is not None:
        return bail

    from .interface_kind import same_interface

    device = str(evidence.get("device", "unknown"))
    isis_key = evidence_key(device, "isis", interface)

    for record in parsed_records(section):
        if not same_interface(record.get("interface", ""), interface):
            continue
        if record.get("state") == "Up":
            return healthy(
                subject=interface,
                evidence_keys=(isis_key,),
                reason=f"IS-IS adjacency on {interface} to {record.get('system_id')} is Up",
            )
        return broken(
            reason=f"IS-IS adjacency on {interface} is {record.get('state')}, not Up",
            subject=interface,
            evidence_keys=(isis_key,),
        )

    # No IS-IS record for this interface. Corroborate against LLDP before
    # deciding what that silence means -- see the docstring above.
    lldp_section, lldp_bail = require_parsed(evidence, "lldp", subject=interface)
    if lldp_bail is not None:
        return unevaluated(
            reason=(
                f"no IS-IS adjacency record for interface {interface!r}, and LLDP "
                f"could not be read to confirm whether a neighbor is cabled there "
                f"({lldp_bail.reason})"
            ),
            subject=interface,
        )

    # LLDP prints the long interface form (`GigabitEthernet0/0/0/0`); `isis`
    # and `interfaces` print the short one (`Gi0/0/0/0`) -- both read from
    # this one device, which is exactly the within-device case
    # `interface_kind.same_interface` exists for (OBS-117).
    for record in parsed_records(lldp_section):
        if not same_interface(record.get("local_interface", ""), interface):
            continue
        lldp_key = evidence_key(device, "lldp", interface)
        return broken(
            reason=(
                f"no IS-IS adjacency on {interface}, despite LLDP showing "
                f"{record.get('neighbor')} cabled there on "
                f"{record.get('neighbor_interface')} -- a real, physically-connected "
                f"neighbor with no IS-IS adjacency formed"
            ),
            subject=interface,
            evidence_keys=(isis_key, lldp_key),
        )

    # Neither isis nor lldp says anything about this interface. Before calling
    # that ambiguous, check whether the interface itself explains the silence
    # -- see point 2 above. Bulk `interfaces`, not the `interface:<name>`
    # template: it is always collected, and rung 2 (below this one) reads the
    # same section for the same reason.
    interfaces_section, interfaces_bail = require_parsed(
        evidence, "interfaces", subject=interface
    )
    if interfaces_bail is not None:
        # Could not corroborate either way -- not "reads up", which would claim
        # a fact never observed. See the module rule this whole function exists
        # to keep: a check may only answer about a field it actually read.
        return unevaluated(
            reason=(
                f"no IS-IS adjacency record for interface {interface!r}, no LLDP "
                f"neighbor there, and the interface's own state could not be read "
                f"either ({interfaces_bail.reason})"
            ),
            subject=interface,
        )

    for record in parsed_records(interfaces_section):
        if not same_interface(record.get("interface", ""), interface):
            continue
        admin_state = record.get("admin_state")
        line_state = record.get("line_protocol")
        if admin_state != "up" or line_state != "up":
            intf_key = evidence_key(device, "interfaces", interface)
            return broken(
                reason=(
                    f"no IS-IS adjacency on {interface}, and no LLDP neighbor "
                    f"either -- the interface itself is not up "
                    f"(admin_state={admin_state!r}, line_state={line_state!r}), "
                    f"which explains both"
                ),
                subject=interface,
                evidence_keys=(isis_key, intf_key),
            )
        break

    return unevaluated(
        reason=(
            f"no IS-IS adjacency record for interface {interface!r}, no LLDP "
            f"neighbor there, and the interface itself reads up -- it may simply "
            f"not be an IS-IS-enabled link"
        ),
        subject=interface,
    )


# --------------------------------------------------------------------------- #
# ldp_session_up -- the ldp_session flow's top rung (B-109)
# --------------------------------------------------------------------------- #


def ldp_session_up(evidence: dict[str, Any], interface: str) -> CheckResult:
    """Is there an Oper LDP session discovered via ``interface``?

    Same shape as :func:`isis_neighbor_up`, with one deliberate difference in
    *why* the corroboration tier exists. LLDP does not gate IS-IS adjacency
    formation (OBS-167) -- it is a merely-correlated, independent protocol on
    the same wire, so it stays evidence read *inside* that check rather than
    becoming its own rung. LDP discovery (Hello) is not merely correlated with
    an LDP session: by the protocol's own state machine, a session cannot
    reach ``Oper`` without first completing discovery, exactly the relationship
    ``bgp_transport``'s TCP socket has to a BGP session. So the ``ldp``
    section's absence of a peer record is corroborated here against
    ``ldp_discovery`` (a real, gating precondition, read only to interpret that
    absence) before falling back to the interface's own state -- and both are
    named in this rung's ``collect`` alongside ``ldp``, per the epoch coherence
    rule (OBS-167): a check may only read what its own rung declared.

    1. **A session record naming this interface, not ``Oper``** is a direct,
       definite ``broken`` -- the device is telling us the FSM state.
    2. **No session record, but a discovery source for this interface exists.**
       Two measured shapes, both real and both ``broken``:
       - a peer was discovered (``peer_id`` present) with no session record --
         Hello completed, session still not established;
       - Hello is configured and sending but no peer has replied (``peer_id``
         is ``None``) -- measured live on P2's ``Gi0/0/0/4`` toward PE3
         (direction ``xmit`` only, no ``LDP Id:`` line at all), the same
         physical link whose IS-IS adjacency is also absent (OBS-159/B-496) --
         two independent protocols failing on the same link is evidence of a
         shared root cause below what this tool reads, not evidence that one
         causes the other.
    3. **Neither says anything about this interface.** Corroborate against the
       interface's own admin/line state, same reasoning as
       :func:`isis_neighbor_up`'s third tier: a down interface explains
       silence in both LDP subsystems at once.

    Absent from all three stays ``unevaluated``: the interface is up with no
    LDP discovery source and no session record, which may simply mean LDP is
    not enabled on this link.
    """

    section, bail = require_parsed(evidence, "ldp", subject=interface)
    if bail is not None:
        return bail

    from .interface_kind import same_interface

    device = str(evidence.get("device", "unknown"))
    ldp_key = evidence_key(device, "ldp", interface)

    for record in parsed_records(section):
        discovery_interfaces = record.get("discovery_interfaces") or []
        if not any(same_interface(name, interface) for name in discovery_interfaces):
            continue
        peer_id = record.get("peer_id")
        state = record.get("state")
        if state == "Oper":
            return healthy(
                subject=interface,
                evidence_keys=(ldp_key,),
                reason=f"LDP session with peer {peer_id} on {interface} is Oper",
            )
        return broken(
            reason=f"LDP session with peer {peer_id} on {interface} is {state}, not Oper",
            subject=interface,
            evidence_keys=(ldp_key,),
        )

    # No LDP session record references this interface. Corroborate against LDP
    # discovery (Hello) before deciding what that silence means -- see the
    # docstring above on why this is a real dependency, not mere correlation.
    discovery_section, discovery_bail = require_parsed(
        evidence, "ldp_discovery", subject=interface
    )
    if discovery_bail is not None:
        return unevaluated(
            reason=(
                f"no LDP session record for interface {interface!r}, and LDP discovery "
                f"could not be read to confirm whether Hello has formed there "
                f"({discovery_bail.reason})"
            ),
            subject=interface,
        )

    for record in parsed_records(discovery_section):
        if not same_interface(record.get("interface", ""), interface):
            continue
        discovery_key = evidence_key(device, "ldp_discovery", interface)
        peer_id = record.get("peer_id")
        if peer_id:
            return broken(
                reason=(
                    f"LDP Hello established with peer {peer_id} on {interface}, but no "
                    f"LDP session record for it -- session not Oper despite a completed "
                    f"discovery"
                ),
                subject=interface,
                evidence_keys=(ldp_key, discovery_key),
            )
        return broken(
            reason=(
                f"LDP Hello is configured on {interface} but no peer has been discovered "
                f"there (sending only, not receiving a reply) -- no LDP session can form"
            ),
            subject=interface,
            evidence_keys=(ldp_key, discovery_key),
        )

    # Neither ldp nor ldp_discovery says anything about this interface. Before
    # calling that ambiguous, check whether the interface itself explains the
    # silence -- same reasoning as isis_neighbor_up's third tier.
    interfaces_section, interfaces_bail = require_parsed(
        evidence, "interfaces", subject=interface
    )
    if interfaces_bail is not None:
        return unevaluated(
            reason=(
                f"no LDP session record for interface {interface!r}, no LDP discovery "
                f"source there, and the interface's own state could not be read either "
                f"({interfaces_bail.reason})"
            ),
            subject=interface,
        )

    for record in parsed_records(interfaces_section):
        if not same_interface(record.get("interface", ""), interface):
            continue
        admin_state = record.get("admin_state")
        line_state = record.get("line_protocol")
        if admin_state != "up" or line_state != "up":
            intf_key = evidence_key(device, "interfaces", interface)
            return broken(
                reason=(
                    f"no LDP session or discovery on {interface} -- the interface itself "
                    f"is not up (admin_state={admin_state!r}, line_state={line_state!r}), "
                    f"which explains both"
                ),
                subject=interface,
                evidence_keys=(ldp_key, intf_key),
            )
        break

    config_section, config_bail = require_parsed(evidence, "config_ldp", subject=interface)
    if config_bail is not None:
        return unevaluated(
            reason=(
                f"no LDP session record or discovery source on {interface}, and the "
                f"bounded LDP configuration could not be read to establish whether "
                f"LDP is enabled there ({config_bail.reason})"
            ),
            subject=interface,
        )

    config_key = evidence_key(device, "config_ldp")
    if any(
        same_interface(record.get("interface", ""), interface)
        for record in parsed_records(config_section)
    ):
        discovery_key = evidence_key(device, "ldp_discovery", interface)
        interfaces_key = evidence_key(device, "interfaces", interface)
        return broken(
            reason=(
                f"LDP is configured on {interface}, but it has no LDP discovery "
                f"source or session record while the interface is up"
            ),
            subject=interface,
            evidence_keys=(ldp_key, discovery_key, interfaces_key, config_key),
        )

    intent_section, intent_bail = require_parsed(evidence, "ldp_intent", subject=interface)
    if intent_bail is not None:
        return unevaluated(
            reason=(
                f"no LDP session record or discovery source on {interface}, and the "
                f"declared LDP intent could not be read ({intent_bail.reason})"
            ),
            subject=interface,
        )

    intent_key = evidence_key(device, "ldp_intent")
    if any(
        same_interface(record.get("interface", ""), interface)
        for record in parsed_records(intent_section)
    ):
        discovery_key = evidence_key(device, "ldp_discovery", interface)
        interfaces_key = evidence_key(device, "interfaces", interface)
        return broken(
            reason=(
                f"inventory declares LDP is required on {interface}, but the current "
                f"configuration, discovery, and session state are all absent"
            ),
            subject=interface,
            evidence_keys=(ldp_key, discovery_key, interfaces_key, config_key, intent_key),
        )

    return unevaluated(
        reason=(
            f"no LDP session record for interface {interface!r}, no LDP discovery source "
            f"there, and the interface itself reads up -- LDP is not configured on "
            f"this link"
        ),
        subject=interface,
    )


# --------------------------------------------------------------------------- #
# interface_state -- line state, and an error-counter *rate* across two
# observations (T-020, Q-005)
# --------------------------------------------------------------------------- #

# The eight counters Q-005 ruled worth a rate comparison: all of them count
# faults. Deliberately excludes packets_input/bytes_input/packets_output/
# bytes_output (and the non-error runts/giants/throttles/parity/ignored/
# applique/resets counters) -- those grow on any live link and a rising delta
# in them means nothing.
ERROR_COUNTER_NAMES: tuple[str, ...] = (
    "input_errors",
    "crc",
    "frame",
    "overrun",
    "abort",
    "output_errors",
    "underruns",
    "carrier_transitions",
)

# Q-005 also considered a single-observation *absolute* threshold (e.g. "500
# CRC errors is suspicious even with nothing to diff against"). Its ruling is
# explicit that such a threshold may only ever flag a warning-equivalent --
# never make this check `broken`, and never stop a descent -- and this module
# has exactly three statuses, none of which is "warning". Rather than smuggle
# a fourth meaning into `broken` or `healthy`, the simplest compliant choice
# is taken: no absolute threshold is implemented here at all.


def _extract_error_counters(section: dict[str, Any]) -> dict[str, int] | None:
    """Pull the eight error counters out of an ``interface:<name>`` section.

    Returns ``None`` -- not an empty dict -- when none of them are present,
    which is the OBS-044 shape: a line-down interface (see
    ``template_parsers.parse_xr_interface``'s module comment, shape 3) omits
    the error-counter line entirely rather than reporting zero. ``None`` is
    what lets :func:`_counter_delta_status` tell "counters read as zero" apart
    from "counters were never emitted".
    """

    counters: dict[str, int] = {}
    for record in parsed_records(section):
        name = record.get("counter")
        if name not in ERROR_COUNTER_NAMES:
            continue
        try:
            counters[name] = int(record.get("value"))
        except (TypeError, ValueError):
            continue
    return counters or None


def _read_interface_observation(
    evidence: dict[str, Any], name: str
) -> tuple[
    tuple[str | None, str | None, dict[str, int] | None] | None,
    tuple[str, ...],
    CheckResult | None,
]:
    """Resolve one interface's admin/line state, and its error counters if any.

    Tries the per-interface template result (``interface:<name>``) first --
    the only source carrying error counters -- and falls back to the bulk
    ``interfaces`` intent (``show interfaces brief``) for an interface with no
    dedicated capture. The bulk table never carries counters, so a fallback
    observation's counter half is always ``None`` (absent), which is exactly
    the shape :func:`_counter_delta_status` must call unevaluated, never 0.

    Returns ``(observation, evidence_keys, None)`` on success, or
    ``(None, (), CheckResult)`` carrying an ``unevaluated`` verdict when
    neither source has a usable record for this interface.
    """

    device = str(evidence.get("device", "unknown"))

    template_key_name = f"interface:{name}"
    template_section, template_bail = require_parsed(evidence, template_key_name, subject=name)
    if template_section is not None:
        meta = (template_section.get("data", {}).get("parsed") or {}).get("meta") or {}
        admin_state = meta.get("admin_state")
        line_state = meta.get("line_state")
        if admin_state is not None and line_state is not None:
            counters = _extract_error_counters(template_section)
            key = evidence_key(device, template_key_name)
            return (admin_state, line_state, counters), (key,), None

    from .interface_kind import same_interface

    bulk_section, bulk_bail = require_parsed(evidence, "interfaces", subject=name)
    if bulk_section is not None:
        for record in parsed_records(bulk_section):
            # Matched through interface_kind.canonical (OBS-117's fix,
            # reused rather than a second normaliser -- see
            # interface_exists() above) so a long-form subject (`--flow
            # interface GigabitEthernet0/0/0/2.300`) still finds the
            # short-form record `show interfaces brief` stores
            # (`Gi0/0/0/2.300`), same as `interface_exists` already does for
            # the subject-presence check. Exact `==` here previously fell
            # through to "no record of interface ... in 'interfaces'"
            # (unevaluated) even though the interface plainly exists.
            if same_interface(record.get("interface") or "", name):
                key = evidence_key(device, "interfaces", name)
                return (record.get("admin_state"), record.get("line_protocol"), None), (key,), None
        return None, (), unevaluated(
            reason=f"no record of interface {name!r} in 'interfaces'", subject=name
        )

    reasons = [b.reason for b in (template_bail, bulk_bail) if b is not None and b.reason]
    detail = "; ".join(reasons) if reasons else f"no evidence for interface {name!r}"
    return None, (), unevaluated(reason=detail, subject=name)


def _counter_delta_status(
    current: dict[str, int] | None,
    previous: dict[str, int] | None,
    *,
    has_previous: bool,
) -> tuple[str, str]:
    """Assess the error-counter *rate* between two observations of one interface.

    A counter total is meaningless without knowing how long it accumulated
    over -- 500 CRC errors across three months is noise, 500 in ten minutes is
    a dying optic. So this never reads a total in isolation; it only ever
    compares two of them. ``unevaluated`` when there is only one observation
    to look at, or when either observation is missing the counters outright
    (``None``, from :func:`_extract_error_counters`); ``broken`` only when a
    counter is strictly higher in ``current`` than in ``previous``.
    """

    if not has_previous:
        return UNEVALUATED, "single observation"
    if current is None or previous is None:
        return UNEVALUATED, "error counters absent from at least one observation"

    rising = [
        (counter, previous[counter], current[counter])
        for counter in ERROR_COUNTER_NAMES
        if counter in current and counter in previous and current[counter] > previous[counter]
    ]
    if rising:
        detail = "; ".join(f"{name} {was}->{now}" for name, was, now in rising)
        return BROKEN, f"error-counter rate rising: {detail}"
    return HEALTHY, "error-counter rate flat across two observations"


def interface_state(
    evidence: dict[str, Any], name: str, *, previous: dict[str, Any] | None = None
) -> CheckResult:
    """Is interface ``name`` up, and is its error-counter rate flat?

    Answers two questions that do **not** compose symmetrically:

    1. **Not fully up (admin_state and line_state both ``up``) is conclusively
       ``broken`` on a single observation, and outranks everything else,
       including the counter half.** This covers both the classic fault (admin
       up, line down -- a real link failure) and an interface shut at both
       layers (admin-down, line admin-down): PE2's uplinks in the ``broken``
       fixture are exactly the latter, and a descent investigating why PE2 is
       isolated at the IGP layer needs this rung to say ``broken``, not
       ``unevaluated`` -- an admin-down interface is still the fact that
       explains the isolation, whatever the operator's intent behind it was.
       ``health.py``'s ``interface_admin_up_line_down`` rule treats admin-down
       as intentional and deliberately does not fire for it -- that is a
       *device-health* judgement ("is this worth an operator's attention right
       now"), a different question from this check's ("is this interface up"),
       and the two are allowed to disagree; see the module docstring's
       "Relationship to health.py" section.
    2. Otherwise (both ``up``), the error-counter *rate* decides: a rising
       delta between ``evidence`` and ``previous`` is ``broken``.
    3. Otherwise -- line state read and ``up``, and the counter half did not
       reach a ``broken`` verdict -- the result is **``healthy``**, and the
       reason states plainly whether the counter half was actually evaluated.
       **This is deliberate and must not be "simplified" into `unevaluated`
       when there is no `previous`.** A descent runs on a single collection;
       if a missing second observation made the counter half `unevaluated`
       and that in turn made the *whole check* `unevaluated`, every descent
       would end `undetermined` the first time it reached an interface rung,
       which is precisely the failure Q-005's ruling rules out ("must never
       stop a descent"). The check answers `healthy` about the field it
       actually read -- line state -- and says in the reason what it did not
       evaluate, which is consistent with the module rule: that rule forbids
       claiming health about a field never read, not reporting a field that
       was read as what it says.
    4. If the interface's own admin/line state could not be read at all (no
       usable ``interface:<name>`` or ``interfaces`` record) -- ``unevaluated``.

    ``previous`` is an optional earlier ``evidence`` dict for the *same*
    interface, in the same shape as ``evidence`` itself (i.e. also carrying an
    ``interface:<name>`` and/or ``interfaces`` section). The delta is computed
    here, directly, rather than via ``network_tools.diff_evidence`` -- that
    function lives outside this module and calling it would break the "no
    dependency but parsers" purity ``tests/test_checks.py`` enforces.
    """

    obs, keys, bail = _read_interface_observation(evidence, name)
    if bail is not None:
        return bail

    admin_state, line_state, current_counters = obs

    if admin_state != "up" or line_state != "up":
        return broken(
            reason=(
                f"interface {name} is not up "
                f"(admin_state={admin_state!r}, line_state={line_state!r})"
            ),
            subject=name,
            evidence_keys=keys,
        )

    previous_counters: dict[str, int] | None = None
    previous_keys: tuple[str, ...] = ()
    if previous is not None:
        prev_obs, previous_keys, prev_bail = _read_interface_observation(previous, name)
        if prev_bail is None and prev_obs is not None:
            previous_counters = prev_obs[2]

    status, detail = _counter_delta_status(
        current_counters, previous_counters, has_previous=previous is not None
    )

    if status == BROKEN:
        return broken(
            reason=f"line protocol up; {detail}",
            subject=name,
            evidence_keys=tuple(dict.fromkeys(keys + previous_keys)),
        )
    if status == UNEVALUATED:
        return healthy(
            reason=f"line protocol up; error-counter rate not evaluated ({detail})",
            subject=name,
            evidence_keys=keys,
        )
    return healthy(
        reason=f"line protocol up; {detail}",
        subject=name,
        evidence_keys=tuple(dict.fromkeys(keys + previous_keys)),
    )


# =========================================================================== #
# Device health verdicts (merged from ``health.py`` at B-403)
# =========================================================================== #
#
# **These rules are not pure, and that is the cost of this merge.** Everything
# above answers "is this rung broken for this subject" from parsed records
# alone. Everything below answers "is this device healthy given its role", and
# the baseline rules need ``inventory/lab.yaml``'s ``expected:`` blocks to do
# it -- so this module now reads the inventory, which the section above states
# it does not.
#
# That guarantee used to be structural: the descent's dependency chain simply
# could not reach an inventory read, because the module holding its predicates
# imported nothing that could. After the merge it is a *tested* property
# instead -- ``test_the_descent_predicates_never_touch_the_inventory`` asserts
# each of the five rung predicates runs against a bare evidence dict with the
# inventory loader replaced by a bomb. An invariant you cannot violate became
# one you are told about.
#
# The operator directed this merge after that trade was stated (OBS-104). The
# one thing genuinely removed is real: ``_is_numeric`` existed twice, and its
# own docstring said it was "deliberately re-declared ... neither should have
# to import the other to share one three-line predicate".


# Ordered weakest to strongest so "max severity" is a simple index comparison.
SEVERITY_ORDER: tuple[str, ...] = ("ok", "info", "warning", "unreachable", "critical")

# The four intents any health rule reads. Kept as one tuple so the
# "unevaluated" bookkeeping in evaluate_device stays in lockstep with the
# rules below -- add an intent here (and to _build_context) before writing a
# rule that reads it.
_HEALTH_INTENTS: tuple[str, ...] = ("isis", "bgp", "interfaces", "sr")


def severity_rank(severity: str) -> int:
    """Return a severity's position in ``SEVERITY_ORDER`` (higher is worse)."""

    return SEVERITY_ORDER.index(severity)


def _max_severity(a: str, b: str) -> str:
    return a if severity_rank(a) >= severity_rank(b) else b


def exit_code_for_severity(severity: str) -> int:
    """Map a verdict's severity to a process exit code for CI/cron gating.

    ``0`` for ok/info (nothing actionable), ``1`` for warning/unreachable,
    ``2`` for critical. An unreachable device needs attention but is not a
    diagnosis, so it must not be rendered or paged as a confirmed critical.
    """

    if severity == "critical":
        return 2
    if severity in {"warning", "unreachable"}:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Evidence extraction: turn one device's evidence into a rule-ready context.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleContext:
    """One device's evidence, pre-extracted so every rule reads the same shape.

    ``*_ok`` records whether that intent's parse succeeded -- rules must check
    it before trusting the paired records/meta, never infer health from an
    empty list, since "no records" and "not evaluated" are different facts.
    """

    device: Device
    isis_ok: bool
    isis_records: list[dict[str, Any]]
    bgp_ok: bool
    bgp_records: list[dict[str, Any]]
    bgp_meta: dict[str, Any]
    interfaces_ok: bool
    interface_records: list[dict[str, Any]]
    sr_ok: bool
    sr_records: list[dict[str, Any]]


# Why an intent could not be evaluated. The distinction drives severity: a
# vendor that has no such command is benign, while an unreachable device or
# unreadable output means we are flying blind and must not report health.
UNEVAL_UNSUPPORTED = "unsupported"
UNEVAL_COLLECTION_FAILED = "collection_failed"
UNEVAL_PARSE_FAILED = "parse_failed"


def _intent_records(
    evidence: dict[str, Any], intent: str
) -> tuple[list[dict[str, Any]] | None, dict[str, Any], bool]:
    """Return ``(records, meta, ok)`` for one intent's section of evidence.

    ``ok`` is False whenever the section did not reach ``parsers.PARSE_OK`` --
    a health rule has nothing trustworthy to read in that case. Use
    ``_uneval_reason`` to find out *why*, which is what severity depends on.
    """

    section = evidence.get(intent)
    data = section.get("data", {}) if isinstance(section, dict) else {}
    if data.get("parse_status") != parsers.PARSE_OK:
        return None, {}, False
    parsed = data.get("parsed") or {}
    return list(parsed.get("records") or []), dict(parsed.get("meta") or {}), True


def _uneval_reason(evidence: dict[str, Any], intent: str) -> str | None:
    """Classify why an intent yielded nothing trustworthy, or None if it did.

    Order matters: a section whose commands never ran is a collection failure
    regardless of what parse status it carries.
    """

    section = evidence.get(intent)
    if not isinstance(section, dict):
        return UNEVAL_COLLECTION_FAILED

    status = section.get("status")
    if status == STATUS_UNSUPPORTED:
        return UNEVAL_UNSUPPORTED
    if status == STATUS_ERROR:
        return UNEVAL_COLLECTION_FAILED

    parse_status = section.get("data", {}).get("parse_status")
    if parse_status == parsers.PARSE_OK:
        return None
    if parse_status == parsers.PARSE_UNAVAILABLE:
        # No parser for this platform. Not the device's fault, but we still
        # cannot assess it -- treated as unsupported for severity purposes.
        return UNEVAL_UNSUPPORTED
    return UNEVAL_PARSE_FAILED


def _build_context(evidence: dict[str, Any], device: Device) -> RuleContext:
    isis_records, _, isis_ok = _intent_records(evidence, "isis")
    bgp_records, bgp_meta, bgp_ok = _intent_records(evidence, "bgp")
    interface_records, _, interfaces_ok = _intent_records(evidence, "interfaces")
    sr_records, _, sr_ok = _intent_records(evidence, "sr")
    return RuleContext(
        device=device,
        isis_ok=isis_ok,
        isis_records=isis_records or [],
        bgp_ok=bgp_ok,
        bgp_records=bgp_records or [],
        bgp_meta=bgp_meta,
        interfaces_ok=interfaces_ok,
        interface_records=interface_records or [],
        sr_ok=sr_ok,
        sr_records=sr_records or [],
    )


# --------------------------------------------------------------------------- #
# Role invariants: what must be true of a router in this fabric, given its
# role, independent of the (possibly broken) recorded baseline.
# --------------------------------------------------------------------------- #


def _isis_isolated(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a router with zero IS-IS adjacencies.

    Every router in this fabric must have at least one -- this must fire for
    PE2 and PE4 even though ``inventory/lab.yaml`` records their baseline as
    exactly zero (see the module docstring): the baseline describes a broken
    fabric, not a target.
    """

    if not ctx.isis_ok or ctx.isis_records:
        return []
    return [
        {
            "intent": "isis",
            "message": "No IS-IS adjacencies; device is isolated at the IGP layer.",
            "expected": ">=1",
            "actual": 0,
            "subject": None,
        }
    ]


def _isis_adjacency_not_up(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an IS-IS adjacency stuck in a non-Up state (one finding per adjacency)."""

    if not ctx.isis_ok:
        return []
    return [
        {
            "intent": "isis",
            "message": f"IS-IS adjacency to {record.get('system_id')} is "
            f"{record.get('state')}, not Up.",
            "expected": "Up",
            "actual": record.get("state"),
            "subject": record.get("system_id"),
        }
        for record in ctx.isis_records
        if record.get("state") != "Up"
    ]


def _bgp_session_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a BGP peer not in the Established state.

    IOS-XR reuses the ``St/PfxRcd`` summary column for a state word (``Idle``,
    ``Active``, ``Connect``, ...) whenever a session is not Established; a
    plain number in that column *is* the received-prefix count and means the
    session is up. Non-numeric there is the only reliable "session down"
    signal in this output.
    """

    if not ctx.bgp_ok:
        return []
    findings = []
    for record in ctx.bgp_records:
        state = record.get("session_state", "")
        if state != "Established":
            findings.append(
                {
                    "intent": "bgp",
                    "message": f"BGP session to {record.get('neighbor')} is not "
                    f"Established (state: {state}).",
                    "expected": "Established",
                    "actual": state,
                    "subject": record.get("neighbor"),
                }
            )
    return findings


def _bgp_process_absent(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an edge or route-reflector device with no active BGP process.

    A core P-router legitimately runs no BGP at all in this fabric (it only
    speaks IS-IS), so this must never fire for role "core" -- only edge and
    route-reflector roles are expected to run BGP.
    """

    if not ctx.bgp_ok or ctx.device.role == "core":
        return []
    # The parser only sets "active" (to False) on the inactive-process reply;
    # its absence means a normal, active process was parsed instead.
    if ctx.bgp_meta.get("active", True) is False:
        return [
            {
                "intent": "bgp",
                "message": f"No active BGP process; role {ctx.device.role!r} requires one.",
                "expected": "active",
                "actual": "inactive",
                "subject": None,
            }
        ]
    return []


def _bgp_no_prefixes(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an Established BGP peer carrying zero received prefixes.

    Legitimate in an empty lab with no routes advertised anywhere, so this is
    informational only -- never a warning or critical, unlike a session that
    is not Established at all (see ``bgp_session_down``).
    """

    if not ctx.bgp_ok:
        return []
    findings = []
    for record in ctx.bgp_records:
        # Absent means the session is not Established, which this rule is not
        # about -- `bgp_no_prefixes` fires on an *Established* peer carrying
        # nothing. Reading absence as zero would make every down session report
        # a prefix problem on top of its real one.
        prefixes = record.get("prefixes_received")
        if prefixes == 0:
            findings.append(
                {
                    "intent": "bgp",
                    "message": f"BGP peer {record.get('neighbor')} is Established "
                    "with 0 prefixes received.",
                    "expected": ">0",
                    "actual": 0,
                    "subject": record.get("neighbor"),
                }
            )
    return findings


def _interface_admin_up_line_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an interface administratively up whose line protocol is not up.

    An admin-down interface is intentional and must never fire this rule --
    only a link the operator meant to be usable but that is not actually
    passing traffic.
    """

    if not ctx.interfaces_ok:
        return []
    return [
        {
            "intent": "interfaces",
            "message": f"Interface {record.get('interface')} is admin up but line "
            f"protocol is {record.get('line_protocol')}.",
            "expected": "up",
            "actual": record.get("line_protocol"),
            "subject": record.get("interface"),
        }
        for record in ctx.interface_records
        if record.get("admin_state") == "up" and record.get("line_protocol") != "up"
    ]


def _sr_policy_down(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: an SR-TE policy that is not operationally up.

    Traffic steered onto a down policy has either fallen back to best-effort
    routing or has no path at all -- worth a warning, not a critical, since
    IGP forwarding underneath may still be fine.
    """

    if not ctx.sr_ok:
        return []
    return [
        {
            "intent": "sr",
            "message": f"SR-TE policy {record.get('policy')} is not operationally up "
            f"(state: {record.get('operational_state')}).",
            "expected": "up",
            "actual": record.get("operational_state"),
            "subject": record.get("policy"),
        }
        for record in ctx.sr_records
        if record.get("operational_state") != "up"
    ]


# --------------------------------------------------------------------------- #
# Baseline rules: observed counts vs. the inventory's recorded `expected:`.
# --------------------------------------------------------------------------- #


def _isis_adjacency_count_drift(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: observed IS-IS adjacency count differs from the learned baseline.

    Skipped entirely when no baseline was ever derived (``expected`` absent) --
    there is nothing to drift from.
    """

    if not ctx.isis_ok or ctx.device.expected is None:
        return []
    expected = ctx.device.expected.isis_adjacencies
    if expected is None:
        return []
    actual = len(ctx.isis_records)
    if actual == expected:
        return []

    # B-465. Direction matters, and treating both directions as one `warning`
    # was measured wrong: the MCP re-test found every baseline here stale --
    # derived from the broken fabric and never re-derived -- so the rule fired
    # on devices that had been *repaired*. An adjacency count reported as drift
    # because connectivity was restored is indistinguishable from one reported
    # because connectivity was lost, and only the second is a fault.
    if actual > expected:
        return [
            {
                "intent": "isis",
                "severity": "info",
                "message": (
                    f"IS-IS adjacency count {actual} is above the recorded "
                    f"baseline {expected}. More adjacencies than expected is not "
                    "a fault; the baseline is likely stale. Re-run "
                    "`nettools learn-topology` against the current fabric."
                ),
                "expected": expected,
                "actual": actual,
                "subject": None,
            }
        ]

    return [
        {
            "intent": "isis",
            "message": f"IS-IS adjacency count {actual} is below the recorded "
            f"baseline {expected}.",
            "expected": expected,
            "actual": actual,
            "subject": None,
        }
    ]


def _bgp_peer_count_drift(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: observed BGP peer count differs from the learned baseline.

    Skipped for a device with no ``bgp_peers`` baseline at all -- a device
    with no BGP process (never given a baseline, see ``topology.py``) has
    nothing to compare.
    """

    if not ctx.bgp_ok or ctx.device.expected is None:
        return []
    expected = ctx.device.expected.bgp_peers
    if expected is None:
        return []
    actual = len(ctx.bgp_records)
    if actual == expected:
        return []
    return [
        {
            "intent": "bgp",
            "message": f"BGP peer count {actual} differs from the recorded baseline "
            f"{expected}.",
            "expected": expected,
            "actual": actual,
            "subject": None,
        }
    ]


# --------------------------------------------------------------------------- #
# Meta rule: keep the tool honest about its own (possibly broken) baseline.
# --------------------------------------------------------------------------- #


# B-465/OBS-146. The first version of this rule tested a VALUE
# (``isis_adjacencies == 0``) where the defect is a RELATIONSHIP: a baseline
# that disagrees with what the device's role requires, whatever the value is.
# Testing only zero was measured wrong on the case that motivated the item --
# PE1's recorded baseline was ``1``, `_isis_isolated` only requires ">= 1", so
# ``1`` satisfies that invariant and a rule reusing it would have missed PE1
# exactly as the old rule did (OBS-146).
#
# This fabric has no single-homed role: every device, including the leanest
# ones (an edge PE or the route reflector, each dual-homed to two P-routers
# by design), carries at least two IS-IS adjacencies once healthy -- the
# 2026-08-19 re-derivation (B-465/B-516) measured every device in
# ``inventory/lab.yaml`` at or above this floor except PE3, deliberately left
# alone for its own still-open fault (B-496). A recorded baseline *below* the
# floor for its role was learned while the device looked less connected than
# this fabric's design ever allows, which is exactly the "learned from a
# broken fabric" shape `suspicious_baseline` exists to catch -- one is as
# telling as zero.
#
# A future role that is genuinely meant to be single-homed would need its own
# entry here rather than reusing this table -- do not lower the floor to fit
# a new role, add a role.
ROLE_MIN_ISIS_ADJACENCIES: dict[str, int] = {
    "core": 2,
    "edge": 2,
    "route-reflector": 2,
}


def _suspicious_baseline(ctx: RuleContext) -> list[dict[str, Any]]:
    """Catches: a recorded baseline that disagrees with its role's expected shape.

    Not "is this value zero" -- "does this baseline fall below what
    ``ROLE_MIN_ISIS_ADJACENCIES`` requires for this device's role". Zero is
    still caught (it is always below the floor), but so is any other value
    that undershoots the role, e.g. an edge device recorded at ``1`` when
    every edge in this fabric is dual-homed. This never depends on freshly
    observed evidence -- it is a statement about the inventory itself -- so it
    is not gated on any intent's parse status.

    Absence is never zero: a device with no recorded ``isis_adjacencies``
    baseline at all has nothing to be suspicious of yet (`learn-topology`
    never ran, or the field was never populated) -- that is "not
    established", not "0 adjacencies", and must not fire.
    """

    expected = ctx.device.expected
    if expected is None or expected.isis_adjacencies is None:
        return []
    actual = expected.isis_adjacencies
    role = ctx.device.role
    floor = ROLE_MIN_ISIS_ADJACENCIES.get(role, 1)
    if actual >= floor:
        return []
    return [
        {
            "intent": None,
            "message": (
                f"Recorded baseline for {ctx.device.name} (role {role!r}) "
                f"encodes isis_adjacencies={actual}, below the minimum of "
                f"{floor} this fabric's design requires for that role -- a "
                "broken state captured at learn-topology time, not a "
                "healthy target."
            ),
            "expected": f">={floor} (role {role!r} invariant)",
            "actual": actual,
            "subject": None,
        }
    ]


# --------------------------------------------------------------------------- #
# The rule table itself. Adding a rule is: write a small function above
# returning finding fragments (message/expected/actual/subject/intent), then
# add one line below naming it, its severity, and which table it belongs to.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    fn: Callable[[RuleContext], list[dict[str, Any]]]


ROLE_INVARIANT_RULES = (
    Rule("isis_isolated", "critical", _isis_isolated),
    Rule("isis_adjacency_not_up", "critical", _isis_adjacency_not_up),
    Rule("bgp_session_down", "critical", _bgp_session_down),
    Rule("bgp_process_absent", "warning", _bgp_process_absent),
    Rule("bgp_no_prefixes", "info", _bgp_no_prefixes),
    Rule("interface_admin_up_line_down", "warning", _interface_admin_up_line_down),
    Rule("sr_policy_down", "warning", _sr_policy_down),
)

BASELINE_RULES = (
    Rule("isis_adjacency_count_drift", "warning", _isis_adjacency_count_drift),
    Rule("bgp_peer_count_drift", "warning", _bgp_peer_count_drift),
)

META_RULES: tuple[Rule, ...] = (Rule("suspicious_baseline", "warning", _suspicious_baseline),)

ALL_RULES: tuple[Rule, ...] = ROLE_INVARIANT_RULES + BASELINE_RULES + META_RULES


# --------------------------------------------------------------------------- #
# Evaluation: one device, then a whole fabric roll-up.
# --------------------------------------------------------------------------- #


def evaluate_device(evidence: dict[str, Any], device: Device) -> dict[str, Any]:
    """Evaluate one device's collected evidence into a health verdict.

    Runs every rule in ``ALL_RULES`` against the same ``RuleContext``; each
    rule already guards its own required intent(s), so a failed/unsupported
    intent simply yields no findings for the rules that need it -- it is
    listed in ``unevaluated`` instead of being silently read as healthy.
    """

    ctx = _build_context(evidence, device)

    findings: list[dict[str, Any]] = []
    for rule in ALL_RULES:
        for raw in rule.fn(ctx):
            findings.append(
                {
                    "rule": rule.name,
                    # A rule's severity is normally a property of the rule. A
                    # fragment may override it when severity genuinely depends
                    # on the *instance* rather than the kind -- see
                    # `_isis_adjacency_count_drift`, where losing an adjacency
                    # and gaining one are the same rule and not the same news.
                    # The override is deliberate and rare; a rule reaching for
                    # it routinely is two rules.
                    "severity": raw.get("severity", rule.severity),
                    "intent": raw.get("intent"),
                    "message": raw["message"],
                    "expected": raw.get("expected"),
                    "actual": raw.get("actual"),
                    "subject": raw.get("subject"),
                }
            )

    # Classify why each health intent could not be evaluated, then let genuine
    # failures raise severity. Without this a totally unreachable device scores
    # "ok" with exit code 0 -- every rule guards its own intent, so an empty
    # collection simply produces no findings, and silence reads as health. That
    # is the single most dangerous outcome this module can produce.
    reasons = {intent: _uneval_reason(evidence, intent) for intent in _HEALTH_INTENTS}
    unsupported = sorted(i for i, r in reasons.items() if r == UNEVAL_UNSUPPORTED)
    failed_collection = sorted(i for i, r in reasons.items() if r == UNEVAL_COLLECTION_FAILED)
    failed_parse = sorted(i for i, r in reasons.items() if r == UNEVAL_PARSE_FAILED)
    unevaluated = sorted(failed_collection + failed_parse)

    if failed_collection and not any(reasons[i] is None for i in _HEALTH_INTENTS):
        # Nothing at all could be read: report an operationally distinct
        # unreachable state, not a confirmed critical fault or healthy state.
        findings.append(
            {
                "rule": "device_unreachable",
                "severity": "unreachable",
                "intent": None,
                "message": (
                    "no intent could be collected; the device is unreachable or "
                    "every command failed, so its health is unknown -- not ok"
                ),
                "expected": "at least one intent collected",
                "actual": f"all {len(_HEALTH_INTENTS)} health intents failed",
                "subject": None,
            }
        )
    else:
        for intent in failed_collection:
            findings.append(
                {
                    "rule": "intent_collection_failed",
                    "severity": "warning",
                    "intent": intent,
                    "message": f"{intent} could not be collected, so it was not assessed",
                    "expected": "collected",
                    "actual": "error",
                    "subject": None,
                }
            )
    for intent in failed_parse:
        findings.append(
            {
                "rule": "intent_unparsed",
                "severity": "warning",
                "intent": intent,
                "message": f"{intent} returned output that could not be parsed, so it was not assessed",
                "expected": "parseable output",
                "actual": "parse failed",
                "subject": None,
            }
        )

    counts = {"critical": 0, "unreachable": 0, "warning": 0, "info": 0}
    severity = "ok"
    for finding in findings:
        severity = _max_severity(severity, finding["severity"])
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1

    platform = str(evidence.get("platform") or device.platform or DEFAULT_PLATFORM)
    # One record per device evaluated, whether called directly or via
    # evaluate_fabric (which calls this once per device) -- see metrics.py's
    # module docstring for why this is the one hook point for verdict counts.
    metrics.record_verdict(severity)
    return {
        "device": device.name,
        "role": device.role,
        "platform": platform,
        "severity": severity,
        "findings": findings,
        "counts": counts,
        # Split deliberately: "unevaluated" is what we failed to assess and is
        # reflected in severity; "unsupported" is what this platform has no
        # command for, which is benign and must never move severity.
        "unevaluated": unevaluated,
        "unsupported": unsupported,
    }


def evaluate_fabric(
    evidence_by_device: dict[str, dict[str, Any]],
    devices: list[Device] | None = None,
) -> dict[str, Any]:
    """Evaluate every device with evidence, and roll up to one fabric severity.

    ``devices`` defaults to the resolved inventory (``load_inventory_file()``),
    so the common case needs only an evidence dict. Devices are visited in
    inventory order for deterministic output; a device with evidence but no
    matching inventory entry (e.g. one already removed from the YAML) is
    skipped rather than guessed at.
    """

    if devices is None:
        devices = load_inventory_file().devices

    device_verdicts: dict[str, Any] = {}
    fabric_severity = "ok"
    for device in devices:
        evidence = evidence_by_device.get(device.name)
        if evidence is None:
            continue
        verdict = evaluate_device(evidence, device)
        device_verdicts[device.name] = verdict
        fabric_severity = _max_severity(fabric_severity, verdict["severity"])

    # Fabric-level roll-up: how many devices sit at each severity, and how many
    # had an intent that could not be evaluated. At fleet scale the per-device
    # list is unreadable, so this line is what an operator or a cron job reads.
    by_severity: dict[str, int] = {
        "critical": 0, "unreachable": 0, "warning": 0, "info": 0, "ok": 0,
    }
    for verdict in device_verdicts.values():
        by_severity[verdict["severity"]] += 1

    return {
        "severity": fabric_severity,
        "counts": {
            "devices": len(device_verdicts),
            "by_severity": by_severity,
            "unevaluated_devices": sorted(
                name for name, verdict in device_verdicts.items() if verdict["unevaluated"]
            ),
        },
        "devices": device_verdicts,
    }


# --------------------------------------------------------------------------- #
# Subject existence -- the input side of the containment boundary (B-459)
# --------------------------------------------------------------------------- #


def bgp_peer_exists(evidence: dict[str, Any], peer: str) -> CheckResult:
    """Does this device have a BGP neighbour at ``peer`` at all?

    **Runs before the descent walks**, and it is the only check in this module
    that answers a question about the *question* rather than about the network.

    Why it is needed. `render_command` validates a peer address by
    reconstruction, which is a check on **syntax** -- `10.255.0.99` is a
    perfectly well-formed IPv4 address and always will be. Nothing downstream
    then asks whether the device has ever heard of it: rung 1 reads
    `show bgp summary`, finds no matching row, and reports honestly that the
    session is not Established. Every rung beneath answers honestly too. The
    report cites real evidence keys and grounding confirms they resolve.

    The result is a fully sourced investigation of a session that does not
    exist, with **nothing malfunctioning anywhere** -- which is why no gate
    caught it and why the fix has to be at the input rather than anywhere along
    the chain.

    `unevaluated` when the BGP intent could not be read, because "the device has
    no such peer" and "we could not ask" are different answers and only one of
    them means the caller should change the question.
    """

    device = str(evidence.get("device", "unknown"))
    section, bail = require_parsed(evidence, "bgp", subject=peer)
    if bail is not None:
        return bail

    key = evidence_key(device, "bgp", peer)
    neighbours = [r.get("neighbor") for r in parsed_records(section) if r.get("neighbor")]
    if peer in neighbours:
        return healthy(
            subject=peer,
            evidence_keys=(key,),
            reason=f"{device} has a BGP neighbour at {peer}",
        )

    # Deliberately lists what the device *does* have. A refusal that only says
    # "not found" invites the caller to try another guess; one that shows the
    # real neighbours ends the guessing.
    return broken(
        subject=peer,
        evidence_keys=(key,),
        reason=(
            f"{device} has no BGP neighbour at {peer}. It has "
            f"{len(neighbours)}: {', '.join(sorted(neighbours)) or 'none'}"
        ),
    )


def interface_exists(evidence: dict[str, Any], name: str) -> CheckResult:
    """Does this device have an interface called ``name``?

    The `interface` flow's counterpart to :func:`bgp_peer_exists`. Matched
    through `interface_kind.canonical` so `Gi0/0/0/0` and
    `GigabitEthernet0/0/0/0` are one name -- **within this device**, which is
    the only scope that comparison is valid in (OBS-117).
    """

    from .interface_kind import same_interface

    device = str(evidence.get("device", "unknown"))
    section, bail = require_parsed(evidence, "interfaces", subject=name)
    if bail is not None:
        return bail

    key = evidence_key(device, "interfaces", name)
    present = [r.get("interface") for r in parsed_records(section) if r.get("interface")]
    if any(same_interface(candidate, name) for candidate in present):
        return healthy(
            subject=name,
            evidence_keys=(key,),
            reason=f"{device} has an interface {name}",
        )
    return broken(
        subject=name,
        evidence_keys=(key,),
        reason=(
            f"{device} has no interface {name}. It has "
            f"{len(present)}: {', '.join(sorted(present)) or 'none'}"
        ),
    )
