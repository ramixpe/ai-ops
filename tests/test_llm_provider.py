import sys
import types

import pytest

from agent_nettools.llm_analysis import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MINIMAX_BASE_URL_DEFAULT,
    MINIMAX_MODEL_DEFAULT,
    TRUNCATION_NOTICE,
    LLMAnalysisError,
    analyze_with_anthropic,
    analyze_with_minimax,
    analyze_with_openai,
    complete_prompt,
    get_provider,
)
from agent_nettools.prompt_library import RenderedPrompt


def test_provider_requires_a_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="No LLM API key"):
        get_provider()


def test_provider_rejects_unsupported_value(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")

    with pytest.raises(ValueError, match="LLM_PROVIDER must be"):
        get_provider()


def test_provider_auto_prefers_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    assert get_provider() == "anthropic"


def test_provider_auto_uses_openai_when_anthropic_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    assert get_provider() == "openai"


def test_provider_ollama_selected_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_provider() == "ollama"


def test_unsupported_error_lists_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    with pytest.raises(ValueError, match="ollama"):
        get_provider()


def test_provider_minimax_selected_with_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax")
    assert get_provider() == "minimax"


def test_provider_minimax_requires_a_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MINIMAX_API_KEY is required"):
        get_provider()


def test_provider_auto_never_selects_minimax(monkeypatch):
    """Guardrail for the constraint that LLM_PROVIDER=auto must never pick
    minimax -- this must fail if minimax is ever added to the auto chain."""

    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="No LLM API key"):
        get_provider()


def test_unsupported_error_lists_minimax(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    with pytest.raises(ValueError, match="minimax"):
        get_provider()


class FakeAPIStatusError(Exception):
    """Stand-in for anthropic.APIStatusError, which carries status_code/message."""

    def __init__(self, message="upstream failure", status_code=500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class FakeAPIConnectionError(Exception):
    pass


class FakeStream:
    """Stand-in for the context manager ``client.messages.stream(...)`` returns."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._message


def install_fake_anthropic(monkeypatch, respond, *, captured=None):
    """Install a fake anthropic module whose ``messages.stream``/``beta.messages.stream``
    call ``respond(**kwargs)`` to get a message (or let it raise).

    ``respond`` receives every keyword argument the real SDK call would (model,
    max_tokens, system, messages, tools, and -- on the beta path -- betas/
    fallbacks). If ``captured`` is a list, every call's kwargs are appended to
    it so a test can inspect exactly what was sent.
    """

    module = types.ModuleType("anthropic")
    module.APIStatusError = FakeAPIStatusError
    module.AuthenticationError = type("AuthenticationError", (FakeAPIStatusError,), {})
    module.NotFoundError = type("NotFoundError", (FakeAPIStatusError,), {})
    module.RateLimitError = type("RateLimitError", (FakeAPIStatusError,), {})
    module.APIConnectionError = FakeAPIConnectionError

    def make_stream(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        message = respond(**kwargs)
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


def fake_usage(**overrides):
    base = {
        "input_tokens": 10,
        "output_tokens": 10,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def fake_message(text, stop_reason="end_turn", *, stop_details=None, usage=None, content=None):
    if content is None:
        content = [types.SimpleNamespace(type="text", text=text)]
    return types.SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage or fake_usage(),
    )


# Every test below uses a model outside the Opus-5/Fable-5/Mythos-5 family so
# fallbacks are not requested and the plain (non-beta) streaming path is
# exercised -- matching this lab's own .env, which pins claude-sonnet-4-5.
@pytest.fixture(autouse=True)
def _pin_non_fallback_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def test_anthropic_requests_the_full_token_budget(monkeypatch):
    calls = []

    def respond(**kwargs):
        return fake_message("## Summary\nAll neighbours are up.")

    install_fake_anthropic(monkeypatch, respond, captured=calls)

    analysis = analyze_with_anthropic({"device": "PE1"})

    assert calls[0]["max_tokens"] == ANTHROPIC_MAX_OUTPUT_TOKENS
    assert analysis == "## Summary\nAll neighbours are up."


def test_anthropic_flags_a_truncated_analysis(monkeypatch):
    install_fake_anthropic(
        monkeypatch,
        lambda **kwargs: fake_message("## Summary\nPartial", stop_reason="max_tokens"),
    )

    analysis = analyze_with_anthropic({"device": "PE1"})

    assert analysis.startswith("## Summary\nPartial")
    assert "truncated" in analysis.lower()


def test_anthropic_refusal_becomes_an_analysis_error(monkeypatch):
    install_fake_anthropic(
        monkeypatch,
        lambda **kwargs: fake_message("", content=[], stop_reason="refusal"),
    )

    with pytest.raises(LLMAnalysisError, match="declined"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_refusal_reads_stop_details_category(monkeypatch):
    """``stop_details`` must be read only when ``stop_reason == 'refusal'`` --
    it is null otherwise -- and its category should surface in the error."""

    details = types.SimpleNamespace(category="cyber")
    install_fake_anthropic(
        monkeypatch,
        lambda **kwargs: fake_message("", content=[], stop_reason="refusal", stop_details=details),
    )

    with pytest.raises(LLMAnalysisError, match="cyber"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_api_error_becomes_an_analysis_error(monkeypatch):
    def respond(**kwargs):
        raise FakeAPIStatusError("service unavailable", status_code=503)

    install_fake_anthropic(monkeypatch, respond)

    with pytest.raises(LLMAnalysisError, match="503"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_connection_error_becomes_an_analysis_error(monkeypatch):
    def respond(**kwargs):
        raise FakeAPIConnectionError("dns failure")

    install_fake_anthropic(monkeypatch, respond)

    with pytest.raises(LLMAnalysisError, match="Could not reach"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_cached_prefix_is_byte_identical_across_different_evidence(monkeypatch):
    """Prompt caching is a prefix match: the static system block must be
    byte-for-byte identical across calls with different volatile evidence, or
    nothing is ever cacheable. Only the message content may vary."""

    calls = []
    install_fake_anthropic(
        monkeypatch,
        lambda **kwargs: fake_message("ok"),
        captured=calls,
    )

    analyze_with_anthropic({"device": "PE1", "timestamp": "2020-01-01T00:00:00Z"})
    analyze_with_anthropic({"device": "RR1", "timestamp": "2099-12-31T23:59:59Z"})

    assert len(calls) == 2
    assert calls[0]["system"] == calls[1]["system"]
    # And the cache_control marker itself must be present, or there is
    # nothing to hit on the next call.
    assert calls[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # The volatile evidence must differ -- otherwise this test would pass
    # even if the code accidentally cached the wrong (evidence-bearing) block.
    assert calls[0]["messages"] != calls[1]["messages"]


def test_anthropic_fallbacks_enabled_for_opus_5_family(monkeypatch):
    """fallbacks="default" must only be requested for the Opus-5/Fable-5/
    Mythos-5 family, via the beta endpoint."""

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5")
    calls = []
    install_fake_anthropic(monkeypatch, lambda **kwargs: fake_message("ok"), captured=calls)

    analyze_with_anthropic({"device": "PE1"})

    assert calls[0]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in calls[0]["betas"]


def test_anthropic_fallbacks_disabled_by_env_var_even_for_opus_5(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5")
    monkeypatch.setenv("NETTOOLS_LLM_FALLBACKS", "0")
    calls = []
    install_fake_anthropic(monkeypatch, lambda **kwargs: fake_message("ok"), captured=calls)

    analyze_with_anthropic({"device": "PE1"})

    assert "fallbacks" not in calls[0]
    assert "betas" not in calls[0]


def test_anthropic_fallbacks_not_requested_for_non_opus_5_model(monkeypatch):
    # The autouse fixture already pins claude-sonnet-4-5; this is the default
    # behaviour under test, spelled out explicitly.
    calls = []
    install_fake_anthropic(monkeypatch, lambda **kwargs: fake_message("ok"), captured=calls)

    analyze_with_anthropic({"device": "PE1"})

    assert "fallbacks" not in calls[0]


# --------------------------------------------------------------------------- #
# B-421 -- prompt caching on the rendered-prompt path (`complete_prompt`)
#
# `complete_prompt` used to receive one fully-rendered string with the static
# template and the volatile payload already interleaved, so nothing was ever
# cacheable -- caching is a prefix match on `system`, and the "static" prefix
# changed on every call the moment the payload did. `build_report_prompt`/
# `build_correlate_prompt` now return a `RenderedPrompt` (system/user
# pre-split); these tests exercise `complete_prompt` directly with two
# `RenderedPrompt`s that share a `system` but differ in `user`, mirroring
# `test_anthropic_cached_prefix_is_byte_identical_across_different_evidence`
# above -- the same claim, made about the rendered-prompt path instead of the
# single-shot `analyze_evidence` path.
# --------------------------------------------------------------------------- #


def test_complete_prompt_sends_a_cacheable_system_block_on_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    calls = []
    install_fake_anthropic(monkeypatch, lambda **kwargs: fake_message("ok"), captured=calls)

    static = "GROUNDING\n=========\nsome fixed instructions\n"
    complete_prompt(RenderedPrompt(system=static, user='{"descent": "one"}'))
    complete_prompt(RenderedPrompt(system=static, user='{"descent": "two"}'))

    assert len(calls) == 2
    # The system block itself, not just its text: byte-identical, or nothing
    # is cacheable.
    assert calls[0]["system"] == calls[1]["system"]
    assert calls[0]["system"] == [
        {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}
    ]
    # The volatile half must actually vary, or this test would pass even if
    # `complete_prompt` accidentally cached the payload-bearing block instead.
    assert calls[0]["messages"] != calls[1]["messages"]
    assert calls[0]["messages"] == [{"role": "user", "content": '{"descent": "one"}'}]


def test_complete_prompt_surfaces_anthropic_cache_usage(monkeypatch):
    """`TokenUsage.cache_read_input_tokens`/`cache_creation_input_tokens`
    (B-421) must actually carry what the API reported, not just exist as
    zeroed fields nothing populates."""

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    usage = fake_usage(cache_read_input_tokens=123, cache_creation_input_tokens=45)
    install_fake_anthropic(monkeypatch, lambda **kwargs: fake_message("ok", usage=usage))

    completion = complete_prompt(RenderedPrompt(system="static", user="volatile"))

    assert completion.usage.cache_read_input_tokens == 123
    assert completion.usage.cache_creation_input_tokens == 45


def test_complete_prompt_concatenates_system_and_user_for_openai(monkeypatch):
    """No prefix cache on the Responses API -- `complete_prompt` must send
    exactly the string a single fully-rendered prompt would have been, so
    behaviour on this path is unchanged by the B-421 split."""

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    calls = []
    install_fake_openai(
        monkeypatch, lambda **kwargs: fake_openai_response("ok"), captured_calls=calls
    )

    complete_prompt(RenderedPrompt(system="static half", user="volatile half"))

    assert calls[0]["input"] == "static half\n\nvolatile half"


# --------------------------------------------------------------------------- #
# OpenAI-Responses-API-compatible fake module, shared by the plain OpenAI
# regression guard and the MiniMax tests below (MiniMax reuses _openai_call).
# --------------------------------------------------------------------------- #


class FakeOpenAIAPIStatusError(Exception):
    """Stand-in for openai.APIStatusError, which carries status_code/message."""

    def __init__(self, message="upstream failure", status_code=500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class FakeOpenAIAPIConnectionError(Exception):
    pass


def install_fake_openai(monkeypatch, respond, *, captured_client=None, captured_calls=None):
    """Install a fake openai module whose ``responses.create(...)`` calls
    ``respond(**kwargs)`` to get a response (or let it raise).

    If ``captured_client`` is a list, every ``openai.OpenAI(**kwargs)``
    constructor call's kwargs are appended to it. If ``captured_calls`` is a
    list, every ``responses.create(**kwargs)`` call's kwargs are appended to
    it. This mirrors ``install_fake_anthropic`` above.
    """

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


def fake_openai_response(text, *, incomplete_reason=None):
    incomplete_details = (
        types.SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
    )
    return types.SimpleNamespace(output_text=text, incomplete_details=incomplete_details)


def test_default_openai_call_constructs_client_with_no_base_url_kwarg(monkeypatch):
    """Regression guard for the plain OpenAI path: with no keyword arguments,
    the client constructor call must carry no ``base_url`` key at all (not
    even ``base_url=None``), so the SDK's own default applies exactly as
    before MiniMax support was added."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    client_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_openai_response("ok"),
        captured_client=client_calls,
    )

    analysis = analyze_with_openai({"device": "PE1"})

    assert "base_url" not in client_calls[0]
    assert client_calls[0]["api_key"] == "test-openai-key"
    assert analysis == "ok"


def test_analyze_with_minimax_wires_the_client_correctly(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    client_calls = []
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_openai_response("## Summary\nAll neighbours are up."),
        captured_client=client_calls,
        captured_calls=create_calls,
    )

    analysis = analyze_with_minimax({"device": "PE1"})

    assert client_calls[0]["base_url"] == MINIMAX_BASE_URL_DEFAULT
    assert client_calls[0]["api_key"] == "test-minimax-key"
    assert create_calls[0]["model"] == MINIMAX_MODEL_DEFAULT
    assert create_calls[0]["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert analysis == "## Summary\nAll neighbours are up."


def test_minimax_base_url_and_model_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://custom.minimax.example/v1")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-Custom")
    client_calls = []
    create_calls = []
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_openai_response("ok"),
        captured_client=client_calls,
        captured_calls=create_calls,
    )

    analyze_with_minimax({"device": "PE1"})

    assert client_calls[0]["base_url"] == "https://custom.minimax.example/v1"
    assert create_calls[0]["model"] == "MiniMax-Custom"


def test_minimax_flags_a_truncated_analysis(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    install_fake_openai(
        monkeypatch,
        lambda **kwargs: fake_openai_response(
            "## Summary\nPartial", incomplete_reason="max_output_tokens"
        ),
    )

    analysis = analyze_with_minimax({"device": "PE1"})

    assert analysis.endswith(TRUNCATION_NOTICE)


def test_minimax_auth_error_names_minimax_api_key(monkeypatch):
    """The error wording must reflect the actual provider -- a MiniMax
    failure must not tell the operator to check OPENAI_API_KEY."""

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

    def raising_respond(**kwargs):
        raise sys.modules["openai"].AuthenticationError("bad key")

    install_fake_openai(monkeypatch, raising_respond)

    with pytest.raises(LLMAnalysisError, match="MINIMAX_API_KEY was rejected"):
        analyze_with_minimax({"device": "PE1"})


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("NotFoundError", "Unknown MiniMax model"),
        ("RateLimitError", "Rate limited by the MiniMax API"),
        ("APIStatusError", "MiniMax API error"),
        ("APIConnectionError", "Could not reach the MiniMax API"),
    ],
)
def test_minimax_errors_name_minimax_not_openai(monkeypatch, error_name, expected):
    """Every failure path must name the endpoint that actually failed.

    Telling an operator "Could not reach the OpenAI API" when MiniMax is down
    sends them to the wrong service and the wrong credential. The auth path is
    covered separately above; these are the other four.
    """

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

    def raising_respond(**kwargs):
        raise getattr(sys.modules["openai"], error_name)("upstream failure")

    install_fake_openai(monkeypatch, raising_respond)

    with pytest.raises(LLMAnalysisError, match=expected):
        analyze_with_minimax({"device": "PE1"})
    # The OpenAI provider's own wording must be unchanged by the same code.
    with pytest.raises(LLMAnalysisError) as caught:
        analyze_with_minimax({"device": "PE1"})
    assert "OpenAI" not in str(caught.value)


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("NotFoundError", "Unknown OpenAI model"),
        ("RateLimitError", "Rate limited by the OpenAI API"),
        ("APIStatusError", "OpenAI API error"),
        ("APIConnectionError", "Could not reach the OpenAI API"),
    ],
)
def test_openai_error_wording_is_unchanged_by_the_minimax_parameterisation(
    monkeypatch, error_name, expected
):
    """The other half of the guard above: parameterising the provider label
    must not have altered a single word the plain OpenAI path emits."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def raising_respond(**kwargs):
        raise getattr(sys.modules["openai"], error_name)("upstream failure")

    install_fake_openai(monkeypatch, raising_respond)

    with pytest.raises(LLMAnalysisError, match=expected):
        analyze_with_openai({"device": "PE1"})
