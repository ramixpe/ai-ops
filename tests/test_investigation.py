"""The MVP-0 runner, end to end (T-030).

Driven against the real fixture labels with a **scripted analyst** — a callable
that returns a fixed string for each prompt it is handed. No model, no network,
fully deterministic.

That is deliberate rather than a compromise. The thing under test is the
*wiring*: does the descent's cause pick the device the window is read from, does
a bad report get discarded rather than flagged, does a coverage shortfall
downgrade instead of reject. A real model would make every one of those
assertions probabilistic while testing none of them better.

The analyst is scripted to produce reports a model plausibly produces —
including the good one, the one that drops the chain, the one wrapped in a
markdown fence the prompt forbids, and the one that is not JSON at all.
"""

from __future__ import annotations

import json

import pytest
from helpers import set_device_environment

from agent_nettools import investigation
from agent_nettools.fixtures import fixture_sender
from agent_nettools.grounding import observation_labels
from agent_nettools.investigation import (
    COVERAGE_LIMITED,
    EMITTED,
    NOT_ATTEMPTED,
    WITHHELD,
    investigate,
)

_OWNER = {"10.255.0.12": "PE2", "10.255.0.31": "RR1"}

#: The descent under test, run once with no model. Also the anti-vacuity
#: anchor: if this ever stops being a five-rung descent with a four-rung chain,
#: half the assertions below become trivially satisfiable, so
#: `test_the_descent_runs_alone_and_says_so` pins it explicitly.
_BROKEN_DESCENT = None


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    set_device_environment(monkeypatch)


@pytest.fixture(autouse=True)
def _descent_under_test(_credentials):
    """One model-free descent, shared by the scripted reports below."""

    global _BROKEN_DESCENT
    if _BROKEN_DESCENT is None:
        _BROKEN_DESCENT = _run("broken").descent
    return _BROKEN_DESCENT


def _run(label, analyst=None, **kwargs):
    return investigate(
        "RR1", "10.255.0.12", flow="bgp_session", analyst=analyst,
        resolver=lambda s: _OWNER[s], sender=fixture_sender(label=label), **kwargs
    )


class Scripted:
    """Returns a canned response per prompt kind, and records what it was asked.

    B-421: `analyst` now receives a `prompt_library.RenderedPrompt` (the
    system/user split), not a plain string -- `build_report_prompt`/
    `build_correlate_prompt` return the split object so `complete_prompt` can
    cache the static half. `self.prompts` still records plain text, though:
    every assertion in this file that reads it (`"LOG WINDOW" in p`, `len(p)`)
    only cares about substrings and length, and `system + user` in that order
    is exactly the text a single fully-rendered prompt used to be -- nothing
    downstream needs to change to keep meaning what it meant.
    """

    def __init__(self, *, report=None, correlate=None):
        self._report, self._correlate = report, correlate
        self.prompts: list[str] = []

    def __call__(self, prompt) -> str:
        text = f"{prompt.system}\n\n{prompt.user}"
        self.prompts.append(text)
        which = self._correlate if "LOG WINDOW" in text else self._report
        return which(text) if callable(which) else (which or "{}")


def _good_report(prompt: str) -> str:
    """One observation per rung, an interpretation citing the whole chain.

    Derived from the descent the prompt was rendered from, rather than
    hardcoded, so it stays a *correct* report if the descent changes shape. A
    hardcoded one would start failing for the wrong reason and be "fixed" by
    loosening the assertion it was supposed to be making.
    """

    descent = _BROKEN_DESCENT
    observations = [
        {"claim": f"{o.rung} on {o.device} is {o.status}",
         "evidence_key": o.result.evidence_keys[0]}
        for o in descent.outcomes
    ]
    return json.dumps({
        "observations": observations,
        "interpretations": [{
            "claim": "The admin-down uplinks isolated PE2.",
            "based_on": list(observation_labels(len(observations))),
        }],
        "recommendation": {"next_check": "confirm the shutdown was intended",
                           "requires_human": True},
    })


_FOUND = json.dumps({
    "timeline": [{"at": "Aug 16 07:41:54.688 UTC", "event": "uplinks down",
                  "mnemonic": "PKT_INFRA-LINK-5-CHANGED"}],
    "correlation": {"found": True, "summary": "the uplinks went down",
                    "followed_a_commit": True, "recurrence": "once"},
})

_NOT_FOUND = json.dumps({
    "timeline": [],
    "correlation": {"found": False,
                    "summary": "no correlating events in the available coverage",
                    "followed_a_commit": False, "recurrence": "none"},
})


# --------------------------------------------------------------------------- #
# The descent is the product, and it needs no model
# --------------------------------------------------------------------------- #


def test_the_descent_runs_alone_and_says_so():
    """No model is a mode, not a degraded run.

    A descent found the cause with no model anywhere in it -- that is the claim
    the whole layer rests on, and the runner must be able to deliver it.
    """

    result = _run("broken")

    assert result.finding == "interface_line_down"
    assert result.descent.cause.device == "PE2"
    assert len(result.descent.causal_chain) == 4
    # Changed at B-439, and the change is the point of that item. The report
    # used to be NOT_ATTEMPTED without a model. It is now **rendered from the
    # descent and always present**, so `--no-model` produces a complete answer
    # rather than a reduced one -- the deterministic half was always the half
    # that found the cause, and now it is the half that reports it too.
    assert result.report_status == EMITTED
    assert result.report is not None and result.report["authoritative"] is True
    assert result.report["generated_by"] == "code"
    assert result.report["finding"] == "interface_line_down"

    # No model ran, so there is no paraphrase. Still NOT_ATTEMPTED, still not a
    # failure -- that half of the original property is unchanged.
    assert result.paraphrase is None and result.paraphrase_status == NOT_ATTEMPTED

    # The timeline is rendered too, and needs no model either.
    assert result.correlation is not None
    assert result.correlation["generated_by"] == "code"
    assert result.correlation_paraphrase is None
    assert "interface_line_down on PE2" in result.summary()


def test_a_healthy_fabric_descends_clean():
    result = _run("healthy")

    assert result.finding == "all_layers_healthy"
    assert result.descent.cause is None


# --------------------------------------------------------------------------- #
# Rule 1 -- a report that fails grounding does not leave the module
# --------------------------------------------------------------------------- #


def test_a_grounded_report_is_emitted():
    analyst = Scripted(report=_good_report, correlate=_FOUND)
    result = _run("broken", analyst)

    assert result.paraphrase_status == EMITTED
    assert result.paraphrase is not None
    assert result.paraphrase_grounding.ok
    assert result.paraphrase_grounding.rungs_covered == 5


def test_a_report_that_drops_the_chain_is_withheld_and_its_prose_is_gone():
    """The failure `ground_report` exists for, arriving through the runner.

    Citation-perfect, and it throws away the four rungs that make the answer an
    argument. The assertion that matters is the second one: the prose is not
    in the result at all, so a caller cannot print it by mistake.
    """

    invented = "the interface is down and somebody should look at it"
    # The cause's *real* evidence key, so this report is citation-perfect and
    # the only thing wrong with it is the omission. A made-up key would fail
    # for the ordinary reason and test nothing new.
    real_key = _BROKEN_DESCENT.cause.result.evidence_keys[0]
    chain_dropped = json.dumps({
        "observations": [{"claim": invented, "evidence_key": real_key}],
        "interpretations": [{"claim": invented, "based_on": ["obs-1"]}],
        "recommendation": {"next_check": invented, "requires_human": True},
    })
    result = _run("broken", Scripted(report=chain_dropped, correlate=_FOUND))

    assert result.paraphrase_status == WITHHELD
    assert result.paraphrase is None
    assert invented not in json.dumps(result.withheld_because())
    assert {f.kind for f in result.paraphrase_grounding.failures} == {"uncited_rung"}


def test_an_invented_evidence_key_withholds_the_report():
    bad = json.dumps({
        "observations": [{"claim": "x", "evidence_key": "PE2:interface:Nonsense0/0/0/9"}],
        "interpretations": [{"claim": "y", "based_on": ["obs-1"]}],
    })
    result = _run("broken", Scripted(report=bad, correlate=_FOUND))

    assert result.paraphrase is None
    assert any(f.kind == "invented_evidence_key" for f in result.paraphrase_grounding.failures)


@pytest.mark.parametrize(
    "response",
    ["not json at all", "", "[]", '{"observations": "nope"}'],
    ids=["prose", "empty", "list", "wrong-type"],
)
def test_a_malformed_model_response_is_a_withheld_report_not_a_crash(response):
    """Model output arrives over a network. A traceback in the emit path is an
    outage where a refusal was the correct answer."""

    result = _run("broken", Scripted(report=response, correlate=_FOUND))

    assert result.paraphrase_status == WITHHELD
    assert result.paraphrase is None
    assert result.descent.finding == "interface_line_down", "the descent still stands"


def test_a_markdown_fence_is_stripped_and_the_repair_is_recorded():
    """The one repair, and the reason it is visible.

    Stripping a fence cannot change what the JSON says, so doing it is safe;
    doing it *silently* would hide a model that keeps ignoring an explicit
    instruction, which is a prompt problem someone should see.
    """

    fenced = lambda p: "```json\n" + _good_report(p) + "\n```"  # noqa: E731
    result = _run("broken", Scripted(report=fenced, correlate=_FOUND))

    assert result.paraphrase_status == EMITTED
    assert any("markdown fence" in r for r in result.repairs)


def test_nothing_beyond_a_fence_is_repaired():
    """The line between transport wrapper and content. Trailing prose after the
    JSON is the model doing something else, and guessing at which part was
    meant is exactly the probabilistic step this layer exists to avoid."""

    chatty = lambda p: _good_report(p) + "\n\nHope that helps!"  # noqa: E731
    result = _run("broken", Scripted(report=chatty, correlate=_FOUND))

    assert result.paraphrase_status == WITHHELD
    assert result.paraphrase is None


# --------------------------------------------------------------------------- #
# Rule 2 -- correlation reads the device the CAUSE is on
# --------------------------------------------------------------------------- #


def test_the_log_window_comes_from_the_cause_device_not_the_local_one():
    """The wiring mistake that would look like a clean result.

    The investigation starts on RR1. The cause is on PE2, and PE2's buffer is
    where the interface and IS-IS events are. Reading RR1's logs would correlate
    a PE2 event against a device that never saw it and return "no correlating
    events" with perfect confidence.
    """

    asked: list[str] = []
    result = _run("broken", Scripted(report=_good_report, correlate=_FOUND),
                  window=_capture(asked))

    assert asked == ["PE2"], "the window must be read where the cause is"
    assert result.correlation_paraphrase_status == EMITTED


def _capture(sink):
    from pathlib import Path

    from agent_nettools import log_window, template_parsers

    fixtures = Path(__file__).resolve().parent / "fixtures" / "cisco_xr"

    def window(device: str):
        sink.append(device)
        raw = (fixtures / device / "broken" / "show-logging-last-200.txt").read_text()
        parsed, _ = template_parsers.parse_template_output("cisco_xr", "logging", raw)
        return log_window.shape_window(
            parsed["records"], coverage=log_window.coverage_from_logging(parsed, device)
        )

    return window


def test_no_cause_means_no_correlation_to_run():
    """A healthy descent has nothing to place on a timeline. Not attempted is
    the honest status -- not an empty correlation reading as "checked, nothing
    there"."""

    result = _run("healthy", Scripted(report="{}", correlate=_FOUND))

    assert result.correlation_paraphrase_status == NOT_ATTEMPTED
    assert result.correlation_paraphrase is None
    assert result.coverage is None


# --------------------------------------------------------------------------- #
# Rule 3 -- a coverage shortfall downgrades, it does not discard
# --------------------------------------------------------------------------- #


def test_an_absence_claim_over_a_truncated_buffer_is_downgraded_not_discarded():
    """T-029a arriving through the runner, and the decision it forced.

    PE2's buffer holds 593 records and 200 were read, so "no correlating events"
    is a real answer at the wrong strength. Discarding it loses a usable result;
    emitting it as a negative overstates it. It is kept, labelled
    `coverage_limited`, and reads as an `unevaluated`.
    """

    asked: list[str] = []
    result = _run("broken", Scripted(report=_good_report, correlate=_NOT_FOUND),
                  window=_capture(asked))

    assert result.correlation_paraphrase_status == COVERAGE_LIMITED
    assert result.correlation_paraphrase is not None, "downgraded, not discarded"
    assert not result.correlation_grounding.ok
    assert [f.kind for f in result.correlation_grounding.failures] == [
        "absence_claim_exceeds_coverage"
    ]
    assert "200 of 593" in " ".join(result.withheld_because())


def test_a_found_correlation_is_unaffected_by_the_same_coverage_gap():
    """The §0.12 companion for the downgrade.

    Without this, the runner could label every correlation `coverage_limited`
    and the test above would still pass. Presence is not weakened by a gap.
    """

    asked: list[str] = []
    result = _run("broken", Scripted(report=_good_report, correlate=_FOUND),
                  window=_capture(asked))

    assert result.correlation_paraphrase_status == EMITTED
    assert result.coverage is not None and not result.coverage.complete


def test_a_correlation_that_fails_for_a_real_reason_is_still_withheld():
    """The downgrade is scoped to coverage shortfalls alone. A correlation with
    no coverage record at all is a construction bug, not a weak answer."""

    result = _run("broken", Scripted(report=_good_report, correlate=_NOT_FOUND),
                  window=lambda d: __import__(
                      "agent_nettools.log_window", fromlist=["ShapedWindow"]
                  ).ShapedWindow(total_in=0, coverage=None))

    assert result.correlation_paraphrase_status == WITHHELD
    assert result.correlation_paraphrase is None
    assert result.correlation_grounding.failures[0].kind == "unbacked_absence_claim"


# --------------------------------------------------------------------------- #
# The gate is the composed one
# --------------------------------------------------------------------------- #


def test_the_runner_grounds_through_ground_report(monkeypatch):
    """T-029's stated acceptance condition for this task.

    `check_grounding` alone passes every internally consistent report, including
    the chain-dropping one above. Asserting the call by name is blunt, and it is
    the only way to catch a future edit that swaps one for the other while every
    behavioural test still passes -- which is precisely §0.13's failure shape.
    """

    calls: list[str] = []
    real = investigation.ground_report

    def spy(report, descent):
        calls.append("ground_report")
        return real(report, descent)

    monkeypatch.setattr(investigation, "ground_report", spy)
    _run("broken", Scripted(report=_good_report, correlate=_FOUND))

    assert calls == ["ground_report"]


def test_the_resolver_refuses_an_unknown_subject():
    """A wrong-device read looks exactly like a healthy one, so there is no
    fallback to the local device -- the same refusal `_resolve_devices` makes."""

    with pytest.raises(ValueError, match="no device in the inventory owns"):
        investigation.inventory_resolver("10.255.99.99")


def test_the_inventory_resolver_finds_the_real_owner():
    """The companion. Without it the refusal above could be unconditional."""

    assert investigation.inventory_resolver("10.255.0.12") == "PE2"
    assert investigation.inventory_resolver("10.255.0.31") == "RR1"


# --------------------------------------------------------------------------- #
# The claim the whole layer rests on
# --------------------------------------------------------------------------- #


def test_the_model_cannot_influence_the_diagnosis():
    """No gate, no narrowing pass, stated as a property rather than a promise.

    The same investigation run with and without a model must produce a
    byte-identical descent. If a model call ever leaks into evidence collection
    or rung selection, this fails -- and nothing else in the suite would.
    """

    without = _run("broken").descent
    with_model = _run("broken", Scripted(report=_good_report, correlate=_FOUND)).descent

    assert without.finding == with_model.finding
    assert without.rung_path == with_model.rung_path
    assert without.evidence_keys == with_model.evidence_keys
    assert [(o.rung, o.device, o.status, o.result.reason) for o in without.outcomes] == \
           [(o.rung, o.device, o.status, o.result.reason) for o in with_model.outcomes]


def test_the_model_is_called_exactly_twice_and_never_in_a_loop():
    """MVP-0 has no agent loop in this path. Two calls: correlate, then report.

    A bound that is a property of the code's shape rather than of a counter,
    but worth asserting -- the failure mode if it ever becomes a loop is a cost
    and latency regression nobody notices until a bill arrives.
    """

    analyst = Scripted(report=_good_report, correlate=_FOUND)
    _run("broken", analyst, window=_capture([]))

    assert len(analyst.prompts) == 2
    assert sum("LOG WINDOW" in p for p in analyst.prompts) == 1
    assert sum("EXPECTED OUTPUT" in p for p in analyst.prompts) == 2


def test_a_healthy_descent_calls_the_model_once():
    """Nothing to correlate means the correlate call is not made at all, rather
    than made against an empty window and answered."""

    analyst = Scripted(report="{}", correlate=_FOUND)
    _run("healthy", analyst)

    assert len(analyst.prompts) == 1
    assert not any("LOG WINDOW" in p for p in analyst.prompts)


# --------------------------------------------------------------------------- #
# `_log_window` itself -- the function every other test in this file stubs out
# --------------------------------------------------------------------------- #


def test_the_real_log_window_reads_and_shapes_a_window():
    """The function T-033 found broken, tested against a real read.

    Every other test here passes `window=`, so `_log_window` was never executed
    by the suite -- the tests confirmed the stub, not the code. It shipped with
    two defects that made every log read fail silently: `count` passed as an
    `int` where `render_command` requires text, and the output read from
    `data["outputs"]` where `run_template` writes `data["commands"]`.

    Neither raised. `run_template` returns a structured error and `_log_window`
    turned it into an empty window with no coverage -- which then correctly
    withheld the correlation, so the *only* symptom was a missing timeline.
    §0.13's tests face: a stub cannot fail the way the real thing does.
    """

    shaped = investigation._log_window("PE2", sender=fixture_sender(label="broken"))

    assert shaped.total_in == 200, "the read must actually return the window"
    assert len(shaped.records) == 28
    assert shaped.coverage is not None, "a successful read must carry coverage"
    assert shaped.coverage.records_available == 593
    assert shaped.coverage.device == "PE2"


def test_a_failed_log_read_is_a_window_with_no_coverage_not_an_empty_one():
    """The distinction that makes the failure above recoverable.

    A read that did not happen must not look like a device with nothing in its
    buffer. No coverage record means `check_absence_coverage` refuses to let
    anything claim absence over it -- which is exactly what happened live, and
    is why a broken log read surfaced as a withheld correlation and exit 2
    rather than as a confident "no correlating events".
    """

    def broken_sender(device, command):
        raise RuntimeError("device unreachable")

    shaped = investigation._log_window("PE2", sender=broken_sender)

    assert shaped.coverage is None
    assert shaped.is_empty

    from agent_nettools.grounding import ground_correlation

    refusal = {"correlation": {"found": False, "summary": "no correlating events"}}
    verdict = ground_correlation(refusal, shaped.coverage)
    assert not verdict.ok
    assert verdict.failures[0].kind == "unbacked_absence_claim"


def test_a_fabricated_timestamp_withholds_the_correlation_through_the_runner():
    """T-029b arriving through the runner -- the T-033 failure, end to end.

    The descent and the report are unaffected: the diagnosis is deterministic
    and the report grounds against it. Only the timeline is refused, which is
    exactly the scoping the two gates are meant to give.
    """

    import json as _json

    fabricated = _json.dumps({
        "timeline": [{"at": "Aug 14 04:28.238 UTC", "event": "adjacency down",
                      "mnemonic": "ROUTING-ISIS-5-ADJCHANGE"}],
        "correlation": {"found": True, "summary": "s",
                        "followed_a_commit": True, "recurrence": "once"},
    })
    result = _run("broken", Scripted(report=_good_report, correlate=fabricated))

    assert result.correlation_paraphrase_status == WITHHELD
    assert result.correlation_paraphrase is None
    assert [f.kind for f in result.correlation_grounding.failures] == ["invented_timestamp"]

    assert result.paraphrase_status == EMITTED, "the report is unaffected"
    assert result.finding == "interface_line_down", "the diagnosis is unaffected"


def test_a_faithful_timeline_still_passes_through_the_runner():
    """The companion, and it needs the real window -- the entries are copied out
    of it, so a runner reading the wrong device would fail this."""

    import json as _json

    window = investigation._log_window("PE2", sender=fixture_sender(label="broken"))
    faithful = _json.dumps({
        "timeline": [{"at": r["timestamp"], "event": r["text"][:30],
                      "mnemonic": r["mnemonic"]} for r in window.records[:3]],
        "correlation": {"found": True, "summary": "s",
                        "followed_a_commit": True, "recurrence": "once"},
    })
    result = _run("broken", Scripted(report=_good_report, correlate=faithful))

    assert result.correlation_paraphrase_status == EMITTED
    assert result.correlation_grounding.ok
    assert result.correlation_grounding.timeline_entries_checked == 3


# --------------------------------------------------------------------------- #
# B-425 -- what an investigation costs, measured rather than estimated
# --------------------------------------------------------------------------- #


class CountingAnalyst(Scripted):
    """A scripted analyst that also reports usage, as the real one does."""

    def __init__(self, **kw):
        super().__init__(**kw)
        from agent_nettools.llm_analysis import TokenUsage

        self.usage = TokenUsage()

    def __call__(self, prompt):
        from agent_nettools.llm_analysis import TokenUsage

        text = f"{prompt.system}\n\n{prompt.user}"
        self.usage = self.usage + TokenUsage(
            input_tokens=len(text) // 4, output_tokens=64, calls=1
        )
        return super().__call__(prompt)


def test_an_investigation_reports_what_its_model_calls_cost():
    """T-033 was asked for token usage and could only give a character-count
    proxy, because nothing on the rendered-prompt path surfaced `usage`. A cost
    estimated from character counts is the kind of number that quietly becomes
    folklore."""

    analyst = CountingAnalyst(report=_good_report, correlate=_FOUND)
    result = _run("broken", analyst)

    assert result.usage is not None
    assert result.usage.calls == 2, "one correlate, one report"
    assert result.usage.total_tokens > 0
    assert result.to_payload()["usage"]["calls"] == 2


def test_an_analyst_that_reports_nothing_is_not_reported_as_zero():
    """"The provider did not say" and "it cost nothing" are different facts.

    A plain scripted callable has no `.usage`, and the result says `None`
    rather than a zeroed record that reads as a measurement.
    """

    result = _run("broken", Scripted(report=_good_report, correlate=_FOUND))

    assert result.usage is None
    assert result.to_payload()["usage"] is None


def test_a_run_with_no_model_reports_no_usage():
    assert _run("broken").usage is None


def test_unreported_usage_is_distinguishable_from_zero_usage():
    from agent_nettools.llm_analysis import TokenUsage

    silent = TokenUsage(calls=1, reported=False)
    free = TokenUsage(input_tokens=0, output_tokens=0, calls=1)

    assert silent.total_tokens == free.total_tokens == 0
    assert silent.reported is False and free.reported is True
    assert "did not report" in silent.summary()
    assert "did not report" not in free.summary()
    assert (silent + free).reported is False, "one unreported call taints the total"
