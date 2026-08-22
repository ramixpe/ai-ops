"""`agent_nettools.event_caller` -- the adapter that lets any
`llm_analysis.ModelCaller` (a real MiniMax turn, or a fake of that same
shape) drive `event_agent.run_event`, which owns its own, differently-shaped
`ModelCaller` protocol.

Every test here fakes `llm_analysis.minimax_model_caller`'s own call shape
directly (a plain function returning a `llm_analysis.ModelTurn`) -- no API
key, no network, no `openai` client. `inspect.signature` pins the adapter's
own call shape against `event_agent.ModelCaller`'s documented parameter
names (`system`, `messages`, `tools`, `timeout_s` -- not `timeout`).
"""

from __future__ import annotations

import inspect

import pytest

from agent_nettools import event_caller as ec
from agent_nettools.llm_analysis import LLMAnalysisError, ModelTurn, TokenUsage, ToolCall


def _turn(**kwargs) -> ModelTurn:
    kwargs.setdefault("text", "")
    kwargs.setdefault("tool_calls", ())
    kwargs.setdefault("stop_reason", "completed")
    kwargs.setdefault("usage", TokenUsage(calls=1, input_tokens=10, output_tokens=5))
    return ModelTurn(**kwargs)


class _FakeMiniMaxCaller:
    """Stands in for `llm_analysis.minimax_model_caller`'s own shape:
    ``(*, system, messages, tools, timeout) -> ModelTurn``. Records every
    call it received so a test can assert on the *translated* Responses-API
    input this adapter built."""

    def __init__(self, turns: list[ModelTurn]):
        self._turns = list(turns)
        self.calls: list[dict] = []

    def __call__(self, *, system, messages, tools, timeout):
        self.calls.append(
            {"system": system, "messages": list(messages), "tools": list(tools), "timeout": timeout}
        )
        if not self._turns:
            raise AssertionError("fake MiniMax caller ran out of scripted turns")
        return self._turns.pop(0)


# --------------------------------------------------------------------------- #
# Signature -- pins the adapter against event_agent.ModelCaller's own names.
# --------------------------------------------------------------------------- #


def test_build_event_caller_returns_the_event_agent_shaped_signature():
    caller = ec.build_event_caller(_FakeMiniMaxCaller([_turn(text="hi")]))
    params = inspect.signature(caller).parameters
    assert set(params) == {"system", "messages", "tools", "timeout_s"}
    for p in params.values():
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_minimax_event_caller_is_prebuilt_and_matches_the_shape():
    params = inspect.signature(ec.minimax_event_caller).parameters
    assert set(params) == {"system", "messages", "tools", "timeout_s"}


# --------------------------------------------------------------------------- #
# 1. A text-only turn.
# --------------------------------------------------------------------------- #


def test_text_only_turn_translates_cleanly():
    fake = _FakeMiniMaxCaller([_turn(text="no fault found", stop_reason="completed")])
    caller = ec.build_event_caller(fake)

    result = caller(
        system="sys prompt",
        messages=[{"role": "user", "content": "Investigate device PE1"}],
        tools=[{"name": "explore_lab", "description": "d", "input_schema": {"type": "object", "properties": {}, "required": []}}],
        timeout_s=30.0,
    )

    assert result["text"] == "no fault found"
    assert result["tool_calls"] == []
    assert result["stop_reason"] == "completed"
    assert result["tokens"]["input_tokens"] == 10
    assert result["tokens"]["output_tokens"] == 5
    assert result["tokens"]["total_tokens"] == 15

    # The underlying MiniMax caller saw Responses-API shapes, not
    # event_agent's own -- system passed through, timeout_s -> timeout,
    # tools flattened to {"type": "function", ...}.
    call = fake.calls[0]
    assert call["system"] == "sys prompt"
    assert call["timeout"] == 30.0
    assert call["messages"] == [{"role": "user", "content": "Investigate device PE1"}]
    assert call["tools"] == [
        {
            "type": "function",
            "name": "explore_lab",
            "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    ]


# --------------------------------------------------------------------------- #
# 2. A tool-call turn with empty text (OBS-698's normal shape).
# --------------------------------------------------------------------------- #


def test_tool_call_turn_with_empty_text_translates_cleanly():
    fake = _FakeMiniMaxCaller([
        _turn(
            text="",
            tool_calls=(
                ToolCall(
                    id="call_1", name="check_lab", raw_arguments='{"intent": "bgp"}',
                    arguments={"intent": "bgp"}, parse_error=None,
                ),
            ),
            stop_reason="tool_calls",
        )
    ])
    caller = ec.build_event_caller(fake)

    result = caller(
        system="sys", messages=[{"role": "user", "content": "go"}],
        tools=[], timeout_s=10.0,
    )

    assert result["text"] == ""
    assert result["stop_reason"] == "tool_calls"
    assert result["tool_calls"] == [
        {
            "id": "call_1", "name": "check_lab", "arguments": {"intent": "bgp"},
            "raw_arguments": '{"intent": "bgp"}', "parse_error": None,
        }
    ]


# --------------------------------------------------------------------------- #
# 3. Multi-turn continuation -- function_call / function_call_output built
#    correctly and in order, echoing raw_arguments verbatim.
# --------------------------------------------------------------------------- #


def test_multi_turn_continuation_echoes_raw_arguments_and_preserves_order():
    turn1 = _turn(
        text="",
        tool_calls=(
            ToolCall(
                id="call_1", name="check_lab",
                # Deliberately odd whitespace/key order -- proves this is
                # ECHOED, not re-serialized from `.arguments` (which would
                # normalize it).
                raw_arguments='{ "intent":  "bgp" }',
                arguments={"intent": "bgp"}, parse_error=None,
            ),
        ),
        stop_reason="tool_calls",
    )
    turn2 = _turn(text="BGP session is up; no fault found.", stop_reason="completed")
    fake = _FakeMiniMaxCaller([turn1, turn2])
    caller = ec.build_event_caller(fake)

    # This mirrors exactly what event_agent.run_event does to its own
    # `messages` list between turns.
    messages: list[dict] = [{"role": "user", "content": "Investigate PE1"}]

    first = caller(system="sys", messages=messages, tools=[], timeout_s=30.0)
    messages.append({"role": "assistant", "text": first["text"], "tool_calls": first["tool_calls"]})
    messages.append({
        "role": "tool_results",
        "results": [{"id": "call_1", "name": "check_lab", "content": '{"status": "ok"}', "is_error": False}],
    })

    second = caller(system="sys", messages=messages, tools=[], timeout_s=30.0)
    assert second["text"] == "BGP session is up; no fault found."

    # The second call to the underlying MiniMax caller must have received
    # the rebuilt, explicit input list -- never previous_response_id.
    second_call = fake.calls[1]
    assert second_call["messages"] == [
        {"role": "user", "content": "Investigate PE1"},
        {
            "type": "function_call", "call_id": "call_1", "name": "check_lab",
            "arguments": '{ "intent":  "bgp" }',  # echoed verbatim, not re-serialized
        },
        {"type": "function_call_output", "call_id": "call_1", "output": '{"status": "ok"}'},
    ]


def test_tool_results_error_is_folded_into_the_output_text():
    fake = _FakeMiniMaxCaller([_turn(text="ok")])
    caller = ec.build_event_caller(fake)
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "text": "", "tool_calls": [
            {"id": "c1", "name": "check_lab", "arguments": {"intent": "bgp"}, "raw_arguments": '{"intent":"bgp"}'}
        ]},
        {"role": "tool_results", "results": [
            {"id": "c1", "name": "check_lab", "content": "refused: budget exhausted", "is_error": True}
        ]},
    ]
    caller(system="sys", messages=messages, tools=[], timeout_s=5.0)
    output_items = [m for m in fake.calls[0]["messages"] if m.get("type") == "function_call_output"]
    assert output_items == [
        {"type": "function_call_output", "call_id": "c1", "output": "[ERROR] refused: budget exhausted"}
    ]


# --------------------------------------------------------------------------- #
# 4. A parse-failed tool call -- {} passed through, never repaired.
# --------------------------------------------------------------------------- #


def test_parse_failed_tool_call_passes_through_empty_arguments_and_surfaces_the_error():
    fake = _FakeMiniMaxCaller([
        _turn(
            text="",
            tool_calls=(
                ToolCall(
                    id="call_2", name="history_lab", raw_arguments='{"mode": ',
                    arguments=None, parse_error="Expecting value: line 1 column 11 (char 10)",
                ),
            ),
            stop_reason="tool_calls",
        )
    ])
    caller = ec.build_event_caller(fake)

    result = caller(system="sys", messages=[{"role": "user", "content": "go"}], tools=[], timeout_s=5.0)

    assert result["tool_calls"] == [
        {
            "id": "call_2", "name": "history_lab", "arguments": {},
            "raw_arguments": '{"mode": ', "parse_error": "Expecting value: line 1 column 11 (char 10)",
        }
    ]
    # Never synthesized from raw_arguments -- arguments is exactly {}.
    assert result["tool_calls"][0]["arguments"] == {}


def test_on_tool_call_hook_observes_every_tool_call_including_parse_failures():
    fake = _FakeMiniMaxCaller([
        _turn(
            text="",
            tool_calls=(
                ToolCall(id="a", name="check_lab", raw_arguments='{"intent":"bgp"}', arguments={"intent": "bgp"}),
                ToolCall(id="b", name="history_lab", raw_arguments="not json", arguments=None, parse_error="bad json"),
            ),
            stop_reason="tool_calls",
        )
    ])
    seen: list[dict] = []
    caller = ec.build_event_caller(fake, on_tool_call=seen.append)
    caller(system="sys", messages=[{"role": "user", "content": "go"}], tools=[], timeout_s=5.0)

    assert [c["id"] for c in seen] == ["a", "b"]
    assert seen[1]["arguments"] == {}
    assert seen[1]["parse_error"] == "bad json"


# --------------------------------------------------------------------------- #
# The empty-turn invariant propagates unchanged -- never caught here.
# --------------------------------------------------------------------------- #


def test_empty_turn_invariant_propagates_uncaught():
    def exploding_underlying_caller(*, system, messages, tools, timeout):
        # A real provider call that itself hits ModelTurn's own
        # __post_init__ invariant (no text, no tool calls).
        return ModelTurn(text="", tool_calls=(), stop_reason="completed", usage=TokenUsage(calls=1))

    caller = ec.build_event_caller(exploding_underlying_caller)
    with pytest.raises(LLMAnalysisError, match="no text and no tool calls"):
        caller(system="sys", messages=[{"role": "user", "content": "go"}], tools=[], timeout_s=5.0)


def test_underlying_caller_error_propagates_uncaught():
    def raising_caller(*, system, messages, tools, timeout):
        raise LLMAnalysisError("MINIMAX_API_KEY was rejected.")

    caller = ec.build_event_caller(raising_caller)
    with pytest.raises(LLMAnalysisError, match="MINIMAX_API_KEY was rejected"):
        caller(system="sys", messages=[{"role": "user", "content": "go"}], tools=[], timeout_s=5.0)
