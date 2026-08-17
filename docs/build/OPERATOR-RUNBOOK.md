# Operator Runbook — the lab window

**For the human operator only.** Everything in this document is blocked on you
sitting down at a terminal with the `sota-xrd` fabric reachable. It assumes you
have read nothing else recently — that is the point of it existing.

Work top to bottom. Task A is the highest-value use of a lab window; Tasks C and
part of D need no lab at all and can be done from a sofa.

---

## 1. Sixty seconds of orientation

This is a read-only IOS-XR inspection tool with a deterministic dependency
descent underneath it: point it at a device and a subject, and it walks a fixed
ladder of rungs (`bgp_session → transport → route_to_peer → igp_adjacency →
interface`) reporting the *lowest broken one* as the cause, with no model in the
loop for the diagnosis itself. MVP-0 shipped and is scored — 1813 tests pass,
lint is clean, and four fault-injection rounds have run against the real
9-device lab, each with a prediction sealed and pushed before the fault landed.

What is left is not building. It is spending lab time to settle two open
claims (B-463 — does `bgp_session` separate from `transport` at all, and B-440 —
does the tool stay quiet about an unrelated shut port when a real fault is
elsewhere), plus a small amount of no-lab work re-testing how an MCP client
picks tools and finishing off a Telegram relay so a report can leave the
terminal — the relay's code, tests and CLI wiring are already sitting in the
working tree, just not yet committed. Round 8b and round 6 are both sealed and
waiting; nothing about them can go stale by sitting, but every hour they don't
run is an hour B-463 and B-440 stay open.

---

## 2. Pre-flight checklist

Run these before touching the lab. All read-only.

```bash
cd /home/rami/ai-agent-ops/ios-xr-nettools

# 1. Branch is what you think it is, and matches the remote.
git fetch origin
git status -sb
#   -> "## feat/investigation-layer...origin/feat/investigation-layer"
#      with no [ahead]/[behind]. Untracked/modified files touching
#      notifier.py, cli.py, TRACKER.md, .env.example or interfaces.md are

# 2. Tests and lint, from the venv.
source .venv/bin/activate
make test
#   -> 1813 passed, 24 skipped, no network and no credentials needed for this.
#      A LOWER number, or a failure, means something changed under you --
#      stop and find out what before you touch the lab.
make lint
#   -> "All checks passed!"

# 3. Inventory loads, credential-free.
nettools inventory
#   -> lists the 9 devices (P1-P4, PE1-PE4, RR1). No SSH yet.

# 4. Live reachability, one device.
make facts DEVICE=PE1
#   -> facts come back. If this hangs or refuses, the lab is not up --
#      go and check containerlab before anything else.

# 5. Fabric health, live, every device.
make health
#   -> exit 0/1/2 per device severity. Read the JSON findings, don't just
#      trust the exit code -- a live check while writing this runbook came
#      back WARNING on several PE-series devices, and read cleanly as two
#      already-filed, already-understood false alarms rather than anything
#      broken:
#        - isis_adjacency_count_drift (B-465, unfixed): the expected baseline
#          was learned while the fabric was broken, nobody has re-run
#          learn-topology since it was repaired, so a *healthy* adjacency
#          count now reads as drift.
#        - bgp_no_prefixes: inventory/lab.yaml already carries an operator
#          note that every session on this lab carries 0 prefixes by design;
#          health.py has no such knowledge and flags it every time regardless.
```

If steps 1-4 are clean and step 5's only findings are one or both of those two,
the fabric is in the state every round below assumes. Anything else -- a
device that won't collect, a finding that isn't one of these two -- stop and
look before starting a round.

---

## 3. Task A — Round 8b (do this one first)

**What it tests, in two sentences.** Round 8 put an AS mismatch on PE2's
session to RR1 and confirmed four of five sealed claims, but its socket-state
sampler had a first-match regex bug that read `False` on every sample —
including 195 on fully Established sessions — so its central claim (§2a.2:
does `bgp_session` ever separate from `transport`, even transiently, during the
OpenSent retry window) came back **void, not refuted**. Round 8b re-runs the
identical fault with a fixed, self-checking instrument, at enough retry cycles
that a zero this time means something (P(zero) ≈ 2×10⁻⁵ under the round-8
timing model) rather than measuring the sampler again.

### 3.1 The three preconditions, and how the script enforces them

`ROUND-8.md` §6.2 sealed three things that must hold before the round runs.
`round8b.py` checks all three itself, in order, before it ever pushes anything:

| Precondition | What it checks | Where in the script |
|---|---|---|
| 1. Regex is anchored and positional | Uses `template_parsers._BGP_SOCKET` verbatim, not round 8's `socket.*?(armed\|not armed)` minimal-width search | Built into `parse_neighbor()` |
| 2. Raw lines stored beside derived fields | Every sample carries `socket_line_raw` and `last_reset` as well as the booleans parsed from them | `parse_neighbor()`, checked at baseline |
| 3. Baseline reported separately, and aborts the round | 4 baseline samples taken before the fault; if the socket does not read armed on **all** of them, the round stops | `main()`, before the `push()` call |

**If the instrument check fails, it aborts before pushing anything.** Two
possible aborts, both exit code 2, both before `push(_injector, APPLY, ...)` is
ever called:

- session not Established at baseline — nothing to break;
- socket not armed on every baseline sample — the instrument itself is
  suspect, and running anyway would produce round 8's exact failure again.

The third abort message says so explicitly: *"NOTHING HAS BEEN PUSHED; the
fabric is untouched."* The first two return before that line is even reached,
by construction. If you hit an abort, the fabric is exactly as it was — there
is nothing to restore, and re-running `--dry-run` is always safe.

### 3.2 The exact command sequence

```bash
cd ~/ai-agent-ops/faultlab

# Dry run first -- exercises the whole flow, including both instrument
# checks, and pushes nothing (fault_lab._dry_run gates every push() call).
python round8b.py --dry-run

# If the dry run's instrument check passes and nothing looks wrong in the
# baseline printout, run for real:
python round8b.py
```

No flags are required for the real run — `--dense` defaults to 900 s (the
value §6.3's arithmetic in the module docstring is built around) and `--out`
defaults into the tracked repo (`evidence-archive/round8b/`), so the payload
does not need to be found and copied afterwards the way round 7 and round 8's
did.

### 3.3 What it does and how long it takes

Baseline (4 samples) → instrument check → apply the fault (`remote-as 65001` +
`ebgp-multihop 5` on PE2's neighbour `10.255.0.31`) → **900 s dense sampling**
(one `show bgp neighbor` per sample, free-running, no sleep — resolution is
whatever the wire gives you) → 60 s settle → restore, verified by reading the
device back → 60 s post-restore sampling → verdict. **End to end, ~18 minutes**
of sampling once the fault is live, plus a few seconds either side for
baseline and restore.

### 3.4 What "good" looks like

There is no single pass/fail verdict here — the round exists to convert a void
into a result **in either direction**, and both directions are a successful
round. Read `verdict.json`'s `instrument` block first:

```
"instrument": {
  "baseline_trustworthy": true,   <- must be true, or nothing below it means anything
  ...
}
```

With that true, `SEPARATION_OBSERVED` is the actual finding:

- **`true`** — at least one sample caught the socket armed for read while the
  session was not Established. §2a.2 holds; rungs 1 and 2 separate, captured
  rather than composed. B-463 closes in the positive direction.
- **`false`**, with `baseline_trustworthy: true` and `samples_post_fault`
  in the low hundreds — under the round-8 timing model this is evidence of
  *absence*, not of not-looking. §2a.2 is refuted and B-463 closes as "not
  separable at any resolution reachable over CLI."

Either way, the console prints the verdict fields as it finishes, and the
payload directory path, with a reminder that a round is archived when its
payload is *committed*, not when it's written to disk.

### 3.5 Commit and push, immediately after

```bash
cd /home/rami/ai-agent-ops/ios-xr-nettools
git add evidence-archive/round8b/<timestamp>/
git status   # confirm samples.jsonl, verdict.json, round8b.log and round8b.py
             # (self-archived by the script) are all staged
git commit -m "round 8b: <one line, what SEPARATION_OBSERVED came back as>"
git push
```

The script copies itself into the run directory on start, so the instrument
that produced the numbers is archived with them automatically — you don't need
to separately copy `round8b.py` in. If you want the result reflected in
`ROUND-8.md` itself (a new results subsection under §6, following the shape of
§5), that's a documentation follow-up and can happen any time after the commit
above — it does not gate archiving the raw payload.

---

## 4. Task B — Round 6 (do this one second)

**What it tests, in two sentences.** This is B-440, the reviewer-identified
trust-loss scenario: a real BGP fault plus an unrelated shut spare port on the
same device, checked for whether the tool ever names the spare port as a cause.
It is the first round with **two concurrent faults live at once**, deliberately
raising the harness's one-fault ceiling under `chaos-harness.md` §3.3's explicit
exception.

Unlike round 8b, **there is no dedicated script for this round** — it runs from
`fault_lab.py`'s menu.

### 4.1 Setup: already done, and worth knowing why

When this runbook was drafted, `fault_lab.py` had no entry applying both lines:
option 5 is the BGP-neighbour shutdown alone. **That has been fixed — use
option 8**, `bgp_shut_plus_spare_port_down`, which applies both in one commit
and reverts both.

It was added as a *new* option rather than by extending option 5, because
earlier rounds are recorded against option numbers and changing what option 5
means would rewrite their history.

> **The second half of that fix matters more than the first, and it is worth
> reading before you trust the restore.** `SNAPSHOT_SECTIONS` did not include
> `GigabitEthernet0/0/0/2`, and restore verification is an exact comparison of
> *those sections only*. **A section that is not listed cannot fail the check,
> whatever is left in it** — so a `shutdown` lingering on the spare port would
> have been inert on a restored fabric *and* invisible to the verification built
> to catch exactly that.
>
> `ROUND-6.md` §0.1 already warns that *"harmless and undetectable is the
> combination worth checking for."* It was written about the fault. It turned up
> in the machinery that checks the fault. `SPARE_IF` is in `SNAPSHOT_SECTIONS`
> now, so the restore covers it.

### 4.2 Before the window: confirm the spare is actually off-path

`ROUND-6.md` §4 is explicit that the round is void before it starts if this
doesn't hold. The most recent evidence (`MCP-EXPERIMENT.md` §10.3) already
observed `Gi0/0/0/2` off-path and healthy on 2026-08-17, but fabric state can
change — check fresh, read-only, no lab window spent:

```bash
nettools interface PE2 GigabitEthernet0/0/0/2
#   -> must show up/up. If it's already down, pick a different spare port
#      and update SPARE_IF above before proceeding.

nettools route PE2 10.255.0.31
#   -> PE2's route to RR1's loopback. Confirm the egress interface is NOT
#      Gi0/0/0/2. If it is, the round is void as written -- stop here.
```

### 4.3 The exact command sequence

```bash
cd ~/ai-agent-ops/faultlab
python fault_lab.py
```

At the menu, choose **`8`** — `bgp_shut_plus_spare_port_down`. Not `5`, which is
the BGP shutdown alone and would run a different, one-fault round.
`fault_lab.py` applies both lines in one commit, verifies by reading the device,
and then blocks:

```
  FAULT IS LIVE.  Subject to investigate:  RR1 10.255.0.12
  Auto-revert in 20 minutes if you do not confirm.

  Press Enter when the investigation has finished...
```

**Do not press Enter yet.** In a second terminal, run the actual subject:

```bash
source /home/rami/ai-agent-ops/ios-xr-nettools/.venv/bin/activate
nettools investigate RR1 10.255.0.12 --format json | tee ~/round6-investigate.json
```

Read the output against `ROUND-6.md` §2 before doing anything else — this is
the one round whose failure mode is a *plausible-looking wrong answer*, so read
it carefully rather than glancing at the exit code.

Then return to the first terminal and press Enter. `fault_lab.py` reverts both
lines and verifies by config-snapshot comparison (now that `SNAPSHOT_SECTIONS`
includes the spare port).

### 4.4 What "good" looks like

`ROUND-6.md` §2.1-2.2 predicts `B B H H H` → `transport_blocked`, with rung 5
(`interface`) reporting **healthy** and `Gi0/0/0/2` **not named anywhere** in
the report's prose — not as a cause, not hedged, not mentioned at all. That
silence is the claim. If `Gi0/0/0/2` appears anywhere, or rung 5 reports
`broken`, that is reviewer B's trust-loss scenario reproduced, and it's still a
valid, valuable, must-be-recorded result — just not the one that closes B-440
the way you'd want.

### 4.5 Restore verification — both faults, read back

Do not trust `fault_lab.py`'s console output alone. After it reports
`RESTORE verified`, confirm independently and read-only:

```bash
nettools interface PE2 GigabitEthernet0/0/0/2
#   -> must be back up/up

make bgp DEVICE=PE2
#   -> the session to 10.255.0.31 must be re-Established
```

`ROUND-6.md` §4 is explicit that this is the combination worth checking for: a
lingering shutdown on a spare port is inert and undetectable by anyone who
isn't looking, which is exactly reviewer B's scenario one level down.

### 4.6 Commit and push

```bash
cd /home/rami/ai-agent-ops/ios-xr-nettools
mkdir -p evidence-archive/round6/<timestamp>
cp ~/round6-investigate.json evidence-archive/round6/<timestamp>/
cp ~/ai-agent-ops/faultlab/runs/<session-stamp>/public.log \
   ~/ai-agent-ops/faultlab/runs/<session-stamp>/truth.jsonl \
   evidence-archive/round6/<timestamp>/
git add evidence-archive/round6/<timestamp>/
git commit -m "round 6: B-440, two concurrent faults, <one line on the result>"
git push
```

`~/ai-agent-ops/faultlab/runs/` is not a git repository, so its logs are
unarchived until copied in — the same OBS-135 problem `ROUND-6.md` §4 already
names.

---

## 5. Task C — the MCP re-ask

**Why the last run was void, in two sentences.** The registered prediction
(`MCP-EXPERIMENT.md` §9) needed the fabric in the same *broken* state as the
original observation, but the re-test ran against a healthy fabric — so the
model's discovery step (going to find PE2's peer address) resolved the question
before ever reaching the tool-selection choice the prediction was about. The
fix costs nothing extra: run the same question **while a real fault is live**,
which Tasks A and B both already give you for free.

### 5.1 Q1, during Task A or B's fault window

While round 8b is dense-sampling, or while round 6's fault is held (either
works — the sampling is a separate process and asking a model a question does
not perturb it), open a **fresh** LM Studio session against `nettools-mcp` and
ask, verbatim, exactly this:

> **why is the BGP session on PE2 down?**

When it asks for a peer address (expected — the question doesn't contain one),
give it `10.255.0.31`. Capture:

- which tool it calls **first**, and the exact arguments;
- the **reasoning trace**, if your client exposes one — this is worth more
  than the transcript, because the original finding was the trace *naming the
  tool's description* as the reason for selecting it;
- wall-clock time to a final answer;
- model and client, exactly (the original was `gemma-4-e4b` in LM Studio; a
  different one is fine but changes what the run measures, so record it).

This settles §9 properly for the first time — both prior "why" questions in the
re-test went to `investigate_lab_session` on a healthy fabric; this is the one
still owed on a genuinely broken one.

### 5.2 Q5/Q6, no lab needed

Separately, any time, fabric healthy or not — this needs the MCP server and the
live fabric reachable, but **not** a fault applied, so it doesn't need to ride
along with Task A or B at all. In one **fresh** session:

1. Ask, verbatim: **why can't RR1 reach 10.255.0.12?** (this is Q2 — it must
   run first, in this session, so what follows summarises a real five-rung
   result rather than a refusal the way the last attempt accidentally did).
2. Immediately after it completes, in the same session: **summarise that in
   two sentences for a colleague.**
3. Only if that summary drops or misattributes something: **did your summary
   include every rung the investigation reported?**

Capture the full transcript and the reasoning trace for both. This is the
re-ask `MCP-EXPERIMENT.md` §10.5 says is still owed — the previous attempt
asked the summarisation question after a refusal (Q4) instead of after a real
investigation, so nothing was actually tested.

---

## 6. Task D — Telegram (T-035)

**T-035 is built, committed and pushed** (`808f9b4`) — `notifier.py`,
`tests/test_notifier.py`, and the `--notify` wiring in `cli.py`. This runbook
was drafted while that work was still in the working tree and said otherwise;
`docs/build/TRACKER.md`'s T-035 row is authoritative if the two ever disagree
again.

**Nothing about it delivers until you put a token in `.env`.** Until then
`--notify` is a no-op that says so on stderr, which is deliberate: the flag is
meant to be safe in a cron entry written before a channel exists. Trust what's
on disk over this
document.

```bash
grep -A1 "^| T-035" docs/build/TRACKER.md
pytest tests/test_notifier.py -q     # -> 18 passed, if the module is there
```

### 6.1 What goes in `.env`

Two variables, per `notifier.py`'s own env names (these are what the current
`.env.example` documents — note they are **not** the `NETTOOLS_TELEGRAM_*`
names an older draft used):

```bash
NETTOOLS_NOTIFIER=telegram        # default is "none" -- a silent no-op
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_CHAT_ID=<your numeric chat id>
```

`TELEGRAM_BOT_TOKEN` is a credential. It goes in `.env` only — never in a
commit, a log line, or pasted into a chat. `TELEGRAM_CHAT_ID` is a
comma-separated allowlist; **empty means send to nobody**, not everybody, the
same fail-closed rule the command allowlist uses. Two optional ones, defaults
are sane: `NETTOOLS_NOTIFIER_TIMEOUT_SECONDS` (default 10) and
`NETTOOLS_TELEGRAM_MAX_CHARS` (default 3500, refuses to send rather than
truncate a report that was grounded as a whole).

### 6.2 Getting a bot token and your chat ID

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the
   prompts (a display name, then a username ending in `bot`). It replies with
   the token — copy that into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot from your own account, so it has
   something to look up.
3. Get your numeric chat ID either by messaging **@userinfobot** (it replies
   with your ID directly), or by fetching
   `https://api.telegram.org/bot<token>/getUpdates` and reading the
   `message.chat.id` field of the message you just sent.

### 6.3 Trying it

```bash
nettools investigate PE2 10.255.0.31 --from-fixtures --label broken --notify
```

Watch for one of these three notes in the output:

- `# Notification sent via telegram.` — it worked, check your phone.
- `# Notification failed (telegram): ...` — read the reason; it's never fatal
  to the exit code, by design (a run that found the fault and failed to post
  about it has still found the fault).
- `# NETTOOLS_NOTIFIER is 'none'; --notify did nothing.` — the env var isn't
  set, or `.env` wasn't picked up.

If the module is genuinely there and passing, it's worth a commit of its own —
`src/agent_nettools/notifier.py`, `tests/test_notifier.py`, and the `cli.py`/
`.env.example`/`TRACKER.md`/`interfaces.md` changes that go with it — before
anything else touches those files.

---

## 7. What to send back — one consolidated list

Gather all of this in one pass once Tasks A-D are done:

| From | What |
|---|---|
| **Task A** | The pushed commit hash for `evidence-archive/round8b/<timestamp>/`; the printed verdict block (`SEPARATION_OBSERVED`, `instrument`, `note`) |
| **Task B** | The pushed commit hash for `evidence-archive/round6/<timestamp>/`; whether `Gi0/0/0/2` appeared anywhere in the investigate report's prose |
| **Task C — Q1** | Full transcript, reasoning trace (or a note that the client doesn't expose one), first tool called + arguments, wall-clock time, model/client identity, which fault window it rode along with (A or B) |
| **Task C — Q2/Q5/Q6** | Full transcript and trace for all three; explicitly note whether Q6 ran (only if Q5 erred) |
| **Task D** | Whether `--notify` delivered, and if not, the exact failure note from stderr (the token is redacted from it by design — check that it is) |
| **Anything that felt wrong** | Per `MCP-RETEST-PROTOCOL.md` — slow, confusing, a tool that should exist and doesn't. Worth as much as a defect |

---

## 8. A decision you still owe — Q-008

**Which host runs `nettools` in the target deployment, and does it have
outbound egress to Telegram?** Recorded in `FINDINGS.md` as Q-008, open. It
doesn't block anything in this document — T-035 is optional and Task D above
works from wherever you're sitting right now — but it blocks T-035 actually
being useful in production rather than from a laptop with a Telegram client
already logged in. Worth answering before you rely on `--notify` from
anywhere other than an interactive session.
