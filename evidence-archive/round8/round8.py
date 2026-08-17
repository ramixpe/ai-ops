#!/usr/bin/env python3
"""
round8.py — can rungs 1 and 2 separate?

B-463. The bgp_session|transport boundary is separated only by a composed vector.
Round 3 tried and produced BBHHH because an admin shutdown broke the socket as well as
the FSM. This applies a wrong remote-AS with ebgp-multihop, so TCP establishes and the
OPEN is rejected on the AS check — the one fault that should leave the socket armed
while the session is not Established.

WHY THE RUNG VECTOR IS NOT THE RESULT (seal §2a.7)

Three independent failures all produce B B H H H:
    the intended AS rejection          — the mechanism under test
    TTL / multihop breaking TCP        — the round's own setup failing
    BFD holding the session down       — 100ms x 3, no bfd multihop configured

A round scored on the vector alone confirms its prediction in all three, including the
two where the mechanism never happened. So this harness samples the DISCRIMINATORS:

    socket armed          armed => TCP established => rules out TTL/multihop
    last reset reason     AS notification vs BFD
    bfd session state     rules out BFD holding it down
    session FSM state     OpenSent/OpenConfirm is the separation window

Dense sampling from the commit is the measurement, not an optimisation. OpenSent recurs
on every connect retry, so the window comes round repeatedly rather than once.

Safety: same discipline as fault_lab.py. Two sessions opened before the fault. Restore
verified by config comparison, never by the write's report. The revert must also leave
NO ebgp-multihop line — inert on a restored iBGP peer, and therefore undetectable.

Usage
    python round8.py --dry-run
    python round8.py
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import fault_lab
from fault_lab import connect, snapshot, push, diff_keys

# --------------------------------------------------------------------------- config

DEVICE = "PE2"
PEER = "10.255.0.31"                 # RR1
BGP_AS = "65000"
WRONG_AS = "65001"

SESSION_CMD = f"show bgp summary"
NEIGHBOR_CMD = f"show bgp neighbor {PEER}"
BFD_CMD = "show bfd session"

APPLY = [f"router bgp {BGP_AS}", f" neighbor {PEER}",
         f"  remote-as {WRONG_AS}", "  ebgp-multihop 5"]
REVERT = [f"router bgp {BGP_AS}", f" neighbor {PEER}",
          f"  remote-as {BGP_AS}", "  no ebgp-multihop"]

PRE_SAMPLES = 4
DENSE_SECONDS = 240        # cover several connect-retry cycles
SETTLE_SECONDS = 60
SPARSE_INTERVAL = 5.0
WATCHDOG_MINUTES = 20

# --------------------------------------------------------------------------- state

RUN: Path | None = None
LOG: Path | None = None
_baseline: dict | None = None
_armed = False
_dry = False
_sampler = None
_injector = None
_lock = threading.Lock()
_stop = threading.Event()
_n = 0


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def say(msg: str) -> None:
    line = f"{now()}  {msg}"
    print(line, flush=True)
    if LOG:
        with LOG.open("a") as fh:
            fh.write(line + "\n")


def rec(o: dict) -> None:
    o.setdefault("ts", now())
    if RUN:
        with (RUN / "samples.jsonl").open("a") as fh:
            fh.write(json.dumps(o) + "\n")


# --------------------------------------------------------------------------- parsing

# show bgp summary row: the last column is a state string OR a prefix count
_SUMMARY_ROW = re.compile(
    rf"^{re.escape(PEER)}\s+\S+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\S+)",
    re.M,
)
_CONN_STATE = re.compile(r"BGP state\s*=\s*([A-Za-z]+)", re.I)
_SOCKET = re.compile(r"socket.*?(armed|not armed)", re.I)
_LAST_RESET = re.compile(r"[Ll]ast reset\s+(.*)", re.M)
_NOTIF = re.compile(r"(notification|bad peer as|open message error|hold time expired|bfd)",
                    re.I)
_BFD_ROW = re.compile(rf"{re.escape(PEER)}\s+\S+\s+(\S+)", re.M)


def parse_session(text: str) -> dict:
    """State from show bgp summary. The last column is a state OR a count."""
    m = _SUMMARY_ROW.search(text)
    if not m:
        return {"present": False, "state": None, "established": None}
    tail = m.group(2)
    numeric = tail.isdigit()
    return {
        "present": True,
        "remote_as": m.group(1),
        "state": "Established" if numeric else tail,
        "established": numeric,
    }


def parse_neighbor(text: str) -> dict:
    fsm = _CONN_STATE.search(text)
    sock = _SOCKET.search(text)
    reset = _LAST_RESET.search(text)
    reset_txt = reset.group(1).strip() if reset else None
    return {
        "fsm": fsm.group(1) if fsm else None,
        "socket_armed": (sock.group(1).lower() == "armed") if sock else None,
        "socket_reported": bool(sock),
        "last_reset": reset_txt,
        "reset_names": _NOTIF.search(reset_txt).group(1).lower()
        if reset_txt and _NOTIF.search(reset_txt) else None,
    }


def parse_bfd(text: str) -> dict:
    m = _BFD_ROW.search(text)
    return {"present": bool(m), "state": m.group(1) if m else None}


# --------------------------------------------------------------------------- sampling

def take(phase: str) -> dict:
    global _n
    _n += 1
    t0 = time.monotonic()
    try:
        s_raw = _sampler.send_command(SESSION_CMD, read_timeout=30)
        n_raw = _sampler.send_command(NEIGHBOR_CMD, read_timeout=30)
        b_raw = _sampler.send_command(BFD_CMD, read_timeout=30)
        err = None
    except Exception as exc:
        s_raw = n_raw = b_raw = ""
        err = exc.__class__.__name__
    dur = round(time.monotonic() - t0, 3)

    sess = parse_session(s_raw) if s_raw else {}
    nbr = parse_neighbor(n_raw) if n_raw else {}
    bfd = parse_bfd(b_raw) if b_raw else {}

    # THE SEPARATION: socket armed while the session is not Established.
    separated = bool(nbr.get("socket_armed") is True and sess.get("established") is False)

    s = {
        "n": _n, "phase": phase, "duration_s": dur, "error": err,
        "session": sess, "neighbor": nbr, "bfd": bfd,
        "SEPARATED": separated,
    }
    rec(s)
    if separated:
        say(f"  ** sample {_n:03d} [{phase}] SEPARATION — socket armed, session "
            f"{sess.get('state')} (fsm {nbr.get('fsm')})")
    return s


def dense(phase: str, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop.is_set():
        take(phase)


def sparse(phase: str, seconds: float, interval: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop.is_set():
        t = time.monotonic()
        take(phase)
        rest = interval - (time.monotonic() - t)
        if rest > 0:
            _stop.wait(rest)


# --------------------------------------------------------------------------- restore

def restore(reason: str) -> bool:
    global _armed
    with _lock:
        if not _armed:
            return True
        say(f"RESTORE starting ({reason})")
        ok = False
        for attempt in (1, 2, 3):
            status = "not-attempted"
            try:
                c = _injector if _injector and _injector.is_alive() else connect()
                status = push(c, REVERT, "revert")
            except Exception as exc:
                status = f"push-raised:{exc.__class__.__name__}"
            if _dry:
                say("RESTORE verified (dry-run: nothing written)")
                ok = True
                break
            try:
                c = _injector if _injector and _injector.is_alive() else connect()
                time.sleep(3)
                after = snapshot(c)
                changed = diff_keys(_baseline, after)
                # the procedure face: name the end state, do not assume it
                lingering = any("ebgp-multihop" in v for v in after.values())
            except Exception as exc:
                say(f"RESTORE attempt {attempt}: cannot READ device "
                    f"({exc.__class__.__name__})")
                time.sleep(5)
                continue
            if not changed and not lingering:
                say(f"RESTORE verified on attempt {attempt} — config identical, "
                    f"no ebgp-multihop remaining (push said '{status}')")
                rec({"event": "restore_verified", "attempt": attempt})
                ok = True
                break
            if lingering:
                say(f"RESTORE attempt {attempt}: ebgp-multihop STILL PRESENT")
            say(f"RESTORE attempt {attempt}: {len(changed)} section(s) differ")
            time.sleep(5)
        if not ok:
            msg = "\n" + "!" * 72 + f"\n!!  RESTORE FAILED on {DEVICE}. Run by hand NOW:\n!!"
            for line in REVERT:
                msg += f"\n!!      {line}"
            msg += "\n!!      commit\n" + "!" * 72
            print(msg, file=sys.stderr, flush=True)
            rec({"event": "restore_failed", "manual": REVERT})
        _armed = False
        return ok


def _sig(signum=None, frame=None):
    say(f"signal {signum} — restoring")
    _stop.set()
    restore(f"signal-{signum}")
    sys.exit(130)


# --------------------------------------------------------------------------- verdict

def verdict() -> dict:
    rows = []
    with (RUN / "samples.jsonl").open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "SEPARATED" in d:
                rows.append(d)

    post = [r for r in rows if r["phase"] != "pre"]
    sep = [r for r in post if r["SEPARATED"]]
    armed = [r for r in post if r["neighbor"].get("socket_armed") is True]
    unreported = [r for r in post if r["neighbor"].get("socket_reported") is False]
    resets = sorted({r["neighbor"].get("reset_names") for r in post
                     if r["neighbor"].get("reset_names")})
    fsms = sorted({r["neighbor"].get("fsm") for r in post if r["neighbor"].get("fsm")})
    bfd_states = sorted({r["bfd"].get("state") for r in post if r["bfd"].get("state")})
    durs = [r["duration_s"] for r in post if r.get("duration_s")]
    res = round(sum(durs) / len(durs), 3) if durs else None

    # discriminators
    tcp_ruled_out = bool(armed)          # socket armed at any point => TCP established
    bfd_named = "bfd" in (resets or [])
    as_named = any(x in (resets or []) for x in
                   ("bad peer as", "notification", "open message error"))

    v = {
        "samples_post_fault": len(post),
        "mean_sample_seconds": res,
        "SEPARATION_OBSERVED": bool(sep),
        "separation_sample_count": len(sep),
        "samples_socket_armed": len(armed),
        "samples_socket_not_reported": len(unreported),
        "fsm_states_seen": fsms,
        "last_reset_reasons_seen": resets,
        "bfd_states_seen": bfd_states,
        "discriminator_tcp_established": tcp_ruled_out,
        "discriminator_reset_names_as": as_named,
        "discriminator_reset_names_bfd": bfd_named,
        "note": (
            "Socket never armed after the fault. The vector alone cannot distinguish the "
            "AS rejection from TTL/multihop or BFD — read last_reset_reasons_seen and "
            "bfd_states_seen before scoring. A separation shorter than the sampling "
            f"resolution (~{res}s) is bounded above, not measured as absent."
            if not sep else
            "Socket armed while the session was not Established. Rungs 1 and 2 separate, "
            "captured rather than composed."
        ),
    }
    (RUN / "verdict.json").write_text(json.dumps(v, indent=2))
    return v


# --------------------------------------------------------------------------- main

def main() -> int:
    global RUN, LOG, _baseline, _armed, _dry, _sampler, _injector

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dense", type=int, default=DENSE_SECONDS)
    args = ap.parse_args()

    _dry = args.dry_run
    fault_lab._dry_run = args.dry_run          # OBS-130: the flag the guard reads

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RUN = Path("round8") / stamp
    RUN.mkdir(parents=True, exist_ok=True)
    LOG = RUN / "round8.log"

    say(f"round 8 start  device={DEVICE}  peer={PEER}  {BGP_AS}->{WRONG_AS} "
        f"+ebgp-multihop  dry_run={_dry}")
    print(f"\n  Run dir: {RUN}\n")

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    atexit.register(lambda: restore("atexit"))

    try:
        say("opening sampler session")
        _sampler = connect()
        say("opening injector session")
        _injector = connect()
    except Exception as exc:
        say(f"connect failed: {exc}")
        return 2

    _baseline = snapshot(_injector)
    say(f"baseline captured ({len(_baseline)} sections)")

    say(f"--- baseline: {PRE_SAMPLES} samples ---")
    for _ in range(PRE_SAMPLES):
        s = take("pre")
    say(f"  session: {s['session']}")
    say(f"  neighbor: fsm={s['neighbor'].get('fsm')} "
        f"socket_armed={s['neighbor'].get('socket_armed')} "
        f"socket_reported={s['neighbor'].get('socket_reported')}")
    say(f"  bfd: {s['bfd']}")

    if s["neighbor"].get("socket_reported") is False:
        say("NOTE — no socket state in neighbor output. Seal claim 2 is already "
            "refuted: bgp_transport takes its fallback on this platform.")
    if not s["session"].get("established"):
        say("ABORT — the session is not Established at baseline. Nothing to break.")
        return 2

    def watchdog():
        if not _stop.wait(WATCHDOG_MINUTES * 60):
            say(f"WATCHDOG: {WATCHDOG_MINUTES} min")
            restore("watchdog")
            import os
            os._exit(2)
    threading.Thread(target=watchdog, daemon=True).start()

    say(f"--- applying: remote-as {WRONG_AS} + ebgp-multihop 5 ---")
    _armed = True
    status = push(_injector, APPLY, "apply")
    rec({"event": "fault_applied", "push_status": status})
    say(f"applied (push said '{status}') — dense sampling for {args.dense}s")

    dense("post_fault_dense", args.dense)
    say(f"--- dense done, sparse for {SETTLE_SECONDS}s ---")
    sparse("post_fault_settle", SETTLE_SECONDS, SPARSE_INTERVAL)

    restore("round-complete")
    _stop.set()

    say("--- 60s of post-restore sampling ---")
    _stop.clear()
    sparse("post_restore", 60, SPARSE_INTERVAL)
    _stop.set()

    v = verdict()
    say("--- verdict ---")
    for k, val in v.items():
        say(f"  {k}: {val}")

    for c in (_sampler, _injector):
        try:
            c.disconnect()
        except Exception:
            pass

    say("round 8 complete")
    print(f"\n  Samples : {RUN / 'samples.jsonl'}")
    print(f"  Verdict : {RUN / 'verdict.json'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
