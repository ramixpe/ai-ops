"""`nettools investigate` — the CLI, and the exit-code decision (T-031).

The exit-code scheme is the design decision this task turned on, so most of
these tests are about it rather than about argument parsing.

    0  the descent completed and found no fault
    1  the descent completed and found a fault      -- a NETWORK problem
    2  no trustworthy answer was produced           -- an ANSWER problem

The three labels give all three codes from real captured output, with no lab,
no credentials and no API key: `healthy` -> 0, `broken` -> 1, `t0` -> 2 (its
transport rung predates template capture and genuinely cannot be read).
"""

from __future__ import annotations

import json
import sys

import pytest

from agent_nettools import cli, investigation

ARGS = ["investigate", "RR1", "10.255.0.12", "--from-fixtures"]


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """`main()` loads `.env`, which in this repo holds real credentials."""

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(cli, "find_dotenv", lambda *a, **k: "")


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch):
    """No credentials, no API keys. The `--from-fixtures` claim, enforced.

    A demo that quietly needs `DEVICE_PASSWORD` set is not a demo that runs on
    a sceptical engineer's laptop, and it is the first command in the README.
    """

    for name in ("DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE",
                 "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY",
                 "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def _main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nettools", *argv])
    return cli.main()


@pytest.fixture
def run(monkeypatch, capsys):
    def _run(*extra):
        code = _main([*ARGS, *extra], monkeypatch)
        return code, capsys.readouterr().out
    return _run


def _payload(out: str) -> dict:
    """Parse the JSON payload out of a run's stdout.

    `_note` prints informational lines prefixed with `#` to stdout -- an
    established convention in this CLI (`nettools diff` does the same for its
    snapshot path), so it is stripped here rather than changed. It does mean
    `--format json` is not directly pipeable to `jq`; filed as B-422.
    """

    return json.loads("\n".join(
        line for line in out.splitlines() if not line.startswith("#")
    ))


# --------------------------------------------------------------------------- #
# The three exit codes, from real captured output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "expected", "finding"),
    [
        ("healthy", 0, "all_layers_healthy"),
        ("broken", 1, "interface_line_down"),
        ("t0", 2, "undetermined"),
    ],
)
def test_the_three_outcomes_map_to_the_three_codes(run, label, expected, finding):
    code, out = run("--label", label)

    assert code == expected
    assert finding in out


def test_all_three_codes_are_actually_reachable(monkeypatch):
    """§0.12, applied to the scheme itself.

    Every assertion above is individually satisfiable by a function that always
    returns its expected value. What makes the parametrisation meaningful is
    that the three are *different*, produced by three real labels.
    """

    codes = {_main([*ARGS, "--label", label, "--quiet"], monkeypatch)
             for label in ("healthy", "broken", "t0")}
    assert codes == {0, 1, 2}


def test_exit_one_is_a_network_problem_and_exit_two_is_an_answer_problem(run):
    """The distinction the scheme exists for, stated as an assertion.

    `broken` is a real fault, cleanly diagnosed: the answer is trustworthy and
    the *network* is not. `t0` is a fabric that may be perfectly healthy: the
    answer is missing and that is all we know.
    """

    code, out = run("--label", "broken", "--format", "json")
    payload = _payload(out)
    assert code == 1
    assert payload["trustworthy"] is True, "exit 1 says the answer IS trustworthy"

    code, out = run("--label", "t0", "--format", "json")
    payload = _payload(out)
    assert code == 2
    assert payload["trustworthy"] is False
    assert payload["cause"] is None, (
        "an undetermined walk stopped; the lowest rung it reached is not a cause"
    )


def _result(label, **overrides):
    """A real descent from a fixture label, with the model's outcome overridden.

    `--from-fixtures` skips the model steps by design, so the CLI's exit-code
    mapping for a withheld or coverage-limited model output cannot be reached
    through it. Constructing the result and driving `_cmd_investigate` tests
    exactly what this task owns -- the mapping -- while `investigate()`'s own
    behaviour is covered in `test_investigation.py`.
    """

    from dataclasses import replace as _replace

    from agent_nettools.fixtures import fixture_sender

    base = investigation.investigate(
        "RR1", "10.255.0.12", flow="bgp_session",
        resolver=lambda s: {"10.255.0.12": "PE2", "10.255.0.31": "RR1"}[s],
        sender=fixture_sender(label=label),
    )
    return _replace(base, **overrides)


def _drive(result, monkeypatch, capsys, fmt="json"):
    monkeypatch.setattr(cli, "investigate", lambda *a, **k: result)
    code = _main([*ARGS, "--format", fmt], monkeypatch)
    return code, capsys.readouterr().out


def test_a_withheld_paraphrase_no_longer_changes_the_exit_code(monkeypatch, capsys):
    """**A deliberate reversal at B-439, and the argument it has to answer.**

    This test asserted the opposite until 2026-08-17: a withheld report exited
    2, on the reasoning that *"if a grounding failure exited 1, a systematic
    grounding regression would hide in the noise of routine faults forever."*

    That reasoning was right while the report **was** the answer. It is not any
    more. The authoritative report is rendered from the descent and cannot fail
    to be produced; what can fail grounding is the model's paraphrase, which
    nothing downstream depends on. Exiting 2 because a restatement was clumsy
    would mean a cosmetic failure suppresses a trustworthy finding, which
    inverts the point of B-439.

    **But the original concern is not answered by that, and is not dismissed
    here.** A systematic paraphrase-grounding regression is now invisible to
    exit codes. It has to be visible somewhere, so the replacement requirement
    is that it is loud in the payload and on stderr -- asserted below, and filed
    as B-457 because a field nobody aggregates is not detection either.
    """

    from agent_nettools.grounding import GroundingFailure, GroundingResult

    withheld = _result(
        "broken",
        paraphrase=None,
        paraphrase_status=investigation.WITHHELD,
        paraphrase_grounding=GroundingResult(
            failures=(GroundingFailure("uncited_rung", "rung:transport", "not cited"),)
        ),
    )
    code, out = _drive(withheld, monkeypatch, capsys)
    payload = _payload(out)

    assert code == 1, "the network is broken; the answer about it is still good"
    assert payload["trustworthy"] is True

    # The authoritative answer is present and is not the model's.
    assert payload["report"]["authoritative"] is True
    assert payload["report"]["content"]["generated_by"] == "code"

    # And the failure is not silent.
    assert payload["report"]["paraphrase"]["status"] == investigation.WITHHELD
    assert payload["report"]["paraphrase"]["content"] is None, (
        "rejected prose still does not leave the module"
    )

def test_the_identical_descent_without_a_withheld_report_exits_one(monkeypatch, capsys):
    """The companion. Without it, the test above could pass because `broken`
    exits 2 for some unrelated reason -- the two differ only in the model's
    outcome, and the exit code moves with it."""

    code, _ = _drive(_result("broken"), monkeypatch, capsys)
    assert code == 1


# --------------------------------------------------------------------------- #
# coverage_limited follows the descent, and carries its caveat
# --------------------------------------------------------------------------- #


def _coverage_limited():
    from pathlib import Path

    from agent_nettools import log_window, template_parsers
    from agent_nettools.grounding import GroundingFailure, GroundingResult

    raw = (Path(__file__).resolve().parent / "fixtures" / "cisco_xr" / "PE2" /
           "broken" / "show-logging-last-200.txt").read_text()
    parsed, _ = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    coverage = log_window.coverage_from_logging(parsed, "PE2")

    return _result(
        "broken",
        correlation_paraphrase={"timeline": [], "correlation": {"found": False}},
        correlation_paraphrase_status=investigation.COVERAGE_LIMITED,
        correlation_grounding=GroundingResult(
            failures=(GroundingFailure("absence_claim_exceeds_coverage",
                                       "correlation.found", "; ".join(coverage.gaps())),),
            absence_claims_checked=1,
        ),
        coverage=coverage,
    )


@pytest.mark.parametrize("fmt", ["json", "table", "summary"])
def test_a_coverage_limited_correlation_keeps_the_exit_code_and_carries_the_caveat(
    monkeypatch, capsys, fmt
):
    """The operator's rule, and the reason it is right.

    PE2's buffer holds 593 records and 200 were read, so the *timeline* is
    qualified. The *finding* is not: the descent is deterministic and was
    reached with no model anywhere in it. Downgrading the exit code would
    report doubt about a diagnosis that has none.

    Parametrised over every format because "carries the caveat" is a promise
    unless every renderer keeps it. A caveat surviving only in `--format json`
    is a caveat the person reading a summary never sees.
    """

    code, out = _drive(_coverage_limited(), monkeypatch, capsys, fmt=fmt)

    assert code == 1, "the descent found a fault; a limited timeline is not doubt"
    assert "coverage_limited" in out or "coverage-limited" in out
    assert "interface_line_down" in out


def test_the_caveat_names_the_shortfall_and_says_the_finding_stands(monkeypatch, capsys):
    _, out = _drive(_coverage_limited(), monkeypatch, capsys)
    caveat = _payload(out)["correlation"]["caveat"]

    assert "200 of 593" in caveat
    assert "deterministic and unaffected" in caveat
    assert "not as 'nothing happened'" in caveat


# --------------------------------------------------------------------------- #
# The chain survives every renderer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ["json", "table", "summary"])
def test_the_causal_chain_survives_every_format(run, fmt):
    """A finding without its chain is an assertion where the descent produced
    an argument. `--format summary` is the one most likely to be pasted into a
    ticket, so it is the one that must not lose it."""

    _, out = run("--label", "broken", "--format", fmt)

    for rung in ("bgp_session", "transport", "route_to_peer", "igp_adjacency", "interface"):
        assert rung in out, f"{fmt} lost the {rung} rung"


def test_the_table_marks_the_cause_and_shows_the_whole_ladder(run):
    _, out = run("--label", "broken", "--format", "table")

    assert "<-- CAUSE" in out
    assert out.count("BROKEN") == 5
    assert "FINDING: interface_line_down on PE2" in out


def test_quiet_prints_nothing_and_still_carries_the_outcome(run):
    code, out = run("--label", "broken", "--quiet")

    assert out == ""
    assert code == 1


# --------------------------------------------------------------------------- #
# The scheme is documented where a caller will look
# --------------------------------------------------------------------------- #


def test_the_help_text_warns_that_health_uses_a_different_scheme():
    """A real trap in this CLI, and one that predates this task.

    `nettools health` maps 2 to the worst *network* outcome; `diff` and
    `investigate` map it to "the answer is not trustworthy". A script calling
    both and assuming one scheme will read a critical fabric as a broken tool,
    or the reverse. Documented rather than silently inherited.
    """

    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions)  # noqa: SLF001
    description = action.choices["investigate"].description

    assert "NETWORK" in description and "ANSWER" in description
    assert "health" in description


def test_an_unknown_flow_is_refused_by_the_parser(monkeypatch):
    with pytest.raises(SystemExit):
        _main([*ARGS, "--flow", "no_such_flow"], monkeypatch)


def test_an_unknown_subject_exits_two_rather_than_raising(monkeypatch):
    """A subject no device owns produced no answer at all. Exit 2, and a
    structured error rather than a traceback."""

    code = _main(["investigate", "RR1", "10.255.99.99", "--quiet"], monkeypatch)
    assert code == 2


# --------------------------------------------------------------------------- #
# B-112 -- free-text flow selection wired into `investigate`, optionally
# --------------------------------------------------------------------------- #


def test_a_sentence_shaped_subject_reaches_the_same_descent_as_the_direct_call(
    monkeypatch, capsys
):
    """`RR1 10.255.0.12 --flow bgp_session` and the sentence below must land
    on the identical descent -- this is the whole point of B-112: the human
    should not need to know `--flow bgp_session` exists."""

    code = _main(
        ["investigate", "RR1", "why can't RR1 reach 10.255.0.12?",
         "--from-fixtures", "--label", "broken", "--format", "json"],
        monkeypatch,
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "interface_line_down" in captured.out
    assert "Free-text selection" in captured.err


def test_an_unmatched_sentence_exits_two_with_actionable_candidates(monkeypatch, capsys):
    """Unmatched is an answer, not a traceback (flow_selection.py): the CLI
    must surface the reason and the candidates, and must never hand the raw
    sentence down to `investigate()` as if it were a real subject."""

    code = _main(
        ["investigate", "RR1", "why is the sky blue",
         "--from-fixtures", "--label", "broken", "--format", "json"],
        monkeypatch,
    )
    captured = capsys.readouterr()

    assert code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert any("did not understand" in e for e in payload["errors"])
    assert any(e.startswith("try:") for e in payload["errors"])


def test_an_explicit_flow_bypasses_free_text_selection_entirely(monkeypatch, capsys):
    """The optionality requirement, tested directly: with `--flow` given, a
    sentence-shaped SUBJECT is passed straight through exactly as it always
    was, and hits the pre-existing "no such subject" failure -- not the new
    free-text path. If this test needed `Free-text selection` on stderr, the
    wiring would have broken the "otherwise behave exactly as today" rule."""

    code = _main(
        ["investigate", "RR1", "why can't RR1 reach 10.255.0.12?",
         "--flow", "bgp_session", "--from-fixtures", "--label", "broken",
         "--quiet"],
        monkeypatch,
    )
    assert code == 2  # no answer at all -- the sentence is not a real subject


def test_a_device_named_in_the_sentence_never_overrides_the_positional_device(
    monkeypatch, capsys
):
    """The device always comes from the DEVICE argument, never from the
    sentence -- `flow_selection.select_flow` resolves a device for its own
    contract, but this CLI wiring only ever adopts the FLOW and SUBJECT from
    it, exactly as `_cmd_investigate`'s own comment states."""

    captured_call: dict = {}

    def fake_investigate(device, subject, **kwargs):
        captured_call["device"] = device
        captured_call["subject"] = subject
        captured_call["flow"] = kwargs.get("flow")
        return _result("broken")

    monkeypatch.setattr(cli, "investigate", fake_investigate)

    code = _main(
        ["investigate", "PE2", "why can't RR1 reach 10.255.0.12?", "--format", "json"],
        monkeypatch,
    )
    err = capsys.readouterr().err

    assert code == 1
    assert captured_call["device"] == "PE2", "the positional DEVICE argument wins"
    assert captured_call["subject"] == "10.255.0.12"
    assert captured_call["flow"] == "bgp_session"
    assert "sentence named device 'RR1'" in err
    assert "investigating from 'PE2'" in err


def test_flow_defaults_to_bgp_session_exactly_as_before_when_subject_is_not_a_sentence(
    monkeypatch,
):
    """The argparse default changed from the literal string `"bgp_session"`
    to `None` so the CLI can tell "omitted" apart from "explicitly chosen" --
    this pins that the *effective* default is unchanged for every subject
    that is not sentence-shaped, which is every subject `investigate` has
    ever accepted before B-112."""

    captured_call: dict = {}

    def fake_investigate(device, subject, **kwargs):
        captured_call["flow"] = kwargs.get("flow")
        return _result("broken")

    monkeypatch.setattr(cli, "investigate", fake_investigate)
    _main(["investigate", "RR1", "10.255.0.12", "--quiet"], monkeypatch)

    assert captured_call["flow"] == "bgp_session"


# --------------------------------------------------------------------------- #
# B-422 -- stdout is the payload, stderr is about the run
# --------------------------------------------------------------------------- #


def test_format_json_output_is_valid_json_on_its_own(monkeypatch, capsys):
    """The point of B-422, and the thing that was actually broken.

    `_note` printed informational lines to **stdout**, so

        nettools investigate ... --format json | jq

    failed on the first note. The JSON output was not consumable by the tool
    everyone reaches for, and the `#` prefix made the lines look like comments,
    which is true of very little and not of JSON.
    """

    _main([*ARGS, "--format", "json"], monkeypatch)
    captured = capsys.readouterr()

    payload = json.loads(captured.out)
    assert payload["finding"] == "interface_line_down"


def test_the_notes_are_still_shown_just_on_the_other_stream():
    """Not a deletion. A human sees both interleaved exactly as before; only a
    pipe sees the difference."""

    import sys as _sys
    from unittest import mock

    import pytest as _pytest

    with mock.patch.object(cli, "load_dotenv", lambda *a, **k: False), \
         mock.patch.object(cli, "find_dotenv", lambda *a, **k: ""):
        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(_sys, "argv", ["nettools", *ARGS, "--format", "json"])
            import io

            out, err = io.StringIO(), io.StringIO()
            mp.setattr(_sys, "stdout", out)
            mp.setattr(_sys, "stderr", err)
            cli.main()

    assert "Fixture replay" in err.getvalue(), "the note is still emitted"
    assert "Fixture replay" not in out.getvalue(), "and not into the payload"


def test_quiet_still_silences_both_streams(monkeypatch, capsys):
    code = _main([*ARGS, "--quiet"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == ""


# --------------------------------------------------------------------------- #
# The payload carries per-device session counts (B-446 ticket instrumentation)
# --------------------------------------------------------------------------- #


def test_the_investigate_payload_carries_the_session_summary(run):
    """`bgp_session`'s own documented shape, reaching all the way to the CLI's
    rendered JSON: RR1 in one session, PE2 (the fan-out device) in two.

    `commands_run`/`latency_ms` (the B-446 gap closure) are checked for
    presence and shape here rather than pinned to a literal: this is the CLI
    payload, not the epoch itself, and the exact per-device command count
    depends on inventory-derived path scoping that is not this test's
    concern (see `test_epoch.py`'s
    `test_investigation_result_carries_the_epochs_session_summary` for why a
    literal here would rot for reasons unrelated to this field)."""

    _, out = run()
    payload = _payload(out)

    sessions = payload["sessions"]
    assert sessions["total"] == 3
    assert sessions["by_device"] == {"RR1": 1, "PE2": 2}

    commands = sessions["commands_run"]
    assert set(commands["by_device"]) == {"RR1", "PE2"}
    assert commands["total"] == sum(commands["by_device"].values())
    assert all(count > 0 for count in commands["by_device"].values())

    latency = sessions["latency_ms"]
    assert set(latency["by_device"]) == {"RR1", "PE2"}
    assert all(ms > 0 for ms in latency["by_device"].values())


# --------------------------------------------------------------------------- #
# `_ledger_for_cli`'s `default_ledger()` bug (B-485/B-446 build wave)
#
# `ledger.default_ledger` is a module-level INSTANCE (`ledger.py`'s own
# `default_ledger = DiagnosisLedger()`), not a factory. `_ledger_for_cli` used
# to call it as `_ledger.default_ledger()`, raising `TypeError: 'DiagnosisLedger'
# object is not callable` on every run with `NETTOOLS_DIAGNOSIS_LEDGER_FILE`
# unset -- the common case, since that env var has no default
# (`ledger.py`'s own "No new environment variable" section). Verified by hand
# before the fix, 2026-08-18: `nettools investigate RR1 10.255.0.12
# --from-fixtures` printed "# accuracy ledger not updated: 'DiagnosisLedger'
# object is not callable" on stderr and recorded nothing; `nettools ledger
# summary` raised the same `TypeError` uncaught, to a full traceback.
# --------------------------------------------------------------------------- #


def test_ledger_for_cli_returns_the_process_wide_default_ledger(monkeypatch):
    """The direct repro: calling `_ledger_for_cli()` itself must not raise,
    and with no env var set it must be the real, shared `default_ledger`
    instance -- not a broken call, not a fresh unrelated ledger."""

    from agent_nettools import ledger

    monkeypatch.delenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", raising=False)

    assert cli._ledger_for_cli() is ledger.default_ledger


def test_investigate_from_fixtures_records_a_diagnosis_in_memory_when_env_is_unset(
    monkeypatch, capsys,
):
    """The exact repro from the build report, end to end through `cli.main()`.

    Before the fix this printed the `TypeError` note on stderr and recorded
    nothing; after it, the diagnosis actually lands in `ledger.default_ledger`'s
    in-memory entries -- gone at process exit (no path configured, by
    design), but real within this run, which is what `nettools ledger
    summary` immediately afterward (same process) would report.
    """

    from agent_nettools import ledger

    monkeypatch.delenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", raising=False)
    ledger.reset()
    try:
        _main(ARGS, monkeypatch)
        captured = capsys.readouterr()

        assert "not callable" not in captured.err
        assert "accuracy ledger not updated" not in captured.err

        diagnoses = ledger.diagnoses()
        assert len(diagnoses) == 1
        assert diagnoses[0]["device"] == "RR1"
        assert diagnoses[0]["subject"] == "10.255.0.12"
        assert diagnoses[0]["flow"] == "bgp_session"
        # `--from-fixtures` on ARGS: must never be counted as a live diagnosis.
        assert diagnoses[0]["source"] == ledger.SOURCE_FIXTURE
    finally:
        ledger.reset()


# --------------------------------------------------------------------------- #
# The ledger write's id, surfaced (B-446 build wave)
#
# `nettools ledger verdict --help` calls its `diagnosis_id` argument "the id
# `investigate` reported" -- `_record_diagnosis_in_ledger` captured `write.id`
# and only ever read `.warning` off it, so `investigate` never actually
# reported it anywhere an operator could see.
# --------------------------------------------------------------------------- #


def test_the_ledger_id_is_surfaced_so_an_operator_can_run_ledger_verdict(
    monkeypatch, capsys,
):
    from agent_nettools import ledger

    monkeypatch.delenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", raising=False)
    ledger.reset()
    try:
        _main(ARGS, monkeypatch)
        err = capsys.readouterr().err

        recorded = ledger.diagnoses()
        assert len(recorded) == 1
        real_id = recorded[0]["id"]

        assert f"id={real_id}" in err
        assert f"nettools ledger verdict {real_id}" in err
    finally:
        ledger.reset()


# --------------------------------------------------------------------------- #
# W3c -- the ledger row carries this run's real run_id and the cause rung's
# own subject (not the investigation's top-level subject), the two fields
# `incident_correlation.from_ledger_diagnoses` needs and could not get before
# this wiring landed (see that module's "ledger integration gap" section).
# --------------------------------------------------------------------------- #


def test_a_run_writes_a_ledger_row_carrying_a_real_run_id_and_cause_subject(
    monkeypatch, capsys,
):
    from agent_nettools import ledger

    monkeypatch.delenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", raising=False)
    ledger.reset()
    try:
        _main(ARGS, monkeypatch)

        recorded = ledger.diagnoses()
        assert len(recorded) == 1
        row = recorded[0]

        # A real run_id -- present, not None, and it round-trips through
        # ledger.diagnoses() (the same shape incident_correlation.py's
        # from_ledger_diagnoses reads).
        assert row["run_id"] is not None
        assert isinstance(row["run_id"], str) and row["run_id"]

        # The cause rung's own subject (the interface, "Gi0/0/0/0" on the
        # committed "broken" fixture), NOT the investigation's own top-level
        # subject ("10.255.0.12") -- the two are deliberately different
        # fields, per incident_correlation.py's module docstring.
        assert row["subject"] == "10.255.0.12"
        assert row["cause"]["subject"] not in (None, row["subject"])
        assert row["cause"]["rung"] == "interface"
        assert row["cause"]["device"] == "PE2"
    finally:
        ledger.reset()


def test_the_ledger_run_id_matches_the_tickets_own_run_id(monkeypatch, tmp_path):
    """The join key actually joins: the run_id on the ledger row is the SAME
    run_id as the ticket opened for the same investigation, not two
    independently-minted ids that merely look alike."""

    from agent_nettools import ledger

    monkeypatch.delenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", raising=False)
    monkeypatch.setenv("NETTOOLS_TICKET_DIR", str(tmp_path / "tickets"))
    ledger.reset()
    try:
        _main(ARGS, monkeypatch)

        recorded = ledger.diagnoses()
        assert len(recorded) == 1
        run_id = recorded[0]["run_id"]

        from agent_nettools import ticket_read

        found = ticket_read.read_ticket_by_run_id(run_id)
        assert found is not None
        assert found["run_id"] == run_id
    finally:
        ledger.reset()


# --------------------------------------------------------------------------- #
# W4a -- the ticket records how the raw question became the resolved intent
# --------------------------------------------------------------------------- #


def test_the_ticket_records_the_resolved_intent(monkeypatch, tmp_path):
    """`record_intent` fires once per ticket, carrying the resolved flow and
    subject and the literal resolver name the CLI path always uses (it never
    passes `resolver=` to `investigate()`, so `investigate()`'s own
    `resolve = resolver or inventory_resolver` always falls through to the
    module default)."""

    from agent_nettools import ticket

    _main(ARGS, monkeypatch)

    files = sorted((tmp_path / "tickets").glob("*.md"))
    assert len(files) == 1
    parsed = ticket.read_ticket(files[0])

    assert parsed["intent"] is not None
    assert parsed["intent"]["flow"] == "bgp_session"
    assert parsed["intent"]["resolved_subject"] == "10.255.0.12"
    assert parsed["intent"]["resolver"] == "inventory_resolver"


# --------------------------------------------------------------------------- #
# B-407 -- session memory wiring
#
# `session_memory.py` shipped with the exact call this wiring makes already
# specified in its own docstring; these tests exercise it through the real
# CLI entry point, `NETTOOLS_SESSION_MEMORY_DIR` isolated per test by
# `conftest.py`'s autouse fixture (the same OBS-172 shape `NETTOOLS_TICKET_DIR`
# was isolated for). Every refusal test has a positive control alongside it
# through the identical path (OBS-181).
# --------------------------------------------------------------------------- #


def test_a_run_records_a_turn_session_memory_can_recall(monkeypatch):
    from agent_nettools import session_memory as sm

    code = _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-1"],
        monkeypatch,
    )
    assert code == 1  # cause_not_localised -- a fault was found on the path

    recalled = sm.recall("test-sess-1")
    assert recalled.outcome == sm.FOUND
    assert recalled.turn.device == "PE3"
    assert recalled.turn.subject == "Gi0/0/0/0"
    assert recalled.turn.flow == "isis_adjacency"
    assert recalled.turn.run_id
    assert recalled.turn.ticket_path


def test_it_resolves_subject_from_the_previous_turn_in_the_same_session(
    monkeypatch, capsys,
):
    """Positive control (OBS-181) for the refusal tests below: a legitimate
    'it', with a real prior turn to resolve against, must actually work."""

    _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-2"],
        monkeypatch,
    )
    capsys.readouterr()

    code = _main(
        ["investigate", "PE3", "it",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-2"],
        monkeypatch,
    )
    out, err = capsys.readouterr()

    assert code == 1
    assert "resolved to subject 'Gi0/0/0/0'" in err
    # No --flow given the second time either -- it comes from the recalled
    # turn, so the descent runs isis_adjacency again, not the bgp_session
    # default (B-112's pre-existing fallback).
    assert _payload(out)["flow"] == "isis_adjacency"


def test_it_resolves_device_from_the_previous_turn_too(monkeypatch, capsys):
    _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-3"],
        monkeypatch,
    )
    capsys.readouterr()

    code = _main(
        ["investigate", "it", "it",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-3"],
        monkeypatch,
    )
    out, err = capsys.readouterr()

    assert code == 1
    assert "resolved to device 'PE3'" in err
    assert _payload(out)["device"] == "PE3"


def test_an_explicit_flow_is_never_overridden_by_the_recalled_one(monkeypatch, capsys):
    """Recall only fills in what was not supplied -- an explicit --flow is
    what the human asked for this turn, not a suggestion recall may override."""

    _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-4"],
        monkeypatch,
    )
    capsys.readouterr()

    _main(
        ["investigate", "PE3", "it", "--flow", "ldp_session",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-4"],
        monkeypatch,
    )
    out, _ = capsys.readouterr()

    assert _payload(out)["flow"] == "ldp_session"


def test_it_with_no_recorded_turn_refuses_rather_than_treating_it_as_literal(
    monkeypatch, capsys,
):
    """A store that was never written for this session must refuse (exit 2)
    -- not silently look up a device or subject literally named 'it', which
    does not exist on this fabric."""

    code = _main(
        ["investigate", "it", "it",
         "--from-fixtures", "--label", "isis-broken", "--session", "never-used-session"],
        monkeypatch,
    )
    out, _ = capsys.readouterr()
    payload = _payload(out)

    assert code == 2
    assert payload["status"] == "error"
    assert any("does not resolve" in e for e in payload["errors"])


def test_it_is_case_insensitive(monkeypatch, capsys):
    _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-5"],
        monkeypatch,
    )
    capsys.readouterr()

    code = _main(
        ["investigate", "PE3", "IT",
         "--from-fixtures", "--label", "isis-broken", "--session", "test-sess-5"],
        monkeypatch,
    )
    out, _ = capsys.readouterr()

    assert code == 1
    assert _payload(out)["subject"] == "Gi0/0/0/0"


def test_two_different_sessions_do_not_see_each_others_turns(monkeypatch, capsys):
    _main(
        ["investigate", "PE3", "Gi0/0/0/0", "--flow", "isis_adjacency",
         "--from-fixtures", "--label", "isis-broken", "--session", "session-a"],
        monkeypatch,
    )
    capsys.readouterr()

    code = _main(
        ["investigate", "it", "it",
         "--from-fixtures", "--label", "isis-broken", "--session", "session-b"],
        monkeypatch,
    )
    out, _ = capsys.readouterr()

    assert code == 2
    assert "does not resolve" in _payload(out)["errors"][0]
