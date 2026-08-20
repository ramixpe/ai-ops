"""EER-016: `docs/build/BACKLOG.md` as a computable control plane.

The review found overlapping authoritative/planning tables and internal
inconsistencies: an engineer could not reliably compute what is open,
blocked, satisfied, or release-critical. `BACKLOG.md` itself was restructured
in the same change that added this file (see its own "How this file is
structured" section) so that:

- The **Reconciliation table** (`# Reconciliation — authoritative status`) is
  the single authoritative status per ID -- one row, one recognised state,
  every `DEFERRED`/`BLOCKED` row stating what would change that.
- The four **legacy planning tables** (MVP-1, Stage 2, Stage 3, Cross-cutting)
  are explicitly marked historical, so an ID appearing in both cannot be read
  as carrying two live statuses -- the marker says which one to trust.

This file is what makes that structure a control plane rather than prose: it
parses `BACKLOG.md` itself and fails if the properties above stop holding.
It does not judge whether any individual item's status is *correct* -- that
is a judgement call for whoever reconciles the next item, not something a
docs test can know. It only enforces that the file stays *computable*.

New file, not an extension of `tests/test_docs.py` -- that file is owned by a
different track this wave (see the wave brief), and `BACKLOG.md` is normally
orchestrator-only; this lane owns it specifically because EER-016 is about
its structure.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKLOG_PATH = REPO_ROOT / "docs" / "build" / "BACKLOG.md"

#: The seven-value vocabulary `BACKLOG.md`'s own "State vocabulary" section
#: documents. Hardcoded here (not derived from the doc) so this test is the
#: independent check that the doc and the data agree with each other, not a
#: mirror of whichever one happens to be edited -- the same reasoning
#: `test_docs.py` gives for reading the MCP registry directly rather than a
#: frozen list.
RECOGNIZED_STATES = frozenset({
    "DONE",
    "OPEN",
    "DEFERRED",
    "BLOCKED",
    "OUT-OF-SCOPE",
    "CLOSED-AS-REFUSED",
    "CLOSED-AS-MEASURED",
})

#: One row of the Reconciliation table:
#: ``| **B-123** | `STATE` | evidence... | depends-on... | last-touched |``
#: Matches only rows shaped this way -- the legacy planning tables use a
#: different schema (``ID | Item | Why | Depends on | Size | Decision``, no
#: backtick-quoted state column) and are not matched at all, which is the
#: point: they are historical, not a second source of status.
_ROW_RE = re.compile(r"^\| \*\*([A-Za-z0-9-]+)\*\* \| `([A-Z-]+)`")

#: Words/phrases that, in this file's own established idiom, state an
#: unblocking condition -- "until X", "needs X", "blocked on X", "outside
#: this repository" (a circumstance), etc. A heuristic, not a semantic
#: parser: every DEFERRED/BLOCKED row in the file as of this test's writing
#: uses at least one of these, either in prose or by naming a concrete `B-`
#: dependency ID (checked separately below).
_UNBLOCK_TRIGGER_WORDS = (
    "until", "needs", "need ", "blocked", "waiting", "unblocks when",
    "cannot", "outside this repository", "requires",
)


def _backlog_text() -> str:
    return BACKLOG_PATH.read_text(encoding="utf-8")


def _reconciliation_rows() -> list[tuple[str, str, str, int]]:
    """``(id, state, full_line, line_number)`` for every Reconciliation-table row."""

    rows = []
    for line_no, line in enumerate(_backlog_text().splitlines(), start=1):
        m = _ROW_RE.match(line)
        if m:
            rows.append((m.group(1), m.group(2), line, line_no))
    return rows


def test_backlog_file_exists():
    assert BACKLOG_PATH.is_file()


def test_reconciliation_table_is_actually_present_and_large():
    """Positive control for every test below: if the row regex ever stops
    matching (a heading reworded, the table's own shape changed), every
    other test in this file would pass vacuously over zero rows. Pin a
    floor well below the current count (153) so this fails loudly first."""

    rows = _reconciliation_rows()
    assert len(rows) > 100, (
        f"only found {len(rows)} Reconciliation-table rows -- the row "
        "parser may have stopped matching the table's real shape"
    )


def test_state_vocabulary_section_documents_every_recognized_state():
    text = _backlog_text()
    start = text.index("## State vocabulary")
    end = text.index("## How this file is structured", start)
    section = text[start:end]
    missing = [state for state in RECOGNIZED_STATES if f"`{state}`" not in section]
    assert missing == [], f"states used by the table but not documented: {missing}"


def test_every_recognized_state_is_actually_used_at_least_once():
    """The reverse direction of the check above -- a state documented but
    never used would be aspirational vocabulary, not a description of the
    table. Also the positive control for
    `test_every_reconciliation_row_has_a_recognized_status`: proves the
    recognised-state set is not simply empty or disjoint from the data."""

    used = {state for _, state, _, _ in _reconciliation_rows()}
    unused = RECOGNIZED_STATES - used
    assert unused == set(), f"documented states with zero rows using them: {unused}"


def test_every_reconciliation_row_has_a_recognized_status():
    rows = _reconciliation_rows()
    unrecognized = sorted({(id_, state) for id_, state, _, _ in rows if state not in RECOGNIZED_STATES})
    assert unrecognized == [], f"unrecognized status token(s): {unrecognized}"


def test_no_id_carries_two_different_explicit_statuses():
    """The exact defect the review found (B-103, B-446 as examples): an ID
    reads differently depending on which table a reader lands on first.
    Checked within the Reconciliation table itself -- the one place this
    file now asserts a single row per ID -- rather than against the legacy
    planning tables, which by design no longer carry a competing status at
    all (see `test_every_legacy_planning_section_is_marked_historical`)."""

    by_id: dict[str, set[str]] = defaultdict(set)
    for id_, state, _, _ in _reconciliation_rows():
        by_id[id_].add(state)
    conflicting = {id_: sorted(states) for id_, states in by_id.items() if len(states) > 1}
    assert conflicting == {}, f"IDs with more than one status in the Reconciliation table: {conflicting}"


def test_every_deferred_or_blocked_row_states_its_unblocking_condition():
    """`DEFERRED`/`BLOCKED` rows must carry the thing that makes them
    different from `OUT-OF-SCOPE`/`CLOSED-AS-REFUSED`: a stated condition
    that would change the state, per the vocabulary section's own
    definition ("carrying its unblocking condition").

    Deliberately not a strict per-column parse: at least one row's Evidence
    cell contains a literal ``|`` (B-485: `` `nettools ledger summary|verdict` ``),
    which would misalign a naive split on ``|`` for that row's later
    columns. The check instead looks for a trigger phrase or a named `B-`
    dependency ID anywhere in the row -- a heuristic, stated as one, not a
    schema validator.
    """

    failures = []
    for id_, state, line, line_no in _reconciliation_rows():
        if state not in ("DEFERRED", "BLOCKED"):
            continue
        rest = line.lower()
        has_trigger = any(word in rest for word in _UNBLOCK_TRIGGER_WORDS)
        names_a_dependency = bool(re.search(r"\bb-\d+\b", line, re.IGNORECASE))
        if not (has_trigger or names_a_dependency):
            failures.append((id_, line_no))
    assert failures == [], (
        f"DEFERRED/BLOCKED rows with no stated unblocking condition found: {failures}"
    )


def test_deferred_or_blocked_rows_actually_exist():
    """Positive control for the test above: without this, a regex or state
    name typo that made zero rows match `DEFERRED`/`BLOCKED` would let the
    unblocking-condition test pass over nothing and look green for the
    wrong reason."""

    count = sum(1 for _, state, _, _ in _reconciliation_rows() if state in ("DEFERRED", "BLOCKED"))
    assert count > 10, f"expected a real population of DEFERRED/BLOCKED rows, found {count}"


@pytest.mark.parametrize(
    "heading",
    ["# MVP-1", "# Stage 2", "# Stage 3", "# Cross-cutting"],
)
def test_every_legacy_planning_section_is_marked_historical(heading):
    """Each legacy PLAN-V2 table must carry the marker pointing a reader
    back at the Reconciliation table -- otherwise an ID duplicated across
    both tables is exactly as ambiguous as it was before EER-016."""

    text = _backlog_text()
    assert heading in text, f"expected heading {heading!r} not found in BACKLOG.md"
    start = text.index(heading)
    next_table_header = text.index("| ID | Item | Why | Depends on | Size | Decision |", start)
    window = text[start:next_table_header]
    assert "Historical planning table" in window, (
        f"{heading} section's table is missing the historical-table marker "
        "before its header row"
    )


def test_reconciliation_table_is_positioned_before_the_legacy_tables():
    """`BACKLOG.md`'s own "How this file is structured" section promises the
    authoritative table comes first, historical tables after -- pinned so a
    future edit cannot silently reintroduce the pre-EER-016 ordering where
    the Reconciliation table sat interleaved *inside* the MVP-1 table."""

    text = _backlog_text()
    recon_pos = text.index("# Reconciliation — authoritative status")
    for heading in ("# MVP-1", "# Stage 2", "# Stage 3", "# Cross-cutting"):
        assert text.index(heading) > recon_pos, f"{heading} appears before the Reconciliation table"
