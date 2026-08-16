"""Load versioned prompts and render them from a descent result.

The prompts themselves live in ``prompts/`` as versioned text files, reviewed
like code. This module is the loader and the renderer: it turns a
:class:`descent.DescentResult` into the exact string a model receives.

Invariant 4 is enforced here, structurally
-------------------------------------------
`BUILD-PLAN.md` §0.6: **no unparsed device text is ever passed to a model.**

:func:`build_report_prompt` cannot violate that even by mistake, because it
does not receive device output. It receives a ``DescentResult``, which holds
verdicts, reasons and evidence keys — all produced by checks over already-parsed
records — and it serialises exactly those fields. There is no path from a raw
command string to the rendered prompt, and
``test_the_rendered_prompt_carries_no_raw_device_output`` pins it.

That is deliberately a structural guarantee rather than a filtering one. A
redaction pass over text that might contain raw output is something somebody
eventually gets wrong; a function that never holds the text cannot.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .descent import DescentResult
from .log_window import ShapedWindow

__all__ = [
    "CURRENT_VERSION",
    "PROMPTS_DIR",
    "build_correlate_prompt",
    "build_report_prompt",
    "descent_payload",
    "finding_payload",
    "load_prompt",
]

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

#: The version each builder uses when a caller does not name one.
#:
#: A single reviewable table rather than a default buried in six signatures.
#: `prompts/README.md` rule 2 says a prompt change is a version bump and the
#: caller names the version — this is where "the current one" is decided, so
#: superseding a prompt is one line in a diff someone reads, not a default that
#: drifted.
#:
#: `correlate` is at 2 because v1's grounding text describes a noise filter that
#: dropped whole facilities, which is no longer what the code does. v1 stays in
#: the tree as the record of what was reviewed at T-028.
CURRENT_VERSION: dict[str, int] = {"report": 1, "correlate": 2}


class PromptNotFoundError(FileNotFoundError):
    """A named prompt version does not exist."""


@lru_cache(maxsize=None)
def load_prompt(name: str, version: int = 1) -> str:
    """Load ``prompts/<name>.v<version>.txt``.

    Versioned rather than named, because a prompt is an input to a system whose
    output someone acts on: a report has to be attributable to the exact text
    that produced it. Silently editing a prompt in place makes every earlier
    report unreproducible.
    """

    path = PROMPTS_DIR / f"{name}.v{version}.txt"
    if not path.is_file():
        raise PromptNotFoundError(
            f"no prompt {name!r} version {version} at {path}; "
            "prompts are versioned files, never edited in place"
        )
    return path.read_text(encoding="utf-8")


def descent_payload(result: DescentResult) -> dict:
    """The descent, reduced to what a report may be written from.

    Every field here is a verdict, a reason, a rung name, a device name or an
    evidence key. **No command output, parsed or otherwise, is included** — the
    model is being asked to render an argument that has already been made, not
    to look at evidence and form one.
    """

    return {
        "flow": result.flow,
        "device": result.device,
        "subject": result.subject,
        "finding": result.finding,
        "reason": result.reason,
        "rungs": [
            {
                "rung": outcome.rung,
                "device": outcome.device,
                "status": outcome.status,
                "reason": outcome.result.reason,
                "evidence_keys": list(outcome.result.evidence_keys),
            }
            for outcome in result.outcomes
        ],
        "cause": (
            {
                "rung": result.cause.rung,
                "device": result.cause.device,
                "reason": result.cause.result.reason,
            }
            if result.cause is not None
            else None
        ),
        "causal_chain": [
            {"rung": o.rung, "device": o.device, "reason": o.result.reason}
            for o in result.causal_chain
        ],
        "evidence_keys": list(result.evidence_keys),
    }


def build_report_prompt(result: DescentResult, *, version: int | None = None) -> str:
    """Render the report prompt for one descent."""

    template = load_prompt("report", version or CURRENT_VERSION["report"])
    payload = json.dumps(descent_payload(result), indent=2)
    # str.replace, not str.format: the prompt contains literal JSON braces in
    # its anchor and expected-output blocks, and format() would try to read
    # every one of them as a field.
    return template.replace("{descent_json}", payload)


def finding_payload(result: DescentResult) -> dict:
    """The finding alone, for correlation. No chain, no rung detail.

    Correlation asks *when*, not *why* -- the descent already settled why. A
    payload carrying the full chain would invite the model to re-litigate the
    diagnosis against log text, which is the one thing the deterministic walk
    exists to stop.
    """

    return {
        "flow": result.flow,
        "device": result.device,
        "subject": result.subject,
        "finding": result.finding,
        "cause": (
            {"rung": result.cause.rung, "device": result.cause.device}
            if result.cause is not None
            else None
        ),
    }


def build_correlate_prompt(
    result: DescentResult, window: ShapedWindow, *, version: int | None = None
) -> str:
    """Render the correlate prompt for one finding and one shaped window.

    The window arrives already filtered by :func:`log_window.shape_window`, and
    its removal counts travel with it -- so the model can say how much of the
    window it is seeing rather than presenting a filtered set as the whole.
    """

    template = load_prompt("correlate", version or CURRENT_VERSION["correlate"])
    window_payload = {
        "entries": [
            {
                "at": record.get("timestamp"),
                "mnemonic": record.get("mnemonic"),
                "severity": record.get("severity"),
                "text": record.get("text"),
            }
            for record in window.records
        ],
        "shaping": window.summary(),
        "entries_shown": len(window.records),
        "entries_collected": window.total_in,
    }
    return template.replace("{finding_json}", json.dumps(finding_payload(result), indent=2)).replace(
        "{window_json}", json.dumps(window_payload, indent=2)
    )
