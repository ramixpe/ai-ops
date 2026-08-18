#!/usr/bin/env python3
"""Run a recorded transcript through `model_eval.py` and print a score card. B-494.

`model_eval.py` scores pure `(payload, prose)`/`(question, transcript)` pairs
and calls nothing -- no model, no network, no device. This script is the thin
transport-facing wrapper the module's own docstring asks for: it reads a
transcript file, builds the typed objects, and prints what came back. Every
ground-truth payload it scores against still comes from `--from-fixtures`
replay, so running this script needs no lab, no credentials and no API key,
same as the module it wraps.

Transcript file format -- one JSON object, keyed by `EvalQuestion.id`
(`healthy_why`, `healthy_summary`, `healthy_self_report`, `broken_confirm`;
see `model_eval.EVAL_QUESTIONS` for the full, current list and what each one
scores):

    {
      "healthy_why": {
        "tool_calls": [{"tool": "investigate_lab_session",
                         "args": {"device": "RR1", "subject": "10.255.0.12"}}],
        "prose": "RR1 can actually reach 10.255.0.12. ..."
      },
      "healthy_summary": {"prose": "..."},
      "healthy_self_report": {"prose": "..."},
      "broken_confirm": {"tool_calls": [...], "prose": "..."}
    }

A question with no entry in the file scores as an empty `ModelTranscript` --
no tool calls, no prose -- which reads as a miss on every applicable
dimension rather than crashing, so a partial transcript still produces a full
score card.

Usage:
    python scripts/model_eval_report.py path/to/transcript.json
"""

from __future__ import annotations

import json
import sys

from agent_nettools import model_eval as me


def _load_transcripts(path: str) -> dict[str, me.ModelTranscript]:
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    transcripts: dict[str, me.ModelTranscript] = {}
    for question_id, entry in raw.items():
        tool_calls = tuple(
            me.ToolCall(tool=call["tool"], args=call.get("args", {}))
            for call in entry.get("tool_calls", [])
        )
        transcripts[question_id] = me.ModelTranscript(
            tool_calls=tool_calls, prose=entry.get("prose", "")
        )
    return transcripts


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <transcript.json>", file=sys.stderr)
        return 2

    transcripts = _load_transcripts(argv[1])
    cards = me.score_all(transcripts)
    print(me.render_report(cards))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
