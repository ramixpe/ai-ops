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
