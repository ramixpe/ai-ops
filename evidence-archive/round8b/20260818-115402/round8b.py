#!/usr/bin/env python3
"""Round 8b — the AS-mismatch round, re-run with a working instrument.

Round 8's fault landed correctly and four of its claims were confirmed. Its
primary claim, §2a.2 (the transient separation), came back **void rather than
refuted**: the sampler's socket regex read `False` in all 195 samples where the
session was Established, including 181 in a dry run where the fault never
applied. A falsifier that fires on an instrument which cannot produce the value
that would fail it has measured nothing (OBS-133).

Same fault, unchanged. Four changes to the instrument, each closing one way the
last run became unreadable.

1.  THE SOCKET REGEX IS ANCHORED AND POSITIONAL.
    The real line is
        Socket not armed for io, armed for read, armed for write
    and round 8 used `socket.*?(armed|not armed)`, a minimal-width search which
    lands on the **io** field -- `not armed` on a perfectly healthy session.
    This uses the shipped `template_parsers._BGP_SOCKET`, which is anchored with
    three named positional groups and cannot pick the wrong field.

2.  RAW LINES ARE STORED BESIDE THE FIELDS DERIVED FROM THEM.
    chaos-harness §6.1d, as amended after round 8: *a parsed field is a
    conclusion; the input is the text it was parsed from*. Round 8's recovery of
    §2a.5, §2a.6 and most of §2a.7 rested entirely on `last_reset` having been
    stored raw. Its loss of §2a.2 rested entirely on `socket_armed` not having
    been. Both raw lines are stored now.

3.  THE BASELINE IS COUNTED SEPARATELY, AND IT ABORTS THE ROUND.
    Round 8 printed `samples_socket_armed: 0` over the post-fault window only.
    Counted over the baseline it would have read `0 of 4 armed on an Established
    session` and stopped the round at its first verdict line. That check was
    available and unread. Here it is a precondition: **if the baseline does not
    show the socket armed, the instrument is broken and the round does not
    run.** A control you do not read is not a control.

4.  MORE RETRY CYCLES, NOT FASTER SAMPLES -- and see the note below, because
    this corrects the re-seal's own §6.3.

THE ARITHMETIC THAT CHANGED §6.3
    ROUND-8.md §6.3 asked for "sub-200 ms sampling of the socket field". That is
    not reachable over SSH and saying so before the window is spent is the whole
    point of §6.1b. Round 8 measured 1.563 s for three `show` commands, so one
    command costs about **520 ms** of round trip, and no amount of loop tuning
    beats the wire.

    But sub-200 ms was never the requirement -- it was a proxy for one. What
    §2a.2 needs is a good chance of landing inside the OpenSent window at least
    once, and that is a function of *cycles*, not of resolution:

        expected catches  ~  N_cycles * min(1, W_window / T_sample)

    Round 8: 10 cycles, W ~ 150 ms, T = 1.563 s  ->  expected 0.96.
    It observed exactly 1. The model is calibrated on the only data there is.

    This run: one command instead of three (T ~ 520 ms) and a 900 s dense window
    instead of 240 s (N ~ 39 cycles at the observed ~23 s retry interval)

        expected  ~  39 * (0.15 / 0.52)  ~  11

    **So a zero here means something.** Under the same model, P(zero) is
    e^-11 ~ 2e-5. That is what converts §2a.2 from void into a result in either
    direction, which is the entire purpose of 8b.

    Recorded rather than quietly implemented: the sealed *prediction* (§2a.2) is
    untouched. Only the *method* changed, and it changed because arithmetic said
    the sealed method could not deliver it.

READ-ONLY EXCEPT THE SEALED FAULT
    §0.11 is absolute. This script pushes exactly the two lines in APPLY and
    reverts them, and the revert is verified by reading the device back -- never
    by trusting the write's report (B-412). The revert must also leave NO
    ebgp-multihop line: inert on a restored iBGP peer, and therefore
    undetectable if it lingers.

THE OUTPUT LANDS IN A GIT REPOSITORY, BY DEFAULT
    `~/ai-agent-ops/faultlab/` is not a git repository, so rounds 7 and 8 wrote
    their payloads somewhere §6.1d does not consider archived at all (OBS-135).
    Round 7 lost 158 samples to that. This writes into the tracked repo and
    copies itself in beside the samples, so the instrument is archived with its
    own output without anyone remembering to do it.

Usage
    python round8b.py --dry-run
    python round8b.py
"""

from __future__ import annotations

import argparse
import pathlib
import atexit
import json
import re
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import fault_lab
from fault_lab import connect, diff_keys, push, snapshot

# --------------------------------------------------------------------------- config

DEVICE = "PE2"
PEER = "10.255.0.31"                 # RR1
BGP_AS = "65000"
WRONG_AS = "65001"

SESSION_CMD = "show bgp summary"
NEIGHBOR_CMD = f"show bgp neighbor {PEER}"
BFD_CMD = "show bfd session"

APPLY = [f"router bgp {BGP_AS}", f" neighbor {PEER}",
         f"  remote-as {WRONG_AS}", "  ebgp-multihop 5"]
REVERT = [f"router bgp {BGP_AS}", f" neighbor {PEER}",
          f"  remote-as {BGP_AS}", "  no ebgp-multihop"]

PRE_SAMPLES = 4
DENSE_SECONDS = 900        # ~39 retry cycles at the observed ~23 s interval
SETTLE_SECONDS = 60
SPARSE_INTERVAL = 5.0
WATCHDOG_MINUTES = 30      # raised with the dense window; the round is longer now

#: Where the payload goes. A git repository, deliberately -- see the module
#: docstring. Override only if you have somewhere else that is tracked.
ARCHIVE_ROOT = Path.home() / "ai-agent-ops" / "ios-xr-nettools" / "evidence-archive"

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

_SUMMARY_ROW = re.compile(
    rf"^{re.escape(PEER)}\s+\S+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\S+)",
    re.M,
)
_CONN_STATE = re.compile(r"BGP state\s*=\s*([A-Za-z]+)", re.I)

#: Verbatim from `agent_nettools.template_parsers._BGP_SOCKET`.
#:
#: **The pattern was copied faithfully and the context that makes it work was
#: not, and the round aborted on it (2026-08-18).** The device emits the line
#: INDENTED by two spaces. The shipped parser iterates lines and strips each
#: one before `.match()`; this copy applied the same anchored pattern with
#: `re.M` to the raw buffer, where `^` lands on a space and never matches. Four
#: baseline samples read `socket_reported: False` on an Established session and
#: precondition 3 stopped the round -- correctly, and for ~40 s instead of a
#: whole window.
#:
#: A regex's behaviour is the pattern PLUS the string it is applied to.
#: "Copy it verbatim" secured half of that and read as if it secured both.
#: `_socket_fields` below now strips per line, exactly as the shipped parser
#: does, and `--self-test` proves it against a committed fixture with no lab.
_BGP_SOCKET = re.compile(
    r"^Socket (?P<io>not armed|armed) for io, "
    r"(?P<read>not armed|armed) for read, "
    r"(?P<write>not armed|armed) for write$"
)


def _socket_fields(text: str):
    """The socket match, found the way the shipped parser finds it: per
    stripped line. Returns the match or None."""

    for line in text.splitlines():
        match = _BGP_SOCKET.match(line.strip())
        if match:
            return match
    return None

_LAST_RESET = re.compile(r"[Ll]ast reset\s+(.*)", re.M)

#: Ordered longest-first and matched against the *whole* reason. Round 8's
#: version matched `notification` before `hold time expired` inside
#: "due to BGP Notification sent: hold time expired", so an ordinary hold-timer
#: reset was classified as naming the AS -- and `discriminator_reset_names_as`
#: came back true on a dry run containing no AS string at all (OBS-134).
_RESET_KINDS = (
    ("peer in wrong as", "wrong_as"),
    ("bad peer as", "wrong_as"),
    ("open message error", "open_error"),
    ("hold time expired", "hold_expired"),
    ("bfd", "bfd"),
    ("remote as configuration changed", "config_changed"),
    ("notification", "notification_unspecified"),
)

_BFD_ROW = re.compile(rf"{re.escape(PEER)}\s+\S+\s+(\S+)", re.M)


def classify_reset(text: str | None) -> str | None:
    """The specific reason if the line names one, else a generic marker.

    `notification_unspecified` is deliberately not `wrong_as`. Every BGP reset
    carried by a notification says "Notification"; only some of them say why.
    Collapsing the two is what made round 8's AS discriminator fire on a run
    where no AS was ever mentioned.
    """

    if not text:
        return None
    low = text.lower()
    for needle, kind in _RESET_KINDS:
        if needle in low:
            return kind
    return None


def parse_session(text: str) -> dict:
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
    """Derived fields **and the raw lines they came from** (§6.1d)."""

    fsm = _CONN_STATE.search(text)
    sock = _socket_fields(text)
    reset = _LAST_RESET.search(text)
    reset_txt = reset.group(1).strip() if reset else None
    return {
        "fsm": fsm.group(1) if fsm else None,
        "socket_armed_read": (sock.group("read") == "armed") if sock else None,
        "socket_armed_write": (sock.group("write") == "armed") if sock else None,
        "socket_armed_io": (sock.group("io") == "armed") if sock else None,
        "socket_reported": bool(sock),
        # THE RAW LINES. If a field above is ever disputed, its source is here.
        "socket_line_raw": sock.group(0) if sock else None,
        "last_reset": reset_txt,
        "reset_kind": classify_reset(reset_txt),
    }


def parse_bfd(text: str) -> dict:
    m = _BFD_ROW.search(text)
    return {"present": bool(m), "state": m.group(1) if m else None}


# --------------------------------------------------------------------------- sampling

def take(phase: str, *, full: bool = True) -> dict:
    """One observation.

    ``full=False`` reads only ``show bgp neighbor`` -- one command instead of
    three, which is a ~3x resolution gain during the dense window. Session state
    is then derived from the neighbour's own FSM rather than from `show bgp
    summary`, and the two agree: `Established` in the FSM is the same fact the
    summary's numeric last column reports.
    """

    global _n
    _n += 1
    t0 = time.monotonic()
    s_raw = b_raw = ""
    try:
        n_raw = _sampler.send_command(NEIGHBOR_CMD, read_timeout=30)
        if full:
            s_raw = _sampler.send_command(SESSION_CMD, read_timeout=30)
            b_raw = _sampler.send_command(BFD_CMD, read_timeout=30)
        err = None
    except Exception as exc:
        n_raw = ""
        err = exc.__class__.__name__
    dur = round(time.monotonic() - t0, 3)

    nbr = parse_neighbor(n_raw) if n_raw else {}
    sess = parse_session(s_raw) if s_raw else {}
    bfd = parse_bfd(b_raw) if b_raw else {}

    # With one command there is no summary row, so establishment comes from the
    # FSM. Recorded under its own key so a reader can see which source answered.
    established = sess.get("established")
    if established is None and nbr.get("fsm"):
        established = nbr["fsm"] == "Established"
    established_from = "summary" if sess.get("established") is not None else "fsm"

    # THE SEPARATION: the socket armed for read while the session is not
    # Established. Note `is True` and `is False` -- an unread field is neither,
    # and must never satisfy this.
    separated = bool(nbr.get("socket_armed_read") is True and established is False)

    s = {
        "n": _n, "phase": phase, "duration_s": dur, "error": err, "full": full,
        "session": sess, "neighbor": nbr, "bfd": bfd,
        "established": established, "established_from": established_from,
        "SEPARATED": separated,
    }
    rec(s)
    if separated:
        say(f"  ** sample {_n:03d} [{phase}] SEPARATION — socket armed for read, "
            f"session not Established (fsm {nbr.get('fsm')})")
        say(f"     raw: {nbr.get('socket_line_raw')}")
    return s


def dense(phase: str, seconds: float) -> None:
    """Free-running, one command per sample. No sleep: the wire is the limit."""

    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop.is_set():
        take(phase, full=False)


def sparse(phase: str, seconds: float, interval: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop.is_set():
        t = time.monotonic()
        take(phase, full=True)
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

    pre = [r for r in rows if r["phase"] == "pre"]
    post = [r for r in rows if r["phase"] != "pre"]

    def armed_read(rs):
        return [r for r in rs if r["neighbor"].get("socket_armed_read") is True]

    sep = [r for r in post if r["SEPARATED"]]
    unreported = [r for r in post if r["neighbor"].get("socket_reported") is False]
    kinds = sorted({r["neighbor"].get("reset_kind") for r in post
                    if r["neighbor"].get("reset_kind")})
    fsms = sorted({r["neighbor"].get("fsm") for r in post if r["neighbor"].get("fsm")})
    bfd_states = sorted({r["bfd"].get("state") for r in post if r["bfd"].get("state")})
    durs = [r["duration_s"] for r in post if r.get("duration_s")]
    res = round(sum(durs) / len(durs), 3) if durs else None

    established_post = [r for r in post if r.get("established") is True]

    v = {
        # THE INSTRUMENT CHECK, FIRST AND UNMISSABLE. Round 8's fatal number was
        # reported without a denominator that could reveal it.
        "instrument": {
            "baseline_samples": len(pre),
            "baseline_socket_armed_read": len(armed_read(pre)),
            "baseline_trustworthy": len(pre) > 0 and len(armed_read(pre)) == len(pre),
            "post_established_samples": len(established_post),
            "post_established_socket_armed_read": len(armed_read(established_post)),
            # DID THE FAULT ACTUALLY LAND? Without this the verdict below
            # concludes "refuted" from a run in which nothing was ever broken.
            # The dry run proved it: fault never applied, session never left
            # Established, and the note still read "§2a.2 refuted, B-463
            # closes". A refutation drawn from a fabric that was healthy the
            # whole time is round 8's error running the other way — a zero that
            # measures the absence of an experiment, not the absence of an
            # effect (2026-08-18).
            "fault_landed": bool([f for f in fsms if f != "Established"])
                            or len(established_post) < len(post),
        },
        "samples_post_fault": len(post),
        "mean_sample_seconds": res,
        "SEPARATION_OBSERVED": bool(sep),
        "separation_sample_count": len(sep),
        "samples_socket_armed_read": len(armed_read(post)),
        "samples_socket_not_reported": len(unreported),
        "fsm_states_seen": fsms,
        "reset_kinds_seen": kinds,
        "bfd_states_seen": bfd_states,
        # Discriminators, each named for what it actually tests.
        "discriminator_tcp_established": bool(armed_read(post)) or "OpenSent" in fsms,
        "discriminator_reset_names_as": "wrong_as" in kinds,
        "discriminator_reset_names_bfd": "bfd" in kinds,
    }

    if not v["instrument"]["fault_landed"]:
        v["VOID"] = True
        v["note"] = (
            "VOID — THE FAULT NEVER LANDED. Every post-fault sample shows the "
            "session Established and no other FSM state was seen, so nothing was "
            "measured and no conclusion about §2a.2 is available in either "
            "direction. This is the expected result for --dry-run, and on a real "
            "run it means the injector did not apply: check the device before "
            "re-running. A refutation drawn from an unbroken fabric would be "
            "round 8's mistake inverted."
        )
    elif not v["instrument"]["baseline_trustworthy"]:
        v["note"] = (
            "INSTRUMENT NOT TRUSTWORTHY. The socket did not read armed on every "
            "baseline sample of an Established session, so a zero below measures "
            "the sampler and not the fabric. This is round 8's failure and the "
            "round should not be scored (OBS-133)."
        )
    elif sep:
        v["note"] = (
            "Socket armed for read while the session was not Established. Rungs 1 "
            "and 2 separate, captured rather than composed. §2a.2 holds."
        )
    else:
        v["note"] = (
            f"No separation in {len(post)} samples at ~{res}s resolution, on a "
            "baseline-verified instrument. Under the round-8 model "
            "(~150ms window, ~23s retry cycle) a zero here is ~2e-5, so this is "
            "evidence of ABSENCE rather than of not looking: §2a.2 refuted, and "
            "B-463 closes as not separable at any resolution reachable over CLI."
        )

    (RUN / "verdict.json").write_text(json.dumps(v, indent=2))
    return v


# --------------------------------------------------------------------------- self-test

#: A committed capture of a healthy Established session, indented exactly as the
#: device emits it. The instrument is checked against this BEFORE it is allowed
#: near a device.
_FIXTURE = pathlib.Path(
    "/home/rami/ai-agent-ops/ios-xr-nettools/tests/fixtures/cisco_xr/RR1/healthy"
    "/show-bgp-neighbor-10-255-0-11.txt"
)


def self_test() -> int:
    """Parse a committed fixture and assert the fields the round depends on.

    **Why this exists.** Round 8 spent an entire lab window and produced a void,
    because its socket regex was wrong. Round 8b's precondition 1 demanded a
    unit test -- and one was written, against the SHIPPED parser, mutation-
    verified, and it passed. The round still aborted, because this file carries
    its own copy of the pattern and the test never touched it. A precondition
    verified on the wrong artefact is not a precondition.

    So: the instrument tests ITSELF, against committed text, with no lab. Run it
    before every round. If it fails, nothing has been spent.
    """

    if not _FIXTURE.is_file():
        print(f"SELF-TEST CANNOT RUN — fixture missing: {_FIXTURE}")
        return 2

    raw = _FIXTURE.read_text()
    n = parse_neighbor(raw)
    checks = [
        ("socket line found at all", n["socket_reported"] is True),
        ("read is armed (the field that voided round 8)", n["socket_armed_read"] is True),
        ("write is armed", n["socket_armed_write"] is True),
        ("io is NOT armed -- the field a first-match search wrongly lands on",
         n["socket_armed_io"] is False),
        ("the raw line is stored beside the derived fields (6.1d)",
         isinstance(n["socket_line_raw"], str) and "armed for read" in n["socket_line_raw"]),
        ("the session's FSM state parses", n.get("fsm") == "Established"),
    ]
    bad = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    if bad:
        print(f"\nSELF-TEST FAILED ({len(bad)}). The instrument is broken; do not "
              f"open a lab window.")
        return 1
    print("\nSELF-TEST PASSED — the sampler reads a real capture correctly.")
    return 0


# --------------------------------------------------------------------------- main

def main() -> int:
    global RUN, LOG, _baseline, _armed, _dry, _sampler, _injector

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="parse a committed fixture and exit; touches no device")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dense", type=int, default=DENSE_SECONDS)
    ap.add_argument("--out", type=Path, default=ARCHIVE_ROOT / "round8b",
                    help="run directory root; defaults into the tracked repo")
    args = ap.parse_args()

    # Before anything else, and before any device is touched.
    if args.self_test:
        return self_test()

    _dry = args.dry_run
    fault_lab._dry_run = args.dry_run          # OBS-130: the flag the guard reads

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RUN = args.out / stamp
    RUN.mkdir(parents=True, exist_ok=True)
    LOG = RUN / "round8b.log"

    # §6.1d: the instrument is archived with its output, mechanically. Round 8's
    # numbers were wrong because of two regexes in a file that was not kept.
    try:
        shutil.copy2(__file__, RUN / "round8b.py")
    except Exception as exc:                   # pragma: no cover
        say(f"WARNING: could not archive the instrument ({exc}) — copy it by hand")

    say(f"round 8b start  device={DEVICE}  peer={PEER}  {BGP_AS}->{WRONG_AS} "
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
    pre = [take("pre", full=True) for _ in range(PRE_SAMPLES)]
    s = pre[-1]
    say(f"  session: {s['session']}")
    say(f"  neighbor: fsm={s['neighbor'].get('fsm')} "
        f"read={s['neighbor'].get('socket_armed_read')} "
        f"write={s['neighbor'].get('socket_armed_write')} "
        f"io={s['neighbor'].get('socket_armed_io')} "
        f"reported={s['neighbor'].get('socket_reported')}")
    say(f"  raw: {s['neighbor'].get('socket_line_raw')}")
    say(f"  bfd: {s['bfd']}")

    if not s["session"].get("established"):
        say("ABORT — the session is not Established at baseline. Nothing to break.")
        return 2

    if s["neighbor"].get("socket_reported") is False:
        say("ABORT — no socket line in the neighbour output. Either the platform "
            "stopped emitting it or the regex no longer matches; either way the "
            "round cannot test §2a.2. (This also refutes seal claim §2a.3, which "
            "round 8 CONFIRMED on 363 samples, so suspect the regex first.)")
        return 2

    # PRECONDITION 3, and the one round 8 had available and did not read.
    armed = [p for p in pre if p["neighbor"].get("socket_armed_read") is True]
    say(f"  instrument check: socket armed for read on {len(armed)} of {len(pre)} "
        f"baseline samples")
    if len(armed) != len(pre):
        say("ABORT — the socket does not read armed on an Established session, so "
            "the instrument cannot produce the value that would refute §2a.2. "
            "A zero from this sampler would measure the sampler (OBS-133). "
            "NOTHING HAS BEEN PUSHED; the fabric is untouched.")
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
    say(f"applied (push said '{status}') — dense sampling for {args.dense}s "
        f"(~{int(args.dense / 23)} retry cycles expected)")

    dense("post_fault_dense", args.dense)
    say(f"--- dense done, sparse for {SETTLE_SECONDS}s ---")
    sparse("post_fault_settle", SETTLE_SECONDS, SPARSE_INTERVAL)

    restore("round-complete")
    say(f"--- {SETTLE_SECONDS}s of post-restore sampling ---")
    sparse("post_restore", SETTLE_SECONDS, SPARSE_INTERVAL)

    _stop.set()
    say("--- verdict ---")
    v = verdict()
    for k, val in v.items():
        say(f"  {k}: {val}")
    say("round 8b complete")
    print(f"\n  Payload: {RUN}")
    print("  It is inside the repo. Commit it — a round is archived when its "
          "payload is committed (§6.1d).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
