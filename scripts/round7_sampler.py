#!/usr/bin/env python3
"""Round 7 (B-462): does a down port persist in the route table as an LFA backup?

Free-running, read-only, and **it never touches the fault** — §0.11 is absolute
and the operator is the injector.

Samples three things per iteration, on the device under test:

* every path `show route <origin>/32` names, with its interface and `path_role`
* every physical port's admin and line state
* the member set `descent.path_interfaces` derives from the two

The third is the point. The first two are what the device says; the third is
what the shipped code *concludes*, and the question is whether it can conclude a
dead port is a live path.

Archives the full payload per sample (§6.1d) — a finding cannot validate a
change to the layer that produced it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

from agent_nettools.descent import path_interfaces  # noqa: E402
from agent_nettools.network_tools import (  # noqa: E402
    collect_evidence_and_templates,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="PE2", help="the device the fault is applied to")
    ap.add_argument("--origin", default="10.255.0.31", help="loopback the route points at")
    ap.add_argument("--seconds", type=float, default=180.0)
    # `evidence-archive/`, not `evidence/`: the latter is gitignored as a
    # snapshot cache, so a round written there is one `git clean` from gone
    # (OBS-131). Archiving means committed.
    ap.add_argument("--out", default="evidence-archive/round7/samples.jsonl")
    args = ap.parse_args()

    prefix = f"{args.origin}/32"
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    n = 0
    print(f"  sampling {args.device} route->{prefix} for {args.seconds:.0f}s; Ctrl-C to stop\n")
    print(f"  {'t':>7}  {'route names':28}  {'down ports':20}  member set")

    with out.open("a", encoding="utf-8") as fh:
        while time.monotonic() - started < args.seconds:
            n += 1
            at = round(time.monotonic() - started, 2)
            try:
                evidence, envelopes = collect_evidence_and_templates(
                    args.device, [("route", {"prefix": prefix})]
                )
                evidence = dict(evidence)
                if envelopes:
                    evidence[f"route:{prefix}"] = envelopes[0]

                parsed = ((evidence.get(f"route:{prefix}") or {}).get("data") or {}).get("parsed") or {}
                paths = [
                    {"interface": r.get("interface"), "role": r.get("path_role"),
                     "next_hop": r.get("next_hop")}
                    for r in parsed.get("records") or []
                ]
                ifaces = ((evidence.get("interfaces") or {}).get("data") or {}).get("parsed") or {}
                states = {
                    r.get("interface"): (r.get("admin_state"), r.get("line_state"))
                    for r in ifaces.get("records") or []
                }
                members, unresolved = path_interfaces(evidence, prefix)

                row = {
                    "sample": n, "t": at, "device": args.device, "prefix": prefix,
                    "route_found": (parsed.get("meta") or {}).get("found"),
                    "paths": paths, "interface_states": states,
                    "derived_member_set": members, "unresolved": unresolved,
                }
            except Exception as exc:  # noqa: BLE001 -- a sampler must not stop
                row = {"sample": n, "t": at, "error": f"{type(exc).__name__}: {exc}"}

            fh.write(json.dumps(row) + "\n")
            fh.flush()

            named = ",".join(p["interface"] or "?" for p in row.get("paths", [])) or "-"
            down = ",".join(
                k for k, (admin, line) in (row.get("interface_states") or {}).items()
                if "down" in str(admin).lower() or "down" in str(line).lower()
            ) or "-"
            print(f"  {at:7.1f}  {named[:28]:28}  {down[:20]:20}  {row.get('derived_member_set')}")

    print(f"\n  {n} samples -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
