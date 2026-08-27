#!/usr/bin/env python3
"""B-492 read-only characterization of SSH concurrency and retry behavior.

This script changes no network state. It records the behavior of the existing
admission and retry policy; it does not bypass either one. Same-device runs
therefore quantify admission refusal, while fabric runs characterize the
production-shaped distinct-device transport band.

Usage:
    NETTOOLS_LIVE_LAB=1 python scripts/concurrent_ssh_measurement.py \
      --runs 10 --levels 1,2,4 --mode both
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any, Literal

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

from agent_nettools.network_tools import collect_evidence  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
MeasurementMode = Literal["same-device", "fabric"]


def measurement_plan(*, mode: MeasurementMode, level: int, devices: tuple[str, ...]) -> tuple[str, ...]:
    """Return the exact device schedule for one bounded measurement block."""

    if level < 1:
        raise ValueError("concurrency level must be positive")
    if mode == "same-device":
        if not devices:
            raise ValueError("same-device measurement needs one device")
        return (devices[0],) * level
    if mode == "fabric":
        if len(devices) < level:
            raise ValueError("fabric measurement needs at least level distinct devices")
        return devices[:level]
    raise ValueError(f"unknown measurement mode: {mode!r}")


def summarize_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return distribution metrics without retaining evidence or device text."""

    values = tuple(records)
    durations = sorted(float(record["duration_seconds"]) for record in values)
    successes = sum(record.get("status") == "success" for record in values)
    retry_attempts = sum(int(record.get("retries", 0)) for record in values)
    recovered = sum(
        record.get("status") == "success" and int(record.get("retries", 0)) > 0
        for record in values
    )

    def percentile(fraction: float) -> float | None:
        if not durations:
            return None
        index = max(0, min(len(durations) - 1, int((len(durations) - 1) * fraction)))
        return round(durations[index], 3)

    attempts = len(values)
    failures = attempts - successes
    return {
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "failure_rate": round(failures / attempts, 4) if attempts else None,
        "retry_attempts": retry_attempts,
        "retry_recovery_rate": round(recovered / retry_attempts, 4) if retry_attempts else None,
        "status_counts": {
            status: sum(record.get("status") == status for record in values)
            for status in sorted({str(record.get("status")) for record in values})
        },
        "latency_seconds": {
            "min": round(min(durations), 3) if durations else None,
            "mean": round(statistics.mean(durations), 3) if durations else None,
            "p50": round(statistics.median(durations), 3) if durations else None,
            "p95": percentile(0.95),
            "max": round(max(durations), 3) if durations else None,
        },
    }


def _classify_result(evidence: dict[str, Any]) -> tuple[str, int]:
    sections = [value for value in evidence.values() if isinstance(value, dict) and "status" in value]
    if any(section.get("admission") for section in sections):
        return "admission_refused", 0
    if any(section.get("status") == "error" for section in sections):
        return "error", 0
    retries = 0
    for section in sections:
        data = section.get("data")
        if isinstance(data, dict):
            retries += int(data.get("retries", 0) or 0)
    return "success", retries


def _collect_once(device: str, collector: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        evidence = collector(device)
    except Exception as exc:  # noqa: BLE001 - measurement must preserve transport failure as data
        return {
            "device": device,
            "status": "exception",
            "duration_seconds": round(time.monotonic() - started, 3),
            "retries": 0,
            "error_class": type(exc).__name__,
        }
    status, retries = _classify_result(evidence)
    return {
        "device": device,
        "status": status,
        "duration_seconds": round(time.monotonic() - started, 3),
        "retries": retries,
    }


def run_block(
    *,
    mode: MeasurementMode,
    level: int,
    run: int,
    devices: tuple[str, ...],
    collector: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Run one concurrent block and return metadata-only observations."""

    schedule = measurement_plan(mode=mode, level=level, devices=devices)
    with concurrent.futures.ThreadPoolExecutor(max_workers=level) as executor:
        observations = tuple(executor.map(lambda device: _collect_once(device, collector), schedule))
    return tuple({"mode": mode, "level": level, "run": run, **observation} for observation in observations)


def _live_lab_available() -> str | None:
    if os.getenv("NETTOOLS_LIVE_LAB", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return "NETTOOLS_LIVE_LAB must be explicitly enabled"
    if not os.getenv("DEVICE_USERNAME") or not (os.getenv("DEVICE_PASSWORD") or os.getenv("DEVICE_SSH_KEYFILE")):
        return "DEVICE_USERNAME plus password or keyfile are required"
    return None


def _out_dir() -> pathlib.Path:
    output = REPO / "evidence-archive" / "concurrent-ssh" / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    return output


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--mode", choices=("same-device", "fabric", "both"), default="both")
    parser.add_argument("--devices", default="PE1,PE2,PE3,PE4")
    args = parser.parse_args(argv[1:])
    if args.runs < 5:
        parser.error("--runs must be at least 5 for B-492")
    levels = tuple(int(value) for value in args.levels.split(",") if value.strip())
    if not levels or any(level < 1 for level in levels):
        parser.error("--levels must contain positive integers")
    devices = tuple(value.strip() for value in args.devices.split(",") if value.strip())
    reason = _live_lab_available()
    output = _out_dir()
    if reason is not None:
        (output / "SKIPPED.json").write_text(json.dumps({"skipped": True, "reason": reason}, indent=2))
        print(f"SKIPPED: {reason}")
        return 0

    selected_modes: tuple[MeasurementMode, ...] = (
        ("same-device", "fabric") if args.mode == "both" else (args.mode,)
    )
    records: list[dict[str, Any]] = []
    for mode in selected_modes:
        for level in levels:
            for run in range(1, args.runs + 1):
                records.extend(run_block(mode=mode, level=level, run=run, devices=devices, collector=collect_evidence))

    (output / "records.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))
    summary = {
        "runs": args.runs,
        "levels": levels,
        "modes": selected_modes,
        "groups": {
            f"{mode}:{level}": summarize_records(
                record for record in records if record["mode"] == mode and record["level"] == level
            )
            for mode in selected_modes for level in levels
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))