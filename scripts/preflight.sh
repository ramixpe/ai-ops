#!/usr/bin/env bash
#
# preflight.sh — repository and lab safety checks before a lab window.
#
# Exits non-zero on anything unexpected. Logs to a file so the run is evidence
# rather than scrollback.
#
# READ-ONLY. Nothing here writes to a device. The only writes are the log file
# and a fixture-replay temp dir.
#
# THIS FABRIC'S BASELINE NOISE IS DERIVED, NOT LISTED. `known_benign.py` reports
# every (rule, device, subject) that fires on the committed `healthy` fixtures --
# the label whose definition is "the rebuilt fabric with nothing wrong with it".
# Whatever fires there is the floor; anything else is new and is printed.
#
# The first version of this script carried a hand-written list of two rule names.
# Its first live run flagged three more as "read these", and all three were
# documented known state -- one of them (`suspicious_baseline`) was the rule
# SUCCEEDING, telling the operator not to trust PE2's and PE4's baselines.
#
# A hand-maintained exception list goes stale in the direction that matters: it
# cries wolf about the rules that are working, which trains the reader to skip
# the section where a real finding will eventually appear. §0.13's rules face,
# in a script whose entire job is to be read while tired.
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
    # The benign set is DERIVED, not listed. `known_benign.py` reports every
    # (rule, device, subject) that fires on the committed `healthy` fixtures --
    # the label defined as "the rebuilt fabric with nothing wrong with it". A
    # hand-written list of exceptions goes stale in the direction that matters:
    # it cries wolf about rules that are working, which trains the reader to
    # skip the section where a real finding will eventually appear. The first
    # version of this check did exactly that on its first live run.
    BENIGN="$(mktemp)"; LIVE="$(mktemp)"
    .venv/bin/python scripts/known_benign.py 2>/dev/null | sort -u > "$BENIGN"

    .venv/bin/python - "$OUT" <<'PYEOF' 2>/dev/null | sort -u > "$LIVE"
import json, sys
try:
    payload = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
devices = payload.get("devices") or payload.get("data", {}).get("devices") or {}
if isinstance(devices, dict):
    items = devices.items()
else:
    items = [(d.get("device"), d) for d in devices]
for name, verdict in items:
    for f in (verdict or {}).get("findings", []):
        print(f"{f.get('rule')}|{name}|{f.get('subject') or ''}")
PYEOF

    NB="$(wc -l < "$BENIGN" | tr -d ' ')"
    NL="$(wc -l < "$LIVE" | tr -d ' ')"
    NEW="$(comm -13 "$BENIGN" "$LIVE")"

    say ""
    if [ "$NL" -eq 0 ]; then
      warn "could not parse the health payload; read 'nettools health --all' by hand"
    else
      note "$NL finding(s) live; $NB are this fabric's floor, derived from the"
      note "committed 'healthy' fixtures rather than from a hand-written list."
      note "Known floor: bgp_no_prefixes (nothing is advertised here, B-210),"
      note "isis_adjacency_count_drift (baselines from the broken fabric, B-465),"
      note "interface_admin_up_line_down (PE1/PE3 Gi0/0/0/2.300, by design),"
      note "sr_policy_down (PE1), suspicious_baseline (PE2/PE4 -- this one is the"
      note "rule SUCCEEDING: it is telling you not to trust those baselines)."
      say ""
      if [ -n "$NEW" ]; then
        warn "findings NOT on this fabric's floor — read these:"
        printf '%s\n' "$NEW" | sed 's/^/        /' | tee -a "$LOG"
      else
        ok "nothing outside this fabric's known floor"
      fi
    fi
    rm -f "$BENIGN" "$LIVE"
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
