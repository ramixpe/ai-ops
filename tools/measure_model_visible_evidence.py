#!/usr/bin/env python3
"""Measure model-visible evidence after projection and budgeting, without a model call."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_nettools.evidence_budget import budget_device_evidence  # noqa: E402
from agent_nettools.fixtures import load_fixture_evidence  # noqa: E402
from agent_nettools.model_egress import project_evidence  # noqa: E402


def _command_chars(section: dict[str, Any]) -> int:
    commands = section.get("data", {}).get("commands", {})
    return sum(len(value) for value in commands.values() if isinstance(value, str))


def _withheld_command_chars(section: dict[str, Any]) -> int:
    commands = section.get("data", {}).get("commands_withheld", {})
    return sum(
        value.get("chars", 0)
        for value in commands.values()
        if isinstance(value, dict) and isinstance(value.get("chars"), int)
    )


def report(device: str, label: str, *, per_intent_chars: int | None = None) -> dict[str, Any]:
    """Measure one fixture-backed evidence collection at the model boundary."""

    evidence = load_fixture_evidence(device, label=label)
    projected = project_evidence(evidence)
    budgeted, truncation = budget_device_evidence(
        device, evidence, per_intent_chars=per_intent_chars
    )
    sections: dict[str, dict[str, Any]] = {}
    for intent, source in evidence.items():
        if not isinstance(source, dict):
            continue
        safe = projected.get(intent, {})
        data = source.get("data", {})
        parsed = data.get("parsed") if isinstance(data, dict) else None
        parsed_meta = parsed.get("meta", {}) if isinstance(parsed, dict) else {}
        sections[intent] = {
            "parse_status": data.get("parse_status") if isinstance(data, dict) else None,
            "raw_command_chars": _command_chars(source),
            "withheld_command_chars": _withheld_command_chars(safe),
            "evidence_accounting": parsed_meta.get("evidence_accounting") or None,
            "projected_chars": len(json.dumps(safe, sort_keys=True)),
            "budgeted_chars": len(budgeted[intent]),
            "budget_truncated_chars": next(
                (
                    entry["omitted_chars"]
                    for entry in truncation
                    if entry["intent"] == intent
                ),
                0,
            ),
        }
    return {
        "device": device,
        "label": label,
        "model_call_made": False,
        "sections": sections,
        "totals": {
            "raw_command_chars": sum(section["raw_command_chars"] for section in sections.values()),
            "withheld_command_chars": sum(section["withheld_command_chars"] for section in sections.values()),
            "projected_chars": sum(section["projected_chars"] for section in sections.values()),
            "budgeted_chars": sum(section["budgeted_chars"] for section in sections.values()),
            "budget_truncated_chars": sum(section["budget_truncated_chars"] for section in sections.values()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="RR1")
    parser.add_argument("--label", default="broken")
    parser.add_argument("--per-intent-chars", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = report(args.device, args.label, per_intent_chars=args.per_intent_chars)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result["totals"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())