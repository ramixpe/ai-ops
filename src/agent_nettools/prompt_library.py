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

__all__ = ["PROMPTS_DIR", "build_report_prompt", "descent_payload", "load_prompt"]

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


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


def build_report_prompt(result: DescentResult, *, version: int = 1) -> str:
    """Render the report prompt for one descent."""

    template = load_prompt("report", version)
    payload = json.dumps(descent_payload(result), indent=2)
    # str.replace, not str.format: the prompt contains literal JSON braces in
    # its anchor and expected-output blocks, and format() would try to read
    # every one of them as a field.
    return template.replace("{descent_json}", payload)
