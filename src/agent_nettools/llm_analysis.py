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

**MiniMax.** Verified live against the real API: MiniMax serves the OpenAI
Responses API at ``https://api.minimax.io/v1``, so ``analyze_with_minimax``
reuses ``_openai_call`` (parameterised with ``api_key_env``/``base_url``/
``model``/``model_env``/``model_default``) instead of adding a fourth client
-- there is nothing provider-specific left to write once the base URL and
model name are swapped. It is explicit-only: ``LLM_PROVIDER=minimax`` must be
set directly, exactly like ``ollama`` -- ``auto`` never selects it. Reasoning
is already structurally separated from the answer on the Responses API route
(there is no ``<think>`` block mixed into ``output_text`` the way a raw
chat-completions call to a reasoning model might produce), so no extra
stripping step is needed here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

Provider = Literal["anthropic", "openai", "ollama", "minimax"]

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

# MiniMax serves the OpenAI Responses API -- see the module docstring. These
# apply only when MINIMAX_BASE_URL/MINIMAX_MODEL are unset.
MINIMAX_BASE_URL_DEFAULT = "https://api.minimax.io/v1"
MINIMAX_MODEL_DEFAULT = "MiniMax-M3"

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
    - minimax
    - ollama
    - auto

    auto picks Anthropic first when ANTHROPIC_API_KEY exists, then OpenAI when
    OPENAI_API_KEY exists. Ollama is local and keyless, and minimax is a
    separate BYOK provider reusing the OpenAI Responses API plumbing (see the
    module docstring) -- like ollama, minimax is never chosen by auto, and
    must be selected explicitly with LLM_PROVIDER=ollama or
    LLM_PROVIDER=minimax respectively. A clear error is raised when the
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
    if requested == "minimax":
        if not os.getenv("MINIMAX_API_KEY"):
            raise ValueError("MINIMAX_API_KEY is required when LLM_PROVIDER=minimax.")
        return "minimax"
    if requested == "ollama":
        return "ollama"
    if requested != "auto":
        raise ValueError("LLM_PROVIDER must be 'auto', 'anthropic', 'openai', 'minimax', or 'ollama'.")

    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    raise ValueError(
        "No LLM API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or set LLM_PROVIDER=ollama or LLM_PROVIDER=minimax to use a local "
        "Ollama model or MiniMax."
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


def _openai_call(
    prompt: str,
    *,
    api_key_env: str = "OPENAI_API_KEY",
    base_url: str | None = None,
    model: str | None = None,
    model_env: str = "OPENAI_MODEL",
    model_default: str = "gpt-5.5",
    provider_label: str = "OpenAI",
) -> str:
    """Send one prompt string to an OpenAI-Responses-API-compatible endpoint.

    Factored out of ``analyze_with_openai`` so ``fabric_analysis.py`` can send
    a differently-built prompt (cross-device correlation, not single-device
    evidence) through the same provider plumbing. Also reused, parameterised,
    by ``analyze_with_minimax`` -- MiniMax serves this same Responses API, so
    only ``api_key_env``/``base_url``/``model``/``model_env``/``model_default``/
    ``provider_label`` need to change (see the module docstring); with no
    keyword arguments this behaves exactly as the plain OpenAI path always
    has, including omitting ``base_url`` entirely from the client constructor
    call so the SDK's own default applies.

    ``provider_label`` exists so a failure names the endpoint that actually
    failed. Every message here reaches an operator who is deciding where to
    look, and "Could not reach the OpenAI API" when MiniMax is unreachable
    sends them to the wrong service and the wrong credential -- the same
    class of misattribution this package already avoids by formatting per-
    command errors as ``"<command>: <detail>"``.
    """

    import openai

    client_kwargs: dict[str, Any] = {"api_key": os.getenv(api_key_env)}
    if base_url is not None:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)
    resolved_model = model if model is not None else os.getenv(model_env, model_default)

    try:
        response = client.responses.create(
            model=resolved_model,
            input=prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.AuthenticationError as exc:
        raise LLMAnalysisError(f"{api_key_env} was rejected. Check the key in .env.") from exc
    except openai.NotFoundError as exc:
        raise LLMAnalysisError(
            f"Unknown {provider_label} model: {resolved_model}. Check {model_env}."
        ) from exc
    except openai.RateLimitError as exc:
        raise LLMAnalysisError(f"Rate limited by the {provider_label} API. Retry shortly.") from exc
    except openai.APIStatusError as exc:
        raise LLMAnalysisError(
            f"{provider_label} API error {exc.status_code}: {exc.message}"
        ) from exc
    except openai.APIConnectionError as exc:
        raise LLMAnalysisError(f"Could not reach the {provider_label} API: {exc}") from exc

    analysis = response.output_text
    incomplete = getattr(response, "incomplete_details", None)
    if getattr(incomplete, "reason", None) == "max_output_tokens":
        analysis += TRUNCATION_NOTICE

    return analysis


def analyze_with_openai(evidence: dict[str, Any]) -> str:
    """Analyze evidence with OpenAI Responses API."""

    return _openai_call(build_analysis_prompt(evidence))


def _minimax_call_kwargs() -> dict[str, Any]:
    """Keyword arguments routing ``_openai_call`` at the MiniMax endpoint.

    Shared by ``analyze_with_minimax`` here and ``fabric_analysis.py`` so the
    MiniMax argument set is defined exactly once.
    """

    return {
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": os.getenv("MINIMAX_BASE_URL", MINIMAX_BASE_URL_DEFAULT),
        "model_env": "MINIMAX_MODEL",
        "model_default": MINIMAX_MODEL_DEFAULT,
        "provider_label": "MiniMax",
    }


def analyze_with_minimax(evidence: dict[str, Any]) -> str:
    """Analyze evidence with MiniMax, over the OpenAI Responses API.

    MiniMax implements the OpenAI Responses API (verified live at
    ``https://api.minimax.io/v1``), so this reuses ``_openai_call`` rather
    than adding a fourth client -- see the module docstring. Reasoning is
    already structurally separated from the answer on this route (there is
    no ``<think>`` block mixed into ``output_text``), so no stripping step is
    needed here.
    """

    return _openai_call(build_analysis_prompt(evidence), **_minimax_call_kwargs())


def complete_prompt(prompt: str) -> str:
    """Send one fully-rendered prompt and return the model's raw text.

    The prompt library (T-026-T-029) renders a complete, self-contained prompt
    -- Grounding, Role, Anchors, Constraints, Expected output -- so there is
    nothing for this layer to add. Every other entry point here *builds* a
    prompt from evidence; this one is handed a finished one, which is what lets
    `investigation.investigate` take a plain `(prompt) -> str` callable and stay
    testable with a scripted analyst.

    **Prompt caching is not applied on this path.** Anthropic's cache is a
    prefix match on `system`, and a rendered prompt arrives as one string with
    its static and volatile halves already interleaved. Splitting it back apart
    would mean the renderer returning two pieces, which is a prompt-library
    change rather than CLI wiring. Filed as B-421; the prompts are a few
    kilobytes, so this is cost, not correctness.
    """

    provider = get_provider()

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = _call_anthropic_or_raise(
            client,
            model=os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT),
            messages=[{"role": "user", "content": prompt}],
        )
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            suffix = f" (category: {category})" if category else ""
            raise LLMAnalysisError(f"The model declined this prompt.{suffix}")
        text = "\n".join(
            block.text for block in message.content if getattr(block, "text", None)
        )
        if message.stop_reason == "max_tokens":
            # Not a truncation *notice* here, unlike `analyze_evidence`. This
            # response is parsed as JSON, and appending prose to it would turn
            # a truncation into a parse failure that reads as a malformed model
            # response. Raising says what actually happened.
            raise LLMAnalysisError(
                "The model's response hit max_tokens and is incomplete; a partial "
                "JSON document cannot be grounded"
            )
        return text

    if provider == "openai":
        return _openai_call(prompt)
    if provider == "minimax":
        return _openai_call(prompt, **_minimax_call_kwargs())
    if provider == "ollama":
        return _ollama_call(prompt)

    raise LLMAnalysisError(f"Provider {provider!r} cannot send a rendered prompt.")


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
    if provider == "minimax":
        return analyze_with_minimax(evidence)
    return analyze_with_openai(evidence)
