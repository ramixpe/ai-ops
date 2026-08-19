"""Regression guard for the `_in_scope` scoping bug (2026-08-19).

`scripts/mutate_guards.py` purges and stale-checks bytecode under `REPO`,
which is wherever the script itself lives. For every agent that ever runs
it, that is somewhere under `.claude/worktrees/<name>/` -- there is no other
way an agent in this environment invokes it. `_in_scope` used to match
`_UNSCANNED` against each candidate's *absolute* path, so `REPO`'s own
ancestry (which already contains the substring `.claude/worktrees`)
excluded every path the scan ever found -- `REPO.rglob(...)` only yields
descendants of `REPO`, so every result inherits that ancestry. Measured
2026-08-19: 0 of 470 `__pycache__` directories were considered in scope in a
real worktree run. That silently turned both `purge_pycache` and
`assert_no_stale_bytecode` into no-ops on every run that matters, which is
defect 2 from this script's own module docstring, reintroduced by worktree
deployment.

The consequence was not cosmetic: a fast guard's mutate -> subprocess-compile
-> restore cycle (roughly a quarter of a second, wall clock) can land inside
one filesystem mtime tick, so Python's own staleness check sometimes fails to
notice the restored source no longer matches the cached bytecode. With the
purge disabled, that mutated `.pyc` was left sitting in
`src/agent_nettools/__pycache__/`, silently picked up by the *next*,
completely unrelated `pytest` run. Traced this way to three tests reported
flaky on an otherwise clean, unmodified tree:
`test_flows.py::test_each_member_set_carries_its_own_aggregation` (poisoned
by B-456's own mutation surviving in `descent.py`'s cache, ~50% of the time
immediately following a `mutate_guards.py B-456` run) and
`test_epoch.py::test_latency_ms_deduplicates_observations_sharing_one_window`
/ `test_skew_seconds_is_unaffected_by_commands_run_and_latency_ms` (poisoned
by B-446-LATENCY-DEDUP's mutation surviving in `epoch.py`'s cache, ~43% of
the time). Both rates measured directly: run the guard, then immediately run
the target test file, repeatedly, with no other change.

This is a script, not a package module -- `scripts/` is deliberately not on
`pythonpath`, so it is loaded by path rather than imported normally.
"""

from __future__ import annotations

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mutate_guards.py"
_spec = importlib.util.spec_from_file_location("mutate_guards", _PATH)
assert _spec is not None and _spec.loader is not None
mutate_guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate_guards)


def test_a_pycache_directly_under_repo_is_in_scope():
    """The regression itself. `REPO`'s own `src/agent_nettools/__pycache__`
    must be scanned and purgeable, even though `REPO` lives under
    `.claude/worktrees/<something>/` in every real invocation of this script
    -- that is not a foreign worktree, it is the tree being mutated."""

    p = mutate_guards.REPO / "src" / "agent_nettools" / "__pycache__"
    assert mutate_guards._in_scope(p), (
        "REPO's own bytecode must be in scope, or purge_pycache and "
        "assert_no_stale_bytecode are no-ops for every agent that runs this"
    )


def test_the_venv_is_still_excluded():
    """Unrelated to the regression: `.venv`'s own compiled dependencies were
    never meant to be scanned, and the fix must not widen scope past what
    the module docstring documents."""

    p = mutate_guards.REPO / ".venv" / "lib" / "site-packages" / "__pycache__"
    assert not mutate_guards._in_scope(p)


def test_a_nested_sibling_worktree_is_still_excluded():
    """The case `_UNSCANNED` exists for, and the fix must keep working: run
    from a *main* checkout, `REPO.rglob` descends into
    `.claude/worktrees/<other-agent>/...`, and that tree must be skipped --
    an agent may be mid-run there, and purging under it is a race this
    harness would create for no benefit. The regression fix must not defeat
    this exclusion; it must only stop matching `REPO`'s *own* ancestry."""

    p = mutate_guards.REPO / ".claude" / "worktrees" / "some-other-agent" / "src" / "__pycache__"
    assert not mutate_guards._in_scope(p)
