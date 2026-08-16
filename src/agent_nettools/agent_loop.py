"""A bounded, read-only tool-calling agent loop over the existing safety boundary.

Why a manual loop, not ``client.beta.messages.tool_runner``
--------------------------------------------------------------
The SDK's tool runner (``client.beta.messages.tool_runner``) is the usual
recommendation for a custom-tool agent, and would save the loop below. Three
reasons it is not used here:

1. **Hard bounds are safety-critical here, not just a nicety.** This loop
   talks to real (lab) network devices through the same allowlisted tools the
   rest of this package exposes. It needs both a hard ``max_iterations`` *and*
   a wall-clock ``time_budget_s`` -- a runaway loop must stop on its own, not
   depend on the caller noticing. The tool runner does not expose a
   wall-clock budget, and layering one on top of it means intercepting its
   iteration internally anyway, at which point the manual loop is no bigger.
2. **The loop shape must stay provider-agnostic.** This package already
   supports three providers (Anthropic, OpenAI, Ollama) for single-shot
   analysis, and Task D's OpenAI/Ollama support is explicitly deferred, not
   ruled out. A hand-written ``while`` loop over ``stop_reason`` is a shape
   every provider's SDK can express; ``tool_runner`` is Anthropic-SDK-specific
   and beta besides.
3. **``tool_runner`` is a beta surface.** Nothing here needs its per-turn
   hooks (approval gates, result interception) -- every tool in this loop is
   already read-only and already validated by ``render_command``/the static
   allowlist before it ever touches a device, so there is no approval
   decision to intercept. Taking on a beta dependency for a loop this small
   is not worth it.

The loop shape
----------------
Each turn: send the conversation so far, then dispatch on ``stop_reason``:

- ``"tool_use"``   -> execute every ``tool_use`` block in the response
                      (possibly several -- Claude may request them in
                      parallel) and send back one ``user`` message carrying
                      *all* of their ``tool_result`` blocks, then continue.
- ``"end_turn"``   -> done; the response's text is the final answer.
- ``"max_tokens"`` -> report truncation (``stopped_because = "truncated"``,
                      the partial text plus ``TRUNCATION_NOTICE``).
- ``"refusal"``    -> raise ``LLMAnalysisError`` (see ``llm_analysis.py``'s
                      module docstring on why refusals must be handled even
                      for a benign network-troubleshooting prompt).
- ``"pause_turn"`` -> re-send the conversation with the paused assistant turn
                      appended, per the SDK's documented resume contract, and
                      continue looping.

Hitting ``max_iterations`` or ``time_budget_s`` is a normal outcome, not an
exception: the loop returns whatever partial answer it has with
``stopped_because`` set to say why, rather than raising.

The tool surface: read-only, and validated exactly like a human caller
--------------------------------------------------------------------------
Exactly six tools are exposed, each a thin wrapper over an existing,
already-safe function -- there is no generic executor and no new device
access path:

- ``list_lab_devices``            -> ``network_tools.list_devices``
- ``run_lab_intent``              -> ``network_tools.run_intent``
- ``run_lab_template``            -> ``network_tools.run_template``
- ``check_lab_fabric``            -> ``network_tools.check_fabric``
- ``collect_lab_evidence``        -> ``network_tools.collect_evidence``
- ``assess_lab_health``           -> ``health.evaluate_device`` / ``evaluate_fabric``

``intent``, ``template``, and ``check`` are declared with an ``enum`` in
their JSON schema, drawn from ``platforms.all_intents()``,
``templates.PLATFORM_TEMPLATES``, and ``network_tools.CHECK_TOOLS`` -- so the
model cannot even *name* an intent, template, or check this tool surface does
not already know about. More importantly, every ``run_lab_template`` call's
parameters flow through ``run_template`` -> ``render_command``'s existing
Phase 5 validation (canonicalize by reconstruction, never pass-through, see
``templates.py``) exactly as a human-supplied CLI/MCP argument would -- a
malicious or malformed value from the model is refused the same way, with
the same structured error, not a special case.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .health import evaluate_device, evaluate_fabric
from .inventory_model import load_inventory_file
from .llm_analysis import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    ANTHROPIC_MODEL_DEFAULT,
    TRUNCATION_NOTICE,
    LLMAnalysisError,
    _call_anthropic_or_raise,
    get_provider,
)
from .network_tools import (
    CHECK_TOOLS,
    STATUS_ERROR,
    check_fabric,
    collect_evidence,
    list_devices,
    run_intent,
    run_template,
)
from .platforms import PLATFORM_TEMPLATES, all_intents

AGENT_SYSTEM_PROMPT = """You are a network troubleshooting agent for a small, single-user \
Cisco IOS-XR lab fabric.

Purpose:
Answer the operator's question by investigating with the read-only tools \
available to you, then give a grounded answer.

Knowledge and Constraints:
- Use the tools to gather evidence before answering; do not guess at device state.
- Every tool is read-only. None of them can change device configuration.
- Do not invent device names, addresses, or facts not returned by a tool.
- If a tool call is refused (a validation error, an unsupported combination), \
say so plainly rather than retrying the same call unchanged.
- If you cannot fully answer within your remaining tool calls, say what you \
found and what you were not able to check.
- If a claim cannot be traced to a tool result you actually received, do not \
make it.
"""


def _intent_enum() -> list[str]:
    return list(all_intents())


def _template_enum() -> list[str]:
    names: set[str] = set()
    for templates_for_platform in PLATFORM_TEMPLATES.values():
        names.update(templates_for_platform.keys())
    return sorted(names)


def _check_enum() -> list[str]:
    return sorted(CHECK_TOOLS)


def _build_tools() -> list[dict[str, Any]]:
    """Build the Anthropic tool schemas. Computed once at import time.

    ``enum`` values are drawn live from the registries above, so a new
    intent/template/check added elsewhere in the package is picked up here
    automatically without a second place to update.
    """

    return [
        {
            "name": "list_lab_devices",
            "description": "List the lab's inventory devices (name, hostname, platform). No arguments.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "run_lab_intent",
            "description": (
                "Run one read-only, vendor-neutral intent against one device (facts, "
                "interfaces, bgp, lldp, isis, or sr). Resolves to that device's own "
                "platform syntax."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Device name, e.g. PE1."},
                    "intent": {"type": "string", "enum": _intent_enum()},
                },
                "required": ["device_name", "intent"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_lab_template",
            "description": (
                "Render and run one validated, parameterized command template on one "
                "device -- e.g. look up a specific route, BGP neighbor, interface, log "
                "tail, or ping/traceroute target. Every parameter is validated exactly "
                "as it would be from a human caller (canonicalized by reconstruction, "
                "never passed through as text); an invalid value is refused with a "
                "structured error, not silently sent to the device."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Device name, e.g. PE1."},
                    "template": {"type": "string", "enum": _template_enum()},
                    "params": {
                        "type": "object",
                        "description": (
                            "The template's own parameters, e.g. "
                            '{"address": "10.255.0.31"} for bgp_neighbor/ping/traceroute, '
                            '{"prefix": "10.0.0.0/24"} for route, '
                            '{"interface": "GigabitEthernet0/0/0/1"} for interface, '
                            'or {"count": "20"} for logging.'
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["device_name", "template"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_lab_fabric",
            "description": "Run one read-only check across every device in the fabric inventory.",
            "input_schema": {
                "type": "object",
                "properties": {"check": {"type": "string", "enum": _check_enum()}},
                "required": ["check"],
                "additionalProperties": False,
            },
        },
        {
            "name": "collect_lab_evidence",
            "description": (
                "Collect the full read-only evidence bundle (every intent) from one "
                "device in a single session."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"device_name": {"type": "string"}},
                "required": ["device_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "assess_lab_health",
            "description": (
                "Evaluate deterministic health verdicts (Phase 4 role invariants + "
                'baseline drift) for one device, or pass "fabric" to evaluate every '
                "device at once."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": 'A device name, or the literal string "fabric".',
                    }
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
    ]


TOOLS: list[dict[str, Any]] = _build_tools()


def _device_from_inventory(name: str):
    for device in load_inventory_file().devices:
        if device.name == name:
            return device
    raise LLMAnalysisError(f"Unknown device in inventory: {name}")


def _assess_health(target: str) -> dict[str, Any]:
    if target == "fabric":
        listed = list_devices()
        if listed.get("status") != "success":
            return listed
        names = [device["name"] for device in listed["data"]["devices"]]
        evidence_by_device = {name: collect_evidence(name) for name in names}
        return evaluate_fabric(evidence_by_device)

    evidence = collect_evidence(target)
    device = _device_from_inventory(target)
    return evaluate_device(evidence, device)


def _execute_tool(name: str, tool_input: dict[str, Any]) -> Any:
    """Dispatch one tool call to the existing, already-safe function it wraps.

    Every branch is a thin pass-through -- there is no generic executor, and
    every device-touching argument reaches the same validation a human CLI or
    MCP caller would go through (see the module docstring).
    """

    if name == "list_lab_devices":
        return list_devices()
    if name == "run_lab_intent":
        return run_intent(tool_input["device_name"], tool_input["intent"])
    if name == "run_lab_template":
        params = dict(tool_input.get("params") or {})
        return run_template(tool_input["device_name"], tool_input["template"], **params)
    if name == "check_lab_fabric":
        return check_fabric(tool_input["check"])
    if name == "collect_lab_evidence":
        return collect_evidence(tool_input["device_name"])
    if name == "assess_lab_health":
        return _assess_health(tool_input["target"])
    raise ValueError(f"Unknown tool: {name}")


def _extract_text(message: Any) -> str:
    return "\n".join(block.text for block in message.content if getattr(block, "text", None))


def _accumulate_usage(totals: dict[str, int], usage: Any) -> None:
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        totals[field] = totals.get(field, 0) + (getattr(usage, field, None) or 0)


def _system_blocks() -> list[dict[str, Any]]:
    """Static, cacheable system prompt. Never carries device/timestamp/UUID text."""

    return [
        {
            "type": "text",
            "text": AGENT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _initial_user_content(question: str, device: str | None) -> str:
    if device:
        return f"Focus device: {device}\n\nQuestion: {question}"
    return question


def run_agent_loop(
    question: str,
    *,
    device: str | None = None,
    max_iterations: int = 8,
    time_budget_s: float = 120,
) -> dict[str, Any]:
    """Run a bounded, tool-calling agent loop to answer ``question``.

    Only the Anthropic provider is supported (see the module docstring and
    ``llm_analysis.py``'s provider notes) -- OpenAI and Ollama raise
    ``LLMAnalysisError`` with a clear message rather than attempting an
    unreliable loop.

    Returns ``{"answer", "iterations", "tool_calls", "stopped_because", "usage"}``.
    ``stopped_because`` is one of ``"end_turn"``, ``"max_iterations"``,
    ``"time_budget"``, or ``"truncated"``. Hitting a bound is a normal
    outcome: this function returns partial results rather than raising.
    """

    provider = get_provider()
    if provider != "anthropic":
        raise LLMAnalysisError(
            "The tool-calling agent loop currently requires the Anthropic provider "
            f"(LLM_PROVIDER resolved to {provider!r}). OpenAI support is not "
            "implemented yet, and a 9B local Ollama model is not reliable enough "
            "for tool calling. Set LLM_PROVIDER=anthropic (and ANTHROPIC_API_KEY) "
            "to use the agent loop."
        )

    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL_DEFAULT)

    system = _system_blocks()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _initial_user_content(question, device)}
    ]

    tool_calls: list[dict[str, Any]] = []
    usage_totals: dict[str, int] = {}
    answer = ""
    stopped_because = "end_turn"
    iteration = 0
    final_message: Any = None

    start = time.monotonic()

    while True:
        if iteration >= max_iterations:
            stopped_because = "max_iterations"
            break
        if time.monotonic() - start >= time_budget_s:
            stopped_because = "time_budget"
            break

        iteration += 1
        message = _call_anthropic_or_raise(
            client,
            model=model,
            system=system,
            messages=messages,
            tools=TOOLS,
            max_tokens=ANTHROPIC_MAX_OUTPUT_TOKENS,
        )
        _accumulate_usage(usage_totals, message.usage)
        final_message = message

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            suffix = f" (category: {category})" if category else ""
            raise LLMAnalysisError(f"The model declined to continue the agent loop.{suffix}")

        if message.stop_reason == "max_tokens":
            answer = _extract_text(message) + TRUNCATION_NOTICE
            stopped_because = "truncated"
            break

        if message.stop_reason == "pause_turn":
            # Server-side tool loop paused mid-turn; the documented resume
            # contract is to re-send the conversation with the paused
            # assistant turn appended, unchanged, and continue.
            messages.append({"role": "assistant", "content": message.content})
            continue

        if message.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": message.content})
            tool_use_blocks = [block for block in message.content if block.type == "tool_use"]

            # Every tool_use block in this one response is executed, and every
            # result goes back in a single user message -- never split across
            # multiple messages (that silently trains the model to stop
            # requesting tools in parallel).
            results: list[dict[str, Any]] = []
            for block in tool_use_blocks:
                detail: str | None = None
                try:
                    result = _execute_tool(block.name, block.input or {})
                    content = json.dumps(result)
                    # These tools report failure through the result envelope's
                    # "status", not by raising -- that is the project-wide
                    # convention. Mapping it onto is_error is what tells the
                    # model a refused or failed call needs a different approach,
                    # and what keeps the audit trail honest. "unsupported" is
                    # deliberately excluded: a platform lacking a command is a
                    # property of the fabric, not a failure.
                    is_error = isinstance(result, dict) and result.get("status") == STATUS_ERROR
                    if is_error:
                        detail = "; ".join(str(e) for e in (result.get("errors") or [])) or None
                except Exception as exc:  # noqa: BLE001 - a failed tool becomes is_error, not dropped.
                    is_error = True
                    content = f"{type(exc).__name__}: {exc}"
                    detail = content
                tool_calls.append(
                    {
                        "tool": block.name,
                        "input": block.input,
                        "is_error": is_error,
                        # Truncated so a long device output cannot bloat the
                        # audit record, but present so a caller can see *why* a
                        # call failed without re-walking the message list.
                        "error": (detail[:300] if detail else None),
                    }
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
            continue

        # "end_turn", or any stop reason this loop does not special-case:
        # treat it as done rather than looping forever on an unknown value.
        answer = _extract_text(message)
        stopped_because = "end_turn"
        break

    if stopped_because in ("max_iterations", "time_budget") and final_message is not None:
        answer = _extract_text(final_message)

    return {
        "answer": answer,
        "iterations": iteration,
        "tool_calls": tool_calls,
        "stopped_because": stopped_because,
        "usage": usage_totals,
    }
