#!/usr/bin/env python3
"""M8 — measure the context window, instead of arguing about it.

`docs/build/MCP-EXPERIMENT.md` §10 records that rewording the classic MCP
manifest's tool descriptions grew it **+86%** (5,961 -> 11,107 characters),
paid on every single tool-list call before a model does anything. B-479 then
shipped a `staged` surface and asserted, in a comment, that its manifest is
"under HALF classic's" — checked by `tests/test_staged_surface.py`, but only
over `tool.description`, not the schema a client actually receives. B-113
(open) asks whether a small model can navigate either surface at all, which
is a question about the whole context surface, not just descriptions.

This script measures the three things that make up "the context window" for
this project, all offline, all against fixtures or already-registered code
objects — **no container, no live device, no model call**:

1.  **The MCP manifest.** The *real* serialised size of `tools/list`'s
    response — name + description + JSON input schema + annotations — for
    both `NETTOOLS_MCP_SURFACE=classic` and `=staged`, read from the
    registered `FastMCP` tool objects via the SDK's own `list_tools()` and
    the exact serialisation `mcp/server/stdio.py` uses on the wire
    (`model_dump_json(by_alias=True, exclude_unset=True)`) — not by
    re-reading `server.py`/`staged_surface.py` as text, and not by summing
    `len(description)` the way the existing pinning test does.

2.  **The prompt cost.** `prompt_library.RenderedPrompt.system`/`.user` for
    the real `build_report_prompt`/`build_correlate_prompt` builders, run
    against the same committed fixtures `tests/test_report_prompt.py` and
    `tests/test_correlate_prompt.py` already use as golden cases (so the
    numbers are reproducible by anyone with this repo, no lab required).

3.  **The evidence cost.** `evidence_budget.py` already budgets in
    characters and produces a truncation report; this replays the whole
    lab's fixtures through it (`--from-fixtures`'s own data source) and
    reports what a real fabric-wide bundle actually spends — at the
    project's *default* budgets, and again at a deliberately tightened one,
    because the default numbers alone turn out to hide whether the
    mechanism does anything at all on this corpus (see `evidence_report`'s
    docstring).

Characters vs. tokens — stated, not estimated
-----------------------------------------------
Every number this script produces is in **characters**. `evidence_budget.py`'s
own docstring says plainly that characters are a proxy, not tokens, and this
script keeps that honesty: it looks for an offline tokeniser (`tiktoken`, if
already installed — this project does not depend on it, and this script does
not install it) and, if none is found, reports **"tokens: not measured"**
rather than multiplying by a remembered ratio and presenting that as data.
`anthropic.Anthropic().messages.count_tokens(...)` would give a real number,
but it is a hosted API call, and this task's rule 4 forbids calling a model —
so that path is refused here on purpose, not left out by oversight.

Usage
-----
    python scripts/measure_context.py            # human-readable report
    python scripts/measure_context.py --json      # machine-readable report

Every function below is also imported directly by
`tests/test_context_window_measurement.py`, which pins the numbers this
script prints so a future regression (like the one in §10) fails a test
instead of waiting for someone to notice the manifest got big.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "cisco_xr"

# `pip install -e .` puts `src/` on the path already; this makes the script
# runnable from a checkout that has not been installed at all, matching the
# other scripts in this directory.
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Fixture replay needs no real credentials (`load_fixture_evidence` never
# opens a socket), but some code on the path reads these env vars before it
# gets that far. Safe, test-only values, same ones `tests/helpers.py` uses —
# set only if the caller has not already supplied real ones.
os.environ.setdefault("DEVICE_USERNAME", "test-user")
os.environ.setdefault("DEVICE_PASSWORD", "test-password")


# --------------------------------------------------------------------------- #
# 1. The MCP manifest — both surfaces, the real wire-serialised size.
# --------------------------------------------------------------------------- #

# B-518: the classic manifest is measured LIVE, by importing whatever tools
# are registered right now — which is exactly right for "what does a client
# pay today", and exactly wrong for "did staged get better", because a ratio
# computed against a live, ever-growing classic improves every time a tool is
# added, for doing nothing: staged's own size never moves. B-501 measured
# 20,117 chars / 23 tools; by the time B-518 was filed the same live call
# reported 31,669 / 31 — three more tool waves (B-508's protocol-coverage
# checks, B-512's Loki/Prometheus tools, this task's own NetBox read tools),
# every one a real, reviewed capability addition, none of it "staged got
# leaner". Left alone, `staged_over_classic_ratio` below would have quietly
# improved through every one of those waves and nothing would have said so.
#
# The fix is this history: append-only, like FINDINGS.md/BACKLOG.md — a new
# entry is added when someone deliberately re-measures and re-pins, never a
# silent edit of the one before it. `measure_both_manifests` reports staged
# against BOTH the live classic number (what a client pays right now) and
# the LATEST pinned snapshot here (what "under half" was actually verified
# against, and the fixed point a doubling-class regression is judged
# relative to) — labelled so neither number can be mistaken for the other,
# and the live-vs-pinned delta is reported explicitly rather than left for a
# reader to notice by subtracting two numbers from two different reports.
CLASSIC_MANIFEST_SNAPSHOTS: tuple[dict[str, Any], ...] = (
    {"label": "B-501", "captured": "2026-08-18", "manifest_chars": 20_117, "tool_count": 23,
     "note": "first measurement of the complete wire payload"},
    {"label": "B-518", "captured": "2026-08-19", "manifest_chars": 31_669, "tool_count": 31,
     "note": "re-pinned after B-508/B-512/the NetBox read tools grew classic "
             "+57% since B-501, none of it bloat -- see this constant's own "
             "comment above"},
)


def _reload_server(surface: str | None):
    """Import (or reload) `mcp_server.server` with `NETTOOLS_MCP_SURFACE` set.

    ``surface`` is ``"classic"``, ``"staged"``, or ``None`` to unset the env
    var entirely (the documented default is classic either way). Mirrors
    `tests/test_staged_surface.py`'s `staged_server` fixture, as a plain
    function so both this script and the pinning test can drive it without
    duplicating the reload dance.
    """

    import mcp_server.server as server

    if surface is None or surface == "classic":
        os.environ.pop("NETTOOLS_MCP_SURFACE", None)
    else:
        os.environ["NETTOOLS_MCP_SURFACE"] = surface
    importlib.reload(server)
    return server


def manifest_report(server_module: Any) -> dict[str, Any]:
    """Measure the *real* `tools/list` payload for an already-loaded server.

    Calls the SDK's own `FastMCP.list_tools()` — an async method that walks
    the registered `ToolManager`, exactly what a client triggers on
    `tools/list` — and serialises each tool with
    ``model_dump_json(by_alias=True, exclude_unset=True)``, which is the
    precise call `mcp/server/stdio.py` makes when writing a response to the
    wire (confirmed by reading that module, not assumed). That includes the
    JSON input schema and any annotations, not just the description — the
    existing `test_the_staged_manifest_is_smaller_than_classic` measures only
    ``sum(len(tool.description))``, which undercounts what a client actually
    receives.

    Returns per-tool sizes plus the aggregate manifest — the JSON array a
    `tools/list` response's ``tools`` field would actually contain, comma-
    joined with no extra whitespace (pydantic's default, and the wire
    format).
    """

    tools = asyncio.run(server_module.mcp.list_tools())
    per_tool = []
    parts = []
    for tool in tools:
        wire_json = tool.model_dump_json(by_alias=True, exclude_unset=True)
        parts.append(wire_json)
        per_tool.append({
            "name": tool.name,
            "description_chars": len(tool.description or ""),
            "wire_chars": len(wire_json),
        })
    manifest = "[" + ",".join(parts) + "]"
    return {
        "tool_count": len(tools),
        "manifest_chars": len(manifest),
        "description_only_chars": sum(t["description_chars"] for t in per_tool),
        "per_tool": per_tool,
    }


def manifest_ratios(staged_chars: int, classic_chars: int) -> dict[str, Any]:
    """The two ratios B-518 asks for, plus both surfaces' own absolute sizes
    and the pinned baseline they are each computed from — pure, so it is
    testable with synthetic numbers and does not need a real MCP server
    loaded to prove the self-describing property holds.

    ``staged_over_classic_ratio`` moves with WHATEVER classic is right now —
    correct for "what does a client pay today", wrong for "did staged
    improve", because it improves on its own every time a tool is added
    (B-518's own finding). ``staged_over_pinned_baseline_ratio`` is computed
    against the latest entry in :data:`CLASSIC_MANIFEST_SNAPSHOTS` instead —
    a fixed point that only moves when someone deliberately appends a new
    snapshot, so it cannot be flattered by classic's growth. Both are
    returned together, alongside the raw chars each was computed from
    (``classic_chars``/``staged_chars``/``pinned_baseline``), so a caller
    that only prints one ratio number still has everything needed to tell
    which manifest produced it — the self-describing property B-518 asked
    for.
    """

    baseline = CLASSIC_MANIFEST_SNAPSHOTS[-1]
    live_ratio = staged_chars / classic_chars if classic_chars else None
    baseline_chars = baseline["manifest_chars"]
    pinned_ratio = staged_chars / baseline_chars if baseline_chars else None

    return {
        "classic_chars": classic_chars,
        "staged_chars": staged_chars,
        "staged_over_classic_ratio": live_ratio,
        "pinned_baseline": baseline,
        "staged_over_pinned_baseline_ratio": pinned_ratio,
        # How far the LIVE classic manifest has already moved from the fixed
        # point the pinned ratio is judged against — the number a reader
        # needs to tell "staged got better" from "classic just got bigger"
        # apart without computing it themselves.
        "classic_chars_grown_since_pinned_baseline": classic_chars - baseline_chars,
    }


def measure_both_manifests() -> dict[str, Any]:
    """Classic and staged manifests, measured back to back, env restored after.

    Standalone-process use (the script's own `main()`); the pinning test
    drives `_reload_server`/`manifest_report` itself so it can use
    `monkeypatch` for guaranteed teardown instead of manual restoration.
    """

    original = os.environ.get("NETTOOLS_MCP_SURFACE")
    try:
        classic = manifest_report(_reload_server("classic"))
        staged = manifest_report(_reload_server("staged"))
    finally:
        if original is None:
            os.environ.pop("NETTOOLS_MCP_SURFACE", None)
        else:
            os.environ["NETTOOLS_MCP_SURFACE"] = original
        _reload_server(original)

    ratios = manifest_ratios(staged["manifest_chars"], classic["manifest_chars"])
    return {
        "classic": classic,
        "staged": staged,
        # Kept at the top level, unchanged shape, for every existing caller
        # (this script's own printer, test_context_window_measurement.py's
        # earlier assertions) that already reads `staged_over_classic_ratio`
        # straight off this dict.
        "staged_over_classic_ratio": ratios["staged_over_classic_ratio"],
        "pinned_baseline": ratios["pinned_baseline"],
        "staged_over_pinned_baseline_ratio": ratios["staged_over_pinned_baseline_ratio"],
        "classic_chars_grown_since_pinned_baseline": ratios["classic_chars_grown_since_pinned_baseline"],
    }


# --------------------------------------------------------------------------- #
# 2. The prompt cost — real prompts, real fixtures, no model.
# --------------------------------------------------------------------------- #

# RR1 -> 10.255.0.12 is the same golden bgp_session case
# `tests/test_report_prompt.py` uses; reused here (not imported from the test
# module, so this script has no dependency on test internals that might
# change shape) so the numbers are directly comparable to that file's golden
# cases.
_REPORT_SUBJECT_OWNER = {
    "10.255.0.11": "PE1", "10.255.0.12": "PE2", "10.255.0.13": "PE3",
    "10.255.0.14": "PE4", "10.255.0.31": "RR1",
}


def _report_resolver(subject: str) -> str:
    return _REPORT_SUBJECT_OWNER[subject]


def _report_collect(device: str, rung: Any, subject: str, label: str) -> dict:
    from agent_nettools.fixtures import fixture_sender, load_fixture_evidence
    from agent_nettools.network_tools import run_template

    evidence = dict(load_fixture_evidence(device, label=label))
    sender = fixture_sender(label=label)
    for step in rung.collect:
        if not step.is_template:
            continue
        if step.parameter == "prefix":
            key = f"{subject}/32"
            evidence[f"{step.name}:{key}"] = run_template(
                device, step.name, sender=sender, prefix=key
            )
        elif step.parameter == "interface":
            parsed = evidence.get("interfaces", {}).get("data", {}).get("parsed") or {}
            for record in parsed.get("records", []):
                name = record.get("interface", "")
                if name.startswith("Gi") and "." not in name:
                    evidence[f"{step.name}:{name}"] = run_template(
                        device, step.name, sender=sender, interface=name
                    )
        else:
            evidence[f"{step.name}:{subject}"] = run_template(
                device, step.name, sender=sender, **{step.parameter: subject}
            )
    return evidence


def _report_descent(label: str):
    from agent_nettools import flows
    from agent_nettools.descent import run_descent

    return run_descent(
        flows.flow_for("bgp_session"), "RR1", "10.255.0.12",
        collector=lambda d, r, s: _report_collect(d, r, s, label),
        resolver=_report_resolver,
    )


def report_prompt_report(label: str) -> dict[str, Any]:
    """`build_report_prompt` over the `label` fixture replay of the RR1 ->
    PE2 `bgp_session` golden case (same case as `tests/test_report_prompt.py`)."""

    from agent_nettools.prompt_library import build_report_prompt

    result = _report_descent(label)
    rendered = build_report_prompt(result)
    return {
        "label": label,
        "finding": result.finding,
        "system_chars": len(rendered.system),
        "user_chars": len(rendered.user),
        "total_chars": len(rendered.system) + len(rendered.user),
    }


def _correlate_records(device: str, label: str) -> list[dict]:
    from agent_nettools import template_parsers

    raw = (FIXTURES / device / label / "show-logging-last-200.txt").read_text(encoding="utf-8")
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return parsed["records"]


def _correlate_finding():
    from agent_nettools.checks import CheckResult
    from agent_nettools.descent import DescentResult, RungOutcome

    outcome = RungOutcome(
        "interface", "PE2",
        CheckResult("broken", reason="both uplinks admin-down", subject="Gi0/0/0/0",
                    evidence_keys=("PE2:interface:Gi0/0/0/0",)),
    )
    return DescentResult(
        flow="bgp_session", device="RR1", subject="10.255.0.12",
        finding="interface_line_down", outcomes=(outcome,),
        evidence_keys=("PE2:interface:Gi0/0/0/0",),
    )


def correlate_prompt_report(device: str, label: str) -> dict[str, Any]:
    """`build_correlate_prompt` over PE2's `label` `show logging` window (same
    fixture pair `tests/test_correlate_prompt.py`'s golden cases use)."""

    from agent_nettools import log_window
    from agent_nettools.prompt_library import build_correlate_prompt

    shaped = log_window.shape_window(_correlate_records(device, label))
    rendered = build_correlate_prompt(_correlate_finding(), shaped)
    return {
        "device": device,
        "label": label,
        "records_collected": shaped.total_in,
        "records_shown": len(shaped.records),
        "system_chars": len(rendered.system),
        "user_chars": len(rendered.user),
        "total_chars": len(rendered.system) + len(rendered.user),
    }


# --------------------------------------------------------------------------- #
# 3. The evidence cost — a real fabric-wide `--from-fixtures` bundle.
# --------------------------------------------------------------------------- #


def evidence_report(
    label: str = "broken",
    *,
    tight_per_intent_chars: int = 800,
    tight_total_chars: int = 15000,
) -> dict[str, Any]:
    """What a real fabric-wide evidence bundle spends, through `evidence_budget.py`.

    Loads every inventory device's ``label`` fixture (the same replay
    ``nettools investigate --from-fixtures`` and ``analyze --fabric
    --from-fixtures`` use) and budgets it twice:

    * at this project's shipped defaults
      (``NETTOOLS_EVIDENCE_PER_INTENT_CHARS``/``NETTOOLS_EVIDENCE_TOTAL_CHARS``,
      4000/40000) — what a real investigation actually spends today; and
    * at a deliberately tightened budget — because on this nine-device lab's
      committed fixtures the defaults turn out to be generous enough that
      **truncation never triggers** (the largest single section is ~1.6 kB,
      well under the 4 kB per-intent ceiling). Reporting only the default
      numbers would make ``budget_fabric_evidence``'s truncation *mechanism*
      look untested by this script — PROCESS.md §0.12's vacuous-guardrail
      shape, here applied to a measurement rather than a test: a pass that
      never says what it found wrong is a pass that may not have looked. The
      tightened run proves the mechanism engages on this exact real data,
      not only on the synthetic ``"x" * 10000`` strings
      ``tests/test_evidence_budget.py`` uses.
    """

    from agent_nettools.evidence_budget import _section_text, budget_fabric_evidence
    from agent_nettools.network_tools import list_devices

    listed = list_devices()
    device_names = [d["name"] for d in listed["data"]["devices"]]
    evidence_by_device = {}
    for name in device_names:
        from agent_nettools.fixtures import load_fixture_evidence
        evidence_by_device[name] = load_fixture_evidence(name, label=label)

    raw_total = 0
    largest = None
    for name, evidence in evidence_by_device.items():
        for intent, section in evidence.items():
            if not isinstance(section, dict):
                continue
            size = len(_section_text(section))
            raw_total += size
            if largest is None or size > largest["chars"]:
                largest = {"device": name, "intent": intent, "chars": size}

    default_per_device, default_report = budget_fabric_evidence(evidence_by_device)
    default_total = sum(len(t) for s in default_per_device.values() for t in s.values())

    tight_per_device, tight_report = budget_fabric_evidence(
        evidence_by_device,
        per_intent_chars=tight_per_intent_chars,
        total_chars=tight_total_chars,
    )
    tight_total = sum(len(t) for s in tight_per_device.values() for t in s.values())

    return {
        "label": label,
        "device_count": len(device_names),
        "raw_chars": raw_total,
        "largest_section": largest,
        "default_budget": {
            "per_intent_chars": 4000,
            "total_chars": 40000,
            "budgeted_chars": default_total,
            "sections_truncated": len(default_report),
        },
        "tightened_budget": {
            "per_intent_chars": tight_per_intent_chars,
            "total_chars": tight_total_chars,
            "budgeted_chars": tight_total,
            "sections_truncated": len(tight_report),
        },
    }


# --------------------------------------------------------------------------- #
# 4. Characters vs. tokens — honest, not estimated.
# --------------------------------------------------------------------------- #


def token_stats(texts: dict[str, str]) -> dict[str, Any]:
    """Token counts for ``texts``, IF an offline tokeniser is already
    installed — never estimated from a remembered ratio.

    Tries `tiktoken` opportunistically (this project does not depend on it
    and this function does not install it — the task rule is "if available
    WITHOUT adding a dependency"). If it is not importable, every entry is
    marked unmeasured with the reason stated, rather than a guessed
    chars-per-token constant standing in for a measurement. The Anthropic
    SDK's own `messages.count_tokens` would give an exact number but is a
    hosted API call, and calling a model is out of scope for this
    measurement by the task's own rule 4 — refused on purpose, not missing
    by oversight.
    """

    try:
        import tiktoken
    except ImportError:
        return {
            "measured": False,
            "reason": (
                "no offline tokeniser is installed (tiktoken not found), and "
                "the Anthropic SDK's token counting is a hosted API call, "
                "which this measurement's rules forbid calling. Reporting "
                "characters only -- an estimated tokens-per-character ratio "
                "is not reported, because an invented number is worse than "
                "a stated gap."
            ),
            "char_counts": {name: len(text) for name, text in texts.items()},
        }

    encoding = tiktoken.get_encoding("cl100k_base")
    per_text = {}
    total_chars = 0
    total_tokens = 0
    for name, text in texts.items():
        n_tokens = len(encoding.encode(text))
        n_chars = len(text)
        per_text[name] = {"chars": n_chars, "tokens": n_tokens}
        total_chars += n_chars
        total_tokens += n_tokens
    return {
        "measured": True,
        "encoding": "cl100k_base",
        "per_text": per_text,
        "observed_chars_per_token": (total_chars / total_tokens) if total_tokens else None,
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def build_full_report() -> dict[str, Any]:
    manifests = measure_both_manifests()
    reports = {
        "broken": report_prompt_report("broken"),
        "t0": report_prompt_report("t0"),
    }
    correlates = {
        "broken": correlate_prompt_report("PE2", "broken"),
        "healthy": correlate_prompt_report("PE2", "healthy"),
    }
    evidence = evidence_report("broken")

    # Token counting needs the real prompt text (`token_stats` takes texts,
    # not lengths); the manifest's own JSON is not retained past
    # `manifest_report`, so only the prompts get a token pass, not the
    # manifest -- noted in the printed report rather than silently omitted.
    token_summary = token_stats(_collect_real_prompt_texts())

    return {
        "mcp_manifest": manifests,
        "report_prompt": reports,
        "correlate_prompt": correlates,
        "evidence": evidence,
        "tokens": token_summary,
    }


def _collect_real_prompt_texts() -> dict[str, str]:
    """Re-render the same prompts as text, for the token pass only.

    Kept separate from `report_prompt_report`/`correlate_prompt_report` (which
    return sizes, not text) so the main report stays cheap to build even when
    no tokeniser is installed -- this is only called once, after the manifest
    and evidence work is already done.
    """

    from agent_nettools import log_window
    from agent_nettools.prompt_library import build_correlate_prompt, build_report_prompt

    out = {}
    for label in ("broken", "t0"):
        rendered = build_report_prompt(_report_descent(label))
        out[f"report[{label}]"] = rendered.system + rendered.user
    for label in ("broken", "healthy"):
        shaped = log_window.shape_window(_correlate_records("PE2", label))
        rendered = build_correlate_prompt(_correlate_finding(), shaped)
        out[f"correlate[{label}]"] = rendered.system + rendered.user
    return out


def _print_human_report(report: dict[str, Any]) -> None:
    m = report["mcp_manifest"]
    print("=== M8: context window measurement (characters; see 'tokens' section) ===\n")

    print("MCP manifest -- real tools/list wire size (name + description + JSON schema + annotations)")
    for surface in ("classic", "staged"):
        r = m[surface]
        print(
            f"  {surface:7s}: {r['tool_count']:2d} tools, {r['manifest_chars']:6d} chars "
            f"(description-only: {r['description_only_chars']} chars)"
        )
    # B-518: never a bare ratio -- both absolute sizes and which classic
    # manifest (live, or the pinned snapshot) produced each one, printed
    # right beside it, so growth cannot be misread as improvement.
    live_ratio = m["staged_over_classic_ratio"]
    baseline = m["pinned_baseline"]
    pinned_ratio = m["staged_over_pinned_baseline_ratio"]
    grown = m["classic_chars_grown_since_pinned_baseline"]
    print(
        f"  staged/classic ratio, LIVE classic ({m['classic']['manifest_chars']} chars, "
        f"measured just now): {live_ratio:.3f}  "
        f"({'UNDER HALF' if live_ratio and live_ratio < 0.5 else 'NOT under half'})"
    )
    print(
        f"  staged/classic ratio, PINNED baseline {baseline['label']} "
        f"({baseline['manifest_chars']} chars, captured {baseline['captured']}): "
        f"{pinned_ratio:.3f}  "
        f"({'UNDER HALF -- B-479 claim holds against this snapshot' if pinned_ratio and pinned_ratio < 0.5 else 'NOT under half'})"
    )
    if grown:
        pct = grown / baseline["manifest_chars"] * 100 if baseline["manifest_chars"] else 0.0
        print(f"  classic has grown {grown:+d} chars ({pct:+.1f}%) since the pinned baseline was captured\n")
    else:
        print("  classic is unchanged since the pinned baseline was captured\n")

    print("Report prompt (report.v2, RR1 -> 10.255.0.12, bgp_session)")
    for label, r in report["report_prompt"].items():
        print(f"  {label:6s} ({r['finding']:20s}): system={r['system_chars']:5d} "
              f"user={r['user_chars']:5d} total={r['total_chars']:5d}")
    print()

    print("Correlate prompt (correlate.v4, PE2 show-logging window)")
    for label, r in report["correlate_prompt"].items():
        print(f"  {label:8s}: records_in={r['records_collected']:3d} shown={r['records_shown']:3d} "
              f"system={r['system_chars']:5d} user={r['user_chars']:5d} total={r['total_chars']:5d}")
    print()

    e = report["evidence"]
    print(f"Evidence (fabric-wide, {e['device_count']} devices, label={e['label']!r})")
    print(f"  raw (pre-budget)      : {e['raw_chars']:6d} chars")
    print(f"  largest single section: {e['largest_section']}")
    d = e["default_budget"]
    print(f"  default budget ({d['per_intent_chars']}/{d['total_chars']}): "
          f"{d['budgeted_chars']:6d} chars, {d['sections_truncated']} sections truncated")
    t = e["tightened_budget"]
    print(f"  tightened budget ({t['per_intent_chars']}/{t['total_chars']}): "
          f"{t['budgeted_chars']:6d} chars, {t['sections_truncated']} sections truncated")
    print()

    tok = report["tokens"]
    print("Characters vs tokens")
    if tok["measured"]:
        print(f"  tiktoken ({tok['encoding']}) available -- observed "
              f"{tok['observed_chars_per_token']:.2f} chars/token across the prompts above.")
    else:
        print("  NOT MEASURED.", tok["reason"])


def main() -> int:
    report = build_full_report()
    if "--json" in sys.argv[1:]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
