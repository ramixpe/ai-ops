"""Single command-line entry point for the read-only IOS-XR network tools.

Exposed as the ``nettools`` console script. Subcommands:

    nettools inventory
    nettools facts | interfaces | bgp | lldp | isis | sr  [DEVICE]
    nettools fabric [CHECK]
    nettools route DEVICE PREFIX
    nettools bgp-neighbor DEVICE ADDRESS
    nettools interface DEVICE NAME
    nettools logging DEVICE [--count N]
    nettools ping DEVICE ADDRESS
    nettools traceroute DEVICE ADDRESS
    nettools analyze [DEVICE] [--show-evidence] [--save]
    nettools analyze --fabric [--show-evidence] [--save]
    nettools agent "QUESTION" [--device DEVICE] [--max-iterations N] [--time-budget SECONDS]
    nettools demo [DEVICE]
    nettools diff [DEVICE] [--against golden|latest]
    nettools capture [DEVICE ...] [--all] [--label t0] [--out DIR] [--no-scrub]
    nettools learn-topology [--from-fixtures|--live] [--label t0] [--out PATH] [--write]
    nettools health [DEVICE ...] [--all] [--from-fixtures] [--label t0] [--min-severity S]
    nettools baseline pin [DEVICE] [--from-latest]
    nettools baseline show [DEVICE]
    nettools flaps [DEVICE]
    nettools evidence prune [--keep-days N] [--keep-count M] [--device DEVICE]
    nettools inspect [DEVICE]

``nettools health`` exit codes: 0 (ok/info, nothing actionable), 1 (warning),
2 (critical) -- so CI and cron can gate on the fabric's worst severity.

``route``/``bgp-neighbor``/``interface``/``logging``/``ping``/``traceroute``
are validated, parameterized templates (Phase 5): the prefix/address/interface
name/count argument is never passed through to the device as text -- it is
parsed into a typed object and the command is rendered from that object's own
canonical form (see ``agent_nettools.templates``, "canonicalize by
reconstruction"). ``ping``/``traceroute`` generate traffic (unlike every other
command here) and are gated by ``NETTOOLS_ALLOW_ACTIVE_PROBES`` (default
enabled).

``nettools evidence prune`` (Phase 7) deletes timestamped snapshots outside a
retention window (age and/or count; golden snapshots are never touched),
against whichever evidence backend ``NETTOOLS_EVIDENCE_BACKEND`` selects
(JSON files, the default, or SQLite).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .agent_loop import run_agent_loop
from .fabric_analysis import analyze_fabric
from .fixtures import capture_device, load_fixture_evidence
from .health import evaluate_fabric, exit_code_for_severity, severity_rank
from .inventory import InventoryError, get_default_device_name
from .inventory_model import resolve_inventory_path
from .llm_analysis import LLMAnalysisError, analyze_evidence
from .network_tools import (
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
    list_snapshot_history,
    load_golden_snapshot,
    load_latest_snapshot,
    ping_device,
    prune_snapshots,
    save_golden_snapshot,
    save_snapshot,
    traceroute_device,
)
from .topology import (
    build_anomaly_report,
    derive_expected,
    format_anomaly_report,
    update_expected_in_yaml,
)


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _resolve_device(name: str | None) -> str:
    return name or get_default_device_name()


def _cmd_inventory(_args: argparse.Namespace) -> int:
    result = list_devices()
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_check(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    result = CHECK_TOOLS[args.check](device)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_fabric(args: argparse.Namespace) -> int:
    result = check_fabric(args.check)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_route(args: argparse.Namespace) -> int:
    result = get_route(args.device, args.prefix)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_bgp_neighbor(args: argparse.Namespace) -> int:
    result = get_bgp_neighbor(args.device, args.address)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_interface(args: argparse.Namespace) -> int:
    result = get_interface(args.device, args.name)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_logging(args: argparse.Namespace) -> int:
    result = get_logging(args.device, args.count)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_ping(args: argparse.Namespace) -> int:
    result = ping_device(args.device, args.address)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_traceroute(args: argparse.Namespace) -> int:
    result = traceroute_device(args.device, args.address)
    _print(result)
    return 0 if result.get("status") == "success" else 1


def _cmd_analyze(args: argparse.Namespace) -> int:
    if args.fabric:
        listed = list_devices()
        if listed.get("status") != "success":
            _print(listed)
            return 1
        names = [device["name"] for device in listed["data"]["devices"]]
        evidence_by_device = {name: collect_evidence(name) for name in names}
        if args.save:
            for evidence in evidence_by_device.values():
                path = save_snapshot(evidence)
                print(f"# Snapshot saved: {path}")
        if args.show_evidence:
            print("# Evidence")
            _print(evidence_by_device)
            print("\n# Analysis")
        try:
            result = analyze_fabric(evidence_by_device)
        except (LLMAnalysisError, ValueError) as exc:
            # ValueError covers provider misconfiguration from get_provider().
            print(f"Analysis error: {exc}")
            return 1
        print(result["analysis"])
        if result["truncated"]:
            print(
                f"\n[Note: evidence was truncated for {len(result['truncated'])} "
                "section(s) to stay within the evidence budget; see the "
                "programmatic 'truncated' report for details.]"
            )
        return 0

    device = _resolve_device(args.device)
    evidence = collect_evidence(device)
    if args.save:
        path = save_snapshot(evidence)
        print(f"# Snapshot saved: {path}")
    if args.show_evidence:
        print("# Evidence")
        _print(evidence)
        print("\n# Analysis")
    try:
        print(analyze_evidence(evidence))
    except (LLMAnalysisError, ValueError) as exc:
        # ValueError covers provider misconfiguration from get_provider().
        print(f"Analysis error: {exc}")
        return 1
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    try:
        result = run_agent_loop(
            args.question,
            device=args.device,
            max_iterations=args.max_iterations,
            time_budget_s=args.time_budget,
        )
    except (LLMAnalysisError, ValueError) as exc:
        # ValueError covers provider misconfiguration from get_provider().
        print(f"Agent error: {exc}")
        return 1

    print(result["answer"])
    print(
        f"\n[{result['iterations']} iteration(s), {len(result['tool_calls'])} tool call(s), "
        f"stopped_because={result['stopped_because']}]"
    )
    return 0 if result["stopped_because"] == "end_turn" else 1


def _cmd_demo(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)

    print("## Agent Step 1: Discover devices")
    devices = list_devices()
    names = [d["name"] for d in devices.get("data", {}).get("devices", [])]
    print(f"Available devices: {', '.join(names)}")
    if device not in names:
        print(f"Device not found: {device}")
        return 1

    print("\n## Agent Step 2: Collect approved evidence")
    evidence = collect_evidence(device)
    print("Facts, interfaces, BGP, LLDP, IS-IS, and SR-TE state collected.")

    print("\n## Agent Step 3: Analyze evidence")
    try:
        print(analyze_evidence(evidence))
    except (LLMAnalysisError, ValueError) as exc:
        # ValueError covers provider misconfiguration from get_provider().
        print(f"Analysis error: {exc}")
        return 1
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    if args.against == "golden":
        previous = load_golden_snapshot(device)
        missing_message = (
            f"No golden snapshot pinned for {device}; run `nettools baseline pin {device}` first."
        )
    else:
        previous = load_latest_snapshot(device)
        missing_message = f"No previous snapshot for {device}; baseline established."

    current = collect_evidence(device)
    path = save_snapshot(current)
    print(f"# Snapshot saved: {path}")
    if previous is None:
        print(missing_message)
        return 0
    _print(diff_evidence(previous, current))
    return 0


def _cmd_baseline_pin(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    if args.from_latest:
        evidence = load_latest_snapshot(device)
        if evidence is None:
            print(
                f"No saved snapshot for {device} to pin; run `nettools diff {device}` "
                "first, or omit --from-latest to collect fresh evidence now."
            )
            return 1
    else:
        evidence = collect_evidence(device)
        save_snapshot(evidence)

    path = save_golden_snapshot(evidence)
    print(f"# Golden snapshot pinned: {path}")
    return 0


def _cmd_baseline_show(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    evidence = load_golden_snapshot(device)
    if evidence is None:
        print(f"No golden snapshot pinned for {device}.")
        return 1
    _print(evidence)
    return 0


def _cmd_flaps(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    result = detect_flaps(device, min_transitions=args.min_transitions)
    _print(result)
    return 0 if not result["flapping"] else 1


def _cmd_evidence_prune(args: argparse.Namespace) -> int:
    if args.keep_days is None and args.keep_count is None:
        print("Nothing to do: pass --keep-days and/or --keep-count.")
        return 1
    result = prune_snapshots(
        device_name=args.device, keep_days=args.keep_days, keep_count=args.keep_count
    )
    _print(result)
    return 0


def _cmd_evidence_history(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    history = list_snapshot_history(device)
    _print({"device": device, "count": len(history), "history": history})
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            _print(listed)
            return 2
        names = [device["name"] for device in listed["data"]["devices"]]
    else:
        names = args.devices or [_resolve_device(None)]

    if args.from_fixtures:
        evidence_by_device = {name: load_fixture_evidence(name, label=args.label) for name in names}
    else:
        evidence_by_device = {name: collect_evidence(name) for name in names}

    result = evaluate_fabric(evidence_by_device)

    # One JSON document, like every other subcommand: a stream of concatenated
    # pretty-printed objects is not parseable, and `nettools health --all | jq`
    # is the whole point of having exit codes. --min-severity filters which
    # devices appear, but the fabric severity is always computed over all of
    # them, so filtering the view can never soften the verdict.
    threshold = severity_rank(args.min_severity)
    reported = {
        name: verdict
        for name, verdict in result["devices"].items()
        if severity_rank(verdict["severity"]) >= threshold
    }
    _print(
        {
            "severity": result["severity"],
            "counts": result.get("counts", {}),
            "devices": reported,
            "suppressed": sorted(set(result["devices"]) - set(reported)),
        }
    )
    return exit_code_for_severity(result["severity"])


def _cmd_capture(args: argparse.Namespace) -> int:
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            _print(listed)
            return 1
        devices = [device["name"] for device in listed["data"]["devices"]]
    else:
        devices = args.devices or [_resolve_device(None)]

    captures = [
        capture_device(
            name,
            label=args.label,
            base_dir=args.out,
            scrub=not args.no_scrub,
        )
        for name in devices
    ]
    _print({"label": args.label, "captures": captures})
    # A partial capture must not look like a clean one.
    return 0 if all(not capture["errors"] for capture in captures) else 1


def _cmd_learn_topology(args: argparse.Namespace) -> int:
    """Derive expected topology counts from evidence and report fabric anomalies.

    Reports by default and writes nothing: the inventory YAML is hand-maintained
    and carries explanatory comments that a PyYAML round-trip would silently
    discard. ``--out`` writes a generated draft to a new path for review;
    ``--write`` edits the resolved inventory in place and says what it costs.

    Always exits 0 -- an inconsistent fabric is not a tool failure -- but the
    anomaly report is printed unconditionally so it cannot be missed even when
    every device's counts derive cleanly.
    """

    listed = list_devices()
    if listed.get("status") != "success":
        _print(listed)
        return 1
    names = [device["name"] for device in listed["data"]["devices"]]

    if args.live:
        evidence_by_device = {name: collect_evidence(name) for name in names}
    else:
        evidence_by_device = {name: load_fixture_evidence(name, label=args.label) for name in names}

    derived = derive_expected(evidence_by_device)
    source_path = resolve_inventory_path()

    if args.out or args.write:
        out_path = Path(args.out) if args.out else source_path
        update_expected_in_yaml(source_path, derived, out_path=out_path)
        print(f"# Wrote expected topology for {len(derived)} device(s) to {out_path}")
        print("# Note: comments in the source YAML are not preserved by this rewrite.\n")
    else:
        print(f"# Derived expected topology for {len(derived)} device(s) (nothing written).")
        print(f"# Compare against {source_path}; use --out PATH for a draft or --write to apply.\n")
        _print(derived)
        print()

    print(format_anomaly_report(build_anomaly_report(evidence_by_device)))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    # Imported lazily so the rest of the CLI works without the MCP SDK installed.
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    device = _resolve_device(args.device)

    async def run() -> None:
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("=== TOOLS EXPOSED BY THE SERVER ===")
                for tool in tools.tools:
                    summary = (tool.description or "").splitlines()
                    print(f"  - {tool.name}: {summary[0] if summary else ''}")

                print("\n=== CALL: list_lab_devices ===")
                result = await session.call_tool("list_lab_devices", {})
                for block in result.content:
                    print(getattr(block, "text", block))

                print(f"\n=== CALL: check_lab_bgp_neighbors (device_name={device}) ===")
                result = await session.call_tool(
                    "check_lab_bgp_neighbors", {"device_name": device}
                )
                for block in result.content:
                    print(getattr(block, "text", block))

    asyncio.run(run())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nettools", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inventory", help="List devices without credentials.").set_defaults(
        func=_cmd_inventory
    )

    for check in sorted(CHECK_TOOLS):
        p = sub.add_parser(check, help=f"Run the {check} check on a device.")
        p.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
        p.set_defaults(func=_cmd_check, check=check)

    p_fabric = sub.add_parser("fabric", help="Run a check across the whole inventory.")
    p_fabric.add_argument(
        "check", nargs="?", default="bgp", choices=sorted(CHECK_TOOLS), help="Check to run."
    )
    p_fabric.set_defaults(func=_cmd_fabric)

    p_route = sub.add_parser("route", help="Look up a specific route (validated template).")
    p_route.add_argument("device", help="Device name.")
    p_route.add_argument("prefix", help="IPv4 address or prefix, e.g. 10.0.0.0/24.")
    p_route.set_defaults(func=_cmd_route)

    p_bgp_neighbor = sub.add_parser(
        "bgp-neighbor", help="Look up a specific BGP neighbor (validated template)."
    )
    p_bgp_neighbor.add_argument("device", help="Device name.")
    p_bgp_neighbor.add_argument("address", help="Neighbor IPv4 address.")
    p_bgp_neighbor.set_defaults(func=_cmd_bgp_neighbor)

    p_interface = sub.add_parser(
        "interface", help="Look up a specific interface's status (validated template)."
    )
    p_interface.add_argument("device", help="Device name.")
    p_interface.add_argument("name", help="Interface name, e.g. GigabitEthernet0/0/0/1.")
    p_interface.set_defaults(func=_cmd_interface)

    p_logging = sub.add_parser(
        "logging", help="Show recent log lines (validated template)."
    )
    p_logging.add_argument("device", help="Device name.")
    p_logging.add_argument(
        "--count", type=int, default=20, help="Number of log lines, 1-500 (default: 20)."
    )
    p_logging.set_defaults(func=_cmd_logging)

    p_ping = sub.add_parser(
        "ping",
        help="Ping an IPv4 address from a device (validated template; active probe -- see "
        "NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_ping.add_argument("device", help="Device name.")
    p_ping.add_argument("address", help="Target IPv4 address.")
    p_ping.set_defaults(func=_cmd_ping)

    p_traceroute = sub.add_parser(
        "traceroute",
        help="Traceroute to an IPv4 address from a device (validated template; active probe -- "
        "see NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_traceroute.add_argument("device", help="Device name.")
    p_traceroute.add_argument("address", help="Target IPv4 address.")
    p_traceroute.set_defaults(func=_cmd_traceroute)

    p_analyze = sub.add_parser("analyze", help="Collect evidence and analyze with the LLM.")
    p_analyze.add_argument("device", nargs="?", help="Device name; defaults to PE1. Ignored with --fabric.")
    p_analyze.add_argument("--show-evidence", action="store_true", help="Print evidence first.")
    p_analyze.add_argument("--save", action="store_true", help="Save an evidence snapshot.")
    p_analyze.add_argument(
        "--fabric",
        action="store_true",
        help="Analyze every inventory device together, correlating findings across devices.",
    )
    p_analyze.set_defaults(func=_cmd_analyze)

    p_agent = sub.add_parser(
        "agent", help="Answer a question with a bounded, tool-calling agent loop (Anthropic only)."
    )
    p_agent.add_argument("question", help="The question to investigate and answer.")
    p_agent.add_argument("--device", help="Optional device to focus the investigation on.")
    p_agent.add_argument(
        "--max-iterations", type=int, default=8, help="Maximum agent loop iterations (default: 8)."
    )
    p_agent.add_argument(
        "--time-budget", type=float, default=120, help="Wall-clock budget in seconds (default: 120)."
    )
    p_agent.set_defaults(func=_cmd_agent)

    p_demo = sub.add_parser("demo", help="Run the narrated agent demo.")
    p_demo.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_demo.set_defaults(func=_cmd_demo)

    p_diff = sub.add_parser("diff", help="Diff current evidence against a saved snapshot.")
    p_diff.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_diff.add_argument(
        "--against",
        choices=("golden", "latest"),
        default="latest",
        help="Compare against the pinned golden snapshot or the most recent one (default: latest).",
    )
    p_diff.set_defaults(func=_cmd_diff)

    p_capture = sub.add_parser("capture", help="Capture real device output as test fixtures.")
    p_capture.add_argument("devices", nargs="*", help="Device names; defaults to PE1.")
    p_capture.add_argument("--all", action="store_true", help="Capture every inventory device.")
    p_capture.add_argument("--label", default="t0", help="Capture label (default: t0).")
    p_capture.add_argument("--out", help="Fixture root; defaults to tests/fixtures.")
    p_capture.add_argument(
        "--no-scrub",
        action="store_true",
        help="Write raw output without scrubbing. For local inspection only; never commit.",
    )
    p_capture.set_defaults(func=_cmd_capture)

    p_learn = sub.add_parser(
        "learn-topology",
        help="Derive expected topology counts from evidence and update the inventory.",
    )
    source = p_learn.add_mutually_exclusive_group()
    source.add_argument(
        "--from-fixtures",
        action="store_false",
        dest="live",
        default=False,
        help="Derive from committed test fixtures (default).",
    )
    source.add_argument(
        "--live", action="store_true", dest="live", help="Derive from a live collection."
    )
    p_learn.add_argument("--label", default="t0", help="Fixture label to use (default: t0).")
    p_learn.add_argument("--out", help="Write a generated draft inventory to this new path.")
    p_learn.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the resolved inventory in place. Discards its comments.",
    )
    p_learn.set_defaults(func=_cmd_learn_topology)

    p_health = sub.add_parser(
        "health",
        help="Evaluate deterministic health verdicts. Exit codes: 0 ok/info, 1 warning, 2 critical.",
    )
    p_health.add_argument("devices", nargs="*", help="Device names; defaults to PE1 (or use --all).")
    p_health.add_argument("--all", action="store_true", help="Evaluate every inventory device.")
    p_health.add_argument(
        "--from-fixtures",
        action="store_true",
        help="Evaluate committed test fixtures instead of a live collection.",
    )
    p_health.add_argument(
        "--label", default="t0", help="Fixture label to use with --from-fixtures (default: t0)."
    )
    p_health.add_argument(
        "--min-severity",
        default="ok",
        choices=("ok", "info", "warning", "critical"),
        help="Only print devices at or above this severity (default: ok, i.e. every device).",
    )
    p_health.set_defaults(func=_cmd_health)

    p_baseline = sub.add_parser("baseline", help="Manage per-device pinned golden snapshots.")
    baseline_sub = p_baseline.add_subparsers(dest="baseline_command", required=True)

    p_baseline_pin = baseline_sub.add_parser("pin", help="Pin a golden snapshot for a device.")
    p_baseline_pin.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_baseline_pin.add_argument(
        "--from-latest",
        action="store_true",
        help="Pin the most recently saved snapshot instead of collecting a fresh one.",
    )
    p_baseline_pin.set_defaults(func=_cmd_baseline_pin)

    p_baseline_show = baseline_sub.add_parser("show", help="Print a device's pinned golden snapshot.")
    p_baseline_show.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_baseline_show.set_defaults(func=_cmd_baseline_show)

    p_flaps = sub.add_parser(
        "flaps", help="Detect oscillating fields across a device's saved snapshot history."
    )
    p_flaps.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_flaps.add_argument(
        "--min-transitions",
        type=int,
        default=3,
        help="Minimum value changes before a field is reported as flapping (default: 3).",
    )
    p_flaps.set_defaults(func=_cmd_flaps)

    p_evidence = sub.add_parser("evidence", help="Manage stored evidence snapshots (Phase 7).")
    evidence_sub = p_evidence.add_subparsers(dest="evidence_command", required=True)

    p_evidence_prune = evidence_sub.add_parser(
        "prune", help="Delete timestamped snapshots outside a retention window."
    )
    p_evidence_prune.add_argument(
        "--keep-days", type=float, default=None, help="Keep snapshots from the last N days."
    )
    p_evidence_prune.add_argument(
        "--keep-count", type=int, default=None, help="Keep the N most recent snapshots per device."
    )
    p_evidence_prune.add_argument(
        "--device", help="Prune only this device; defaults to every device in the store."
    )
    p_evidence_prune.set_defaults(func=_cmd_evidence_prune)

    p_evidence_history = evidence_sub.add_parser(
        "history", help="List a device's saved timestamped snapshots, oldest first."
    )
    p_evidence_history.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_evidence_history.set_defaults(func=_cmd_evidence_history)

    p_inspect = sub.add_parser("inspect", help="Smoke-test the MCP server over stdio.")
    p_inspect.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_inspect.set_defaults(func=_cmd_inspect)

    return parser


def main() -> int:
    # Two lookups so .env is found both from the current directory upward and
    # next to an editable install of the package.
    load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except InventoryError as exc:
        _print({"status": "error", "errors": [str(exc)]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
