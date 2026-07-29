"""Single command-line entry point for the read-only IOS-XR network tools.

Exposed as the ``nettools`` console script. Subcommands:

    nettools inventory
    nettools facts | interfaces | bgp | lldp | isis | sr  [DEVICE]
    nettools fabric [CHECK]
    nettools analyze [DEVICE] [--show-evidence] [--save]
    nettools demo [DEVICE]
    nettools diff [DEVICE]
    nettools capture [DEVICE ...] [--all] [--label t0] [--out DIR] [--no-scrub]
    nettools learn-topology [--from-fixtures|--live] [--label t0] [--out PATH] [--write]
    nettools inspect [DEVICE]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .fixtures import capture_device, load_fixture_evidence
from .inventory import InventoryError, get_default_device_name
from .inventory_model import resolve_inventory_path
from .llm_analysis import LLMAnalysisError, analyze_evidence
from .network_tools import (
    CHECK_TOOLS,
    check_fabric,
    collect_evidence,
    diff_evidence,
    list_devices,
    load_latest_snapshot,
    save_snapshot,
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


def _cmd_analyze(args: argparse.Namespace) -> int:
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
    previous = load_latest_snapshot(device)
    current = collect_evidence(device)
    path = save_snapshot(current)
    print(f"# Snapshot saved: {path}")
    if previous is None:
        print(f"No previous snapshot for {device}; baseline established.")
        return 0
    _print(diff_evidence(previous, current))
    return 0


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

    p_analyze = sub.add_parser("analyze", help="Collect evidence and analyze with the LLM.")
    p_analyze.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_analyze.add_argument("--show-evidence", action="store_true", help="Print evidence first.")
    p_analyze.add_argument("--save", action="store_true", help="Save an evidence snapshot.")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_demo = sub.add_parser("demo", help="Run the narrated agent demo.")
    p_demo.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_demo.set_defaults(func=_cmd_demo)

    p_diff = sub.add_parser("diff", help="Diff current evidence against the last snapshot.")
    p_diff.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
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
