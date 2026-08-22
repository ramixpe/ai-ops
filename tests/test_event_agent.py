"""`agent_nettools.event_agent` -- the bounded, event-woken MCP tool-calling
loop: an "AI receives a syslog, asks our MCP for context, then asks our MCP
for wide or narrow path" client, driven by a model instead of a human,
gated hard by `mnemonics.yaml`'s `trigger.fires`.

Every test here injects both `toolset=` and `caller=` (fakes below) --
per this module's own docstring, that is what lets the whole thing be
proven with no lab, no API key, and no MCP SDK. The one real dependency
exercised is `mnemonics.yaml`/`event_routing.MNEMONIC_FLOW_TABLE` through
`route_syslog_line`, against committed fixture log lines -- never a
hand-typed line (`_real_line`, copied from `test_event_routing.py`'s own
helper, which is not importable without pulling in that whole test module).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from agent_nettools import event_agent as ea
from agent_nettools import event_routing as er
from agent_nettools import event_watch as ew
from agent_nettools import model_ingress
from agent_nettools import ticket as ticket_module
from agent_nettools.mcp_client import ToolCallResult, ToolDescriptor

FIXTURES = Path(__file__).parent / "fixtures" / "cisco_xr"


def _real_line(fragment: str, device: str = "RR1") -> str:
    """A verbatim line from the committed fixtures -- never a hand-typed one."""

    text = (FIXTURES / device / "broken" / "show-logging-last-200.txt").read_text()
    for line in text.splitlines():
        if fragment in line:
            return line.strip()
    raise AssertionError(f"no fixture line contains {fragment!r}")


def _bgp_decision(device: str = "RR1") -> er.RoutingDecision:
    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12", device=device)
    decision = er.route_syslog_line(line, device=device)
    assert decision.routable, decision.reason
    return decision


#: A synthetic trigger table clearing ROUTING-BGP-5-ADJCHANGE to fire --
#: OBS-181's own positive-control discipline (`event_watch.py`'s docstring):
#: the live, reviewed `mnemonics.yaml` has zero `fires: true` entries today,
#: so a mechanism only ever exercised against it is proven to refuse, never
#: proven to accept. `event_routing.MNEMONIC_FLOW_TABLE` already has a real
#: entry for this mnemonic (bgp_session, `_ipv4_subject`), so
#: `validate_trigger_table` accepts this table without any further fakery.
_FIRES_TABLE = (
    {
        "mnemonic": "ROUTING-BGP-5-ADJCHANGE",
        "investigate_with": {"flow": "bgp_session"},
        "trigger": {"fires": True, "reason": "synthetic test override"},
    },
)


# --------------------------------------------------------------------------- #
# Fakes -- the toolset/caller injection seam this module's docstring names.
# --------------------------------------------------------------------------- #


class FakeToolset:
    """Duck-types `McpToolset`'s two methods used by `event_agent`. Backed by
    a caller-supplied manifest and a script of canned `ToolCallResult`s, one
    list per tool name, popped in call order -- so a test can hand
    `investigate_lab` a different answer on each of several calls if it
    needs to."""

    def __init__(self, tool_dicts, responses: dict[str, list[ToolCallResult]]):
        self._tools = tuple(ToolDescriptor(**t) for t in tool_dicts)
        self._responses = {name: list(results) for name, results in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments, *, timeout_s):
        self.calls.append((name, dict(arguments)))
        queue = self._responses.get(name)
        if not queue:
            raise AssertionError(f"FakeToolset has no scripted response left for {name!r}")
        return queue.pop(0)


class ScriptedCaller:
    """A `ModelCaller` that plays back a fixed script of turns, one per call.
    Raises loudly if the loop asks for more turns than were scripted --
    a silent `IndexError` would make a bug in the loop's own stop condition
    look like a fixture problem instead."""

    def __init__(self, turns: list[dict]):
        self._turns = list(turns)
        self.calls: list[dict] = []

    def __call__(self, *, system, messages, tools, timeout_s):
        self.calls.append(
            {"system": system, "messages": [dict(m) for m in messages], "tools": tools,
             "timeout_s": timeout_s}
        )
        if not self._turns:
            raise AssertionError("ScriptedCaller ran out of scripted turns")
        return self._turns.pop(0)


class ExplodingCaller:
    """Proof that a refused/gated run never reaches the model at all."""

    def __call__(self, **kwargs):
        raise AssertionError("caller must never be invoked on a refused/gated run")


class ExplodingToolset:
    """Proof that a refused/gated run never opens an MCP connection."""

    def list_tools(self):
        raise AssertionError("toolset.list_tools must never be called on a refused run")

    def call_tool(self, *a, **k):
        raise AssertionError("toolset.call_tool must never be called on a refused run")


def _tool_result(name: str, payload: dict, *, is_error: bool = False, elapsed_s: float = 0.005) -> ToolCallResult:
    return ToolCallResult(
        name=name, arguments={}, text=json.dumps(payload), is_error=is_error,
        elapsed_s=elapsed_s, truncated_chars=None,
    )


_INVESTIGATE_LAB_OK = {
    "tool": "investigate", "device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session",
    "finding": "bgp_hold_timer_expired", "reason": "hold timer expired",
    "cause": {"rung": "bgp_session", "device": "RR1", "reason": "hold timer expired"},
    "causal_chain": [{"rung": "bgp_session", "device": "RR1", "reason": "hold timer expired"}],
    "report": {"status": "ok"}, "correlation": {"status": "ok"},
    "coherence": None, "trustworthy": True,
}

_INVESTIGATE_LAB_NO_FAULT = {
    "tool": "investigate", "device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session",
    "finding": "no_fault_on_path", "reason": "every rung checked out",
    "cause": None, "causal_chain": [],
    "report": {"status": "ok"}, "correlation": {"status": "ok"},
    "coherence": None, "trustworthy": True,
}

_EXPLORE_LAB_OK = {"tool": "explore_lab", "device": "RR1", "facts": {}, "health": {"status": "ok"}}


# --------------------------------------------------------------------------- #
# Positive control (OBS-181): a well-behaved fake model completes a
# wide -> narrow run.
# --------------------------------------------------------------------------- #


def _open_ticket_run(monkeypatch, tmp_path, *, turns, toolset_responses, bounds=None, now=None):
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: _FIRES_TABLE)
    monkeypatch.setenv(ticket_module.NETTOOLS_TICKET_DIR_ENV, str(tmp_path))
    decision = _bgp_decision()
    toolset = FakeToolset(ea._PLANNING_TOOL_SCHEMAS, toolset_responses)
    caller = ScriptedCaller(turns)
    run = ea.run_event(decision, bounds=bounds, toolset=toolset, caller=caller, now=now)
    return run, toolset, caller, decision


def test_a_well_behaved_fake_model_completes_a_wide_then_narrow_run(monkeypatch, tmp_path):
    turns = [
        {"text": "", "tool_calls": [{"id": "1", "name": "explore_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "", "tool_calls": [{"id": "2", "name": "investigate_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "PE has a hold-timer expiry; see investigate_lab's finding.",
         "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {
        "explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)],
        "investigate_lab": [_tool_result("investigate_lab", _INVESTIGATE_LAB_OK)],
    }
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses
    )

    assert run.ran is True
    assert run.mode == "fixtures"
    assert run.complete is True
    assert run.stopped_because == "end_turn"
    assert run.reason  # always populated

    dispatched = [c["tool"] for c in run.tool_calls if not c.get("skipped")]
    assert dispatched == ["explore_lab", "investigate_lab"]
    assert dispatched.index("explore_lab") < dispatched.index("investigate_lab"), (
        "wide before narrow"
    )
    assert not run.fabrication_attempts
    assert not run.limits_hit

    # code_observed: the ticket carries the model's OWN dispatched arguments
    # (pinned values included), never left to the model's own account.
    assert toolset.calls[0] == ("explore_lab", {"device_name": "RR1"})
    assert toolset.calls[1] == ("investigate_lab", {
        "device_name": "RR1", "subject": "10.255.0.12", "flow": "bgp_session",
    })

    # The ticket: record_answer carries investigate_lab's OWN finding, never
    # the model's closing prose.
    assert run.ticket_run_id
    ticket_path = next(tmp_path.glob("*.md"))
    read = ticket_module.read_ticket(ticket_path)
    assert read["run_id"] == run.ticket_run_id
    assert read["answer"] is not None
    assert read["answer"]["finding"] == "bgp_hold_timer_expired"
    assert read["answer"]["trustworthy"] is True
    assert len(read["model_exchanges"]) == 3
    # The model's closing prose is on a model_exchange, never mistaken for
    # the answer.
    assert read["model_exchanges"][-1]["response_text"].startswith("PE has a hold-timer")
    assert read["answer"]["finding"] != read["model_exchanges"][-1]["response_text"]
    # grounding_ok is None, always -- never False (there is no DescentResult
    # for this loop's free-form prose).
    assert all(ex["grounding_ok"] is None for ex in read["model_exchanges"])


def test_no_fault_found_is_recorded_verbatim_not_overridden_by_the_models_prose(monkeypatch, tmp_path):
    """B-490: the model's own prose speculating about a cause must never
    displace investigate_lab's own 'no_fault_on_path' finding."""

    turns = [
        {"text": "", "tool_calls": [{"id": "1", "name": "investigate_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "It might be a flapping interface upstream, though nothing confirms that.",
         "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {"investigate_lab": [_tool_result("investigate_lab", _INVESTIGATE_LAB_NO_FAULT)]}
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses
    )

    assert run.complete is True
    ticket_path = next(tmp_path.glob("*.md"))
    read = ticket_module.read_ticket(ticket_path)
    assert read["answer"]["finding"] == "no_fault_on_path"
    assert "flapping interface" not in json.dumps(read["answer"])


# --------------------------------------------------------------------------- #
# The hard gate: mnemonics.yaml's trigger.fires, checked before anything
# else touches a toolset or a model.
# --------------------------------------------------------------------------- #


def test_the_live_reviewed_table_refuses_every_mnemonic_today():
    """As of 2026-08-21 every mnemonics.yaml entry is trigger.fires: false --
    the module docstring's own claim, pinned. Uses the REAL table (no
    monkeypatch) and the exploding fakes, so a regression that ever let a
    live call reach the model or the MCP server fails loudly here."""

    decision = _bgp_decision()
    run = ea.run_event(decision, toolset=ExplodingToolset(), caller=ExplodingCaller())

    assert run.ran is False
    assert run.mode == "plan"
    assert not run.tool_calls
    assert not run.exchanges
    assert run.ticket_run_id is None


def test_trigger_fires_false_names_the_mnemonic_and_never_dispatches(monkeypatch, tmp_path):
    table = (
        {
            "mnemonic": "ROUTING-BGP-5-ADJCHANGE",
            "investigate_with": {"flow": "bgp_session"},
            "trigger": {"fires": False, "reason": "steady-state noise, not a fault"},
        },
    )
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: table)
    decision = _bgp_decision()

    run = ea.run_event(decision, toolset=ExplodingToolset(), caller=ExplodingCaller())

    assert run.ran is False
    assert run.mode == "plan"
    assert run.reason == "steady-state noise, not a fault"
    assert not list(tmp_path.glob("*.md"))  # no ticket directory activity at all

    plan = ea.plan_event(decision)  # plan_event agrees, and truly calls nothing
    assert plan.ran is False
    assert plan.mode == "plan"
    assert plan.reason == "steady-state noise, not a fault"


def test_an_unclassified_mnemonic_is_refused_not_guessed(monkeypatch):
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: ())
    decision = _bgp_decision()
    run = ea.run_event(decision, toolset=ExplodingToolset(), caller=ExplodingCaller())
    assert run.ran is False
    assert "no trigger entry" in run.reason


def test_a_fires_true_entry_with_no_flow_table_match_raises_loudly(monkeypatch):
    """Mirrors event_watch's own guard (B-630-TRIGGER-TABLE-GUARD), applied
    at this module's own, separately-defined TriggerTableInconsistency --
    see the module docstring for why this is a deliberate second copy."""

    bad_table = (
        {
            "mnemonic": "PKT_INFRA-LINK-5-CHANGED",  # a real investigate_with, no MNEMONIC_FLOW_TABLE entry
            "investigate_with": {"flow": "interface"},
            "trigger": {"fires": True, "reason": "bad review"},
        },
    )
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: bad_table)
    decision = _bgp_decision()
    with pytest.raises(ea.TriggerTableInconsistency):
        ea.run_event(decision, toolset=ExplodingToolset(), caller=ExplodingCaller())


def test_an_unroutable_decision_is_refused_before_the_gate_is_even_checked():
    decision = er.RoutingDecision(routable=False, reason="not an IOS-XR log line")
    run = ea.run_event(decision, toolset=ExplodingToolset(), caller=ExplodingCaller())
    assert run.ran is False
    assert "not routable" in run.reason


def test_run_event_refuses_cleanly_with_no_caller(monkeypatch):
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: _FIRES_TABLE)
    decision = _bgp_decision()
    run = ea.run_event(decision, toolset=ExplodingToolset())
    assert run.ran is False
    assert "ModelCaller" in run.reason


# --------------------------------------------------------------------------- #
# plan_event: resolves everything, calls nothing -- even with mcp absent.
# --------------------------------------------------------------------------- #


def test_plan_event_never_imports_mcp_and_never_needs_a_toolset_or_caller():
    """`plan_event` takes only a decision -- there is no toolset/caller
    parameter for it to accept, which is the structural proof it calls
    nothing. This additionally proves it does not even need the `mcp`
    package importable, in a fresh subprocess -- the same absence-
    simulation `test_mcp_client.py` already uses for the identical claim
    about `mcp_client` itself."""

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; sys.modules['mcp'] = None\n"
            "from agent_nettools import event_routing as er, event_agent as ea\n"
            "d = er.RoutingDecision(routable=False, reason='x')\n"
            "run = ea.plan_event(d)\n"
            "assert run.ran is False and run.mode == 'plan'\n"
            "print('ok')\n",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_plan_event_previews_the_offered_manifest_when_the_gate_would_pass(monkeypatch):
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: _FIRES_TABLE)
    decision = _bgp_decision()
    run = ea.plan_event(decision)
    assert run.ran is False  # plan_event NEVER runs, by construction
    assert run.mode == "plan"
    assert "explore_lab" in run.tools_offered
    assert "investigate_lab" in run.tools_offered
    # This line read `assert "probe_lab" in run.tools_offered  # offered, even
    # though never dispatchable live` until 2026-08-22. The behaviour was
    # noticed and pinned as correct rather than questioned -- the reasoning
    # being that `child_server_env()` disables active probes in the child, so
    # offering it was harmless. It was not correct: it made the prompt's own
    # sentence to the model false, and it left a mechanism standing on a
    # second one (OBS-701). A test can freeze a defect exactly as firmly as it
    # freezes a property, and the comment explaining why it is fine is the
    # tell.
    assert "probe_lab" not in run.tools_offered


# --------------------------------------------------------------------------- #
# Bounds behaviour: every limit names itself, never a silent drop.
# --------------------------------------------------------------------------- #


def test_max_iterations_stops_the_loop_and_leaves_no_answer_when_investigate_lab_never_ran(
    monkeypatch, tmp_path
):
    # The model keeps asking for explore_lab, forever -- never investigate_lab.
    turns = [
        {"text": "", "tool_calls": [{"id": str(i), "name": "explore_lab", "arguments": {}}],
         "stop_reason": "tool_use"}
        for i in range(10)
    ]
    responses = {"explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK) for _ in range(10)]}
    bounds = ea.AgentBounds(max_iterations=2, time_budget_s=90.0, max_tool_calls=20)
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses, bounds=bounds,
    )

    assert run.stopped_because == "max_iterations"
    assert run.complete is False
    assert {"limit": "max_iterations", "value": 2, "observed": 2} in run.limits_hit

    # Absence is never zero: no investigate_lab call ever happened, so the
    # ticket has NO Answer section at all -- not an empty one, an absent one.
    ticket_path = next(tmp_path.glob("*.md"))
    read = ticket_module.read_ticket(ticket_path)
    assert read["answer"] is None


def test_max_tool_calls_refuses_further_dispatch_and_names_the_budget(monkeypatch, tmp_path):
    turns = [
        {"text": "", "tool_calls": [
            {"id": "1", "name": "explore_lab", "arguments": {}},
            {"id": "2", "name": "check_lab", "arguments": {"intent": "bgp"}},
        ], "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {
        "explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)],
        "check_lab": [_tool_result("check_lab", {"tool": "check_lab"})],
    }
    bounds = ea.AgentBounds(max_iterations=4, time_budget_s=90.0, max_tool_calls=1)
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses, bounds=bounds,
    )

    dispatched = [c for c in run.tool_calls if not c.get("skipped")]
    skipped = [c for c in run.tool_calls if c.get("skipped")]
    assert len(dispatched) == 1
    assert len(skipped) == 1
    assert skipped[0]["is_error"] is True
    assert "max_tool_calls" in skipped[0]["detail"]
    assert {"limit": "max_tool_calls", "value": 1, "observed": 1} in run.limits_hit
    # only one tool actually reached the fake MCP server
    assert len(toolset.calls) == 1


def test_max_calls_per_tool_refuses_a_second_investigate_lab_call(monkeypatch, tmp_path):
    turns = [
        {"text": "", "tool_calls": [{"id": "1", "name": "investigate_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "", "tool_calls": [{"id": "2", "name": "investigate_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {"investigate_lab": [_tool_result("investigate_lab", _INVESTIGATE_LAB_OK)]}
    bounds = ea.AgentBounds(max_iterations=4, time_budget_s=90.0, max_tool_calls=10,
                             max_calls_per_tool={"investigate_lab": 1})
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses, bounds=bounds,
    )

    dispatched = [c for c in run.tool_calls if not c.get("skipped")]
    assert len(dispatched) == 1
    skipped = [c for c in run.tool_calls if c.get("skipped")]
    assert len(skipped) == 1
    assert "max_calls_per_tool" in skipped[0]["detail"]
    assert any(
        entry["limit"] == "max_calls_per_tool:investigate_lab" for entry in run.limits_hit
    )
    # investigate_lab's finding is still recorded once, from the ONE real call
    ticket_path = next(tmp_path.glob("*.md"))
    read = ticket_module.read_ticket(ticket_path)
    assert read["answer"]["finding"] == "bgp_hold_timer_expired"


def test_time_budget_stops_before_the_first_turn_and_the_caller_is_never_invoked(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "load_mnemonic_table", lambda: _FIRES_TABLE)
    monkeypatch.setenv(ticket_module.NETTOOLS_TICKET_DIR_ENV, str(tmp_path))
    decision = _bgp_decision()
    toolset = FakeToolset(ea._PLANNING_TOOL_SCHEMAS, {})
    caller = ExplodingCaller()
    bounds = ea.AgentBounds(time_budget_s=5.0)

    # start=0 on the FIRST call; every call after returns 10 -- so the loop's
    # first turn-boundary check already reads 10 >= 5 and stops before ever
    # calling the (exploding) caller, and the final `elapsed_s = now_fn() -
    # start` read at the end of `_dispatch` also lands on the same fixed 10.
    calls = {"n": 0}

    def clock() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 10.0

    run = ea.run_event(decision, bounds=bounds, toolset=toolset, caller=caller, now=clock)

    assert run.ran is True  # the loop DID start (a ticket was opened) -- it just never got a turn
    assert run.stopped_because == "time_budget"
    assert run.complete is False
    assert {"limit": "time_budget_s", "value": 5.0, "observed": 10.0} in run.limits_hit
    assert not run.exchanges


def test_max_result_chars_truncates_names_the_budget_and_marks_is_error(monkeypatch, tmp_path):
    huge = json.dumps({"tool": "explore_lab", "device": "RR1", "padding": "x" * 50_000})
    result = ToolCallResult(
        name="explore_lab", arguments={}, text=huge, is_error=False, elapsed_s=0.01,
        truncated_chars=None,
    )
    turns = [
        {"text": "", "tool_calls": [{"id": "1", "name": "explore_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {"explore_lab": [result]}
    bounds = ea.AgentBounds(max_result_chars=1000)
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses, bounds=bounds,
    )

    dispatched = [c for c in run.tool_calls if not c.get("skipped")]
    assert len(dispatched) == 1
    assert dispatched[0]["is_error"] is True
    assert "max_result_chars" in dispatched[0]["detail"]
    assert {"limit": "max_result_chars", "value": 1000, "observed": len(huge)} in run.limits_hit


# --------------------------------------------------------------------------- #
# Argument fabrication: model_ingress.ArgumentRefusal, counted, never
# silently dispatched.
# --------------------------------------------------------------------------- #


def test_a_model_supplied_pinned_argument_is_refused_and_counted_not_dispatched(monkeypatch, tmp_path):
    turns = [
        {"text": "", "tool_calls": [
            {"id": "1", "name": "investigate_lab", "arguments": {"device_name": "PE99"}},
        ], "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses={"investigate_lab": []},
    )

    assert not toolset.calls  # never reached the MCP server
    assert len(run.fabrication_attempts) == 1
    assert run.fabrication_attempts[0]["tool"] == "investigate_lab"
    assert run.fabrication_attempts[0]["model_supplied"] == {"device_name": "PE99"}
    refused = [c for c in run.tool_calls if c.get("fabrication")]
    assert len(refused) == 1
    assert refused[0]["is_error"] is True


def test_a_refused_call_reaches_the_TICKET_not_only_the_returned_EventRun(monkeypatch, tmp_path):
    """The durable record must show the attempt, not just the return value.

    `EventRun` is what `run_event` hands back; an unattended, event-woken
    caller discards it. The ticket is the flight recorder that survives. If a
    fabrication attempt lived only on `EventRun`, a production ticket would
    show a run in which the model made ZERO tool calls -- which reads as a
    well-behaved model, and is the absence-is-never-zero defect this project
    keeps rediscovering. It is also the single measurement the whole pinning
    design exists to produce (B-459).

    `dispatched: False` and `model_supplied` (never `arguments`, which on a
    dispatched event means "as dispatched, pinned values included") are what
    keep a refusal from reading like a call that ran.
    """

    turns = [
        {"text": "", "tool_calls": [
            {"id": "1", "name": "investigate_lab",
             "arguments": {"device_name": "PE2", "subject": "10.255.0.99"}},
        ], "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    run, toolset, _caller, _decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses={"investigate_lab": []},
    )

    assert not toolset.calls
    read = ticket_module.read_ticket(next(tmp_path.glob("*.md")))
    events = [e for e in read["timeline"] if e.get("kind") == "tool_event"]
    assert len(events) == 1, "the attempt must appear on the ticket at all"
    event = events[0]
    assert event["tool"] == "investigate_lab"
    assert event["status"] == "refused"
    assert event["dispatched"] is False
    assert event["fabrication"] is True
    # The fabricated values are preserved verbatim -- what the model reached
    # for is the finding, so it must not be normalised away.
    assert event["model_supplied"] == {"device_name": "PE2", "subject": "10.255.0.99"}
    # ...and never under the key a dispatched call uses for its real arguments.
    assert "arguments" not in event

    # Absence is never zero, the other direction: nothing ran, so no Answer.
    assert read.get("answer") is None


def test_a_budget_refusal_also_reaches_the_ticket(monkeypatch, tmp_path):
    """Same rule for a bound, not just a fabrication: a ticket that silently
    omits the calls a budget refused understates how hard the model pushed."""

    turns = [
        {"text": "", "tool_calls": [
            {"id": "1", "name": "explore_lab", "arguments": {}},
            {"id": "2", "name": "check_lab", "arguments": {"intent": "bgp"}},
        ], "stop_reason": "tool_use"},
        {"text": "done", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {"explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)]}
    run, _toolset, _caller, _decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses,
        bounds=ea.AgentBounds(max_tool_calls=1),
    )

    read = ticket_module.read_ticket(next(tmp_path.glob("*.md")))
    events = [e for e in read["timeline"] if e.get("kind") == "tool_event"]
    assert [e["tool"] for e in events] == ["explore_lab", "check_lab"]
    assert events[0]["status"] == "success"
    assert events[0].get("dispatched") is not False
    assert events[1]["status"] == "refused"
    assert events[1]["dispatched"] is False
    assert events[1]["fabrication"] is False
    assert "max_tool_calls" in events[1]["detail"]


# --------------------------------------------------------------------------- #
# Both ToolCallResult error shapes -- OBS-698, this task's own measured fact.
# --------------------------------------------------------------------------- #


def test_probe_lab_is_never_offered_even_where_it_pins_cleanly(monkeypatch):
    """The active-probe tool must not be offered to an unattended loop.

    `probe_lab` IS pinnable: on a `bgp_session` flow the subject is
    IPv4-shaped, so `device_name` and `address` pin cleanly and only `kind`
    (ping/traceroute) is left for the model. Offering the whole PIN_TABLE
    therefore handed it to the model on every BGP event -- measured, not
    reasoned about (OBS-701), and it made the prompt's own sentence *"The
    active-probe kind (ping/traceroute) is not offered to this loop"* false.

    Nothing could have been probed -- `child_server_env()` disables active
    probes in the spawned server's own environment -- but a loop should not be
    shown a tool whose only possible outcome is a refusal.

    The `bgp_session` case is the one that regressed; `interface` never
    offered it (an interface name is not an address), which is exactly why a
    test written against only the interface flow would have passed throughout.
    """

    for subject, flow in (("10.255.0.12", "bgp_session"), ("GigabitEthernet0/0/0/0", "interface")):
        context = model_ingress.PinnedContext(device="RR1", subject=subject, flow=flow)
        offered = [o.name for o in ea._offer_tools(ea._PLANNING_TOOL_SCHEMAS, context)]
        assert "probe_lab" not in offered, f"probe_lab offered on the {flow} flow"
        assert "lookup_lab" not in offered
        assert "investigate_lab" in offered  # positive control: not an empty manifest


def test_the_offered_set_is_a_real_subset_of_what_could_be_pinned():
    """Guards the vacuous-exclusion trap: a set that excludes a name which was
    never in the source is not an exclusion, it is a typo that looks like one."""

    assert ea.OFFERED_TOOLS <= set(model_ingress.PIN_TABLE), (
        "OFFERED_TOOLS names a tool model_ingress cannot pin -- it would be "
        "dropped silently by build_offers, offering fewer tools than declared"
    )
    assert "probe_lab" in model_ingress.PIN_TABLE, (
        "probe_lab is not in PIN_TABLE, so excluding it from OFFERED_TOOLS "
        "excludes nothing and the test above proves nothing"
    )
    assert "probe_lab" not in ea.OFFERED_TOOLS


def test_a_clean_finish_is_complete_whatever_the_provider_calls_its_stop_reason(
    monkeypatch, tmp_path
):
    """`complete` is a fact about this loop's control flow, not about a string.

    MiniMax on the Responses API ends a clean turn with `stop_reason:
    "completed"`; Anthropic says `"end_turn"`. Comparing against one
    vocabulary reported `complete=False` on 12 of 12 clean MiniMax runs, every
    one of which finished perfectly (OBS-701). `stopped_because` still carries
    the provider's own word verbatim -- that part was right and is kept.
    """

    responses = {
        "explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)],
        "investigate_lab": [_tool_result("investigate_lab", _INVESTIGATE_LAB_OK)],
    }
    for provider_word in ("completed", "end_turn", "stop", "finished_wibble"):
        turns = [
            {"text": "", "tool_calls": [{"id": "1", "name": "investigate_lab", "arguments": {}}],
             "stop_reason": "tool_calls"},
            {"text": "the descent found a hold-timer expiry", "tool_calls": [],
             "stop_reason": provider_word},
        ]
        run, _toolset, _caller, _decision = _open_ticket_run(
            monkeypatch, tmp_path / provider_word, turns=turns, toolset_responses=responses,
        )
        assert run.complete is True, f"a clean finish reported as incomplete for {provider_word!r}"
        assert run.stopped_because == provider_word, "the provider's own word, unfolded"

    # Negative controls: a bound, and a genuinely empty turn, are NOT complete.
    bounded = [
        {"text": "", "tool_calls": [{"id": "1", "name": "explore_lab", "arguments": {}}],
         "stop_reason": "tool_calls"},
    ] * 4
    run, _t, _c, _d = _open_ticket_run(
        monkeypatch, tmp_path / "bounded", turns=bounded,
        toolset_responses={"explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)] * 4},
        bounds=ea.AgentBounds(max_iterations=2),
    )
    assert run.complete is False
    assert run.stopped_because == "max_iterations"

    run, _t, _c, _d = _open_ticket_run(
        monkeypatch, tmp_path / "empty", toolset_responses={},
        turns=[{"text": "", "tool_calls": [], "stop_reason": "completed"}],
    )
    assert run.complete is False, "an empty turn must never read as a completed run"
    assert run.stopped_because == "empty_turn"


def test_protocol_level_is_error_is_classified_as_an_error():
    result = ToolCallResult(
        name="unknown_tool", arguments={}, text="Unknown tool: unknown_tool",
        is_error=True, elapsed_s=0.001, truncated_chars=None,
    )
    is_error, detail = ea._classify_call_result(result)
    assert is_error is True
    assert "Unknown tool" in detail


def test_a_classified_status_error_envelope_with_is_error_false_is_still_an_error():
    """The shape B-473's active-probe refusal actually returns: `is_error`
    is False at the protocol level, but `.text` carries a `status: "error"`
    envelope. Missing this would let a refused call read as successful."""

    payload = {"tool": "probe_lab", "device": "RR1", "status": "error", "data": {},
               "errors": ["active probes are disabled"]}
    result = ToolCallResult(
        name="probe_lab", arguments={}, text=json.dumps(payload), is_error=False,
        elapsed_s=0.001, truncated_chars=None,
    )
    is_error, detail = ea._classify_call_result(result)
    assert is_error is True
    assert "active probes are disabled" in detail


def test_a_genuinely_successful_result_is_not_misclassified():
    result = ToolCallResult(
        name="explore_lab", arguments={}, text=json.dumps(_EXPLORE_LAB_OK), is_error=False,
        elapsed_s=0.001, truncated_chars=None,
    )
    is_error, detail = ea._classify_call_result(result)
    assert is_error is False
    assert detail is None


# --------------------------------------------------------------------------- #
# OBS-698: no text AND no tool calls is the real anomaly -- empty text alone
# on a tool-calling turn is normal and must not be treated as one.
# --------------------------------------------------------------------------- #


def test_empty_text_on_a_tool_calling_turn_is_not_treated_as_an_error(monkeypatch, tmp_path):
    """OBS-698: a turn that requests tool calls with NO accompanying text is
    the normal shape of a tool-calling turn, not an anomaly -- it must reach
    dispatch exactly like a turn that also wrote prose, and its empty text
    is recorded as-is, not flagged or substituted."""

    turns = [
        {"text": "", "tool_calls": [{"id": "1", "name": "explore_lab", "arguments": {}}],
         "stop_reason": "tool_use"},
        {"text": "finding stated", "tool_calls": [], "stop_reason": "end_turn"},
    ]
    responses = {"explore_lab": [_tool_result("explore_lab", _EXPLORE_LAB_OK)]}
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses=responses,
    )
    assert run.complete is True
    assert run.stopped_because == "end_turn"
    # The FIRST turn's empty text (a normal tool-calling turn) is recorded
    # verbatim and never treated as the "empty_turn" anomaly -- dispatch
    # happened, proven by the tool actually being called.
    assert run.exchanges[0]["response_text"] == ""
    assert run.exchanges[0]["stop_reason"] == "tool_use"
    assert [c["tool"] for c in run.tool_calls] == ["explore_lab"]


def test_no_text_and_no_tool_calls_is_the_real_anomaly(monkeypatch, tmp_path):
    turns = [{"text": "", "tool_calls": [], "stop_reason": "some_provider_specific_pause"}]
    run, toolset, caller, decision = _open_ticket_run(
        monkeypatch, tmp_path, turns=turns, toolset_responses={},
    )
    assert run.stopped_because == "empty_turn"
    assert run.complete is False


# --------------------------------------------------------------------------- #
# Structural: event_agent never imports cli, event_watch, logs_loki,
# agent_loop, or mcp_server -- mirrors
# test_event_watch.test_the_watcher_never_imports_investigation_or_cli.
# --------------------------------------------------------------------------- #


def test_event_agent_never_imports_cli_or_agent_loop_or_the_mcp_server_package():
    source = inspect.getsource(ea)
    forbidden = (
        "from .cli import", "from . import cli", "import agent_nettools.cli",
        "from .agent_loop import", "from . import agent_loop", "import agent_nettools.agent_loop",
        "from .event_watch import", "from . import event_watch", "import agent_nettools.event_watch",
        "from .logs_loki import", "from . import logs_loki", "import agent_nettools.logs_loki",
        "import mcp_server", "from mcp_server import", "from mcp_server.",
        "from .llm_analysis import", "from . import llm_analysis",
    )
    for needle in forbidden:
        assert needle not in source, needle


def test_event_watch_never_imports_event_agent():
    """The other half of the same invariant, checked without touching
    `event_watch.py` or its own test file (both belong to another lane)."""

    import agent_nettools.event_watch as watcher_module

    source = inspect.getsource(watcher_module)
    forbidden = (
        "from .event_agent import", "from . import event_agent", "import agent_nettools.event_agent",
    )
    for needle in forbidden:
        assert needle not in source, needle


def test_event_agent_module_does_not_actually_import_logs_loki_transitively():
    """The whole reason `event_watch.build_trigger_index` is NOT reused --
    proven, not just claimed. Run in a FRESH subprocess: this test file
    itself may import `event_watch` (above), which WOULD pull `logs_loki`
    into `sys.modules` for the rest of this process -- a same-process check
    would therefore prove nothing either way. A subprocess that imports only
    `agent_nettools.event_agent` is the only way to observe its own,
    isolated import graph."""

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "-c",
            "import agent_nettools.event_agent\n"
            "import sys\n"
            "assert 'agent_nettools.logs_loki' not in sys.modules, sorted(sys.modules)\n"
            "print('ok')\n",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# --------------------------------------------------------------------------- #
# Duplicated-logic parity: this module's own build_trigger_index/
# validate_trigger_table agree with event_watch's, on the same inputs.
# --------------------------------------------------------------------------- #


def test_build_trigger_index_matches_event_watchs_own_on_the_real_table():
    assert ea.build_trigger_index() == ew.build_trigger_index()


def test_validate_trigger_table_agrees_with_event_watch_on_a_bad_table():
    bad = (
        {"mnemonic": "X", "investigate_with": None,
         "trigger": {"fires": True, "reason": "bad"}},
    )
    with pytest.raises(ea.TriggerTableInconsistency):
        ea.validate_trigger_table(bad)
    with pytest.raises(ew.TriggerTableInconsistency):
        ew.validate_trigger_table(bad)


# --------------------------------------------------------------------------- #
# AgentBounds / EventRun -- shape and round trip.
# --------------------------------------------------------------------------- #


def test_agent_bounds_defaults_match_the_specified_shape():
    bounds = ea.AgentBounds()
    assert bounds.max_iterations == 4
    assert bounds.time_budget_s == 90.0
    assert bounds.max_tool_calls == 6
    assert dict(bounds.max_calls_per_tool) == {"investigate_lab": 1}
    assert bounds.max_result_chars == 20_000


def test_event_run_as_dict_round_trips_every_field():
    run = ea.EventRun(
        ran=True, reason="x", mode="live", decision={"a": 1}, bounds={"b": 2},
        tools_offered=("explore_lab",), tool_calls=({"tool": "explore_lab"},),
        exchanges=({"purpose": "event_agent_turn"},), stopped_because="end_turn",
        complete=True, limits_hit=(), fabrication_attempts=(), ticket_run_id="abc",
        elapsed_s=1.5,
    )
    d = run.as_dict()
    assert d["tools_offered"] == ["explore_lab"]
    assert d["tool_calls"] == [{"tool": "explore_lab"}]
    assert d["ticket_run_id"] == "abc"
    assert d["complete"] is True
