#!/usr/bin/env python3
"""Round 5's sampler: invoke the descent continuously through a propagation window.

Free-running with no gap between samples, so every instant in the window falls
inside some run's observation window. A sparse sample would miss the transition,
and the transition is the whole point of the round.

Read-only. It runs `investigate(... --no-model)` in a loop and writes one JSON
line per sample. **It never touches the fault** -- §0.11 is absolute, the
operator applies and restores.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

from agent_nettools.investigation import investigate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="RR1")
    ap.add_argument("--subject", default="10.255.0.12")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--out", default="evidence/round5/samples.jsonl")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    n = 0
    with out.open("a", encoding="utf-8") as fh:
        while time.monotonic() - started < args.seconds:
            n += 1
            at = time.monotonic() - started
            wall = time.strftime("%H:%M:%S", time.gmtime())
            try:
                result = investigate(args.device, args.subject)
                payload = result.to_payload()
                row = {
                    "sample": n,
                    "t": round(at, 2),
                    "wall_utc": wall,
                    "finding": payload["finding"],
                    "trustworthy": payload["trustworthy"],
                    "coherence": payload["coherence"],
                    "rungs": [
                        {"rung": r["rung"], "device": r["device"], "status": r["status"]}
                        for r in payload["rungs"]
                    ],
                }
            except Exception as exc:  # noqa: BLE001 -- a sampler must not stop
                row = {"sample": n, "t": round(at, 2), "wall_utc": wall, "error": str(exc)}

            fh.write(json.dumps(row) + "\n")
            fh.flush()

            rungs = "".join(
                {"healthy": ".", "broken": "X", "unevaluated": "?"}.get(r["status"], "-")
                for r in row.get("rungs", [])
            )
            skew = (row.get("coherence") or {}).get("skew_seconds", "-")
            print(
                f"  {row['t']:6.1f}s  {rungs:5}  {row.get('finding', 'ERROR'):22}"
                f"  skew={skew}",
                flush=True,
            )

    print(f"\n  {n} samples -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
