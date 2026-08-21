# On-Call Runbook — the agent runs unattended, you don't

**B-410.** This is not `docs/archive/OPERATOR-RUNBOOK.md`. That document is for
a human sitting at a terminal with the lab in front of them, working through a
list of tasks. This document is for a human who is *not* at a terminal, who
was just paged, and who has not read a design doc in their life. If that is
you, start at §1 and stop reading the moment you have what you need.

**Read this once, before you are ever paged, if you can.** At 3 a.m. you will
not have time to learn what a field means; you will only have time to look it
up. This document is written so that looking it up is fast.

## 0. The caveat that has to come first

`SECURITY.md`'s own "Unsupported deployment modes" section names this exact
use case: *"Production paging or incident closure... None of the outputs here
should drive automated remediation or unattended paging."* Nobody has revoked
that sentence. If you are on call for this system, someone has chosen to run
it this way anyway — that is a real, defensible choice (the tool is
read-only; the worst case is a wrong or missed page, not a wrong change), but
it means **you are the reviewed, tested part of this deployment**. Nothing
below should be read as "the system has been certified for this." It has not.
Treat every page as a lead, not a verdict, until you have read the evidence
behind it yourself.

## 1. How you got paged, in one sentence

Something noticed a condition, decided it was worth investigating, ran
`nettools investigate ... --notify`, and that run posted to Telegram. The
"something" is **not** a part of `nettools` that runs on a timer by itself —
`nettools` never schedules itself, listens on a port, or decides to page you.
It is triggered by infrastructure *around* it that your team built:
most likely an Alertmanager webhook piped through `nettools route-event` into
`nettools investigate --notify` (the shape `examples/systemd/nettools-webhook.py`
and `examples/n8n/investigate-on-alert.json` show — **both are named
"EXAMPLE, not shipped code"** in their own files; whatever actually paged you
may be one of these almost verbatim, or something your team wrote from
scratch). If you don't know which, that not-knowing is itself tracked —
`docs/build/FINDINGS.md`'s Q-008 asks "which host runs `nettools`, and does
it have egress to Telegram" and is still open.

One thing that did **not** page you: `event_watch.py`, the module that reads
Loki and evaluates whether a log line is worth investigating. It is
deliberately dry-run-only — its own docstring says "never wired into
`cli.py`" — and as of this writing every mnemonic in `mnemonics.yaml` is
marked `fires: false` with a stated reason (steady-state noise, no
extractable subject, or never observed). If Loki is involved in your
deployment at all today, a human is reading its output, not a page.

## 2. A page arrived. Read this first.

**What is actually in the message, today**, verbatim, is built by
`notifier.render_report_text()`:

```
{finding} — {device} → {subject}
Cause: <rung> on <device>
Trustworthy: yes  /  Trustworthy: NO — verify before acting on this

• <observation 1>
• <observation 2>
...
<interpretation lines, if a paraphrase ran>

Next: <recommendation, if one was rendered>

Ticket: <run_id>
```

The `Cause:`/`Trustworthy:`/`Ticket:` lines were, until B-209b, **built into
`notifier.py` and not reaching you**: `render_report_text` always accepted
`cause`/`trustworthy`/`ticket_id` as optional arguments, but `cli.py`'s
`--notify` handling — the one thing that actually calls it — did not pass
them. That gap is closed: `_cmd_investigate` now threads all three through
`relay_policy.relay()` (the same call that also applies silences, ownership
routing, and de-dup — §5 and §8 below), which forwards them to
`notify_owner()`/`notify()` unchanged, so a page now names not just *what*
was found and *where*, but whether the answer is trustworthy, which rung
caused it, and the exact ticket to open for the rest.

`Cause:` is still omitted (not blank) when the descent found no cause to
name (`cause_not_localised`, `all_layers_healthy`, ...) — absence is never
zero, the same rule the rest of this codebase applies everywhere else. If a
page you're reading is missing all three lines, it predates this wiring;
check the git blame on `_cmd_investigate`'s `--notify` block if the date
matters.

**What §2's page still does not give you directly — the diagnosis id, and
the ticket's own content (only its `run_id` is on the page now):**

1. `nettools ledger summary` shows recent diagnoses, most-recent first,
   including the id you'll need in §6.
2. The ticket for this run is the most recently opened file under
   `NETTOOLS_TICKET_DIR` (default `./tickets/`): `ls -t tickets/ | head -1`.
   Filenames are UTC-timestamp-prefixed, so lexical order is chronological
   order. If you have an MCP client pointed at this deployment, ask it to
   call `list_lab_tickets` instead — same answer, no shell needed.
3. **The ticket and the ledger diagnosis for the same run are not
   cross-referenced today.** The ticket's `run_id` and the ledger's
   diagnosis id are two independent UUIDs, minted by two different calls in
   `cli.py`'s `_cmd_investigate` (the ledger write happens *before* the
   ticket is even opened), and neither carries the other. If you need both,
   read the terminal output from the original run — see the next point —
   before it scrolls away or the shell history is gone.
4. If the run happened interactively (someone is watching a terminal, or a
   log captured stderr), the diagnosis id is printed on **stderr**, not in
   the JSON payload: `# Diagnosis recorded: id=<id> -- to record a human
   verdict, run nettools ledger verdict <id> ...`. If your unattended
   pipeline discards stderr (check — the systemd example above does, via
   `capture_output=True` with nothing done with it), that line is gone and
   `nettools ledger summary` is your only way back to the id.

## 3. The one distinction that will get you at 3 a.m.

**"The tool could not evaluate this" and "the network is fine" are different
facts, and this system is built to keep them different — but only if you
read the right field.**

`nettools investigate`'s JSON payload carries a boolean, `trustworthy`, that
is *not* "is the network healthy" — it is "did this run produce an answer you
may act on". Six `finding` values are possible; three make `trustworthy:
false`, three make it `true` regardless of whether a fault was found:

| `finding` | `trustworthy` | What it means |
|---|---|---|
| `undetermined` | **false** | A rung could not be read. Nothing below an unread rung means anything. **Retry the question**, don't act on it. |
| `temporally_incoherent` | **false** | Every rung *was* read, but not as one consistent state — the fabric may be broken or fine and this run cannot say which (evidence-epoch skew, B-436). **Re-run**, don't act on it. |
| `subject_not_found` | **false** | The device answered and said the named object does not exist. The refusal is reliable; there is nothing to act on because no investigation of the network happened. **Fix the question** (wrong peer address, wrong interface name), don't retry it as-is. |
| `all_layers_healthy` | true | Every rung walked, none broken. The network, on this path, is fine. |
| `no_fault_on_path` | true | Something on the device *is* broken (see `off_path` in the payload) but it does not sit between this device and this subject. Do not act on the off-path finding as if it explained the page. |
| `cause_not_localised` | true | Rungs broken above, everything healthy below — an honest "nothing beneath explains this," not a failure. |
| *(a rung name, e.g. `bgp_session_down`)* | true | A real fault, localised, with a causal chain you can read in `causal_chain`. |

**The rule that matters:** `trustworthy: false` is never "the network is
fine" wearing a green light, and `trustworthy: true` is never "we didn't
look" wearing a red one. If you remember one field name from this whole
document, remember `trustworthy` and read it before you read `finding`.

**The exit code trap — read this before you script anything.** `nettools
investigate` and `nettools health` both use exit codes `0`/`1`/`2`, and they
**mean different things**:

| Exit | `nettools investigate` | `nettools health` |
|---|---|---|
| 0 | No fault on the path (`all_layers_healthy` or `no_fault_on_path`) | Nothing worse than `info` on any evaluated device |
| 1 | A fault was found and trusted | Worst device severity is `warning` |
| 2 | **No trustworthy answer** (`undetermined`, `temporally_incoherent`, `subject_not_found`, or a collection failure) | Worst device severity is `critical` |

A `2` from `investigate` is an **answer problem** — the tool is telling you
it doesn't know. A `2` from `health` is the **worst network problem** it can
report. If a script or a dashboard reads both commands' exit codes into one
number without saying which command produced it, that number is actively
misleading, and it has already happened once in this project's own history
(that ambiguity is exactly why `_cmd_investigate`'s docstring calls out the
divergence explicitly — read it before building anything that gates on both).

**`unevaluated` is a third state, not a variant of "healthy."** Inside
`nettools health`'s verdict, any rule whose input intent failed to parse is
skipped and the intent name is listed under `unevaluated` in the verdict body
— it never silently counts as a pass. A device can show `severity: "ok"` and
still have entries under `unevaluated`; read that list before treating `ok`
as "checked and clean" rather than "nothing we checked found a problem."

## 4. What this system is not allowed to do

- **It cannot change anything, anywhere.** Every command in `nettools` is
  read-only by construction: `platforms.APPROVED_COMMANDS` is an exact-match
  allowlist checked *before* credentials load, there is no config mode, no
  shell, and no `run_command(device, command)` escape hatch. This is
  `CLAUDE.md`'s central invariant and it is not a policy — it is what the
  code is capable of at all.
- **It cannot acknowledge, escalate, or be talked to.** `notifier.py` has
  *no inbound surface, by design* — no command handler, no webhook listener,
  nothing that reads a reply. Replying to the Telegram message does nothing;
  the bot is not listening. There is no "ack" this system can see, which is
  also why `ownership.py`'s escalation logic can only key off an
  **age**, never off "has anyone looked" — it has no way to know.
- **It cannot fix what it found.** There is no Stage 3 here. The write path
  — anything that would change device state — is gated on an identity
  provider this project does not have (`interfaces.md`, decision D3) and is
  unbuilt. If a page ever implies "and it fixed it," that implication is
  false; nothing in this system commits a change.
- **A silenced finding is quieter, never invisible — to a report.** See §5.
  This is a constraint on the *design*, worth knowing before you go looking
  for a "mute forever" switch: none exists, on purpose.

## 5. Silencing something during a planned change

**[Corrected 2026-08-20] The mechanism is wired for `nettools health` now.**
This section originally said it was not reachable from the command line at
all; that stopped being true when `--silence-file` shipped. **[B-209b]
`nettools investigate --notify` reaches the same silence file now too, and
so does `ownership.py` (who gets told).** Read every part below — what
changed for `health`, what changed for `investigate --notify`, and what is
still genuinely missing.

**What it does.** `health.py` implements a maintenance
window (`Silence`): `device`/`rule`/`subject` (each optional, each an AND), a
mandatory `expires_at` (there is no such thing as a silence that never ends),
and a `reason`/`created_by` that are also mandatory — an unexplained silence
is barely better than an unexplained page. Critically, **a silenced finding
is never removed from a report.** `apply_silences` keeps it in `findings`,
tags it `"silenced": true`, and attaches who silenced it, why, and until
when. What changes is narrower: it stops contributing to the verdict's
`severity` (so a cron job gating on exit code isn't failed by a fault you
already know about) and moves into its own `counts["silenced"]` bucket. A
separate field, `raw_severity`, always preserves what the severity would
have been with **no** silence applied — so "silenced, and would have been
critical" can never be misread as "ok". `--notify`'s narrower rule (in
`notifier.py`) is the one place a silence actually stops delivery outright,
because paging is a single channel with nothing to tag.

**What's reachable now.** `nettools health --silence-file PATH` exists and
calls `evaluate_fabric_with_silences`, which resolves through `health.
_resolve_silences`: an explicit `--silence-file` wins; omit it and the
`NETTOOLS_SILENCE_FILE` environment variable (declared in `settings.py`) is
tried instead; neither present, and behaviour is unchanged from before the
flag existed — no silences applied. So filing a silence and having it
actually suppress `nettools health`'s verdict severity **is** something you
can do from the command line today, for `health`.

**[B-209b] `nettools investigate --notify` now reads the same
`NETTOOLS_SILENCE_FILE` environment variable too**, with no flag of its own
to set — `relay_policy.relay()` (the hardened relay `--notify` calls, §2)
checks it on every run before attempting delivery. One file covers both
surfaces: a silence filed for `health` also stops the identical finding from
paging through `investigate`, and vice versa. Delivery is the only thing a
match changes here, same as the narrower `--notify` rule the "What it does"
paragraph above already states — the investigation's own JSON payload and
exit code are untouched; only the page is withheld. `ownership.py` (who
gets told) is resolved in the same call, via `NETTOOLS_OWNERSHIP_FILE` — a
declarative table routing a finding to an owner's own Telegram destination,
falling back to today's single default channel when unset. A third,
genuinely new capability rides along with no `health` equivalent at all:
`NETTOOLS_RELAY_STATE_FILE` de-duplicates, so the same (device, subject)
signature does not re-page on every run inside a window
(`NETTOOLS_RELAY_DEDUP_SECONDS`, default 1800s). All three variables are
declared in `settings.py` (`nettools config show` lists them) and unset by
default — unset, `--notify`'s delivery is exactly what it was before B-209b.

**The gap that's still real.** There is still no dedicated `nettools
silence` or `nettools ownership` subcommand for authoring either file (you
write the YAML by hand against `health.Silence`'s / `ownership.
OwnershipTable`'s fields), and `investigate` still has no `--silence-file`/
`--ownership-file` *flag* the way `health` has one — only the environment
variables reach it, so a per-run override (as opposed to a per-deployment
default) is not possible from `investigate` today. `incident_correlation.py`
(grouping related pages into one incident) remains genuinely unwired:
tested, but it has no CLI or MCP surface — and `relay_policy.py`'s own
docstring explains why it was not simply called from here either: it answers
a retrospective, batch-shaped question over the ledger, not this module's
"one call, right now" shape.

**What to actually do about an `investigate`-triggered page during a planned
change:** add (or reuse) a row in whatever file `NETTOOLS_SILENCE_FILE`
names — it reaches `investigate --notify` directly now, no external
workaround required (see above). If that variable is not set for your
deployment at all, fall back to the old answer: tell your team out of band
(the channel this system does not have), and disable or pause whatever
*external* scheduler is calling `nettools investigate --notify` for the
device in question (the Alertmanager rule, the n8n flow, the cron entry).

## 6. Closing a ticket, and what closure means

**You do not close a ticket. `nettools` already did, automatically, the
moment the run that opened it finished.** `Ticket.close()` is called at the
end of `_record_in_ticket` inside `cli.py`'s `_cmd_investigate`, for every
run, unconditionally. There is no operator action called "close a ticket" —
if you go looking for one, you are looking for something that does not
exist by design. What "closed" means, structurally: the flight recorder is
done appending sections to that file. It is **not** an incident-management
concept — it does not mean resolved, does not mean correct, and does not
mean anyone has looked at it. A ticket's own header/body records exactly one
thing about correctness: *what the code observed*, kept structurally apart
from anything a model said about itself (`ticket.py`'s central rule — every
field is code-instrumented, never a model's self-report, because two models
lying about their own coverage were, by measurement, indistinguishable at
read time — OBS-165).

So **"closing a ticket" means investigated and reported — never verified
correct.** The only thing left for a human to add is a verdict, and that is
a separate action against a separate identifier: see §7.

## 7. When it's wrong — recording that

**The tool records what it diagnosed. Only a human records whether it was
right.** There is no `outcome` parameter anywhere in the code path that
writes a diagnosis — `ledger.record_diagnosis()` has no such field, and
cannot be called with one. The verdict is a deliberate, separate, later
action:

```bash
nettools ledger verdict DIAGNOSIS_ID confirmed_correct|incorrect|unknown \
    --by YOUR_NAME [--note "why, in one line"]
```

- `DIAGNOSIS_ID` is the id from `nettools ledger summary` or the `# Diagnosis
  recorded: id=...` stderr line (see §2, point 4) — **not** the ticket's
  `run_id`, which is a different string for the same run (§2, point 3).
- `--by` has no silent default identity — you must say who is judging this;
  an accuracy claim with nobody's name on it is not evidence, by design.
- Recording `incorrect` does not undo, retract, or delete anything the tool
  already reported — it is an appended fact, exactly like every other
  append-only record in this project (`FINDINGS.md`'s own rule, applied
  here). The wrong answer stays visible; the correction sits beside it.
- `unknown` is a legitimate answer, not a placeholder to avoid using — if you
  genuinely couldn't confirm either way, say so. `nettools ledger summary`
  always shows the `unknown` count, so a bucket of unjudged diagnoses is
  visible rather than silently dropped from the accounting.
- If `DIAGNOSIS_ID` doesn't match anything in the ledger, the CLI records the
  verdict anyway and tells you so (`diagnosis_found: false` in the response)
  rather than refusing — a mismatch stays visible instead of being silently
  swallowed. That is worth double-checking: it almost always means you
  copied the wrong id (the ticket's `run_id` instead of the diagnosis id is
  the most likely mix-up, per §2).

## 8. Quick reference

| You need | Do this |
|---|---|
| What does the page mean, right now | §2 — read `finding` + `trustworthy` before anything else |
| Is this "couldn't check" or "network's fine" | §3's table — `trustworthy: false` only for `undetermined`/`temporally_incoherent`/`subject_not_found` |
| Same integer, `investigate` vs `health` | §3's exit-code table — they are not the same scale |
| Can it fix this itself | No. Never. §4 |
| Stop paging during a maintenance window | §5 — file a row in `$NETTOOLS_SILENCE_FILE`; it now reaches `investigate --notify` directly (B-209b), no external workaround needed |
| Route a page to a specific owner, or stop the same fault re-paging every run | §5 — `NETTOOLS_OWNERSHIP_FILE` / `NETTOOLS_RELAY_STATE_FILE` (both unset by default; unset changes nothing) |
| "Close" this ticket | Nothing to do — already closed itself. §6 |
| Record that the diagnosis was wrong (or right) | `nettools ledger verdict ID OUTCOME --by NAME` — §7 |
| Find the diagnosis id after the fact | `nettools ledger summary`, or stderr from the original run — §2 |
| Find the ticket file after the fact | `ls -t $NETTOOLS_TICKET_DIR \| head -1`, or MCP `list_lab_tickets` — §2 |

## See also

- `docs/archive/OPERATOR-RUNBOOK.md` — the lab-window runbook (archived
  2026-08-20 as a completed-process document per M0's convention; some of the
  individual lab items it lists, e.g. B-440/round 6, are still `BLOCKED` in
  `BACKLOG.md` rather than done — see that file for current status). Different
  audience: a human at a terminal with the fabric in front of them.
- `SECURITY.md` — read §0 above first, then the full "Unsupported deployment
  modes" section if this is genuinely how your team runs this.
- `examples/README.md` — the orchestrator boundary rule and the two example
  pipelines (n8n, systemd) that can turn an alert into a page.
- `docs/build/FINDINGS.md` Q-008 — which host runs this, and does it have
  egress to Telegram. Open.
