"""Golden cases for the correlate prompt, and the window shaping (T-028).

Shape and citation integrity only. No test asserts prose.

Two golden scenarios, both from committed fixtures:

* **correlating events present** — PE2's `broken` window, which contains the
  complete timestamped story of the isolation: both uplinks going
  administratively down, both IS-IS adjacencies dropping, a configuration
  commit in the same second, and BGP following 154 seconds later when the hold
  timer expired.
* **the refusal path** — PE2's `healthy` window, which after filtering contains
  **zero** network events. The honest answer is "no correlating events in
  window", and the failure to guard against is a model reaching for the
  nearest line in time and describing it as related.

The second is the more likely case in practice, which is why it is pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_nettools import log_window, template_parsers
from agent_nettools.checks import CheckResult
from agent_nettools.descent import DescentResult, RungOutcome
from agent_nettools.prompt_library import (
    build_correlate_prompt,
    load_prompt,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cisco_xr"
CASES_FILE = (
    Path(__file__).resolve().parent.parent
    / "prompts" / "tests" / "cases" / "correlate.cases.json"
)


def _flat(text: str) -> str:
    """Collapse whitespace before matching, so a rewrap cannot fail a test."""

    return " ".join(text.split())


def _case(name: str) -> dict:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    return next(c for c in cases if c["name"] == name)


def _records(device: str, label: str) -> list[dict]:
    raw = (FIXTURES / device / label / "show-logging-last-200.txt").read_text()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return parsed["records"]


def _finding() -> DescentResult:
    """A minimal `interface_line_down` result -- correlation needs the finding,
    not the whole chain."""

    outcome = RungOutcome(
        "interface", "PE2",
        CheckResult("broken", reason="both uplinks admin-down", subject="Gi0/0/0/0",
                    evidence_keys=("PE2:interface:Gi0/0/0/0",)),
    )
    return DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="interface_line_down", outcomes=(outcome,),
        evidence_keys=("PE2:interface:Gi0/0/0/0",),
    )


# --------------------------------------------------------------------------- #
# Window shaping -- the decision to filter in code, measured
# --------------------------------------------------------------------------- #


def test_shaping_removes_the_collector_noise_that_dominates_a_real_window():
    expect = _case("correlating_events")["window"]
    shaped = log_window.shape_window(_records("PE2", "broken"))

    assert shaped.total_in == expect["entries_collected"]
    assert len(shaped.records) == expect["entries_retained"]
    assert shaped.collector_noise_removed == expect["collector_noise_removed"]


def test_dedupe_is_a_no_op_on_a_device_buffer_and_kept_deliberately():
    """A measured correction to OBS-014.

    That finding saw one event stored 1,346 times and concluded any count is
    fiction without deduplication. True of Loki; **not** true of the device's
    own buffer, where deduplicating removes exactly zero records. The
    duplication is introduced by the syslog pipeline, not present at source.

    `dedupe` is kept because B-206 will read the same records back out of Loki,
    where the duplication is real -- a no-op on this source and load-bearing on
    the next. Pinned so nobody deletes it as dead code.
    """

    records = _records("PE2", "broken")
    assert len(log_window.dedupe(records)) == len(records)


def test_shaping_reports_what_it_removed():
    """Because filtering is code, what was dropped is knowable. A report can
    then say "20 of 200 were network events" rather than presenting 20 and
    implying that was all there was."""

    shaped = log_window.shape_window(_records("PE2", "broken"))
    summary = shaped.summary()

    assert str(len(shaped.records)) in summary
    assert str(shaped.total_in) in summary
    assert "collector-generated" in summary


def test_subject_filtering_is_off_by_default_and_would_discard_the_cause():
    """The events that explain an isolated peer never name the peer.

    On the `broken` label the causal lines name `GigabitEthernet0/0/0/0` and
    `P1`, not `10.255.0.12`. Filtering the window to the subject would throw
    away exactly the evidence correlation needs -- so it is available and not
    applied by default.
    """

    records = _records("PE2", "broken")
    unfiltered = log_window.shape_window(records)
    by_subject = log_window.shape_window(records, subject="10.255.0.12")

    assert len(unfiltered.records) > len(by_subject.records)
    assert by_subject.unrelated_removed > 0


# --------------------------------------------------------------------------- #
# Golden case 1 -- correlating events present
# --------------------------------------------------------------------------- #


def test_the_broken_window_contains_the_whole_causal_story():
    """What makes this fabric's `show logging` fallback worth having."""

    shaped = log_window.shape_window(_records("PE2", "broken"))
    mnemonics = {r["mnemonic"] for r in shaped.records}
    expect = _case("correlating_events")["expect"]

    for required in expect["mnemonics_present"]:
        assert any(m.startswith(required) for m in mnemonics), required


def test_the_window_carries_a_config_commit_the_model_can_correlate_against():
    """D6's stated value of the historical axis: "whether it coincided with a
    commit". It did -- in the same second."""

    shaped = log_window.shape_window(_records("PE2", "broken"))
    commits = [r for r in shaped.records if r["facility"] == "MGBL-CONFIG"]

    assert commits, "no configuration commit in the window"
    shuts = [r for r in shaped.records
             if "Administratively Down" in r["text"] and r["timestamp"].startswith("Aug 16")]
    assert shuts
    # The same whole second: the shut is at ...54.688 and the commit at
    # ...54.712. Slice to the second, not into the milliseconds -- the
    # sub-second ordering is real but it is the model's to describe, not this
    # test's to pin.
    to_the_second = len("Aug 16 07:41:54")
    assert shuts[0]["timestamp"][:to_the_second] == commits[-1]["timestamp"][:to_the_second]


def test_the_correlate_prompt_renders_with_device_timestamps():
    shaped = log_window.shape_window(_records("PE2", "broken"))
    prompt = build_correlate_prompt(_finding(), shaped)

    assert "{finding_json}" not in prompt and "{window_json}" not in prompt
    assert "interface_line_down" in prompt
    assert "Aug 16 07:41:54.688 UTC" in prompt, "device timestamps must reach the model verbatim"


# --------------------------------------------------------------------------- #
# Golden case 2 -- the refusal path
# --------------------------------------------------------------------------- #


def test_a_healthy_window_has_no_correlating_events_at_all():
    """The refusal case, from real data rather than construction."""

    shaped = log_window.shape_window(_records("PE2", "healthy"))
    expect = _case("no_correlating_events")["expect"]

    assert len(shaped.records) == expect["entries_retained"]
    assert shaped.is_empty is expect["is_empty"]


def test_the_prompt_forbids_reaching_for_the_nearest_event_in_time():
    """The failure this case guards against.

    A model that presents an unrelated event as context produces something
    indistinguishable from a correlation, and the reader cannot tell -- the
    same silent-degradation shape as OBS-006, OBS-043 and OBS-044, arriving in
    the timeline.
    """

    prompt = _flat(load_prompt("correlate", 1))

    assert "no correlating events in window" in prompt
    assert "Do not reach for the nearest event in time" in prompt
    assert "worse than no timeline" in prompt


def test_an_empty_window_is_distinguished_from_nothing_having_happened():
    """"Nothing was recorded" and "nothing happened" are different claims."""

    prompt = _flat(load_prompt("correlate", 1))
    assert "not evidence that nothing happened" in prompt


# --------------------------------------------------------------------------- #
# The timestamp constraint
# --------------------------------------------------------------------------- #


def test_the_grounding_slot_states_the_device_versus_ingest_time_constraint():
    """Requirement, not implementation detail.

    Ingest time is when the collector noticed; device time is when the network
    changed. A timeline built on arrival time is confidently wrong and nothing
    downstream can detect it -- the ordering looks plausible and the causality
    is invented.
    """

    prompt = _flat(load_prompt("correlate", 1))

    assert "device's own clock" in prompt
    assert "not the time the network changed" in prompt
    assert "confidently wrong" in prompt


def test_the_prompt_treats_a_commit_as_coincident_not_causal():
    prompt = _flat(load_prompt("correlate", 1))
    assert "coincidence in time, not a proven cause" in prompt


def test_the_expected_output_schema_is_specified():
    prompt = _flat(load_prompt("correlate", 1))
    for key in ("timeline", "correlation", "followed_a_commit", "recurrence"):
        assert key in prompt


def test_the_anchor_example_is_valid_json():
    prompt = load_prompt("correlate", 1)
    start = prompt.index('{\n  "timeline"')
    depth, end = 0, None
    for i, ch in enumerate(prompt[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            end = i + 1
            break
    anchor = json.loads(prompt[start:end])

    assert anchor["correlation"]["found"] is True
    assert anchor["timeline"]
    for entry in anchor["timeline"]:
        assert entry["at"] and entry["mnemonic"]


@pytest.mark.parametrize("case", ["correlating_events", "no_correlating_events"])
def test_both_golden_cases_are_declared(case):
    assert _case(case)["expect"]
