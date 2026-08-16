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

B-421 -- the split that makes prompt caching possible
--------------------------------------------------------
Both builders used to return one fully-rendered string, with the template's
fixed instructions and the substituted payload already interleaved. Anthropic's
prompt cache is a prefix match on `system` (see `llm_analysis.py`'s module
docstring), so a string of that shape cached nothing: the "static" prefix
differed on every call the moment the payload changed, because the payload
was *inside* the prefix.

`build_report_prompt`/`build_correlate_prompt` now return a `RenderedPrompt`:
`system` is every byte of the template that is not a substituted value --
identical across every investigation at a given prompt version, and therefore
the part `llm_analysis.complete_prompt` can hand to Anthropic as a cacheable
block -- and `user` is the substituted payload alone.

The templates themselves are not touched to make this true. `report.v1.txt`
and `correlate.v3.txt` both put their payload placeholder(s) in the middle of
the GRACE slots (the payload sits inside GROUNDING, with ROLE/ANCHORS/
CONSTRAINTS/EXPECTED OUTPUT after it), so the split is done here, in code, by
`_split_template`: it walks the template left to right and buckets every span
by kind -- literal template text is static, a substituted value is volatile --
preserving each bucket's own relative order. That is a reordering, not a
rewrite: every character of the template and every substituted value still
appears exactly once, so nothing the model is told changes, only whether it
arrives in the cacheable prefix or the per-call message. Doing this in code
rather than as a new prompt version is deliberate: `prompts/README.md` rule 2
makes a version bump mean *the wording changed*, and here it has not --
bumping the version for a pure code-side reordering would misrepresent what
changed to whoever reads the version history next.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .descent import DescentResult
from .log_window import ShapedWindow

__all__ = [
    "CURRENT_VERSION",
    "PROMPTS_DIR",
    "RenderedPrompt",
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
#: `correlate` is at 3: v1's grounding text described a noise filter that
#: dropped whole facilities, and v2 had no coverage slot, so its refusal claimed
#: a negative the source could not support. Superseded versions stay in the tree
#: as the record of what was reviewed when.
CURRENT_VERSION: dict[str, int] = {"report": 1, "correlate": 3}


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


@dataclass(frozen=True)
class RenderedPrompt:
    """A rendered prompt, already split at the cache boundary (B-421).

    ``system`` is the static half: every byte of the template that is not a
    substituted value, so it is byte-identical for every investigation run at
    this prompt version -- the prefix Anthropic's cache can actually hit.
    ``user`` is the volatile half: the substituted payload for this one
    investigation.

    Deliberately just these two fields, not a richer object -- everything
    downstream (``llm_analysis.complete_prompt``, the OpenAI/MiniMax/Ollama
    concatenation fallback) only ever needs "the cacheable part" and "the
    part that changes every call".
    """

    system: str
    user: str


def _split_template(template: str, substitutions: list[tuple[str, str]]) -> RenderedPrompt:
    """Partition one rendered template into its static and volatile halves.

    ``substitutions`` is an ordered list of ``(placeholder, value)`` pairs, in
    the order the placeholders actually appear in ``template`` -- required,
    because this walks the template left to right with `str.partition`,
    consuming one placeholder at a time from what is left of the text. Getting
    the order wrong would leave an earlier placeholder's literal `{...}` text
    stranded, unsubstituted, inside a `static_parts` span.

    Every span of literal template text between (or before/after) a
    placeholder is static -- identical on every call at this template version
    -- and is appended to ``system`` in the template's own order. Every
    substituted value is volatile and is appended to ``user``, also in order,
    separated by a blank line for readability where more than one payload is
    substituted (`correlate` substitutes three; the blank line is whitespace
    only, not a change to any value's content).

    This is why the reordering the module docstring describes is safe: no
    template character and no substituted value is ever dropped, duplicated,
    or reworded here -- each one is simply relocated to whichever of the two
    output strings its own kind (static template text vs. substituted value)
    belongs to, in the relative order it already had among spans of that kind.
    """

    static_parts: list[str] = []
    volatile_parts: list[str] = []
    remaining = template
    for placeholder, value in substitutions:
        before, found, remaining = remaining.partition(placeholder)
        if not found:
            raise ValueError(f"placeholder {placeholder!r} not found in template")
        static_parts.append(before)
        volatile_parts.append(value)
    static_parts.append(remaining)
    return RenderedPrompt(system="".join(static_parts), user="\n\n".join(volatile_parts))


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


def build_report_prompt(result: DescentResult, *, version: int | None = None) -> RenderedPrompt:
    """Render the report prompt for one descent, split at the cache boundary.

    ``report.v1.txt`` has one placeholder, `{descent_json}`, sitting inside
    GROUNDING with ROLE/ANCHORS/CONSTRAINTS/EXPECTED OUTPUT after it --
    `_split_template` (not `str.format`, for the same reason as before: the
    prompt contains literal JSON braces in its anchor and expected-output
    blocks that `format()` would misread as fields) does the reordering, in
    code, that turns that into a static `system` half and a volatile `user`
    half. See the module docstring's B-421 section.
    """

    template = load_prompt("report", version or CURRENT_VERSION["report"])
    payload = json.dumps(descent_payload(result), indent=2)
    return _split_template(template, [("{descent_json}", payload)])


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
) -> RenderedPrompt:
    """Render the correlate prompt for one finding and one shaped window,
    split at the cache boundary.

    The window arrives already filtered by :func:`log_window.shape_window`, and
    its removal counts travel with it -- so the model can say how much of the
    window it is seeing rather than presenting a filtered set as the whole.

    ``correlate.v3.txt`` substitutes three placeholders inside GROUNDING, in
    this order: `{coverage_json}` (COVERAGE), `{finding_json}` (FINDING), then
    `{window_json}` (LOG WINDOW). `_split_template`'s ``substitutions`` list
    below must name them in that same order -- it consumes the template left
    to right, one placeholder at a time, so an out-of-order list would leave
    an earlier placeholder's literal `{...}` text stranded inside a static
    span instead of substituted. See the module docstring's B-421 section.
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
    coverage = (
        window.coverage.as_dict()
        if window.coverage is not None
        else {"complete": False,
              "gaps": ["no coverage record was produced for this window"]}
    )
    return _split_template(template, [
        ("{coverage_json}", json.dumps(coverage, indent=2)),
        ("{finding_json}", json.dumps(finding_payload(result), indent=2)),
        ("{window_json}", json.dumps(window_payload, indent=2)),
    ])
