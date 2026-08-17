"""MCP server exposing approved read-only IOS-XR network tools."""

from __future__ import annotations

import functools
import inspect
import json
import logging
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
# --------------------------------------------------------------------------- #

_TOOL_ACCEPTS_ANNOTATIONS = "annotations" in inspect.signature(FastMCP.tool).parameters

try:
    from mcp.types import ToolAnnotations

    READ_ONLY_HINT: Any | None = ToolAnnotations(read_only_hint=True) if _TOOL_ACCEPTS_ANNOTATIONS else None
except ImportError:
    READ_ONLY_HINT = None

# What actually happened, so a caller/reviewer can tell without re-deriving it
# from the two checks above.
READ_ONLY_ANNOTATIONS_SUPPORTED = READ_ONLY_HINT is not None


def _read_only_tool(*args: Any, **kwargs: Any) -> Callable[[Callable], Callable]:
    """``@mcp.tool()``, plus ``readOnlyHint``, plus **the raw-text boundary**.

    Every tool below uses this instead of the bare ``@mcp.tool()`` decorator.
    Degrades silently (no annotation, no error) on an SDK old enough to lack
    ``ToolAnnotations``/the ``annotations=`` keyword -- see
    ``READ_ONLY_ANNOTATIONS_SUPPORTED`` for which path this process took.

    **Every return value passes through `boundary.sanitize`.** That is here, in
    the registration decorator, rather than in each tool or behind an argument,
    because both of those make invariant 4 depend on someone remembering -- and
    on this surface the caller is a model. Registering a tool *is* sanitising
    it, so a tool added later inherits the guarantee with no diff to this file.

    Audited 2026-08-17: 14 of 20 tools returned raw device output under
    `data.commands`, up to 37,962 characters. See `boundary.py`.
    """

    if READ_ONLY_HINT is not None:
        kwargs.setdefault("annotations", READ_ONLY_HINT)
    register = mcp.tool(*args, **kwargs)

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def sanitized(*call_args: Any, **call_kwargs: Any) -> Any:
            return sanitize(function(*call_args, **call_kwargs))

        # Registered under the *wrapped* function, so there is no route to the
        # unsanitised one through the MCP protocol. `functools.wraps` keeps the
        # name, docstring and signature the SDK builds the tool schema from.
        register(sanitized)
        return sanitized

    return decorate


@_read_only_tool()
def list_lab_devices() -> dict:
    """List the IOS-XR devices available in the lab inventory."""

    return list_devices()


@_read_only_tool()
def get_lab_device_facts(device_name: str) -> dict:
    """Collect basic read-only facts from a lab device."""

    return get_device_facts(device_name)


@_read_only_tool()
def check_lab_interfaces(device_name: str) -> dict:
    """Collect read-only interface status from a lab device."""

    return check_interfaces(device_name)


@_read_only_tool()
def check_lab_bgp_neighbors(device_name: str) -> dict:
    """Collect read-only BGP neighbor state from a lab device."""

    return check_bgp_neighbors(device_name)


@_read_only_tool()
def check_lab_lldp_neighbors(device_name: str) -> dict:
    """Collect read-only LLDP neighbor state from a lab device."""

    return check_lldp_neighbors(device_name)


@_read_only_tool()
def check_lab_isis_neighbors(device_name: str) -> dict:
    """Collect read-only IS-IS neighbor state from a lab device."""

    return check_isis_neighbors(device_name)


@_read_only_tool()
def check_lab_sr_policies(device_name: str) -> dict:
    """Collect read-only Segment Routing TE policy state from a lab device."""

    return check_sr_policies(device_name)


@_read_only_tool()
def check_lab_fabric(check: str = "bgp") -> dict:
    """Run one read-only check (facts|interfaces|bgp|lldp|isis|sr) across the fabric."""

    return check_fabric(check)


@_read_only_tool()
def collect_lab_evidence(device_name: str) -> dict:
    """Collect the full read-only evidence bundle from a lab device in one session."""

    return collect_evidence(device_name)


@_read_only_tool()
def get_lab_route(device_name: str, prefix: str) -> dict:
    """Look up a specific route on a lab device.

    ``prefix`` must be an IPv4 address or CIDR prefix, e.g. "10.255.0.31" or
    "10.0.0.0/24" -- validated and rendered from its parsed, canonical form
    (never passed through as text); anything else is rejected before any
    connection is made. Narrow this after seeing a route-related anomaly in
    other evidence (e.g. a missing or unexpected next hop).
    """

    return get_route(device_name, prefix)


@_read_only_tool()
def get_lab_bgp_neighbor(device_name: str, address: str) -> dict:
    """Look up a specific BGP neighbor on a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". Use this to
    narrow in on one peer after ``check_lab_bgp_neighbors`` shows it Idle or
    otherwise not Established.
    """

    return get_bgp_neighbor(device_name, address)


@_read_only_tool()
def get_lab_interface(device_name: str, name: str) -> dict:
    """Look up a specific interface's status on a lab device.

    ``name`` must be a valid interface name, e.g. "GigabitEthernet0/0/0/1",
    "Gi0/0/0/2.300", or "Loopback0" -- validated against an anchored
    letters/digits/``._/-`` charset, so it can never carry a shell or CLI
    metacharacter.
    """

    return get_interface(device_name, name)


@_read_only_tool()
def get_lab_logging(device_name: str, count: int = 20) -> dict:
    """Show a lab device's most recent log lines.

    ``count`` must be a plain integer from 1 to 500 (default 20).
    """

    return get_logging(device_name, count)


@_read_only_tool()
def get_lab_ping(device_name: str, address: str) -> dict:
    """Ping an IPv4 address from a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". This is an
    active probe: it generates ICMP traffic (unlike every other tool here)
    even though it changes no device configuration, and is refused when the
    server has ``NETTOOLS_ALLOW_ACTIVE_PROBES`` set to a falsy value.
    """

    return ping_device(device_name, address)


@_read_only_tool()
def get_lab_traceroute(device_name: str, address: str) -> dict:
    """Traceroute to an IPv4 address from a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". An active
    probe like ``get_lab_ping``: generates traffic, changes no device state,
    and is refused when ``NETTOOLS_ALLOW_ACTIVE_PROBES`` is set to a falsy
    value.
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
    """Collect fresh evidence and diff it against the device's most recently saved snapshot.

    Saves the fresh collection as the new "latest" snapshot, same as
    ``nettools diff DEVICE``. ``data.has_previous`` is ``false`` (and
    ``data.diff`` is ``null``) the first time this runs for a device -- there
    is nothing to compare against yet, not an error.
    """

    return _diff_against(
        "diff_lab_device_against_latest", device_name, load_latest_snapshot(device_name)
    )


@_read_only_tool()
def diff_lab_device_against_golden(device_name: str) -> dict:
    """Collect fresh evidence and diff it against the device's pinned golden snapshot.

    ``data.has_previous`` is ``false`` (and ``data.diff`` is ``null``) when no
    golden snapshot has ever been pinned for this device -- see
    ``pin_lab_golden_snapshot``.
    """

    return _diff_against(
        "diff_lab_device_against_golden", device_name, load_golden_snapshot(device_name)
    )


@_read_only_tool()
def assess_lab_device_health(device_name: str) -> dict:
    """Evaluate deterministic health verdicts (role invariants + baseline drift) for one device.

    Cheap, rule-based -- not an LLM call -- so a client can get a severity
    verdict (``ok``/``info``/``warning``/``critical``) and its findings
    without spending a reasoning call. See ``assess_lab_fabric_health`` to
    evaluate every device at once.
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
    """Evaluate deterministic health verdicts across every device in the fabric inventory.

    Same rules as ``assess_lab_device_health``, rolled up to one fabric-wide
    severity (the max over every device's own severity) -- see
    ``health.evaluate_fabric``.
    """

    listed = list_devices()
    if listed.get("status") != "success":
        return listed
    names = [device["name"] for device in listed["data"]["devices"]]
    evidence_by_device = {name: collect_evidence(name) for name in names}
    return evaluate_fabric(evidence_by_device)


@_read_only_tool()
def detect_lab_flaps(device_name: str, min_transitions: int = 3) -> dict:
    """Report fields that oscillated across a device's saved snapshot history.

    A peer that bounced up/down/up between collections can look clean in
    every single pairwise diff -- this reads the device's *entire* saved
    snapshot history instead. Requires prior snapshots (``save_lab_snapshot``,
    ``diff_lab_device_against_latest``, or ``nettools diff``/``capture``) --
    with none saved yet, ``data.flapping`` is simply empty.
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
    """Localise the cause of a fault by walking a dependency ladder, deterministically.

    **Prefer this over calling the individual check tools yourself** when the
    question is "why is this broken?". It walks the layers beneath a symptom in
    order -- session, transport, route, IGP adjacency, physical interface -- and
    reports the *lowest* broken one as the cause, with the broken layers above
    it as the causal chain that explains the symptom. Every verdict comes from
    code comparing parsed fields, with no model involved.

    ``device``  the device to investigate *from*, e.g. "RR1".
    ``subject`` what to investigate, in the flow's own vocabulary. For
                ``bgp_session`` that is the peer's IPv4 address as
                ``show bgp summary`` lists it, e.g. "10.255.0.12".
    ``flow``    the object type. ``bgp_session`` (default) or ``interface``.

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


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""

    for change in protect_stdio():
        print(f"nettools-mcp: redirected {change} away from stdout", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
