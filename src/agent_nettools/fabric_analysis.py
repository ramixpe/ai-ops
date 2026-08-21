"""Fabric-wide LLM analysis: cross-device correlation ``check_fabric`` can't do.

``network_tools.check_fabric`` only concatenates: it runs one check across
every device and hands back a dict keyed by device name. Nothing before this
module ever put two devices' evidence side by side for the *model* to
correlate -- so "PE2 has zero IS-IS adjacencies" and "RR1's peer 10.255.0.12
is Idle" sit in unrelated blocks of the same JSON document, with no signal
that they are one incident.

``analyze_fabric`` feeds the model both the (budgeted, see
``evidence_budget.py``) raw evidence *and* the Phase 4 deterministic health
verdicts for every device. Health verdicts are already the anomaly signal --
role invariants and baseline drift are cheap and deterministic precisely so
the model's job here is interpretation and correlation across devices, not
re-deriving what health.py already computed. Whether the model's job is
"detect problems" or "explain why these already-detected problems are one
incident" is the whole reason both are handed over rather than raw evidence
alone.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .evidence_budget import budget_fabric_evidence, render_budgeted_evidence
from .health import evaluate_fabric
from .llm_analysis import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    ANTHROPIC_MODEL_DEFAULT,
    TRUNCATION_NOTICE,
    LLMAnalysisError,
    _call_anthropic_or_raise,
    _minimax_call_kwargs,
    _ollama_call,
    _openai_call,
    get_provider,
)
from .prompt_library import load_prompt

#: The whole-fabric analysis system prompt, loaded from `prompts/fabric_analysis.v1.txt`.
#:
#: Moved out of this module 2026-08-21 at the operator's request: every
#: prompt this project sends a model is reviewable as a file, without
#: reading Python. It joins `report`/`correlate`, which already worked
#: this way -- this one was simply never migrated.
#:
#: Loaded at import rather than lazily, deliberately. `FABRIC_ANALYSIS_PROMPT` is a
#: module constant that other modules import by name (see
#: `mcp_server/server.py`), so it has to exist as a value at import
#: time. It also means a missing or unreadable prompt file fails here,
#: loudly, rather than half way through an investigation.
FABRIC_ANALYSIS_PROMPT = load_prompt("fabric_analysis", 1)


def _fabric_user_content(
    evidence_by_device: dict[str, Any], verdicts: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Build the volatile half of the fabric prompt: budgeted evidence + verdicts.

    Returns ``(content, truncation_report)``.
    """

    per_device, truncated = budget_fabric_evidence(evidence_by_device)
    evidence_text = render_budgeted_evidence(per_device)
    verdicts_text = json.dumps(verdicts, indent=2, sort_keys=True)

    content = (
        "Fabric evidence (per device; long sections may be truncated in the "
        "middle -- state explicitly what is missing if it matters to your "
        "answer):\n"
        f"{evidence_text}\n\n"
        "Deterministic health verdicts (Phase 4, already computed -- "
        "correlate across devices rather than re-deriving them):\n"
        f"```json\n{verdicts_text}\n```\n"
    )
    return content, truncated


def build_fabric_prompt(
    evidence_by_device: dict[str, Any],
    verdicts: dict[str, Any] | None = None,
) -> str:
    """Return the full, provider-agnostic fabric analysis prompt as one string.

    Exposed standalone (not only reachable through ``analyze_fabric``) so
    tests -- and the OpenAI/Ollama paths, which have no server-side cache
    worth splitting the prompt for -- can use the same text without making a
    live LLM call.
    """

    if verdicts is None:
        verdicts = evaluate_fabric(evidence_by_device)
    content, _truncated = _fabric_user_content(evidence_by_device, verdicts)
    return f"{FABRIC_ANALYSIS_PROMPT}\n\n{content}"


def _analyze_fabric_with_anthropic(user_content: str) -> str:
    """The Anthropic path: static prompt cached in ``system``, evidence in ``messages``."""

    import anthropic  # noqa: F401 - imported to construct the client below.

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT)
    system = [
        {"type": "text", "text": FABRIC_ANALYSIS_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]

    message = _call_anthropic_or_raise(
        client,
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=ANTHROPIC_MAX_OUTPUT_TOKENS,
    )

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details is not None else None
        suffix = f" (category: {category})" if category else ""
        raise LLMAnalysisError(f"The model declined to analyze this fabric evidence.{suffix}")

    analysis = "\n".join(block.text for block in message.content if getattr(block, "text", None))
    if message.stop_reason == "max_tokens":
        analysis += TRUNCATION_NOTICE
    return analysis


def analyze_fabric(
    evidence_by_device: dict[str, Any],
    verdicts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cross-device fabric analysis: evidence + Phase 4 health verdicts, correlated.

    ``verdicts`` defaults to ``health.evaluate_fabric(evidence_by_device)`` so
    the common case only needs the raw evidence. Returns
    ``{"analysis": str, "truncated": [...]}`` -- ``truncated`` is the same
    report ``evidence_budget.budget_fabric_evidence`` produces, surfaced here
    so a caller can tell whether the model's answer was working from partial
    evidence.
    """

    if verdicts is None:
        verdicts = evaluate_fabric(evidence_by_device)

    user_content, truncated = _fabric_user_content(evidence_by_device, verdicts)

    provider = get_provider()
    if provider == "anthropic":
        analysis = _analyze_fabric_with_anthropic(user_content)
    elif provider == "ollama":
        analysis = _ollama_call(f"{FABRIC_ANALYSIS_PROMPT}\n\n{user_content}")
    elif provider == "minimax":
        analysis = _openai_call(
            f"{FABRIC_ANALYSIS_PROMPT}\n\n{user_content}", **_minimax_call_kwargs()
        )
    else:
        analysis = _openai_call(f"{FABRIC_ANALYSIS_PROMPT}\n\n{user_content}")

    return {"analysis": analysis, "truncated": truncated}
