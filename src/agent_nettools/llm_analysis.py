"""LLM analysis layer for structured network evidence.

Supports BYOK (bring your own key) for either Anthropic Claude or OpenAI, plus
a local keyless Ollama model.

Phase 6 additions and the decisions behind them
------------------------------------------------
**Model default.** ``ANTHROPIC_MODEL``'s fallback (used only when the env var
is unset) is now ``claude-opus-5``, the current model as of this writing --
the previous default (``claude-sonnet-4-5``) is stale. This repository's own
``.env`` pins an older model explicitly, which is intentional and unaffected:
the default here only ever applies when ``ANTHROPIC_MODEL`` is not set at
all.

**No sampling/thinking-budget parameters.** Never pass ``temperature``,
``top_p``, ``top_k``, or ``thinking.budget_tokens`` on any Anthropic call --
all four return 400 on ``claude-opus-5``. ``thinking`` is left unset
entirely: adaptive thinking is on by default on that model, so there is
nothing to configure.

**Streaming, and a higher token ceiling.** Because thinking is on by default,
``max_tokens`` now caps thinking *and* text together, so the old 16000-token
ceiling (sized for text alone) risks truncating a response that spent most of
its budget thinking. The Anthropic path now streams
(``client.messages.stream(...)`` / ``get_final_message()``) and raises the
ceiling to ``ANTHROPIC_MAX_OUTPUT_TOKENS`` (32000) -- streaming removes the
non-streaming SDK's HTTP-timeout guard at large ``max_tokens`` values. The
``TRUNCATION_NOTICE`` behaviour on ``stop_reason == "max_tokens"`` is
unchanged.

**Prompt caching.** ``build_analysis_prompt`` (used by the OpenAI and Ollama
paths, which have no server-side cache) still concatenates the static
prompt and the volatile evidence into one string -- fine there, since neither
provider caches anything. The Anthropic path is different: the static
``TROUBLESHOOTING_PROMPT`` goes into ``system`` as a single text block
carrying ``cache_control={"type": "ephemeral"}``, and the volatile evidence
goes into ``messages`` -- caching is a prefix match (render order is
``tools`` -> ``system`` -> ``messages``), so anything after the last
breakpoint, including a per-call timestamp or device name, must never sit
inside the cached block. ``usage.cache_read_input_tokens`` /
``cache_creation_input_tokens`` are attached to the agent loop's returned
``usage`` (see ``agent_loop.py``) so a caller can verify hits; the
single-shot path here still returns a plain string (unchanged contract for
existing callers), so its cache activity is only observable by inspecting
the request/response objects directly, which is what the tests do.

**Refusal fallbacks are conditional, not automatic.** ``fallbacks="default"``
requires the beta endpoint (``client.beta.messages.stream``, beta header
``server-side-fallback-2026-07-01``) and is only meaningful for the
Opus-5/Fable-5/Mythos-5 family -- sending it to any other model is a
mismatch, not just a no-op. ``_fallbacks_enabled`` gates it on both the
resolved model's prefix *and* ``NETTOOLS_LLM_FALLBACKS`` (set to
``0``/``false``/``no``/``off`` to disable even for a matching model). Every
other model uses the plain, non-beta streaming path. A network-troubleshooting
prompt can plausibly trip a cyber-content classifier even though nothing
here is actually malicious, so ``stop_reason == "refusal"`` is always handled;
``stop_details`` is read only in that branch -- it is ``null`` otherwise.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

Provider = Literal["anthropic", "openai", "ollama"]

# Generous ceiling so a full four-section analysis is never cut off on the
# non-Anthropic paths, which have no thinking budget to share with. You are
# billed for what the model actually emits, not for this limit.
MAX_OUTPUT_TOKENS = 16000

# The Anthropic path streams and thinking is on by default there, so this
# ceiling has to cover thinking + text together -- see the module docstring.
ANTHROPIC_MAX_OUTPUT_TOKENS = 32000

# Applies only when ANTHROPIC_MODEL is unset; this repo's own .env pins an
# older model on purpose, which is unaffected by this default.
ANTHROPIC_MODEL_DEFAULT = "claude-opus-5"

TRUNCATION_NOTICE = (
    "\n\n[Analysis truncated: the model hit the output token limit. "
    "Treat the sections above as incomplete.]"
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
    """Build the single-string prompt used by the OpenAI and Ollama paths.

    Neither provider offers server-side prompt caching, so concatenating the
    static instructions and the volatile evidence costs nothing extra here.
    The Anthropic path does not use this function -- see
    ``_anthropic_system_blocks`` / ``_anthropic_user_content``, which keep the
    two halves separate so the static half is cacheable.
    """

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


# --------------------------------------------------------------------------- #
# Anthropic: prompt caching, streaming, and conditional refusal fallbacks.
# --------------------------------------------------------------------------- #

# The only families "fallbacks": "default" is meaningful for. Sending it for
# any other model would be a mismatch (the parameter targets exactly these
# models' own classifier categories), so gating happens on this prefix set,
# not just on a truthy env var.
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

# Beta header for the "default" (scalar) form of server-side fallbacks -- the
# array form (``fallbacks=[{"model": ...}]``) uses a different, older header
# and is not used here; "default" routes by refusal category with no model
# list to maintain.
_FALLBACKS_BETA = "server-side-fallback-2026-07-01"

_FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def _fallbacks_enabled(model: str) -> bool:
    """Whether to request server-side refusal fallbacks for this model.

    Both conditions must hold: the resolved model is in the Opus-5/Fable-5/
    Mythos-5 family, and ``NETTOOLS_LLM_FALLBACKS`` has not been set to a
    falsy value. Every other model uses the plain, non-beta path -- passing
    ``fallbacks`` there would be paired with a family it was never meant for.
    """

    flag = os.getenv("NETTOOLS_LLM_FALLBACKS", "1").strip().lower()
    if flag in _FALSY_ENV_VALUES:
        return False
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


def _anthropic_system_blocks() -> list[dict[str, Any]]:
    """Static, cacheable system prompt for the single-shot analysis path.

    Never interpolate a timestamp, device name, or UUID here -- caching is a
    prefix match, and anything volatile in this block would invalidate the
    cache on every single call. All per-call content (the evidence) goes in
    ``messages`` instead; see ``_anthropic_user_content``.
    """

    return [
        {
            "type": "text",
            "text": TROUBLESHOOTING_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _anthropic_user_content(evidence: dict[str, Any]) -> str:
    """The volatile half of the single-shot Anthropic prompt: the evidence itself."""

    return f"Network evidence:\n```json\n{json.dumps(evidence, indent=2)}\n```\n"


def _stream_anthropic_message(
    client: Any,
    *,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = ANTHROPIC_MAX_OUTPUT_TOKENS,
) -> Any:
    """Stream one Anthropic Messages call and return the accumulated message.

    Streaming (rather than a plain ``.create()``) avoids the HTTP-timeout risk
    a large ``max_tokens`` creates once thinking is on by default. Refusal
    fallbacks go through the beta endpoint only when ``_fallbacks_enabled``
    says this model supports them; every other model gets the plain path, so
    a model outside the fallback family never gets a beta parameter it does
    not support.
    """

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        kwargs["tools"] = tools

    if _fallbacks_enabled(model):
        with client.beta.messages.stream(
            betas=[_FALLBACKS_BETA], fallbacks="default", **kwargs
        ) as stream:
            return stream.get_final_message()

    with client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


def _call_anthropic_or_raise(client: Any, *, model: str, **kwargs: Any) -> Any:
    """``_stream_anthropic_message`` with the SDK's exceptions mapped to ``LLMAnalysisError``."""

    import anthropic

    try:
        return _stream_anthropic_message(client, model=model, **kwargs)
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


def analyze_with_anthropic(evidence: dict[str, Any]) -> str:
    """Analyze evidence with Anthropic Claude.

    Uses the cacheable system/messages split (see the module docstring) and
    streams the response. ``stop_details`` is only read when ``stop_reason``
    is ``"refusal"`` -- it is ``null`` for every other stop reason.
    """

    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT)

    message = _call_anthropic_or_raise(
        client,
        model=model,
        system=_anthropic_system_blocks(),
        messages=[{"role": "user", "content": _anthropic_user_content(evidence)}],
    )

    # Safety classifiers can decline a request; that arrives as a normal 200
    # with an empty (or partial) content list, so check before reading blocks.
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details is not None else None
        suffix = f" (category: {category})" if category else ""
        raise LLMAnalysisError(f"The model declined to analyze this evidence.{suffix}")

    parts: list[str] = []
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    analysis = "\n".join(parts)
    if message.stop_reason == "max_tokens":
        analysis += TRUNCATION_NOTICE

    return analysis


def _openai_call(prompt: str) -> str:
    """Send one prompt string to OpenAI's Responses API and return the text.

    Factored out of ``analyze_with_openai`` so ``fabric_analysis.py`` can send
    a differently-built prompt (cross-device correlation, not single-device
    evidence) through the same provider plumbing.
    """

    import openai

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.5")

    try:
        response = client.responses.create(
            model=model,
            input=prompt,
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


def analyze_with_openai(evidence: dict[str, Any]) -> str:
    """Analyze evidence with OpenAI Responses API."""

    return _openai_call(build_analysis_prompt(evidence))


def _ollama_call(prompt: str) -> str:
    """Send one prompt string to a local Ollama model via its native chat API.

    Factored out of ``analyze_with_ollama`` for the same reason as
    ``_openai_call`` -- reused by ``fabric_analysis.py`` with a different
    prompt.

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
        "messages": [{"role": "user", "content": prompt}],
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


def analyze_with_ollama(evidence: dict[str, Any]) -> str:
    """Analyze evidence with a local Ollama model via its native chat API."""

    return _ollama_call(build_analysis_prompt(evidence))


def analyze_evidence(evidence: dict[str, Any]) -> str:
    """Analyze network evidence using the configured provider."""

    provider = get_provider()
    if provider == "anthropic":
        return analyze_with_anthropic(evidence)
    if provider == "ollama":
        return analyze_with_ollama(evidence)
    return analyze_with_openai(evidence)
