"""Ownership / escalation routing: who gets a finding, declared as a table. B-484.

``event_routing.route_event`` picks *what* to investigate -- a flow, a
device, a subject. That is not this module's question. This module answers
the question that comes after an investigation already has a finding: *who
should be told*. Keeping the two separate is deliberate, and mirrors
``event_routing.py``'s own boundary rule almost exactly: routing is a
**table lookup, not a model judgement**, and it is a table a human can read
end to end, not logic threaded through the delivery mechanics in
``notifier.py``. "Keep it declarative and inspectable" (the operator's own
framing for B-484) means the routing *decision* lives here, as data plus one
pure function over it, and ``notifier.py`` only ever executes a decision this
module already made.

A pure function, the same shape as ``event_routing.RoutingDecision``
----------------------------------------------------------------------
No I/O, no daemon, no retry, no state carried between calls.
:func:`resolve_ownership` takes a table and the facts about one finding and
returns who should hear about it and why -- deterministic, and replayable
from a ticket or a test fixture with nothing else running.

What "escalate after N minutes" can honestly mean here
-----------------------------------------------------------
``notifier.py`` has **no inbound surface, on purpose** -- see its module
docstring, which calls an inbound path "an unauthenticated trigger surface
this project has no identity provider to put in front of". Without an inbound
path there is no acknowledgement, and without an acknowledgement this module
cannot know whether a human has looked at anything. "Escalate after N minutes
*unacknowledged*" would therefore have to fabricate an acknowledgement state
it does not have -- the same shape this build already refuses elsewhere
(B-459, argument fabrication: a model naming a tool call it never made). This
module does not build that.

What it builds instead is the honest, state-free version: **escalate once
this same condition has been open for N minutes**, where "open" is measured
from a timestamp the *caller* supplies (``age_seconds``) rather than tracked
here. The diagnosis ledger's ``recorded_at`` for the earliest still-open
diagnosis of a cause is the natural source once a caller wires it in --
``ledger.py`` is owned by another track this session, so that wiring is
described in the report accompanying this change rather than built here.
Omit ``age_seconds`` and escalation simply never fires, which is correct: a
caller with nothing to measure age against has no basis to claim something
has been open for any length of time.

What happens when nothing matches
------------------------------------
**Never silence.** :class:`OwnershipTable` cannot be constructed without a
``default_owner`` that is itself a declared owner -- the table fails to
*load* rather than fail to *match* -- so "no rule matched" can never resolve
to "nobody is told" by omission. This is the opposite default from
``notifier.TelegramNotifier``'s empty chat-id allowlist, and deliberately so:
there, an unset destination is a misconfiguration worth failing closed on.
Here, an unmatched finding is not a misconfiguration -- it is the table not
yet having been extended for a new device or a new kind of finding, and the
operator still needs to hear about it. :func:`resolve_ownership` reports
which case happened (``matched_rule`` is ``None`` on the fallback path) so a
caller can log "routed to default; consider adding a rule for this" instead
of that fact disappearing.

With zero configuration, :func:`default_table` reproduces today's exact
behaviour -- one owner, delivering through whatever ``notifier.get_notifier``
already resolves from ``NETTOOLS_NOTIFIER`` -- so a single-operator lab is
never forced to declare a table just to keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DEFAULT_CHANNEL",
    "DEFAULT_OWNER_NAME",
    "Owner",
    "OwnershipError",
    "OwnershipRule",
    "OwnershipTable",
    "RoutingResult",
    "default_table",
    "load_ownership_table",
    "parse_ownership_table",
    "resolve_ownership",
]


class OwnershipError(Exception):
    """The declarative table is malformed, or names an owner that is not declared."""


#: The sentinel channel meaning "unchanged": deliver through whatever
#: ``notifier.get_notifier`` already resolves from ``NETTOOLS_NOTIFIER``/env,
#: exactly as it does with no ownership table at all. Any other channel value
#: is interpreted by ``notifier.notify_owner`` as ``"<provider>:<destination>"``.
DEFAULT_CHANNEL = "notifier-default"

DEFAULT_OWNER_NAME = "default"


@dataclass(frozen=True)
class Owner:
    """One destination, and its own optional escalation target.

    ``escalate_to``/``escalate_after_seconds`` must be set together or not at
    all -- a threshold with no target, or a target with no threshold, is a
    rule that can never fire correctly, and that is a configuration mistake
    worth failing on at construction rather than silently never escalating.
    """

    name: str
    channel: str
    escalate_to: str | None = None
    escalate_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.escalate_to is None) != (self.escalate_after_seconds is None):
            raise OwnershipError(
                f"owner {self.name!r}: escalate_to and escalate_after_seconds must be "
                "set together, or not at all"
            )
        if self.escalate_after_seconds is not None and self.escalate_after_seconds <= 0:
            raise OwnershipError(
                f"owner {self.name!r}: escalate_after_seconds must be > 0, got "
                f"{self.escalate_after_seconds!r}"
            )


@dataclass(frozen=True)
class OwnershipRule:
    """One row: match on (device, role, finding) -- each optional, each an AND."""

    name: str
    owner: str
    device: str | None = None
    role: str | None = None
    finding: str | None = None

    def matches(self, *, device: str, role: str | None, finding: str | None) -> bool:
        if self.device is not None and self.device != device:
            return False
        if self.role is not None and self.role != role:
            return False
        if self.finding is not None and self.finding != finding:
            return False
        return True


@dataclass(frozen=True)
class OwnershipTable:
    """The whole declarative table: every owner, every rule, and the default.

    First matching rule wins (declaration order). Validated at construction,
    not at lookup time, so a broken table is refused before it can ever
    silently fall through: every rule's ``owner`` must be declared, every
    owner's ``escalate_to`` (if any) must be declared, and ``default_owner``
    must be declared.
    """

    rules: tuple[OwnershipRule, ...]
    owners: dict[str, Owner]
    default_owner: str

    def __post_init__(self) -> None:
        if self.default_owner not in self.owners:
            raise OwnershipError(
                f"default_owner {self.default_owner!r} is not a declared owner -- "
                f"known owners: {sorted(self.owners)}"
            )
        for rule in self.rules:
            if rule.owner not in self.owners:
                raise OwnershipError(
                    f"rule {rule.name!r} names owner {rule.owner!r}, which is not "
                    f"declared -- known owners: {sorted(self.owners)}"
                )
        for owner in self.owners.values():
            if owner.escalate_to is not None and owner.escalate_to not in self.owners:
                raise OwnershipError(
                    f"owner {owner.name!r} escalates to {owner.escalate_to!r}, which "
                    f"is not a declared owner -- known owners: {sorted(self.owners)}"
                )


def default_table() -> OwnershipTable:
    """One owner, no rules, no escalation -- today's single-channel behaviour.

    What a caller gets with zero configuration. B-484 exists to add routing
    for a NOC that has grown past one channel; a single-operator lab must not
    be forced to declare a table just to keep working exactly as it does
    today.
    """

    owner = Owner(name=DEFAULT_OWNER_NAME, channel=DEFAULT_CHANNEL)
    return OwnershipTable(rules=(), owners={owner.name: owner}, default_owner=owner.name)


def parse_ownership_table(data: Any) -> OwnershipTable:
    """Turn already-loaded YAML/JSON data into a validated :class:`OwnershipTable`.

    Separated from :func:`load_ownership_table` so a caller with in-memory
    data (a test, a future CLI subcommand) never needs a file on disk.
    """

    if not isinstance(data, dict):
        raise OwnershipError("an ownership table must be a mapping at the top level")

    owners_raw = data.get("owners")
    if not isinstance(owners_raw, list) or not owners_raw:
        raise OwnershipError(
            "an ownership table must declare at least one owner under 'owners'"
        )

    owners: dict[str, Owner] = {}
    for index, entry in enumerate(owners_raw):
        if not isinstance(entry, dict) or not entry.get("name"):
            raise OwnershipError(f"owners[{index}]: each owner needs a 'name'")
        name = str(entry["name"])
        if name in owners:
            raise OwnershipError(f"owner {name!r} is declared twice")
        channel = entry.get("channel")
        if not channel or not isinstance(channel, str):
            raise OwnershipError(f"owner {name!r}: 'channel' is required and must be a string")
        escalate_after = entry.get("escalate_after_seconds")
        owners[name] = Owner(
            name=name,
            channel=channel,
            escalate_to=entry.get("escalate_to"),
            escalate_after_seconds=(float(escalate_after) if escalate_after is not None else None),
        )

    rules_raw = data.get("rules") or []
    if not isinstance(rules_raw, list):
        raise OwnershipError("'rules' must be a list when present")

    rules: list[OwnershipRule] = []
    seen_rule_names: set[str] = set()
    for index, entry in enumerate(rules_raw):
        if not isinstance(entry, dict) or not entry.get("owner"):
            raise OwnershipError(f"rules[{index}]: each rule needs at least an 'owner'")
        name = str(entry.get("name") or f"rule-{index}")
        if name in seen_rule_names:
            raise OwnershipError(f"rule name {name!r} is declared twice")
        seen_rule_names.add(name)
        rules.append(
            OwnershipRule(
                name=name,
                owner=str(entry["owner"]),
                device=entry.get("device"),
                role=entry.get("role"),
                finding=entry.get("finding"),
            )
        )

    default_owner = data.get("default_owner")
    if not default_owner:
        raise OwnershipError(
            "'default_owner' is required -- a table with no default is a table that "
            "can silently drop an unmatched finding, which B-484 refuses to allow"
        )

    return OwnershipTable(rules=tuple(rules), owners=owners, default_owner=str(default_owner))


def load_ownership_table(path: str | Path | None) -> OwnershipTable:
    """The declarative ownership table, or :func:`default_table` if none is configured.

    Same "no dedicated environment variable" position ``health.load_silences``
    takes, and for the identical reason -- ``settings.py`` is owned by
    another track this session; see that function's docstring.

    ``path=None`` (or a path that does not exist) is not an error: it means
    "not configured yet", and returns :func:`default_table`, preserving
    today's single-channel behaviour exactly. A file that exists but is
    malformed **does** raise (:class:`OwnershipError`) -- the same
    "a typo must not silently disable the thing it configures" rule
    ``notifier.get_notifier`` applies to an unknown provider name.
    """

    if path is None:
        return default_table()
    resolved = Path(path)
    if not resolved.exists():
        return default_table()
    try:
        raw = resolved.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise OwnershipError(f"cannot read ownership table {resolved}: {exc}") from exc
    return parse_ownership_table(data)


@dataclass(frozen=True)
class RoutingResult:
    """Who gets told, and why. ``owners`` is never empty -- see the module docstring."""

    owners: tuple[Owner, ...]
    matched_rule: str | None
    reason: str
    escalated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "owners": [o.name for o in self.owners],
            "matched_rule": self.matched_rule,
            "reason": self.reason,
            "escalated": self.escalated,
        }


def resolve_ownership(
    table: OwnershipTable,
    *,
    device: str,
    role: str | None = None,
    finding: str | None = None,
    age_seconds: float | None = None,
) -> RoutingResult:
    """Who owns this finding, per ``table`` -- first matching rule wins.

    Falls back to ``table.default_owner`` when nothing matches (never to
    "nobody" -- see the module docstring). When the resolved owner declares
    an escalation and ``age_seconds`` meets or exceeds its threshold, the
    escalation target is appended to ``owners`` alongside the primary --
    both are told, not just the escalation target, so ownership is never
    silently reassigned away from the original owner.
    """

    rule = next(
        (r for r in table.rules if r.matches(device=device, role=role, finding=finding)),
        None,
    )
    if rule is not None:
        owner = table.owners[rule.owner]
        matched_rule = rule.name
        reason = f"rule {rule.name!r} routes to owner {owner.name!r}"
    else:
        owner = table.owners[table.default_owner]
        matched_rule = None
        reason = (
            f"no rule matched device={device!r} finding={finding!r}; routed to the "
            f"default owner {owner.name!r} -- a default of silence is refused by "
            "design (B-484)"
        )

    owners = [owner]
    escalated = False
    if (
        owner.escalate_to is not None
        and owner.escalate_after_seconds is not None
        and age_seconds is not None
        and age_seconds >= owner.escalate_after_seconds
    ):
        escalate_owner = table.owners[owner.escalate_to]
        owners.append(escalate_owner)
        escalated = True
        reason += (
            f"; open {age_seconds:.0f}s >= {owner.escalate_after_seconds:.0f}s, "
            f"escalated to {escalate_owner.name!r}"
        )

    return RoutingResult(
        owners=tuple(owners), matched_rule=matched_rule, reason=reason, escalated=escalated
    )
