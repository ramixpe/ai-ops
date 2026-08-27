"""The event-woken bounded loop: an MCP client driven by a model, woken by
an event instead of a human.

The operator's own framing, verbatim: *"the AI receives a syslog, asks our
MCP for context, then asks our MCP for wide or narrow path, to investigate
the event and enrich the tickets."* `event_routing.py` already turns a raw
event into a validated :class:`~agent_nettools.event_routing.RoutingDecision`
(device, subject, flow -- never the model's to choose). This module is what
happens next: build the ONE manifest a model may see for that decision,
drive a bounded tool-calling loop against the staged MCP surface through
`mcp_client.McpToolset`, and write everything the code itself observed onto
a ticket -- never what a model claims it did.

Why not `agent_loop.py`
-------------------------
`agent_loop.py` is the shape this module borrows (bounded turns, a
tool-calling `while` loop, `stopped_because`/`complete` reported
structurally) -- explicitly NOT its code. That loop calls `network_tools`
directly, in-process, with no MCP boundary in between and no argument
pinning: a model there can name any device, any peer address, any prefix.
This loop exists because that is exactly the gap B-459/`MCP-EXPERIMENT.md`
§6.3 measured (see `model_ingress.py`'s own docstring) -- an event-driven
caller with no human in the loop to notice a fabricated argument is the
worst place to leave that gap open. So this module is built on the MCP
boundary (`mcp_client.McpToolset`) and the argument-pinning layer
(`model_ingress.build_offers`/`resolve_arguments`) instead, and per house
rules it does not import `agent_loop.py` at all.

The hard gate: `mnemonics.yaml`'s `trigger.fires`, not an env var
--------------------------------------------------------------------
`run_event` refuses any mnemonic whose `trigger.fires` is not `true` in
`data/mnemonics.yaml` -- checked before anything else (routability first,
then this). As of 2026-08-21 every one of the 20 reviewed entries is
`fires: false` (see that file's own header comment for the measured
reasoning per entry), so this module ships doing nothing even with a
`toolset`/`caller` fully wired up and every other gate open. That is
`event_watch.py`'s own precedent, restated at a different call site: a
mechanism is proven by what it does with a table that CAN say `fires: true`
(a synthetic table, in the positive-control test), never by what it does
with the one table that currently says no to everything.

Where the trigger table comes from
-----------------------------------
`trigger_table.py` owns the reviewed table lookup, validation, and exception
type. It depends only on `knowledge.py`; neither this bounded model loop nor
the Loki watcher import the other to apply trigger policy. The small local
wrappers retain their prior public seams for callers/tests while delegating
all policy decisions to that leaf module.

Two measured facts this loop's dispatch must honour (today's lanes)
------------------------------------------------------------------------
1. **`ToolCallResult.is_error` is `True` only for an MCP protocol-level
   failure** (an unknown tool name) -- see `mcp_client.py`'s own contract.
   A registered tool's own classified `status: "error"` envelope --
   including the active-probe refusal `mcp_server/server.py` builds for a
   gated tool -- comes back `is_error=False`, with the error **inside
   `.text`** as parsed JSON. `_classify_call_result` below checks both
   shapes and folds them into one `is_error` the loop and the ticket agree
   on; missing the second shape would let a refused call read as a
   successful one everywhere downstream.
2. **A legitimate tool-calling turn has EMPTY text** (OBS-698, measured).
   The real error condition is a turn with **no text AND no tool calls** --
   the model produced nothing at all -- never "empty text" by itself, which
   is the normal shape of a turn that is entirely a tool request. See the
   `stopped_because = "empty_turn"` branch below.

The ticket split is the point
--------------------------------
`ticket.py`'s own rule, applied here: every `record_*` call this module
makes carries something CODE observed -- a `RoutingDecision`, a
`PinnedContext`, a bound, a dispatched call's arguments AS DISPATCHED
(pinned values included), a `ToolCallResult`. `record_model_exchange` is the
one place a model's own text reaches the ticket, and it goes in as
`response_text`/a `model_exchange` section, never as `record_answer`.
**If `investigate_lab` never actually produced a usable `finding`/
`trustworthy` payload, `record_answer` is never called at all** -- not with
an empty or a `None` finding, which would still create an Answer section for
a reader to find. Absence is never zero: a ticket with no Answer section
tells a reader "nothing was concluded", where a fabricated placeholder would
not.

`ModelCaller`: this module's own shape, not an import
----------------------------------------------------------
Another lane is adding a `ModelCaller` protocol to `llm_analysis.py`,
concurrently, in this same session -- and `llm_analysis.py` is off limits to
this module regardless (house rules). So `ModelCaller` below is this
module's OWN minimal structural expectation, satisfied by any plain
callable with the right keyword arguments -- the same `sender=`/`fetcher=`/
`toolset=` duck-typing idiom already used throughout this codebase
(`network_tools.sender`, `logs_loki.fetcher`, `mcp_client.McpToolset`
itself), not a class a real implementation has to inherit from or import
this module to satisfy. Whatever the other lane ships can drive this loop
as long as it can be wrapped in one function of this shape; if it cannot,
that is a fact about this assumed shape being wrong, discoverable without
this module ever importing the other lane's in-flight work.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import admission, event_notification
from . import trigger_table as _trigger_table
from ._env import _float_env
from .event_envelope import EventEnvelopeV2
from .event_reporting import InvestigationReceipt, ReceiptValidationError
from .event_routing import MNEMONIC_FLOW_TABLE, RoutingDecision
from .event_store import EventRecord, EventStore, EventStoreError, EventTransitionError
from .incident_case import IncidentIdentity
from .investigation_activity_adapters import (
    activities_for_receipt,
    command_preview_activity,
    started_activity,
    tool_result_activity,
    tool_selected_activity,
    visible_reasoning_tail_activity,
)
from .knowledge import load_mnemonic_table
from .mcp_client import McpToolset, ToolCallResult, child_server_env
from .model_ingress import (
    ArgumentRefusal,
    PinnedContext,
    ToolOffer,
    build_offers,
    resolve_arguments,
)
from .prompt_library import load_prompt
from .telegram_card_coordinator import TelegramCardCoordinator
from .ticket import open_ticket

NETTOOLS_MAX_EVENT_AGE_SECONDS_ENV = "NETTOOLS_MAX_EVENT_AGE_SECONDS"
DEFAULT_MAX_EVENT_AGE_SECONDS = 300.0

__all__ = [
    "AgentBounds",
    "EventRun",
    "ModelCaller",
    "TriggerTableInconsistency",
    "build_trigger_index",
    "plan_event",
    "run_event",
    "validate_trigger_table",
]


# --------------------------------------------------------------------------- #
# AgentBounds / EventRun -- the shape this task specified verbatim.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentBounds:
    """Every ceiling this loop enforces. Defaults are deliberately tight --
    an unattended, event-woken loop has no human watching to notice it
    running long, so the safe direction to err is small.

    ``max_calls_per_tool`` defaults to ``{"investigate_lab": 1}`` --
    `investigate_lab` is the narrow, authoritative tool (the deterministic
    descent); nothing about asking it twice for the same pinned
    device/subject/flow could produce a second, different answer, so a
    second call is never useful and always billable against the shared
    ``max_tool_calls``/``time_budget_s`` ceilings for nothing. Tools absent
    from this mapping (``explore_lab``, ``check_lab``, ``history_lab``,
    ``probe_lab``) have no per-tool cap of their own -- only the shared
    ``max_tool_calls`` bounds them.
    """

    max_iterations: int = 4
    time_budget_s: float = 90.0
    max_tool_calls: int = 6
    max_calls_per_tool: Mapping[str, int] = field(
        default_factory=lambda: {"investigate_lab": 1}
    )
    max_result_chars: int = 20_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "time_budget_s": self.time_budget_s,
            "max_tool_calls": self.max_tool_calls,
            "max_calls_per_tool": dict(self.max_calls_per_tool),
            "max_result_chars": self.max_result_chars,
        }


@dataclass(frozen=True)
class EventRun:
    """One `plan_event`/`run_event` outcome, both ways -- ``reason`` is
    always populated, the same discipline `RoutingDecision` itself already
    uses, for the identical reason: a caller branching only on ``ran``
    should never have to guess why the other branch happened.

    ``tool_calls`` (code-observed: `mcp_client.ToolCallResult`-derived
    dicts, "as dispatched") and ``exchanges`` (model-claimed: one dict per
    model turn) are deliberately separate fields, mirroring `ticket.py`'s
    own `code_observed`/`model_claimed` split at the in-memory level, before
    either ever reaches disk.
    """

    ran: bool
    reason: str
    mode: str  # "plan" | "fixtures" | "live"
    decision: dict[str, Any]
    bounds: dict[str, Any]
    tools_offered: tuple[str, ...]
    tool_calls: tuple[dict[str, Any], ...]
    exchanges: tuple[dict[str, Any], ...]
    stopped_because: str
    complete: bool
    limits_hit: tuple[dict[str, Any], ...]
    fabrication_attempts: tuple[dict[str, Any], ...]
    ticket_run_id: str | None
    elapsed_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "mode": self.mode,
            "decision": self.decision,
            "bounds": self.bounds,
            "tools_offered": list(self.tools_offered),
            "tool_calls": list(self.tool_calls),
            "exchanges": list(self.exchanges),
            "stopped_because": self.stopped_because,
            "complete": self.complete,
            "limits_hit": list(self.limits_hit),
            "fabrication_attempts": list(self.fabrication_attempts),
            "ticket_run_id": self.ticket_run_id,
            "elapsed_s": self.elapsed_s,
        }


# --------------------------------------------------------------------------- #
# ModelCaller -- this module's own structural expectation. See the module
# docstring's "ModelCaller: this module's own shape, not an import" section.
# --------------------------------------------------------------------------- #


class ModelCaller(Protocol):
    """A callable that drives one model turn.

    Called as ``caller(system=..., messages=..., tools=..., timeout_s=...)``
    and expected to return a plain mapping (a dict is fine; anything with
    ``.get`` works) shaped:

    ``{"text": str, "tool_calls": [{"id": str, "name": str, "arguments":
    dict}, ...], "stop_reason": str, "tokens": dict | None}``

    ``text`` may legitimately be empty on a pure tool-calling turn (OBS-698
    -- see the module docstring). ``tool_calls`` may legitimately be empty
    on a completing turn. ``stop_reason`` is free text this module never
    special-cases beyond ``"end_turn"`` (the only value `complete` treats as
    a genuine finish) -- an unrecognised value is reported plainly, the same
    "name it, don't fold it into a bucket that could be mistaken for success"
    rule `agent_loop.py`'s own `unknown_stop:` prefix follows, simplified
    here because this loop already reports `stopped_because` as whatever
    `stop_reason` said, verbatim.

    ``messages`` is a growing list of plain dicts this module builds and
    owns the shape of -- ``{"role": "user", "content": str}``,
    ``{"role": "assistant", "text": str, "tool_calls": [...]}``,
    ``{"role": "tool_results", "results": [{"id", "name", "content",
    "is_error"}, ...]}`` -- never a provider-specific SDK object. A real
    caller (MiniMax, or anything else) is responsible for translating this
    generic shape into whatever wire format its own API needs; that
    translation lives in the caller, not here, which is what keeps this
    loop provider-agnostic the same way `agent_loop.py`'s own docstring
    argues a hand-written loop should be.
    """

    def __call__(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_s: float,
    ) -> Mapping[str, Any]: ...


# --------------------------------------------------------------------------- #
# The trigger table -- public compatibility wrappers over the shared policy.
# --------------------------------------------------------------------------- #


TriggerTableInconsistency = _trigger_table.TriggerTableInconsistency


def build_trigger_index(
    table: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """``mnemonic -> its trigger block``, from ``mnemonics.yaml`` by default.

    ``table=`` is the same injection seam the watcher exposes: a test
    supplies a synthetic table so a positive control can prove this
    mechanism fires on a table that CAN say yes, without needing the live,
    reviewed table to have any `fires: true` entries today (it does not).
    """

    entries = table if table is not None else load_mnemonic_table()
    return _trigger_table.build_trigger_index(entries)


def validate_trigger_table(table: Iterable[Mapping[str, Any]] | None = None) -> None:
    """Every ``fires: true`` entry must have a working route, or raise.

    The policy lives in `trigger_table.validate_trigger_table`; this wrapper
    supplies the event route table while preserving the caller-facing API.
    """

    entries = table if table is not None else load_mnemonic_table()
    _trigger_table.validate_trigger_table(
        entries,
        routable_mnemonics=(mnemonic for mnemonic, _flow, _extract in MNEMONIC_FLOW_TABLE),
    )


# --------------------------------------------------------------------------- #
# A declared, offline tool manifest -- planning-only. See _resolve_context /
# plan_event: this is what lets plan_event resolve with NO live MCP
# connection at all.
# --------------------------------------------------------------------------- #

#: A declared, offline copy of the staged surface's five PIN_TABLE-covered
#: tools' schemas -- sufficient for `build_offers` to resolve a manifest with
#: NO live MCP connection, which is what makes `plan_event` able to "resolve
#: everything, call nothing" (this task's own required shape for it).
#: Declared, not detected -- the same discipline `model_ingress.PIN_TABLE`
#: documents for the identical reason: `mcp_server` depends on
#: `agent_nettools`, never the reverse, so this module cannot import
#: `mcp_server.staged_surface` to read the real schemas even for planning.
#: **Never the schema `run_event`'s live/fixtures path actually dispatches
#: against** -- that always comes from `toolset.list_tools()`, the MCP
#: server's own live answer; this manifest exists only so a human running
#: `plan_event()` offline sees an honest preview. `lookup_lab` is absent on
#: purpose -- it has no `PIN_TABLE` entry (see that module's
#: `STRUCTURALLY_EXCLUDED_TOOLS`) and `build_offers` would drop it anyway.
#: If `mcp_server/staged_surface.py`'s five function signatures ever change,
#: this table and `model_ingress.PIN_TABLE` both need a matching hand edit --
#: there is no mechanism that could catch drift between them automatically,
#: the same tradeoff `model_ingress.py`'s own `PIN_TABLE` already accepts.
_PLANNING_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "explore_lab",
        "description": "Answers: what is here, and is it broadly OK?",
        "input_schema": {
            "type": "object",
            "properties": {"device_name": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "check_lab",
        "description": "Answers: what is the state of X right now?",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "intent": {
                    "type": "string",
                    "enum": ["facts", "interfaces", "bgp", "lldp", "isis", "sr"],
                },
            },
            "required": ["scope"],
        },
    },
    {
        "name": "investigate_lab",
        "description": "Answers: why is this broken? -- the deterministic dependency descent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "subject": {"type": "string"},
                "flow": {
                    "type": "string",
                    "enum": ["bgp_session", "interface", "isis_adjacency", "ldp_session"],
                },
            },
            "required": ["device_name", "subject"],
        },
    },
    {
        "name": "history_lab",
        "description": "Answers: what changed on this device?",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["latest_diff", "golden_diff", "flaps"],
                },
            },
            "required": ["device_name"],
        },
    },
    {
        "name": "probe_lab",
        "description": "ACTIVE PROBE: sends ICMP/UDP traffic to the target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "kind": {"type": "string", "enum": ["ping", "traceroute"]},
                "address": {"type": "string"},
            },
            "required": ["device_name", "kind", "address"],
        },
    },
)


# --------------------------------------------------------------------------- #
# Resolution: routability -> the hard gate -> a PinnedContext. Shared by
# plan_event and run_event so the two never disagree about why something
# was refused.
# --------------------------------------------------------------------------- #


def _resolve_context(decision: RoutingDecision) -> tuple[bool, str, PinnedContext | None]:
    """``(ok, reason, context)`` -- every refusal this loop can hit before it
    ever needs a tool manifest, a toolset, or a caller. ``reason`` is always
    populated (`RoutingDecision`'s own discipline, restated)."""

    if not decision.routable:
        return False, f"routing decision is not routable: {decision.reason}", None

    mnemonic = decision.matched
    if not mnemonic:
        return False, (
            "routing decision names no mnemonic (matched=None); nothing to "
            "check against mnemonics.yaml's trigger table"
        ), None

    # Deliberately not caught: see TriggerTableInconsistency's own docstring.
    validate_trigger_table()
    index = build_trigger_index()
    entry = index.get(mnemonic)
    if entry is None:
        return False, (
            f"mnemonic {mnemonic!r} has no trigger entry in mnemonics.yaml -- "
            "unclassified, not cleared to fire"
        ), None
    if not entry.get("fires"):
        return False, str(
            entry.get("reason") or f"mnemonics.yaml trigger.fires is false for {mnemonic!r}"
        ), None

    try:
        context = PinnedContext(device=decision.device, subject=decision.subject, flow=decision.flow)
    except ValueError as exc:
        return False, f"could not pin an event context: {exc}", None

    return True, f"{mnemonic} is cleared to fire by mnemonics.yaml (trigger.fires: true)", context


#: The tools this loop offers -- `model_ingress.PIN_TABLE`'s pinnable set
#: MINUS `probe_lab`, and the subtraction is the point.
#:
#: `probe_lab` is pinnable, so passing `PIN_TABLE.keys()` offered it: on any
#: `bgp_session` flow the subject is IPv4-shaped, `address` and `device_name`
#: pin cleanly, and the model was handed ping/traceroute with `kind` free to
#: choose. Measured, not reasoned about (OBS-701) -- and it made
#: `prompts/event_agent.v1.txt` state something false to the model: *"The
#: active-probe kind (ping/traceroute) is not offered to this loop."*
#:
#: Nothing could have been probed: `child_server_env()` sets
#: `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES=0` in the SPAWNED server's own
#: environment, so `_active_probes_refused` fires inside the child before the
#: tool body runs, whatever the operator's `.env` says. That layer held and is
#: why this was a gap rather than an incident. But an unattended loop should
#: not be shown a tool whose only possible outcome is a refusal, and a
#: mechanism that works only because a second one catches it is one env
#: default away from working not at all.
#:
#: Declared here rather than reused from `PIN_TABLE` because "what may be
#: pinned" and "what this loop offers" are two different questions that
#: happened to have the same answer. They no longer do.
OFFERED_TOOLS = frozenset({"explore_lab", "check_lab", "investigate_lab", "history_lab"})


def _offer_tools(tools: Iterable[Mapping[str, Any]], context: PinnedContext) -> tuple[ToolOffer, ...]:
    #: `OFFERED_TOOLS`, never `mcp_server.staged_surface.STAGED_TOOL_NAMES`
    #: (that would import `mcp_server`, the forbidden direction) and no longer
    #: `PIN_TABLE.keys()` -- see `OFFERED_TOOLS`' own comment for what that
    #: silently included.
    return build_offers(tools, context, allowlist=OFFERED_TOOLS)


def _question_text(decision: RoutingDecision) -> str:
    """The ticket's ``question`` text: the routing decision's own already-
    validated fields, reconstructed into one line.

    This task's own spec asks for "the raw syslog line, quoted" --
    `event_routing.RoutingDecision` does not preserve one: `route_syslog_line`
    discards the original line text once it has extracted a mnemonic and a
    subject, and `event_routing.py` is off limits to this change (house
    rules; it belongs to another lane's already-landed work). This is
    therefore the closest honest substitute reachable from what
    `plan_event`/`run_event` are actually handed -- every field here is
    code-validated, quoted verbatim, never a model's paraphrase of it. If the
    ticket ever needs the literal original line, `RoutingDecision` would need
    a new field carrying it -- a change to `event_routing.py`, out of this
    module's scope; flagged here rather than silently worked around.
    """

    parts = [f"mnemonic={decision.matched!r}", f"device={decision.device!r}"]
    if decision.subject is not None:
        parts.append(f"subject={decision.subject!r}")
    if decision.flow is not None:
        parts.append(f"flow={decision.flow!r}")
    parts.append(f"source_kind={decision.source_kind!r}")
    return "event: " + ", ".join(parts)


def _initial_user_content(decision: RoutingDecision) -> str:
    return (
        f"Event: {decision.matched} on device {decision.device}.\n"
        f"Subject: {decision.subject}\n"
        f"Flow: {decision.flow}\n"
        f"Routing reason: {decision.reason}\n\n"
        "Investigate this event using the tools available to you. Prefer a "
        "wide tool before the narrow one, and state a finding -- \"no fault "
        "found\" is a complete answer if that is genuinely what the "
        "evidence shows."
    )


# --------------------------------------------------------------------------- #
# plan_event -- resolve everything, call nothing.
# --------------------------------------------------------------------------- #


def plan_event(decision: RoutingDecision) -> EventRun:
    """Resolve a `RoutingDecision` all the way to an offered tool manifest,
    without opening an MCP connection, calling a model, or writing a ticket.

    ``mode`` is always ``"plan"`` and ``ran`` is always `False` -- nothing
    executes here by construction. This is the dry-run surface: an operator
    (or a test) can see exactly what `run_event` WOULD offer a model for a
    given event, using the same gate and the same argument-pinning rules
    `run_event` itself uses, before ever spawning the real MCP server
    subprocess.
    """

    start = time.monotonic()
    ok, reason, context = _resolve_context(decision)
    offers: tuple[ToolOffer, ...] = ()
    if context is not None:
        offers = _offer_tools(_PLANNING_TOOL_SCHEMAS, context)
        if ok and not offers:
            ok = False
            reason = "no tools could be offered for this context (build_offers returned none)"

    return EventRun(
        ran=False,
        reason=reason,
        mode="plan",
        decision=decision.as_dict(),
        bounds=AgentBounds().as_dict(),
        tools_offered=tuple(o.name for o in offers),
        tool_calls=(),
        exchanges=(),
        stopped_because="not_run",
        complete=False,
        limits_hit=(),
        fabrication_attempts=(),
        ticket_run_id=None,
        elapsed_s=time.monotonic() - start,
    )


# --------------------------------------------------------------------------- #
# The two ToolCallResult error shapes, unified. See the module docstring's
# "Two measured facts" section, point 1.
# --------------------------------------------------------------------------- #


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _classify_call_result(result: ToolCallResult) -> tuple[bool, str | None]:
    """``(is_error, detail)`` -- both `ToolCallResult.is_error` shapes,
    folded into one. `result.is_error=True` is an MCP protocol-level failure
    (an unknown tool name) and is trusted directly. `result.is_error=False`
    can still carry a registered tool's own classified `status: "error"`
    envelope inside `.text` -- including the active-probe refusal
    `mcp_server/server.py` builds (`_active_probes_refused`) -- so that shape
    is checked too, by parsing `.text` as JSON. Neither shape is preferred
    over the other; both are real, and a caller (the loop, the ticket) needs
    exactly one boolean to react to.
    """

    if result.is_error:
        return True, (result.text[:300] if result.text else None)

    payload = _safe_json(result.text)
    if isinstance(payload, Mapping) and payload.get("status") == "error":
        errors = payload.get("errors")
        detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) else None
        return True, (detail or "status: error")[:300]

    return False, None


# --------------------------------------------------------------------------- #
# run_event -- the bounded loop itself.
# --------------------------------------------------------------------------- #


def _event_age_refusal(decision: RoutingDecision) -> str | None:
    """Refuse stale Loki replays before any MCP/model activity.

    IOS-XR device timestamps omit the year and cannot establish age safely.
    Only Loki decisions carry an epoch ingest timestamp. Direct receiver
    decisions are immediate and therefore have no replay-age comparison.
    """

    if decision.ingest_timestamp_ns is None:
        return None
    try:
        ingest_seconds = int(decision.ingest_timestamp_ns) / 1_000_000_000
    except (TypeError, ValueError):
        return "Loki event ingest timestamp is unusable; refusing before model activity"
    max_age = _float_env(NETTOOLS_MAX_EVENT_AGE_SECONDS_ENV, DEFAULT_MAX_EVENT_AGE_SECONDS)
    if max_age < 0:
        max_age = DEFAULT_MAX_EVENT_AGE_SECONDS
    age = time.time() - ingest_seconds
    if age > max_age:
        return (
            f"Loki event is stale ({age:.1f}s old; maximum {max_age:g}s); "
            "refusing before model activity"
        )
    return None


def run_event(
    decision: RoutingDecision,
    *,
    bounds: AgentBounds | None = None,
    toolset: McpToolset | None = None,
    caller: ModelCaller | None = None,
    now: Any = None,
    admit: bool = True,
    event_store: EventStore | None = None,
    event_lease: EventRecord | None = None,
) -> EventRun:
    """Run the bounded, event-woken tool-calling loop for one routing
    decision.

    ``toolset``/``caller`` are injected -- the way `sender=`/`fetcher=`/
    `analyst=` already are across this repo -- so this whole module tests
    with no lab, no API key, and no MCP SDK: a fake `toolset` (any object
    with `.list_tools()`/`.call_tool()` matching `McpToolset`'s own methods)
    and a fake `caller` (any callable matching `ModelCaller`'s shape) are
    enough. ``mode`` reports which: ``toolset`` supplied -> `"fixtures"`
    (whatever backs it is the caller's choice -- committed captures, hand-
    built results, anything not the real subprocess); ``toolset`` omitted ->
    `"live"`, and this function opens a REAL `McpToolset` itself (spawning
    `mcp_server.server` with `child_server_env()`'s defaults: the staged
    surface, active probes disabled -- see the prompt's own "no traffic can
    be generated" constraint, which this default is what actually makes
    true, not merely states).

    ``caller`` has **no live default** -- there is no sanctioned way for this
    module to construct a real MiniMax-backed caller without importing
    `llm_analysis.py`, which is off limits to this change (house rules, and
    another lane is editing it concurrently). Without one, `run_event`
    refuses cleanly (``ran=False``) rather than guessing; wiring a real
    default caller in is a follow-up task's job, once that lane's
    `ModelCaller` (or an adapter satisfying this module's own `ModelCaller`
    shape) exists.

    Never raises for a routine refusal or a runtime failure -- every branch
    below returns a populated `EventRun` instead, the same "never raise past
    the tool boundary" discipline `ticket.py`/`event_routing.py`/
    `logs_loki.py` all already keep. The one deliberate exception is
    `TriggerTableInconsistency`, raised from `_resolve_context` -- see that
    class's own docstring for why a reviewed data table's internal
    inconsistency is not the same kind of failure as an unreachable MCP
    server or a bad model response, and must not be swallowed the same way.
    """

    bounds = bounds or AgentBounds()
    now_fn = now or time.monotonic
    start = now_fn()
    active_lease = event_lease

    def lease_arguments() -> dict[str, Any]:
        if active_lease is None:
            return {}
        return {
            "lease_owner": active_lease.lease_owner,
            "lease_epoch": active_lease.lease_epoch,
        }

    def store_terminal(run: EventRun) -> EventRun:
        if event_store is None:
            return run
        try:
            current = event_store.get(decision.event_id)
            if current is None:
                return run
            if run.complete:
                event_store.transition(
                    decision.event_id,
                    "completed",
                    ticket_id=run.ticket_run_id,
                    **lease_arguments(),
                )
            elif current.state in {"admitted", "running"}:
                event_store.schedule_retry(
                    decision.event_id,
                    reason=run.stopped_because,
                    ticket_id=run.ticket_run_id,
                    **lease_arguments(),
                )
        except (EventStoreError, EventTransitionError):
            # The existing filesystem admission remains authoritative during
            # this migration slice; storage mirroring must not consume a run.
            pass
        return run

    if event_store is not None:
        envelope = EventEnvelopeV2.from_routing_decision(decision)
        try:
            record, created = event_store.create_or_get(
                event_id=envelope.event_id,
                device=envelope.device or "unknown",
                payload=envelope.payload(),
            )
            if active_lease is not None:
                current = event_store.get(decision.event_id)
                if (
                    active_lease.event_id != decision.event_id
                    or current is None
                    or current.state != "running"
                    or current.lease_owner != active_lease.lease_owner
                    or current.lease_epoch != active_lease.lease_epoch
                ):
                    raise EventStoreError("event lease does not match current durable work")
                record = current
            elif record.state == "running" and record.lease_owner is not None:
                raise EventStoreError("active event lease context is required")
            if not created and record.state == "completed":
                return EventRun(
                    ran=False,
                    reason="event already completed in durable store",
                    mode="plan",
                    decision=decision.as_dict(),
                    bounds=bounds.as_dict(),
                    tools_offered=(),
                    tool_calls=(),
                    exchanges=(),
                    stopped_because="event_store_completed_duplicate",
                    complete=True,
                    limits_hit=(),
                    fabrication_attempts=(),
                    ticket_run_id=record.ticket_id,
                    elapsed_s=now_fn() - start,
                )
            if active_lease is not None:
                pass
            elif record.state == "retryable_failed":
                event_store.transition(decision.event_id, "admitted")
            elif record.state == "received":
                event_store.transition(decision.event_id, "admitted")
            if record.state != "running":
                event_store.transition(decision.event_id, "running")
        except (EventStoreError, EventTransitionError) as exc:
            return EventRun(
                ran=False,
                reason=f"durable event store unavailable: {exc}",
                mode="plan",
                decision=decision.as_dict(),
                bounds=bounds.as_dict(),
                tools_offered=(),
                tool_calls=(),
                exchanges=(),
                stopped_because="event_store_failed",
                complete=False,
                limits_hit=(),
                fabrication_attempts=(),
                ticket_run_id=None,
                elapsed_s=now_fn() - start,
            )

    age_refusal = None if active_lease is not None else _event_age_refusal(decision)
    if age_refusal is not None:
        return EventRun(
            ran=False, reason=age_refusal, mode="plan", decision=decision.as_dict(),
            bounds=bounds.as_dict(), tools_offered=(), tool_calls=(), exchanges=(),
            stopped_because="stale_event", complete=False,
            limits_hit=({"limit": "event_age", "reason": age_refusal},),
            fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
        )

    ok, reason, context = _resolve_context(decision)
    if not ok:
        return EventRun(
            ran=False, reason=reason, mode="plan", decision=decision.as_dict(),
            bounds=bounds.as_dict(), tools_offered=(), tool_calls=(), exchanges=(),
            stopped_because="not_run", complete=False, limits_hit=(),
            fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
        )
    if context is None:
        raise TriggerTableInconsistency(
            "event context resolver returned success without a pinned context"
        )

    if caller is None:
        return EventRun(
            ran=False,
            reason="no ModelCaller supplied; run_event needs one to drive the loop",
            mode="plan", decision=decision.as_dict(), bounds=bounds.as_dict(),
            tools_offered=(), tool_calls=(), exchanges=(), stopped_because="not_run",
            complete=False, limits_hit=(), fabrication_attempts=(), ticket_run_id=None,
            elapsed_s=now_fn() - start,
        )

    mode = "fixtures" if toolset is not None else "live"
    if admit:
        try:
            admission.admit_event_run(decision.event_id, context.device)
        except admission.AdmissionDenied as exc:
            if event_store is not None:
                try:
                    event_store.record_admission_shadow(
                        event_id=decision.event_id,
                        legacy_admitted=False,
                        legacy_reason=f"{exc.refusal.scope}: {exc.refusal.reason}",
                    )
                    event_store.schedule_retry(
                        decision.event_id,
                        reason=f"legacy admission refused: {exc.refusal.scope}",
                    )
                except (EventStoreError, EventTransitionError):
                    pass
            return EventRun(
                ran=False, reason=f"event admission refused ({exc.refusal.scope}): {exc.refusal.reason}",
                mode=mode, decision=decision.as_dict(), bounds=bounds.as_dict(), tools_offered=(),
                tool_calls=(), exchanges=(), stopped_because="event_admission_refused", complete=False,
                limits_hit=({"limit": exc.refusal.scope, "reason": exc.refusal.reason},),
                fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
            )
        if event_store is not None:
            try:
                event_store.record_admission_shadow(
                    event_id=decision.event_id,
                    legacy_admitted=True,
                    legacy_reason=None,
                )
            except EventStoreError:
                pass

    def finish_admission(run: EventRun) -> EventRun:
        if admit:
            admission.finish_event_run(decision.event_id, completed=run.complete)
        return run

    if toolset is not None:
        return store_terminal(finish_admission(
            _dispatch(decision, context, bounds, toolset, caller, "fixtures", now_fn, start, event_store)
        ))

    try:
        with McpToolset(env_overrides=child_server_env()) as live_toolset:
            return store_terminal(finish_admission(
                _dispatch(decision, context, bounds, live_toolset, caller, "live", now_fn, start, event_store)
            ))
    except Exception as exc:  # noqa: BLE001 -- starting the live MCP server must degrade, never crash an unattended event handler
        return store_terminal(finish_admission(EventRun(
            ran=False, reason=f"could not start the live MCP server: {exc}", mode="live",
            decision=decision.as_dict(), bounds=bounds.as_dict(), tools_offered=(),
            tool_calls=(), exchanges=(), stopped_because="mcp_start_failed", complete=False,
            limits_hit=(), fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
        )))


def _dispatch(
    decision: RoutingDecision,
    context: PinnedContext,
    bounds: AgentBounds,
    toolset: Any,
    caller: ModelCaller,
    mode: str,
    now_fn: Any,
    start: float,
    event_store: EventStore | None,
) -> EventRun:
    """The gate has already passed and a toolset/caller are in hand. Build
    the real manifest, open the ticket, and run the bounded loop."""

    deadline = start + bounds.time_budget_s

    try:
        live_tools = toolset.list_tools()
    except Exception as exc:  # noqa: BLE001 -- listing tools degrades like every other MCP interaction on this path
        return EventRun(
            ran=False, reason=f"could not list tools from the MCP server: {exc}", mode=mode,
            decision=decision.as_dict(), bounds=bounds.as_dict(), tools_offered=(),
            tool_calls=(), exchanges=(), stopped_because="list_tools_failed", complete=False,
            limits_hit=(), fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
        )

    tool_dicts = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in live_tools
    ]
    offers = _offer_tools(tool_dicts, context)
    if not offers:
        return EventRun(
            ran=False,
            reason="no tools could be offered for this context against the live manifest",
            mode=mode, decision=decision.as_dict(), bounds=bounds.as_dict(), tools_offered=(),
            tool_calls=(), exchanges=(), stopped_because="not_run", complete=False,
            limits_hit=(), fabrication_attempts=(), ticket_run_id=None, elapsed_s=now_fn() - start,
        )

    offers_by_name = {o.name: o for o in offers}
    tools_offered = tuple(o.name for o in offers)
    #: The REWRITTEN manifest -- every pinned parameter already deleted by
    #: `build_offers` -- is what both the model (`tools=`) and the ticket
    #: (`record_model_exchange`'s `tools_offered`) see. Never the live, raw
    #: manifest `toolset.list_tools()` returned.
    tools_manifest = [
        {"name": o.name, "description": o.description, "input_schema": o.input_schema}
        for o in offers
    ]

    system_prompt = load_prompt("event_agent", 1)

    ticket = open_ticket(
        decision.subject or decision.device,
        entry_point="event_agent",
        device=decision.device,
        flow=decision.flow,
    )
    ticket.record_question(
        _question_text(decision),
        device=decision.device, subject=decision.subject, flow_hint=decision.flow,
        extra={"decision": decision.as_dict(), "mode": mode, "raw_event": decision.raw_event},
    )
    ticket.record_intent(
        flow=decision.flow, resolved_subject=context.subject, resolver="event_routing",
        notes=f"mode={mode}",
        extra={
            "pinned_context": {"device": context.device, "subject": context.subject, "flow": context.flow},
            "mode": mode,
            "bounds": bounds.as_dict(),
            #: Structurally the only surface this loop can ever run
            #: against -- `model_ingress.PIN_TABLE` is declared against the
            #: staged surface's own six functions, not the classic surface's
            #: 21-37 tools, so there is no other value this could honestly be.
            "surface": "staged",
            "tool_allowlist": sorted(OFFERED_TOOLS),
            "tools_offered": list(tools_offered),
        },
    )
    live_card_mode = (
        event_store is not None
        and event_notification.notifications_enabled()
        and event_notification.live_card_replaces_legacy()
    )
    coordinators = tuple(
        TelegramCardCoordinator(event_store, chat_id=chat_id)
        for chat_id in event_notification.live_card_chat_ids()
    ) if live_card_mode and event_store is not None else ()
    if coordinators:
        activity = started_activity(decision, incident_id=ticket.incident_id)
        for coordinator in coordinators:
            event_notification.deliver_live_card(event_store, coordinator.apply(activity))
        notification = None
    else:
        notification = event_notification.open_notification(
            decision.event_id,
            ticket_id=ticket.incident_id,
            device=context.device,
            subject=context.subject,
        )
        event_notification.investigation_plan(
            notification,
            flow=context.flow,
            max_iterations=bounds.max_iterations,
            max_tool_calls=bounds.max_tool_calls,
            time_budget_s=bounds.time_budget_s,
        )

    activity_sequence = 10

    def emit_chat_activity(activity) -> None:
        if not coordinators:
            return
        for coordinator in coordinators:
            event_notification.deliver_live_card(event_store, coordinator.apply(activity))
            event_notification.deliver_live_activity(event_store, chat_id=coordinator.chat_id, activity=activity)

    messages: list[dict[str, Any]] = [{"role": "user", "content": _initial_user_content(decision)}]
    exchanges: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    fabrication_attempts: list[dict[str, Any]] = []
    limits_hit: list[dict[str, Any]] = []
    hit_limits: set[str] = set()
    calls_per_tool: dict[str, int] = {}
    total_calls = 0
    iteration = 0
    stopped_because = "end_turn"
    #: Set on exactly one branch below: the model returned a turn with text and
    #: no tool calls -- it stopped asking, of its own accord. That, and nothing
    #: else, is what `complete` means.
    #:
    #: It is deliberately NOT `stopped_because == "end_turn"`, which is what
    #: this was until it was measured. `stopped_because` reports the PROVIDER's
    #: own stop word verbatim (a rule this module keeps on purpose -- see
    #: `ModelCaller`'s docstring on not folding an unrecognised value into a
    #: bucket that could be mistaken for success), and providers disagree about
    #: that word: MiniMax on the Responses API says `"completed"`, Anthropic
    #: says `"end_turn"`. Comparing against one vocabulary made `complete`
    #: False on 12 of 12 clean MiniMax runs -- every one of which finished
    #: perfectly (OBS-701).
    #:
    #: Adding `"completed"` to an accepted set would fix those 12 runs and
    #: break again on the next provider. Completion is a fact about this loop's
    #: control flow, which this module owns, not about a string it receives.
    model_finished = False
    tool_failure = False
    investigate_lab_recorded = False
    final_finding: str | None = None
    final_trustworthy: bool | None = None
    final_receipt: InvestigationReceipt | None = None

    def record_limit(key: str, value: Any, observed: Any) -> None:
        # One entry per LIMIT, not per occurrence -- a limit hit five times
        # in one run is still one fact ("this bound was exhausted"), and five
        # near-identical entries would bury that fact rather than state it.
        if key in hit_limits:
            return
        hit_limits.add(key)
        limits_hit.append({"limit": key, "value": value, "observed": observed})

    def refuse(call_id: Any, name: Any, args: Mapping[str, Any], message: str,
               *, fabrication: bool = False) -> None:
        """A call the model asked for and this loop did NOT dispatch.

        Recorded in THREE places, deliberately: the model sees it (an
        `is_error` tool result naming the budget, so it can adapt rather than
        retry blindly), the in-memory `EventRun` carries it, and -- the part
        that is not redundant -- **the ticket carries it too**.

        The ticket is the durable record; an `EventRun` is a return value an
        unattended caller discards. A ticket showing two tool events for a run
        in which the model attempted five calls reads as a well-behaved model,
        which is exactly the absence-is-never-zero defect this project keeps
        finding. `status="refused"` and `dispatched: False` are what separate
        these from a call that ran.

        `model_supplied` is deliberately NOT called `arguments`: on a
        dispatched event `arguments` means "as dispatched", pinned values
        included. Reusing the key for "what the model asked for, which we
        refused" would give one field two meanings on one timeline.
        """

        results.append({"id": call_id, "name": name, "content": message, "is_error": True})
        entry = {"tool": name, "arguments": dict(args), "is_error": True,
                 "detail": message, "skipped": True}
        if fabrication:
            entry["fabrication"] = True
        tool_calls.append(entry)
        ticket.record_tool_event(
            str(name) if name else "(unnamed tool)", status="refused", device=decision.device,
            detail=message,
            extra={"dispatched": False, "model_supplied": dict(args), "fabrication": fabrication},
        )

    while True:
        if iteration >= bounds.max_iterations:
            stopped_because = "max_iterations"
            record_limit("max_iterations", bounds.max_iterations, iteration)
            break
        turn_start = now_fn()
        if turn_start >= deadline:
            stopped_because = "time_budget"
            record_limit("time_budget_s", bounds.time_budget_s, turn_start - start)
            break
        iteration += 1

        try:
            turn = caller(
                system=system_prompt, messages=messages, tools=tools_manifest,
                timeout_s=max(5.0, deadline - turn_start),
            )
        except Exception as exc:  # noqa: BLE001 -- a model-call failure degrades this run, never crashes an unattended handler
            stopped_because = "caller_error"
            ticket.record_model_exchange(
                purpose="event_agent_turn", tools_offered=list(tools_offered),
                response_text="", stop_reason="caller_error", grounding_ok=None,
                extra={"iteration": iteration, "error": str(exc)[:300]},
            )
            exchanges.append({
                "purpose": "event_agent_turn", "iteration": iteration, "response_text": "",
                "stop_reason": "caller_error", "tokens": None,
            })
            break

        text = (turn.get("text") or "") if isinstance(turn, Mapping) else ""
        turn_stop_reason = ((turn.get("stop_reason") if isinstance(turn, Mapping) else None) or "end_turn")
        requested_calls = list(turn.get("tool_calls") or []) if isinstance(turn, Mapping) else []
        tokens = turn.get("tokens") if isinstance(turn, Mapping) else None

        exchange: dict[str, Any] = {
            "purpose": "event_agent_turn", "iteration": iteration, "response_text": text,
            "stop_reason": turn_stop_reason, "tokens": tokens,
        }
        exchange_extra: dict[str, Any] = {"iteration": iteration}
        if requested_calls:
            requested = [
                {"tool": c.get("name"), "arguments": c.get("arguments")}
                for c in requested_calls if isinstance(c, Mapping)
            ]
            exchange["tool_calls_requested"] = requested
            exchange_extra["tool_calls_requested"] = requested
        exchanges.append(exchange)

        # grounding_ok=None, ALWAYS -- there is no DescentResult for
        # free-form loop prose, and record_model_exchange's own docstring
        # states recording False would misrepresent "never checked" as
        # "checked and failed".
        ticket.record_model_exchange(
            purpose="event_agent_turn", tools_offered=list(tools_offered), response_text=text,
            stop_reason=turn_stop_reason, tokens=tokens, grounding_ok=None, extra=exchange_extra,
        )

        if not requested_calls:
            if not text:
                # OBS-698: no text AND no tool calls -- the model produced
                # nothing at all this turn. This is the actual anomaly; an
                # empty-text TOOL-CALLING turn (handled below, in the
                # dispatch branch) is normal and not this.
                stopped_because = "empty_turn"
            else:
                # The model stopped asking for tools and said something. This
                # is completion, whatever the provider calls it.
                stopped_because = turn_stop_reason
                model_finished = True
            break

        messages.append({"role": "assistant", "text": text, "tool_calls": requested_calls})
        results: list[dict[str, Any]] = []

        for call in requested_calls:
            if not isinstance(call, Mapping):
                continue
            name = call.get("name")
            call_id = call.get("id") or f"{name}-{len(tool_calls)}"
            args = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}

            if total_calls >= bounds.max_tool_calls:
                record_limit("max_tool_calls", bounds.max_tool_calls, total_calls)
                refuse(call_id, name, args,
                       f"not started: max_tool_calls budget exhausted ({bounds.max_tool_calls})")
                continue

            per_tool_cap = bounds.max_calls_per_tool.get(name) if name else None
            if per_tool_cap is not None and calls_per_tool.get(name, 0) >= per_tool_cap:
                record_limit(f"max_calls_per_tool:{name}", per_tool_cap, calls_per_tool.get(name, 0))
                refuse(call_id, name, args,
                       f"not started: {name} already called {per_tool_cap} time(s) "
                       "(max_calls_per_tool budget)")
                continue

            if now_fn() >= deadline:
                record_limit("time_budget_s", bounds.time_budget_s, now_fn() - start)
                refuse(call_id, name, args, "not started: time budget exhausted")
                continue

            offer = offers_by_name.get(name)
            if offer is None:
                refuse(call_id, name, args, f"{name!r} is not an offered tool for this event")
                continue

            activity_sequence += 1
            emit_chat_activity(
                tool_selected_activity(
                    incident_id=ticket.incident_id,
                    event_id=decision.event_id,
                    sequence=activity_sequence,
                    tool=str(name),
                    stage=context.flow,
                )
            )

            try:
                resolved_args = resolve_arguments(offer, args)
            except ArgumentRefusal as exc:
                fabrication_attempts.append({
                    "tool": name, "model_supplied": dict(args), "reason": str(exc),
                })
                refuse(call_id, name, args, str(exc), fabrication=True)
                continue

            activity_sequence += 1
            emit_chat_activity(
                command_preview_activity(
                    incident_id=ticket.incident_id,
                    event_id=decision.event_id,
                    sequence=activity_sequence,
                    tool=str(name),
                    device=context.device,
                    command_label=_chat_command_label(str(name), resolved_args),
                )
            )

            total_calls += 1
            calls_per_tool[name] = calls_per_tool.get(name, 0) + 1
            remaining = max(5.0, deadline - now_fn())
            try:
                call_result: ToolCallResult = toolset.call_tool(
                    name, resolved_args, timeout_s=remaining
                )
            except Exception as exc:  # noqa: BLE001 -- tool failures are event data, not loop crashes
                tool_failure = True
                call_result = ToolCallResult(
                    name=name,
                    arguments=resolved_args,
                    text=f"tool call failed: {exc}",
                    is_error=True,
                    elapsed_s=0.0,
                    truncated_chars=None,
                )

            is_error, detail = _classify_call_result(call_result)
            content_text = call_result.text
            if len(content_text) > bounds.max_result_chars:
                record_limit("max_result_chars", bounds.max_result_chars, len(content_text))
                note = (
                    f"[event_agent max_result_chars budget exceeded: "
                    f"{len(call_result.text)} chars, kept {bounds.max_result_chars}]"
                )
                content_text = content_text[: bounds.max_result_chars] + "\n..." + note
                detail = f"{detail}; {note}" if detail else note
                is_error = True  # never a silent drop -- the model must see this and adapt

            results.append({"id": call_id, "name": name, "content": content_text, "is_error": is_error})
            tool_calls.append({
                "tool": name, "arguments": resolved_args, "is_error": is_error, "detail": detail,
                "duration_ms": call_result.elapsed_s * 1000, "truncated_chars": call_result.truncated_chars,
                "skipped": False,
            })
            ticket.record_tool_event(
                name, status=("error" if is_error else "success"), device=decision.device,
                duration_ms=call_result.elapsed_s * 1000, detail=detail,
                extra={"arguments": resolved_args, "truncated_chars": call_result.truncated_chars},
            )
            activity_sequence += 1
            emit_chat_activity(
                tool_result_activity(
                    incident_id=ticket.incident_id,
                    event_id=decision.event_id,
                    sequence=activity_sequence,
                    tool=str(name),
                    device=context.device,
                    is_error=is_error,
                    duration_ms=round(call_result.elapsed_s * 1000),
                )
            )

            if name == "investigate_lab" and not is_error and not investigate_lab_recorded:
                payload = _safe_json(call_result.text)
                if isinstance(payload, Mapping):
                    try:
                        receipt = InvestigationReceipt.parse(payload)
                    except ReceiptValidationError as exc:
                        is_error = True
                        tool_calls[-1]["is_error"] = True
                        tool_calls[-1]["detail"] = f"invalid deterministic investigation receipt: {exc}"
                        ticket.record_tool_event(
                            name, status="error", device=decision.device,
                            detail=tool_calls[-1]["detail"],
                            extra={"receipt_refused": True},
                        )
                        results[-1]["is_error"] = True
                        results[-1]["content"] = tool_calls[-1]["detail"]
                    else:
                        ticket.record_answer(
                            receipt.finding,
                            trustworthy=receipt.trustworthy,
                            cause=receipt.cause,
                            coherence=receipt.coherence,
                            report_status=receipt.report_status,
                            correlation_status=receipt.correlation_status,
                            extra=receipt.ticket_extra(),
                        )
                        investigate_lab_recorded = True
                        final_finding = receipt.finding
                        final_trustworthy = receipt.trustworthy
                        final_receipt = receipt
                        _record_receipt_shadows(event_store, decision.event_id, receipt)
                        if coordinators:
                            if event_notification.reasoning_tail_enabled():
                                coverage_complete = (
                                    receipt.coverage.get("complete")
                                    if receipt.coverage is not None
                                    and isinstance(receipt.coverage.get("complete"), bool)
                                    else None
                                )
                                activity_sequence += 1
                                emit_chat_activity(
                                    visible_reasoning_tail_activity(
                                        incident_id=ticket.incident_id,
                                        event_id=decision.event_id,
                                        sequence=activity_sequence,
                                        finding=receipt.finding,
                                        trustworthy=receipt.trustworthy,
                                        coverage_complete=coverage_complete,
                                    )
                                )
                            receipt_activities = activities_for_receipt(
                                receipt,
                                incident_id=ticket.incident_id,
                                event_id=decision.event_id,
                                sequence_start=activity_sequence,
                            )
                            for activity in receipt_activities:
                                for coordinator in coordinators:
                                    event_notification.deliver_live_card(event_store, coordinator.apply(activity))
                            activity_sequence = receipt_activities[-1].sequence
                        else:
                            event_notification.deterministic_drill(notification, receipt)
                # A dispatched, non-error investigate_lab call whose payload
                # does NOT carry finding/trustworthy is NOT recorded as an
                # answer either -- absence is never zero: an unrecognisable
                # payload is not a finding, and a ticket must not be made to
                # look like one concluded when it did not.

        messages.append({"role": "tool_results", "results": results})

    elapsed_s = now_fn() - start
    try:
        ticket.close()
    except Exception as exc:  # noqa: BLE001 -- a terminal write failure must leave the event retryable
        stopped_because = "ticket_close_failed"
        model_finished = False
        if notification is not None:
            event_notification.close(notification, outcome="needs human review")
        return EventRun(
            ran=True,
            reason=f"dispatched against {mode} tools; ticket close failed: {exc}",
            mode=mode,
            decision=decision.as_dict(),
            bounds=bounds.as_dict(),
            tools_offered=tools_offered,
            tool_calls=tuple(tool_calls),
            exchanges=tuple(exchanges),
            stopped_because=stopped_because,
            complete=False,
            limits_hit=tuple(limits_hit),
            fabrication_attempts=tuple(fabrication_attempts),
            ticket_run_id=ticket.run_id,
            elapsed_s=elapsed_s,
        )
    if coordinators:
        pass  # Receipt activities already emitted the terminal card state.
    elif final_receipt is not None:
        event_notification.final_diagnosis(notification, final_receipt)
    elif final_finding == "no_fault_on_path":
        event_notification.close(notification, outcome="no fault found", finding=final_finding)
    elif final_finding is not None and final_trustworthy:
        event_notification.close(notification, outcome="resolved", finding=final_finding)
    else:
        event_notification.close(notification, outcome="needs human review")

    return EventRun(
        ran=True,
        reason=f"dispatched against {mode} tools; stopped because {stopped_because}",
        mode=mode,
        decision=decision.as_dict(),
        bounds=bounds.as_dict(),
        tools_offered=tools_offered,
        tool_calls=tuple(tool_calls),
        exchanges=tuple(exchanges),
        stopped_because=stopped_because,
        complete=model_finished and not tool_failure,
        limits_hit=tuple(limits_hit),
        fabrication_attempts=tuple(fabrication_attempts),
        ticket_run_id=ticket.run_id,
        elapsed_s=elapsed_s,
    )


def _record_receipt_shadows(
    store: EventStore | None,
    event_id: str,
    receipt: InvestigationReceipt,
) -> None:
    """Mirror deterministic receipt metadata without changing event authority."""

    if store is None:
        return
    try:
        shadow = receipt.narrowing_shadow
        if shadow is not None and shadow.get("mode") == "shadow" and shadow.get("active") is False:
            candidates = shadow.get("candidates")
            decision = shadow.get("decision")
            refusal = shadow.get("refusal")
            if (
                isinstance(candidates, list)
                and all(isinstance(candidate, dict) for candidate in candidates)
                and (decision is None or isinstance(decision, dict))
                and (refusal is None or isinstance(refusal, str))
            ):
                store.record_narrowing_shadow(
                    event_id=event_id,
                    mode="shadow",
                    candidates=tuple(candidates),
                    decision=decision,
                    refusal=refusal,
                )
        if receipt.trustworthy and receipt.cause is not None:
            cause_rung = receipt.cause.get("rung")
            cause_device = receipt.cause.get("device")
            if isinstance(cause_rung, str) and isinstance(cause_device, str):
                store.create_or_join_incident(
                    identity=IncidentIdentity(cause_device, cause_rung, receipt.subject),
                    event_id=event_id,
                )
    except (EventStoreError, ValueError):
        pass


def _chat_command_label(tool: str, arguments: Mapping[str, Any]) -> str:
    """A safe operator label from resolved approved arguments, never raw CLI."""

    if tool == "investigate_lab":
        return f"deterministic {arguments.get('flow', 'network')} investigation"
    if tool == "explore_lab":
        return "inventory and health context"
    if tool == "check_lab":
        return f"{arguments.get('intent', 'network')} status check"
    return f"approved {tool} check"
