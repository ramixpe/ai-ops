"""M8 -- pins the numbers `scripts/measure_context.py` measures.

`docs/build/MCP-EXPERIMENT.md` §10 recorded a real, un-pinned regression: a
rewording of the classic MCP manifest's descriptions grew it **+86%**
(5,961 -> 11,107 characters) with nothing to notice at the time, because
nothing measured the manifest as a number. B-479's `staged_surface.py`
asserts "under HALF classic's" in a comment, and
`tests/test_staged_surface.py::test_the_staged_manifest_is_smaller_than_classic`
checks it -- but only over `sum(len(tool.description))`, undercounting what a
client actually receives on `tools/list` (the JSON input schema and any
annotations are not descriptions and are not free).

This file does not re-implement any of that measurement -- it imports
`scripts/measure_context.py` (a standalone script, not a package, so it is
loaded by file path rather than `import`) and pins what it reports:

* the MCP manifest, for both surfaces, using the SDK's own `list_tools()`
  and the exact wire serialisation `mcp/server/stdio.py` uses;
* the report/correlate prompts, over the same committed fixtures the golden
  cases in `test_report_prompt.py`/`test_correlate_prompt.py` use;
* what a real fabric-wide `--from-fixtures` evidence bundle spends through
  `evidence_budget.py`, at both the shipped defaults and a tightened budget;
* that "tokens" is honestly reported as "not measured" when no offline
  tokeniser is installed, and is not silently approximated from a remembered
  characters-per-token ratio.

Every band below is wide enough to survive routine prose editing and narrow
enough to fail on a doubling-class regression -- the class of thing that
actually happened in §10 and went unnoticed for a full session.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest
from helpers import set_device_environment

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "measure_context.py"


def _load_measure_context():
    """Import `scripts/measure_context.py` by file path.

    It is a standalone script (like every other file in `scripts/`, none of
    which are a package `tests/` imports today), so this loads it the way
    CPython's own tooling loads a script module, rather than adding
    `scripts/` to `sys.path` and risking a name collision with something
    else on it.
    """

    spec = importlib.util.spec_from_file_location("measure_context", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mc():
    return _load_measure_context()


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    set_device_environment(monkeypatch)


@pytest.fixture(autouse=True)
def _restore_mcp_surface_after(monkeypatch):
    """`manifest_report`/`measure_both_manifests` reload `mcp_server.server`
    with `NETTOOLS_MCP_SURFACE` toggled and restore it themselves on the
    success path -- this is the belt-and-braces version for the test suite,
    so a test in this file that raises partway through still leaves the
    module classic for every test collected after it, in this file or any
    other.
    """

    yield
    import mcp_server.server as server

    monkeypatch.delenv("NETTOOLS_MCP_SURFACE", raising=False)
    importlib.reload(server)


# --------------------------------------------------------------------------- #
# 1. The MCP manifest -- both surfaces, the real wire-serialised size.
# --------------------------------------------------------------------------- #


def test_manifest_sizes_are_pinned_within_a_band_that_catches_a_doubling(mc):
    report = mc.measure_both_manifests()
    classic = report["classic"]
    staged = report["staged"]
    baseline = report["pinned_baseline"]

    # Structural: only moves on a deliberate change to which tools exist.
    assert classic["tool_count"] >= 23
    assert staged["tool_count"] == 6

    # B-518: classic is NOT checked against a fixed absolute band any more.
    # It went 20,117 (B-501) -> 31,669 (B-518) chars as real tool waves
    # landed (B-508's protocol-coverage checks, B-512's Loki/Prometheus
    # tools, this task's own NetBox read tools) -- a genuine, reviewed
    # capability addition each time, not bloat, and an absolute ceiling had
    # already needed raising once (30,000 -> 34,000) before this task even
    # started, with more tool waves landing in sibling worktrees as this
    # sentence is written. A ceiling that must be manually bumped every time
    # a real tool is added is the wrong instrument -- it either lags behind
    # legitimate growth (false failures) or gets loosened so far it stops
    # catching anything.
    #
    # The RELATIONSHIP does not have that problem: classic must stay within
    # a band AROUND the latest pinned snapshot (`CLASSIC_MANIFEST_SNAPSHOTS`
    # in measure_context.py), wide enough to absorb several more legitimate
    # tool waves (a doubling from here would be ~63,000) but narrow enough
    # to still catch a regression shaped like MCP-EXPERIMENT.md §10's real
    # +86% jump. When growth is real and reviewed, someone re-pins by
    # appending a new snapshot -- the same append-only discipline
    # FINDINGS.md/BACKLOG.md already use -- and the band moves with it,
    # deliberately, instead of by a bare number edited in this file.
    floor = baseline["manifest_chars"] * 0.5
    ceiling = baseline["manifest_chars"] * 2.0
    assert floor <= classic["manifest_chars"] <= ceiling, (
        f"classic manifest is {classic['manifest_chars']} chars, outside "
        f"[{floor:.0f}, {ceiling:.0f}] -- a band around the pinned baseline "
        f"{baseline['label']} ({baseline['manifest_chars']} chars). If this "
        "grew from real tool additions, re-pin by appending a new entry to "
        "CLASSIC_MANIFEST_SNAPSHOTS; if it roughly doubled with nothing "
        "reviewed to explain it, this is MCP-EXPERIMENT.md Sec10 again."
    )

    # staged, unlike classic, IS the right kind of surface for an absolute
    # ceiling: it is deliberately small and curated (6 tools, B-479), so a
    # number that grows means staged itself grew, not "the world added a
    # tool" -- the relationship-vs-absolute distinction this task drew for
    # classic runs the other way here on purpose.
    assert 2_500 <= staged["manifest_chars"] <= 8_000, (
        f"staged manifest is {staged['manifest_chars']} chars, outside [2500, 8000]"
    )


def test_the_staged_surface_is_independently_measured_under_half_classic(mc):
    """B-479's own comment says "Staged manifest asserted under HALF
    classic's"; `tests/test_staged_surface.py` checks that, but only over
    description text. This is the first check of that claim against the
    *complete* wire payload (schema + annotations included) -- see
    `manifest_report`'s docstring for why that is a different, larger
    number than description-only.

    Checked against BOTH ratios `measure_both_manifests` reports (B-518):
    the live one, because it is still a true fact about what a client pays
    right now, and the pinned one, because that is the one the "under half"
    CLAIM is actually about -- a claim a live, ever-growing classic manifest
    cannot settle on its own (see the previous test's comment).
    """

    report = mc.measure_both_manifests()
    classic_chars = report["classic"]["manifest_chars"]
    staged_chars = report["staged"]["manifest_chars"]

    assert classic_chars > 0 and staged_chars > 0  # not a vacuous 0/0
    assert staged_chars < classic_chars

    live_ratio = report["staged_over_classic_ratio"]
    assert live_ratio < 0.5, (
        f"staged/classic (live) = {live_ratio:.3f} -- the under-half claim "
        "does not hold today once the JSON schema and annotations are "
        "counted, not just the descriptions"
    )

    pinned_ratio = report["staged_over_pinned_baseline_ratio"]
    baseline = report["pinned_baseline"]
    assert pinned_ratio < 0.5, (
        f"staged/classic (vs pinned baseline {baseline['label']}, "
        f"{baseline['manifest_chars']} chars) = {pinned_ratio:.3f} -- the "
        "under-half claim does not hold against the snapshot it was "
        "actually verified against"
    )


# --------------------------------------------------------------------------- #
# B-518 -- the ratio must not flatter staged for classic's growth.
# --------------------------------------------------------------------------- #
#
# B-501 measured the classic manifest at 20,117 chars and B-479's staged-vs-
# classic ratio (0.235) was checked against that number. By B-518 classic had
# grown to 31,669 chars -- real tool waves, not bloat -- and the staged/classic
# ratio computed against a LIVE classic would have quietly IMPROVED through
# every one of them, because staged's own size never moved. `manifest_ratios`
# is the pure function `measure_both_manifests` now delegates to; these tests
# exercise it directly with synthetic numbers, so the flattery bug and its fix
# are provable without a real MCP server, in milliseconds.


def test_manifest_ratios_reports_both_absolute_sizes_beside_every_ratio(mc):
    """The self-describing property B-518 asked for: a caller reading only
    the ratio field still has, right beside it, both surfaces' absolute
    sizes and which classic manifest (live or pinned) produced each ratio.
    """

    result = mc.manifest_ratios(staged_chars=4_723, classic_chars=31_669)

    assert result["staged_chars"] == 4_723
    assert result["classic_chars"] == 31_669
    assert result["staged_over_classic_ratio"] == pytest.approx(4_723 / 31_669)

    baseline = result["pinned_baseline"]
    assert baseline["label"] and baseline["captured"] and baseline["manifest_chars"] > 0
    assert result["staged_over_pinned_baseline_ratio"] == pytest.approx(
        4_723 / baseline["manifest_chars"]
    )
    assert result["classic_chars_grown_since_pinned_baseline"] == 31_669 - baseline["manifest_chars"]


def test_the_pinned_ratio_does_not_move_when_classic_grows_with_nothing_reviewed(mc):
    """The exact regression B-518 named: 'left alone, that ratio improves
    every time we add a tool -- flattering the staged surface for doing
    nothing.' Simulates a tool wave landing (classic grows, staged does not)
    and shows the LIVE ratio does exactly that, while the PINNED ratio --
    computed against the fixed snapshot, not whatever classic is today --
    does not move at all. This is the property that makes growth
    undetectable-as-improvement impossible, not just documented against.
    """

    baseline_chars = mc.CLASSIC_MANIFEST_SNAPSHOTS[-1]["manifest_chars"]
    staged_chars = 4_723

    before = mc.manifest_ratios(staged_chars=staged_chars, classic_chars=baseline_chars)
    # A tool wave lands: classic grows by 50%, staged is untouched.
    after = mc.manifest_ratios(staged_chars=staged_chars, classic_chars=int(baseline_chars * 1.5))

    # The live ratio DOES improve -- this is the bug, reproduced on purpose,
    # so the fix below is proven against a real failure mode and not a straw
    # man.
    assert after["staged_over_classic_ratio"] < before["staged_over_classic_ratio"], (
        "if this fails, the live ratio stopped being flattered by classic's "
        "growth -- which would be good, but means this test's premise has "
        "changed and it should be revisited, not just re-asserted the other way"
    )

    # The pinned ratio does NOT move: both measurements were taken against
    # the same fixed baseline, so it reports the exact same number before
    # and after the simulated tool wave -- staged did not get better, and
    # this is the number that says so.
    assert before["staged_over_pinned_baseline_ratio"] == after["staged_over_pinned_baseline_ratio"]
    assert after["classic_chars_grown_since_pinned_baseline"] > 0, (
        "the growth-since-baseline figure must reflect the simulated growth, "
        "so a reader is told classic moved even though the pinned ratio alone "
        "would not show it"
    )


def test_classic_growing_alone_never_changes_the_pinned_baseline_reported(mc):
    """Anti-vacuity companion: proves `pinned_baseline` itself is the SAME
    dict regardless of what `classic_chars` is passed in -- if it silently
    tracked the live number instead of the fixed snapshot, the previous
    test's core claim (the pinned ratio does not move) would be vacuously
    true for the wrong reason (both sides recomputing the same live value).
    """

    small = mc.manifest_ratios(staged_chars=100, classic_chars=1_000)
    large = mc.manifest_ratios(staged_chars=100, classic_chars=1_000_000)

    assert small["pinned_baseline"] == large["pinned_baseline"] == mc.CLASSIC_MANIFEST_SNAPSHOTS[-1]


def test_the_classic_manifest_snapshot_history_is_append_only_in_shape(mc):
    """Pins the append-only convention itself (like FINDINGS.md/BACKLOG.md):
    every entry states its own label/date/count, and later entries do not
    overwrite earlier ones -- `manifest_ratios` always reads the LATEST
    entry, but history stays visible for anyone diffing this file over time.
    """

    snapshots = mc.CLASSIC_MANIFEST_SNAPSHOTS
    assert len(snapshots) >= 2, "the whole point is a history, not one number"
    labels = [s["label"] for s in snapshots]
    assert len(labels) == len(set(labels)), "each snapshot needs a distinct label"
    for entry in snapshots:
        assert entry["manifest_chars"] > 0 and entry["tool_count"] > 0 and entry["captured"]
    # Growth across the recorded history is real, not a copy-paste of the
    # same number under a new label.
    assert snapshots[-1]["manifest_chars"] >= snapshots[0]["manifest_chars"]


def test_the_full_wire_manifest_counts_more_than_descriptions_alone(mc):
    """Anti-vacuity companion (PROCESS.md Sec0.12): this measurement's
    entire reason to exist alongside the older, description-only test is
    that a client also pays for the JSON schema and any annotations. If
    `manifest_chars` always equalled `description_only_chars`, this script
    would just be re-deriving the number the existing test already
    produces, more expensively.
    """

    report = mc.measure_both_manifests()
    for surface in ("classic", "staged"):
        r = report[surface]
        assert r["manifest_chars"] > r["description_only_chars"], (
            f"{surface}: the JSON schema/annotations/name contributed "
            "nothing to the wire size -- this measurement collapsed to "
            "the old description-only one"
        )
        # Every tool actually contributed to the total (no silently-empty
        # per-tool entries dragging the aggregate down).
        assert len(r["per_tool"]) == r["tool_count"]
        assert all(t["wire_chars"] > 0 for t in r["per_tool"])


def test_manifest_report_would_catch_a_doubled_description(mc):
    """Proves `manifest_report` itself discriminates a regression, using a
    synthetic two-tool surface built from the real `mcp.types.Tool` class
    (so the serialisation is the genuine wire call, not a stand-in) --
    without touching the real `mcp_server/server.py` on disk. The on-disk
    version of this same check (temporarily inflate a real tool's
    docstring, confirm this file's pinned band actually fails, restore) was
    run once by hand as this guard's mutation test; this is the automated,
    repeatable half.
    """

    from types import SimpleNamespace

    import mcp.types as mcp_types

    class _FakeMCP:
        def __init__(self, descriptions):
            self._descriptions = descriptions

        async def list_tools(self):
            return [
                mcp_types.Tool(
                    name=f"tool_{i}",
                    description=d,
                    input_schema={"type": "object", "properties": {}},
                )
                for i, d in enumerate(self._descriptions)
            ]

    # Long enough that the description dominates the fixed per-tool JSON
    # envelope (name/braces/schema keys) -- realistic tool descriptions in
    # this repo run to hundreds of characters (see Appendix A of
    # MCP-EXPERIMENT.md), so this is the shape a real regression takes, not
    # a worst case chosen to make the assertion easy.
    base_description = "Answers a question about the lab fabric. " * 8  # ~344 chars
    small = SimpleNamespace(mcp=_FakeMCP([base_description] * 5))
    doubled = SimpleNamespace(mcp=_FakeMCP([base_description * 2] * 5))

    small_report = mc.manifest_report(small)
    doubled_report = mc.manifest_report(doubled)

    assert doubled_report["manifest_chars"] > small_report["manifest_chars"] * 1.5, (
        "manifest_report did not notice a doubled description -- the "
        "pinned band in this file would not catch a real regression either"
    )
    # Sanity: same call, same input, same result -- not measuring noise.
    assert mc.manifest_report(small)["manifest_chars"] == small_report["manifest_chars"]


# --------------------------------------------------------------------------- #
# 2. The prompt cost.
# --------------------------------------------------------------------------- #


def test_report_prompt_sizes_are_pinned(mc):
    broken = mc.report_prompt_report("broken")
    refusal = mc.report_prompt_report("t0")

    assert broken["finding"] == "interface_line_down"
    assert refusal["finding"] == "undetermined"

    # Measured today: broken total 7,836 chars, t0 (refusal) 5,893.
    assert 6_000 <= broken["total_chars"] <= 11_000
    assert 4_000 <= refusal["total_chars"] <= 8_000

    # The B-421 cache split: the static half is the same prompt-template
    # text regardless of which descent produced it.
    assert broken["system_chars"] == refusal["system_chars"]
    # And the volatile half is not -- otherwise nothing case-specific would
    # ever reach the model at all (the vacuous version of this check).
    assert broken["user_chars"] != refusal["user_chars"]


def test_correlate_prompt_sizes_are_pinned(mc):
    broken = mc.correlate_prompt_report("PE2", "broken")
    healthy = mc.correlate_prompt_report("PE2", "healthy")

    assert broken["records_shown"] > healthy["records_shown"]

    # Measured today: broken total 15,603 chars, healthy 10,570.
    assert 12_000 <= broken["total_chars"] <= 20_000
    assert 8_000 <= healthy["total_chars"] <= 14_000
    assert broken["system_chars"] == healthy["system_chars"]
    assert broken["user_chars"] != healthy["user_chars"]


# --------------------------------------------------------------------------- #
# 3. The evidence cost.
# --------------------------------------------------------------------------- #


def test_fabric_evidence_cost_at_default_budget_is_pinned(mc):
    report = mc.evidence_report("broken")

    assert report["device_count"] == 9
    # Measured today: 25,054 raw chars across all nine devices' full
    # evidence collections (facts/interfaces/bgp/lldp/isis/sr).
    assert 15_000 <= report["raw_chars"] <= 40_000

    default = report["default_budget"]
    # The honest finding this measurement exists to surface: on this lab's
    # committed fixtures, the shipped defaults (4000/40000) never truncate
    # anything -- the raw total is well under the 40,000 total ceiling and
    # the largest single section is well under the 4,000 per-intent one.
    assert default["budgeted_chars"] == report["raw_chars"]
    assert default["sections_truncated"] == 0
    assert report["largest_section"]["chars"] < default["per_intent_chars"], (
        "if this fails, the largest section now exceeds the per-intent "
        "budget and 'truncated == 0' above should also have changed -- "
        "these two facts must move together"
    )


def test_the_truncation_mechanism_is_proven_live_not_just_dormant(mc):
    """Anti-vacuity companion (PROCESS.md Sec0.12) for the test above.

    "0 sections truncated at the defaults" is indistinguishable, from the
    outside, between "the mechanism works and there is nothing to truncate"
    and "the mechanism is broken and truncates nothing ever". This proves
    the second reading is false, on the *same* real fixture data, by
    tightening the budget until it has to engage.
    """

    report = mc.evidence_report("broken", tight_per_intent_chars=800, tight_total_chars=15000)
    tight = report["tightened_budget"]

    assert tight["budgeted_chars"] < report["raw_chars"]
    # Measured today: 65 sections truncated out of 54 (a section can be
    # truncated twice -- once per-intent, once again by the proportional
    # total-ceiling pass) at this tightened budget. A wide floor, since the
    # exact count depends on fixture content that may shift on a recapture.
    assert tight["sections_truncated"] >= 30


# --------------------------------------------------------------------------- #
# 4. Characters vs. tokens -- honest, not estimated.
# --------------------------------------------------------------------------- #


def test_tokens_are_reported_as_not_measured_without_a_tokeniser(mc, monkeypatch):
    """The position this task's rules require: if there is no offline
    tokeniser, say so plainly rather than multiplying by a remembered
    characters-per-token ratio and presenting that as a measurement.

    `tiktoken` is genuinely not installed in this project's dependencies
    (checked: neither `pyproject.toml` nor `pip list` names it) -- this
    forces the import to fail regardless of what happens to be on whichever
    machine runs the suite, so the "not installed" path is exercised
    deterministically rather than by environmental accident.
    """

    monkeypatch.setitem(sys.modules, "tiktoken", None)  # forces ImportError on `import tiktoken`

    result = mc.token_stats({"sample": "some prompt text"})

    assert result["measured"] is False
    assert "not" in result["reason"].lower()
    assert "tiktoken" in result["reason"]
    # No invented ratio anywhere in the refusal.
    assert "char_counts" in result
    assert "ratio" not in result


def test_tokens_are_measured_when_a_tokeniser_is_available(mc, monkeypatch):
    """The other half of the honesty rule: read literally, "if a tokeniser
    is available offline WITHOUT adding a dependency, report both and state
    the ratio observed" -- so the measured path must also work, not only be
    reachable in principle. `tiktoken` is not installed here (confirmed
    above), so this proves the code path with a minimal fake standing in
    for it, exercising the exact contract `token_stats` calls
    (`get_encoding(name).encode(text) -> list`) rather than the real
    library's tokenisation -- the plumbing is what this test is for, not a
    claim about a specific model's actual tokeniser.
    """

    from types import SimpleNamespace

    class _FakeEncoding:
        def encode(self, text):
            # A crude, deterministic stand-in: one "token" per 4 characters.
            return list(range(max(1, len(text) // 4)))

    fake_tiktoken = SimpleNamespace(get_encoding=lambda name: _FakeEncoding())
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)

    result = mc.token_stats({"a": "x" * 40, "b": "y" * 20})

    assert result["measured"] is True
    assert result["per_text"]["a"]["tokens"] == 10
    assert result["per_text"]["b"]["tokens"] == 5
    assert result["observed_chars_per_token"] == pytest.approx(4.0)


def test_the_full_report_states_tokens_are_not_measured_in_this_environment(mc):
    """End-to-end: the actual report this repo's CI produces today says
    tokens are not measured, honestly, rather than omitting the question."""

    report = mc.build_full_report()
    assert report["tokens"]["measured"] is False
    assert "tiktoken" in report["tokens"]["reason"]
