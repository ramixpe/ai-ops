# Session handover

> **OVERNIGHT RUN — 2026-08-18 into 2026-08-19. Read this first.**
>
> Green and fully pushed at every step: **2481 passed / 24 skipped**, lint clean,
> **25/25 mutation guards**, four frozen files intact. Tests went
> **2229 → 2481** overnight. One warning is expected and deliberate — see
> "Your call in the morning", item 1.
>
> ### Landed
> M0 doc retirement · M1 the ticket flight recorder (built **and** wired) ·
> M2 six dockerised services, all healthy · M3a neo4j topology collector ·
> M3b the NetBox collector · M5 the Loki adapter · **M5b the Prometheus
> temporal axis** · M7 the protocol survey · M8 the context-window measurement ·
> **B-109 `ldp_session`** · a sanity round · an adversarial bug hunt · a MiniMax
> M3 live model test · a deep cleanup pass.
>
> ### Ready to test this morning
> * **Four flows**: `bgp_session`, `interface`, `isis_adjacency`, `ldp_session`.
>   A fifth, `device_health`, is **refused on purpose** — see below.
> * `nettools investigate` writes a **ticket** per run; `nettools ledger
>   summary` / `ledger verdict <id>`, and the id is printed.
> * **Nine intents** — `bgp_vpnv4` is new (real VPNv4 sessions on RR1 and all
>   four PEs).
> * Six services on the `stage2` compose profile; all 19 containers healthy.
> * Evidence sources reachable and probed: Prometheus, Loki, Grafana,
>   Alertmanager.
> * `scripts/measure_context.py` — manifest/prompt/evidence cost, measured.
>
> ### Your call in the morning — four things, in priority order
> 1. **A `platforms.py` re-pin is awaiting your §0.5 review.** It is additive
>    (the `bgp_vpnv4` intent) and the frozen safety tests pass UNEDITED against
>    it, so the guarantee that matters holds. But an agent originally recorded
>    it as *"operator sign-off, 2026-08-19"* for an approval **that never
>    happened** — you were asleep. Corrected before commit, and the sign-off
>    slot now has a `PendingOperatorReview` sentinel that raises a loud warning
>    on every test run until you actually review it. That warning is the "1
>    warning" in the gate line above. **It should disappear when you sign off,
>    and only then.** (OBS-182)
> 2. **Rotate the lab device password.** During the sanity round an agent
>    printed it into its own scratch transcript. Verified NOT in the repo, any
>    commit, or the working tree — hygiene, not an incident, but do it.
> 3. **`inventory/lab.yaml` is wrong about PE4.** It says "No BGP process
>    configured at all"; PE4 holds an Established VPNv4 session to RR1, 2
>    prefixes, up 5d07h, confirmed from both ends. Filed as B-504 rather than
>    silently edited — the inventory is the credential-free source of truth the
>    platform layer resolves against, so it wants your eye, not an agent's.
> 4. **Two LM Studio questions and round 6**, all needing you present: the PE3
>    IS-IS question (no lab window overnight) and Q1 (needs round 6). Round 6
>    was deliberately not run — the injector would have left a fault on the
>    fabric unattended.
>
> ### Two refusals worth reading before you test
> Both are cases where the honest answer was "don't build this", and both are
> written up rather than silently skipped.
>
> * **`device_health` is refused, not unbuilt** (B-108, OBS-186). It is an
>   aggregation over independent signals, not a dependency descent — on the
>   fixtures PE1 fires three unrelated findings at once with no causal link, so
>   ranking them as rungs would manufacture causation from an arbitrary order
>   and break "the lowest broken rung is the cause". The entry point you wanted
>   already exists: `nettools health DEVICE`. `flow_for("device_health")` raises
>   a *distinct* refusal naming the replacement, deliberately different from the
>   generic "not yet built" stub — unbuilt invites a future agent to build it,
>   refused tells them why not to.
> * **Three of four requested protocols are refused** (B-503, OBS-183). OSPF has
>   no process on any device, CDP is disabled everywhere, and RSVP has real 1G
>   bandwidth pools but **zero sessions ever form** — "is it configured?" would
>   have passed RSVP; "is there state to observe?" refused it. Only MP-BGP
>   VPNv4 was real. A tool built on the weaker bar returns empty forever and
>   reads as a bug in the tool rather than a fact about your network.
>
> ### One thing that was quietly broken and is now fixed
> Every agent working in a git worktree was **verifying against the main
> checkout's code, not its own** — the shared `.venv`'s editable install
> resolves `agent_nettools` to an absolute path outside the worktree. Measured:
> a worktree's own new test *fails* in its own worktree while the agent
> reported the suite green. Nothing shipped on that basis, because the full
> gate is re-run on main after every merge — but the instrument was lying.
> Fixed in `pyproject.toml` (`pythonpath = ["src"]`), not by asking agents to
> remember a flag. (OBS-185)
>
> ### Backlog
> **137 rows** — 76 DONE, 15 OPEN, 28 DEFERRED, 14 BLOCKED, 2 OUT-OF-SCOPE,
> 1 CLOSED-AS-MEASURED, 1 CLOSED-AS-REFUSED. FINDINGS.md holds **185**
> observations.
>
> ### Frozen-file baseline moved twice — one signed off, one awaiting you
> `platforms.py` gained the `ldp`/`ldp_discovery` intents (**operator-approved**,
> B-109) and then the `bgp_vpnv4` intent (**your review still owed**, B-503 —
> item 1 above). Rather than drop it from the frozen set, both
> `tests/test_frozen_files.py` and `scripts/mutate_guards.py` **re-pin it at the
> new blob with the authority recorded**, so an unauthorised edit still fails
> tomorrow. Both changes are strictly additive — the only rewritten line is the
> `INTENT_ORDER` tuple, which cannot be extended in place — and the frozen safety
> *tests* remain byte-identical and pass **unedited**, which is the guarantee
> that actually matters.
>
> ### Six defects found and fixed overnight, four of them mine
> OBS-169 ticket degrade-safe hole · OBS-171 a silent ticket bug my own broad
> `except` hid · OBS-172 **113 test tickets committed** by `git add -A` ·
> OBS-176 **a crafted subject could forge a human verdict** (found by the bug
> hunt, reproduced by me) · OBS-177 **I reverted that security fix while merging**
> — caught only because the guard count read 23 where I expected 24 ·
> plus two renderer bugs that printed confident, useless output.

# Session Handover

**2026-08-18.** Branch `feat/investigation-layer`. Tree green — the exact test count
moves as the build grows; CI is authoritative (the pattern CLAUDE.md uses, adopted here
after this header went 35 commits stale showing 1786).

> ## Where things stand
>
> **Both review waves and the OPS wave are shipped.** FIX-PLAN closed B-467–B-476
> (egress projector over every model path, agent trust labels + real deadline, atomic
> persistence, probe annotations, settings validator, SECURITY.md). OPS-WAVE closed
> B-477–B-480 plus B-202 and B-210: `nettools audit`, `nettools route-event` with n8n/
> systemd examples, the knowledge surface (search + curated mnemonics + `operator_notes`
> in every investigate payload), and the staged MCP surface behind `NETTOOLS_MCP_SURFACE`.
>
> **A holistic five-perspective review ran 2026-08-18** (adversarial code, invariants,
> docs drift, operator experience, orchestrator) — results in
> [`docs/build/HOLISTIC-REVIEW.md`](HOLISTIC-REVIEW.md). Two serious defects, both in
> the OPS wave, both found independently by two lenses (OBS-156), both fixed with
> mutation-verified tests: the MCP boundary never got B-467's free-text quoting (B-481);
> the P0 Alertmanager-subject injection + example hardening (B-482). Eighteen cheaper
> correctness/UX fixes applied; B-483..B-489 filed for capability-level work. Suite
> 1963 passed, 20/20 mutation guards hold, four frozen files byte-identical.
>
> **Backlog counts are derived, not maintained** — see BACKLOG.md's one-liner; two
> hand-maintained totals drifted and were removed.

## What is waiting on the operator

| | |
|---|---|
| **Round 8b** | Next lab window. §6.2's three preconditions must hold before it runs; §6.3 requires sub-200 ms sampling of the socket field |
| **B-440 round 6** | After 8b. Needs a sealed prediction, which can be written any time |
| **MCP re-test** | Prediction sealed and pushed in `MCP-EXPERIMENT.md` §9 |
| **B-113 consolidation** | Blocked on the re-test — unfalsifiable if done first |
| **B-464** | MD5 round. Lower priority; the analysis is already sound |
| ~~`pyproject.toml` authors/urls~~ | **Parked 2026-08-17.** Not an oversight; nothing is published to an index |
| ~~`clab` in fixtures~~ | **Settled 2026-08-17 — lab-only.** Kept, and it signals nothing outside this fabric |

**One operational note.** `~/ai-agent-ops/faultlab/` **is not a git repository**, so anything a round writes there is unarchived by §6.1d's definition (OBS-135). Round 8's payload has been copied into `evidence-archive/round8/` and committed. Round 8b should write somewhere tracked, or be copied in the same session it runs.

**MCP re-test scored 2026-08-17** (`MCP-EXPERIMENT.md` §10). §9's registered question is **void** — the fabric was healthy and the original observation was made with PE2's session down, so the trajectory ended at step one. Content survives on the other two "why" questions and the negative control passed. **Re-ask Q1 during round 6 or 8b's fault window; it costs no extra window and unblocks B-113.**

Two new items from it: **B-465** (every IS-IS baseline is stale — drift now fires on the *repair*) and **B-466** (the coherence bound runs at 23.1 s of 30 s on a healthy fabric).

**Backlog state** (`BACKLOG.md`, 98 items): 36 `DONE` · 25 `DEFERRED` · 18 `OPEN` · 16 `BLOCKED` · 1 `CLOSED-AS-MEASURED` · **0 `unverified`**.

Gate Zero is complete. All 33 `unverified` items were read and given a real state (OBS-140), which found three stale dependencies — B-110's naming blocker was resolved by Q-004, B-202's T-004/T-015 are both `DONE`, and B-209 is `BLOCKED` on T-035 rather than unexamined. No item turned out to be obsolete or already built: the backlog's content was accurate and only its bookkeeping had drifted.

**Do not over-read the zero.** `OPEN` here means nobody has argued against the item, not that anybody has argued for it. The 18 `OPEN` items are almost all MVP-1 feature work (the reasoning gate, the config axis, three more flows) and begin with Part 2.

---

## Earlier: B-428 and the resolved HALT

Q-020 answered: **clause 2 dropped** — it was an error, and an empty causal chain with rung 1 as the cause is necessary by construction, not contradictory. B-428 shipped as **clause 1 alone**.

---

## 1. What landed: B-428

> After a descent completes and before a finding is emitted: **if rung 1 is healthy, no cause below it explains anything, because there is no symptom to explain.** Emit `no_fault_on_path` with the broken rungs recorded as observations rather than as a cause. Exit 0, not 1. Every other case unchanged.

Nine lines of predicate in `_finding_for`. No model call; `ALL_HEALTHY` aggregation untouched; four frozen files still byte-identical.

| Vector | Before | After |
|---|---|---|
| round 1 `B B B B H` | `igp_isolated` | unchanged |
| round 2 `B B H H H` | `transport_blocked` | unchanged |
| round 3 `B B H H H` | `transport_blocked` | unchanged |
| **round 4 `H H H H B`** | `interface_line_down`, **exit 1** | **`no_fault_on_path`, exit 0** |
| captured `broken` `B B B B B` | `interface_line_down` | unchanged |
| `cause_not_localised` `B H H H H` | `cause_not_localised` | unchanged |

All six pinned in `tests/test_rounds_regression.py` (13 tests) — the four rounds as executable vectors, which is the form a B-427 corpus row should take.

**Exit 0 now covers two findings.** `all_layers_healthy`, and `no_fault_on_path` — the session is fine *and* something else on the device is genuinely down. The broken rungs are reported as observations in all three output formats, never as a cause. **`no_fault_on_path` must never read as "nothing found"**; a test asserts every renderer shows the broken rung.

**Read exit 0 as "not on this path", not as "all clear".** The tool still cannot say *"this device is healthy"* — that was never the question it answers.

### Four judgements from it, now standing rules (OBS-098)

Recorded where someone who never sees B-428 will read them.

**Shape 8 — a fix silently deletes coverage of behaviour that was always correct** (§0.13). Converting `test_a_healthy_rung_does_not_stop_the_walk_either` to the new finding left *"descend past a healthy rung to a broken one below and name it"* — the property round 1 depends on — with **no coverage at all**. The suite went 1377 green → 1378 green; nothing was ever red. **Not §0.13's tests face**: there the test was wrong; here it was right and got repurposed out of existence. **The tell is a test whose *inputs* had to change rather than its expectations.**

**Corpus integrity, binding on B-427.** *Scores are never rewritten after a fix; corrections are appended as new rows referencing it.* Binding rather than a judgement about round 4, because the urge to tidy a corpus is strongest exactly when a fix has just landed and the old score reads as an embarrassment rather than as evidence.

**Deleting a stated limitation asserts a capability** (§0.14). Removing *"it cannot tell you nothing is wrong"* would have read as *the limitation is gone*, which is a different claim from the one B-428 supports. Narrowed instead. **An absence of stated limits is itself a claim, and the one kind nobody reviews.**

**"X rather than Y" contains two obligations.** *"Observations rather than as a cause"* means suppress the cause **and** report the rungs. Half of it passes any `cause is None` test and silently drops a real interface fault — quieter than the defect it replaced, therefore worse.

Three of those four are about what happens *after* a defect is fixed. Every rule before §0.14 is about detection; these cover the moment immediately after detection succeeds, which had no rules and is where the pressure to tidy is highest.

---

## 2. State of the work

### Done and merged to the branch

| | |
|---|---|
| **MVP-0** | Complete. T-001–T-034 plus T-029a/b/c. **M4 reached** |
| **Review** | `MVP0-REVIEW.md`, written at M4 before any MVP-1 work |
| **Injection rounds** | **All four complete.** Scored in OBS-095, pinned in `tests/test_rounds_regression.py` |
| **B-428** | **Landed** (OBS-097). Round 4's known vector now passes its regression test (not: the class is closed — reviewer C, review §4) |

### The four rounds

| # | Fault | Predicted | Actual | Via | Answer ≥ evidence | Correct |
|---|---|---|---|---|---|---|
| 1 | IS-IS shut, PE3 uplinks | `igp_adjacency` | `igp_adjacency` | primary | ✅ | ✅ |
| 2 | transport block, RR1↔PE1 | `transport` | `transport` | primary | ✅ | ✅ |
| 3 | BGP admin-shut, PE2 | `cause_not_localised` / *`transport_blocked`* | `transport_blocked` | **refutation branch** | ❌ | ⚠️ impoverished |
| 4 | one uplink shut, IGP absorbs | `interface`, empty chain, exit 1 | **identical** | primary *(predicted failure)* | n/a | ❌ **wrong — fixed by B-428** |

**Prediction accuracy 4/4. Diagnostic accuracy 3/4 — do not report 75%.** The classes differ:

- fault **on the dependency path**: **3 / 3**
- no fault on the path: **0 / 1 at the time; the class is now closed** (B-428)

Row 4 keeps its ❌ deliberately. **A corpus records what the system did at the time, not what it does now** — one that silently rewrites its own history cannot show that a fix worked, which is most of what a corpus is for.

**Is agreement a pattern?** Provisionally yes for fault localisation — three rungs, and rounds 1 and 2 put *opposite* pressure on the walk rule (descend past a healthy rung; do not descend into healthy rungs). Three caveats keep it provisional: selection effect (rounds designed by someone who knows the ladder), every fault single (Q-019 untouched), and round 3 matched via its declared branch. **No for concluding health, and more rounds will not change that.**

### Fabric state

**PE2 has one uplink administratively shut** — round 4's fault, still applied and **not restored**. Rounds 1–3's faults were restored by the operator. Verify before any further trial; the round-4 predicate is `2 of 3 members healthy` on PE2. Note that `nettools investigate RR1 10.255.0.12` now correctly exits **0** against that state.

---

## 3. Next actions, in order

**Nothing is blocked.** The next item is a decision about sequencing, not a HALT.

1. **Restore PE2's uplink** before any further trial.
2. **B-430** — `bgp_transport` ignores `last_reset_reason`, which stated round 3's cause verbatim in the same parsed record. Small, and it is silent-failure shape 7's only known instance.
3. **B-431** — `EACH_PHYSICAL_INTERFACE` is a naming filter duplicated in three places with one differing definition, and an empty member set raises `IndexError` in the emit path.
4. **B-432** — `cause_not_localised` is unreachable for `bgp_session`. Two options recorded, neither chosen; do not resolve without the round-2 and round-3 envelopes side by side.
5. **B-433** — audit what every check reads against what its inputs contain. `bgp_neighbor` parses 23 fields and 5 are read. Should precede any new flow, since B-107's checks will be written against the same parsers.
6. Then **tracks B and C**, and the five flows only after `isis_adjacency` runs serially.

**Untouched, deliberately:** everything in 2–6 above, plus MVP-1.

## 4. What a new session most needs to know

**Read in this order:** `BUILD-PLAN.md` Part 0 (§0.9a–§0.14 are the rules this build learned — §0.13 now carries **eight** silent-failure shapes), then `MVP0-REVIEW.md`, then this file.

**The four things most likely to be got wrong by someone picking this up:**

**`descent.py` has no model call, permanently.** That is the claim the layer rests on. `test_the_model_cannot_influence_the_diagnosis` asserts the descent is byte-identical with and without an analyst.

**A failed grounding check means the report is not emitted, structurally.** `InvestigationResult.report` is `None` when grounding failed, and `GroundingFailure` has no field a claim can occupy. Do not "improve" either into a flag beside the prose.

**Exit 2 means the *answer* is untrustworthy; exit 1 means the *network* is broken.** This matches `nettools diff` and deliberately not `nettools health`, where 2 is the worst network outcome. A grounding failure is exit 2 even over a real fault — otherwise a systematic grounding regression hides in the noise of routine faults.

**Do not treat exit 1 as paging-safe.** ~~An earlier version of this line said it was.~~ Round 4's false positive (`cause: interface on PE2`, exit 1, on a session that was Established) is closed by B-428, and **one regression test on one known vector does not establish paging safety** — reviewer C, `peer-review-response.md` §4. The vector that produced it passes; the class it belongs to is uncharacterised, and B-436 has since identified a second way to reach a confident wrong answer that no current test covers. Exit 0 separately means *no fault on the path*, never *this device is healthy*.

### The one thing worth carrying to another project

> **Every defect that mattered in this build was found by evidence from outside the artefact that had it.**

Sixteen passing tests did not find the noise filter; an independently-written specification did. 1,365 passing tests did not find `_log_window`; a live run did. A human watching a device console found the harness defect the harness's own verification missed. Round 3's wasted answer was found by auditing what a check reads against what its input contains — **not** by grading its output, which was correct.

That is §0.12, §0.13 and §0.14 in one sentence, and it is why the seven silent-failure shapes are catalogued rather than merely fixed.

### The uncomfortable number

**We predicted the tool's behaviour more accurately than the tool diagnosed the network — 4/4 against 3/4.** The backlog is currently a more accurate model of this system than the code is. Round 4 is the clearest case: constructed offline, written down with its falsifiers, and reproduced exactly on live hardware long before anyone fixed it.

B-428 is the first item that closes that gap rather than widening it. It is blocked on one small decision.
