"""The bounded, read-only tool-calling agent loop (Phase 6, Task C).

Uses a fake ``anthropic`` module (the pattern already established in
tests/test_llm_provider.py) so nothing here makes a real network or API
call. Device-touching tool calls that would otherwise open a real SSH
session go through ``install_fake_netmiko`` (tests/helpers.py), exactly like
every other test in this suite that exercises ``run_intent``/``check_fabric``.
"""

from __future__ import annotations

import json
import sys
import types

import pytest
from helpers import install_fake_netmiko, set_device_environment

from agent_nettools.agent_loop import _execute_tool, run_agent_loop
from agent_nettools.llm_analysis import LLMAnalysisError


class FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._message


def install_scripted_anthropic(monkeypatch, responses, *, captured=None):
    """Install a fake anthropic module that returns ``responses`` in order.

    Each entry is a callable ``(**kwargs) -> message``; once the list is
    exhausted, the last entry is reused for any further call (so a
    deliberately non-converging test does not need one entry per iteration).
    If ``captured`` is a list, every call's kwargs are appended to it.
    """

    module = types.ModuleType("anthropic")
    module.APIStatusError = type("APIStatusError", (Exception,), {})
    module.AuthenticationError = type("AuthenticationError", (module.APIStatusError,), {})
    module.NotFoundError = type("NotFoundError", (module.APIStatusError,), {})
    module.RateLimitError = type("RateLimitError", (module.APIStatusError,), {})
    module.APIConnectionError = type("APIConnectionError", (Exception,), {})

    state = {"n": 0}

    def make_stream(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        idx = min(state["n"], len(responses) - 1)
        state["n"] += 1
        message = responses[idx](**kwargs)
        return FakeStream(message)

    class FakeMessages:
        def stream(self, **kwargs):
            return make_stream(**kwargs)

    class FakeBetaMessages:
        def stream(self, **kwargs):
            return make_stream(**kwargs)

    class FakeBeta:
        def __init__(self):
            self.messages = FakeBetaMessages()

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()
            self.beta = FakeBeta()

    module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def _usage(**overrides):
    base = {
        "input_tokens": 5,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _text_message(text, stop_reason="end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=_usage(),
    )


def _refusal_message(category=None):
    details = types.SimpleNamespace(category=category) if category else None
    return types.SimpleNamespace(content=[], stop_reason="refusal", stop_details=details, usage=_usage())


def _tool_use_message(*tool_uses):
    blocks = [
        types.SimpleNamespace(type="tool_use", id=tu["id"], name=tu["name"], input=tu.get("input", {}))
        for tu in tool_uses
    ]
    return types.SimpleNamespace(content=blocks, stop_reason="tool_use", stop_details=None, usage=_usage())


@pytest.fixture(autouse=True)
def _anthropic_provider(monkeypatch):
    # Outside the Opus-5/Fable-5/Mythos-5 family so the plain (non-beta)
    # streaming path is exercised by default; the fake module supports both
    # paths regardless, but this keeps most tests' captured kwargs simple.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


# --------------------------------------------------------------------------- #
# Termination and bounds.
# --------------------------------------------------------------------------- #


def test_loop_terminates_on_end_turn(monkeypatch):
    install_scripted_anthropic(monkeypatch, [lambda **kw: _text_message("All neighbours are up.")])

    result = run_agent_loop("Is PE1 healthy?")

    assert result["answer"] == "All neighbours are up."
    assert result["iterations"] == 1
    assert result["stopped_because"] == "end_turn"
    assert result["tool_calls"] == []


def test_max_iterations_returns_partial_results_not_an_exception(monkeypatch):
    # A deliberately non-converging model: every turn asks for another tool call.
    def always_tool_use(**kwargs):
        return _tool_use_message({"id": "toolu_x", "name": "list_lab_devices"})

    install_scripted_anthropic(monkeypatch, [always_tool_use])
    set_device_environment(monkeypatch)

    result = run_agent_loop("Investigate forever", max_iterations=3)

    assert result["stopped_because"] == "max_iterations"
    assert result["iterations"] == 3
    assert isinstance(result["answer"], str)


def test_time_budget_returns_partial_results_not_an_exception(monkeypatch):
    def always_tool_use(**kwargs):
        return _tool_use_message({"id": "toolu_x", "name": "list_lab_devices"})

    install_scripted_anthropic(monkeypatch, [always_tool_use])
    set_device_environment(monkeypatch)

    # Schedule: start=0.0, then two cheap checks under budget, then a jump
    # past it. Clamped indexing means any extra calls just reuse the last
    # (already-expired) value, so this does not depend on exact call counts.
    schedule = [0.0, 0.1, 0.2, 5.0]
    state = {"i": 0}

    def fake_monotonic():
        idx = min(state["i"], len(schedule) - 1)
        state["i"] += 1
        return schedule[idx]

    monkeypatch.setattr("agent_nettools.agent_loop.time.monotonic", fake_monotonic)

    result = run_agent_loop("Investigate forever", max_iterations=100, time_budget_s=1)

    assert result["stopped_because"] == "time_budget"
    assert 1 <= result["iterations"] <= 3
    assert isinstance(result["answer"], str)


# --------------------------------------------------------------------------- #
# Tool round-tripping.
# --------------------------------------------------------------------------- #


def test_tool_use_round_trips_a_tool_result_with_the_matching_id(monkeypatch):
    set_device_environment(monkeypatch)
    captured = []
    responses = [
        lambda **kw: _tool_use_message({"id": "toolu_abc", "name": "list_lab_devices"}),
        lambda **kw: _text_message("Devices listed."),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("List the devices")

    assert result["answer"] == "Devices listed."
    assert result["stopped_because"] == "end_turn"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "list_lab_devices"
    assert result["tool_calls"][0]["is_error"] is False

    second_call_messages = captured[1]["messages"]
    tool_result_turn = second_call_messages[-1]
    assert tool_result_turn["role"] == "user"
    blocks = tool_result_turn["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "toolu_abc"
    assert blocks[0]["is_error"] is False


def test_parallel_tool_use_blocks_return_in_a_single_user_message(monkeypatch):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    captured = []
    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "list_lab_devices"},
            {"id": "toolu_2", "name": "run_lab_intent", "input": {"device_name": "PE1", "intent": "facts"}},
        ),
        lambda **kw: _text_message("Done."),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("Check PE1")

    assert len(result["tool_calls"]) == 2
    second_call_messages = captured[1]["messages"]
    tool_result_turn = second_call_messages[-1]
    assert tool_result_turn["role"] == "user"
    assert len(tool_result_turn["content"]) == 2
    ids = {block["tool_use_id"] for block in tool_result_turn["content"]}
    assert ids == {"toolu_1", "toolu_2"}


def test_unknown_tool_name_yields_is_error_result_not_a_dropped_call(monkeypatch):
    captured = []
    responses = [
        lambda **kw: _tool_use_message({"id": "toolu_1", "name": "not_a_real_tool"}),
        lambda **kw: _text_message("ok"),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("do something")

    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["is_error"] is True

    tool_result_block = captured[1]["messages"][-1]["content"][0]
    assert tool_result_block["is_error"] is True
    assert "not_a_real_tool" in tool_result_block["content"]


# --------------------------------------------------------------------------- #
# Every tool argument flows through the existing validation.
# --------------------------------------------------------------------------- #


def test_execute_tool_refuses_a_malicious_template_argument_directly():
    """No credentials or fake transport needed: render_command's validation
    runs before any device access, exactly as it does for a human CLI caller
    (see network_tools.run_template's ordering invariant)."""

    result = _execute_tool(
        "run_lab_template",
        {"device_name": "PE1", "template": "ping", "params": {"address": "10.0.0.1 | reload"}},
    )

    assert result["status"] == "error"
    assert any(
        "whitespace is not allowed" in message or "forbidden character" in message
        for message in result["errors"]
    )


def test_malicious_template_argument_is_refused_inside_the_loop(monkeypatch):
    captured = []
    responses = [
        lambda **kw: _tool_use_message(
            {
                "id": "toolu_1",
                "name": "run_lab_template",
                "input": {
                    "device_name": "PE1",
                    "template": "ping",
                    "params": {"address": "10.0.0.1 | reload"},
                },
            }
        ),
        lambda **kw: _text_message("The address was refused; here is what I found instead."),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("Ping 10.0.0.1 | reload from PE1")

    # A validation refusal arrives as a structured envelope with status="error"
    # rather than a Python exception, and the loop must map that onto is_error --
    # otherwise a refused call is recorded and reported to the model as a
    # success, which both corrupts the audit trail and denies the model the
    # signal it needs to try something else.
    call = result["tool_calls"][0]
    assert call["is_error"] is True
    # The internal audit trail (`tool_calls`, never sent to the model) is
    # built from the raw envelope, so it still carries the real reason.
    assert "whitespace is not allowed" in call["error"]
    tool_result_block = captured[1]["messages"][-1]["content"][0]
    assert tool_result_block["is_error"] is True
    content = tool_result_block["content"]
    # B-470/P0-02: `content` (what the model actually sees) is routed through
    # model_egress.project_envelope, which classifies every `errors` entry
    # against a fixed table rather than passing raw exception text through.
    # Validation-refusal phrases are IN that table (added after wave 2-B):
    # they originate in the frozen validators, are tool-authored, and the
    # agent loop's own prompt tells the model to act on WHY a call was
    # refused -- which is only possible if the kind crosses the boundary.
    # The offending value itself still never does.
    assert "refused" in content
    assert "whitespace is not allowed" in content
    assert "reload" not in content
    # The command was never assembled, so nothing resembling it reached a device.
    assert "| reload" not in content.replace("\\u007c", "|").split("errors")[0]


def test_unsupported_intent_is_not_reported_as_a_tool_error(monkeypatch):
    """The mirror of the test above: status="error" maps onto is_error, but
    status="unsupported" must not. A vendor that has no command for an intent is
    a property of the fabric, and flagging it as a failure would teach the model
    to retry something that can never succeed."""

    from agent_nettools import lab

    monkeypatch.setitem(lab.PLATFORMS, "PE2", "juniper_junos")  # no "sr" intent
    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "run_lab_intent", "input": {"device_name": "PE2", "intent": "sr"}}
        ),
        lambda **kw: _text_message("PE2 does not support SR-TE policies."),
    ]
    captured: list[dict] = []
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("Does PE2 have SR-TE policies?")

    call = result["tool_calls"][0]
    assert call["is_error"] is False
    assert call["error"] is None
    assert captured[1]["messages"][-1]["content"][0]["is_error"] is False
    assert "unsupported" in captured[1]["messages"][-1]["content"][0]["content"]


# --------------------------------------------------------------------------- #
# stop_reason handling.
# --------------------------------------------------------------------------- #


def test_refusal_raises_llm_analysis_error(monkeypatch):
    install_scripted_anthropic(monkeypatch, [lambda **kw: _refusal_message("cyber")])

    with pytest.raises(LLMAnalysisError, match="declined"):
        run_agent_loop("investigate")


def test_max_tokens_appends_the_truncation_notice(monkeypatch):
    install_scripted_anthropic(
        monkeypatch, [lambda **kw: _text_message("partial answer", stop_reason="max_tokens")]
    )

    result = run_agent_loop("investigate")

    assert result["stopped_because"] == "truncated"
    assert result["answer"].startswith("partial answer")
    assert "truncated" in result["answer"].lower()


# --------------------------------------------------------------------------- #
# Provider support (Task D): only Anthropic implements the loop.
# --------------------------------------------------------------------------- #


def test_openai_provider_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LLMAnalysisError, match="Anthropic"):
        run_agent_loop("investigate")


def test_ollama_provider_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(LLMAnalysisError, match="Anthropic"):
        run_agent_loop("investigate")


# --------------------------------------------------------------------------- #
# Prompt caching.
# --------------------------------------------------------------------------- #


def test_cache_prefix_is_byte_identical_across_different_questions(monkeypatch):
    """The static system prompt must be identical across calls -- caching is
    a prefix match, and only the (volatile) question/device may vary."""

    captured = []
    install_scripted_anthropic(monkeypatch, [lambda **kw: _text_message("ok")], captured=captured)

    run_agent_loop("What is wrong with PE1?")
    run_agent_loop("What is wrong with RR1? (a completely different question, id=abc-123)")

    assert len(captured) == 2
    assert captured[0]["system"] == captured[1]["system"]
    assert captured[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert captured[0]["messages"] != captured[1]["messages"]


# --------------------------------------------------------------------------- #
# Model-exchange instrumentation (OBS-165 follow-up): everything a caller
# needs to feed `ticket.Ticket.record_model_exchange`, without this module
# importing ticket.py or deciding when a ticket is opened.
# --------------------------------------------------------------------------- #


def test_the_returned_dict_carries_model_provider_and_prompt_identity(monkeypatch):
    from agent_nettools import agent_loop

    install_scripted_anthropic(monkeypatch, [lambda **kw: _text_message("All good.")])

    result = run_agent_loop("Is PE1 healthy?")

    assert result["model"] == "claude-sonnet-4-5"
    assert result["provider"] == "anthropic"
    assert result["system_prompt"] == agent_loop.AGENT_SYSTEM_PROMPT
    assert result["tools_offered"] == [t["name"] for t in agent_loop.TOOLS]
    # The full tool-schema JSON -- the 30k+-char manifest
    # `ticket.Ticket.record_model_exchange` is the one responsible for
    # content-addressing, not this function's job to shrink.
    assert json.loads(result["tools_manifest"]) == agent_loop.TOOLS


def test_each_turn_produces_one_instrumented_exchange(monkeypatch):
    """Two turns -- a tool_use turn, then an end_turn -- produce two
    `exchanges` entries, each carrying only ITS OWN turn's usage (a delta,
    not the running cumulative total) and stop reason, and the tool_use turn
    alone carries the tool call the model actually requested."""

    install_scripted_anthropic(
        monkeypatch,
        [
            lambda **kw: _tool_use_message(
                {"id": "toolu_1", "name": "list_lab_devices", "input": {}}
            ),
            lambda **kw: _text_message("PE1 and PE2 are both up."),
        ],
    )
    set_device_environment(monkeypatch)

    result = run_agent_loop("Is the fabric healthy?")

    exchanges = result["exchanges"]
    assert len(exchanges) == 2

    first, second = exchanges
    assert first["purpose"] == "agent_turn"
    assert first["iteration"] == 1
    assert first["stop_reason"] == "tool_use"
    # A pure tool_use turn carries no accompanying prose in this fixture --
    # an honest empty string, not an omitted field.
    assert first["response_text"] == ""
    assert first["tool_calls_requested"] == [
        {"tool": "list_lab_devices", "input": {}}
    ]
    # `_usage()` costs 5 input / 5 output tokens per call in this harness --
    # each turn's delta must show that turn's own cost, not the running total.
    assert first["usage"]["input_tokens"] == 5
    assert first["usage"]["output_tokens"] == 5

    assert second["iteration"] == 2
    assert second["stop_reason"] == "end_turn"
    assert second["response_text"] == "PE1 and PE2 are both up."
    assert "tool_calls_requested" not in second, (
        "a turn that made no tool call has nothing to report there"
    )
    assert second["usage"]["input_tokens"] == 5, (
        "the second turn's delta must not include the first turn's cost"
    )

    # The running total in `usage` (unchanged, existing field) IS cumulative --
    # confirms the per-exchange numbers above are genuinely a different,
    # non-cumulative view, not just a copy of the same dict.
    assert result["usage"]["input_tokens"] == 10
