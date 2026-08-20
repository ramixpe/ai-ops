# Next level — the three tiers and the local model, read together

**2026-08-17. A proposal document, not a plan.** Nothing here is filed in the backlog;
candidates are marked and the operator picks. Companion to `design-thinking.md` (the
tier hierarchy, D1–D20) and `MCP-EXPERIMENT.md` (what LM Studio taught).

> **The model question this document treats as open (§7's "two-model station",
> and the casual "LM Studio, 4B" throughout) was settled by operator decision
> on 2026-08-19, months after this was written (B-495).** Production is
> `qwen3.8-27b`; the test arm is `gemma-4-e4b`. Every other model named below
> or elsewhere in this repo's history — `gemma-4-31b-it` chief among them — is
> no longer a live candidate. The evidence the decision rests on is not this
> document: it is `docs/build/MCP-EXPERIMENT.md` §11–§14b (kept, marked
> historical from §11 onward rather than deleted) — §12/§13 are the paired-arm
> comparison showing capability is not monotonic in model size, §14/§14b are
> `qwen3.8-27b`'s premise-refusal and diagnostic behaviour, the property that
> actually decided production. Tier I/II/III's feature proposals below are
> otherwise unaffected by this — they were never about which model, only about
> what surface a model (any model) navigates.

---

## 1. The synthesis, in one claim

The original design put every capability in a three-tier hierarchy — **commands** a human
approved exactly, **tools** with typed contracts, **flows** whose procedure a human wrote —
and let the model do nothing but *select and fill*. The LM Studio experiment then showed a
**4-billion-parameter local model selecting the right tool from a 21-tool surface**, and
naming the tool's *description* as the reason (OBS-112).

Put together, those two facts say something the industry mostly has backwards:

> **The dumber the model can afford to be, the better the architecture is.** Every
> competing AIOps tool needs a frontier model because the model carries the intelligence.
> Here the intelligence is in the flows, the checks, and the descent — deterministic,
> versioned, testable — so the model only navigates a menu. A menu can be navigated by a
> small, free, local, private model. **The architecture is what makes local viable.**

That is the thesis "next level" should serve: not more model autonomy, but a wider
enumerable surface that a small model steers and a deterministic core answers. Every idea
below is expressible as selection-and-filling; nothing needs the "Revisit if" exception.

---

## 2. Tier I — commands worth adding (cheap: additions to frozen-by-addition tables)

Each is one `PLATFORM_INTENTS`/template entry plus a parser plus a README block. The
allowlist derives itself. Ranked by what it unlocks above it.

| Command | What it unlocks | Why it matters operationally |
|---|---|---|
| **`show controllers <intf>` (optics)** ★ | **A rung *below* `interface`** — light levels, lane state | `interface line down` conflates "cable unplugged", "dead optic", and "config". Rx power answers which. This is the descent's missing physical layer, and no competitor's LLM can hallucinate a dBm reading |
| **`show cef <prefix>` / `show route <prefix> detail`** | The **forwarding plane** | Both external reviews said the same thing: "control plane healthy" is not "traffic flows". A RIB/FIB comparison rung turns `all_layers_healthy` into a claim about packets, not protocols |
| `show bfd session detail` | A liveness rung independent of BGP's FSM | Round 8 already proved BFD state matters to the descent; today it is read only by the round sampler |
| `show mpls ldp neighbor` | The `ldp_session` flow (B-109) needs it | Already on the roadmap; the command is the missing prerequisite |
| `show policy-map interface <intf>` | QoS drops | "The session is up but the app is slow" — drops in a queue are invisible to every current rung |
| `show environment` / `show redundancy` | `device_health` flow depth (B-108) | Power, temperature, RP state — the faults that announce themselves *before* a protocol notices |
| `show arp` / `show adjacency` | L2/L3 boundary evidence | The gap between "route exists" and "next hop resolves" |

★ = the one I would do first. Optics gives the ladder a sixth rung whose evidence no
model can fake and whose absence currently makes the tool's deepest answer "the line is
down" — which is where the operator's *real* work starts.

---

## 3. Tier II — tools worth adding (typed contracts, no new authority)

### 3.1 `nettools watch DEVICE SUBJECT --seconds N` ★ — productise the round samplers

**[Name since taken, 2026-08-20]** `nettools watch` shipped (W6) as a different
command — `event_watch.py`'s read-only Loki-window preview (see the corrected
note on item 2 of the candidate ranking below). The proposal in this
subsection, a repeated-descent sampler, is unbuilt and would need a different
name if the operator still wants it.

Rounds 5, 7 and 8 each hand-built the same thing: a bounded, read-only loop invoking the
descent and recording transitions. That is not test tooling — **it is the capability an
operator wants the moment `investigate` returns**: "is it flapping, or is it down-down?"

One command: run the descent (or one rung) at its natural cadence for a bounded window,
emit transitions and a rung-vector timeline, exit codes distinguishing *stable-healthy /
stable-broken / oscillating*. The samplers are the proven prototype; the epoch is the
correctness story; `--max-hold`-style bounds are already house discipline. **B-466's skew
distribution falls out for free** — every watch run measures the coherence margin.

### 3.2 `nettools compare DEVICE_A DEVICE_B [--intent X]`

Two devices in the same role should look alike; **asymmetry is signal**. A deterministic,
parsed-record diff of two *different* devices (today's diff only compares one device with
its own past). "PE1 has 2 IS-IS adjacencies and PE3 has 1 — here is the record-level
difference" is a ten-second answer to a question operators currently answer by
split-screen eyeballing. All the machinery exists: `record_key`, `volatile_fields`,
`diff_evidence`'s comparison core.

### 3.3 `nettools verify DEVICE SUBJECT --against FINDING-REF` — see §4.1; the tool half

### 3.4 MCP: a `triage` prompt

The MCP server exposes tools and two resources. It should also expose the **operator's
own decision table** as an MCP *prompt*: symptom shape → which flow → which subject
vocabulary. The LM Studio session showed the model doing this navigation well when the
descriptions carried it; a prompt makes the routing explicit instead of emergent, and it
is versioned in `prompts/` like everything else.

---

## 4. Tier III — flows worth adding (beyond the planned IS-IS/LDP/L3VPN)

### 4.1 `verify_fix` ★ — the second question every operator asks

`investigate` answers *"what is broken?"*. The operator fixes it. The very next question —
**"did my fix work?"** — is currently answered by re-running `investigate` and comparing
by eye.

The flow: take a prior finding (from the payload or the archive), re-run the **same
descent** on the same subject, and diff the rung vectors. Output: `FIXED` (cause rung now
healthy, symptom cleared), `PARTIAL` (cause cleared, symptom persists — **this is Q-019's
masked-second-fault detector, arriving as a product feature**), `UNCHANGED`, or
`DIFFERENT` (a new cause surfaced). Deterministic end to end; the rounds' regression
machinery is the prototype. This closes the operational loop investigate opened, and the
`PARTIAL` verdict is the honest multi-fault answer the descent alone cannot give.

### 4.2 `blast_radius` — the ladder walked upward

The descent walks *down*: what below explains the symptom. The same declared dependency
graph read **upward** answers: *"if I take this down for maintenance, what breaks?"* —
enumerate the sessions/flows whose ladders pass through the named object, from evidence
already collected. No model, no new commands; a different traversal of structures that
exist. Pre-maintenance checks are half of NOC change tickets.

### 4.3 `convergence` — the epoch used as an instrument

After any change: collect an epoch, wait a declared settle interval, collect again, and
report what is still moving (record-level, volatile fields excluded). "Has the network
finished converging?" is today answered by watching a prompt and feeling lucky. The
epoch's re-read machinery *is* this flow; it needs a second read and a report shape.

### 4.4 `optics_degradation` — with Tier I's controllers command

Light levels against declared thresholds, per interface, with baseline drift. The fault
class that develops over weeks and pages at 3am; a deterministic rung with numbers no
model can invent.

---

## 5. The operations loop — every piece now exists except one small listener

The event-driven design (B-201/202) has quietly become almost fully built:

```
syslog/Alertmanager ──► [webhook listener]* ──► mnemonic→flow table (B-202, a lookup)
                                                        │
                                                 nettools investigate (deterministic)
                                                        │
                                              Telegram relay (T-035 ✅ shipped)
```

\* the one missing piece — and it must **not** live in the MCP server (the no-inbound-
surface rule). A separate, small listener process whose only capability is calling
`investigate` with arguments from an enumerable table. The model appears nowhere in this
loop. **An alert arrives; ninety seconds later the phone shows the lowest broken rung
with its causal chain.** That is the demo that sells the whole architecture, and it is
one small process away.

## 6. Fleet scale — `nettools fabric investigate`

The morning-coffee NOC question is not "why is this session down" but **"which of my
sessions deserve attention?"** Enumerate every BGP session from the fabric's own evidence
(subjects from parsed records, never from a user), run the descent over each, and emit a
**matrix of rung vectors** — sixteen rows of `H H H H H` with the exceptions highlighted.
Deterministic, parallelised with the existing pool discipline, admission-controlled per
device. This is `check_fabric`'s question answered at the *flow* level instead of the
command level, and it is what makes the tool a morning ritual rather than an incident
tool.

## 7. The two-model station — where LM Studio points

The measured division of labour:

| Role | Model | Why |
|---|---|---|
| Answering | **none** — the descent | The answer must be right |
| Navigating (tool selection, follow-ups) | **small, local, free** (LM Studio, 4B) | OBS-112: selection quality came from descriptions, not model size |
| Prose (paraphrase, timeline narration) | small local, output-gated | Grounding + containment already refuse what cannot be verified |

Next-level moves along this axis: keep investing in **descriptions as the model's UX**
(measured, like §9/§10 did); finish B-113's consolidation once the re-test lands; and
treat "runs fully offline on a laptop next to the lab" as a *stated product property* —
privacy, cost and latency fall out, and no competitor with a frontier-model dependency
can follow.

## 8. What not to do, restated so growth does not erode it

No config push (verify_fix verifies, it never applies). No inbound path in the MCP
server. No flow authored at runtime. No raw device text to any model. Every addition
above is items-in-tiers — which is exactly what D-Growth promised: *adding capability
means adding items, never changing the rules about what the model may do with them.*

---

### Candidate ranking, if the operator wants a shortlist

1. **`verify_fix`** — closes the operational loop; `PARTIAL` is the masked-fault detector.
2. ~~**`nettools watch`**~~ — **the name shipped, the proposal did not.** `nettools watch`
   exists (W6), but as `event_watch.py`'s read-only dry-run preview — fetch a Loki
   window, collapse repeated lines to one root cause, print a routing decision, never
   call `investigate`. §3.1's repeated-descent sampler (one device+subject, watched at a
   cadence, with B-466's skew distribution falling out) is a different capability under
   a now-taken name; still open if the operator wants it, under a different command.
3. **Optics rung** — the descent's missing physical layer.
4. **The webhook listener** — completes an already-built loop end to end.
5. **`fabric investigate`** — the daily-ritual surface.
