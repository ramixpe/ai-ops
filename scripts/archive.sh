#!/usr/bin/env bash
#
# archive.sh <round> <dir> — put a round's payload where §6.1d says it belongs.
#
#     scripts/archive.sh round8b ~/ai-agent-ops/faultlab/round8b/20260818-101500
#
# WHY THIS EXISTS. chaos-harness.md §6.1d:
#
#     A round is archived when its payload is committed. Until then it is a
#     working file that happens to still exist.
#
# Round 7 lost a 158-sample result to a tidy-up. A blanket `*.jsonl` in
# .gitignore silently dropped every sample from an archive that otherwise looked
# complete (OBS-131). Round 8's payload was never archived at all, because it
# was written to a directory that is not a git repository (OBS-135).
#
# THE PROCEDURE FACE APPLIES TO THE TOOL THAT IMPLEMENTS IT. A script that
# copies files and reports success has done exactly what the rule that produced
# OBS-131 did: performed every step, and possibly produced nothing. So this
# script does not report success until `git ls-files` lists what it wrote —
# checking the END STATE, not the steps. If git will not track a file, this
# exits non-zero and says which file and why.
#
# It stages but does not commit: the commit message is a judgement about what
# the round found, and this script does not know that.
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat >&2 <<'EOF'
usage: archive.sh <round> <dir>

  <round>  round name, e.g. round8b        -> evidence-archive/<round>/
  <dir>    the run directory to archive    (its basename is preserved)

Stages the payload and verifies with `git ls-files` that every file is tracked.
Does not commit — the message is yours to write.
EOF
  exit 2
}

[ $# -eq 2 ] || usage
ROUND="$1"
SRC="$2"

[ -d "$SRC" ] || { echo "archive.sh: no such directory: $SRC" >&2; exit 2; }

case "$ROUND" in
  */*|"") echo "archive.sh: <round> must be a plain name, got '$ROUND'" >&2; exit 2 ;;
esac

cd "$REPO" || exit 2

STAMP="$(basename "$SRC")"
DEST="evidence-archive/$ROUND/$STAMP"

echo "archive.sh: $SRC"
echo "         -> $DEST"
echo

# --------------------------------------------------------------------------- copy
mkdir -p "$DEST" || exit 2

COPIED=0
while IFS= read -r -d '' f; do
  cp -p "$f" "$DEST/" || { echo "  FAIL  could not copy $f" >&2; exit 1; }
  COPIED=$((COPIED+1))
done < <(find "$SRC" -maxdepth 1 -type f -print0)

if [ "$COPIED" -eq 0 ]; then
  echo "  FAIL  $SRC contains no files. Nothing to archive." >&2
  exit 1
fi
echo "  copied $COPIED file(s)"

# The instrument, if it is beside the run rather than in it. Round 8's numbers
# were wrong because of two regexes in a file that was not kept: a payload
# recording what an instrument concluded, without the instrument, cannot be
# re-read against a defect in it.
INSTRUMENT="$(dirname "$SRC")/../${ROUND}.py"
if [ -f "$INSTRUMENT" ] && [ ! -f "$DEST/${ROUND}.py" ]; then
  cp -p "$INSTRUMENT" "$DEST/" && echo "  copied the instrument: ${ROUND}.py"
fi

# --------------------------------------------------------------------------- ignore check
# BEFORE forcing. This is the step that was missing on the first draft, and its
# absence made the verification below nearly vacuous: `git add -f` beats any
# ignore rule, so `git ls-files` afterwards would confirm success no matter what
# .gitignore said. A check that cannot fail is §0.12, and finding one in the
# script written to enforce §6.1d is worth the extra ten lines.
#
# So: report the rule, then force anyway. Both halves matter. Forcing keeps THIS
# payload; reporting is how the ignore rule gets fixed before the next round is
# archived by hand without this script.
IGNORED=0
for f in "$DEST"/*; do
  [ -f "$f" ] || continue
  # `check-ignore -v` reports the last matching pattern, INCLUDING negations —
  # and a pattern beginning `!` is a rescue, not a swallow. Reporting those as
  # near-misses is a false positive, and a warning that cries wolf about the
  # rules that are working is worse than no warning: it trains the reader to
  # skip the section where the real one will appear.
  REASON="$(git check-ignore -v --no-index "$f" 2>/dev/null)" || continue
  # Format is  <source>:<line>:<pattern>\t<path>  -- the pattern is the third
  # colon-field of the FIRST tab-field, not the second tab-field (that is the
  # path). Getting this wrong printed every rescue as a near-miss.
  PATTERN="$(printf '%s' "$REASON" | cut -f1 | cut -d: -f3-)"
  case "$PATTERN" in
    "!"*) continue ;;
  esac
  if true; then
    if [ "$IGNORED" -eq 0 ]; then
      echo >&2
      echo "  WARNING — .gitignore would have swallowed part of this payload:" >&2
    fi
    printf '      %s\n' "$REASON" >&2
    IGNORED=$((IGNORED+1))
  fi
done
if [ "$IGNORED" -gt 0 ]; then
  echo >&2
  echo "      $IGNORED file(s) matched an ignore rule. Forcing them in, so this" >&2
  echo "      payload survives — but FIX THE RULE. A .gitignore match is the" >&2
  echo "      absence of an event: nothing warns when it swallows the next" >&2
  echo "      round, and that is how round 7 lost 158 samples (OBS-131)." >&2
  echo >&2
fi

# --------------------------------------------------------------------------- stage
# -f deliberately: evidence-archive/ has been swallowed by a blanket ignore
# before, and the payload matters more than the rule being right today.
git add -f "$DEST" || { echo "  FAIL  git add failed" >&2; exit 1; }

# --------------------------------------------------------------------------- VERIFY
# The end state, not the steps.
echo
echo "  verifying with git ls-files (the check OBS-131 exists for)..."

MISSING=0
for f in "$DEST"/*; do
  [ -f "$f" ] || continue
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    printf '    tracked   %s\n' "$(basename "$f")"
  else
    printf '    NOT TRACKED  %s\n' "$f" >&2
    if REASON="$(git check-ignore -v "$f" 2>/dev/null)"; then
      printf '                 ignored by: %s\n' "$REASON" >&2
    fi
    MISSING=$((MISSING+1))
  fi
done

if [ "$MISSING" -gt 0 ]; then
  echo >&2
  echo "  FAIL  $MISSING file(s) are on disk and NOT tracked by git." >&2
  echo "        This is exactly OBS-131: every step succeeded and the archive" >&2
  echo "        is empty. Fix the ignore rule; do not commit and call it done." >&2
  exit 1
fi

TRACKED="$(git ls-files "$DEST" | wc -l | tr -d ' ')"
echo
echo "  $TRACKED file(s) staged and tracked."
echo
echo "  NOT YET ARCHIVED — staged is not committed. Finish with:"
echo
echo "      git commit -m 'evidence: archive $ROUND $STAMP'"
echo "      git push"
echo
echo "  And §6.1 wants the push, not the commit: a local commit is not a seal."
exit 0
