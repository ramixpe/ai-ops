# MVP-0 Review

**Written 2026-08-16, at M4, before any MVP-1 work begins.** Part 7 specifies this review as the gate. It is written now rather than reconstructed later because the most valuable thing in it — why the design documents were wrong — is the part that fades first.

| | |
|---|---|
| Tasks | T-001 … T-034, plus T-029a/b/c pulled forward from the backlog |
| Commits | 106 on `feat/investigation-layer` |
| Tests | **1776 pass, 24 skipped** as of 2026-08-17 (**1377 / 22** at M4) — no network, no credentials, no API key |
| Findings | **138** as of 2026-08-17 (**97** at M4, when this review was written) |
| Backlog | 67 items |
| Investigation layer | ~5,100 lines across 9 modules |
| Frozen files | `test_safety.py`, `test_template_security.py`, `platforms.py`, `templates.py` — **byte-identical** against `6629a2c`, the commit before T-001 |

---

## 1. What the log book says as a whole

97 findings is too many to read as a list, and the list is not where the value is. Three families account for most of them, and the interesting question is not how many but **how they were found**.

### The families

| Family | Rule | Instances |
|---|---|---|
| **Silent failure** — a green thing that verifies nothing | §0.12, §0.13 | **8 shapes** (7 at M4; shape 8 added after), ~15 instances |
| **Classification** — a true statement filed as the wrong kind | §0.14 | 4 instances, all in one session |
| **Tests face** — a test agreeing with the code by construction | §0.13 | 4 instances |

By declared kind: 37 `decision-made`, 10 `risk`, 9 `surprise`, 13 `defect`/`defect-found`, 6 `assumption-wrong`, 5 `environment`, 3 `insight`, 2 `deferred`.

The `decision-made` count is the one worth pausing on. **Forty-three percent of the log book is decisions the plan did not specify.** That is not plan failure — a plan that specified them all would be the implementation — but it does say that the ratio of judgement to typing in this build was much higher than a 34-task list suggests, and that a future plan of this shape should budget for it explicitly rather than discovering it.

### The silent-failure shapes, and their polarity

**Eight as of 2026-08-17.** Seven are listed below, as they stood at M4. Shape 8 — *a fix silently deletes coverage of behaviour that was always correct* (OBS-097) — was added afterwards and is in `docs/build/PROCESS.md`, under "Shape 8" (formerly `BUILD-PLAN.md`'s Part 0; a stale line-number citation to that section was corrected here 2026-08-20 — it had drifted to point at the wrong paragraph even before the file moved). **Not merged into the table below**, because this document is the M4 review and what it did not know then is part of what it records.

| # | Shape | Found by |
|---|---|---|
| 1 | Green flag over a degraded read | incident (OBS-006, OBS-043, OBS-044) |
| 2 | Absence read as a healthy value | incident, then rule (`unevaluated`) |
| 3 | A guardrail passing over an empty set | one caught by a companion test, two by inspection |
| 4 | A rule generalised from one instance | each caught by the *next* instance arriving |
| 5 | A test agreeing with the code by construction | independent specification, live run, and once **prospectively** |
| 6 | Wrong evidence read as right evidence | re-deriving a claim during a document merge |
| 7 | **Evidence collected, parsed, carried, and never read** | **a live round, by auditing what a check reads against what its input contains** |

Shapes 1–5 are all **absence** presented as presence. Shape 6 is *presence of the wrong thing* — real, correctly read, and answering a different question than the one asked; **a property of inference from partial evidence, not of tools** (two of its four instances are human).

**Shape 7, added after this review was first written, is the one that breaks the frame.** Every other shape concerns what the evidence could not tell you. Shape 7 is the evidence telling you and nothing listening: `bgp_transport` reads `connection_state` and ignores `last_reset_reason`, which stated the cause verbatim in the same parsed record. **It is invisible to every mechanism in this build**, because all of them are aimed at output that claims *too much* and this claims too little — the output is correct. Detection is an audit of what each check reads against what its inputs contain, and it is measurable: `bgp_neighbor` parses 23 fields and 5 are read.

### Did the rate fall as the rules landed?

**No, and the question cannot be answered cleanly. What changed is how instances were found, not how often.**

Being honest about why the number is not measurable: the taxonomy was built retrospectively, the tasks are not uniform in size, and the later tasks were deliberately the judgement-dense ones. Any rate computed across that is an artefact of the denominator.

What *is* measurable is the detection mechanism, and it moved in one direction:

| Era | How instances surfaced |
|---|---|
| T-001 → T-020 | **Incident.** Something produced a wrong answer and the cause was traced back |
| T-021 → T-026 | **Companion tests.** §0.12 made vacuity checkable, and it caught the agreement test |
| T-028 → T-033 | **Independent specification.** `evidence-reduction.md`, written from the problem rather than from the code, found a defect sixteen passing tests could not |
| T-029c | **Prospectively.** A change made on principle failed three tests that had encoded the defect as correct — before any incident |

The last row is the only one that represents the rules working as intended rather than as post-mortems, and it happened once. **One prospective catch out of roughly fourteen instances is the honest score.**

So: we did not get fewer bugs of this kind. We got better at finding them, and slowly. The rules are worth having, and nothing in this log book supports a claim that they prevent the failures — only that they make them findable and give them names.

### The one thing the log book shows that no single finding does

**Every defect that mattered was found by evidence from outside the artefact that had it.** Sixteen passing tests did not find the noise filter; an independently-written specification did. 1,365 passing tests did not find `_log_window`; a live run did. Two design documents and the whole suite did not find the false positive on a working session; asking "what does correct absorption look like" did. A human watching a device console found the harness defect that the harness's own verification missed.

That is one observation with three rule-numbers attached (§0.12, §0.13, §0.14) and it is the single most transferable thing in the build.

---

## 2. Every open question

**19 raised. 13 resolved or accepted. 6 open. One turned out to be the wrong question.**

### Resolved, and load-bearing

| Q | Question | Outcome |
|---|---|---|
| **Q-006** | Does the descent's stopping rung match what an engineer concludes by hand? | **Yes.** Blind trial, hand diagnosis committed to git 12 seconds before the run. Identical: `igp_adjacency` on PE3, `igp_isolated`, interface rung healthy. **This is the validation the architecture rests on** |
| **Q-017** | The specified walk semantics make four of five findings unreachable | **A defect in the plan.** Corrected: `broken` → continue, result is the *lowest* broken rung. Without this the tool restates the alert |
| **Q-013** | Does a rung carry its own device scope? | **Yes** — `DeviceScope`, plus `SubjectRule`, which was not in the plan at all |
| **Q-005** | What does `interface_state` do when the counters are absent? | **`unevaluated`.** Generalised into a stated rule in `checks.py` |
| **Q-012** | The lab was rebuilt and is now healthy — capture against what? | Keep `t0`/`t1`, add `healthy` and `broken`. The `broken` label caught four parser defects, Q-017, and the subject-vocabulary gap |
| **Q-015 / Q-016** | §0.11 waivers for the capture windows | Granted, exercised, discharged. Fabric verified restored three ways each time |
| Q-001, Q-002, Q-003, Q-004, Q-010, Q-014 | Provider, Loki, Alertmanager, L3VPN naming, Responses API, `ttp` dependency | Resolved or accepted; none blocked MVP-0 |

### Still open

| Q | Question | Blocks |
|---|---|---|
| **Q-019** | Is "lowest broken rung" right under **two** simultaneous faults? | Nothing in MVP-0 — every corpus label is a single fault. **Only injection answers it** |
| **Q-011** | Should the devices' trap level change so severity-5 reaches Loki? | Not MVP-0. Yes for a useful historical axis |
| **Q-009** | Should the MiniMax key and lab credentials be rotated? | Nothing — but they were pasted into a transcript no control here can revoke. **Recommended** |
| **Q-010** | Confirm the Responses-API route before the MVP-1 gate is built on it | The MVP-1 gate |
| **Q-007 / Q-008** | Telegram or Mattermost; which host has egress | T-035 only, which is optional |

### The wrong question

**Q-006 as originally framed** — *"does the stopping rung match what an engineer would conclude **from the same fixtures**?"* Answering it that way would have compared two readings of a corpus the code was written against. It would have agreed, and the agreement would have meant nothing.

The version that was answered is a different question: a **live fault the agent had never seen, chosen so its symptom is identical to a captured fault with a different cause**, with the hand diagnosis committed before the run. The reframing came from the operator and it is the difference between a demonstration and a test.

---

## 3. What the build changed about the design

**Six design decisions were wrong and measurement corrected them.** This is the list, and it is the argument.

| # | The design said | Measurement said | Cost of not measuring |
|---|---|---|---|
| 1 | **Walk semantics** (D6, T-024): `broken` → stop | Rungs 1–3 are *all* broken; stopping reports `peer_not_established` — where the investigation started. **Four of five declared findings unreachable** | The tool would restate alerts and be called working |
| 2 | **Device scope** (Q-013): rungs run on the local device | RR1's own IS-IS was healthy while the far end had zero adjacencies. A rung must run where the *subject* lives | Every cross-device fault read as healthy |
| 3 | **Subject vocabulary**: one subject per descent | The ladder crosses four vocabularies — peer address, host prefix, device, each physical interface. `SubjectRule` did not exist in the plan | The walker returned `undetermined` on both labels |
| 4 | **Noise filtering** (`evidence-reduction.md` §3.2): "filter by source, not by content" — implemented as a facility drop | Eight severity-3 records deleted unattributed, one an interactive session dying 22.7 s before the incident | A noise filter that silently deletes the tool's own damage |
| 5 | **Historical projection** (§3.5): project to subject, as on the config axis | Retains **0 of 28** records. The events that explain a subject are the ones that do not name it | "No correlating events" about an incident whose full timeline was in the buffer |
| 6 | **Source hierarchy** (§9): Loki is the real source, `show logging` the fallback | Every causal event is severity 5/6 and never reaches Loki. The platform returns two real events **from the previous day's incident** | A plausible, non-empty, confidently wrong timeline |

Plus one the design never considered at all: **forward consistency** (B-428). The descent has no mechanism for concluding *health* — it exists to find the lowest broken thing, and one uplink down on a redundant device produces `cause: interface` with an empty causal chain on a session that is Established and carrying traffic.

### What this list argues

Two of these (1, 3) were caught by the `broken` fixture label. Two (4, 5, 6) by measuring a document's claims against captured output. One (2) by a fixture whose far end disagreed with its near end. **None was caught by review, and all six were written by people reasoning carefully about protocols they understand.**

> **Build against captured reality, not against a specification — and when a specification arrives for code that already exists, read the specification against the code. The other direction finds nothing, because every line of code justifies itself.**

The corollary is the more uncomfortable one. The design documents were not sloppy; they were *good*, and they were wrong in six places. A build that had trusted them would have shipped six defects, every one of which produces a plausible answer.

---

## 4. What we now know we do not know

Listed as questions rather than risks, because each has a specific experiment attached.

**Can the descent conclude health at all?** ~~No.~~ **Partly, as of B-428 (OBS-097).** It can now conclude *"no fault on the path between these endpoints"* and exit 0, which is the question it was always answering. It still cannot conclude *"this device is healthy"*, and should not be read as doing so. The measurement that forced this (OBS-094): Round 4 reproduced all nine predicted values on live hardware, no falsifier firing: BGP Established and carrying traffic, `cause: interface on PE2`, empty causal chain, `trustworthy: true`, **exit code 1**. The failure is louder than predicted — the system does not merely fail to conclude health, it **asserts a fault and marks the assertion trustworthy.** B-428 is no longer a hypothesis; it is a defect with a demonstrated reproduction and a known-sufficient information source.

**Is the descent's behaviour under two faults acceptable?** (Q-019) A single interface fault and an interface fault plus a BGP shut produce **byte-identical rung tables**. The masking is structural. Two candidate signals — a second unexplained commit in the timeline, and forward consistency — neither validated, and the corpus contains no two-fault capture.

**Is Q-006's agreement a pattern or a data point?** ~~One match, on one rung.~~ **Answered 2026-08-16 by four rounds (OBS-095), and the answer is narrower than it was first written.** ~~"Provisionally a pattern."~~ **The tool succeeded on these three discriminating cases** — three different rungs, with rounds 1 and 2 exercising the walk rule in opposite directions. That is what was observed and no inference beyond it is supported: the cases were designed by someone who knows the ladder and were not sampled from any defined population (reviewer C, `peer-review-response.md` §3.5, §4). Three caveats keep it provisional: the rounds were designed by someone who knows the ladder (selection effect), every fault was single (Q-019 untouched), and round 3 matched via its declared refutation branch with an answer weaker than the evidence supported. **Not a pattern for concluding health — 0 of 1, and structurally 0 of *n* until B-428.** ~~Do not report the combined 3/4; the denominator hides a class failure.~~ **Superseded 2026-08-17 by B-441 / reviewer C §3.5, which is at §7 of this document.** Report all three strata — 3/3 on-path, 0/1 no-fault, **3/4 overall** — and state that none of them estimates field accuracy, because the cases were not sampled from a defined population. C's correction against me: *"deployment performance necessarily combines categories; the appropriate combination depends on their production prevalence, which is currently unknown."* **The problem was never the denominator. It is the sampling frame.**

**Does the model layer degrade gracefully under a model that is worse, or busier, or changed?** One live run, one provider, one prompt version. ~~The grounding gate refused a fabricated timestamp, which is evidence that the gate works.~~ **Corrected 2026-08-17 — this was backwards.** The gate did **not** refuse it. The report path's citations were checked; the correlation path had **no citation check at all**, so the fabricated timestamp was emitted unverified (OBS-085, `FINDINGS.md`). The hole was closed afterwards by `grounding.check_timeline_citations`, and the exact line is pinned as a regression.

So what the trial is evidence for is **discovery** — one live run found a hole that 1,365 passing tests and two design documents did not. It is not evidence that the gate works, because on this claim there was no gate. And nothing measures how often the model produces something the gate must catch.

**Why this one is worth flagging rather than quietly fixing.** It inverted a finding into its opposite and made the system look better tested than it was, in the document that serves as the M4 gate — the same failure `peer-review-response.md` §4 catalogued. Two of that section's corrections were applied to this file (§4, §5) and this sentence was not, because nobody was looking here. **A correction pass that fixes the claims it was handed does not find the ones it was not** (OBS-137).

**What does this cost per investigation?** ~~Not instrumented (B-425). ~5k tokens of
prompt is a character-count proxy, response excluded. A number this project will be
asked for and cannot currently give.~~ **B-425 shipped (checked 2026-08-20).**
`TokenUsage`/`Completion` now carry real provider-reported usage through
`complete_prompt`, into `InvestigationResult` and the payload. Measured live: **1 call,
2,286 tokens (1,840 in / 446 out)** for a healthy investigation — about half the
character-count proxy this review originally cited, which is itself the argument for
measuring rather than estimating. `reported=False` distinguishes a provider that did
not surface usage from a call that genuinely cost nothing.

**Does any of this hold on a second vendor?** `cisco_iosxe` and `juniper_junos` are declared from documentation with no device to test against. The abstraction is honest in shape and unverified in fact.

**Does it hold at fabric scale?** Nine devices. The tool surface and flow count are device-count independent by construction (B-409), which is a claim, not a measurement.

---

## 5. What MVP-0 can and cannot do

For an operations engineer deciding whether to point this at something.

### What it does

**Given a BGP session that is down, it tells you which layer is broken and shows its work.** Not "BGP is down" — that is where you started. It walks session → transport → route → IGP → interface, on the right devices, and reports the *lowest* broken layer plus every broken layer above it as a chain you can check link by link.

**No model reaches that answer.** The descent is parse-and-compare. Turn the model off entirely (`--no-model`) and you still get the diagnosis; the model only writes it up and places it on a timeline.

**It is designed to refuse rather than guess, and that is an intention, not a measured distribution** (reviewer C, `peer-review-response.md` §4). A layer it could not read ends the walk with `undetermined` and no cause named. **One of four blind trials nonetheless produced a confident false positive rather than a refusal**, so the design holds in the cases tested and the rate at which it holds is unknown. A written report whose claims do not cite evidence the descent actually read is **not emitted** — you get the descent and the reason, never the prose.

**You can verify all of that in ten seconds with no lab**: `nettools investigate RR1 10.255.0.12 --from-fixtures --format table`.

**Exit codes distinguish a broken network from a broken answer.** `1` is a fault. `2` is "no trustworthy answer" — and never the two conflated.

### What it does not do

**One flow.** `bgp_session` only. Not IS-IS, LDP, L3VPN, interfaces as a subject, or device health as an entry point. Ask it about anything else and there is no ladder to walk.

**One vendor verified.** IOS-XR. The other two are unverified command strings.

**~~It cannot tell you nothing is wrong.~~ Corrected 2026-08-16 — B-428 has landed.**

This was the sharpest limitation in the review and it no longer holds. Round 4 measured it on live hardware: one down interface on a device with redundancy produced `cause: interface on PE2`, `trustworthy: true` and **exit code 1** on a BGP session that was Established and carrying traffic. The descent now checks, before emitting, whether the cause accounts for the symptom the investigation started from — **if the first rung is healthy there is no symptom, so nothing beneath it can be a cause.** That case reports `no_fault_on_path` and exits **0**, with the broken rungs recorded as observations.

The round-4 vector is pinned as a regression, and rounds 1–3 are pinned unchanged beside it (`tests/test_rounds_regression.py`).

**What replaces the warning, because it is narrower rather than gone.** The tool can now say "no fault on the path between these two endpoints, and here is what else is broken". It still cannot say "this device is healthy" — that was never the question it answers, and `no_fault_on_path` is explicitly *not* that claim. **Read exit 0 as "not on this path", not as "all clear".**

Worth knowing if you read the reports rather than the exit codes: before the fix, the model's prose already caught it — *"because higher layers are healthy, the broken interface state observed here is not on the dependency path"* — and recommended clarifying scope. It was right, in the one layer this architecture deliberately treats as non-authoritative. That is what made B-428 a predicate rather than an evidence-gathering exercise: **the information was already sufficient, and something reading only the descent payload reached the right answer first time.**

**It cannot see a second fault.** If two things are broken it reports the lower one, correctly and incompletely, and the output is indistinguishable from the single-fault case. Fix what it names, and the session may still be down.

**It does not act.** Read-only by construction — there is no configuration path, no shell, and no generic command tool. It will not fix anything and cannot be made to.

**Scope that claim to the right path** (reviewer A, §4). The defensible form is:

> *"The model cannot alter device configuration, and **one investigation path** localises a finding using deterministic predicates."*

`nettools investigate` is that path. **`nettools agent` is a separate, explicitly untrusted path** — a bounded tool-calling loop where the model chooses what to call — and it must not inherit the same trust language. Both are read-only; only one is deterministic. And the read-only guarantee itself had a hole until B-438: `pin_lab_golden_snapshot` performed a persistent write from behind a decorator named `_read_only_tool` (§3.1).

**It does not know history.** No baselines, no "has this happened before", no rarity. A flap and a first-ever transition look the same.

**Its timelines are bounded by what a device buffer holds.** 200 of 684 records on the measured run, and it says so — a negative over incomplete coverage is reported as `unevaluated`, never as "nothing happened".

**It succeeded on three discriminating blind cases and failed the fourth** (OBS-095). The fourth was a **false positive on a working session**, predicted in advance, reproduced exactly, and its **known vector** now passes a regression test (B-428) — which is not the same as the class being closed (reviewer C, §4).

Three strata, reported together and none of them an estimate of field accuracy, **because the cases were not sampled from a defined population** (§3.5): **3/3** on-path, **0/1** no-fault, **3/4** overall. No round has yet tested a fault chosen without reference to the ladder, and none has run while the network was still changing — which reviewer A, B and C independently identify as both the dominant production condition and the one the trial protocol excludes by construction (§2.1, and `docs/build/PROCESS.md` §0.15).

### The honest summary

> MVP-0 answers *"which layer is broken, and what is the evidence"* for one protocol on one vendor, deterministically, and refuses when it cannot. It does not yet answer *"is anything actually wrong"*, and it should not be trusted to tell you that nothing is.

---

## 6. What comes next, and what does not

**Next: the four injection rounds** (B-426 sequencing). Not MVP-1. One match is one data point, and rounds 2–4 land on different rungs — including round 4, whose adverse outcome is already predicted and recorded.

**Before or alongside them: B-428.** Round 4 is designed to hit it, and it is the difference between "cannot conclude health" being a known limitation and being a defect that reaches an operator.

**Not yet:** the reasoning gate (B-101), more flows (B-107–B-111), the config axis (B-104). All of them assume the descent is trustworthy, and the four rounds are how that is established rather than asserted.

`T-035` (report relay) remains optional and does not gate anything. Q-007 and Q-008 must be answered before it is built, not during.
