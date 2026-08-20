"""Drift guards for `docs/build/ON-CALL-RUNBOOK.md` (B-410).

The runbook is written against the system as it actually is *today* --
several claims in it are honest gaps ("this capability exists but nothing
calls it yet") rather than descriptions of a finished feature. A gap like
that is exactly the kind of fact that silently goes stale the moment someone
wires it up, and the runbook would then be actively misleading instead of
merely incomplete -- worse than not having it. These tests don't parse the
markdown (it is prose, written for a human under pressure, not a machine);
instead they pin the underlying code facts the runbook asserts, the same
"match on what the code actually does" discipline `tests/test_docs.py`
already uses for README.md. A failure here means: go re-read
`ON-CALL-RUNBOOK.md` and update the section the failing assertion names.
"""

from __future__ import annotations

import inspect
import re

from agent_nettools import admission, checks, cli, flows, ledger

# --------------------------------------------------------------------------- #
# Section 3 -- the trustworthy/finding table and the exit-code tables.
# --------------------------------------------------------------------------- #


def test_the_documented_finding_vocabulary_is_still_the_whole_vocabulary():
    """The runbook's §3 table lists exactly six terminal findings. If a
    seventh is ever added, this must fail so the table gets a new row."""

    documented = {
        "undetermined",
        "temporally_incoherent",
        "subject_not_found",
        "all_layers_healthy",
        "no_fault_on_path",
        "cause_not_localised",
    }
    assert flows.UNIVERSAL_FINDINGS == documented


def test_exactly_three_findings_make_an_answer_untrustworthy():
    """§3 claims `trustworthy` is False for exactly
    undetermined/temporally_incoherent/subject_not_found and True for every
    other finding. Read straight out of `InvestigationResult.trustworthy`'s
    own source rather than re-implemented here, so this fails the moment a
    fourth `return False` branch is added and nobody updated the table."""

    from agent_nettools import investigation

    source = inspect.getsource(investigation.InvestigationResult.trustworthy.fget)
    matched = set(re.findall(r"self\.descent\.finding == flows\.(\w+)", source))
    assert matched == {"UNDETERMINED", "TEMPORALLY_INCOHERENT", "SUBJECT_NOT_FOUND"}
    # And every one of those constants still spells what the table says.
    assert flows.UNDETERMINED == "undetermined"
    assert flows.TEMPORALLY_INCOHERENT == "temporally_incoherent"
    assert flows.SUBJECT_NOT_FOUND == "subject_not_found"


def test_health_exit_codes_are_ok_warning_critical_in_that_order():
    """§3's exit-code table for `nettools health`: 0/1/2 for
    ok-or-info/warning/critical. Pinned against the real mapping function
    rather than re-typed, so a re-ordering there is caught here too."""

    assert checks.exit_code_for_severity("ok") == 0
    assert checks.exit_code_for_severity("info") == 0
    assert checks.exit_code_for_severity("warning") == 1
    assert checks.exit_code_for_severity("critical") == 2


# --------------------------------------------------------------------------- #
# Section 5 -- silencing exists as a library and has no CLI/env surface yet.
# This is the gap most likely to be closed by a future change; if it is,
# THIS TEST SHOULD FAIL, which is the point -- it is the reminder to rewrite
# §5 rather than leave it describing a gap that no longer exists.
# --------------------------------------------------------------------------- #


def test_health_cli_does_not_yet_apply_silences():
    """`_cmd_health` calls `evaluate_fabric` and nothing that reads a silence
    file. If this ever starts calling `apply_silences`/`apply_silences_to_
    fabric`, §5's "there is no CLI switch for this yet" paragraph is wrong
    and must be rewritten to describe how to use the new switch instead."""

    source = inspect.getsource(cli._cmd_health)
    assert "apply_silences" not in source


def test_no_silence_subcommand_is_registered():
    """§5 says there is no `nettools silence` subcommand. Pinned against the
    parser-building source rather than by trying to invoke the CLI, so this
    stays a cheap, import-only check."""

    source = inspect.getsource(cli)
    assert 'sub.add_parser("silence"' not in source
    assert "NETTOOLS_SILENCE_FILE" not in source


# --------------------------------------------------------------------------- #
# Section 2 -- what a page actually contains today, and the two independent
# ids for one run. Also gap-shaped; also should fail the day it's wired.
# --------------------------------------------------------------------------- #


def test_notify_call_site_does_not_yet_pass_the_rca_fields():
    """§2 says today's `--notify` message is `{finding} — {device} →
    {subject}` and nothing else, because `cli.py`'s call into
    `notifier.notify()` does not pass `cause`/`trustworthy`/`ticket_id` even
    though `notify()` accepts all three. If a future change wires them
    through, this fails and §2 needs rewriting to describe the fuller page."""

    source = inspect.getsource(cli._cmd_investigate)
    # Isolate just the `_notify(...)` call, not the whole (long) function.
    start = source.index("record = _notify(")
    end = source.index(")", source.index("finding=result.descent.finding", start))
    call_text = source[start:end]
    for missing_kwarg in ("cause=", "trustworthy=", "ticket_id="):
        assert missing_kwarg not in call_text, (
            f"{missing_kwarg} now appears in --notify's call to notify() -- "
            "ON-CALL-RUNBOOK.md section 2 describes the OLD, narrower page "
            "and must be updated"
        )


def test_the_ticket_is_opened_before_the_ledger_write_and_shares_its_run_id():
    """§2/§7's premise changed by design in the release-1.0 cleanup: wiring
    every unwired capability (the operator's own instruction) included
    threading the ticket's run_id into the ledger row, so a diagnosis and
    its ticket can be joined later (`ticket_read.find_ticket_path_by_run_id`,
    used by `nettools ledger verdict` to mirror a human's verdict into the
    ticket's Outcome section). The ticket must therefore now open FIRST, so
    its run_id exists in time to pass to the ledger call -- the reverse of
    this test's pre-cleanup assertion."""

    source = inspect.getsource(cli._cmd_investigate)
    ledger_call = source.index("_record_diagnosis_in_ledger(")
    ticket_open = source.index("_open_ticket_for(")
    assert ticket_open < ledger_call

    call_span = source[ledger_call: source.index(")\n", ledger_call)]
    assert "run_id=" in call_span


def test_record_diagnosis_has_no_outcome_parameter():
    """§7's central claim: the tool records WHAT it diagnosed; only a human,
    later, via `nettools ledger verdict`, records whether it was right.
    There is no `outcome` parameter on the write path at all."""

    params = inspect.signature(ledger.record_diagnosis).parameters
    assert "outcome" not in params


def test_ledger_verdict_outcomes_match_the_documented_three():
    """§7's `nettools ledger verdict ID {confirmed_correct,incorrect,unknown}`
    -- pinned against the ledger's own vocabulary, not re-typed."""

    assert ledger.OUTCOMES == ("confirmed_correct", "incorrect", "unknown")


# --------------------------------------------------------------------------- #
# Section 3 -- the measured admission defaults quoted in passing (§ "read
# this before you script anything" leans on admission behaving as measured).
# --------------------------------------------------------------------------- #


def test_admission_defaults_match_the_measured_values():
    assert admission.DEFAULT_MAX_CONCURRENT_PER_DEVICE == 1
    assert admission.DEFAULT_MAX_CONCURRENT_FABRIC == 4
