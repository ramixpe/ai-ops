"""Verify that agent worktrees' uncommitted work is represented in main, then prune.

Why this exists
---------------
Every background agent gets its own git worktree, and nothing removes them at
merge time. By 2026-08-19 fifty-one had accumulated (7.37 GB), and three tools
had failed three different ways because a directory full of repo copies is not
an ordinary directory: a repo-wide scan went 1s -> 4+ minutes (OBS-320), the
mutation harness's cache hygiene silently disabled itself (OBS-203), and a
killed run's leftover mutation was harder to localise (OBS-300). The durable
fix is that a merge wave ends with a **verified prune** instead of accumulation
-- this script is that verifier, kept permanent (B-621).

What "verified" means -- the tiered proof
-----------------------------------------
A worktree may be pruned only when every non-noise dirty file is *proven*
represented in the target branch. Three tiers, each mechanically checkable:

  Tier A -- blob-set membership. ``git hash-object`` (never ``-w``) the
    worktree file and test membership in the set of every blob reachable from
    the target branch (``git rev-list --objects``, measured at ~0.05s on this
    repo). Membership proves this exact content was committed at some point at
    some path -- it survives later edits (the intermediate blob still matches)
    and renames (membership is path-independent). Deliberately NOT shortcut
    with ``git cat-file -e``: the shared object DB also holds blobs written by
    other worktrees' staging and stashes, so DB existence does not prove
    main-reachability -- that shortcut is itself a false-safe.

  Tier B -- renumbering normalisation. Hand-merges renumbered backlog/finding
    ids (OBS-195 -> OBS-197 and friends), so a byte-compare legitimately fails
    on files whose only divergence is ``\\b(OBS|B)-\\d+\\b``. Normalise those
    tokens on both sides and against every historical blob of the path since
    the worktree's HEAD; a normalised match is a proven supersession, and the
    matching commit is recorded as evidence.

  Tier C -- line containment. For what remains: every nontrivial ``+`` line of
    the worktree's diff (id-normalised, whitespace-stripped) must appear in the
    union of the target's current and historical versions of that path. This is
    what ``git log -S`` would establish, answered directly.

  Anything failing all three -> HOLD. A held worktree is never touched; the
  operator sees the diff. ``git log -S`` may be used by a human as evidence on
  a hold, never by this script as an auto-safe verdict.

Design decisions the reviewer locked in (2026-08-19)
----------------------------------------------------
* Enumerate via ``git worktree list --porcelain``, never by globbing the
  directory -- only registered worktrees are removable via ``git worktree
  remove``, and porcelain is what distinguishes a registered worktree from a
  stray directory.
* The comparison base is each worktree's own ``rev-parse HEAD``. NOT the
  harness's recorded ``CLAUDE_BASE`` -- that was measured stale in 6 of 51
  worktrees, and HEAD is what ``git status`` actually diffs against.
* Deletions (`` D``) and staged entries hard-fail the worktree to HOLD rather
  than being silently skipped: both are shapes the classifier does not prove
  anything about, and a skipped class is a silent wrong answer (OBS-188's
  family).
* Branch deletion uses lowercase ``-d`` as a free, independent second guard:
  git itself refuses unless the branch is merged, so a ``-d`` failure
  contradicts this script's verdict and aborts the run.
* ``--dry-run`` is the default; ``--execute`` is explicit; the target branch is
  an argument (a ``master`` branch exists in this repo -- never hardcode);
  any HOLD exits nonzero; a dirty main refuses to start.

The classifier core is pure -- no git or filesystem access at import or in the
classification functions -- so tests inject synthetic data (the
``test_mutate_guards.py`` path-import pattern).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Paths (relative to a worktree root) that are generated runtime output, never
#: authored work. A dirty file matching one of these is noise, not signal.
#: `.env` is deliberately here for the CLASSIFIER -- credentials are handled by
#: a separate key-level sweep (B-621 phase 3), never by content comparison.
NOISE_PATTERNS = (
    re.compile(r"^tickets/"),
    re.compile(r"^admission/"),
    re.compile(r"^session_memory/"),
    re.compile(r"^evidence/"),
    re.compile(r"^evidence-archive/.*\.jsonl$"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)__pycache__(/|$)"),
    re.compile(r"(^|/)\.pytest_cache(/|$)"),
    re.compile(r"(^|/)\.ruff_cache(/|$)"),
    re.compile(r"(^|/)agent_nettools\.egg-info(/|$)"),
    re.compile(r"(^|/)\.venv(/|$)"),
    # (A docs/diagrams/*.svg pattern used to live here: the old generated
    # diagram set produced regenerable SVGs that were noise in a worktree
    # diff. Retired 2026-08-23 -- docs/diagrams/ is now a single hand-authored
    # HTML layer, and every file under it is authored work, not output, so it
    # classifies normally and needs no special-case entry.)
    re.compile(r"\.log$"),
    re.compile(r"\.jsonl$"),
)

#: The id shapes hand-merges renumber. Normalising them lets Tier B recognise
#: a file whose only divergence from a committed version is renumbering.
_ID_RE = re.compile(r"\b(OBS|B)-\d+\b")


def is_noise(path: str) -> bool:
    return any(p.search(path) for p in NOISE_PATTERNS)


def normalise(text: str) -> str:
    """Id-normalised, whitespace-normalised form used by Tiers B and C."""

    out = []
    for line in _ID_RE.sub(r"\1-N", text).splitlines():
        stripped = " ".join(line.split())
        out.append(stripped)
    return "\n".join(out)


def nontrivial_added_lines(diff_text: str) -> list[str]:
    """The ``+`` lines of a unified diff worth proving, normalised.

    Drops the ``+++`` header, blank lines, and single-character/punctuation
    lines -- a ``}`` or a lone ``)`` proves nothing about provenance.
    """

    lines = []
    for raw in diff_text.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        body = " ".join(_ID_RE.sub(r"\1-N", raw[1:]).split())
        if len(body) > 3:
            lines.append(body)
    return lines


@dataclass
class FileVerdict:
    path: str
    tier: str  # NOISE | A_BLOB | B_RENUMBERED | C_LINES | HOLD
    evidence: str = ""


@dataclass
class WorktreeVerdict:
    name: str
    verdict: str  # CLEAN | SAFE_TO_PRUNE | HOLD
    reasons: list[str] = field(default_factory=list)
    files: list[FileVerdict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure classifier -- everything above the gather layer is injectable.
# --------------------------------------------------------------------------- #


def classify_file(
    path: str,
    worktree_blob: str,
    worktree_text: str | None,
    diff_text: str,
    main_blobs: frozenset[str],
    history_texts: list[tuple[str, str]],
) -> FileVerdict:
    """Classify one dirty file. ``history_texts`` is ``[(commit, text)]`` for
    the path in the target branch: current version first, then every version
    committed since the worktree's HEAD."""

    if is_noise(path):
        return FileVerdict(path, "NOISE")
    if worktree_blob in main_blobs:
        return FileVerdict(path, "A_BLOB", "exact content committed to target")
    if worktree_text is not None:
        wt_norm = normalise(worktree_text)
        for commit, text in history_texts:
            if normalise(text) == wt_norm:
                return FileVerdict(path, "B_RENUMBERED", f"normalised match at {commit[:9]}")
    added = nontrivial_added_lines(diff_text)
    if not added and diff_text.strip():
        # A tracked modification whose diff adds nothing (pure deletion or
        # whitespace churn) has no authored content to lose -- the removal
        # either also happened in main or main's version supersedes it, and
        # either way there is no line here that pruning could destroy.
        return FileVerdict(path, "C_LINES", "diff adds no nontrivial lines")
    if not added and worktree_text is not None:
        # Untracked file: there is no diff-vs-HEAD, so treat the WHOLE content
        # as added lines and demand containment, exactly as Tier C does for a
        # modification. Without this, an untracked file whose every line is in
        # main still held (measured: test_ticket.py, whose exact blob never
        # landed because its forgery bug was fixed before commit -- OBS-176).
        added = nontrivial_added_lines(
            "\n".join("+" + ln for ln in worktree_text.splitlines())
        )
        if not added:
            return FileVerdict(path, "C_LINES", "untracked file with no nontrivial content")
    if added:
        union = "\n".join(normalise(t) for _, t in history_texts)
        missing = [ln for ln in added if ln not in union]
        if not missing:
            return FileVerdict(path, "C_LINES", f"all {len(added)} added lines present in target history")
        return FileVerdict(path, "HOLD", f"{len(missing)} added line(s) absent from target history; first: {missing[0][:80]!r}")
    return FileVerdict(path, "HOLD", "unreadable content")


def classify_worktree(
    name: str,
    status_lines: list[str],
    ancestry_ok: bool,
    classify_one,
) -> WorktreeVerdict:
    """Pure per-worktree verdict from porcelain status lines.

    ``classify_one(path)`` -> FileVerdict is injected so tests never touch git.
    """

    v = WorktreeVerdict(name, "CLEAN")
    if not ancestry_ok:
        v.verdict = "HOLD"
        v.reasons.append("branch holds commits not on the target branch")
        return v
    for line in status_lines:
        if not line.strip():
            continue
        index_state, wt_state, path = line[0], line[1], line[3:].strip()
        # Hard-fail classes the classifier proves nothing about (reviewer item 10).
        if index_state not in (" ", "?"):
            v.verdict = "HOLD"
            v.reasons.append(f"staged entry: {line!r}")
            continue
        if "D" in (index_state, wt_state):
            v.verdict = "HOLD"
            v.reasons.append(f"deletion: {line!r}")
            continue
        fv = classify_one(path)
        v.files.append(fv)
        if fv.tier == "HOLD":
            v.verdict = "HOLD"
            v.reasons.append(f"{path}: {fv.evidence}")
    if v.verdict != "HOLD" and any(f.tier != "NOISE" for f in v.files):
        v.verdict = "SAFE_TO_PRUNE"
    if v.verdict != "HOLD" and all(f.tier == "NOISE" for f in v.files) and v.files:
        v.verdict = "SAFE_TO_PRUNE"  # only noise: nothing authored to lose
    return v


# --------------------------------------------------------------------------- #
# Gather layer -- the only part that talks to git.
# --------------------------------------------------------------------------- #


def pathlib_rel(child: Path, root: Path) -> Path:
    return child.relative_to(root)


def _run(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd or REPO, capture_output=True, text=True).stdout


def registered_worktrees() -> list[Path]:
    out, current = [], None
    for line in _run(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            current = Path(line.split(" ", 1)[1])
            if current != REPO:
                out.append(current)
    return out


def main_blob_set(target: str) -> frozenset[str]:
    listing = _run(["git", "rev-list", "--objects", target])
    oids = "\n".join(line.split(" ", 1)[0] for line in listing.splitlines() if line)
    batch = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        cwd=REPO, input=oids, capture_output=True, text=True,
    ).stdout
    return frozenset(
        line.split()[0] for line in batch.splitlines() if line.endswith(" blob")
    )


def history_texts_for(path: str, since: str, target: str) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    show = subprocess.run(["git", "show", f"{target}:{path}"], cwd=REPO, capture_output=True, text=True)
    if show.returncode == 0:
        texts.append((f"{target}(current)", show.stdout))
    commits = _run(["git", "log", f"{since}..{target}", "--format=%H", "--", path]).split()
    for c in commits:
        s = subprocess.run(["git", "show", f"{c}:{path}"], cwd=REPO, capture_output=True, text=True)
        if s.returncode == 0:
            texts.append((c, s.stdout))
    return texts


def verify_all(target: str) -> list[WorktreeVerdict]:
    blobs = main_blob_set(target)
    verdicts = []
    for wt in registered_worktrees():
        name = wt.name
        head = _run(["git", "-C", str(wt), "rev-parse", "HEAD"]).strip()
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, target], cwd=REPO
        ).returncode == 0
        status = _run(["git", "-C", str(wt), "status", "--porcelain"]).splitlines()

        def classify_one(path: str, _wt=wt, _head=head) -> FileVerdict:
            f = _wt / path
            if is_noise(path):
                return FileVerdict(path, "NOISE")
            if f.is_dir():
                # `git status --porcelain` collapses an untracked directory to
                # one `dir/` entry. Expand it: every file inside must prove
                # itself individually, or the directory holds.
                sub = [classify_one(str(pathlib_rel(c, _wt)), _wt, _head)
                       for c in sorted(f.rglob("*")) if c.is_file()]
                bad = [x for x in sub if x.tier == "HOLD"]
                if bad:
                    return FileVerdict(path, "HOLD", f"{len(bad)} file(s) inside unproven; first: {bad[0].path}")
                return FileVerdict(path, "C_LINES", f"all {len(sub)} contained files proven")
            try:
                blob = _run(["git", "hash-object", "--", str(f)]).strip()
                text = f.read_text(errors="replace")
            except (OSError, IsADirectoryError):
                return FileVerdict(path, "HOLD", "unreadable")
            diff = _run(["git", "-C", str(_wt), "diff", "HEAD", "--", path])
            return classify_file(path, blob, text, diff, blobs,
                                 history_texts_for(path, _head, target))

        verdicts.append(classify_worktree(name, status, ancestry, classify_one))
    return verdicts


def prune(verdicts: list[WorktreeVerdict], execute: bool) -> int:
    held = [v for v in verdicts if v.verdict == "HOLD"]
    for v in verdicts:
        if v.verdict == "HOLD":
            print(f"HOLD  {v.name}: {'; '.join(v.reasons)[:200]}")
            continue
        path = REPO / ".claude/worktrees" / v.name
        branch = f"worktree-{v.name}"
        if not execute:
            print(f"DRY   {v.name}: {v.verdict} ({len(v.files)} classified)")
            continue
        rm = subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=REPO,
                            capture_output=True, text=True)
        if rm.returncode != 0:
            print(f"ABORT {v.name}: worktree remove failed: {rm.stderr.strip()[:200]}")
            return 2
        # Lowercase -d: git's own merged-check as an independent second guard.
        bd = subprocess.run(["git", "branch", "-d", branch], cwd=REPO,
                            capture_output=True, text=True)
        if bd.returncode != 0:
            print(f"ABORT {v.name}: branch -d refused (contradicts verdict!): {bd.stderr.strip()[:200]}")
            return 2
        print(f"PRUNED {v.name}")
    if execute:
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
    return 1 if held else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=None, help="target branch (default: current branch of the main worktree)")
    ap.add_argument("--execute", action="store_true", help="actually prune (default: dry run)")
    ap.add_argument("--json", type=Path, default=None, help="write the verdict manifest here")
    args = ap.parse_args(argv)

    if _run(["git", "status", "--porcelain"]).strip():
        print("REFUSING: the main worktree is dirty -- commit or stash first.")
        return 2
    target = args.target or _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    verdicts = verify_all(target)
    if args.json:
        args.json.write_text(json.dumps(
            [{"name": v.name, "verdict": v.verdict, "reasons": v.reasons,
              "files": [{"path": f.path, "tier": f.tier, "evidence": f.evidence} for f in v.files]}
             for v in verdicts], indent=1))
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    print(f"verdicts: {counts or 'no worktrees'}")
    return prune(verdicts, args.execute)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
