"""LLM analysis layer for structured network evidence.

Supports BYOK (bring your own key) for either Anthropic Claude or OpenAI.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

Provider = Literal["anthropic", "openai", "ollama"]

# Generous ceiling so a full four-section analysis is never cut off. You are
# billed for what the model actually emits, not for this limit.
MAX_OUTPUT_TOKENS = 16000

TRUNCATION_NOTICE = (
    "\n\n[Analysis truncated: the model hit the "
    f"{MAX_OUTPUT_TOKENS}-token output limit. Treat the sections above as incomplete.]"
)


class LLMAnalysisError(RuntimeError):
    """Raised when the configured provider could not produce an analysis."""


TROUBLESHOOTING_PROMPT = """You are a network troubleshooting assistant.

Purpose:
Analyze the provided network evidence and recommend the next troubleshooting check.

Examples:
Return your answer using this format:

## Summary
Short summary of what appears wrong or healthy.

## Evidence
Bullet list of evidence from the provided data.

## Possible Cause
Possible explanation based only on the evidence.

## Recommended Next Check
One safe next check. Do not recommend changes yet.

Knowledge and Constraints:
- Use only the provided network evidence.
- Do not invent device facts.
- Do not assume missing data.
- Do not recommend configuration changes unless explicitly asked.
- If the evidence is incomplete, say what is missing.

Evaluation:
Before responding, verify that every claim is supported by the provided data.
"""


def build_analysis_prompt(evidence: dict[str, Any]) -> str:
    """Build the prompt sent to the selected LLM provider."""

    return (
        f"{TROUBLESHOOTING_PROMPT}\n\n"
        "Network evidence:\n"
        f"```json\n{json.dumps(evidence, indent=2)}\n```\n"
    )


def get_provider() -> Provider:
    """Return the configured provider.

    LLM_PROVIDER may be one of:
    - anthropic
    - openai
    - ollama
    - auto

    auto picks Anthropic first when ANTHROPIC_API_KEY exists, then OpenAI when
    OPENAI_API_KEY exists. Ollama is local and keyless, so it must be selected
    explicitly with LLM_PROVIDER=ollama. A clear error is raised when the
    selected cloud provider has no API key.
    """

    requested = os.getenv("LLM_PROVIDER", "auto").strip().lower()

    if requested in {"anthropic", "claude"}:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.")
        return "anthropic"
    if requested == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        return "openai"
    if requested == "ollama":
        return "ollama"
    if requested != "auto":
        raise ValueError("LLM_PROVIDER must be 'auto', 'anthropic', 'openai', or 'ollama'.")

    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    raise ValueError(
        "No LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or set LLM_PROVIDER=ollama to use a local Ollama model."
    )


def analyze_with_anthropic(evidence: dict[str, Any]) -> str:
    """Analyze evidence with Anthropic Claude."""

    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    try:
        message = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": build_analysis_prompt(evidence)}],
        )
    except anthropic.AuthenticationError as exc:
        raise LLMAnalysisError("ANTHROPIC_API_KEY was rejected. Check the key in .env.") from exc
    except anthropic.NotFoundError as exc:
        raise LLMAnalysisError(f"Unknown Anthropic model: {model}. Check ANTHROPIC_MODEL.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMAnalysisError("Rate limited by the Anthropic API. Retry shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMAnalysisError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMAnalysisError(f"Could not reach the Anthropic API: {exc}") from exc

    # Safety classifiers can decline a request; that arrives as a normal 200
    # with an empty content list, so check before reading the blocks.
    if message.stop_reason == "refusal":
        raise LLMAnalysisError("The model declined to analyze this evidence.")

    parts: list[str] = []
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    analysis = "\n".join(parts)
    if message.stop_reason == "max_tokens":
        analysis += TRUNCATION_NOTICE

    return analysis


def analyze_with_openai(evidence: dict[str, Any]) -> str:
    """Analyze evidence with OpenAI Responses API."""

    import openai

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.5")

    try:
        response = client.responses.create(
            model=model,
            input=build_analysis_prompt(evidence),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.AuthenticationError as exc:
        raise LLMAnalysisError("OPENAI_API_KEY was rejected. Check the key in .env.") from exc
    except openai.NotFoundError as exc:
        raise LLMAnalysisError(f"Unknown OpenAI model: {model}. Check OPENAI_MODEL.") from exc
    except openai.RateLimitError as exc:
        raise LLMAnalysisError("Rate limited by the OpenAI API. Retry shortly.") from exc
    except openai.APIStatusError as exc:
        raise LLMAnalysisError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
    except openai.APIConnectionError as exc:
        raise LLMAnalysisError(f"Could not reach the OpenAI API: {exc}") from exc

    analysis = response.output_text
    incomplete = getattr(response, "incomplete_details", None)
    if getattr(incomplete, "reason", None) == "max_output_tokens":
        analysis += TRUNCATION_NOTICE

    return analysis


def analyze_with_ollama(evidence: dict[str, Any]) -> str:
    """Analyze evidence with a local Ollama model via its native chat API.

    Configuration:
    - OLLAMA_HOST  (default http://localhost:11434)
    - OLLAMA_MODEL (default ornith:9b-q8_0)

    ``think`` is disabled so thinking-capable models return their answer in
    ``message.content`` instead of an empty string.
    """

    import urllib.error
    import urllib.request

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "ornith:9b-q8_0")

    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": build_analysis_prompt(evidence)}],
        "options": {"num_predict": MAX_OUTPUT_TOKENS, "temperature": 0},
    }
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise LLMAnalysisError(f"Ollama returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise LLMAnalysisError(
            f"Could not reach Ollama at {host}: {exc.reason}. Is `ollama serve` running?"
        ) from exc
    except TimeoutError as exc:
        raise LLMAnalysisError(f"Ollama at {host} did not respond within 120s.") from exc
    except json.JSONDecodeError as exc:
        raise LLMAnalysisError(f"Ollama at {host} returned a non-JSON response.") from exc

    analysis = body.get("message", {}).get("content", "").strip()
    if not analysis:
        raise LLMAnalysisError(f"Ollama model {model} returned an empty analysis.")
    if body.get("done_reason") == "length":
        analysis += TRUNCATION_NOTICE

    return analysis


def analyze_evidence(evidence: dict[str, Any]) -> str:
    """Analyze network evidence using the configured provider."""

    provider = get_provider()
    if provider == "anthropic":
        return analyze_with_anthropic(evidence)
    if provider == "ollama":
        return analyze_with_ollama(evidence)
    return analyze_with_openai(evidence)
