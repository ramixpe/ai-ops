"""The staged MCP surface: five stage-shaped tools plus a probe. B-479.

Why a second surface instead of a replacement
-----------------------------------------------
B-113's consolidation is already justified by arithmetic — the reworded
descriptions grew the classic manifest +86%, paid on every tool-list call —
but collapsing the 21-tool surface would destroy the historical selection A/B
whose third arm (Q1 during a live fault) is still owed. The experiment record
(retained in Git history) names the
*between-surface* comparison as the right experiment.
So: **both surfaces exist**, selected by ``NETTOOLS_MCP_SURFACE``
(``classic``, the default, or ``staged``), and the default flips only after
the measurement lands.

What "staged" means
---------------------
One tool per stage of an investigation, mirroring how an engineer actually
moves — orient, check, look up, ask why, look back — instead of one tool per
underlying function. Every tool is a thin composition of the same already-safe
functions: no new device path, no new authority, the same sanitisation
boundary applied by the same registration machinery, and the two active-probe
kinds still separately annotated (B-473 — folding probes into a passive tool
would erase the distinction that wave built).

Descriptions are the product (OBS-112): each opens with the question it
answers and says when to prefer another tool — that wording is what a
4B-parameter navigator measurably selects on.

Enum parameters are ``Literal[...]`` so the generated JSON Schema carries the
valid values (a validating client refuses a bad value before the call); the
in-function unknown-value branches stay for non-validating clients — defence
in depth, and their error text is classified into static valid-value kinds by
the boundary rather than withheld (walkthrough stumble 8).
"""

from __future__ import annotations

from typing import Any, Literal

from agent_nettools.health import evaluate_device, evaluate_fabric
from agent_nettools.inventory_model import load_inventory_file
from agent_nettools.mcp_profiles import GUIDED_CAPABILITIES, GUIDED_TOOL_NAMES, RegistrationClass
from agent_nettools.network_tools import (
    CHECK_TOOLS,
    check_fabric,
    collect_evidence,
    detect_flaps,
    diff_evidence,
    get_bgp_neighbor,
    get_interface,
    get_logging,
    get_route,
    list_devices,
    load_golden_snapshot,
    load_latest_snapshot,
    ping_device,
    run_intent,
    traceroute_device,
)

__all__ = ["STAGED_TOOL_NAMES", "apply"]

STAGED_TOOL_NAMES = GUIDED_TOOL_NAMES

_LOOKUPS = {
    "route": get_route,
    "bgp_neighbor": get_bgp_neighbor,
    "interface": get_interface,
    "logging": get_logging,
}


def _device_record(name: str):
    for device in load_inventory_file().devices:
        if device.name == name:
            return device
    return None


# --------------------------------------------------------------------------- #
# The six tools, as plain functions. `apply()` registers them.
# --------------------------------------------------------------------------- #


def explore_lab(device_name: str | None = None) -> dict:
    """Answers: *what is here, and is it broadly OK?*

    Prefer this as the opening move of any session: with no argument it lists
    the inventory; with a device it returns that device's facts and a
    deterministic health verdict. For a specific protocol's state prefer
    `check_lab`; for "why is X broken" prefer `investigate_lab`.
    """

    if not device_name:
        return list_devices()
    record = _device_record(device_name)
    if record is None:
        return {"tool": "explore_lab", "device": device_name,
                "error": f"{device_name!r} is not in the inventory"}
    # One collection, not two: `collect_evidence`'s result already carries the
    # `facts` section, so a separate `run_intent(device, "facts")` doubled the
    # SSH round-trips on the tool billed as the cheapest opening move
    # (2026-08-18 review). The house invariant is one login per orientation.
    evidence = collect_evidence(device_name)
    return {"tool": "explore_lab", "device": device_name,
            "facts": evidence.get("facts"),
            "health": evaluate_device(evidence, record)}


def check_lab(scope: str, intent: Literal["facts", "interfaces", "bgp", "lldp", "isis", "sr"] | None = None) -> dict:
    """Answers: *what is the state of X right now?*

    ``scope`` is a device name or the literal ``"fabric"``. With an ``intent``
    (facts/interfaces/bgp/lldp/isis/sr) it runs that one check; without, a
    device gets its full health verdict and the fabric gets every device's.
    Prefer `investigate_lab` for any "why" question — this reports state, it
    does not explain it.
    """

    if scope == "fabric":
        if intent:
            return check_fabric(intent)
        listed = list_devices()
        if listed.get("status") != "success":
            return listed
        names = [d["name"] for d in listed["data"]["devices"]]
        # Pooled like assess_lab_fabric_health (B-475); inventory order kept.
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=max(1, min(8, len(names)))) as pool:
            futures = {n: pool.submit(collect_evidence, n) for n in names}
            evidence = {n: futures[n].result() for n in names}
        return evaluate_fabric(evidence)
    if intent:
        if intent not in CHECK_TOOLS:
            return {"tool": "check_lab", "device": scope, "status": "error", "data": {},
                    "errors": [f"unknown intent {intent!r}; one of {sorted(CHECK_TOOLS)}"]}
        return run_intent(scope, intent)
    record = _device_record(scope)
    if record is None:
        return {"tool": "check_lab", "device": scope, "status": "error", "data": {},
                "errors": [f"{scope!r} is not in the inventory"]}
    return evaluate_device(collect_evidence(scope), record)


def lookup_lab(device_name: str, kind: Literal["route", "bgp_neighbor", "interface", "logging"], value: str) -> dict:
    """Answers: *what does this device say about this specific object?*

    ``kind``: ``route`` (an IPv4 prefix), ``bgp_neighbor`` (a peer address),
    ``interface`` (a name), or ``logging`` (a line count). Every value is
    validated by reconstruction exactly as a human caller's would be. Prefer
    `check_lab` for whole-protocol state and `investigate_lab` for causes.
    """

    fn = _LOOKUPS.get(kind)
    if fn is None:
        return {"tool": "lookup_lab", "device": device_name, "status": "error", "data": {},
                "errors": [f"unknown kind {kind!r}; one of {sorted(_LOOKUPS)}"]}
    return fn(device_name, value)


def investigate_lab(
    device_name: str,
    subject: str,
    flow: Literal["bgp_session", "interface", "isis_adjacency", "ldp_session"] = "bgp_session",
) -> dict:
    """Answers: *why is this broken?* — the one to prefer for any cause question.

    Walks the flow's dependency ladder deterministically and reports the
    lowest broken rung with its causal chain; every verdict is code comparing
    parsed fields, and no model is involved in reaching the answer. Read
    `finding` first, and check `trustworthy` before reporting anything.
    """

    from agent_nettools.investigation import investigate

    return investigate(device_name, subject, flow=flow).to_payload()


def history_lab(device_name: str, mode: Literal["latest_diff", "golden_diff", "flaps"] = "latest_diff") -> dict:
    """Answers: *what changed on this device?*

    ``latest_diff`` compares live state against the last snapshot,
    ``golden_diff`` against the pinned known-good baseline, ``flaps`` scans
    the whole snapshot history for oscillating fields a single diff cannot
    see. Prefer `investigate_lab` when you already know what is broken and
    need the cause rather than the change.
    """

    if mode == "flaps":
        return detect_flaps(device_name)
    loader = {"latest_diff": load_latest_snapshot, "golden_diff": load_golden_snapshot}.get(mode)
    if loader is None:
        return {"tool": "history_lab", "device": device_name, "status": "error", "data": {},
                "errors": [f"unknown mode {mode!r}; latest_diff, golden_diff, or flaps"]}
    baseline = loader(device_name)
    if baseline is None:
        return {"tool": "history_lab", "device": device_name, "status": "error", "data": {},
                "errors": [f"no {'golden' if mode == 'golden_diff' else 'saved'} "
                           f"snapshot exists for {device_name}"]}
    return diff_evidence(baseline, collect_evidence(device_name))


def probe_lab(device_name: str, kind: Literal["ping", "traceroute"], address: str) -> dict:
    """ACTIVE PROBE: sends ICMP/UDP traffic to the target. Answers: *can this
    device reach that address right now, and by which path?*

    ``kind`` is ``ping`` or ``traceroute``. Unlike every other staged tool
    this generates traffic (it changes no device state); it is separately
    annotated and gated by ``NETTOOLS_ALLOW_ACTIVE_PROBES``. Prefer the
    passive tools unless the question is specifically about live
    reachability.
    """

    if kind == "ping":
        return ping_device(device_name, address)
    if kind == "traceroute":
        return traceroute_device(device_name, address)
    return {"tool": "probe_lab", "device": device_name, "status": "error", "data": {},
            "errors": [f"unknown kind {kind!r}; ping or traceroute"]}


def apply(server_module: Any) -> None:
    """Replace the classic surface with the staged one, on the module's `mcp`.

    Called by `server.py`'s end-of-file hook when ``NETTOOLS_MCP_SURFACE`` is
    ``staged``. Uses the server's own registration machinery so the boundary
    (sanitise-at-registration) and the probe annotation are the same code
    paths the classic surface goes through — not copies.

    **Fails loudly on an unknown SDK shape.** Clearing the classic tools
    reaches into FastMCP's tool manager; if the installed SDK does not expose
    it where expected, this raises at startup rather than serving both
    surfaces at once — a surface flag that half-applies is worse than one
    that refuses.
    """

    mcp = server_module.mcp
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError(
            "NETTOOLS_MCP_SURFACE=staged: this mcp SDK does not expose the tool "
            "manager where expected; refusing to serve a mixed surface"
        )
    tools.clear()

    register_passive = server_module._read_only_tool
    register_probe = server_module._active_probe_tool

    functions = {
        "explore_lab": explore_lab,
        "check_lab": check_lab,
        "lookup_lab": lookup_lab,
        "investigate_lab": investigate_lab,
        "history_lab": history_lab,
        "probe_lab": probe_lab,
    }
    for capability in GUIDED_CAPABILITIES:
        register = register_probe if capability.registration is RegistrationClass.ACTIVE_PROBE else register_passive
        register()(functions[capability.name])
