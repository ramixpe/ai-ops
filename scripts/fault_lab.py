#!/usr/bin/env python3
"""
fault_lab.py — apply and revert scoped faults for blind diagnosis trials.

Runs a session of rounds. Each round: pick a fault, apply it, hold it while an
investigation runs elsewhere, revert it, verify the device config is byte-identical
to the pre-fault snapshot.

Two log files per session:

    runs/<session>/public.log   timings and events, NO fault identity. Safe to share
                                while the trial is running.
    runs/<session>/truth.jsonl  what was actually applied. SEALED until comparison.

Safety
    - The fabric is ALWAYS restored: on completion, on Ctrl+C, on exception, on
      timeout, and on SIGTERM.
    - Restore is verified by config snapshot comparison, never by trusting the write.
      A write that reports failure may have succeeded (and vice versa) — see B-412.
    - Restore is idempotent and retried.
    - A watchdog auto-reverts after --max-hold minutes even if nobody presses Enter.

NOT part of ios-xr-nettools. That tree cannot configure a device and must stay that
way. This script lives outside it deliberately.

Usage
    python fault_lab.py --preflight              # dump config sections, change nothing
    python fault_lab.py --dry-run                # full flow, no device writes
    python fault_lab.py                          # menu: you choose each round
    python fault_lab.py --random --rounds 4      # script chooses, nobody knows
"""

from __future__ import annotations

import argparse
import atexit
import difflib
import json
import os
import random
import signal
import sys
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("pip install python-dotenv netmiko", file=sys.stderr)
    raise
try:
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoTimeoutException, ReadTimeout
except ImportError:
    print("pip install python-dotenv netmiko", file=sys.stderr)
    raise

# --------------------------------------------------------------------------- config
# VERIFY THESE AGAINST YOUR FABRIC WITH --preflight BEFORE RUNNING ANY FAULT.

# TARGET_DEVICE = "PE3"
# TARGET_HOST = "172.20.250.23"

TARGET_DEVICE = "PE2"
TARGET_HOST   = "172.20.250.22"

RR_NEIGHBOR   = "10.255.0.31"        # RR1's loopback — the neighbour PE2 peers with
SUBJECT       = "RR1 10.255.0.12"    # PE2's own loopback — confirm below

ISIS_PROCESS = "CORE"
BGP_AS = "65000"
CORE_IF_A = "GigabitEthernet0/0/0/0"
CORE_IF_B = "GigabitEthernet0/0/0/1"

# The spare. Physical, up today, and NOT on PE2's path back toward RR1 -- which
# is what makes it reviewer B's "old, intentionally shut spare interface"
# (ROUND-6.md). Confirm both facts with --preflight before round 6: if the path
# ever egresses this port, the round is void before it starts.
SPARE_IF = "GigabitEthernet0/0/0/2"


# SUBJECT = "RR1 10.255.0.13"        # what the investigator is asked to diagnose

# Config sections captured before and after every change. Restore verification is
# an exact comparison of these, so no parsing is involved anywhere.
SNAPSHOT_SECTIONS = [
    f"show running-config interface {CORE_IF_A}",
    f"show running-config interface {CORE_IF_B}",
    # Added for round 6, and it is not optional. Restore verification is an
    # exact comparison of THESE SECTIONS ONLY, so a section that is not listed
    # cannot fail the check no matter what is left in it. A `shutdown` lingering
    # on a spare port is inert on a restored fabric and invisible to a snapshot
    # that never reads the port -- harmless and undetectable, which ROUND-6.md
    # §0.1 names as the combination worth checking for. It would have arrived
    # here, in the verification machinery, rather than in the fault.
    f"show running-config interface {SPARE_IF}",
    "show running-config interface Loopback0",
    f"show running-config router isis {ISIS_PROCESS}",
    f"show running-config router bgp {BGP_AS}",
]

# Descriptions are factual, never predictive. No expected rung appears anywhere in
# this file — a recorded prediction would contaminate the comparison.
FAULTS = {
    1: {
        "id": "isis_shut_both",
        "name": "IS-IS shutdown on both core interfaces",
        "note": "physical interfaces stay up/up",
        "apply": [
            f"router isis {ISIS_PROCESS}",
            f" interface {CORE_IF_A}",
            "  shutdown",
            " exit",
            f" interface {CORE_IF_B}",
            "  shutdown",
        ],
        "revert": [
            f"router isis {ISIS_PROCESS}",
            f" interface {CORE_IF_A}",
            "  no shutdown",
            " exit",
            f" interface {CORE_IF_B}",
            "  no shutdown",
        ],
    },
    2: {
        "id": "phys_shut_both",
        "name": "Physical shutdown on both core interfaces",
        "note": "",
        "apply": [f"interface {CORE_IF_A}", " shutdown",
                  f"interface {CORE_IF_B}", " shutdown"],
        "revert": [f"interface {CORE_IF_A}", " no shutdown",
                   f"interface {CORE_IF_B}", " no shutdown"],
    },
    3: {
        "id": "phys_shut_one",
        "name": "Physical shutdown on one core interface",
        "note": "the IGP has an alternate path",
        "apply": [f"interface {CORE_IF_A}", " shutdown"],
        "revert": [f"interface {CORE_IF_A}", " no shutdown"],
    },
    4: {
        "id": "loopback_shut",
        "name": "Shutdown Loopback0",
        "note": "core links untouched",
        "apply": ["interface Loopback0", " shutdown"],
        "revert": ["interface Loopback0", " no shutdown"],
    },
    5: {
        "id": "bgp_neighbor_shut",
        "name": "Administratively shut the BGP neighbor toward RR1",
        "note": "everything below BGP untouched",
        "apply": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}", "  shutdown"],
        "revert": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}", "  no shutdown"],
    },
    6: {
        "id": "bgp_password",
        "name": "Set an MD5 password on the BGP neighbor toward RR1",
        "note": "one side only",
        "apply": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}",
                  "  password clear FaultLabTrial"],
        "revert": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}", "  no password"],
    },
    8: {
        "id": "bgp_shut_plus_spare_port_down",
        "name": "BGP neighbour shut, AND an unrelated spare port shut",
        "note": ("two concurrent faults -- the only entry here that applies more "
                 "than one. The spare port is off the path and must stay off it"),
        "apply":  [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}", "  shutdown",
                   "!", f"interface {SPARE_IF}", " shutdown"],
        "revert": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}", "  no shutdown",
                   "!", f"interface {SPARE_IF}", " no shutdown"],
    },
    7: {
        "id": "bgp_remote_as_wrong",
        "name": "Wrong remote-AS on the neighbour toward RR1",
        "note": "TCP establishes; the OPEN is rejected on the AS check",
        "apply":  [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}",
                "  remote-as 65001",
                "  ebgp-multihop 5"],
        "revert": [f"router bgp {BGP_AS}", f" neighbor {RR_NEIGHBOR}",
                f"  remote-as {BGP_AS}",
                "  no ebgp-multihop"],
    },
}


# Lines that change between identical reads. IOS-XR prepends a timestamp banner to
# every show command, so a raw comparison of two unchanged snapshots always differs.
# This is the same class as parsers.volatile_fields and the reason B-412 happened.
VOLATILE_LINE = re.compile(
    r"^\s*(?:"
    r"\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"      # Fri Aug 16 13:50:28.721 UTC
    r"|Building configuration"
    r"|!! Last configuration change"
    r"|!! IOS XR Configuration"
    r"|Wed|Thu|Fri|Sat|Sun|Mon|Tue"
    r")",
    re.IGNORECASE,
)


def normalise(text: str) -> str:
    """Strip volatile and cosmetic lines so two reads of unchanged config match."""
    keep = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s or s.strip() == "!":
            continue
        if VOLATILE_LINE.match(s):
            continue
        keep.append(s)
    return "\n".join(keep)


def diff_preview(a: str, b: str, limit: int = 20) -> list[str]:
    d = difflib.unified_diff(a.splitlines(), b.splitlines(),
                             lineterm="", n=1)
    return [ln for ln in list(d)[:limit]]


# --------------------------------------------------------------------------- state

SESSION_DIR: Path | None = None
PUBLIC_LOG: Path | None = None
TRUTH_LOG: Path | None = None

_active_fault: dict | None = None     # set while a fault is live
_active_baseline: dict | None = None
_conn = None
_dry_run = False
_restore_failed = False
_restore_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def public(msg: str) -> None:
    """Timeline only. Never contains fault identity."""
    line = f"{now()}  {msg}"
    print(line, flush=True)
    if PUBLIC_LOG:
        with PUBLIC_LOG.open("a") as fh:
            fh.write(line + "\n")


def truth(record: dict) -> None:
    """Sealed ground truth. Not shown during the trial."""
    record["ts"] = now()
    if TRUTH_LOG:
        with TRUTH_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------- device

def connect():
    load_dotenv()
    user = os.getenv("NETTOOLS_USERNAME") or os.getenv("LAB_USERNAME")
    pw = os.getenv("NETTOOLS_PASSWORD") or os.getenv("LAB_PASSWORD")
    if not user or not pw:
        sys.exit("ERROR: set NETTOOLS_USERNAME / NETTOOLS_PASSWORD in .env")
    conn = ConnectHandler(
        device_type="cisco_xr", host=TARGET_HOST, username=user, password=pw,
        fast_cli=False, conn_timeout=30, banner_timeout=30,
    )
    # Belt and braces: suppress the banner at source, and normalise anyway.
    for cmd in ("terminal length 0", "terminal exec prompt no-timestamp"):
        try:
            conn.send_command(cmd, read_timeout=20, expect_string=r"#")
        except Exception:
            pass
    return conn


def snapshot(conn) -> dict:
    """Capture the config sections. This is the restore oracle."""
    out = {}
    for cmd in SNAPSHOT_SECTIONS:
        try:
            out[cmd] = normalise(conn.send_command(cmd, read_timeout=60))
        except (ReadTimeout, NetmikoTimeoutException) as exc:
            out[cmd] = f"<<READ FAILED: {exc.__class__.__name__}>>"
    return out


def push(conn, lines: list[str], label: str) -> str:
    """Push config and commit. NEVER raises. The caller verifies by reading the device.

    Returns a status string for the log only. A status is not evidence — see the
    restore() docstring. Different netmiko versions accept different keyword
    arguments here, so each call is attempted defensively.
    """
    if _dry_run:
        public(f"[dry-run] would push {label} ({len(lines)} lines)")
        return "dry-run"

    status = []
    try:
        conn.send_config_set(lines, exit_config_mode=False)
        status.append("sent")
    except Exception as exc:
        status.append(f"send:{exc.__class__.__name__}")

    try:
        conn.commit()
        status.append("committed")
    except TypeError:
        try:
            conn.commit()
            status.append("committed")
        except Exception as exc:
            status.append(f"commit:{exc.__class__.__name__}")
    except Exception as exc:
        status.append(f"commit:{exc.__class__.__name__}")

    try:
        conn.exit_config_mode()
        status.append("exited")
    except Exception as exc:
        status.append(f"exit:{exc.__class__.__name__}")

    result = "+".join(status)
    if "committed" not in status:
        public(f"NOTE: {label} reported '{result}' — verification decides, not this")
    return result


def diff_keys(a: dict, b: dict) -> list[str]:
    return [k for k in a if a.get(k) != b.get(k)]


# --------------------------------------------------------------------------- restore

def restore(reason: str) -> bool:
    """Idempotent, verified, retried. Safe to call at any time."""
    global _active_fault, _active_baseline
    with _restore_lock:
        if _active_fault is None:
            return True
        fault, baseline = _active_fault, _active_baseline
        public(f"RESTORE starting ({reason})")
        ok = False
        for attempt in (1, 2, 3):
            status = "not-attempted"
            try:
                conn = _conn if _conn and _conn.is_alive() else connect()
                status = push(conn, fault["revert"], "revert")
            except Exception as exc:
                status = f"push-raised:{exc.__class__.__name__}"
                public(f"RESTORE attempt {attempt}: push raised {exc.__class__.__name__}")

            if _dry_run:
                public("RESTORE verified (dry-run: nothing was written)")
                ok = True
                break

            # The device is the oracle, never the push status. A push that reported
            # failure may have succeeded; a push that raised may still have applied.
            # This block runs unconditionally for exactly that reason.
            try:
                conn = _conn if _conn and _conn.is_alive() else connect()
                time.sleep(3)
                after = snapshot(conn)
                changed = diff_keys(baseline, after)
            except Exception as exc:
                public(f"RESTORE attempt {attempt}: could not READ device "
                       f"({exc.__class__.__name__}) — cannot verify")
                truth({"event": "restore_unverifiable", "fault": fault["id"],
                       "attempt": attempt, "push_status": status,
                       "read_error": exc.__class__.__name__})
                time.sleep(5)
                continue

            if not changed:
                public(f"RESTORE verified on attempt {attempt} — config identical "
                       f"(push reported '{status}')")
                truth({"event": "restore_verified", "fault": fault["id"],
                       "attempt": attempt, "reason": reason, "push_status": status})
                ok = True
                break

            public(f"RESTORE attempt {attempt}: {len(changed)} section(s) still differ")
            truth({"event": "restore_incomplete", "fault": fault["id"],
                   "attempt": attempt, "sections": changed, "push_status": status,
                   "diff": {k: diff_preview(baseline[k], after[k]) for k in changed}})
            for k in changed:
                for ln in diff_preview(baseline[k], after[k], limit=8):
                    public(f"    {k}: {ln}")
            time.sleep(5)

        if not ok:
            globals()["_restore_failed"] = True
            msg = (
                "\n" + "!" * 72 +
                f"\n!!  RESTORE FAILED on {TARGET_DEVICE} after 3 attempts."
                f"\n!!  The fabric may still be faulted. Restore by hand NOW:\n!!"
            )
            for line in fault["revert"]:
                msg += f"\n!!      {line}"
            msg += "\n!!      commit\n" + "!" * 72
            print(msg, file=sys.stderr, flush=True)
            public("RESTORE FAILED — manual intervention required")
            truth({"event": "restore_failed", "fault": fault["id"],
                   "manual_commands": fault["revert"]})
        _active_fault = None
        _active_baseline = None
        return ok


def _emergency(signum=None, frame=None):
    public(f"interrupt received (signal {signum}) — restoring before exit")
    restore(f"signal-{signum}")
    sys.exit(130)


# --------------------------------------------------------------------------- rounds

def hold_and_wait(max_hold_min: int) -> None:
    """Hold the fault while the investigation runs. Watchdog auto-reverts."""
    done = threading.Event()

    def watchdog():
        if not done.wait(max_hold_min * 60):
            public(f"WATCHDOG: {max_hold_min} min elapsed with no confirmation")
            restore("watchdog-timeout")
            os._exit(2)

    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    print()
    print("  " + "=" * 66)
    print(f"  FAULT IS LIVE.  Subject to investigate:  {SUBJECT}")
    print(f"  Auto-revert in {max_hold_min} minutes if you do not confirm.")
    print("  " + "=" * 66)
    try:
        input("\n  Press Enter when the investigation has finished... ")
    finally:
        done.set()


def run_round(conn, n: int, choice: int, max_hold_min: int) -> None:
    global _active_fault, _active_baseline
    fault = FAULTS[choice]

    public(f"--- round {n} begin ---")
    baseline = snapshot(conn)
    public(f"round {n}: baseline captured ({len(baseline)} sections)")
    truth({"event": "round_begin", "round": n, "fault_number": choice,
           "fault_id": fault["id"], "fault_name": fault["name"],
           "apply": fault["apply"]})

    _active_baseline = baseline
    _active_fault = fault

    t0 = time.time()
    status = push(conn, fault["apply"], "apply")
    if _dry_run:
        public(f"round {n}: apply skipped (dry-run) — verification not applicable")
        changed = ["<dry-run>"]
    else:
        time.sleep(3)
        after = snapshot(conn)
        changed = diff_keys(baseline, after)
        truth({"event": "apply_diff", "round": n,
               "diff": {k: diff_preview(baseline[k], after[k]) for k in changed}})

    if not changed and not _dry_run:
        public(f"round {n}: ABORT — config unchanged after apply (push: {status}), "
               f"nothing to test")
        truth({"event": "apply_ineffective", "round": n, "push_status": status})
        _active_fault = None
        return

    public(f"round {n}: change applied and confirmed by read ({len(changed)} section(s))")
    truth({"event": "apply_verified", "round": n, "push_status": status,
           "sections_changed": changed, "apply_seconds": round(time.time() - t0, 1)})

    hold_and_wait(max_hold_min)

    t1 = time.time()
    restore(f"round-{n}-complete")
    public(f"round {n}: restored in {round(time.time() - t1, 1)}s")
    public(f"--- round {n} end ---")
    truth({"event": "round_end", "round": n,
           "hold_seconds": round(t1 - t0, 1)})


def menu() -> int:
    print("\n  Available changes:\n")
    for k, f in FAULTS.items():
        note = f"  ({f['note']})" if f["note"] else ""
        print(f"    {k}.  {f['name']}{note}")
    print()
    while True:
        raw = input("  Choose 1-6 (or q to finish): ").strip().lower()
        if raw == "q":
            return 0
        if raw.isdigit() and int(raw) in FAULTS:
            return int(raw)
        print("  Not a valid choice.")


# --------------------------------------------------------------------------- main

def main() -> int:
    global SESSION_DIR, PUBLIC_LOG, TRUTH_LOG, _conn, _dry_run

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preflight", action="store_true",
                    help="dump the config sections and exit, changing nothing")
    ap.add_argument("--dry-run", action="store_true", help="no device writes")
    ap.add_argument("--random", action="store_true",
                    help="the script chooses — nobody knows until the seal is opened")
    ap.add_argument("--rounds", type=int, default=0,
                    help="number of rounds (with --random); 0 = ask each time")
    ap.add_argument("--max-hold", type=int, default=20,
                    help="minutes before auto-revert (default 20)")
    ap.add_argument("--seed", type=int, help="random seed, recorded in the sealed log")
    args = ap.parse_args()

    _dry_run = args.dry_run

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    SESSION_DIR = Path("runs") / stamp
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_LOG = SESSION_DIR / "public.log"
    TRUTH_LOG = SESSION_DIR / "truth.jsonl"

    public(f"session {stamp} start  device={TARGET_DEVICE} host={TARGET_HOST} "
           f"subject='{SUBJECT}' dry_run={_dry_run}")

    print(f"\n  Session : {SESSION_DIR}")
    print(f"  Public  : {PUBLIC_LOG}   (safe to share during the trial)")
    print(f"  SEALED  : {TRUTH_LOG}    (do NOT open until comparison)\n")

    signal.signal(signal.SIGINT, _emergency)
    signal.signal(signal.SIGTERM, _emergency)
    atexit.register(lambda: restore("atexit"))

    try:
        _conn = connect()
    except Exception as exc:
        public(f"connect failed: {exc}")
        return 2
    public(f"connected to {TARGET_DEVICE}")

    if args.preflight:
        snap = snapshot(_conn)
        print("\n  Verify these names match the constants at the top of this script:\n")
        for cmd, body in snap.items():
            print(f"  ===== {cmd} =====")
            print("  " + "\n  ".join(body.splitlines()[:40]) or "  <empty>")
            print()
        public("preflight complete, nothing changed")
        _conn.disconnect()
        return 0

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    truth({"event": "session_start", "device": TARGET_DEVICE, "subject": SUBJECT,
           "mode": "random" if args.random else "manual", "seed": seed,
           "dry_run": _dry_run})

    n = 0
    try:
        while True:
            n += 1
            if args.random:
                if args.rounds and n > args.rounds:
                    break
                choice = rng.choice(list(FAULTS))
                public(f"round {n}: change selected by script (identity sealed)")
            else:
                choice = menu()
                if choice == 0:
                    break
                public(f"round {n}: change selected by operator (identity sealed)")

            run_round(_conn, n, choice, args.max_hold)

            if _restore_failed:
                public("session ABORTED — a restore failed, not starting another round")
                break

            if not args.random or not args.rounds:
                if input("\n  Another round? [y/N] ").strip().lower() != "y":
                    break
    finally:
        restore("session-end")
        try:
            if _conn:
                _conn.disconnect()
        except Exception:
            pass
        public("session end")
        truth({"event": "session_end", "rounds": n})

    print(f"\n  Done. Sealed log: {TRUTH_LOG}")
    print("  Open it only after every diagnosis is written down.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())