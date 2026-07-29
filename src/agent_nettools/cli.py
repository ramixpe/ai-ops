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
    nettools evidence history [DEVICE]
    nettools metrics [--format json|prometheus] [--quiet]
    nettools inspect [DEVICE]
    nettools version

Most commands that print a structured result accept ``--format
json|table|summary`` (default: ``json``, so nothing that already parses this
tool's JSON output breaks) and ``--quiet``/``-q`` (suppress all output; only
the exit code carries the outcome) -- see ``agent_nettools.output`` for what
each format shows. **A table or summary view never invents or softens data**:
the exit code below is always computed from the full result before
rendering, so ``--format summary`` on a critical fabric still exits
non-zero exactly like ``--format json`` would. Commands whose output is
narrative text rather than one structured payload (``analyze``, ``agent``,
``demo``, ``capture``, ``learn-topology``, ``inspect``) do not take
``--format``/``--quiet``.

Exit codes
------------
``nettools health`` set the shape this project follows everywhere else:
``0`` (ok/info, nothing actionable), ``1`` (warning), ``2`` (critical).
Applied consistently:

- ``0`` -- success; nothing actionable.
- ``1`` -- the command ran but reports a problem: a tool/fabric result with
  ``status: "error"`` (one or more commands/devices failed), a health
  verdict at ``warning``, or (``diff``) real drift was found.
- ``2`` -- the command could not run at all, or reports the worst possible
  outcome: a missing/invalid inventory or LLM configuration
  (``InventoryError``, or ``get_provider()``'s ``ValueError``), a health
  verdict at ``critical``, or (``diff``) an intent failed to collect on
  either side, so the comparison itself is not trustworthy.

``diff`` follows the Unix ``diff --exit-code`` convention on top of that
scheme: ``0`` no differences, ``1`` differences found, ``2`` could not fully
compare.

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

``nettools metrics`` (Phase 8) reports the current process's operational
metrics -- per-device collection success/failure, latency, retries, and
health verdict counts by severity -- as JSON (default) or Prometheus text
exposition format (``--format prometheus``). Counters are in-memory only
unless ``NETTOOLS_METRICS_FILE`` is set, in which case they persist across
separate ``nettools`` invocations too; see ``agent_nettools.metrics``.

``nettools version`` (Phase 8) prints the installed package version plus the
Python/platform it is running on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import __version__, metrics, output
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

# Exit codes, applied consistently across every command -- see the module
# docstring's "Exit codes" section for the full rationale.
EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2


def _emit(payload: dict, args: argparse.Namespace) -> None:
    """Print one structured result, honoring ``--format``/``--quiet``.

    Never affects the exit code: every ``_cmd_*`` function computes its
    return value from the full, unfiltered ``payload`` before (or without
    regard to) calling this -- a table/summary rendering is strictly a
    reformat, never a softer read of the same facts.
    """

    if getattr(args, "quiet", False):
        return
    print(output.render(payload, getattr(args, "format", "json")))


def _note(message: str, args: argparse.Namespace) -> None:
    """Print an informational (non-payload) line, suppressed by --quiet."""

    if not getattr(args, "quiet", False):
        print(message)


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=output.FORMATS,
        default="json",
        help="Output format for the structured result (default: json).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all output; only the exit code carries the outcome.",
    )


def _resolve_device(name: str | None) -> str:
    return name or get_default_device_name()


def _cmd_inventory(args: argparse.Namespace) -> int:
    result = list_devices()
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_check(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    result = CHECK_TOOLS[args.check](device)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_fabric(args: argparse.Namespace) -> int:
    result = check_fabric(args.check)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_route(args: argparse.Namespace) -> int:
    result = get_route(args.device, args.prefix)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_bgp_neighbor(args: argparse.Namespace) -> int:
    result = get_bgp_neighbor(args.device, args.address)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_interface(args: argparse.Namespace) -> int:
    result = get_interface(args.device, args.name)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_logging(args: argparse.Namespace) -> int:
    result = get_logging(args.device, args.count)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_ping(args: argparse.Namespace) -> int:
    result = ping_device(args.device, args.address)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_traceroute(args: argparse.Namespace) -> int:
    result = traceroute_device(args.device, args.address)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_analyze(args: argparse.Namespace) -> int:
    if args.fabric:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
        names = [device["name"] for device in listed["data"]["devices"]]
        evidence_by_device = {name: collect_evidence(name) for name in names}
        if args.save:
            for evidence in evidence_by_device.values():
                path = save_snapshot(evidence)
                print(f"# Snapshot saved: {path}")
        if args.show_evidence:
            print("# Evidence")
            print(output.render(evidence_by_device, "json"))
            print("\n# Analysis")
        try:
            result = analyze_fabric(evidence_by_device)
        except ValueError as exc:
            # Provider misconfiguration from get_provider(): nothing could be attempted.
            print(f"Analysis error: {exc}")
            return EXIT_CRITICAL
        except LLMAnalysisError as exc:
            print(f"Analysis error: {exc}")
            return EXIT_WARNING
        print(result["analysis"])
        if result["truncated"]:
            print(
                f"\n[Note: evidence was truncated for {len(result['truncated'])} "
                "section(s) to stay within the evidence budget; see the "
                "programmatic 'truncated' report for details.]"
            )
        return EXIT_OK

    device = _resolve_device(args.device)
    evidence = collect_evidence(device)
    if args.save:
        path = save_snapshot(evidence)
        print(f"# Snapshot saved: {path}")
    if args.show_evidence:
        print("# Evidence")
        print(output.render(evidence, "json"))
        print("\n# Analysis")
    try:
        print(analyze_evidence(evidence))
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Analysis error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Analysis error: {exc}")
        return EXIT_WARNING
    return EXIT_OK


def _cmd_agent(args: argparse.Namespace) -> int:
    try:
        result = run_agent_loop(
            args.question,
            device=args.device,
            max_iterations=args.max_iterations,
            time_budget_s=args.time_budget,
        )
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Agent error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Agent error: {exc}")
        return EXIT_WARNING

    print(result["answer"])
    print(
        f"\n[{result['iterations']} iteration(s), {len(result['tool_calls'])} tool call(s), "
        f"stopped_because={result['stopped_because']}]"
    )
    return EXIT_OK if result["stopped_because"] == "end_turn" else EXIT_WARNING


def _cmd_demo(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)

    print("## Agent Step 1: Discover devices")
    devices = list_devices()
    names = [d["name"] for d in devices.get("data", {}).get("devices", [])]
    print(f"Available devices: {', '.join(names)}")
    if device not in names:
        print(f"Device not found: {device}")
        return EXIT_CRITICAL

    print("\n## Agent Step 2: Collect approved evidence")
    evidence = collect_evidence(device)
    print("Facts, interfaces, BGP, LLDP, IS-IS, and SR-TE state collected.")

    print("\n## Agent Step 3: Analyze evidence")
    try:
        print(analyze_evidence(evidence))
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Analysis error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Analysis error: {exc}")
        return EXIT_WARNING
    return EXIT_OK


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
    _note(f"# Snapshot saved: {path}", args)
    if previous is None:
        _note(missing_message, args)
        return EXIT_OK

    result = diff_evidence(previous, current)
    _emit(result, args)

    # Unix `diff --exit-code`-style: 0 nothing differs, 1 real differences,
    # 2 the comparison itself is not trustworthy (an intent failed to collect
    # on one side or the other).
    if result["failed"]:
        return EXIT_CRITICAL
    if result["changed"] or result["added"] or result["removed"]:
        return EXIT_WARNING
    return EXIT_OK


def _cmd_baseline_pin(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    if args.from_latest:
        evidence = load_latest_snapshot(device)
        if evidence is None:
            print(
                f"No saved snapshot for {device} to pin; run `nettools diff {device}` "
                "first, or omit --from-latest to collect fresh evidence now."
            )
            return EXIT_WARNING
    else:
        evidence = collect_evidence(device)
        save_snapshot(evidence)

    path = save_golden_snapshot(evidence)
    print(f"# Golden snapshot pinned: {path}")
    return EXIT_OK


def _cmd_baseline_show(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    evidence = load_golden_snapshot(device)
    if evidence is None:
        _note(f"No golden snapshot pinned for {device}.", args)
        return EXIT_WARNING
    _emit(evidence, args)
    return EXIT_OK


def _cmd_flaps(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    result = detect_flaps(device, min_transitions=args.min_transitions)
    _emit(result, args)
    return EXIT_OK if not result["flapping"] else EXIT_WARNING


def _cmd_evidence_prune(args: argparse.Namespace) -> int:
    if args.keep_days is None and args.keep_count is None:
        _note("Nothing to do: pass --keep-days and/or --keep-count.", args)
        return EXIT_WARNING
    result = prune_snapshots(
        device_name=args.device, keep_days=args.keep_days, keep_count=args.keep_count
    )
    _emit(result, args)
    return EXIT_OK


def _cmd_evidence_history(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    history = list_snapshot_history(device)
    _emit({"device": device, "count": len(history), "history": history}, args)
    return EXIT_OK


def _cmd_health(args: argparse.Namespace) -> int:
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
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
    _emit(
        {
            "severity": result["severity"],
            "counts": result.get("counts", {}),
            "devices": reported,
            "suppressed": sorted(set(result["devices"]) - set(reported)),
        },
        args,
    )
    return exit_code_for_severity(result["severity"])


def _cmd_capture(args: argparse.Namespace) -> int:
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
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
    print(output.render({"label": args.label, "captures": captures}, "json"))
    # A partial capture must not look like a clean one.
    return EXIT_OK if all(not capture["errors"] for capture in captures) else EXIT_WARNING


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
        print(output.render(listed, "json"))
        return EXIT_CRITICAL
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
        print(output.render(derived, "json"))
        print()

    print(format_anomaly_report(build_anomaly_report(evidence_by_device)))
    return EXIT_OK


def _cmd_metrics(args: argparse.Namespace) -> int:
    """Report operational metrics: always exits 0 -- this is a report, not a check."""

    if args.quiet:
        return EXIT_OK
    if args.format == "prometheus":
        print(metrics.render_prometheus_text(), end="")
    else:
        print(metrics.render_json())
    return EXIT_OK


def _cmd_version(args: argparse.Namespace) -> int:
    import platform as platform_module

    payload = {
        "nettools_version": __version__,
        "python_version": platform_module.python_version(),
        "platform": platform_module.platform(),
    }
    _emit(payload, args)
    return EXIT_OK


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

                resources = await session.list_resources()
                print("\n=== RESOURCES EXPOSED BY THE SERVER ===")
                for resource in resources.resources:
                    print(f"  - {resource.uri}: {resource.description or ''}")

                prompts = await session.list_prompts()
                print("\n=== PROMPTS EXPOSED BY THE SERVER ===")
                for prompt in prompts.prompts:
                    print(f"  - {prompt.name}: {prompt.description or ''}")

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

                print(f"\n=== CALL: assess_lab_device_health (device_name={device}) ===")
                result = await session.call_tool(
                    "assess_lab_device_health", {"device_name": device}
                )
                for block in result.content:
                    print(getattr(block, "text", block))

    asyncio.run(run())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettools",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inventory = sub.add_parser("inventory", help="List devices without credentials.")
    _add_output_arguments(p_inventory)
    p_inventory.set_defaults(func=_cmd_inventory)

    for check in sorted(CHECK_TOOLS):
        p = sub.add_parser(check, help=f"Run the {check} check on a device.")
        p.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
        _add_output_arguments(p)
        p.set_defaults(func=_cmd_check, check=check)

    p_fabric = sub.add_parser("fabric", help="Run a check across the whole inventory.")
    p_fabric.add_argument(
        "check", nargs="?", default="bgp", choices=sorted(CHECK_TOOLS), help="Check to run."
    )
    _add_output_arguments(p_fabric)
    p_fabric.set_defaults(func=_cmd_fabric)

    p_route = sub.add_parser("route", help="Look up a specific route (validated template).")
    p_route.add_argument("device", help="Device name.")
    p_route.add_argument("prefix", help="IPv4 address or prefix, e.g. 10.0.0.0/24.")
    _add_output_arguments(p_route)
    p_route.set_defaults(func=_cmd_route)

    p_bgp_neighbor = sub.add_parser(
        "bgp-neighbor", help="Look up a specific BGP neighbor (validated template)."
    )
    p_bgp_neighbor.add_argument("device", help="Device name.")
    p_bgp_neighbor.add_argument("address", help="Neighbor IPv4 address.")
    _add_output_arguments(p_bgp_neighbor)
    p_bgp_neighbor.set_defaults(func=_cmd_bgp_neighbor)

    p_interface = sub.add_parser(
        "interface", help="Look up a specific interface's status (validated template)."
    )
    p_interface.add_argument("device", help="Device name.")
    p_interface.add_argument("name", help="Interface name, e.g. GigabitEthernet0/0/0/1.")
    _add_output_arguments(p_interface)
    p_interface.set_defaults(func=_cmd_interface)

    p_logging = sub.add_parser(
        "logging", help="Show recent log lines (validated template)."
    )
    p_logging.add_argument("device", help="Device name.")
    p_logging.add_argument(
        "--count", type=int, default=20, help="Number of log lines, 1-500 (default: 20)."
    )
    _add_output_arguments(p_logging)
    p_logging.set_defaults(func=_cmd_logging)

    p_ping = sub.add_parser(
        "ping",
        help="Ping an IPv4 address from a device (validated template; active probe -- see "
        "NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_ping.add_argument("device", help="Device name.")
    p_ping.add_argument("address", help="Target IPv4 address.")
    _add_output_arguments(p_ping)
    p_ping.set_defaults(func=_cmd_ping)

    p_traceroute = sub.add_parser(
        "traceroute",
        help="Traceroute to an IPv4 address from a device (validated template; active probe -- "
        "see NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_traceroute.add_argument("device", help="Device name.")
    p_traceroute.add_argument("address", help="Target IPv4 address.")
    _add_output_arguments(p_traceroute)
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

    p_diff = sub.add_parser(
        "diff",
        help="Diff current evidence against a saved snapshot. Exit codes: 0 no differences, "
        "1 differences found, 2 could not fully compare.",
    )
    p_diff.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_diff.add_argument(
        "--against",
        choices=("golden", "latest"),
        default="latest",
        help="Compare against the pinned golden snapshot or the most recent one (default: latest).",
    )
    _add_output_arguments(p_diff)
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
    _add_output_arguments(p_health)
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
    _add_output_arguments(p_baseline_show)
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
    _add_output_arguments(p_flaps)
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
    _add_output_arguments(p_evidence_prune)
    p_evidence_prune.set_defaults(func=_cmd_evidence_prune)

    p_evidence_history = evidence_sub.add_parser(
        "history", help="List a device's saved timestamped snapshots, oldest first."
    )
    p_evidence_history.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    _add_output_arguments(p_evidence_history)
    p_evidence_history.set_defaults(func=_cmd_evidence_history)

    p_metrics = sub.add_parser(
        "metrics",
        help="Report operational metrics: per-device collection outcomes/latency/retries and "
        "health verdict counts by severity. Always exits 0.",
    )
    p_metrics.add_argument(
        "--format",
        choices=("json", "prometheus"),
        default="json",
        help="Output form (default: json).",
    )
    p_metrics.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress output; still exits 0."
    )
    p_metrics.set_defaults(func=_cmd_metrics)

    p_version = sub.add_parser("version", help="Print the installed nettools version.")
    _add_output_arguments(p_version)
    p_version.set_defaults(func=_cmd_version)

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
        # Nothing could even be resolved (bad/missing inventory or
        # credentials) -- the worst-possible, "could not run at all" outcome.
        print(output.render({"status": "error", "errors": [str(exc)]}, "json"))
        return EXIT_CRITICAL


if __name__ == "__main__":
    raise SystemExit(main())
