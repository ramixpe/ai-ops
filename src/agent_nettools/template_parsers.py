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
# The registry
# --------------------------------------------------------------------------- #

# Keyed by (platform, template_name), mirroring parsers.PARSERS' (platform,
# intent) shape. A template parser takes the single rendered command's output.
#
# Empty until T-012. The contract, its tests, and the accounting helper above
# all exist first, deliberately: they are what every parser is then written
# against, and getting them right is cheaper before there are six
# implementations to keep in step.
TEMPLATE_PARSERS: dict[tuple[str, str], Callable[[str], dict[str, Any]]] = {}

# Fields that move on their own between two captures of an unchanged device --
# uptimes, counters, timestamps. Excluded from comparison by diff_evidence and
# detect_flaps, exactly as parsers.VOLATILE_FIELDS is.
TEMPLATE_VOLATILE_FIELDS: dict[tuple[str, str], frozenset[str]] = {}

# The field identifying a record across two captures. ``None`` means records
# are positional and cannot be matched by identity.
TEMPLATE_RECORD_KEYS: dict[tuple[str, str], str | None] = {}


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
