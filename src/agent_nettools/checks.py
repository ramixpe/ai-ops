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
from typing import Any

from . import parsers

__all__ = [
    "BROKEN",
    "HEALTHY",
    "STATUSES",
    "UNEVALUATED",
    "CheckResult",
    "broken",
    "evidence_key",
    "healthy",
    "parsed_records",
    "require_parsed",
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


def healthy(*, subject: str | None, evidence_keys: tuple[str, ...], reason: str | None = None):
    return CheckResult(HEALTHY, reason=reason, subject=subject, evidence_keys=evidence_keys)


def broken(*, reason: str, subject: str | None, evidence_keys: tuple[str, ...]):
    return CheckResult(BROKEN, reason=reason, subject=subject, evidence_keys=evidence_keys)


def unevaluated(*, reason: str, subject: str | None = None, evidence_keys: tuple[str, ...] = ()):
    """The honest answer when the evidence needed to decide was not there.

    Always carries a reason. "Unevaluated" with no explanation is only
    marginally better than a wrong answer, because the operator still cannot
    tell whether to re-collect, fix a parser, or look elsewhere.
    """

    return CheckResult(UNEVALUATED, reason=reason, subject=subject, evidence_keys=evidence_keys)


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
