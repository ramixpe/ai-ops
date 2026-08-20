"""The diagnosis accuracy ledger (B-485).

Structured the same way `test_metrics.py` is: persistence across separate
instances, in-memory-only default, degrade-safely on a bad path, and one
anti-vacuity test for `summary()` -- built from a corpus that mixes every
outcome, every source, and both trustworthy values, so the counts could not
pass by having only one value ever appear (BUILD-PLAN.md SS0.12's fourth
shape: a parameterised check is not covered by parameters that cannot
disagree; this one is a summary, not a parametrised test, but the same
"assert the corpus actually varies" discipline applies).
"""

from __future__ import annotations

import json

import pytest

from agent_nettools import ledger


def _diagnosis(led, **overrides):
    kwargs = {
        "device": "PE1",
        "subject": "10.255.0.31",
        "flow": "bgp_session",
        "finding": "interface_line_down",
        "trustworthy": True,
        "source": ledger.SOURCE_LIVE,
    }
    kwargs.update(overrides)
    return led.record_diagnosis(**kwargs)


# --------------------------------------------------------------------------- #
# Recording a diagnosis -- the automatic half
# --------------------------------------------------------------------------- #


def test_record_diagnosis_returns_an_id_and_timestamp(tmp_path):
    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    result = _diagnosis(led)

    assert result.id
    assert result.recorded_at
    assert result.persisted is True
    assert result.warning is None


def test_record_diagnosis_never_carries_a_verdict(tmp_path):
    """The written entry has no outcome field at all -- diagnoses() supplies
    'unknown' only when reading back, and never as something record_diagnosis
    itself wrote. The tool cannot mark its own homework if the field it would
    need to mark does not exist on the record it writes."""

    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    _diagnosis(led)

    (raw_entry,) = led.entries()
    assert "outcome" not in raw_entry
    assert "outcome_by" not in raw_entry


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("device", ""),
        ("device", "   "),
        ("subject", ""),
        ("flow", ""),
        ("finding", ""),
        ("source", ""),
    ],
)
def test_record_diagnosis_rejects_empty_required_fields(field_name, bad_value):
    with pytest.raises(ValueError):
        _diagnosis(ledger.DiagnosisLedger(), **{field_name: bad_value})


def test_record_diagnosis_rejects_non_bool_trustworthy():
    with pytest.raises(ValueError):
        _diagnosis(ledger.DiagnosisLedger(), trustworthy="yes")


# --------------------------------------------------------------------------- #
# run_id (B-486/W3a): the investigation-run identifier a diagnosis carries,
# distinct from this ledger entry's own `id`. See the module docstring.
# --------------------------------------------------------------------------- #


def test_run_id_round_trips_through_append_and_read(tmp_path):
    """The core promise of this field: what a caller passes in comes back out
    unchanged on a fresh instance reading the same file, exactly like every
    other recorded field."""

    path = str(tmp_path / "ledger.jsonl")
    first = ledger.DiagnosisLedger(path=path)
    written = _diagnosis(first, run_id="run-abc123")

    second = ledger.DiagnosisLedger(path=path)
    (seen,) = second.diagnoses()

    assert seen["id"] == written.id
    assert seen["run_id"] == "run-abc123"


def test_run_id_defaults_to_none_never_an_empty_string(tmp_path):
    """Absence is never zero: a caller that omits run_id (every call site
    before wave 2 wires the ticket handle through) must get `None` back, not
    a silently-substituted empty string that a reader could mistake for a
    recorded-but-blank value."""

    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    _diagnosis(led)  # No run_id passed at all.

    (raw_entry,) = led.entries()
    assert "run_id" in raw_entry  # Present, not omitted.
    assert raw_entry["run_id"] is None

    (resolved,) = led.diagnoses()
    assert resolved["run_id"] is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_run_id_is_normalized_to_none_not_stored_verbatim(tmp_path, blank):
    """An empty or whitespace-only run_id must never be indistinguishable
    from a genuinely missing one to a later reader (`incident_correlation.py`
    treats `None`, and only `None`, as unrecorded)."""

    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    _diagnosis(led, run_id=blank)

    (raw_entry,) = led.entries()
    assert raw_entry["run_id"] is None


def test_two_diagnoses_from_different_runs_carry_different_run_ids(tmp_path):
    """Positive control: run_id is not merely present, it actually varies
    with what the caller passes -- a corpus that only ever wrote one value
    could not tell a bug that hardcodes it apart from one that plumbs it
    through correctly."""

    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    _diagnosis(led, run_id="run-1")
    _diagnosis(led, run_id="run-2")

    run_ids = {d["run_id"] for d in led.diagnoses()}
    assert run_ids == {"run-1", "run-2"}


def test_record_diagnosis_requires_source_explicitly():
    """No default -- see the module docstring on why a default would silently
    mislabel the common --from-fixtures case as a live diagnosis."""

    import inspect

    assert inspect.signature(ledger.DiagnosisLedger.record_diagnosis).parameters[
        "source"
    ].default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
# Persistence -- metrics.py's idiom
# --------------------------------------------------------------------------- #


def test_persists_across_separate_ledger_instances_when_a_path_is_configured(tmp_path):
    """Simulates two separate `nettools` process invocations sharing a ledger
    file: the second instance must see the first's diagnosis."""

    path = str(tmp_path / "ledger.jsonl")

    first = ledger.DiagnosisLedger(path=path)
    written = _diagnosis(first, device="PE1")

    second = ledger.DiagnosisLedger(path=path)
    (seen,) = second.diagnoses()

    assert seen["id"] == written.id
    assert seen["device"] == "PE1"
    assert seen["outcome"] == ledger.UNKNOWN

    # And the second instance's own writes accumulate on top, on disk.
    _diagnosis(second, device="PE2")
    third = ledger.DiagnosisLedger(path=path)
    assert len(third.diagnoses()) == 2


def test_ledger_file_is_literally_append_only_jsonl(tmp_path):
    """The physical file grows by whole lines and old lines are byte-identical
    after a second write -- not just logically append-only but append-only on
    disk, the same guarantee network_tools._audit_log gives NETTOOLS_LOG."""

    path = tmp_path / "ledger.jsonl"
    led = ledger.DiagnosisLedger(path=str(path))

    _diagnosis(led, device="PE1")
    first_line = path.read_text(encoding="utf-8").splitlines()[0]

    _diagnosis(led, device="PE2")
    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert lines[0] == first_line  # Untouched by the second write.
    assert json.loads(lines[1])["device"] == "PE2"


def test_in_memory_only_when_no_path_is_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    led = ledger.DiagnosisLedger()
    _diagnosis(led)

    assert list(tmp_path.iterdir()) == []  # No file appeared anywhere.
    assert len(led.diagnoses()) == 1  # Still readable back within this process.
    assert len(ledger.DiagnosisLedger().diagnoses()) == 0  # A fresh instance starts empty.


def test_default_ledger_is_in_memory_only():
    """The module-level default has no path -- see 'No new environment
    variable' in the module docstring: this ledger reads no env var at all,
    unlike metrics.default_collector."""

    assert ledger.default_ledger._path is None


# --------------------------------------------------------------------------- #
# Degrade safely -- the diagnosis must survive a bad ledger path
# --------------------------------------------------------------------------- #


def test_a_write_failure_never_raises_and_is_visible_in_the_result(tmp_path):
    """Point the ledger's path at a location that cannot be a file (a
    directory sits where the ledger file would need to go), forcing the
    on-disk append to fail. record_diagnosis must not raise, and the failure
    must be visible in the returned result rather than swallowed -- the
    'degrade safely, and say so' requirement, and the one place this module
    deliberately does NOT copy metrics.py's silent 'except OSError: pass'."""

    blocked = tmp_path / "ledger.jsonl"
    blocked.mkdir()  # A directory now occupies the path a file needs.
    led = ledger.DiagnosisLedger(path=str(blocked))

    result = _diagnosis(led)  # Must not raise.

    assert result.persisted is False
    assert result.warning is not None
    assert "ledger" in result.warning.lower()
    # The diagnosis still happened, in-memory, in this process -- a caller
    # (once wired in) can still act on it even though nothing is on disk.
    assert len(led.diagnoses()) == 1


def test_a_write_failure_is_warned_on_stderr(tmp_path, capsys):
    blocked = tmp_path / "ledger.jsonl"
    blocked.mkdir()
    led = ledger.DiagnosisLedger(path=str(blocked))

    _diagnosis(led)

    assert "WARNING" in capsys.readouterr().err


def test_a_corrupt_trailing_line_is_skipped_not_fatal(tmp_path, capsys):
    """Mirrors evidence_store.py's corrupt-snapshot handling: a maimed final
    line (what a crash mid-append would produce) is skipped with a warning,
    and every entry before it still loads."""

    path = tmp_path / "ledger.jsonl"
    led = ledger.DiagnosisLedger(path=str(path))
    _diagnosis(led, device="PE1")

    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "diagnosis", "id": "broken", "device": "PE2"\n')  # Truncated JSON.

    reloaded = ledger.DiagnosisLedger(path=str(path))
    diagnoses = reloaded.diagnoses()

    assert len(diagnoses) == 1
    assert diagnoses[0]["device"] == "PE1"
    assert "WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# The human verdict -- the only place an outcome other than 'unknown' can come from
# --------------------------------------------------------------------------- #


def test_record_verdict_requires_a_named_human():
    led = ledger.DiagnosisLedger()
    written = _diagnosis(led)

    with pytest.raises(ValueError):
        led.record_verdict(written.id, ledger.CONFIRMED_CORRECT, by="")


def test_record_verdict_rejects_an_unknown_outcome_value():
    """Unlike metrics.record_verdict (which silently ignores a bad severity),
    a malformed verdict must raise -- a dropped verdict discovered months
    later is exactly the invisible-non-result this module exists to prevent."""

    led = ledger.DiagnosisLedger()
    written = _diagnosis(led)

    with pytest.raises(ValueError):
        led.record_verdict(written.id, "probably-fine", by="ops@example.com")


def test_an_unverdicted_diagnosis_resolves_to_unknown_not_absence():
    led = ledger.DiagnosisLedger()
    _diagnosis(led)

    (resolved,) = led.diagnoses()

    assert resolved["outcome"] == ledger.UNKNOWN
    assert resolved["outcome_by"] is None
    assert resolved["outcome_at"] is None
    assert resolved["verdict_count"] == 0


def test_a_verdict_resolves_the_diagnosis_outcome(tmp_path):
    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))
    written = _diagnosis(led)

    verdict = led.record_verdict(
        written.id, ledger.CONFIRMED_CORRECT, by="ops@example.com", note="checked against console"
    )

    assert verdict.persisted is True
    assert verdict.diagnosis_found is True

    (resolved,) = led.diagnoses()
    assert resolved["outcome"] == ledger.CONFIRMED_CORRECT
    assert resolved["outcome_by"] == "ops@example.com"
    assert resolved["outcome_note"] == "checked against console"
    assert resolved["verdict_count"] == 1


def test_a_correction_is_a_new_entry_never_an_edit(tmp_path):
    """FINDINGS.md's own rule, applied to a verdict: recording a second,
    corrected verdict does not remove or rewrite the first -- both remain in
    entries(), and diagnoses() resolves to whichever was recorded last."""

    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))
    written = _diagnosis(led)

    led.record_verdict(written.id, ledger.CONFIRMED_CORRECT, by="ops@example.com")
    led.record_verdict(
        written.id, ledger.INCORRECT, by="ops@example.com", note="reversed after a second look"
    )

    verdict_entries = [e for e in led.entries() if e["kind"] == "verdict"]
    assert len(verdict_entries) == 2  # The first verdict was not deleted or rewritten.
    assert verdict_entries[0]["outcome"] == ledger.CONFIRMED_CORRECT  # Untouched.

    (resolved,) = led.diagnoses()
    assert resolved["outcome"] == ledger.INCORRECT  # The latest wins in the resolved view.
    assert resolved["verdict_count"] == 2


def test_record_verdict_reports_but_does_not_block_an_unknown_diagnosis_id():
    led = ledger.DiagnosisLedger()

    result = led.record_verdict("never-recorded", ledger.INCORRECT, by="ops@example.com")

    assert result.diagnosis_found is False  # Reported, not refused.


# --------------------------------------------------------------------------- #
# summary() -- unknown must be visible, never hidden
# --------------------------------------------------------------------------- #


def test_summary_of_an_empty_ledger_is_explicitly_vacuous():
    """The exact 'absence read as a healthy value' trap: an empty summary's
    counts are all zero, which reads like a spotless record unless something
    says plainly that nothing was ever recorded."""

    summ = ledger.DiagnosisLedger().summary()

    assert summ["total_diagnoses"] == 0
    assert summ["vacuous"] is True
    assert summ["by_outcome"] == {
        ledger.CONFIRMED_CORRECT: 0, ledger.INCORRECT: 0, ledger.UNKNOWN: 0,
    }


def test_summary_counts_by_outcome_including_unknown_on_a_mixed_corpus():
    """A corpus that varies on every axis at once (outcome, source,
    trustworthy) -- a summary that only distinguished one axis, or that
    dropped 'unknown' from the output, would still pass a corpus with a
    single value on the axis it ignores. This one cannot be satisfied by a
    summary that only ever reports two of the three outcomes."""

    led = ledger.DiagnosisLedger()

    confirmed = _diagnosis(led, source=ledger.SOURCE_LIVE, trustworthy=True)
    led.record_verdict(confirmed.id, ledger.CONFIRMED_CORRECT, by="a")

    wrong = _diagnosis(led, source=ledger.SOURCE_LIVE, trustworthy=True)
    led.record_verdict(wrong.id, ledger.INCORRECT, by="a")

    _diagnosis(led, source=ledger.SOURCE_FIXTURE, trustworthy=False)  # No verdict -> unknown.

    summ = led.summary()

    assert summ["vacuous"] is False
    assert summ["total_diagnoses"] == 3
    assert summ["by_outcome"] == {
        ledger.CONFIRMED_CORRECT: 1, ledger.INCORRECT: 1, ledger.UNKNOWN: 1,
    }
    assert summ["by_source"][ledger.SOURCE_LIVE]["total"] == 2
    assert summ["by_source"][ledger.SOURCE_FIXTURE]["total"] == 1
    assert summ["by_source"][ledger.SOURCE_FIXTURE]["by_outcome"][ledger.UNKNOWN] == 1
    assert summ["by_trustworthy"]["true"] == {
        ledger.CONFIRMED_CORRECT: 1, ledger.INCORRECT: 1, ledger.UNKNOWN: 0,
    }
    assert summ["by_trustworthy"]["false"] == {
        ledger.CONFIRMED_CORRECT: 0, ledger.INCORRECT: 0, ledger.UNKNOWN: 1,
    }


def test_fixture_and_live_diagnoses_never_merge_into_one_source_bucket():
    led = ledger.DiagnosisLedger()
    _diagnosis(led, source=ledger.SOURCE_LIVE)
    _diagnosis(led, source=ledger.SOURCE_FIXTURE)
    _diagnosis(led, source=ledger.SOURCE_FIXTURE)

    summ = led.summary()

    assert set(summ["by_source"]) == {ledger.SOURCE_LIVE, ledger.SOURCE_FIXTURE}
    assert summ["by_source"][ledger.SOURCE_LIVE]["total"] == 1
    assert summ["by_source"][ledger.SOURCE_FIXTURE]["total"] == 2


def test_render_summary_text_always_shows_unknown_even_at_zero():
    led = ledger.DiagnosisLedger()
    confirmed = _diagnosis(led)
    led.record_verdict(confirmed.id, ledger.CONFIRMED_CORRECT, by="a")

    text = led.render_summary_text()

    assert "unknown=0" in text  # Present at zero, not omitted.
    assert "confirmed_correct=1" in text


def test_render_summary_text_names_vacuous_explicitly_when_empty():
    text = ledger.DiagnosisLedger().render_summary_text()

    assert "vacuous" in text.lower()


# --------------------------------------------------------------------------- #
# reset() -- test isolation only
# --------------------------------------------------------------------------- #


def test_reset_clears_in_memory_and_on_disk_state(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = ledger.DiagnosisLedger(path=str(path))
    _diagnosis(led)
    assert path.is_file()

    led.reset()

    assert led.diagnoses() == ()
    assert not path.is_file()


# --------------------------------------------------------------------------- #
# Module-level free functions -- the same shape metrics.py exposes
# --------------------------------------------------------------------------- #


def test_module_level_functions_operate_on_an_explicit_ledger(tmp_path):
    led = ledger.DiagnosisLedger(path=str(tmp_path / "ledger.jsonl"))

    written = ledger.record_diagnosis(
        device="PE1", subject="10.255.0.31", flow="bgp_session",
        finding="interface_line_down", trustworthy=True, source=ledger.SOURCE_LIVE,
        ledger=led,
    )
    ledger.record_verdict(written.id, ledger.CONFIRMED_CORRECT, by="ops", ledger=led)

    assert ledger.summary(ledger=led)["by_outcome"][ledger.CONFIRMED_CORRECT] == 1
    assert len(ledger.entries(ledger=led)) == 2
    assert len(ledger.diagnoses(ledger=led)) == 1

    ledger.reset(ledger=led)
    assert ledger.diagnoses(ledger=led) == ()


def test_module_level_functions_default_to_the_process_wide_ledger():
    ledger.reset()
    try:
        written = _diagnosis(ledger.default_ledger)
        ledger.record_verdict(written.id, ledger.INCORRECT, by="ops")

        assert ledger.summary()["by_outcome"][ledger.INCORRECT] == 1
    finally:
        ledger.reset()


# --------------------------------------------------------------------------- #
# CLI wiring (B-485). The module's guarantees are tested above; these pin that
# the command line cannot get around them.
# --------------------------------------------------------------------------- #


def test_investigate_records_a_fixture_replay_as_fixture_not_live(tmp_path, monkeypatch):
    """The mislabelling this ledger would otherwise quietly accumulate.

    `--from-fixtures` is this project's most-run path. If it recorded as
    `live`, the accuracy corpus would fill with replays of committed captures
    and read as field evidence.
    """

    import sys

    from agent_nettools import cli
    from agent_nettools import ledger as L

    path = tmp_path / "acc.jsonl"
    monkeypatch.setenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", str(path))
    monkeypatch.setattr(sys, "argv",
                        ["nettools", "investigate", "RR1", "10.255.0.12",
                         "--from-fixtures", "--quiet"])
    cli.main()

    entries = L.DiagnosisLedger(path=path).diagnoses()
    assert len(entries) == 1
    assert entries[0]["source"] == L.SOURCE_FIXTURE
    assert entries[0]["outcome"] == "unknown", "the tool must not judge its own diagnosis"


def test_the_cli_offers_no_way_for_the_tool_to_score_itself(tmp_path, monkeypatch):
    """`ledger verdict` requires a diagnosis id and an outcome chosen by a
    person. There is deliberately no verb that lets a run mark its own result,
    and `record_diagnosis` has no `outcome` parameter for one to reach."""

    import inspect

    from agent_nettools import ledger as L

    assert "outcome" not in inspect.signature(L.record_diagnosis).parameters


def test_a_ledger_write_failure_never_fails_the_investigation(tmp_path, monkeypatch):
    """Bookkeeping is not allowed to take down a diagnosis."""

    import sys

    from agent_nettools import cli

    # A directory where a file must go: the append cannot succeed.
    monkeypatch.setenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", str(tmp_path))
    monkeypatch.setattr(sys, "argv",
                        ["nettools", "investigate", "RR1", "10.255.0.12",
                         "--from-fixtures", "--quiet"])
    code = cli.main()

    assert code == 1, "the investigation still reports its own finding, not a ledger error"


def test_investigate_writes_a_ticket_carrying_the_session_counts(tmp_path, monkeypatch):
    """The wiring, not the module — OBS-170's lesson applied to this seam.

    `ticket.py` is well tested and was correct; the twelve lines in `cli.py`
    that call it were not tested, and the first version passed
    `sessions=` where the API takes `session_count=`. The broad
    bookkeeping-never-fails-a-run catch turned that TypeError into a stderr
    note, which `--quiet` then suppressed: a silently empty ticket.

    So this test exercises the SEAM. It asserts the ticket actually carries the
    per-device session counts the epoch computed, which no test of ticket.py
    alone could ever catch.
    """

    import sys

    from agent_nettools import cli, ticket

    tickets = tmp_path / "tickets"
    monkeypatch.setenv("NETTOOLS_TICKET_DIR", str(tickets))
    monkeypatch.setattr(sys, "argv", ["nettools", "investigate", "RR1", "10.255.0.12",
                                      "--from-fixtures", "--quiet"])
    cli.main()

    written = sorted(tickets.glob("*.md"))
    assert len(written) == 1, "one interaction, one ticket"

    data = ticket.read_ticket(written[0])
    interactions = {d["device"]: d["session_count"] for d in data["device_interactions"]}

    # collect_epoch's own docstring predicts this shape: RR1 in one session,
    # PE2 in two (it fans out), total 3.
    assert interactions == {"RR1": 1, "PE2": 2}, (
        f"the ticket must carry the epoch's real session counts, got {interactions}"
    )


def test_investigate_writes_a_ticket_carrying_commands_run_and_latency(tmp_path, monkeypatch):
    """The same seam as the test above, extended to the two provenance
    fields B-446 shipped with, and which stayed permanently null: measured
    live against PE1 the night this landed, `commands_run` and `latency_ms`
    were `None` on every run, not only on the fixture path -- nothing filled
    them in, though `Ticket.record_device_interaction` always accepted them.

    The expected `commands_run` is derived from an independently run
    investigation over the same device/subject/sender (`investigate()`'s own
    default resolver, `inventory_resolver`, since the CLI never overrides
    it) rather than a hardcoded literal, so this test does not rot if an
    intent is ever added to the `bgp_session` ladder or the inventory
    changes PE2's path-scoped interface set -- both would change the exact
    count without this being the wrong fix.
    """

    import sys

    from agent_nettools import cli, investigation, ticket
    from agent_nettools.fixtures import fixture_sender

    reference = investigation.investigate(
        "RR1", "10.255.0.12", sender=fixture_sender(label="broken")
    )
    expected_commands = reference.session_summary["commands_run"]["by_device"]
    assert expected_commands, "the reference investigation must actually have collected something"
    assert all(count > 0 for count in expected_commands.values()), expected_commands

    tickets = tmp_path / "tickets"
    monkeypatch.setenv("NETTOOLS_TICKET_DIR", str(tickets))
    monkeypatch.setattr(sys, "argv", ["nettools", "investigate", "RR1", "10.255.0.12",
                                      "--from-fixtures", "--quiet"])
    cli.main()

    written = sorted(tickets.glob("*.md"))
    assert len(written) == 1, "one interaction, one ticket"

    data = ticket.read_ticket(written[0])
    commands_by_device = {d["device"]: d["commands_run"] for d in data["device_interactions"]}
    latency_by_device = {d["device"]: d["latency_ms"] for d in data["device_interactions"]}

    assert commands_by_device == expected_commands, (
        f"the ticket's commands_run must match the epoch's own count, got {commands_by_device}, "
        f"expected {expected_commands}"
    )
    for device, count in commands_by_device.items():
        assert count is not None, f"{device} commands_run must not be null"

    for device, latency in latency_by_device.items():
        assert latency is not None, f"{device} latency_ms must not be null"
        assert latency > 0, f"{device} latency_ms must be positive, got {latency}"


def test_investigate_ticket_retries_is_none_not_a_default_zero(tmp_path, monkeypatch):
    """OBS-188's defect class, checked directly at the wiring seam.

    No path from a device to an `Observation` carries a measured per-device
    retry count today: `_netmiko_send_commands` computes one, but
    `network_tools._section_from_combined` and the template runners
    (`run_template`/`run_templates_split`) strip it out of every per-
    intent/per-template envelope before `collect_epoch` ever sees it. Before
    this change `record_device_interaction`'s `retries` defaulted to `0`,
    which reads as "zero retries were measured" and is indistinguishable
    from a genuine zero -- exactly OBS-188's shape. The ticket must record
    `None` -- "not measured" -- instead.
    """

    import sys

    from agent_nettools import cli, ticket

    tickets = tmp_path / "tickets"
    monkeypatch.setenv("NETTOOLS_TICKET_DIR", str(tickets))
    monkeypatch.setattr(sys, "argv", ["nettools", "investigate", "RR1", "10.255.0.12",
                                      "--from-fixtures", "--quiet"])
    cli.main()

    written = sorted(tickets.glob("*.md"))
    data = ticket.read_ticket(written[0])

    assert data["device_interactions"], "the ticket must carry at least one device interaction"
    for interaction in data["device_interactions"]:
        assert interaction["retries"] is None, (
            f"{interaction['device']}'s retries must be None (not measured), "
            f"got {interaction['retries']!r} -- a default masquerading as a measurement"
        )
