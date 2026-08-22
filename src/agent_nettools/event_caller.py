"""The translation layer: `llm_analysis.ModelCaller` -> `event_agent.ModelCaller`.

Two lanes landed concurrently, each owning its own boundary on purpose:

* `llm_analysis.minimax_model_caller` speaks the OpenAI Responses API,
  verbatim -- `messages` are Responses-API input items
  (`{"role": "user"|"assistant", "content": str}`, plus, to continue after a
  tool call, `{"type": "function_call", "call_id", "name", "arguments":
  <raw string>}` / `{"type": "function_call_output", "call_id", "output":
  str}`), and it returns a frozen `llm_analysis.ModelTurn` whose
  `tool_calls` are `llm_analysis.ToolCall(id, name, raw_arguments,
  arguments, parse_error)`.
* `event_agent.ModelCaller` is that module's own generic internal shape:
  `messages` entries are `{"role": "user", "content": str}` /
  `{"role": "assistant", "text": str, "tool_calls": [...]}` /
  `{"role": "tool_results", "results": [{"id", "name", "content",
  "is_error"}, ...]}`, `tools` entries are `{"name", "description",
  "input_schema"}`, and a turn comes back as `{"text", "tool_calls":
  [{"id", "name", "arguments": dict}, ...], "stop_reason", "tokens"}`.
  Parameter name too: `timeout_s=`, not `timeout=`.

Neither module imports the other (`event_agent.py`'s own docstring: its
`ModelCaller` is "this module's own shape, not an import", and it stays free
of any provider import -- the injected-seam design is the point). This
module is the seam that closes over both: it imports `llm_analysis` (never
the reverse) and `event_agent` imports nothing from here either -- a caller
built by `build_event_caller` below just happens to satisfy
`event_agent.ModelCaller`'s structural protocol.

Why `raw_arguments`, never a re-serialization of `.arguments`
------------------------------------------------------------------
`llm_analysis.ModelCaller`'s own docstring: continuing a tool-calling
conversation must echo the model's own prior call back as a
`function_call` item using `ToolCall.raw_arguments` -- "the former is always
present, even when parsing failed" -- never `json.dumps(tool_call.arguments)`,
which does not exist at all when parsing failed and would be a *different
string* than what the model actually sent even when it does parse (key
order, whitespace). `event_agent`'s own `messages` list stores exactly the
mapping this adapter returned for `tool_calls` (see `run_event`'s
`messages.append({"role": "assistant", "text": text, "tool_calls":
requested_calls})`) and echoes it back verbatim on the next call --
`event_agent` never rebuilds or reserializes it. So this adapter smuggles
`raw_arguments` (and `parse_error`) through as extra keys on the dict it
hands back for each tool call; `event_agent` ignores keys it does not know
about (`call.get("name")` / `call.get("arguments")` / `call.get("id")` are
the only ones it reads), and this module reads `raw_arguments` back off the
very same dict on the next turn to build the `function_call` echo. No
provider-shaped object ever needs to survive a round trip through
`event_agent`'s own state -- only plain dicts do, which is exactly what that
module's docstring already promises callers.

Never repair or guess a fabrication
--------------------------------------
`ToolCall.arguments is None` means `raw_arguments` did not parse as a JSON
object (see that class's own docstring). This adapter passes `{}` through
as `"arguments"` in that case -- never `raw_arguments` re-parsed loosely,
never a best-effort dict built from partial content. `{}` against a tool
with pinned-only fields (`explore_lab`, `investigate_lab`: zero
model-settable fields per the probe this task was handed) resolves cleanly;
against a tool with a required enumerated field (`check_lab`'s `intent`,
`history_lab`'s `mode`) it becomes a `resolve_arguments` refusal --
`event_agent`'s own `fabrication_attempts` bookkeeping, unchanged, decides
what that refusal means. `parse_error` rides along as an extra key on the
same dict so a caller inspecting the turn (or the `on_tool_call` hook below)
can tell a genuine parse failure apart from a resolvable-but-refused call --
`event_agent.py` itself has no ticket field for `parse_error` (it is off
limits to this change; see this module's own docstring section below), so
`on_tool_call` is this adapter's own side channel for a caller that needs
it, e.g. a measurement harness.

`previous_response_id` is never used
----------------------------------------
Measured (by the other lane, `llm_analysis.ModelCaller`'s own docstring) to
return `400 invalid_prompt: tool result's tool id ... not found` against
MiniMax -- the endpoint holds no server-side turn state the way
api.openai.com does. This module rebuilds the full explicit `input` list
from `event_agent`'s own growing `messages` list on every single turn;
there is no shortcut here to accidentally reach for.

What this module cannot fix
--------------------------------
`event_agent.py`, `model_ingress.py`, `mcp_client.py` and `llm_analysis.py`
are frozen for this change (house rules). Two consequences worth naming
rather than working around silently:

1. A `parse_error` on a tool call `event_agent` goes on to *refuse* (a
   `check_lab`/`history_lab` call with a required field missing because the
   model's own JSON did not parse) is recorded by `event_agent.refuse()`
   with `model_supplied={}` -- indistinguishable, in the ticket, from a
   model that legitimately supplied nothing. Surfacing that distinction
   durably would need a new field on `event_agent`'s own ticket/refusal
   path -- flagged here, not made.
2. `event_agent`'s `exchange["tool_calls_requested"]` extracts only
   `{"tool": ..., "arguments": ...}` from each requested call, so
   `raw_arguments`/`parse_error` never reach `EventRun.exchanges` or the
   ticket either, even though they survive the `messages` round trip inside
   this adapter. Same flag as above.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import llm_analysis

__all__ = [
    "build_event_caller",
    "minimax_event_caller",
]

#: A callback a caller (typically a measurement harness) can pass to
#: `build_event_caller` to observe every `llm_analysis.ToolCall` this
#: adapter translates, independent of whatever `event_agent`'s own
#: bookkeeping does with it afterwards -- see the module docstring's "What
#: this module cannot fix" section, point 1.
OnToolCall = Callable[[Mapping[str, Any]], None]


def _responses_tool_from_offer(tool: Mapping[str, Any]) -> dict[str, Any]:
    """One `event_agent`-shaped tool manifest entry -> one Responses-API tool.

    `event_agent`/`model_ingress.ToolOffer` carry `{"name", "description",
    "input_schema"}` (an MCP-shaped tool descriptor); the Responses API
    wants a flat `{"type": "function", "name", "description", "parameters"}`
    -- `input_schema` renamed to `parameters`, everything else copied
    through unchanged. Never reshaped beyond that rename: `input_schema` is
    already the rewritten, pin-stripped schema `model_ingress.build_offers`
    produced, and this adapter has no business second-guessing it.
    """

    return {
        "type": "function",
        "name": tool.get("name"),
        "description": tool.get("description") or "",
        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
    }


def _translate_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_responses_tool_from_offer(t) for t in tools if isinstance(t, Mapping)]


def _function_call_output_text(result: Mapping[str, Any]) -> str:
    """One `event_agent` tool-result dict -> the `output` string a
    `function_call_output` item carries.

    The Responses API's `function_call_output` item has no separate
    error-flag field the way an Anthropic `tool_result` content block does
    (`llm_analysis.ModelCaller`'s own docstring states the shape verbatim,
    with no `is_error` key) -- so an error is folded into the text itself
    with a plain, unambiguous marker rather than invented as a new wire
    field this module cannot verify MiniMax accepts.
    """

    content = str(result.get("content", ""))
    return f"[ERROR] {content}" if result.get("is_error") else content


def _responses_input_from_event_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """`event_agent`'s growing `messages` list -> one explicit Responses-API
    `input` list, rebuilt from scratch every call (see the module
    docstring's `previous_response_id` section for why every call, not just
    the first, does this).

    Order is preserved -- `event_agent` only ever appends to its own
    `messages` list, so a single left-to-right pass here reproduces the
    conversation in the order it actually happened.
    """

    items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")

        if role == "user":
            items.append({"role": "user", "content": str(message.get("content", ""))})

        elif role == "assistant":
            text = message.get("text") or ""
            if text:
                items.append({"role": "assistant", "content": text})
            for call in message.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                # `raw_arguments` is this adapter's own extra key (see the
                # module docstring) -- present on every tool call this
                # adapter itself produced. A message list built by something
                # else (a hand-written test fixture that skips this
                # adapter's own return shape) has no raw string to echo;
                # "" is a stated absence, never a guess reconstructed from
                # `.arguments`.
                raw_arguments = call.get("raw_arguments")
                items.append({
                    "type": "function_call",
                    "call_id": call.get("id"),
                    "name": call.get("name"),
                    "arguments": raw_arguments if isinstance(raw_arguments, str) else "",
                })

        elif role == "tool_results":
            for result in message.get("results") or ():
                if not isinstance(result, Mapping):
                    continue
                items.append({
                    "type": "function_call_output",
                    "call_id": result.get("id"),
                    "output": _function_call_output_text(result),
                })

    return items


def _translate_tool_call(tool_call: llm_analysis.ToolCall) -> dict[str, Any]:
    """One `llm_analysis.ToolCall` -> the dict `event_agent` expects, plus
    the two extra keys (`raw_arguments`, `parse_error`) this adapter and a
    harness need later. See the module docstring's "Never repair or guess a
    fabrication" section.
    """

    return {
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": dict(tool_call.arguments) if tool_call.arguments is not None else {},
        "raw_arguments": tool_call.raw_arguments,
        "parse_error": tool_call.parse_error,
    }


def _tokens_from_usage(usage: llm_analysis.TokenUsage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "calls": usage.calls,
        "reported": usage.reported,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
    }


def _translate_turn(
    turn: llm_analysis.ModelTurn, *, on_tool_call: OnToolCall | None
) -> dict[str, Any]:
    tool_calls: list[dict[str, Any]] = []
    for tool_call in turn.tool_calls:
        translated = _translate_tool_call(tool_call)
        tool_calls.append(translated)
        if on_tool_call is not None:
            on_tool_call(translated)

    return {
        "text": turn.text,
        "tool_calls": tool_calls,
        "stop_reason": turn.stop_reason,
        "tokens": _tokens_from_usage(turn.usage),
    }


def build_event_caller(
    model_caller: llm_analysis.ModelCaller, *, on_tool_call: OnToolCall | None = None
) -> Callable[..., Mapping[str, Any]]:
    """Wrap any `llm_analysis.ModelCaller` (`minimax_model_caller`,
    `openai_model_caller`, or a test fake of that same shape) so it
    satisfies `event_agent.ModelCaller` instead.

    `on_tool_call`, if given, is invoked once per `llm_analysis.ToolCall`
    this adapter translates -- every tool call the underlying model
    actually produced, in order, regardless of what `event_agent` goes on
    to do with it (dispatch, refuse, or drop for a budget already
    exhausted). See the module docstring's "What this module cannot fix"
    section for why this exists: `event_agent`'s own ticket has no field for
    `parse_error`, so a caller that needs it (a measurement harness) reads
    it here instead.

    `ModelTurn.__post_init__`'s empty-turn invariant is never caught here --
    if `model_caller` raises (including that invariant), it propagates
    unchanged. `event_agent.run_event`'s own `caller(...)` call site already
    catches any exception and reports `stopped_because="caller_error"`; that
    is the one place this failure is meant to be handled, not here.
    """

    def _event_caller(
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        timeout_s: float,
    ) -> Mapping[str, Any]:
        responses_input = _responses_input_from_event_messages(messages)
        responses_tools = _translate_tools(tools)
        turn = model_caller(
            system=system, messages=responses_input, tools=responses_tools, timeout=timeout_s
        )
        return _translate_turn(turn, on_tool_call=on_tool_call)

    return _event_caller


#: A ready-to-inject `event_agent.ModelCaller` bound to the real MiniMax
#: endpoint, built the same way an operator would: `run_event(decision,
#: caller=minimax_event_caller, ...)`. No `on_tool_call` hook -- a caller
#: that wants one builds its own via `build_event_caller` directly (this is
#: what `scripts/measure_event_agent.py` does).
minimax_event_caller = build_event_caller(llm_analysis.minimax_model_caller)
