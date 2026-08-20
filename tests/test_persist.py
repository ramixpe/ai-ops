"""EER-015: `_persist.py` collapsed four byte-identical copies each of
`_timestamp_now` and `_secure_mkdir`/`_SECURE_DIR_MODE` (plus three of
`_SECURE_FILE_MODE` and two of `_require_nonempty_str`) into one module.

Unlike the `_snapshot_dir` delegation this extraction follows the pattern of
(EER-015 step 2, `network_tools`/`evidence_store`), there was no hidden knob
here to rename and check for a follower that failed to keep up -- all four
copies were already byte-identical, with no config value or env key involved
that could silently diverge. So the property worth pinning is not "the same
behaviour under a renamed constant" (there is no constant); it is the
structural claim the extraction actually makes: **no module re-declares its
own copy any more.** A test that only checked behaviour (e.g. "the output
looks like an ISO-8601 timestamp") would keep passing even if a future edit
reintroduced a local `_timestamp_now` in one file that drifted from the
others -- exactly the failure this extraction removed. Identity is what
proves that cannot happen silently: if any importer stopped delegating,
`is` would go false immediately, in every test that touches that module,
not just here.
"""

from __future__ import annotations

from agent_nettools import _persist, evidence_store, ledger, session_memory, ticket


def test_timestamp_now_is_one_shared_implementation_not_four():
    assert evidence_store._timestamp_now is _persist._timestamp_now
    assert ledger._timestamp_now is _persist._timestamp_now
    assert session_memory._timestamp_now is _persist._timestamp_now
    assert ticket._timestamp_now is _persist._timestamp_now


def test_secure_mkdir_and_its_mode_are_one_shared_implementation():
    assert evidence_store._secure_mkdir is _persist._secure_mkdir
    assert ledger._secure_mkdir is _persist._secure_mkdir
    assert session_memory._secure_mkdir is _persist._secure_mkdir
    assert ticket._secure_mkdir is _persist._secure_mkdir

    # `_SECURE_DIR_MODE` itself is no longer re-exported by any of the four --
    # only `_persist._secure_mkdir` reads it now, so importing it into
    # evidence_store/ledger/ticket's own namespace would be dead weight (ruff
    # F401 catches exactly that if it is ever added back without a local
    # user). `_SECURE_FILE_MODE` stays imported directly in the three modules
    # that chmod a *file* themselves (session_memory.py never does).
    assert _persist._SECURE_DIR_MODE == 0o700
    assert evidence_store._SECURE_FILE_MODE is _persist._SECURE_FILE_MODE
    assert ledger._SECURE_FILE_MODE is _persist._SECURE_FILE_MODE
    assert ticket._SECURE_FILE_MODE is _persist._SECURE_FILE_MODE


def test_require_nonempty_str_is_one_shared_implementation():
    # Only ledger.py and ticket.py ever declared this one; evidence_store.py
    # and session_memory.py validate their own inputs by other means and
    # never had a copy to collapse.
    assert ledger._require_nonempty_str is _persist._require_nonempty_str
    assert ticket._require_nonempty_str is _persist._require_nonempty_str


def test_require_nonempty_str_still_refuses_blank_and_non_string_input():
    """Positive control for the identity test above: an import that merely
    aliased the *name* to something that behaves differently (say, a
    permissive stub) would still satisfy `is` if it were the literal object
    in scope, but this pins that the shared function itself still does its
    one job -- refuse a blank or non-string value -- so a future edit to
    `_persist._require_nonempty_str` that quietly loosened the check would be
    caught here, not just at whichever call site happened to exercise it."""

    import pytest

    with pytest.raises(ValueError, match="must be a non-empty string"):
        _persist._require_nonempty_str("field", "   ")
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _persist._require_nonempty_str("field", 5)
    assert _persist._require_nonempty_str("field", "ok") == "ok"
