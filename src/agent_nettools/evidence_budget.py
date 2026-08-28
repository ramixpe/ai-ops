"""Evidence budget: keep a fabric-wide bundle from silently blowing the context.

A single device's evidence is small enough that ``build_analysis_prompt``
could always afford to send it whole. A *fabric-wide* bundle (Phase 6's
``analyze_fabric``, one evidence collection per device times nine-plus
devices) has no such guarantee -- a verbose ``show logging`` or a large
BGP/route table on even one device can dwarf everything else, and nothing
before this module ever capped it.

Characters as a token proxy
----------------------------
Every budget here is measured in *characters*, not tokens. Characters are a
cheap, dependency-free proxy: no tokenizer call is needed to enforce a bound,
the relationship between characters and tokens is roughly stable for the
mostly-ASCII CLI output this tool handles, and a slightly generous
character bound is a safe conservative estimate of a token bound (a token is
very rarely more than a few characters for this kind of text). It is not
exact -- do not treat these constants as a precise token count.

Truncate the middle, not the end
----------------------------------
A long "show logging" or route table is often most informative at its start
(what changed first) and its end (the most recent state); truncating only
the tail throws away one of those for free. Truncating the *middle* instead
keeps a bounded window on to both ends and replaces the cut span with an
explicit ``[TRUNCATED: N characters omitted]`` marker, so the model knows the
evidence is partial rather than silently reading a short device as
uneventful -- the analysis prompts already instruct the model to say what is
missing when the evidence is incomplete.

Parsed over raw -- and, since B-467/B-470, parsed only
---------------------------------------------------------
When an intent's ``parse_status`` is ``"ok"``, the parsed structure (compact
JSON) is sent instead of the raw command text. Parsed data is far more
information-dense per character than a raw text table, and raw CLI output's
column-aligned whitespace is exactly the kind of thing a model misreads or
wastes tokens re-deriving structure from.

**Raw command text is never sent, including when parsing failed.** Before
B-467 this module's answer to "parsing failed, what do we send instead" was
the raw command output -- DEEP-REVIEW-2026-08-17 §2.1 named it, correctly, as
one of three paths that violated invariant 4 by test-pinned design. The
model now gets the same thing a caller of the sanitised MCP surface already
gets for the same failure: a withheld-commands record (chars/lines per
command, never the text) and a classified error, both produced by
``model_egress.project_envelope`` -- see ``_section_text``. A parse failure
is communicated as "unavailable, and why", never as raw device text the
model was never supposed to see just because the parser did not run.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from . import model_egress, parsers

# Env vars documented in .env.example. Read at call time (not import time) so
# tests can monkeypatch them without reloading this module.
PER_INTENT_CHAR_BUDGET_ENV = "NETTOOLS_EVIDENCE_PER_INTENT_CHARS"
TOTAL_CHAR_BUDGET_ENV = "NETTOOLS_EVIDENCE_TOTAL_CHARS"

# Generous defaults: large enough that a normal single-device evidence
# section is never touched, small enough that one pathological section (a
# huge route table, a verbose log dump) cannot dominate a fabric-wide prompt.
DEFAULT_PER_INTENT_CHAR_BUDGET = 4000
DEFAULT_TOTAL_CHAR_BUDGET = 40000

# Reserved headroom for the marker text itself when truncating -- the marker's
# own length varies with the omitted-character count, so this is a generous
# fixed reservation rather than an exact fit.
_MARKER_RESERVE = 48
_MIN_KEEP = 20


def _budget_disclosure(omitted_chars: int) -> str:
    """Render the model-visible omission margin for one evidence section."""

    return json.dumps({"budget_truncated_chars": omitted_chars}, separators=(",", ":"))


def _truncate_with_disclosure(text: str, budget: int) -> tuple[str, int]:
    """Bound ``text`` while retaining its structured omission disclosure."""

    if budget <= 0 or len(text) <= budget:
        return text, 0

    full_omission = _budget_disclosure(len(text))
    minimum_marker = "[TRUNCATED: 0 characters omitted]"
    if budget < len(full_omission) + len(minimum_marker) + _MIN_KEEP + 2:
        return full_omission, len(text)

    omitted = 0
    for _ in range(8):
        disclosure = _budget_disclosure(omitted)
        content_budget = max(0, budget - len(disclosure) - 1)
        truncated, next_omitted = _truncate_middle(text, content_budget)
        if next_omitted == omitted:
            return f"{truncated}\n{disclosure}", omitted
        omitted = next_omitted
    return f"{truncated}\n{_budget_disclosure(omitted)}", omitted


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _truncate_middle(text: str, budget: int) -> tuple[str, int]:
    """Truncate the middle of ``text`` to fit within ``budget`` characters.

    Returns ``(possibly-truncated text, characters omitted)``; ``omitted`` is
    ``0`` when nothing was cut. The head and tail kept are split evenly (head
    gets the larger half on an odd split), and the omitted span is replaced
    with an explicit ``[TRUNCATED: N characters omitted]`` marker so the
    model is told evidence is partial rather than reading a short section as
    a complete, uneventful one.

    ``budget`` is treated as a hard ceiling on the *result*, not just an
    input to the head/tail split: the marker's own length grows with the
    digit count of ``omitted``, so at a small budget a naive fixed split can
    overshoot. The loop below shrinks the kept head/tail until the rendered
    result actually fits (or there is nothing left to shrink).
    """

    if budget <= 0 or len(text) <= budget:
        return text, 0

    keep = max(budget - _MARKER_RESERVE, _MIN_KEEP)
    head_len = keep - keep // 2
    tail_len = keep // 2

    while True:
        omitted = len(text) - head_len - tail_len
        marker = f"[TRUNCATED: {omitted} characters omitted]"
        head = text[:head_len]
        tail = text[len(text) - tail_len :] if tail_len else ""
        result = f"{head}\n{marker}\n{tail}" if tail else f"{head}\n{marker}"
        if len(result) <= budget or (head_len <= 0 and tail_len <= 0):
            return result, omitted
        if tail_len > 0:
            tail_len -= 1
        else:
            head_len -= 1


def _section_text(section: Any) -> str:
    """Render one intent's evidence section as compactly as possible.

    Prefers the parsed structure (JSON, no indentation) when
    ``parse_status == parsers.PARSE_OK`` -- projected through
    ``model_egress.project_envelope`` first, so a free-text field inside the
    parsed structure (a log line's ``text``, a BGP peer's
    ``last_reset_reason``, an interface ``description``) reaches this
    section quoted and budgeted rather than as unmarked device prose. See
    the module docstring for why parsed data is preferred over raw text at
    all.

    When parsing failed, is unavailable, or the section carries no ``data``
    at all, **raw command output is never returned** (B-467/B-470): the
    withheld-commands record and classified errors ``project_envelope``
    already produced for this section are rendered instead, so the model
    reads "unavailable, and why" in the same shape the sanitised MCP surface
    would give it for the same failure.
    """

    if not isinstance(section, dict):
        return str(section)

    projected = model_egress.project_envelope(section)
    original_data = section.get("data") or {}
    projected_data = projected.get("data") or {}

    if original_data.get("parse_status") == parsers.PARSE_OK and original_data.get("parsed") is not None:
        return json.dumps(projected_data.get("parsed"), separators=(",", ":"), sort_keys=True)

    status = projected.get("status", "unknown")
    errors = projected.get("errors") or []
    commands_withheld = projected_data.get("commands_withheld", {})
    return (
        f"status={status} errors={errors} "
        f"commands_withheld={json.dumps(commands_withheld, sort_keys=True)}"
    )


def budget_device_evidence(
    device_name: str,
    evidence: Mapping[str, Any],
    *,
    per_intent_chars: int | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Budget one device's evidence, intent by intent.

    Returns ``({intent: budgeted_text}, truncation_report)``, where each
    truncation report entry is
    ``{"device": ..., "intent": ..., "omitted_chars": ...}``.
    """

    per_intent_chars = per_intent_chars or _env_int(
        PER_INTENT_CHAR_BUDGET_ENV, DEFAULT_PER_INTENT_CHAR_BUDGET
    )

    sections: dict[str, str] = {}
    report: list[dict[str, Any]] = []
    for intent, section in evidence.items():
        if not isinstance(section, dict):
            continue  # "device", "platform", "timestamp" are plain strings.
        text = _section_text(section)
        truncated_text, omitted = _truncate_with_disclosure(text, per_intent_chars)
        sections[intent] = truncated_text
        if omitted:
            report.append({"device": device_name, "intent": intent, "omitted_chars": omitted})

    return sections, report


def budget_fabric_evidence(
    evidence_by_device: Mapping[str, Mapping[str, Any]],
    *,
    per_intent_chars: int | None = None,
    total_chars: int | None = None,
) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    """Budget an entire fabric's evidence: per-intent, then an overall ceiling.

    Per-intent truncation runs first (via ``budget_device_evidence``) so a
    single pathological section cannot dominate; if the *sum* across the
    whole fabric still exceeds ``total_chars``, every already-budgeted
    section is proportionally shrunk (and re-truncated through the middle
    again) until the total fits. Returns
    ``({device: {intent: budgeted_text}}, truncation_report)``.
    """

    per_intent_chars = per_intent_chars or _env_int(
        PER_INTENT_CHAR_BUDGET_ENV, DEFAULT_PER_INTENT_CHAR_BUDGET
    )
    total_chars = total_chars or _env_int(TOTAL_CHAR_BUDGET_ENV, DEFAULT_TOTAL_CHAR_BUDGET)

    per_device: dict[str, dict[str, str]] = {}
    report: list[dict[str, Any]] = []
    for device_name, evidence in evidence_by_device.items():
        sections, device_report = budget_device_evidence(
            device_name, evidence, per_intent_chars=per_intent_chars
        )
        per_device[device_name] = sections
        report.extend(device_report)

    total = sum(len(text) for sections in per_device.values() for text in sections.values())
    if total > total_chars > 0:
        scale = total_chars / total
        for device_name, sections in per_device.items():
            for intent, text in list(sections.items()):
                section_budget = max(_MIN_KEEP, int(len(text) * scale))
                if len(text) <= section_budget:
                    continue
                truncated_text, omitted = _truncate_with_disclosure(text, section_budget)
                sections[intent] = truncated_text
                if omitted:
                    report.append({"device": device_name, "intent": intent, "omitted_chars": omitted})

    return per_device, report


def render_budgeted_evidence(per_device: Mapping[str, Mapping[str, str]]) -> str:
    """Render a budgeted per-device/per-intent bundle as prompt-ready text."""

    parts: list[str] = []
    for device_name, sections in per_device.items():
        parts.append(f"### {device_name}")
        for intent, text in sections.items():
            parts.append(f"#### {intent}\n{text}")
    return "\n\n".join(parts)
