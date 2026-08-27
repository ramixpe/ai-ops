#!/usr/bin/env python3
"""B-206b passive attribution of router-bound SSH connection churn.

The script only observes TCP SYN packets destined for inventory router SSH
ports and snapshots Docker network metadata. It never opens SSH, changes a
container, or changes a router.

Usage:
    python scripts/ssh_churn_attribution.py --capture-file /path/to/tcpdump.txt
    python scripts/ssh_churn_attribution.py --interface br-... --seconds 600
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

from agent_nettools.inventory import load_inventory  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
_SYN = re.compile(
    r"^(?P<timestamp>\d+(?:\.\d+)?) IP (?P<source>\d+\.\d+\.\d+\.\d+)\.\d+ "
    r"> (?P<destination>\d+\.\d+\.\d+\.\d+)\.22: Flags \[S\]"
)


def parse_syn_records(lines: Iterable[str], *, router_addresses: frozenset[str]) -> tuple[dict[str, Any], ...]:
    """Parse passive tcpdump output into metadata-only router SSH SYN records."""

    records: list[dict[str, Any]] = []
    for line in lines:
        match = _SYN.match(line.strip())
        if match is None or match["destination"] not in router_addresses:
            continue
        records.append(
            {
                "timestamp": float(match["timestamp"]),
                "source_ip": match["source"],
                "destination_ip": match["destination"],
            }
        )
    return tuple(records)


def summarize_syn_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize captured SYN metadata without process or packet payloads."""

    rows = tuple(records)
    timestamps = [float(row["timestamp"]) for row in rows]
    sources = collections.Counter(str(row["source_ip"]) for row in rows)
    destinations = collections.Counter(str(row["destination_ip"]) for row in rows)
    return {
        "attempts": len(rows),
        "window_seconds": round(max(timestamps) - min(timestamps), 3) if len(timestamps) > 1 else 0.0,
        "sources": dict(sorted(sources.items())),
        "destinations": dict(sorted(destinations.items())),
    }


def _docker_network_snapshot() -> dict[str, Any]:
    """Return container names and addresses for correlation, never env values."""

    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False
    )
    if completed.returncode:
        return {"available": False, "error": "docker ps failed"}
    containers = [name for name in completed.stdout.splitlines() if name]
    snapshot: dict[str, dict[str, list[str]]] = {}
    for name in containers:
        inspected = subprocess.run(
            ["docker", "inspect", name, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspected.returncode == 0:
            snapshot[name] = {"addresses": sorted(set(inspected.stdout.split()))}
    return {"available": True, "containers": snapshot}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture-file", type=pathlib.Path)
    source.add_argument("--interface")
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args(argv[1:])
    if args.seconds < 1:
        parser.error("--seconds must be positive")

    router_addresses = frozenset(str(device["hostname"]) for device in load_inventory())
    capture: dict[str, Any] = {"source": "file" if args.capture_file is not None else "tcpdump"}
    if args.capture_file is not None:
        lines = args.capture_file.read_text(encoding="utf-8", errors="replace").splitlines()
        capture["returncode"] = 0
        capture["lines"] = len(lines)
    else:
        command = [
            "tcpdump", "-tt", "-n", "-i", args.interface, "-c", "10000",
            "tcp[tcpflags] & tcp-syn != 0 and dst port 22",
        ]
        print(f"passively capturing router-bound SSH SYNs for up to {args.seconds} seconds", file=sys.stderr)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=args.seconds, check=False)
            lines = completed.stdout.splitlines()
            capture.update(
                {
                    "returncode": completed.returncode,
                    "lines": len(lines),
                    "diagnostic": completed.stderr.strip()[-500:],
                }
            )
        except subprocess.TimeoutExpired as exc:
            lines = (exc.stdout or "").splitlines() if isinstance(exc.stdout, str) else []
            capture.update({"returncode": None, "lines": len(lines), "diagnostic": "capture timeout reached"})

    records = parse_syn_records(lines, router_addresses=router_addresses)
    output = REPO / "evidence-archive" / "ssh-churn" / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    (output / "records.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))
    (output / "summary.json").write_text(
        json.dumps(
            {"capture": capture, "summary": summarize_syn_records(records), "docker": _docker_network_snapshot()},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"wrote {output}")
    if args.capture_file is None and capture["returncode"] not in {0, None}:
        print("capture failed; inspect summary.json diagnostic before interpreting zero records", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))