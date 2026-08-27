"""Tests for the tool-calling turn added to the Responses API route (OBS-698).

``llm_analysis._openai_model_call``/``minimax_model_caller``/
``openai_model_caller`` are the tool-calling sibling of the existing
single-string ``_openai_call`` path (covered by ``test_llm_provider.py``,
untouched by this task). The central thing under test is the invariant
OBS-698 states exactly:

    A turn with no text AND no tool calls is a structured error, never an
    answer.

...and specifically that it is NOT written as "empty text is an error" --
OBS-698 measured live against MiniMax-M3 that a *successful* tool call
returns ``output_text == ''`` alongside a populated ``function_call`` item.
``test_a_successful_tool_call_with_empty_text_is_accepted`` and
``test_a_genuinely_empty_turn_is_rejected`` are the accept/reject pair that
proves the distinction is actually implemented, not just documented
(OBS-181).
"""

from __future__ import annotations

import inspect
import sys
import types

import pytest

from agent_nettools.llm_analysis import (
    MAX_OUTPUT_TOKENS,
    MINIMAX_BASE_URL_DEFAULT,
    MINIMAX_MODEL_DEFAULT,
    LLMAnalysisError,
    ModelCaller,
    ModelTurn,
    _openai_model_call,
    minimax_model_caller,
    openai_model_caller,
)

# --------------------------------------------------------------------------- #
# Fakes -- a fresh, self-contained fake `openai` module per test file, same
# shape as `test_llm_provider.py`'s `install_fake_openai` but extended with
# `.output`/`.status`/`.error`, which that file's `fake_openai_response`
# never needed because the single-string path never reads them.
# --------------------------------------------------------------------------- #


class FakeOpenAIAPIStatusError(Exception):
    def __init__(self, message="upstream failure", status_code=500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class FakeOpenAIAPIConnectionError(Exception):
    pass


def install_fake_openai(monkeypatch, respond, *, captured_client=None, captured_calls=None):
    """Install a fake `openai` module whose `responses.create(**kwargs)` calls
    `respond(**kwargs)` to get a response object (or raises)."""

    module = types.ModuleType("openai")
    module.APIStatusError = FakeOpenAIAPIStatusError
    module.AuthenticationError = type("AuthenticationError", (FakeOpenAIAPIStatusError,), {})
    module.NotFoundError = type("NotFoundError", (FakeOpenAIAPIStatusError,), {})
    module.RateLimitError = type("RateLimitError", (FakeOpenAIAPIStatusError,), {})
    module.APIConnectionError = FakeOpenAIAPIConnectionError

    class FakeResponses:
        def create(self, **kwargs):
            if captured_calls is not None:
                captured_calls.append(kwargs)
            return respond(**kwargs)

    class FakeOpenAIClient:
        def __init__(self, **kwargs):
            if captured_client is not None:
                captured_client.append(kwargs)
            self.responses = FakeResponses()

    module.OpenAI = FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", module)
    return module


def fake_function_call_item(*, call_id="call_1", name="check_lab", arguments='{"protocol": "bgp"}'):
    return types.SimpleNamespace(
        type="function_call", call_id=call_id, id=f"{call_id}_fc", name=name, arguments=arguments
    )


def fake_response(
    *,
    output_text="",
    output=None,
    status="completed",
    incomplete_reason=None,
    error=None,
    usage=None,
):
    incomplete_details = (
        types.SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
    )
    return types.SimpleNamespace(
        output_text=output_text,
        output=output if output is not None else [],
        status=status,
        incomplete_details=incomplete_details,
        error=error,
        usage=usage,
    )


def call_minimax(monkeypatch, respond, **caller_kwargs):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    install_fake_openai(monkeypatch, respond)
    caller_kwargs.setdefault("system", "")
    caller_kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
    caller_kwargs.setdefault("tools", [])
    caller_kwargs.setdefault("timeout", None)
    return minimax_model_caller(**caller_kwargs)


# --------------------------------------------------------------------------- #
# The accept/reject pair (OBS-181, OBS-698).
# --------------------------------------------------------------------------- #


def test_a_successful_tool_call_with_empty_text_is_accepted(monkeypatch):
    """OBS-698's exact trap: `output_text == ''` alongside a populated
    `function_call` item is a SUCCESSFUL turn, not an error."""

    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="", output=[fake_function_call_item()]
        ),
    )

    assert turn.text == ""
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.name == "check_lab"
    assert call.arguments == {"protocol": "bgp"}
    assert call.parse_error is None
    assert turn.stop_reason == "tool_calls"


def test_a_genuinely_empty_turn_is_rejected(monkeypatch):
    """The paired negative control: no text AND no tool calls at all --
    the only shape the invariant actually exists to catch."""

    with pytest.raises(LLMAnalysisError, match="no text and no tool calls"):
        call_minimax(
            monkeypatch,
            lambda **kwargs: fake_response(output_text="", output=[]),
        )


# --------------------------------------------------------------------------- #
# Ordinary, non-trap turns.
# --------------------------------------------------------------------------- #


def test_a_text_only_turn_is_accepted(monkeypatch):
    turn = call_minimax(
        monkeypatch, lambda **kwargs: fake_response(output_text="All neighbours are up.")
    )

    assert turn.text == "All neighbours are up."
    assert turn.tool_calls == ()
    assert turn.stop_reason == "completed"


def test_a_turn_with_both_text_and_a_tool_call_is_accepted(monkeypatch):
    """The live probe run for this task (2026-08-22, MiniMax-M3) actually
    returned commentary text ALONGSIDE the function_call item on one run --
    OBS-698's `''` case is not the only shape a successful tool call takes."""

    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="I'll call check_lab now.", output=[fake_function_call_item()]
        ),
    )

    assert turn.text == "I'll call check_lab now."
    assert len(turn.tool_calls) == 1
    assert turn.stop_reason == "tool_calls"


def test_truncated_turn_reports_the_incomplete_reason_as_stop_reason(monkeypatch):
    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="partial answer",
            status="incomplete",
            incomplete_reason="max_output_tokens",
        ),
    )

    assert turn.text == "partial answer"
    assert turn.stop_reason == "max_output_tokens"


def test_provider_reported_error_is_raised_before_the_empty_turn_check(monkeypatch):
    """A 200 the provider used to signal failure (`response.error`) gets a
    specific message, not the generic empty-turn one."""

    error = types.SimpleNamespace(message="content policy violation", code="bio_policy")
    with pytest.raises(LLMAnalysisError, match="content policy violation"):
        call_minimax(monkeypatch, lambda **kwargs: fake_response(output_text="", error=error))


# --------------------------------------------------------------------------- #
# Malformed / truncated tool-call arguments: parsed defensively, never
# repaired or guessed at.
# --------------------------------------------------------------------------- #


def test_unparseable_tool_arguments_are_not_repaired_or_guessed(monkeypatch):
    """A cut-off `arguments` string that fails to parse as JSON must not be
    silently patched into something that does parse. The raw text survives
    unchanged, and the failure is explicit."""

    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="",
            output=[fake_function_call_item(arguments='{"protocol": "bg')],
        ),
    )

    # A malformed tool call is still a tool call -- the empty-turn invariant
    # must not fire just because parsing failed.
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.arguments is None
    assert call.parse_error is not None
    assert call.raw_arguments == '{"protocol": "bg'  # untouched, not closed/guessed


def test_tool_arguments_that_parse_to_a_non_object_are_a_parse_error(monkeypatch):
    """Valid JSON that is not an object (e.g. a bare string) is not silently
    accepted as if it were `{}` or coerced into one."""

    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="",
            output=[fake_function_call_item(arguments='"bgp"')],
        ),
    )

    call = turn.tool_calls[0]
    assert call.arguments is None
    assert "not a JSON object" in call.parse_error
    assert call.raw_arguments == '"bgp"'


def test_a_truncated_but_syntactically_valid_tool_call_is_not_flagged_as_malformed(monkeypatch):
    """Measured live: MiniMax under a tight token budget can close a tool
    call's JSON early but validly (e.g. a long string field cut short). That
    is a real, parseable call -- `parse_error` must stay `None` -- even
    though its *content* is truncated. Semantic truncation is a
    `stop_reason` question, not a parse-error question."""

    turn = call_minimax(
        monkeypatch,
        lambda **kwargs: fake_response(
            output_text="",
            output=[fake_function_call_item(arguments='{"protocol": "bgp", "note": "because Border"}')],
        ),
    )

    call = turn.tool_calls[0]
    assert call.parse_error is None
    assert call.arguments == {"protocol": "bgp", "note": "because Border"}


# --------------------------------------------------------------------------- #
# Request wiring: messages pass through verbatim, system -> instructions,
# tools/instructions omitted when empty.
# --------------------------------------------------------------------------- #


def test_messages_are_passed_through_to_input_verbatim(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_calls=create_calls,
    )

    messages = [
        {"role": "user", "content": "Call the check_lab tool with protocol=bgp."},
        {"type": "function_call", "call_id": "call_1", "name": "check_lab", "arguments": '{"protocol": "bgp"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"state": "Established"}'},
    ]

    minimax_model_caller(system="", messages=messages, tools=[], timeout=None)

    assert create_calls[0]["input"] == messages


def test_system_becomes_instructions_and_is_omitted_when_empty(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_calls=create_calls,
    )

    minimax_model_caller(
        system="You are a network assistant.",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        timeout=None,
    )
    assert create_calls[0]["instructions"] == "You are a network assistant."

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )
    assert "instructions" not in create_calls[1]


def test_tools_are_omitted_when_empty_and_sent_when_present(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_calls=create_calls,
    )

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )
    assert "tools" not in create_calls[0]

    tools = [{"type": "function", "name": "check_lab", "parameters": {}}]
    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=tools, timeout=None
    )
    assert create_calls[1]["tools"] == tools


def test_max_output_tokens_is_still_set_on_the_tool_calling_path(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_calls=create_calls,
    )

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )

    assert create_calls[0]["max_output_tokens"] == MAX_OUTPUT_TOKENS


# --------------------------------------------------------------------------- #
# Provider wiring: minimax_model_caller / openai_model_caller bind
# `_openai_model_call` the same way analyze_with_minimax / analyze_with_openai
# bind `_openai_call` -- mirrors test_llm_provider.py's equivalent checks for
# the single-string path.
# --------------------------------------------------------------------------- #


def test_minimax_model_caller_wires_the_minimax_endpoint(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    client_calls = []
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_client=client_calls,
        captured_calls=create_calls,
    )

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )

    assert client_calls[0]["base_url"] == MINIMAX_BASE_URL_DEFAULT
    assert client_calls[0]["api_key"] == "test-minimax-key"
    assert create_calls[0]["model"] == MINIMAX_MODEL_DEFAULT


def test_openai_model_caller_constructs_client_with_no_base_url_kwarg(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    client_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_client=client_calls,
    )

    openai_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )

    assert "base_url" not in client_calls[0]
    assert client_calls[0]["api_key"] == "test-openai-key"


def test_openrouter_model_caller_wires_the_measurement_endpoint(monkeypatch):
    from agent_nettools.llm_analysis import openrouter_model_caller

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    client_calls = []
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_client=client_calls,
        captured_calls=create_calls,
    )

    openrouter_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )

    assert client_calls[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert client_calls[0]["api_key"] == "test-openrouter-key"
    assert create_calls[0]["model"] == "deepseek/deepseek-v4-flash-vision-exp"


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("NotFoundError", "Unknown MiniMax model"),
        ("RateLimitError", "Rate limited by the MiniMax API"),
        ("APIStatusError", "MiniMax API error"),
        ("APIConnectionError", "Could not reach the MiniMax API"),
    ],
)
def test_minimax_model_caller_error_wording_names_minimax(monkeypatch, error_name, expected):
    """Regression guard for the `_call_openai_or_raise` refactor: the shared
    exception-mapping helper must still name the provider that actually
    failed on this new path, exactly as it does on the legacy one
    (test_llm_provider.py's `test_minimax_errors_name_minimax_not_openai`)."""

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

    def raising_respond(**kwargs):
        raise getattr(sys.modules["openai"], error_name)("upstream failure")

    install_fake_openai(monkeypatch, raising_respond)

    with pytest.raises(LLMAnalysisError, match=expected):
        minimax_model_caller(
            system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
        )


def test_minimax_model_caller_auth_error_names_minimax_api_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

    def raising_respond(**kwargs):
        raise sys.modules["openai"].AuthenticationError("bad key")

    install_fake_openai(monkeypatch, raising_respond)

    with pytest.raises(LLMAnalysisError, match="MINIMAX_API_KEY was rejected"):
        minimax_model_caller(
            system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
        )


def test_timeout_flows_through_to_client_construction(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.setenv("NETTOOLS_LLM_TIMEOUT_SECONDS", "33")
    client_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_response(output_text="ok"),
        captured_client=client_calls,
    )

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=None
    )
    assert client_calls[0]["timeout"] == 33.0

    minimax_model_caller(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], timeout=9.0
    )
    assert client_calls[1]["timeout"] == 9.0


# --------------------------------------------------------------------------- #
# Usage (TokenUsage.reported -- "the provider did not say" vs "it cost
# nothing" are different facts, and this path must not collapse them either).
# --------------------------------------------------------------------------- #


def test_usage_is_captured_when_the_provider_reports_it(monkeypatch):
    usage = types.SimpleNamespace(input_tokens=120, output_tokens=40)
    turn = call_minimax(monkeypatch, lambda **kwargs: fake_response(output_text="ok", usage=usage))

    assert turn.usage.input_tokens == 120
    assert turn.usage.output_tokens == 40
    assert turn.usage.reported is True


def test_usage_reported_false_when_the_provider_omits_it(monkeypatch):
    turn = call_minimax(monkeypatch, lambda **kwargs: fake_response(output_text="ok", usage=None))

    assert turn.usage.reported is False


# --------------------------------------------------------------------------- #
# ModelCaller: the shape another lane's loop depends on and fakes in tests.
# --------------------------------------------------------------------------- #


def test_minimax_and_openai_model_callers_match_the_modelcaller_call_shape():
    """Nothing in this repo runs mypy in CI (`make lint` is `ruff check .`
    only -- see CLAUDE.md), so `ModelCaller` conformance is not statically
    checked anywhere. This pins it at runtime: both concrete callers must
    accept exactly `ModelCaller`'s four keyword-only parameters, so a loop
    written against the protocol can call either interchangeably."""

    expected = {"system", "messages", "tools", "timeout"}
    for caller in (minimax_model_caller, openai_model_caller, _openai_model_call):
        signature = inspect.signature(caller)
        names = set(signature.parameters)
        assert expected <= names, f"{caller.__name__} is missing {expected - names}"
        for name in expected:
            assert signature.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_a_hand_rolled_fake_satisfies_modelcaller_and_can_stand_in_for_the_real_one():
    """Demonstrates the point of the protocol: a loop's test suite can fake
    a whole turn with a plain function, no `openai.OpenAI` client involved."""

    def fake_caller(*, system, messages, tools, timeout) -> ModelTurn:
        from agent_nettools.llm_analysis import TokenUsage

        return ModelTurn(
            text="ok",
            tool_calls=(),
            stop_reason="completed",
            usage=TokenUsage(calls=1, reported=False),
        )

    caller: ModelCaller = fake_caller  # structural typing -- no inheritance needed
    turn = caller(system="sys", messages=[], tools=[], timeout=None)
    assert turn.stop_reason == "completed"


def test_modelturn_construction_itself_enforces_the_invariant():
    """The invariant is enforced at the type's own construction
    (`ModelTurn.__post_init__`), not only inside `_model_turn_from_response`
    -- so ANY future code path that builds a `ModelTurn` directly (a
    different provider integration, a hand-rolled fake used for the wrong
    purpose) gets it for free. D2's principle, named for this exact route by
    OBS-010: make the unsafe state unrepresentable, not merely instructed
    against."""

    from agent_nettools.llm_analysis import TokenUsage

    with pytest.raises(LLMAnalysisError, match="no text and no tool calls"):
        ModelTurn(text="", tool_calls=(), stop_reason="completed", usage=TokenUsage(calls=1))
