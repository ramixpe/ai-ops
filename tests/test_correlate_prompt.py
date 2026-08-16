"""Golden cases for the correlate prompt, and the window shaping (T-028).

Shape and citation integrity only. No test asserts prose.

Two golden scenarios, both from committed fixtures:

* **correlating events present** — PE2's `broken` window, which contains the
  complete timestamped story of the isolation: both uplinks going
  administratively down, both IS-IS adjacencies dropping, a configuration
  commit in the same second, and BGP following 154 seconds later when the hold
  timer expired.
* **the refusal path** — PE2's `healthy` window, which after filtering contains
  nine records and not one of them bears on a BGP session. The honest answer is
  "no correlating events in the available coverage", and the failure to guard
  against is a model reaching for the nearest line in time and describing it as
  related.

The second is the more likely case in practice, which is why it is pinned.

Also here: the failure modes `docs/design/evidence-reduction.md` §7 names, each
tested against a corpus whose correct answer is known in advance.
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
CORRELATE_VERSION = 3

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
    assert shaped.unattributed_kept == expect["unattributed_kept"]


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
    then say "28 of 200 were network events" rather than presenting 28 and
    implying that was all there was -- and can say how many it *declined* to
    drop, which is the honest half of an aggressive filter."""

    shaped = log_window.shape_window(_records("PE2", "broken"))
    summary = shaped.summary()

    assert str(len(shaped.records)) in summary
    assert str(shaped.total_in) in summary
    assert "collector-generated" in summary
    assert "source could not be established" in summary


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
    # B-421: build_correlate_prompt returns a RenderedPrompt (system/user
    # split, so the static half is cacheable); system + user is the same
    # text one fully-rendered prompt used to be.
    rendered = build_correlate_prompt(_finding(), shaped, version=CORRELATE_VERSION)
    prompt = f"{rendered.system}\n\n{rendered.user}"

    assert "{finding_json}" not in prompt and "{window_json}" not in prompt
    assert "interface_line_down" in prompt
    assert "Aug 16 07:41:54.688 UTC" in prompt, "device timestamps must reach the model verbatim"


def test_invariant_4_holds_in_each_half_of_the_split_separately():
    """Not just the concatenation.

    B-421 splits the rendered prompt across two different fields of the
    Anthropic request (`system`, the cached block, and `user`, the one
    volatile message) -- a raw-output leak into either half individually is
    the failure that would actually reach the model. The markers here are the
    raw `show logging` preamble, never present in a parsed record -- the
    device timestamps checked above (`Aug 16 07:41:54.688 UTC`) are legitimate
    in `user`, since they are parsed log-line fields re-serialised, not raw
    command output; see `test_end_to_end_offline.py` for the same distinction
    made explicitly.
    """

    shaped = log_window.shape_window(_records("PE2", "broken"))
    rendered = build_correlate_prompt(_finding(), shaped, version=CORRELATE_VERSION)

    for half_name, half in (("system", rendered.system), ("user", rendered.user)):
        for marker in ("RP/0/RP0/CPU0", "Log Buffer (4194303 bytes)"):
            assert marker not in half, f"raw device output leaked into {half_name}: {marker!r}"


# --------------------------------------------------------------------------- #
# B-421 -- the system/user split that makes prompt caching possible
# --------------------------------------------------------------------------- #


def test_the_system_half_is_byte_identical_across_two_different_windows():
    """The whole point of the split. Anthropic's prompt cache is a prefix
    match on `system`: if the static half ever differed between two
    investigations at the same prompt version, nothing would be cacheable.

    The `broken` and `healthy` windows are as different as this fixture pair
    gets -- one carries the whole causal story, the other nine unattributed
    session events -- so this is not passing by accident of two similar
    payloads.
    """

    broken_window = log_window.shape_window(_records("PE2", "broken"))
    healthy_window = log_window.shape_window(_records("PE2", "healthy"))

    a = build_correlate_prompt(_finding(), broken_window, version=CORRELATE_VERSION)
    b = build_correlate_prompt(_finding(), healthy_window, version=CORRELATE_VERSION)

    assert a.system == b.system


def test_the_user_half_differs_between_two_different_windows():
    """The companion assertion. If `user` never changed either, the split
    would be hiding a bug where nothing volatile ever reaches the model at
    all -- every correlation would be written from the same window."""

    broken_window = log_window.shape_window(_records("PE2", "broken"))
    healthy_window = log_window.shape_window(_records("PE2", "healthy"))

    a = build_correlate_prompt(_finding(), broken_window, version=CORRELATE_VERSION)
    b = build_correlate_prompt(_finding(), healthy_window, version=CORRELATE_VERSION)

    assert a.user != b.user


def test_nothing_is_lost_in_the_reordering():
    """The split moves text between `system` and `user`; it must not drop any
    of it. Every GRACE slot the template names -- including the three
    sub-headings the payload placeholders sit under -- and the substituted
    payloads themselves must still be present somewhere in system + user."""

    shaped = log_window.shape_window(_records("PE2", "broken"))
    rendered = build_correlate_prompt(_finding(), shaped, version=CORRELATE_VERSION)
    combined = rendered.system + rendered.user

    for slot in (
        "GROUNDING", "COVERAGE", "FINDING", "LOG WINDOW",
        "ROLE", "ANCHORS", "CONSTRAINTS", "EXPECTED OUTPUT",
    ):
        assert slot in combined
    assert "interface_line_down" in combined, "the payload itself must survive the split"


# --------------------------------------------------------------------------- #
# Golden case 2 -- the refusal path
# --------------------------------------------------------------------------- #


def test_a_healthy_window_has_no_correlating_events_at_all():
    """The refusal case, from real data rather than construction.

    Not an empty window. Nine records survive, every one of them a session
    failure the noise filter declined to attribute -- and at severity 3 they
    are the *highest*-severity records on the device. The model is shown the
    scariest lines in the buffer and must still answer `found: false`, which is
    a far better test of constraint 5 than an empty list was.
    """

    shaped = log_window.shape_window(_records("PE2", "healthy"))
    expect = _case("no_correlating_events")["expect"]

    assert len(shaped.records) == expect["entries_retained"]
    assert shaped.is_empty is expect["is_empty"]
    assert shaped.unattributed_kept == len(shaped.records)
    assert {r["facility"] for r in shaped.records} == {"SECURITY-SSHD_SYSLOG_PRX"}
    assert {r["severity"] for r in shaped.records} == {"3"}


def test_an_empty_record_list_is_still_an_empty_window():
    """Constraint 6, kept covered now that the `healthy` label is not empty."""

    expect = _case("empty_window")["expect"]
    shaped = log_window.shape_window([])

    assert len(shaped.records) == expect["entries_retained"]
    assert shaped.is_empty is expect["is_empty"]


# --------------------------------------------------------------------------- #
# evidence-reduction.md §7 -- the named failure modes
# --------------------------------------------------------------------------- #


def test_noise_filtering_attributes_rather_than_assuming():
    """§7, noise filter over-reach -- the mode this implementation failed.

    A corpus containing both: the collector's own session churn, and a session
    failure from an address that is not the collector's. Dropping by facility
    passes every other test in this file and deletes the second one.
    """

    collector = {
        "timestamp": "Aug 16 07:41:20.000 UTC", "facility": "SECURITY-SSHD_SYSLOG_PRX",
        "mnemonic": "SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL", "severity": "6",
        "text": "sshd[1]: Connection closed by 172.20.250.2 port 52574",
    }
    genuine = {
        "timestamp": "Aug 16 07:41:21.000 UTC", "facility": "SECURITY-SSHD_SYSLOG_PRX",
        "mnemonic": "SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL", "severity": "3",
        "text": "sshd[2]: error: maximum authentication attempts exceeded for root from 10.0.9.9 port 40001",
    }
    unattributable = {
        "timestamp": "Aug 16 07:41:22.000 UTC", "facility": "SECURITY-SSHD_SYSLOG_PRX",
        "mnemonic": "SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL", "severity": "3",
        "text": "sshd[3]: kex_exchange_identification: Connection closed by remote host",
    }

    shaped = log_window.shape_window([collector, genuine, unattributable])

    assert [r["text"] for r in shaped.records] == [genuine["text"], unattributable["text"]]
    assert shaped.collector_noise_removed == 1
    assert shaped.unattributed_kept == 2


def test_a_singleton_survives_a_window_of_thousands_of_repeats():
    """§7, over-aggregation -- the mode the document calls out hardest.

    One `ROUTING-BGP-5-ADJCHANGE` among three thousand routine records. Any
    reduction with a minimum-count threshold, or one that keys aggregation on
    the mnemonic without preserving membership, loses exactly this line.
    """

    routine = [
        {
            "timestamp": f"Aug 16 07:{m:02d}:{s:02d}.000 UTC",
            "facility": "SECURITY-SSHD_SYSLOG_PRX", "severity": "6",
            "mnemonic": "SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL",
            "text": f"sshd[{m * 60 + s}]: Connection closed by 172.20.250.2 port {40000 + m * 60 + s}",
        }
        for m in range(50) for s in range(60)
    ]
    critical = {
        "timestamp": "Aug 16 07:44:28.097 UTC", "facility": "ROUTING-BGP", "severity": "5",
        "mnemonic": "ROUTING-BGP-5-ADJCHANGE",
        "text": "neighbor 10.255.0.31 Down - Hold timer expired",
    }
    corpus = routine[:1500] + [critical] + routine[1500:]
    assert len(corpus) == 3001

    shaped = log_window.shape_window(corpus)

    assert [r["mnemonic"] for r in shaped.records] == ["ROUTING-BGP-5-ADJCHANGE"]
    assert shaped.total_in == 3001


def test_two_distinct_events_never_collapse_into_one():
    """§7, template collision.

    Today the only step that can merge records is `dedupe`, and the mnemonic is
    in its key so two event types cannot merge however similar their text. The
    assertion the document asks for -- distinct mnemonics never share a
    template ID -- is this, in the form the current implementation has.
    """

    shared_text = "neighbor 10.255.0.31 Down"
    a = {"timestamp": "Aug 16 07:44:28.097 UTC", "mnemonic": "ROUTING-BGP-5-ADJCHANGE",
         "facility": "ROUTING-BGP", "severity": "5", "text": shared_text}
    b = {**a, "mnemonic": "ROUTING-BGP-5-NBR_RESET", "text": shared_text}

    assert len(log_window.dedupe([a, b])) == 2
    assert len(log_window.dedupe([a, dict(a)])) == 1


def test_every_noise_rule_declares_how_it_attributes_a_record():
    """The §0.10 discipline, applied to filtering.

    A rule that cannot say what produced a record has no business deleting it,
    and `describe()` is what puts that in a report rather than in a comment.
    """

    for rule in log_window.COLLECTOR_NOISE:
        assert rule.reason and rule.facility
        assert rule.attribution.value in rule.describe()
        assert rule.reason in rule.describe()

    with pytest.raises(ValueError):
        log_window.NoiseRule(
            facility="X", attribution=log_window.Attribution.SOURCE_ADDRESS, reason="y",
        )
    with pytest.raises(ValueError):
        log_window.NoiseRule(
            facility="X", attribution=log_window.Attribution.GENERATING_PROCESS, reason="y",
        )


def test_no_lab_device_address_is_treated_as_a_collector_source():
    """The rule that keeps source attribution honest.

    `COLLECTOR_SOURCES` is an enumerated set rather than a subnet test because
    the devices' own management interfaces sit in the same /24. A subnet rule
    would attribute a device-sourced session to the collector -- and would do
    it silently, which is the whole failure class this module exists to avoid.
    """

    from agent_nettools import lab

    device_addresses = set(lab.DEVICES.values())
    assert len(device_addresses) == 9
    assert not (device_addresses & log_window.COLLECTOR_SOURCES)


def test_the_prompt_forbids_reaching_for_the_nearest_event_in_time():
    """The failure this case guards against.

    A model that presents an unrelated event as context produces something
    indistinguishable from a correlation, and the reader cannot tell -- the
    same silent-degradation shape as OBS-006, OBS-043 and OBS-044, arriving in
    the timeline.
    """

    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))

    assert "no correlating events in the available coverage" in prompt
    assert "Do not reach for the nearest event in time" in prompt
    assert "worse than no timeline" in prompt


def test_an_empty_window_is_distinguished_from_nothing_having_happened():
    """"Nothing was recorded" and "nothing happened" are different claims."""

    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))
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

    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))

    assert "device's own clock" in prompt
    assert "not the time the network changed" in prompt
    assert "confidently wrong" in prompt


def test_the_prompt_says_a_retained_entry_is_not_thereby_a_relevant_one():
    """What v2 exists for.

    Correcting the noise filter put nine severity-3 lines in front of the model
    on a healthy device. The filter is right to keep them -- nothing attributes
    them to this tool -- but the model must not read retention as relevance, and
    severity is the exact hook it would reach for. Both halves are stated.
    """

    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))

    assert "Do not treat retention as relevance" in prompt
    assert "an unattributed line is not a correlation" in prompt
    assert "A high-severity entry is not thereby a relevant one" in prompt


def test_the_superseded_version_is_still_loadable_and_still_says_what_it_said():
    """Rule 2's point. v1 produced no report, but the mechanism that makes a
    report attributable is only real if the old text is still there."""

    v1 = _flat(load_prompt("correlate", 1))
    assert "duplicates have been collapsed" in v1
    assert "Do not treat retention as relevance" not in v1


def test_the_prompt_treats_a_commit_as_coincident_not_causal():
    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))
    assert "coincidence in time, not a proven cause" in prompt


def test_the_expected_output_schema_is_specified():
    prompt = _flat(load_prompt("correlate", CORRELATE_VERSION))
    for key in ("timeline", "correlation", "followed_a_commit", "recurrence"):
        assert key in prompt


def test_the_anchor_example_is_valid_json():
    prompt = load_prompt("correlate", CORRELATE_VERSION)
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
