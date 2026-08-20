"""B-407 -- session memory: a pointer from one interactive sitting to its
most recent turn. See `session_memory.py`'s module docstring for the full
design rationale (why this is not B-203/B-204/B-205, why it stores no
finding text, and why `FOUND`/`NOT_FOUND`/`CANNOT_RECALL` are three distinct
outcomes rather than two).

Structured like `test_config_diff.py` for the outcome-validation tests (the
`CANNOT_RECALL`-requires-`reason` guard mirrors `FieldDiff`'s
`CANNOT_COMPARE`-requires-`reason` guard) and like `test_ledger.py`/
`test_ticket.py` for the persistence tests (`tmp_path`, explicit `store=`,
never the process-wide default so tests cannot see each other's state).
"""

from __future__ import annotations

import json

import pytest

from agent_nettools import session_memory as sm


def _store(tmp_path):
    return sm.FileSessionMemoryStore(base_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# Recording a turn
# --------------------------------------------------------------------------- #


def test_record_turn_returns_a_persisted_result(tmp_path):
    store = _store(tmp_path)

    result = store.record_turn("sess-1", device="PE2", subject="10.255.0.31", flow="bgp_session")

    assert result.session_id == "sess-1"
    assert result.recorded_at
    assert result.persisted is True
    assert result.warning is None


def test_record_turn_with_no_identity_fields_is_refused():
    """A turn with device/subject/flow/run_id/ticket_path all None carries
    nothing worth recording -- validated eagerly, like `ledger.record_diagnosis`
    validates its required fields, not degraded into a useless write."""

    store = sm.FileSessionMemoryStore(base_dir="/nonexistent-should-not-be-touched")

    with pytest.raises(ValueError, match="at least one"):
        store.record_turn("sess-1")


def test_record_turn_persists_only_identity_and_pointer_fields(tmp_path):
    """The written record never carries a finding/cause/answer field -- only
    the pointer fields this module's docstring names. Guards against a future
    edit quietly turning this into a second copy of the ticket's content."""

    store = _store(tmp_path)
    store.record_turn(
        "sess-1", device="PE2", subject="10.255.0.31", flow="bgp_session",
        run_id="abc123", ticket_path="/tickets/foo.md",
    )

    raw = json.loads((tmp_path / "sess-1.json").read_text(encoding="utf-8"))
    assert set(raw) == {"session_id", "recorded_at", "device", "subject", "flow", "run_id", "ticket_path"}


# --------------------------------------------------------------------------- #
# Recall: FOUND / NOT_FOUND / CANNOT_RECALL -- the three-outcome guard
# --------------------------------------------------------------------------- #


def test_recall_after_record_returns_found_with_the_same_fields(tmp_path):
    store = _store(tmp_path)
    store.record_turn("sess-1", device="PE2", subject="10.255.0.31", flow="bgp_session", run_id="r1")

    result = store.recall("sess-1")

    assert result.outcome == sm.FOUND
    assert result.reason is None
    assert result.turn.device == "PE2"
    assert result.turn.subject == "10.255.0.31"
    assert result.turn.flow == "bgp_session"
    assert result.turn.run_id == "r1"


def test_recall_of_a_session_never_recorded_is_not_found_not_an_error(tmp_path):
    store = _store(tmp_path)

    result = store.recall("never-seen")

    assert result.outcome == sm.NOT_FOUND
    assert result.turn is None
    # NOT_FOUND is a real, checked answer -- it must not silently carry a
    # reason as if something had gone wrong; only CANNOT_RECALL does that.
    assert result.reason is None


def test_recall_of_a_corrupt_session_file_is_cannot_recall_not_not_found(tmp_path, capsys):
    """This is the OBS-188/OBS-202 guard applied to session lookup: a store
    that could not be read must never present as 'no prior turn' -- that
    would make turn 2 silently forget a session was ever underway,
    indistinguishable from it genuinely being new."""

    store = _store(tmp_path)
    (tmp_path / "sess-1.json").write_text("{not valid json", encoding="utf-8")

    result = store.recall("sess-1")

    assert result.outcome == sm.CANNOT_RECALL
    assert result.outcome != sm.NOT_FOUND
    assert result.turn is None
    assert result.reason  # a cannot_recall result always carries an explanation
    assert "WARNING" in capsys.readouterr().err


def test_recall_of_a_structurally_wrong_session_file_is_cannot_recall(tmp_path):
    """Valid JSON, wrong shape (e.g. an unexpected key) -- still CANNOT_RECALL,
    not a crash and not NOT_FOUND."""

    store = _store(tmp_path)
    (tmp_path / "sess-1.json").write_text(json.dumps({"not_a_real_field": True}), encoding="utf-8")

    result = store.recall("sess-1")

    assert result.outcome == sm.CANNOT_RECALL
    assert result.reason


# --------------------------------------------------------------------------- #
# RecallResult.__post_init__ -- the guard itself, and its consistency checks
# --------------------------------------------------------------------------- #


def test_a_cannot_recall_result_with_no_reason_is_rejected():
    """The mutation-tested guard (B-540's shape, one level up) -- see
    scripts/mutate_guards.py's SESSION-MEMORY-CANNOT-RECALL entry."""

    with pytest.raises(ValueError, match="must carry a reason"):
        sm.RecallResult(outcome=sm.CANNOT_RECALL, session_id="sess-1")


def test_a_cannot_recall_result_with_a_reason_is_accepted():
    """Positive control for the guard above (OBS-181): a legitimate
    cannot_recall construction, with a reason, must succeed."""

    result = sm.RecallResult(outcome=sm.CANNOT_RECALL, session_id="sess-1", reason="disk unreadable")
    assert result.reason == "disk unreadable"


def test_a_found_result_with_no_turn_is_rejected():
    with pytest.raises(ValueError, match="must carry a turn"):
        sm.RecallResult(outcome=sm.FOUND, session_id="sess-1")


def test_a_not_found_result_carrying_a_turn_is_rejected():
    turn = sm.Turn(session_id="sess-1", recorded_at="2026-08-19T00:00:00+00:00", device="PE2")
    with pytest.raises(ValueError, match="must not carry a turn"):
        sm.RecallResult(outcome=sm.NOT_FOUND, session_id="sess-1", turn=turn)


def test_an_unknown_outcome_is_rejected():
    with pytest.raises(ValueError, match="must be one of"):
        sm.RecallResult(outcome="maybe", session_id="sess-1")


# --------------------------------------------------------------------------- #
# session_id validation -- refusal AND a positive control (OBS-181)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "..",
        ".",
        "",
        "sess/1",
        "sess\x00nul",
        "a" * 200,
    ],
)
def test_a_malicious_or_malformed_session_id_is_refused(tmp_path, bad_id):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="invalid session id"):
        store.record_turn(bad_id, device="PE2")
    with pytest.raises(ValueError, match="invalid session id"):
        store.recall(bad_id)


def test_a_legitimate_session_id_is_accepted_and_recalled(tmp_path):
    """Positive control for the refusal test above (OBS-181): a realistic
    session id -- the shape a CLI wiring would actually mint -- must travel
    the identical path and succeed, proving the guard rejects bad input
    rather than everything."""

    store = _store(tmp_path)
    session_id = "cli-20260819T120000-ab12cd34"

    write = store.record_turn(session_id, device="PE1", subject="bgp:10.0.0.1", flow="bgp_session")
    result = store.recall(session_id)

    assert write.persisted is True
    assert result.outcome == sm.FOUND
    assert result.turn.session_id == session_id


def test_a_session_id_that_never_reaches_the_filesystem_cannot_traverse(tmp_path):
    """Even if validation were bypassed somehow, confirm the actual on-disk
    layout stays inside the store's own directory for every accepted id --
    belt-and-suspenders over the regex guard itself."""

    store = _store(tmp_path)
    store.record_turn("cli-safe-id", device="PE1")
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == tmp_path


# --------------------------------------------------------------------------- #
# Overwrite semantics -- one row per session, not an accumulating log
# --------------------------------------------------------------------------- #


def test_a_second_turn_overwrites_the_first(tmp_path):
    store = _store(tmp_path)
    store.record_turn("sess-1", device="PE1", subject="bgp:10.0.0.1", flow="bgp_session")
    store.record_turn("sess-1", device="PE2", subject="Gi0/0/0/1", flow="interface")

    result = store.recall("sess-1")

    assert result.turn.device == "PE2"
    assert result.turn.subject == "Gi0/0/0/1"
    assert result.turn.flow == "interface"


def test_only_one_file_exists_per_session_after_multiple_turns(tmp_path):
    store = _store(tmp_path)
    for i in range(5):
        store.record_turn("sess-1", device=f"PE{i}")

    assert list(tmp_path.glob("sess-1*.json")) == [tmp_path / "sess-1.json"]


# --------------------------------------------------------------------------- #
# Durable-data permissions (EER-019). A turn names a device/subject/flow/
# ticket path for one operator's recent interaction -- owner-only by default,
# the same fixed mode `evidence_store.py`/`ticket.py`/`ledger.py` use.
#
# Both modes below come from `_atomic_write_text`'s mkstemp (files) and
# `_secure_mkdir`'s explicit `os.chmod` (directories) -- see
# `_persist._secure_mkdir`'s docstring (EER-015: the shared implementation
# every module in this family imports) for why that makes these exact
# assertions rather than "no group/other bits", with no umask fixture needed.
# --------------------------------------------------------------------------- #


def test_session_memory_directory_is_0700(tmp_path):
    # A subdirectory that does not exist yet, not `tmp_path` itself -- pytest's
    # own `tmp_path` fixture already creates its directory at 0700, which
    # would make this assertion pass whether or not `_secure_mkdir` ever ran
    # (mkdir's `exist_ok=True` is a no-op on an already-existing directory).
    base_dir = tmp_path / "session-memory"
    store = sm.FileSessionMemoryStore(base_dir=str(base_dir))
    store.record_turn("sess-1", device="PE1")

    assert base_dir.stat().st_mode & 0o777 == 0o700


def test_session_turn_file_is_0600(tmp_path):
    store = _store(tmp_path)
    store.record_turn("sess-1", device="PE1")

    assert (tmp_path / "sess-1.json").stat().st_mode & 0o777 == 0o600


# --------------------------------------------------------------------------- #
# forget()
# --------------------------------------------------------------------------- #


def test_forget_removes_a_recorded_session(tmp_path):
    store = _store(tmp_path)
    store.record_turn("sess-1", device="PE1")

    store.forget("sess-1")

    assert store.recall("sess-1").outcome == sm.NOT_FOUND


def test_forget_a_session_never_recorded_does_not_raise(tmp_path):
    store = _store(tmp_path)
    store.forget("never-seen")  # must not raise


# --------------------------------------------------------------------------- #
# list_sessions() / prune()
# --------------------------------------------------------------------------- #


def test_list_sessions_is_empty_before_anything_is_recorded(tmp_path):
    store = _store(tmp_path)
    assert store.list_sessions() == []


def test_list_sessions_reports_every_recorded_session(tmp_path):
    store = _store(tmp_path)
    store.record_turn("sess-a", device="PE1")
    store.record_turn("sess-b", device="PE2")

    assert store.list_sessions() == ["sess-a", "sess-b"]


def test_prune_removes_only_sessions_older_than_keep_days(tmp_path):
    import os
    import time

    store = _store(tmp_path)
    store.record_turn("old-sess", device="PE1")
    store.record_turn("new-sess", device="PE2")

    old_path = tmp_path / "old-sess.json"
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old_path, (ten_days_ago, ten_days_ago))

    result = store.prune(keep_days=1)

    assert result["removed"] == ["old-sess"]
    assert store.recall("old-sess").outcome == sm.NOT_FOUND
    assert store.recall("new-sess").outcome == sm.FOUND


def test_prune_with_nothing_stale_removes_nothing(tmp_path):
    store = _store(tmp_path)
    store.record_turn("fresh-sess", device="PE1")

    result = store.prune(keep_days=30)

    assert result["removed"] == []
    assert store.recall("fresh-sess").outcome == sm.FOUND


# --------------------------------------------------------------------------- #
# get_store()
# --------------------------------------------------------------------------- #


def test_get_store_with_an_explicit_base_dir_returns_a_file_store_there(tmp_path):
    store = sm.get_store(base_dir=str(tmp_path))

    assert isinstance(store, sm.FileSessionMemoryStore)
    store.record_turn("sess-1", device="PE1")
    assert (tmp_path / "sess-1.json").is_file()


def test_get_store_with_no_base_dir_uses_the_default_directory_name(tmp_path, monkeypatch):
    """No env var exists for this yet (settings.py is out of scope this
    change -- see the module docstring's "Storage" section), so the only
    way to observe the real default is the literal relative directory name
    it resolves to from the current working directory."""

    monkeypatch.chdir(tmp_path)
    store = sm.get_store()

    store.record_turn("sess-1", device="PE1")

    assert (tmp_path / sm.DEFAULT_SESSION_MEMORY_DIR / "sess-1.json").is_file()


# --------------------------------------------------------------------------- #
# Module-level convenience functions -- explicit store=, never the ambient default
# --------------------------------------------------------------------------- #


def test_module_level_functions_accept_an_explicit_store(tmp_path):
    store = _store(tmp_path)

    write = sm.record_turn("sess-1", device="PE1", flow="bgp_session", store=store)
    found = sm.recall("sess-1", store=store)
    assert sm.list_sessions(store=store) == ["sess-1"]

    sm.forget("sess-1", store=store)
    forgotten = sm.recall("sess-1", store=store)

    assert write.persisted is True
    assert found.outcome == sm.FOUND
    assert forgotten.outcome == sm.NOT_FOUND


def test_module_level_prune_accepts_an_explicit_store(tmp_path):
    store = _store(tmp_path)
    sm.record_turn("sess-1", device="PE1", store=store)

    result = sm.prune(keep_days=30, store=store)

    assert result == {"removed": []}
