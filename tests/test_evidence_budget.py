"""Evidence budget: per-intent/total character ceilings, middle-truncation, and
preference for parsed structures over raw command text."""

from __future__ import annotations

from agent_nettools.evidence_budget import (
    budget_device_evidence,
    budget_fabric_evidence,
    render_budgeted_evidence,
)
from agent_nettools.network_tools import STATUS_SUCCESS
from agent_nettools.parsers import PARSE_FAILED, PARSE_OK


def _section(*, parse_status: str, parsed=None, commands=None, status: str = STATUS_SUCCESS) -> dict:
    return {
        "status": status,
        "data": {"parse_status": parse_status, "parsed": parsed, "commands": commands or {}},
    }


def test_under_budget_text_is_untouched():
    from agent_nettools.evidence_budget import _truncate_middle

    text, omitted = _truncate_middle("short text", budget=1000)

    assert text == "short text"
    assert omitted == 0


def test_over_budget_text_is_truncated_in_the_middle_with_a_marker():
    from agent_nettools.evidence_budget import _truncate_middle

    text = "".join(f"line-{i}\n" for i in range(2000))
    truncated, omitted = _truncate_middle(text, budget=200)

    assert omitted > 0
    assert len(truncated) < len(text)
    assert f"[TRUNCATED: {omitted} characters omitted]" in truncated
    # The head and the tail of the original text must both still be present --
    # this is what distinguishes middle-truncation from tail-truncation.
    assert text[:10] in truncated
    assert text[-10:] in truncated


def test_budget_device_evidence_prefers_parsed_over_raw():
    evidence = {
        "bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"neighbor": "10.0.0.1"}]}),
    }

    sections, report = budget_device_evidence("PE1", evidence, per_intent_chars=4000)

    assert report == []
    assert "10.0.0.1" in sections["bgp"]
    # Compact JSON, not a whitespace-padded CLI table.
    assert '"neighbor":"10.0.0.1"' in sections["bgp"]


def test_budget_device_evidence_withholds_raw_output_when_parsing_failed():
    """Pins the corrected behaviour, replacing the raw-fallback assertion this
    test used to make (B-467/B-470, DEEP-REVIEW-2026-08-17 §2.1).

    Before this change, a parse failure made ``_section_text`` fall back to
    the *raw* command output -- by prior test-pinned design, this was one of
    three paths that violated invariant 4 ("no unparsed device text ever
    reaches a model"). This is the one test this task's spec permits
    rewriting rather than adding a new one beside it, because the old
    expectation was itself the pinned bug.

    The corrected contract: raw device text is never returned, even on parse
    failure. The model gets a withheld-commands record (chars/lines, never
    the text) via ``model_egress.project_envelope`` instead -- "unavailable,
    and why", the same shape the sanitised MCP surface already gives for the
    same failure.
    """

    evidence = {
        "bgp": _section(
            parse_status=PARSE_FAILED,
            commands={"show bgp summary": "garbled output that failed to parse"},
        ),
    }

    sections, _report = budget_device_evidence("PE1", evidence, per_intent_chars=4000)

    assert "garbled output that failed to parse" not in sections["bgp"]
    assert "commands_withheld" in sections["bgp"]
    assert '"show bgp summary"' in sections["bgp"]
    # The withheld record still carries an honest size, so a reader can tell
    # a command that produced output apart from one that produced nothing.
    assert '"chars":35' in sections["bgp"].replace(" ", "")


def test_budget_device_evidence_reports_per_intent_truncation():
    huge = "x" * 10000
    evidence = {
        "bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"blob": huge}]}),
        "isis": _section(parse_status=PARSE_OK, parsed={"records": []}),
    }

    sections, report = budget_device_evidence("PE1", evidence, per_intent_chars=500)

    assert len(sections["bgp"]) <= 500
    assert "[TRUNCATED:" in sections["bgp"]
    entries = {(e["device"], e["intent"]) for e in report}
    assert ("PE1", "bgp") in entries
    assert ("PE1", "isis") not in entries  # small section, never touched.


def test_budget_fabric_evidence_enforces_a_total_ceiling_across_devices():
    huge = "y" * 5000
    evidence_by_device = {
        "PE1": {"bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"blob": huge}]})},
        "PE2": {"bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"blob": huge}]})},
        "PE3": {"bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"blob": huge}]})},
    }

    per_device, report = budget_fabric_evidence(
        evidence_by_device, per_intent_chars=4000, total_chars=1000
    )

    total = sum(len(text) for sections in per_device.values() for text in sections.values())
    assert total <= 1000 + 200  # small slack for markers/formatting, still far below 12000 raw.
    assert report  # something was reported as truncated.


def test_render_budgeted_evidence_includes_every_device_and_intent():
    per_device = {"PE1": {"bgp": "bgp text", "isis": "isis text"}}

    rendered = render_budgeted_evidence(per_device)

    assert "### PE1" in rendered
    assert "#### bgp" in rendered
    assert "bgp text" in rendered
    assert "#### isis" in rendered
    assert "isis text" in rendered


def test_env_vars_override_the_default_budgets(monkeypatch):
    monkeypatch.setenv("NETTOOLS_EVIDENCE_PER_INTENT_CHARS", "50")
    huge = "z" * 500
    evidence = {"bgp": _section(parse_status=PARSE_OK, parsed={"records": [{"blob": huge}]})}

    sections, report = budget_device_evidence("PE1", evidence)

    assert len(sections["bgp"]) <= 50
    assert report
