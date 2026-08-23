"""Load versioned prompts and render them from a descent result.

The prompts themselves live in ``prompts/`` as versioned text files, reviewed
like code. This module is the loader and the renderer: it turns a
:class:`descent.DescentResult` into the exact string a model receives.

Invariant 4 is enforced here, structurally
-------------------------------------------
`docs/build/PROCESS.md` §0.6: **no unparsed device text is ever passed to a model.**

:func:`build_report_prompt` cannot violate that even by mistake, because it
does not receive device output. It receives a ``DescentResult``, which holds
verdicts, reasons and evidence keys — all produced by checks over already-parsed
records — and it serialises exactly those fields. There is no path from a raw
command string to the rendered prompt, and
``test_the_rendered_prompt_carries_no_raw_device_output`` pins it.

That is deliberately a structural guarantee rather than a filtering one. A
redaction pass over text that might contain raw output is something somebody
eventually gets wrong; a function that never holds the text cannot.

:func:`build_correlate_prompt` is different, on purpose, and is not covered
by the guarantee above: it receives a :class:`~agent_nettools.log_window.ShapedWindow`
*because* correlation needs the log line's own text -- "when did it happen"
and "did it coincide with anything else" cannot be answered from verdicts
alone. DEEP-REVIEW-2026-08-17 §2.1 measured this precisely: 28 of 28 shaped
window records had their ``text`` field embedded verbatim, unmarked, into
this prompt -- the deterministic path's own correlate/paraphrase call, not
only the exploratory `analyze`/`agent` paths. Since B-467/B-470, every
record's ``text`` is wrapped with :func:`model_egress.quote_device_text`
before it reaches ``window_payload`` below, and the template's GROUNDING
section names the delimiters and tells the model not to follow instructions
found inside them. That is a mitigation for prompt steering, not a
structural guarantee like `build_report_prompt`'s -- see `model_egress.py`'s
module docstring for why the two are different claims.

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
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any

from .descent import DescentResult
from .log_window import ShapedWindow
from .model_egress import quote_device_text

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

# The source-tree location, three parents up from this file
# (src/agent_nettools/prompt_library.py -> repo root -> prompts/). Kept as a
# plain `Path`, and kept in `__all__` unchanged, deliberately: this constant
# is public API (reviewed prompts are meant to be walkable on disk by anyone
# working in a checkout), so widening it to a `Traversable` would be a type
# change for every existing caller/test that treats it as a `Path`. It is
# correct for a source checkout (editable install or a clone) and simply does
# not exist in an installed wheel with no source tree nearby -- see
# `_packaged_prompts_dir` and EER-003 below for the fallback that covers that
# case without touching this constant's type or meaning.
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

#: The version each builder uses when a caller does not name one.
#:
#: A single reviewable table rather than a default buried in six signatures.
#: `prompts/README.md` rule 2 says a prompt change is a version bump and the
#: caller names the version — this is where "the current one" is decided, so
#: superseding a prompt is one line in a diff someone reads, not a default that
#: drifted.
#:
#: `correlate` is at 4: v1's grounding text described a noise filter that
#: dropped whole facilities, v2 had no coverage slot, so its refusal claimed
#: a negative the source could not support, and v3 added constraint 7 and the
#: coverage slot. v4 (B-467/B-470) adds the untrusted-device-text grounding
#: paragraph naming the quote delimiters `window_json` entries are now
#: wrapped in -- see `prompts/README.md`'s version history. Superseded
#: versions stay in the tree as the record of what was reviewed when.
CURRENT_VERSION: dict[str, int] = {
    "report": 2,
    "correlate": 4,
    # Migrated out of Python 2026-08-21 at the operator's request -- every
    # prompt sent to a model is now reviewable as a file. All three landed
    # at v1 because they are byte-for-byte the strings that were already
    # in `llm_analysis.py`/`fabric_analysis.py`/`agent_loop.py`: a move,
    # not an edit, and versioning them higher would have implied a review
    # that never happened.
    "troubleshooting": 1,
    "fabric_analysis": 1,
    "agent_system": 1,
}


class PromptNotFoundError(FileNotFoundError):
    """A named prompt version does not exist."""


def _packaged_prompts_dir() -> Path:
    """The packaged fallback copy of the runtime-required prompts (EER-003).

    ``PROMPTS_DIR`` is three parents up from this file -- the repo root -- so
    it does not exist in an installed wheel with no source checkout nearby,
    and `load_prompt` would raise `PromptNotFoundError` for every caller,
    including the deterministic `investigate` path that runs with
    `--no-model` and touches no LLM at all (`build_report_prompt` is still
    reachable from a plain descent report). Follows the same precedent as
    `inventory_model._packaged_fallback_path`: `src/agent_nettools/data/
    prompts/*.txt` are checked-in byte-identical copies of every prompt file
    under the source-tree `prompts/` -- `load_prompt` accepts any version and
    superseded versions (`report.v1`, `correlate.v1..v3`) are kept reachable
    on purpose, not just the two named in `CURRENT_VERSION` -- declared in
    `pyproject.toml`'s `[tool.setuptools.package-data]`, resolved through
    `importlib.resources` so it works whether this is an editable or an
    installed copy. Kept honest by `tests/test_packaging_prompts.py`, which
    derives the required set from `prompts/*.txt` itself (not a hand-
    maintained list -- one of those went stale and shipped `event_agent.v1`
    with no packaged twin, docs/findings_gpt_23aug.md H1, closed 2026-08-23)
    and fails loudly if a packaged copy is missing or ever drifts from its
    source-tree original.

    `prompts/tests/cases/*.json` and `prompts/README.md` are test-only and
    are deliberately NOT packaged here -- nothing at runtime reads them.
    """

    return importlib_resources.files("agent_nettools") / "data" / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str, version: int = 1) -> str:
    """Load ``prompts/<name>.v<version>.txt``.

    Versioned rather than named, because a prompt is an input to a system whose
    output someone acts on: a report has to be attributable to the exact text
    that produced it. Silently editing a prompt in place makes every earlier
    report unreproducible.

    Resolution order (EER-003): the source-tree ``PROMPTS_DIR`` first -- so an
    operator's own edit to a prompt under active development is always what
    runs -- then the packaged fallback under ``importlib.resources``, which is
    the only copy that exists at all once this package is installed from a
    built wheel with no source checkout nearby. The cache key stays
    ``(name, version)``, unchanged: which of the two locations answered a
    given call is not part of what a caller can observe or needs to.
    """

    filename = f"{name}.v{version}.txt"
    source_path = PROMPTS_DIR / filename
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")

    packaged_path = _packaged_prompts_dir() / filename
    if packaged_path.is_file():
        return packaged_path.read_text(encoding="utf-8")

    raise PromptNotFoundError(
        f"no prompt {name!r} version {version} at {source_path} or in the "
        "packaged fallback; prompts are versioned files, never edited in place"
    )


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


def _split_template(
    template: str, substitutions: list[tuple[str, str, str]]
) -> RenderedPrompt:
    """Partition one rendered template into its static and volatile halves.

    ``substitutions`` is an ordered list of ``(placeholder, label, value)``, in
    the order the placeholders actually appear in ``template`` -- required,
    because this walks the template left to right with `str.partition`,
    consuming one placeholder at a time from what is left of the text. Getting
    the order wrong would leave an earlier placeholder's literal `{...}` text
    stranded, unsubstituted, inside a `static_parts` span.

    Every span of literal template text between (or before/after) a
    placeholder is static -- identical on every call at this template version
    -- and is appended to ``system`` in the template's own order. Every
    substituted value is volatile and is appended to ``user``, also in order.

    **Each value carries its section heading with it into ``user``**, and that
    is the correction the first version of this needed. Moving payloads to the
    end without their headings left the static half reading

        COVERAGE
        --------
        What this source was able to tell us, measured by code:

        **Read `gaps` before you conclude anything negative.** ...

    -- a heading promising content that had gone -- while the volatile half was
    three anonymous JSON documents separated by blank lines. Every character of
    the prompt still existed, so the reordering looked text-preserving. It was
    not: **the association between a heading and its payload is content**, and
    `correlate` substitutes three payloads whose only remaining distinguisher
    would have been their internal shape.

    The headings are a few dozen tokens of static text living on the volatile
    side, so they are not cached. That is the right trade every time: B-421 is
    explicitly *cost, not correctness*, and a cheaper prompt that says something
    slightly different is not the thing being optimised.

    This is why the reordering the module docstring describes is safe: no
    template character and no substituted value is ever dropped, duplicated,
    or reworded here -- each one is simply relocated to whichever of the two
    output strings its own kind (static template text vs. substituted value)
    belongs to, in the relative order it already had among spans of that kind.
    """

    static_parts: list[str] = []
    volatile_parts: list[str] = []
    remaining = template
    for placeholder, label, value in substitutions:
        before, found, remaining = remaining.partition(placeholder)
        if not found:
            raise ValueError(f"placeholder {placeholder!r} not found in template")
        static_parts.append(before)
        volatile_parts.append(f"{label}\n{'-' * len(label)}\n{value}")
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
    return _split_template(template, [("{descent_json}", "DESCENT RESULT", payload)])


def _quoted_record_text(record: dict) -> Any:
    """One shaped log record's ``text``, wrapped for the model (B-467/B-470).

    ``text`` is the syslog line's own prose -- device-authored, and the one
    field on this fabric an unauthenticated attacker can write from the
    network. ``None`` (a record missing the field, which `shape_window`
    never produces but a hand-built test window might) passes through
    unwrapped rather than being coerced into the string `"None"`.
    """

    text = record.get("text")
    return quote_device_text(text) if isinstance(text, str) else text


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

    Each record's ``text`` -- the syslog line's own prose, and the one field
    an unauthenticated attacker can write from the network -- is wrapped with
    :func:`model_egress.quote_device_text` before it reaches
    ``window_payload``. See the module docstring's note on why this builder,
    unlike :func:`build_report_prompt`, is not structurally free of device
    text and needs this instead (B-467/B-470).

    ``correlate.v4.txt`` substitutes three placeholders inside GROUNDING, in
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
                "text": _quoted_record_text(record),
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
    # Labels match the headings these payloads sit under in the template, so a
    # reader of the volatile half sees the same three sections in the same
    # order. Template order matters -- see `_split_template`.
    return _split_template(template, [
        ("{coverage_json}", "COVERAGE", json.dumps(coverage, indent=2)),
        ("{finding_json}", "FINDING", json.dumps(finding_payload(result), indent=2)),
        ("{window_json}", "LOG WINDOW (device timestamps)",
         json.dumps(window_payload, indent=2)),
    ])
