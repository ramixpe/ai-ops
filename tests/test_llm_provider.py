import sys
import types

import pytest

from agent_nettools.llm_analysis import (
    MAX_OUTPUT_TOKENS,
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


def install_fake_anthropic(monkeypatch, create):
    """Install a fake anthropic module whose messages.create runs ``create``."""

    module = types.ModuleType("anthropic")
    module.APIStatusError = FakeAPIStatusError
    module.AuthenticationError = type("AuthenticationError", (FakeAPIStatusError,), {})
    module.NotFoundError = type("NotFoundError", (FakeAPIStatusError,), {})
    module.RateLimitError = type("RateLimitError", (FakeAPIStatusError,), {})
    module.APIConnectionError = FakeAPIConnectionError

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = types.SimpleNamespace(create=create)

    module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def fake_message(text, stop_reason="end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


def test_anthropic_requests_the_full_token_budget(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return fake_message("## Summary\nAll neighbours are up.")

    install_fake_anthropic(monkeypatch, create)

    analysis = analyze_with_anthropic({"device": "PE1"})

    assert calls[0]["max_tokens"] == MAX_OUTPUT_TOKENS
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
        lambda **kwargs: types.SimpleNamespace(content=[], stop_reason="refusal"),
    )

    with pytest.raises(LLMAnalysisError, match="declined"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_api_error_becomes_an_analysis_error(monkeypatch):
    def create(**kwargs):
        raise FakeAPIStatusError("service unavailable", status_code=503)

    install_fake_anthropic(monkeypatch, create)

    with pytest.raises(LLMAnalysisError, match="503"):
        analyze_with_anthropic({"device": "PE1"})


def test_anthropic_connection_error_becomes_an_analysis_error(monkeypatch):
    def create(**kwargs):
        raise FakeAPIConnectionError("dns failure")

    install_fake_anthropic(monkeypatch, create)

    with pytest.raises(LLMAnalysisError, match="Could not reach"):
        analyze_with_anthropic({"device": "PE1"})
