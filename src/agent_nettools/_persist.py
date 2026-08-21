"""EER-015: the handful of persistence primitives that had drifted into four
byte-identical copies -- one each in `evidence_store.py`, `ledger.py`,
`session_memory.py` and `ticket.py` -- collapsed into one leaf module all
four now import from.

What was actually duplicated, counted by hand before this module existed
--------------------------------------------------------------------------
``grep -rn`` across `src/`, `mcp_server/` and `tests/` before this change
found:

- ``_timestamp_now`` -- four definitions (`evidence_store.py`, `ledger.py`,
  `session_memory.py`, `ticket.py`), every one the identical one-liner
  ``datetime.now(timezone.utc).isoformat()``. No `_now_iso` or `_utc_now`
  spelling exists anywhere in this tree -- if an earlier count named those as
  aliases, that count was wrong; there is one name, not three, and this
  module is now its one definition.
- ``_secure_mkdir`` -- four definitions, same four files, identical bodies.
- ``_SECURE_DIR_MODE`` (``0o700``) -- four definitions, same four files.
- ``_SECURE_FILE_MODE`` (``0o600``) -- three definitions (`evidence_store.py`,
  `ledger.py`, `ticket.py`; `session_memory.py` never chmods a *file*, only
  the directory, so it never declared this one).
- ``_require_nonempty_str`` -- two definitions (`ledger.py`, `ticket.py`;
  `evidence_store.py` and `session_memory.py` validate their inputs by other
  means and never declared this one).

Seventeen definitions under five names, collapsed to five. Checked first that
this was safe the same way EER-015's `_snapshot_dir` delegation was: zero
sites in `tests/` monkeypatch, `mock.patch`, or otherwise substitute any of
these five names, and nothing patches `datetime` on any of the four modules
to freeze time indirectly either -- so nothing depends on these staying four
separate objects.

Why this is not the coupling `session_memory.py` and `ticket.py` warn against
-------------------------------------------------------------------------------
Both of those modules' docstrings are explicit that they deliberately do
**not** import each other, or `evidence_store.py`/`ledger.py`, by name:
"these files are plausibly being changed by separate tracks in the same
session, and a hard import dependency between two modules that are each
somebody else's is a self-inflicted breakage risk" (`session_memory.py`'s
"Storage" section); `epoch.py`'s `_wall_clock_now` restates the same policy
for this exact helper -- "a private helper this small is cheaper to repeat
than to couple two modules that do not otherwise depend on each other."

That reasoning is about depending on a *sibling's own evolving logic* --
importing `ledger.py` from `ticket.py` would mean a change to how the ledger
resolves its file format could ripple into the ticket recorder without
either track intending it. This module is not that: it has no logic of its
own to evolve, no state, no I/O beyond `_secure_mkdir`'s one `os.chmod`, and
nothing here has changed since `evidence_store.py`'s original copy was
written. A four-line leaf that every importer already re-verifies against
its own tests (permission-bit assertions in `test_persistence_hardening.py`,
`test_ledger.py`, `test_session_memory.py`, `test_ticket.py`) is a different
trade than a hard dependency on a file three other people might be mid-edit
on. `epoch.py`'s own `_wall_clock_now` is left as its own fifth copy here,
deliberately: it was not in this extraction's scope, and the policy
statement above is still the right call for a module outside the four this
pass touched.

One helper this deliberately does not include
-------------------------------------------------
`_atomic_write_text` duplicates across the same four files by the same
shape and is named in `session_memory.py`'s docstring in the same breath as
the helpers above ("`_atomic_write_text`, id/timestamp helpers ... re-declared
locally rather than imported"). It stayed out of this pass on scope grounds
only, not because it fails the same safety check -- a future EER-015 step
can fold it in as a straightforward continuation of this one.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# EER-019: every caller of `_secure_mkdir`/`_SECURE_DIR_MODE`/
# `_SECURE_FILE_MODE` stores data that should not be readable by other
# accounts on a shared host -- device evidence, diagnosis reasoning, model
# prompts/responses, session pointers into any of those. Owner-only
# (0700/0600) is the fixed default across all four; an operator who wants to
# share a directory can already `chmod` it after the fact, or use a group-ID
# sticky bit on the parent, without this project adding a new env var
# (`settings.py` is owned elsewhere this wave -- see the EER-019 report for
# why a setting was considered and not added).
_SECURE_DIR_MODE = 0o700
_SECURE_FILE_MODE = 0o600


def _secure_mkdir(directory: Path) -> None:
    """Create ``directory`` (and parents) then force ``_SECURE_DIR_MODE`` on it.

    ``Path.mkdir(mode=...)`` alone is not sufficient: with ``exist_ok=True``
    the ``mode`` argument is silently ignored once the directory already
    exists (the common case -- every write after the first), so a directory
    created under a permissive umask before this fix, or nudged open by hand,
    would stay world-readable forever. The explicit ``os.chmod`` below runs
    on *every* call, not just the directory's first creation, so it
    self-heals on the next write instead of only protecting directories
    created after this change shipped.
    """

    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, _SECURE_DIR_MODE)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_nonempty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value
