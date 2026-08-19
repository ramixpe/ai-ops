"""MCP server exposing approved read-only IOS-XR network tools."""

from __future__ import annotations

import concurrent.futures
import functools
import inspect
import json
import logging
import os
import sys
from typing import Any, Callable

from dotenv import find_dotenv, load_dotenv

from .boundary import sanitize

try:
    # MCP SDK >= 2.0 renamed the high-level server to MCPServer.
    from mcp.server.mcpserver import MCPServer as FastMCP
except ModuleNotFoundError:
    # MCP SDK < 2.0 exposed it as FastMCP.
    from mcp.server.fastmcp import FastMCP

from agent_nettools.health import evaluate_fabric
from agent_nettools.inventory_model import load_inventory_file
from agent_nettools.investigation import investigate
from agent_nettools.llm_analysis import TROUBLESHOOTING_PROMPT
from agent_nettools.network_tools import (
    check_bgp_neighbors,
    check_fabric,
    check_interfaces,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    detect_flaps,
    diff_evidence,
    get_bgp_neighbor,
    get_device_facts,
    get_interface,
    get_logging,
    get_route,
    list_devices,
    load_golden_snapshot,
    load_latest_snapshot,
    ping_device,
    traceroute_device,
)

# MCP clients (and `make mcp` / `nettools-mcp`) launch this server directly, so it
# has to load .env itself — otherwise every tool fails on a missing
# DEVICE_USERNAME / DEVICE_PASSWORD. Two lookups so it works both from the
# current directory upward and next to an editable install; a no-op in Docker,
# where the credentials arrive via -e / --env-file.
load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

mcp = FastMCP("IOS-XR Read-Only Network Tools")

# --------------------------------------------------------------------------- #
# readOnlyHint annotations: every tool this server exposes is read-only (see
# CLAUDE.md, "The safety boundary") -- ToolAnnotations.read_only_hint is how a
# client acts on that guarantee (e.g. auto-approving calls) without having to
# inspect this file. The installed MCP SDK's `tool()` decorator is checked for
# an `annotations=` parameter at import time, rather than assumed, because an
# older SDK's decorator does not accept that keyword at all -- passing it
# would be a TypeError on every single tool registration, which would take
# the whole server down rather than just omitting a nice-to-have annotation.
#
# B-473 (expert review P1-03): read-only is not the whole story for
# `get_lab_ping`/`get_lab_traceroute` -- they change no device state (so
# `read_only_hint=True` is still true and is not weakened below), but they
# generate ICMP/UDP traffic toward a caller-supplied address, which a passive
# `show` read never does. A client that auto-approves on `readOnlyHint` alone
# cannot tell the two kinds apart from that annotation. `_active_probe_tool`
# below also sets `open_world_hint=True` (probe traffic leaves the device
# toward the wider network, not a fixed internal set of endpoints -- the MCP
# spec's own meaning for that hint) and an annotation `title` naming what it
# is, and the docstring is prefixed the same way for a client that only reads
# descriptions.
#
# This is signalling, not enforcement. `NETTOOLS_ALLOW_ACTIVE_PROBES`
# (network_tools.py) is what actually refuses to send the traffic; these
# annotations are hints a client is free to ignore, same as `read_only_hint`
# always was -- they widen what a client *can* know without inspecting this
# file, not what the server *allows*.
#
# B-493 (2026-08-18, MCP-EXPERIMENT.md 12.3): that gap was not hypothetical.
# A 31B model followed a clean `investigate_lab_session` descent with an
# UNPROMPTED `get_lab_ping` -- the first time a model generated traffic on
# this fabric without being asked. The engineering was sound (the flow it
# had just run covers the control plane; "reach" can mean the data plane),
# and nothing on this surface refused it, because `NETTOOLS_ALLOW_ACTIVE_PROBES`
# defaults to enabled and the annotations above are exactly what this
# comment already said they are: hints, not a gate. **Initiative scales
# with capability** -- the model most likely to start probing when told
# "no fault on this path" during an incident is the capable one.
#
# `_mcp_active_probes_allowed`/`NETTOOLS_MCP_ALLOW_ACTIVE_PROBES` below is
# the enforcement this comment predicted was missing. It is a second,
# MCP-only gate, deliberately opposite-by-default from the CLI's: a human
# typing `nettools ping` asked for the probe explicitly; an MCP client is a
# model deciding to generate traffic on its own. It does not touch
# `NETTOOLS_ALLOW_ACTIVE_PROBES` (still checked, further down, inside
# `run_template`) -- both must allow a probe for one to run.
# --------------------------------------------------------------------------- #

# B-493: NETTOOLS_MCP_ALLOW_ACTIVE_PROBES, default OFF. See settings.py's
# entry for the full rationale; unlike NETTOOLS_ALLOW_ACTIVE_PROBES this
# fails CLOSED on an unrecognized value -- a gate whose entire purpose is to
# keep something off by default must not reopen on a typo (that footgun is
# exactly what settings.py's own P2-02 finding is about).
NETTOOLS_MCP_ALLOW_ACTIVE_PROBES_ENV = "NETTOOLS_MCP_ALLOW_ACTIVE_PROBES"
_MCP_ACTIVE_PROBES_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _mcp_active_probes_allowed() -> bool:
    value = os.getenv(NETTOOLS_MCP_ALLOW_ACTIVE_PROBES_ENV, "0").strip().lower()
    return value in _MCP_ACTIVE_PROBES_TRUTHY


def _active_probes_refused(tool_name: str, call_args: tuple, call_kwargs: dict) -> dict:
    """The envelope returned in place of calling an active-probe tool.

    Shaped like `network_tools._safe_error`'s own envelopes (`status: "error"`,
    the reason under `errors`) on purpose: `boundary.sanitize` -> `_classify_errors`
    walks every `errors` list the same way regardless of which function built
    it, and the whole point of this refusal is that it is CLASSIFIED, not
    withheld -- see boundary.ERROR_KINDS' "active probes (ping/traceroute) are
    disabled" / "active probes are disabled" entries, which this message
    matches deliberately (never a silent no-op, and never "an unclassified
    error").

    Every current active-probe tool (`get_lab_ping`, `get_lab_traceroute`,
    `probe_lab`) takes `device_name` first, positionally or by keyword, so
    it is read the same way here rather than duplicated per tool.
    """

    device_name = call_kwargs.get("device_name")
    if device_name is None and call_args:
        device_name = call_args[0]

    return {
        "tool": tool_name,
        "device": device_name,
        "status": "error",
        "data": {},
        "errors": [
            f"{tool_name}: Active probes (ping/traceroute) are disabled for "
            "the MCP surface: NETTOOLS_MCP_ALLOW_ACTIVE_PROBES is not set to "
            "a truthy value. Set NETTOOLS_MCP_ALLOW_ACTIVE_PROBES=true (or 1) "
            "to allow an MCP client to generate ping/traceroute traffic; this "
            "is independent of NETTOOLS_ALLOW_ACTIVE_PROBES, which still "
            "gates the CLI and every other caller, and defaults to enabled."
        ],
    }


_TOOL_ACCEPTS_ANNOTATIONS = "annotations" in inspect.signature(FastMCP.tool).parameters

try:
    from mcp.types import ToolAnnotations

    READ_ONLY_HINT: Any | None = ToolAnnotations(read_only_hint=True) if _TOOL_ACCEPTS_ANNOTATIONS else None

    # open_world_hint is a newer field than read_only_hint; an SDK old enough to
    # lack it would raise on construction. Checked via inspect.signature the
    # same way `_TOOL_ACCEPTS_ANNOTATIONS` is checked above, rather than
    # assumed -- pydantic's generated signature reports fields by their alias
    # (camelCase), not the snake_case constructor kwarg, so both spellings are
    # checked rather than guessing which one this SDK version uses.
    _annotation_fields = set(inspect.signature(ToolAnnotations).parameters)
    _ANNOTATIONS_ACCEPT_OPEN_WORLD = _TOOL_ACCEPTS_ANNOTATIONS and bool(
        _annotation_fields & {"open_world_hint", "openWorldHint"}
    )

    _active_probe_kwargs: dict[str, Any] = {
        "read_only_hint": True,
        "title": "ACTIVE PROBE — generates network traffic",
    }
    if _ANNOTATIONS_ACCEPT_OPEN_WORLD:
        _active_probe_kwargs["open_world_hint"] = True

    ACTIVE_PROBE_HINT: Any | None = (
        ToolAnnotations(**_active_probe_kwargs) if _TOOL_ACCEPTS_ANNOTATIONS else None
    )
except ImportError:
    READ_ONLY_HINT = None
    ACTIVE_PROBE_HINT = None
    _ANNOTATIONS_ACCEPT_OPEN_WORLD = False

# What actually happened, so a caller/reviewer can tell without re-deriving it
# from the checks above.
READ_ONLY_ANNOTATIONS_SUPPORTED = READ_ONLY_HINT is not None
ACTIVE_PROBE_ANNOTATIONS_SUPPORTED = ACTIVE_PROBE_HINT is not None


def _register_sanitized_tool(
    annotations: Any | None, *args: Any, active_probe: bool = False, **kwargs: Any
) -> Callable[[Callable], Callable]:
    """The shared body of `_read_only_tool` and `_active_probe_tool`.

    Registration and **the raw-text boundary** are identical for both -- the
    only thing that may ever legitimately differ between a passive read and an
    active probe is the annotations describing it (B-473), and the sanitisation
    guarantee below must hold for both regardless. Written once here and called
    by both wrappers, rather than duplicated, so that property cannot drift
    between them by one being edited and not the other.

    **Every return value passes through `boundary.sanitize`.** That is here, in
    the registration decorator, rather than in each tool or behind an argument,
    because both of those make invariant 4 depend on someone remembering -- and
    on this surface the caller is a model. Registering a tool *is* sanitising
    it, so a tool added later inherits the guarantee with no diff to this file.

    Audited 2026-08-17: 14 of 20 tools returned raw device output under
    `data.commands`, up to 37,962 characters. See `boundary.py`.

    **`active_probe=True` (B-493) is the same move applied to enforcement, not
    just sanitisation.** `_active_probe_tool` passes it; `_read_only_tool` never
    does. When set, the wrapped function is not called at all unless
    `_mcp_active_probes_allowed()` says so -- checked here, in the one place
    every active-probe tool registers, rather than inside each tool body, for
    the same reason sanitisation lives here: a check a tool author has to
    remember to add is a check a future tool will ship without.
    """

    if annotations is not None:
        kwargs.setdefault("annotations", annotations)
    register = mcp.tool(*args, **kwargs)

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def sanitized(*call_args: Any, **call_kwargs: Any) -> Any:
            if active_probe and not _mcp_active_probes_allowed():
                # Fails CLOSED, and SAYS SO: `function` (ping_device/
                # traceroute_device/probe_lab) is never called, so no traffic
                # is generated -- but the caller gets a classified, structured
                # reason, not a silent no-op and not an unclassified error.
                return sanitize(
                    _active_probes_refused(function.__name__, call_args, call_kwargs)
                )
            return sanitize(function(*call_args, **call_kwargs))

        # Registered under the *wrapped* function, so there is no route to the
        # unsanitised one through the MCP protocol. `functools.wraps` keeps the
        # name, docstring and signature the SDK builds the tool schema from.
        register(sanitized)
        return sanitized

    return decorate


def _read_only_tool(*args: Any, **kwargs: Any) -> Callable[[Callable], Callable]:
    """``@mcp.tool()``, plus ``readOnlyHint``, plus **the raw-text boundary**.

    Every passive-read tool below uses this instead of the bare ``@mcp.tool()``
    decorator. Degrades silently (no annotation, no error) on an SDK old enough
    to lack ``ToolAnnotations``/the ``annotations=`` keyword -- see
    ``READ_ONLY_ANNOTATIONS_SUPPORTED`` for which path this process took.

    See `_register_sanitized_tool` for what registering through this actually
    does; this only supplies which annotations to register with.
    """

    return _register_sanitized_tool(READ_ONLY_HINT, *args, **kwargs)


def _active_probe_tool(*args: Any, **kwargs: Any) -> Callable[[Callable], Callable]:
    """``_read_only_tool``'s twin for tools that generate network traffic (B-473).

    Used by `get_lab_ping`/`get_lab_traceroute` (classic surface) and
    `probe_lab` (staged surface, via `staged_surface.apply`'s
    `register_probe = server_module._active_probe_tool`). Same registration,
    same sanitisation boundary (`_register_sanitized_tool`) -- annotated with
    `ACTIVE_PROBE_HINT` instead of `READ_ONLY_HINT` so a client reading
    annotations, not just descriptions, can tell these apart from a passive
    read. See the module comment above for why the annotation is a hint, not
    the enforcement mechanism.

    ``active_probe=True`` (B-493) IS the enforcement mechanism: every tool
    registered through this function is gated by
    `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`, by construction -- a tool added here
    later inherits the gate with no diff to this file, the same guarantee
    `_register_sanitized_tool` already gives the sanitisation boundary.
    """

    return _register_sanitized_tool(ACTIVE_PROBE_HINT, *args, active_probe=True, **kwargs)


@_read_only_tool()
def list_lab_devices() -> dict:
    """Answers: *what devices exist, and what are they called?*

    Prefer this first when a question names a device you have not seen, or
    names none at all. Every other tool takes a device name, and they are exact
    -- "PE2", not "pe2" or "PE-2". Returns names, management addresses, roles
    and sites; no device is contacted.
    """

    return list_devices()


@_read_only_tool()
def get_lab_device_facts(device_name: str) -> dict:
    """Answers: *what is this device, and is it up?*

    Prefer this to confirm a device is reachable and identify what it is running
    before interpreting anything else about it. It is the cheapest tool that
    touches a device. It will not tell you whether anything is wrong -- use
    `assess_lab_device_health` for that.
    """

    return get_device_facts(device_name)


@_read_only_tool()
def check_lab_interfaces(device_name: str) -> dict:
    """Answers: *which of this device's interfaces are up, and which are not?*

    Prefer this when the question is about physical connectivity on one device,
    or to find which interface to ask about in detail. For one named interface,
    `get_lab_interface` returns error counters and flap history this does not.

    Note a line-down subinterface is normal on this fabric and is not a path
    fault.
    """

    return check_interfaces(device_name)


@_read_only_tool()
def check_lab_bgp_neighbors(device_name: str) -> dict:
    """Answers: *which BGP sessions does this device have, and what state are they in?*

    Prefer this to find out **that** a session is down, and which peer address
    it belongs to. It will not tell you **why**.

    If the question is why a session is down, prefer `investigate_lab_session`
    -- it walks the layers beneath the session and reports which one broke.
    """

    return check_bgp_neighbors(device_name)


@_read_only_tool()
def check_lab_lldp_neighbors(device_name: str) -> dict:
    """Answers: *what is physically cabled to this device, and to which port?*

    Prefer this to establish topology -- which neighbour sits on which
    interface -- rather than to diagnose a fault. LLDP reports what a neighbour
    calls itself, which on this fabric is not always its inventory name, so
    treat a mismatch as a naming difference before treating it as an anomaly.
    """

    return check_lldp_neighbors(device_name)


@_read_only_tool()
def check_lab_isis_neighbors(device_name: str) -> dict:
    """Answers: *does this device have IGP adjacencies, and to whom?*

    Prefer this when a device appears unreachable at a higher layer: no IS-IS
    adjacencies means no route to it, which means every BGP session to its
    loopback will fail regardless of BGP configuration. Zero adjacencies on a
    router that should have them is isolation, not a BGP problem.
    """

    return check_isis_neighbors(device_name)


@_read_only_tool()
def check_lab_sr_policies(device_name: str) -> dict:
    """Answers: *what SR-TE policies exist on this device, and are they up?*

    Prefer this only when the question is about traffic engineering. An SR-TE
    policy being down does not by itself explain a BGP session failure or an
    unreachable loopback -- check the IGP and the interfaces first.
    """

    return check_sr_policies(device_name)


@_read_only_tool()
def check_lab_fabric(check: str = "bgp") -> dict:
    """Answers: *how does one thing look across every device at once?*

    Prefer this when the question is fabric-wide -- "are all BGP sessions up?",
    "is anything isolated?" -- rather than about one device. `check` selects
    what to run: facts, interfaces, bgp, lldp, isis or sr.

    It is the most expensive tool here: it contacts every device in the
    inventory, and on this fabric consecutive logins are slow. Prefer a
    single-device tool when you already know the device.
    """

    return check_fabric(check)


@_read_only_tool()
def collect_lab_evidence(device_name: str) -> dict:
    """Answers: *everything the read-only tools can say about one device.*

    Prefer this when you need several kinds of state from the same device and
    would otherwise call three or four tools -- it collects them in one login,
    which on this fabric is much faster than the separate calls.

    Prefer a specific tool when you know what you are looking for. This returns
    a lot, and more evidence is not more diagnosis: if the question is why
    something is broken, `investigate_lab_session` answers it directly.
    """

    return collect_evidence(device_name)


@_read_only_tool()
def get_lab_route(device_name: str, prefix: str) -> dict:
    """Answers: *does this device have a route to that destination, and via where?*

    Prefer this when a peer or host is unreachable and you need to know whether
    the local device even knows how to reach it. No route to a peer's loopback
    means every session to it will fail, whatever the session configuration
    says.

    ``prefix`` is an IPv4 address or CIDR prefix, e.g. "10.255.0.31" or
    "10.0.0.0/24". It is parsed and the command rebuilt from its canonical form,
    never passed through as text, so anything malformed is refused before any
    connection is made.
    """

    return get_route(device_name, prefix)


@_read_only_tool()
def get_lab_bgp_neighbor(device_name: str, address: str) -> dict:
    """Answers: *what does this device say about one specific BGP peer?*

    Prefer this over `check_lab_bgp_neighbors` when you already know which peer
    is wrong and want the detail -- the reason the session last reset, whether a
    TCP socket exists, the negotiated timers and address families.

    If the question is *why* the session is down rather than *what the peer
    record says*, prefer `investigate_lab_session`: it reads this same detail
    and also checks the route, the IGP and the interfaces beneath it.

    ``address`` is a plain IPv4 address, e.g. "10.255.0.31".
    """

    return get_bgp_neighbor(device_name, address)


@_read_only_tool()
def get_lab_interface(device_name: str, name: str) -> dict:
    """Answers: *what is the detailed state of one named interface?*

    Prefer this over `check_lab_interfaces` when you already know which
    interface matters and need error counters, drop counts, or when it last
    flapped -- a link that is up but flapping does not show as down in a
    summary.

    ``name`` is an interface name as the device spells it, e.g.
    "GigabitEthernet0/0/0/1", "Gi0/0/0/2.300" or "Loopback0". It is validated
    against an anchored charset, so it cannot carry shell or CLI syntax.
    """

    return get_interface(device_name, name)


@_read_only_tool()
def search_lab_knowledge(query: str) -> dict:
    """Answers: *what does this project's own documentation or the operator's
    notes say about X?*

    Prefer this before asking a person: the corpus includes the glossary, the
    design decisions, the security posture, and the inventory's operator-
    authored notes about known local conditions (which have twice held answers
    nobody read -- OBS-139). Plain text search with `path:line` citations; no
    device is contacted. Not a device-state tool -- for state, use the check/
    lookup tools; for "why is X broken", use `investigate_lab_session`.

    This project's own internal evaluation material -- test protocols and
    their expected answers -- is withheld from the corpus and returns no hit,
    the same as a topic nobody has written up. A miss here is not proof a
    topic is undocumented elsewhere in the project.
    """

    from agent_nettools.knowledge import search_knowledge

    return search_knowledge(query)


@_read_only_tool()
def explain_lab_mnemonic(mnemonic: str) -> dict:
    """Answers: *what does this IOS-XR syslog mnemonic mean, what typically
    causes it, and which flow investigates it?*

    Prefer this over reasoning about a mnemonic from its name alone: the
    table is curated and reviewed (knowledge as a declared item, not a
    model's recollection), and its `investigate_with` field names the flow
    that turns the event into a diagnosis. An unknown mnemonic still gets its
    facility/severity/code parts split out. No device is contacted.
    """

    from agent_nettools.knowledge import explain_mnemonic

    return explain_mnemonic(mnemonic)


@_read_only_tool()
def get_lab_logging(device_name: str, count: int = 20) -> dict:
    """Answers: *what did this device report happening, and when?*

    Prefer this to place a fault in time -- to find when a link went down, or
    whether a configuration commit preceded a failure. Prefer it *after* you
    know what you are looking for; the buffer is dominated by routine
    management-session noise, and reading it to discover a fault is far less
    reliable than checking the relevant state directly.

    Raw log text is not returned to you (invariant 4): you get parsed records
    with timestamps, mnemonics and severities. ``count`` is how many recent
    lines to read, 1-500.
    """

    return get_logging(device_name, count)


@_active_probe_tool()
def get_lab_ping(device_name: str, address: str) -> dict:
    """ACTIVE PROBE: sends ICMP/UDP traffic to the target. Answers: *can this device actually reach that address right now?*

    Prefer this to confirm or rule out data-plane reachability once you have a
    hypothesis -- for example after finding a route exists, to check the path
    actually forwards. It generates traffic, unlike every other tool here, so
    prefer a state read when one would answer the question.

    A failed ping tells you the path does not work; it does not tell you which
    layer broke. For that, prefer `investigate_lab_session`.

    ``address`` is a plain IPv4 address.
    """

    return ping_device(device_name, address)


@_active_probe_tool()
def get_lab_traceroute(device_name: str, address: str) -> dict:
    """ACTIVE PROBE: sends ICMP/UDP traffic to the target. Answers: *which hops does traffic from this device actually take?*

    Prefer this when reachability fails and you need to know **where** it stops
    -- the last responding hop localises the problem to a segment. Like
    `get_lab_ping` it generates traffic, so prefer a state read when one would
    do.

    ``address`` is a plain IPv4 address.
    """

    return traceroute_device(device_name, address)


# --------------------------------------------------------------------------- #
# Phase 8: MCP parity for snapshots, diffing, health, and flap detection --
# through Phase 7 these were CLI-only, so the MCP client (an LLM, the primary
# consumer of this server) could not do drift detection or get a health
# verdict at all. Every tool below is a thin wrapper over an existing,
# already-safe network_tools.py/health.py function -- no new device access
# path, exactly like every tool above.
# --------------------------------------------------------------------------- #


def _diff_against(tool_name: str, device_name: str, previous: dict | None) -> dict:
    """Shared shape for both diff tools: collect fresh evidence, save it, diff if possible.

    Mirrors ``nettools diff DEVICE [--against golden|latest]`` **except that it
    does not persist the fresh collection** (B-438).

    The CLI saves it, deliberately, so a human's diff advances the baseline. On
    this surface the caller is a model, and appending to snapshot history is a
    persistent write: the history is what ``detect_lab_flaps`` reads, so a model
    calling diff in a loop reshapes the evidence a later flap analysis sees.
    Reviewer A, ``peer-review-response.md`` §3.1 --
    *"the architecture protects the managed network more carefully than it
    protects its own source of truth."*

    The consequence is a **stable** baseline here rather than a moving one:
    repeated calls compare against the same saved snapshot. For a read-only
    surface that is the better semantics anyway.
    """

    current = collect_evidence(device_name)
    result: dict[str, Any] = {
        "tool": tool_name,
        "device": device_name,
        "status": "success",
        "data": {"snapshot_path": None, "has_previous": previous is not None, "diff": None},
        "errors": [],
    }
    if previous is not None:
        result["data"]["diff"] = diff_evidence(previous, current)
    return result


@_read_only_tool()
def diff_lab_device_against_latest(device_name: str) -> dict:
    """Answers: *what changed on this device since it was last looked at?*

    Prefer this when a fault is new and you want to know what moved, rather than
    what is currently wrong -- a change is a much stronger lead than a state.
    It compares against the previous collection, so it answers "since last
    time", which may be minutes or weeks ago.

    ``data.has_previous`` is ``false`` the first time this runs for a device:
    nothing to compare against yet, not an error.
    """

    return _diff_against(
        "diff_lab_device_against_latest", device_name, load_latest_snapshot(device_name)
    )


@_read_only_tool()
def diff_lab_device_against_golden(device_name: str) -> dict:
    """Answers: *how does this device differ from its known-good state?*

    Prefer this over `diff_lab_device_against_latest` when you want drift from a
    state someone deliberately declared correct, rather than from whatever was
    collected last. Useful after a maintenance window, or when "it used to work"
    is the only description of the problem available.

    ``data.has_previous`` is ``false`` when no golden snapshot was ever pinned
    for this device.
    """

    return _diff_against(
        "diff_lab_device_against_golden", device_name, load_golden_snapshot(device_name)
    )


@_read_only_tool()
def assess_lab_device_health(device_name: str) -> dict:
    """Answers: *is anything wrong with this device, by rules rather than judgement?*

    Prefer this over reading raw state yourself when the question is "is this
    device healthy?". It applies deterministic rules -- role invariants and
    drift from a recorded baseline -- and returns a severity
    (``ok``/``info``/``warning``/``critical``) with the findings behind it. No
    model is involved, so the verdict does not vary between calls.

    It tells you *that* something is wrong, and which rule fired. If you then
    need to know *why* a session is down, prefer `investigate_lab_session`.
    """

    evidence = collect_evidence(device_name)
    result = evaluate_fabric({device_name: evidence})
    verdict = result["devices"].get(device_name)
    if verdict is None:
        return {
            "tool": "assess_lab_device_health",
            "device": device_name,
            "status": "error",
            "data": {},
            "errors": [f"{device_name} is not in the lab inventory."],
        }
    return verdict


@_read_only_tool()
def assess_lab_fabric_health() -> dict:
    """Answers: *is anything wrong anywhere, and where should I look first?*

    Prefer this as an opening move when the question is broad -- "is the fabric
    healthy?", "anything wrong today?" -- because it returns a per-device
    severity you can use to choose where to go next, instead of guessing a
    device.

    Same deterministic rules as `assess_lab_device_health`, rolled up to one
    fabric severity (the worst device's). It contacts every device, so it is
    slow on this fabric; prefer the single-device version when you already know
    which device matters.
    """

    listed = list_devices()
    if listed.get("status") != "success":
        return listed
    names = [device["name"] for device in listed["data"]["devices"]]

    # B-475/P1-08: this was `{name: collect_evidence(name) for name in names}`
    # -- nine sequential logins end to end, each one paying this fabric's own
    # connect latency in series. A bounded pool overlaps them instead, the same
    # move `network_tools._iter_check_results` already makes for every
    # `check_lab_fabric` call; the worker-count clamp is copied from there
    # (`max(1, min(max_workers, len(devices)))`) so this never opens more
    # sockets than there are devices to open them to, and never a zero-worker
    # pool when the inventory is empty. Futures are submitted in inventory
    # order and collected in that same order (not completion order), so the
    # resulting dict's key order matches `names` regardless of which device
    # answers first -- callers that rely on inventory-ordered output (like
    # `check_fabric` does for its own parallel runner) get it here too.
    #
    # This closes the one serial path on the MCP surface. `assess_lab_device_health`,
    # the diff tools, and `check_lab_fabric` each still open their own
    # sequential or independent set of connections; a single scheduler shared
    # across every fabric-wide tool on this surface is future work, not this
    # change.
    workers = max(1, min(8, len(names)))
    evidence_by_device: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {name: pool.submit(collect_evidence, name) for name in names}
        for name in names:
            evidence_by_device[name] = futures[name].result()
    return evaluate_fabric(evidence_by_device)


@_read_only_tool()
def detect_lab_flaps(device_name: str, min_transitions: int = 3) -> dict:
    """Answers: *has anything been unstable over time, rather than wrong right now?*

    Prefer this when a problem is intermittent, or when someone reports trouble
    that is not visible when you look. A peer that bounced up/down/up looks
    clean in every single pairwise diff and in every current-state read -- this
    reads the device's *entire* saved snapshot history instead, which is the
    only view that shows the pattern.

    Requires prior snapshots. With none saved, ``data.flapping`` is simply
    empty -- that means "no history", not "nothing flapped".
    """

    return detect_flaps(device_name, min_transitions=min_transitions)


# --------------------------------------------------------------------------- #
# MCP resources: let a client ground itself in the inventory and the expected
# topology without spending a tool call on it. Both are read directly off the
# credential-free inventory layers (`inventory_model.py`/`network_tools.list_devices`),
# so exposing them adds no new device-access path either.
# --------------------------------------------------------------------------- #


@mcp.resource(
    "lab://inventory",
    name="lab_inventory",
    description="The lab's device inventory (name, hostname, platform) -- no credentials.",
    mime_type="application/json",
)
def lab_inventory_resource() -> str:
    """Same data as the ``list_lab_devices`` tool, as a resource instead of a tool call."""

    return json.dumps(list_devices(), indent=2)


@mcp.resource(
    "lab://topology/expected",
    name="lab_expected_topology",
    description=(
        "Each device's derived expected topology counts (isis_adjacencies, bgp_peers) "
        "from inventory/lab.yaml -- see `nettools learn-topology`."
    ),
    mime_type="application/json",
)
def lab_expected_topology_resource() -> str:
    """Expected per-device topology counts, keyed by device name.

    A device's value is ``null`` if no baseline has ever been derived for it,
    never a fabricated zero -- ``isis_adjacencies``/``bgp_peers`` are absent
    for a device with no active BGP process, matching ``Expected``'s own
    "absent, not zero" contract (see ``inventory_model.py``).
    """

    inventory = load_inventory_file()
    payload = {
        device.name: (device.expected.model_dump(exclude_none=True) if device.expected else None)
        for device in inventory.devices
    }
    return json.dumps(payload, indent=2)


# --------------------------------------------------------------------------- #
# MCP prompt: the same troubleshooting framing `nettools analyze` sends to
# whichever LLM provider is configured, exposed so an MCP client can reuse it
# directly instead of inventing its own analysis prompt from scratch.
# --------------------------------------------------------------------------- #


@mcp.prompt(
    name="troubleshooting_prompt",
    description="This project's own network-troubleshooting system prompt (see llm_analysis.py).",
)
def troubleshooting_prompt() -> str:
    return TROUBLESHOOTING_PROMPT


@_read_only_tool()
def investigate_lab_session(
    device: str, subject: str, flow: str = "bgp_session"
) -> dict:
    """Answers: *why is this broken?* -- by walking the layers beneath the symptom.

    **Prefer this over calling the individual check tools yourself** whenever the
    question is why something is failing rather than what its state is. It walks the layers beneath a symptom in
    order -- session, transport, route, IGP adjacency, physical interface -- and
    reports the *lowest* broken one as the cause, with the broken layers above
    it as the causal chain that explains the symptom. Every verdict comes from
    code comparing parsed fields, with no model involved.

    ``device``  the device to investigate *from*, e.g. "RR1".
    ``subject`` what to investigate, in the flow's own vocabulary. For
                ``bgp_session`` that is the peer's IPv4 address as
                ``show bgp summary`` lists it, e.g. "10.255.0.12".
    ``flow``    the object type. One of ``bgp_session`` (default),
                ``interface``, ``isis_adjacency`` or ``ldp_session``.
                For ``isis_adjacency`` and ``ldp_session`` the subject is
                the LOCAL interface the adjacency forms over, e.g.
                "GigabitEthernet0/0/0/2" -- not the neighbour's name.
                ``device_health`` is deliberately NOT a flow: device
                health is an aggregation over independent signals, not a
                dependency descent, so it has no lowest-broken-layer to
                report. Use ``assess_lab_device_health`` for that.

    Read ``finding`` first. Values you will see:

    ``all_layers_healthy``     no fault on the path between these two endpoints.
    ``no_fault_on_path``       the session is fine; broken layers were found that
                               are **not** on the path -- read ``off_path``, and
                               do not report them as the cause of anything.
    ``cause_not_localised``    the symptom is real and every layer beneath it is
                               healthy. Look at configuration and policy.
    ``undetermined``           a layer could not be read, so nothing below it was
                               evaluated. ``reason`` says which.
    ``temporally_incoherent``  the fabric changed while it was being read. These
                               observations do not describe one state; run again.
    otherwise                  the terminal finding for the lowest broken layer,
                               e.g. ``interface_line_down``, ``igp_isolated``.

    ``trustworthy`` is false when the run did not produce an answer you may act
    on -- which is **not** the same as the network being broken. Check it before
    reporting a finding.

    ``coherence.caveat``, when present, must be repeated to the user: the answer
    was read over a window wider than the bound, so it is true at both ends of
    that window rather than throughout it.

    No model is called and no paraphrase is produced. The report is rendered
    from the descent's own typed fields.
    """

    result = investigate(device, subject, flow=flow)
    return result.to_payload()


def protect_stdio() -> list[str]:
    """Make sure nothing in this process logs to **stdout**.

    Over stdio transport, stdout carries JSON-RPC and nothing else. A single
    stray line breaks the framing and the client sees a protocol error rather
    than a log message, which is a genuinely confusing failure to debug from the
    other end.

    Nothing in this package writes to stdout -- no ``print``, no
    ``basicConfig``, and `descent.py`'s logger installs no handler. **But the
    process is not only this package.** A host launcher we do not control (an
    IDE wrapper, a desktop client, a shell profile that sets ``PYTHONSTARTUP``)
    can call ``logging.basicConfig()`` before this module is imported, and
    ``basicConfig``'s default stream is ``sys.stderr`` only when it creates the
    handler -- a wrapper that passes ``stream=sys.stdout``, or a library that
    attaches its own, lands on stdout.

    So this redirects any root handler already pointed at stdout to stderr,
    rather than asserting the process is clean. Cheap, and it makes the
    transport robust against a host nobody here configured.

    Returns what it changed, so a caller can log it -- to stderr.
    """

    changed: list[str] = []
    for handler in logging.getLogger().handlers:
        stream = getattr(handler, "stream", None)
        if stream is sys.stdout:
            handler.setStream(sys.stderr)
            changed.append(type(handler).__name__)

    # Anything configured *after* this point still lands correctly: the root
    # logger gets an explicit stderr handler, so `basicConfig` becomes a no-op
    # rather than installing a stdout one of its own.
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stderr))
        changed.append("installed a stderr handler on an unconfigured root logger")

    return changed


# --------------------------------------------------------------------------- #
# Surface selection (B-479). `classic` (default) is everything registered
# above, byte-identical in behaviour to before this flag existed. `staged`
# replaces it with five stage-shaped tools plus a probe -- see
# `staged_surface.py` for why both exist (the §9/§10 A/B must stay
# measurable) and why apply() fails loudly rather than half-applying.
# --------------------------------------------------------------------------- #

NETTOOLS_MCP_SURFACE_ENV = "NETTOOLS_MCP_SURFACE"
ACTIVE_SURFACE = os.getenv(NETTOOLS_MCP_SURFACE_ENV, "classic").strip().lower()

if ACTIVE_SURFACE == "staged":
    from . import staged_surface as _staged

    _staged.apply(sys.modules[__name__])
elif ACTIVE_SURFACE != "classic":
    # A typo (e.g. `stage`) silently fell back to classic -- inconsistent with
    # apply()'s own fail-loud philosophy (2026-08-18 review). Warn; do not
    # crash, since classic is a safe default.
    logging.getLogger(__name__).warning(
        "NETTOOLS_MCP_SURFACE=%r is not 'classic' or 'staged'; using classic",
        ACTIVE_SURFACE,
    )


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""

    for change in protect_stdio():
        print(f"nettools-mcp: redirected {change} away from stdout", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
