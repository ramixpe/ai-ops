"""Tier 3: a bounded, contained drill-down into raw log evidence. B-419.

`docs/design/evidence-reduction.md` §9 and decision D11 draw one line:
reduction is mandatory and outside model control, but *investigation scope*
is not. A model is never handed `get_raw_logs()` -- but it may ask, for
**one evidence key at a time**, for the first occurrence, the last
occurrence, a bounded set of neighbouring raw records, and a pointer to
where the fuller record lives. Four tiers, each reachable only from the one
above:

    0  the finding and its causal chain          -- prompt_library.py
    1  normalised records                        -- log_window.ShapedWindow
    2  representative verbatim samples            -- log_window.MnemonicAggregate
    3  raw records, via `expand_evidence` ONLY    -- this module

Tiers 0-2 already exist and are not reimplemented here: `descent_payload`/
`finding_payload` (`prompt_library.py`) are tier 0, `log_window.shape_window`
already produces tier 1 (`ShapedWindow.records`), and
`log_window.aggregate_by_mnemonic` already produces tier 2
(`MnemonicAggregate.sample`/`.first_seen`/`.last_seen`). This module adds the
one thing evidence-reduction.md names as the "defensible exception" to the
five-tool decision: `expand_evidence`, and the tier-2 disclosure step
(`disclose_log_window`) that makes calling it possible at all.

The operator's decision on tier 3 and invariant 4
----------------------------------------------------
Invariant 4 (`CLAUDE.md`, `docs/build/PROCESS.md` §0.6) is *no unparsed
device text ever reaches a model*. Tier 3 is, by definition, unparsed device
text -- exactly the anti-pattern `evidence-reduction.md` §11 names ("the
model reads raw lines"). That tension was put to the operator explicitly
rather than resolved by assumption, and the decision was: **build all four
tiers, with tier 3 contained** -- every raw record passes through
`model_egress.quote_device_text` so it arrives marked untrusted rather than
bare. Invariant 4 is preserved in substance (nothing unmarked crosses); the
surface it applies to is wider (a marked span of raw text now can cross,
where before none did at all).

So the hard requirement this module exists to meet is not "reduce logs
correctly" -- `log_window.py`/B-418 already do that -- it is: **tier 3
exists, and nothing in it may reach a caller unquoted.**

Reused, not reimplemented
--------------------------
`model_egress.quote_device_text` is imported, never copied or reimplemented.
It already strips any occurrence of its own delimiter strings from the input
(`quote_device_text`'s own docstring), which is the one property that stops
a hostile log line from forging a close-tag and splicing itself out of the
untrusted span -- getting that subtly wrong a second time in a second
module is exactly the risk B-467/B-470's "two independent guarantees, not
one" section warns against.

Applied once, structurally, at the boundary
-----------------------------------------------
`expand_evidence` assembles its whole answer first, with raw text sitting in
plain (unquoted) string fields, and quotes it in exactly one place, at the
very end, by walking the assembled payload recursively and wrapping every
value found under a declared field name (`_RAW_TEXT_FIELDS`). This is the
same shape `mcp_server.boundary.sanitize` and `ticket_read.
_quote_untrusted_fields` already use, and for the same reason their own
docstrings give: quoting field-by-field while a payload is being built is a
call site a future field addition can forget; quoting once, over the whole
assembled structure, right before it is returned, cannot be partially
applied. Every return path in `expand_evidence` -- success or refusal --
goes through this one function, not just the branch that happens to carry
raw text today.

Each tier reachable only from the one above -- enforced, not documented
----------------------------------------------------------------------------
Three independent checks, not one, because a single check is one bypass
away from being decorative -- each closes a different way a caller could
otherwise skip a tier:

1. **Tier 1 -> Tier 2, by construction.** `expand_evidence` does not accept
   a bare `ShapedWindow` (tier 1). It requires a `DisclosedLogWindow`, and
   the only way to build one is `disclose_log_window`, which derives the
   tier-2 aggregates *from* the tier-1 window itself (`log_window.
   aggregate_by_mnemonic`) rather than accepting them as an independently
   fabricable argument. A caller cannot claim "tier 2 was already shown"
   without actually deriving it from a real tier-1 window.

2. **Tier 0 -> the rest, by descent membership.** The key's device must be
   part of the investigation itself: either the calling `DescentResult`'s
   own device or one of the devices its rungs actually evaluated
   (`result.device` plus every `RungOutcome.device`). A key naming a device
   the descent never touched could not have been legitimately disclosed at
   tier 0 either, so it is refused before tier 1/2 are even consulted.

3. **Tier 2 -> Tier 3, by disclosure membership.** Two further conditions,
   both checked at runtime: the key's device must equal the device
   `disclose_log_window` actually built the disclosure for (a real,
   descent-touched device is still refused if it names a DIFFERENT device's
   window than the one disclosed), and the key's mnemonic must be present
   among the aggregates that disclosure actually computed
   (`DisclosedLogWindow.aggregate_for`). A mnemonic tier 1 filtered out
   (noise, a duplicate, a subject-narrowed window) or that was never in this
   device's collected window at all is refused as `EXPANSION_UNREACHABLE`,
   not silently expanded.

Bounded, and one key at a time
----------------------------------
`evidence_key` is validated through `storage_key.is_valid_storage_key`
(shared with `evidence_store.py`/`fixtures.py`/`session_memory.py`) for its
device and mnemonic components -- the same charset a device name or a
fixture label must already satisfy elsewhere in this project. That charset
excludes glob and wildcard characters (`*`, `?`, `[`, `]`, `,`) and is a
single `str`, never a list, by the function's own type signature -- "one
evidence key at a time, never a list, never a glob" is enforced by the same
validation this project already trusts, not a bespoke check invented here.

`neighbor_limit` is capped at `MAX_NEIGHBOR_LIMIT` (5, lines on each side of
one anchor) and refused, never silently clamped, if a caller asks for more.
Measured against the real `PE2/broken` fixture used in this module's own
tests: 200 raw log lines average 187 characters each (max 223). Five lines
on each side of an anchor is, worst case, about 1,115 characters; with up to
two anchors (first and last occurrence can both need their own
neighbourhood) that is at most about 4,460 characters of raw text for one
call -- deliberately the same order of magnitude as `evidence_budget.
DEFAULT_PER_INTENT_CHAR_BUDGET` (4,000), so one bounded drill-down costs
about what one ordinary evidence section already costs, not more. Widening
that bound would recreate the exact incentive `evidence-reduction.md` §9
says progressive disclosure exists to remove: sending everything "just in
case" because asking twice is free.

Absence is never zero
------------------------
Three, not two, outcomes, matching this project's closed-states discipline
(`checks.STATUSES`): `EXPANSION_OK`, `EXPANSION_UNREACHABLE` (this key was
never disclosed at tier 1/2 for this window -- filtered, narrowed, or never
collected at all -- so tier 3 refuses to manufacture new evidence for it),
and `EXPANSION_NOT_FOUND_IN_SOURCE` (tier 1/2 *did* disclose this mnemonic,
but no matching line was found in the raw text handed to this call -- a
mismatch between the window and the raw source, which is a fact about the
call, not about the device). A window with genuinely zero neighbouring
records within the bound is `EXPANSION_OK` with empty neighbour lists and
`raw_occurrences_found` still reported honestly -- "no neighbours within the
bound" and "this was never collected" must never render the same, and here
they are three different, named states rather than one falsy list standing
in for all of them.

Pure, no I/O -- the same discipline `log_window.py` states for itself
--------------------------------------------------------------------------
This module touches no device, no evidence store, and no filesystem. It
takes the raw text and the already-computed tier-1 window as plain
arguments and returns a plain `dict`. That keeps it testable without a lab
and keeps `evidence_store.py` (owned by another lane) out of this module's
dependency graph entirely, exactly as `log_window.py`'s own docstring states
for itself: "Pure functions over already-parsed records. No I/O, no device
access, no model."

What is deliberately NOT here
---------------------------------
No MCP tool is registered and no CLI subcommand is added -- both `cli.py`
and `mcp_server/server.py` are held by other lanes while this task runs, and
the MCP manifest's size is itself an experiment variable another lane is
measuring (B-501/B-479); adding a tool here would silently change the
quantity being measured. Wiring this in later is one call site: a tool
handler resolves the raw log command text and the `ShapedWindow` it already
has in hand from the current investigation, calls `disclose_log_window`
once per window, and calls `expand_evidence` once per model-requested key --
then registers that handler the same way every other MCP tool is
registered, through `mcp_server/boundary.py`'s decorator. (That decorator
would not double-quote anything this module already quotes: its
`_FREE_TEXT_FIELD_NAMES` table has no entry named `"line"`, so a payload
this module already returns passes through unchanged.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .descent import DescentResult
from .log_window import MnemonicAggregate, ShapedWindow, aggregate_by_mnemonic
from .model_egress import quote_device_text
from .storage_key import is_valid_storage_key

__all__ = [
    "DEFAULT_NEIGHBOR_LIMIT",
    "EXPANSION_NOT_FOUND_IN_SOURCE",
    "EXPANSION_OK",
    "EXPANSION_STATUSES",
    "EXPANSION_UNREACHABLE",
    "LOG_EVIDENCE_KEY_PREFIX",
    "MAX_NEIGHBOR_LIMIT",
    "DisclosedLogWindow",
    "build_log_evidence_key",
    "disclose_log_window",
    "expand_evidence",
    "parse_log_evidence_key",
]

# --------------------------------------------------------------------------- #
# The log evidence key -- a namespace of its own
# --------------------------------------------------------------------------- #
#
# `checks.evidence_key(device, intent_or_template, subject=None)` builds
# unprefixed keys like "PE2:interface:Gi0/0/0/0" for point-in-time,
# structured evidence (a BGP neighbor table, an interface counter) that has
# no "raw record" to drill into at all -- there is only ever one snapshot.
# Log evidence is different: it is a SEQUENCE of discrete raw lines, which is
# exactly what makes "first occurrence, last occurrence, neighbours" a
# meaningful question. The "logs:" prefix keeps the two namespaces visually
# and structurally distinct, so `parse_log_evidence_key` can refuse a
# checks.py-shaped key outright rather than silently misreading its second
# segment as a mnemonic.

LOG_EVIDENCE_KEY_PREFIX = "logs"


def build_log_evidence_key(device: str, mnemonic: str) -> str:
    """The one way to build a log evidence key: `logs:<device>:<mnemonic>`.

    Both components are validated through `storage_key.is_valid_storage_key`
    -- the same charset policy `evidence_store.py`/`fixtures.py`/
    `session_memory.py` already use for a single path/key component. A
    mnemonic never contains a colon (`template_parsers._LOG_ENTRY`'s own
    charset is `[A-Za-z0-9_-]+`), so splitting a built key on `:` is always
    unambiguous.
    """

    if not is_valid_storage_key(device):
        raise ValueError(f"not a valid device name for a log evidence key: {device!r}")
    if not is_valid_storage_key(mnemonic):
        raise ValueError(f"not a valid mnemonic for a log evidence key: {mnemonic!r}")
    return f"{LOG_EVIDENCE_KEY_PREFIX}:{device}:{mnemonic}"


def parse_log_evidence_key(evidence_key: str) -> tuple[str, str]:
    """`logs:<device>:<mnemonic>` -> `(device, mnemonic)`, refusing anything else.

    **One key, never a list.** `evidence_key` must be a `str` by the type
    signature every caller of this module is expected to honour; this
    function additionally raises `TypeError` explicitly for a list/tuple/set
    rather than letting `.split` fail with a confusing `AttributeError`,
    because "expand exactly one key per call" is a requirement worth naming
    at the one point every call funnels through, not an accident of Python's
    own error messages.

    A glob or wildcard-shaped mnemonic (`*`, `?`, `[...]`) is refused by the
    SAME `storage_key` charset check `build_log_evidence_key` uses to build
    one -- "never a glob" falls out of reusing that policy rather than
    needing its own detector.
    """

    if not isinstance(evidence_key, str):
        raise TypeError(
            "an evidence key is one string, never a list, tuple or set -- "
            "expand_evidence expands exactly one key per call; got "
            f"{type(evidence_key).__name__}"
        )

    parts = evidence_key.split(":")
    if len(parts) != 3 or parts[0] != LOG_EVIDENCE_KEY_PREFIX:
        raise ValueError(
            "not a log evidence key (expected 'logs:<device>:<mnemonic>'): "
            f"{evidence_key!r}"
        )
    _, device, mnemonic = parts
    if not is_valid_storage_key(device) or not is_valid_storage_key(mnemonic):
        raise ValueError(f"not a valid log evidence key: {evidence_key!r}")
    return device, mnemonic


# --------------------------------------------------------------------------- #
# Tier 2, disclosed -- the proof tier 3 requires
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DisclosedLogWindow:
    """Proof that tier 2 (representative verbatim samples) was actually
    derived from a real tier-1 window for one device.

    Not constructed by hand in production code -- `disclose_log_window` is
    the only builder, and it derives `aggregates` from `window.records`
    itself rather than accepting them as a second, independently fabricable
    argument. See the module docstring's "Tier 1 -> Tier 2" gate.
    """

    device: str
    window: ShapedWindow
    aggregates: tuple[MnemonicAggregate, ...]

    def aggregate_for(self, mnemonic: str) -> MnemonicAggregate | None:
        """The disclosed tier-2 aggregate for one mnemonic, or `None` if this
        window never disclosed it -- the exact test `expand_evidence` gates
        tier 3 on."""

        return next((agg for agg in self.aggregates if agg.mnemonic == mnemonic), None)


def disclose_log_window(device: str, window: ShapedWindow) -> DisclosedLogWindow:
    """Tier 1 -> Tier 2 for one device's already-shaped log window.

    `log_window.aggregate_by_mnemonic` already computes exactly what
    evidence-reduction.md §8 calls tier 2 ("one verbatim sample per
    template" -- `MnemonicAggregate.sample`, alongside `first_seen`/
    `last_seen`/`count`); this function's job is narrow: run that
    computation once per window and hand back a value that also carries
    *which device* the window is for, so `expand_evidence` can check a
    caller isn't naming one device's evidence key against another device's
    disclosed window.
    """

    return DisclosedLogWindow(
        device=device,
        window=window,
        aggregates=aggregate_by_mnemonic(window.records),
    )


# --------------------------------------------------------------------------- #
# Locating raw lines -- self-contained, not borrowed from template_parsers
# --------------------------------------------------------------------------- #
#
# `template_parsers._LOG_ENTRY` is the authoritative parser and is NOT
# imported here, on purpose: it is a private symbol of a module owned by
# another lane, and `model_egress.py`'s own precedent (RAW_TEXT_KEYS,
# ERROR_KINDS) is to COPY a small, independently-reviewable table across a
# lane boundary rather than reach into another module's internals -- "this
# package must not depend on [that module]" there, "this module must not
# depend on another lane's private API" here, same reasoning. The marker
# this module needs is much narrower than a full parse: template_parsers.py
# itself documents the space-before-colon framing after the mnemonic
# ("%MNEMONIC : text") as "real IOS-XR framing, not a typo to normalise
# away", surveyed across 1,800 fixture entries with zero exceptions -- safe
# to depend on as a stable, documented format without depending on the
# parser's own regex object.
_MNEMONIC_MARKER = re.compile(r"\]: %(?P<mnemonic>[A-Za-z0-9_-]+) : ")


def _raw_lines(raw_log_text: str) -> list[str]:
    """Every non-blank line of one raw `show logging` response, in device
    order, stripped exactly as `template_parsers.parse_xr_logging` strips
    them -- so a position computed here means the same thing a position
    computed there would."""

    return [line.strip() for line in raw_log_text.splitlines() if line.strip()]


def _indices_for_mnemonic(lines: list[str], mnemonic: str) -> list[int]:
    """Every raw line index whose mnemonic marker names ``mnemonic``, in
    ascending (device chronological) order.

    `re.search` finds the LEFTMOST match on each line, which is always the
    genuine structural marker: it sits right after `process[pid]:`, and
    nothing an attacker controls (the free-text message tail) can precede
    it on the same line -- only follow it. A forged `]: %OTHER : ` sequence
    planted inside another record's own device-authored text therefore
    cannot be mistaken for that record's real marker; it would have to sit
    to the marker's right, where `re.search`'s leftmost-match rule never
    looks for a second one on the same line.
    """

    indices = []
    for index, line in enumerate(lines):
        match = _MNEMONIC_MARKER.search(line)
        if match is not None and match["mnemonic"] == mnemonic:
            indices.append(index)
    return indices


def _neighbor_indices(total: int, anchor: int, limit: int) -> tuple[list[int], list[int]]:
    """Up to ``limit`` line indices on each side of ``anchor``, clipped to
    ``[0, total)``.

    Clipped, never padded: an anchor near the start or end of the buffer
    legitimately has fewer than ``limit`` neighbours on that side, and the
    caller reports exactly how many were found rather than a bound presented
    as a count.
    """

    before = list(range(max(0, anchor - limit), anchor))
    after = list(range(anchor + 1, min(total, anchor + limit + 1)))
    return before, after


def _occurrence(lines: list[str], index: int) -> dict[str, Any]:
    """One raw record: its position in the buffer and its literal text.

    ``line`` is UNQUOTED here -- the whole point of building the answer this
    way is that no raw text is quoted until `_quote_raw_fields` walks the
    fully assembled payload in one pass, right before `expand_evidence`
    returns it. See the module docstring's "Applied once, structurally"
    section.
    """

    return {"position": index, "line": lines[index]}


def _occurrences(lines: list[str], indices: list[int]) -> list[dict[str, Any]]:
    return [_occurrence(lines, index) for index in indices]


# --------------------------------------------------------------------------- #
# The one quoting boundary
# --------------------------------------------------------------------------- #
#
# A declared table of field NAMES that carry raw device text, exactly the
# shape `mcp_server.boundary._FREE_TEXT_FIELD_NAMES` and `ticket_read.
# _UNTRUSTED_TEXT_FIELDS` already use -- a set, not a single hardcoded name,
# so a second raw-text field added here later has an obvious place to be
# declared rather than a second ad hoc `if key == "line"` appearing at a
# different call site.
_RAW_TEXT_FIELDS: frozenset[str] = frozenset({"line"})


def _quote_raw_fields(payload: Any) -> Any:
    """Recursively wrap every value reached under a `_RAW_TEXT_FIELDS` key,
    walking dicts and lists to any depth -- the same recursive shape
    `mcp_server.boundary.sanitize` and `ticket_read._quote_untrusted_fields`
    already use. Called exactly ONCE, over the whole assembled result, by
    `expand_evidence` -- not field by field while the payload is being
    built -- so no call site can forget a subfield. See
    `TICKET-READ-CONTAINMENT` in `scripts/mutate_guards.py` for the
    precedent this module's own `EXPAND-EVIDENCE-CONTAINMENT` entry follows.
    """

    if isinstance(payload, dict):
        return {
            key: (
                quote_device_text(value)
                if key in _RAW_TEXT_FIELDS and isinstance(value, str)
                else _quote_raw_fields(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_quote_raw_fields(item) for item in payload]
    return payload


# --------------------------------------------------------------------------- #
# expand_evidence -- tier 2 -> tier 3
# --------------------------------------------------------------------------- #

#: Lines kept on EACH side of ONE anchor (first occurrence, last occurrence).
#: See the module docstring's "Bounded, and one key at a time" section for
#: the measured character-budget derivation. `MAX_NEIGHBOR_LIMIT` is the
#: refused ceiling; `DEFAULT_NEIGHBOR_LIMIT` is deliberately equal to it --
#: there is exactly one declared bound, not a default a caller is expected
#: to routinely override upward.
MAX_NEIGHBOR_LIMIT = 5
DEFAULT_NEIGHBOR_LIMIT = MAX_NEIGHBOR_LIMIT

#: Three closed outcomes, matching `checks.STATUSES`'s own discipline: no
#: bare `True`/`False`, no `None` standing in for "didn't check". See the
#: module docstring's "Absence is never zero" section for what each one
#: means and why they must never collapse into one shape.
EXPANSION_OK = "ok"
EXPANSION_UNREACHABLE = "unreachable"
EXPANSION_NOT_FOUND_IN_SOURCE = "not_found_in_source"
EXPANSION_STATUSES: frozenset[str] = frozenset(
    {EXPANSION_OK, EXPANSION_UNREACHABLE, EXPANSION_NOT_FOUND_IN_SOURCE}
)


def expand_evidence(
    evidence_key: str,
    *,
    result: DescentResult,
    disclosed: DisclosedLogWindow,
    raw_log_text: str,
    storage_pointer: str | None = None,
    neighbor_limit: int = DEFAULT_NEIGHBOR_LIMIT,
) -> dict[str, Any]:
    """Tier 3: a bounded, contained drill-down into one already-disclosed
    log evidence key.

    Parameters
    ----------
    evidence_key
        `logs:<device>:<mnemonic>` (`build_log_evidence_key`). One key, one
        call -- see `parse_log_evidence_key`.
    result
        The `DescentResult` (tier 0) this expansion is in service of. Used
        for traceability (the finding this drill-down was requested for)
        and, together with `disclosed`, for the reachability gate: the
        key's device must be either the descent's own device or one of the
        devices its own rungs actually evaluated (`RungOutcome.device`) --
        cross-device correlation is legitimate (a finding on one device is
        routinely explained by a neighbour's logs, exactly as
        `prompt_library.build_correlate_prompt`'s own tests exercise), an
        UNRELATED device is not.
    disclosed
        Tier 2, from `disclose_log_window`. `expand_evidence` cannot be
        called without one -- see the module docstring's two-gate section.
    raw_log_text
        The raw `show logging` command output this window's records were
        parsed from (an envelope's `data.commands["show logging last
        <n>"]`, or the fixture-shaped equivalent in tests). Never persisted,
        never returned unquoted.
    storage_pointer
        Wherever the fuller evidence collection this window came from is
        stored (e.g. `evidence_store.save_snapshot`'s own return value), or
        `None` if it was never persisted. Passed through, not resolved --
        this module does no storage I/O (see the module docstring's "Pure,
        no I/O" section). Reported honestly either way: `None` renders as
        `storage_pointer_available: false`, never as an omitted key that
        could be misread as "forgot to check".
    neighbor_limit
        Lines kept on each side of one anchor. `1..MAX_NEIGHBOR_LIMIT`;
        anything else is refused (`ValueError`), never silently clamped.

    Returns a plain `dict`, JSON-shaped, with every raw-text field already
    quoted through `model_egress.quote_device_text` -- see the module
    docstring's "Applied once, structurally" section.
    """

    if not (0 < neighbor_limit <= MAX_NEIGHBOR_LIMIT):
        raise ValueError(
            f"neighbor_limit must be between 1 and {MAX_NEIGHBOR_LIMIT}, "
            f"got {neighbor_limit!r}"
        )

    device, mnemonic = parse_log_evidence_key(evidence_key)

    base: dict[str, Any] = {
        "evidence_key": evidence_key,
        "device": device,
        "mnemonic": mnemonic,
        "finding": result.finding,
        "neighbor_limit": neighbor_limit,
        "storage_pointer": storage_pointer,
        "storage_pointer_available": storage_pointer is not None,
    }

    # Gate: the key's device must be part of what tier 0 actually
    # investigated -- not merely "some device that happens to have a
    # window disclosed somewhere". See the module docstring's "Tier 2 ->
    # Tier 3" section.
    touched_devices = {result.device, *(outcome.device for outcome in result.outcomes)}
    if device not in touched_devices:
        payload = {
            **base,
            "status": EXPANSION_UNREACHABLE,
            "reason": (
                f"device {device!r} was never part of this descent (tier 0) "
                f"-- the investigated devices were {sorted(touched_devices)!r}"
            ),
        }
    elif device != disclosed.device:
        payload = {
            **base,
            "status": EXPANSION_UNREACHABLE,
            "reason": (
                f"the disclosed tier-1/2 window is for device "
                f"{disclosed.device!r}, not {device!r} -- an evidence key "
                "cannot be expanded against a different device's window"
            ),
        }
    else:
        agg = disclosed.aggregate_for(mnemonic)
        if agg is None:
            payload = {
                **base,
                "status": EXPANSION_UNREACHABLE,
                "reason": (
                    "this evidence key was never disclosed at tier 1/2 for "
                    "this window -- filtered as noise, deduplicated, "
                    "narrowed by subject, or never collected at all. "
                    "expand_evidence only drills into evidence already "
                    "shown; it does not go looking for new evidence"
                ),
            }
        else:
            lines = _raw_lines(raw_log_text)
            raw_indices = _indices_for_mnemonic(lines, mnemonic)

            if not raw_indices:
                payload = {
                    **base,
                    "status": EXPANSION_NOT_FOUND_IN_SOURCE,
                    "tier2_count": agg.count,
                    "reason": (
                        "tier 1/2 disclosed this mnemonic, but no matching "
                        "raw line was found in the raw source given to this "
                        "call -- the raw text does not correspond to the "
                        "window that was disclosed"
                    ),
                }
            else:
                first_index = raw_indices[0]
                last_index = raw_indices[-1]
                is_singleton = first_index == last_index

                before_first, after_first = _neighbor_indices(
                    len(lines), first_index, neighbor_limit
                )
                if is_singleton:
                    before_last, after_last = [], []
                else:
                    before_last, after_last = _neighbor_indices(
                        len(lines), last_index, neighbor_limit
                    )

                payload = {
                    **base,
                    "status": EXPANSION_OK,
                    # What tier 2 already claimed, alongside what tier 3
                    # actually found in the raw source -- reported as two
                    # separate numbers, never collapsed into one signed
                    # "difference" field, because a mismatch can run either
                    # direction (the raw source held MORE occurrences than
                    # tier 1's deduped count, e.g. `log_window.dedupe`
                    # removed a true duplicate -- or FEWER, which is the
                    # source/window mismatch case above). Absence is never
                    # zero: a reader who wants to know whether they agree
                    # compares the two numbers themselves.
                    "tier2_count": agg.count,
                    "raw_occurrences_found": len(raw_indices),
                    "first_occurrence": _occurrence(lines, first_index),
                    "last_occurrence": _occurrence(lines, last_index),
                    "first_and_last_are_the_same_occurrence": is_singleton,
                    "neighbors_before_first": _occurrences(lines, before_first),
                    "neighbors_after_first": _occurrences(lines, after_first),
                    "neighbors_before_last": _occurrences(lines, before_last),
                    "neighbors_after_last": _occurrences(lines, after_last),
                    "raw_lines_total": len(lines),
                }

    # The one quoting boundary -- every return path funnels through here,
    # not only the branch that happens to carry raw text today. See the
    # module docstring's "Applied once, structurally" section.
    return _quote_raw_fields(payload)
