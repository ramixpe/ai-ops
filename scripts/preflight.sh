#!/usr/bin/env bash
#
# preflight.sh — everything §2 of OPERATOR-RUNBOOK.md asks for, before a lab window.
#
# Exits non-zero on anything unexpected. Logs to a file so the run is evidence
# rather than scrollback.
#
# READ-ONLY. Nothing here writes to a device. The only writes are the log file
# and a fixture-replay temp dir.
#
# TWO FINDINGS ARE KNOWN-BENIGN ON THIS FABRIC and are printed by name so they
# do not read as alarms:
#
#   bgp_no_prefixes             Every BGP session in this lab carries 0 prefixes;
#                               nothing is advertised into it. The operator's own
#                               note in inventory/lab.yaml says so. B-210 is the
#                               fix (operator knowledge in the descent).
#   isis_adjacency_count_drift  Every `expected:` baseline was derived by
#                               learn-topology from the BROKEN fabric and never
#                               re-derived. Since B-465 an increase is `info` and
#                               says the baseline is stale. B-465's other half —
#                               re-running learn-topology — needs the lab.
#
# Usage:  scripts/preflight.sh [--log PATH] [--skip-lab]
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 2

LOG="preflight-$(date +%Y%m%d-%H%M%S).log"
SKIP_LAB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --log) LOG="$2"; shift 2 ;;
    --skip-lab) SKIP_LAB=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FAILED=0
WARNED=0

say()  { printf '%s\n' "$*" | tee -a "$LOG"; }
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*" | tee -a "$LOG" >/dev/null; printf '  OK    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*" | tee -a "$LOG"; FAILED=$((FAILED+1)); }
warn() { printf '  WARN  %s\n' "$*" | tee -a "$LOG"; WARNED=$((WARNED+1)); }
note() { printf '        %s\n' "$*" | tee -a "$LOG"; }

say "preflight $(date -u +%Y-%m-%dT%H:%M:%SZ)  repo=$REPO  log=$LOG"
say ""

# --------------------------------------------------------------------------- 1
say "1. Repository"

if [ -n "$(git status --porcelain)" ]; then
  warn "working tree is dirty — a round's seal must be committed AND pushed before the fault"
  git status --short | sed 's/^/        /' | tee -a "$LOG"
else
  ok "working tree clean"
fi

BRANCH="$(git branch --show-current)"
ok "branch $BRANCH at $(git rev-parse --short HEAD)"

if git rev-parse '@{u}' >/dev/null 2>&1; then
  AHEAD="$(git rev-list --count '@{u}'..HEAD)"
  if [ "$AHEAD" -gt 0 ]; then
    fail "$AHEAD commit(s) not pushed — §6.1: a local commit is not a seal"
  else
    ok "in sync with $(git rev-parse --abbrev-ref '@{u}')"
  fi
else
  fail "no upstream configured — the seal cannot be pushed"
fi

# --------------------------------------------------------------------------- 2
say ""
say "2. Frozen files (BUILD-PLAN §0.5) — blob hashes against 6629a2c"

for f in tests/test_safety.py tests/test_template_security.py \
         src/agent_nettools/platforms.py src/agent_nettools/templates.py; do
  base="$(git rev-parse "6629a2c:$f" 2>/dev/null)"
  head="$(git rev-parse "HEAD:$f" 2>/dev/null)"
  if [ -n "$base" ] && [ "$base" = "$head" ]; then
    ok "$f identical (${base:0:12})"
  else
    fail "$f DIFFERS from the T-001 baseline — HALT, do not run anything"
  fi
done

# --------------------------------------------------------------------------- 3
say ""
say "3. Suite and lint"

if [ ! -x .venv/bin/python ]; then
  fail ".venv missing — run 'make setup' first"
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if OUT="$(ruff check . 2>&1)"; then
    ok "ruff clean"
  else
    fail "ruff failed"; printf '%s\n' "$OUT" | sed 's/^/        /' | tee -a "$LOG"
  fi

  if OUT="$(python -m pytest -q 2>&1)"; then
    ok "$(printf '%s' "$OUT" | tail -1)"
  else
    fail "pytest failed"; printf '%s' "$OUT" | tail -20 | sed 's/^/        /' | tee -a "$LOG"
  fi
fi

# --------------------------------------------------------------------------- 4
say ""
say "4. Offline demo — the README's opening claim, with the environment stripped"

if [ -x .venv/bin/python ]; then
  if env -u DEVICE_USERNAME -u DEVICE_PASSWORD -u DEVICE_SSH_KEYFILE \
         -u ANTHROPIC_API_KEY -u OPENAI_API_KEY \
         NETTOOLS_INVENTORY="$REPO/inventory/lab.yaml" \
         .venv/bin/nettools investigate RR1 10.255.0.12 --from-fixtures --quiet \
         >/dev/null 2>&1; then
    ok "fixture replay runs (exit 1 = fault found on the path, as expected)"
  else
    rc=$?
    if [ "$rc" -eq 1 ]; then
      ok "fixture replay runs, exit 1 = fault found on the path (expected)"
    else
      fail "fixture replay exited $rc — expected 1"
    fi
  fi
fi

# --------------------------------------------------------------------------- 5
say ""
say "5. Fault injector"

FL="$HOME/ai-agent-ops/faultlab"
if [ -d "$FL" ]; then
  ok "faultlab present at $FL"
  if git -C "$FL" rev-parse --git-dir >/dev/null 2>&1; then
    ok "faultlab is a git repository"
  else
    warn "faultlab is NOT a git repository (OBS-135)"
    note "a payload written there is not archived by §6.1d's definition."
    note "round8b.py already defaults into evidence-archive/; anything else"
    note "must be copied in with scripts/archive.sh and committed."
  fi
  for s in fault_lab.py round8b.py; do
    if [ -f "$FL/$s" ]; then
      if python3 -c "import ast,sys; ast.parse(open('$FL/$s').read())" 2>/dev/null; then
        ok "$s parses"
      else
        fail "$s has a syntax error"
      fi
    else
      fail "$s missing from $FL"
    fi
  done
  if grep -q "bgp_shut_plus_spare_port_down" "$FL/fault_lab.py" 2>/dev/null; then
    ok "fault option 8 present (round 6's two-fault entry)"
  else
    fail "fault option 8 missing — round 6 cannot run as sealed"
  fi
  if grep -q 'interface {SPARE_IF}' "$FL/fault_lab.py" 2>/dev/null; then
    ok "SPARE_IF is in SNAPSHOT_SECTIONS — the restore can see the spare port"
  else
    fail "SPARE_IF not snapshotted — a lingering shutdown would be invisible"
  fi
else
  warn "faultlab not found at $FL — lab tasks cannot run"
fi

# --------------------------------------------------------------------------- 6
say ""
say "6. Lab"

if [ "$SKIP_LAB" -eq 1 ]; then
  note "skipped (--skip-lab)"
elif [ ! -x .venv/bin/nettools ]; then
  warn "nettools not installed; skipping lab checks"
else
  if [ ! -f .env ]; then
    warn ".env absent — device checks will fail without credentials"
  fi

  if OUT="$(.venv/bin/nettools health --all --format json 2>&1)"; then
    LABRC=0
  else
    LABRC=$?
  fi

  case "$LABRC" in
    0) ok "fabric health: ok/info" ;;
    1) ok "fabric health: warning (see the known-benign note below)" ;;
    2) fail "fabric health: CRITICAL — do not start a round on a fabric that is already broken" ;;
    *) fail "nettools health --all could not run (exit $LABRC)" ;;
  esac

  if [ "$LABRC" -le 1 ] && [ -n "${OUT:-}" ]; then
    BENIGN=$(printf '%s' "$OUT" | grep -c 'bgp_no_prefixes\|isis_adjacency_count_drift' || true)
    OTHER=$(printf '%s' "$OUT" \
      | grep -o '"rule": "[a-z_]*"' | sort -u \
      | grep -v 'bgp_no_prefixes\|isis_adjacency_count_drift' || true)
    say ""
    say "  Known-benign on this fabric — $BENIGN finding(s), NOT alarms:"
    note "bgp_no_prefixes            nothing is advertised into any session here."
    note "                           inventory/lab.yaml records this. B-210 is the fix."
    note "isis_adjacency_count_drift baselines were derived from the BROKEN fabric and"
    note "                           never re-derived. Since B-465 an increase is 'info'."
    note "                           Re-running learn-topology is B-465's other half."
    if [ -n "$OTHER" ]; then
      say ""
      say "  Findings that are NOT on the benign list — read these:"
      printf '%s\n' "$OTHER" | sed 's/^/        /' | tee -a "$LOG"
    else
      say ""
      ok "no findings outside the known-benign set"
    fi
  fi

  # Round 6 specifically: the spare port must be up AND off-path.
  say ""
  say "  Round 6 preconditions (ROUND-6.md §4):"
  if .venv/bin/nettools interface PE2 GigabitEthernet0/0/0/2 --quiet >/dev/null 2>&1; then
    ok "PE2 Gi0/0/0/2 readable — confirm up/up in the output before running round 6"
  else
    warn "could not read PE2 Gi0/0/0/2"
  fi
  note "OFF-PATH cannot be checked mechanically here. Run:"
  note "    nettools route PE2 10.255.0.31"
  note "and confirm the egress is NOT Gi0/0/0/2. If it is, round 6 is void as sealed."
fi

# --------------------------------------------------------------------------- 7
say ""
say "─────────────────────────────────────────────────────────────"
if [ "$FAILED" -gt 0 ]; then
  say "PREFLIGHT FAILED — $FAILED failure(s), $WARNED warning(s). Log: $LOG"
  exit 1
fi
if [ "$WARNED" -gt 0 ]; then
  say "PREFLIGHT PASSED with $WARNED warning(s). Read them. Log: $LOG"
  exit 0
fi
say "PREFLIGHT PASSED. Log: $LOG"
exit 0
