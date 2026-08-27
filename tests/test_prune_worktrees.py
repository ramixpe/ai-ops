"""Tests for scripts/prune_worktrees.py's pure classifier core.

The classifier is deliberately pure -- no git or filesystem access -- so these
tests inject synthetic data (the ``test_mutate_guards.py`` path-import
pattern). Integration against real worktrees is deliberately absent: after the
B-621 reclamation there are none, and a test that requires worktrees to exist
would either fail forever or force their recreation (the
``test_frozen_files.py`` self-skip precedent, taken one step further -- there
is nothing here worth even a skip).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "prune_worktrees",
    Path(__file__).resolve().parent.parent / "scripts" / "prune_worktrees.py",
)
pw = importlib.util.module_from_spec(_SPEC)
# Registered BEFORE exec: the module uses dataclasses under
# `from __future__ import annotations`, and dataclass field-type resolution
# looks the module up in sys.modules -- an unregistered module makes every
# dataclass definition raise at import.
sys.modules["prune_worktrees"] = pw
_SPEC.loader.exec_module(pw)


# --------------------------------------------------------------------------- #
# Noise and normalisation
# --------------------------------------------------------------------------- #


def test_generated_runtime_output_is_noise() -> None:
    for path in (
        "tickets/20260818T231301Z_10-255-0-12.md",
        "admission/device.PE1.0.lock",
        "session_memory/abc.json",
        ".env",
        "sub/.env",
        "run.log",
        "evidence-archive/x/y.jsonl",
    ):
        assert pw.is_noise(path), path


def test_authored_work_is_never_noise() -> None:
    """Positive control (OBS-181): the classifier must actually classify the
    things that matter, or the noise filter has eaten the verification."""

    for path in (
        "src/agent_nettools/descent.py",
        "tests/test_flows.py",
        "docs/build/FINDINGS.md",
        "scripts/mutate_guards.py",
        "tests/fixtures/cisco_xr/PE1/t0/show-version.txt",
        "docs/diagrams/high-level.html",
    ):
        assert not pw.is_noise(path), path


def test_normalise_makes_renumbered_ids_compare_equal() -> None:
    a = "see OBS-195 and B-513 for why"
    b = "see OBS-197 and B-516 for why"
    assert pw.normalise(a) == pw.normalise(b)


def test_normalise_does_not_erase_real_differences() -> None:
    """Anti-vacuity companion: normalisation must not turn every file into
    every other file."""

    assert pw.normalise("return False") != pw.normalise("return True")


# --------------------------------------------------------------------------- #
# classify_file -- the three tiers
# --------------------------------------------------------------------------- #

_BLOBS = frozenset({"aaaa", "bbbb"})


def test_tier_a_exact_blob_membership() -> None:
    v = pw.classify_file("src/x.py", "aaaa", "content", "", _BLOBS, [])
    assert v.tier == "A_BLOB"


def test_tier_b_renumbered_content_matches_history() -> None:
    wt_text = "row OBS-195 says hello"
    hist = [("deadbeefcafe", "row OBS-197 says hello")]
    v = pw.classify_file("docs/x.md", "cccc", wt_text, "", _BLOBS, hist)
    assert v.tier == "B_RENUMBERED"
    assert "deadbeefc" in v.evidence


def test_tier_c_added_lines_contained_in_history() -> None:
    diff = "+++ b/x.py\n+def genuinely_new_function():\n+    return compute(42)\n"
    hist = [("c1", "def genuinely_new_function():\n    return compute(42)\nplus other stuff")]
    v = pw.classify_file("src/x.py", "cccc", "wt text that matches nothing", diff, _BLOBS, hist)
    assert v.tier == "C_LINES"


def test_unproven_content_holds() -> None:
    diff = "+++ b/x.py\n+this line was never committed anywhere at all\n"
    v = pw.classify_file("src/x.py", "cccc", "unique text", diff, _BLOBS, [("c1", "unrelated")])
    assert v.tier == "HOLD"
    assert "absent from target history" in v.evidence


def test_a_real_zero_is_not_manufactured_from_absence() -> None:
    """An untracked file whose content matches nothing must HOLD, never pass --
    absence of proof is not proof of merging (the OBS-188 family)."""

    v = pw.classify_file("src/new.py", "cccc", "never merged", "", _BLOBS, [])
    assert v.tier == "HOLD"


# --------------------------------------------------------------------------- #
# classify_worktree -- verdict aggregation and the hard-fail classes
# --------------------------------------------------------------------------- #


def _safe(path: str) -> pw.FileVerdict:
    return pw.FileVerdict(path, "A_BLOB", "test")


def test_clean_worktree_is_clean() -> None:
    v = pw.classify_worktree("agent-x", [], ancestry_ok=True, classify_one=_safe)
    assert v.verdict == "CLEAN"


def test_all_proven_is_safe_to_prune() -> None:
    v = pw.classify_worktree("agent-x", [" M src/a.py", "?? tests/b.py"],
                             ancestry_ok=True, classify_one=_safe)
    assert v.verdict == "SAFE_TO_PRUNE"


def test_one_hold_holds_the_worktree() -> None:
    def one_bad(path: str) -> pw.FileVerdict:
        if path == "src/lost.py":
            return pw.FileVerdict(path, "HOLD", "never merged")
        return _safe(path)

    v = pw.classify_worktree("agent-x", [" M src/a.py", " M src/lost.py"],
                             ancestry_ok=True, classify_one=one_bad)
    assert v.verdict == "HOLD"
    assert any("lost.py" in r for r in v.reasons)


def test_unmerged_branch_commits_hold_without_classifying() -> None:
    v = pw.classify_worktree("agent-x", [" M src/a.py"], ancestry_ok=False,
                             classify_one=_safe)
    assert v.verdict == "HOLD"
    assert v.files == []  # never got as far as file classification


def test_staged_entries_hard_fail() -> None:
    """A staged entry is a shape the classifier proves nothing about; skipping
    it silently would be a wrong answer wearing a verdict (reviewer item 10)."""

    v = pw.classify_worktree("agent-x", ["M  src/staged.py"], ancestry_ok=True,
                             classify_one=_safe)
    assert v.verdict == "HOLD"
    assert any("staged" in r for r in v.reasons)


def test_deletions_hard_fail() -> None:
    v = pw.classify_worktree("agent-x", [" D src/deleted.py"], ancestry_ok=True,
                             classify_one=_safe)
    assert v.verdict == "HOLD"
    assert any("deletion" in r for r in v.reasons)


def test_noise_only_worktree_is_prunable() -> None:
    """A worktree whose only dirt is generated runtime output has nothing
    authored to lose."""

    def noise(path: str) -> pw.FileVerdict:
        return pw.FileVerdict(path, "NOISE")

    v = pw.classify_worktree("agent-x", ["?? tickets/t1.md", "?? tickets/t2.md"],
                             ancestry_ok=True, classify_one=noise)
    assert v.verdict == "SAFE_TO_PRUNE"


# --------------------------------------------------------------------------- #
# nontrivial_added_lines
# --------------------------------------------------------------------------- #


def test_added_lines_drop_trivia_and_headers() -> None:
    diff = "+++ b/x\n+}\n+\n+real content line here\n-removed\n context\n"
    assert pw.nontrivial_added_lines(diff) == ["real content line here"]


def test_added_lines_normalise_ids() -> None:
    diff = "+see OBS-195 for the reasoning\n"
    assert pw.nontrivial_added_lines(diff) == ["see OBS-N for the reasoning"]
