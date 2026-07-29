import sys
import types

import pytest

from agent_nettools.llm_analysis import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    LLMAnalysisError,
    analyze_with_anthropic,
    get_provider,
)


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
