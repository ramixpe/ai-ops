# Design Thinking — A Deterministic AI Agent for Network Operations

**Project:** read-only troubleshooting agent for the `sota-xrd` Cisco IOS-XR fabric
**Status:** foundation design, theory complete, pre-build
**Companion documents:** Chapter 1 deck (theory), LLD (pending repo review)

---

## How to read this document

This is not a specification. It is the reasoning behind one, written so that a future reader — including us in six months — can see not just what was chosen but what was rejected and why.

Every decision follows the same shape:

| Field | Meaning |
|---|---|
| **Question** | The fork in the road |
| **Options** | What was genuinely on the table |
| **Decision** | What we chose |
| **Why** | The reasoning, including what we gave up |
| **Growth** | What happens to this decision as the system scales |
| **Revisit if** | The observation that would make us reopen it |

A decision without a *Revisit if* is either trivial or dishonest. Every one here has one.

---

## The organising principle

Everything below is an application of a single sentence:

> **The model never authors anything. It only selects from — and fills into — structures a human approved in advance, so the agent's entire action space is enumerable, testable, and auditable before the model is ever invoked.**

This comes from an operations background and a specific fear: that a probabilistic system ends up reading, controlling, or deciding on a production network. The design does not manage that risk with prompts or with model quality. It manages it structurally, by making the unsafe action *unrepresentable*.

The second sentence, which is the justification rather than the rule:

> **Pattern recognition is not the same thing as deterministic truth.**

A language model can recognise structure, extract values, summarise meaning, and explain what something *probably* represents. Every decision in this document is an answer to the word *probably*.

---

# Part 1 — Framing

## D1. Is an agent the right tool at all?

**Question.** We already have automation, monitoring, scripts, and a substantial existing codebase. What, if anything, actually needs an agent?

**Options.**
1. Agent-first — put an LLM in front of everything, treat it as an upgrade to automation.
2. Agent-never — stay with deterministic automation, treat LLMs as a fashion.
3. Agent for a narrow class — identify the workflows where the *next step depends on what you find*, and use an agent only there.

**Decision.** Option 3.

**Why.** Traditional automation is excellent when the workflow is known in advance. It struggles precisely when the next step depends on changing context. That is the only place an agent earns its cost. Checking a device responds every minute is monitoring. Archiving configs nightly is automation. Enforcing a known policy is a deterministic check. Approving a production change is a human. If a basic script solves the problem reliably, the script wins — and saying so protects the credibility of the cases where an agent genuinely helps.

Concretely, the agent-shaped work in this fabric is *correlating evidence across the P / PE / RR layers when a symptom has no obvious owner*: a CE reporting loss, where the question is whether the cause is the PE–CE link, the PE's session to the route reflector, an IGP adjacency in the core, or a flapping link on the P2↔P3 diagonal.

**Growth.** The filter stays the same as scope grows. Each new candidate workflow gets one question: does the next step depend on what we find? If no, it is automation and should be built as automation.

**Revisit if.** We find ourselves adding agent capability to workflows that a `for` loop would handle. That is the signal the filter has stopped being applied.

---

## D2. What is the model allowed to do?

**Question.** Where exactly is the boundary between model judgement and system behaviour?

**Options.**
1. **Prompt-enforced.** Tell the model what not to do; rely on instruction-following.
2. **Review-enforced.** Let the model propose anything; a human approves every action.
3. **Structurally enforced.** Make the unsafe action unrepresentable — the model can only choose from, and fill into, structures that already exist.

**Decision.** Option 3, with option 2 retained as an additional gate on any write path.

**Why.** Prompt enforcement fails for a reason that is not about model quality: negative instructions are unreliable, and naming a forbidden thing raises its salience. Review enforcement is real but does not scale and degrades under alert fatigue — a human approving their fortieth proposal of the night is not a control.

Structural enforcement is the only one whose guarantee does not depend on anyone's attention. If the model's output space is a closed set, the safety property can be *tested* rather than *trusted*. This is the same move made earlier on the configuration-generation track, where the conclusion was that the model should emit a typed intent object and a deterministic renderer should produce the CLI. Here it is applied to actions rather than syntax.

The practical form is a three-tier composition hierarchy:

| Layer | What a human pre-approves | What the model may do |
|---|---|---|
| **III — Flows** | The ordered procedure, its preconditions, its rollback plan | Choose which approved flow applies. Never author one. |
| **II — Tools** | The tool contract: arguments, allowed scope, result envelope | Choose the tool and supply typed arguments. |
| **I — Commands** | The exact command string and the parse template for its output | Choose the command and fill declared slots only. |

Each tier composes only items already approved beneath it. Nothing new can be created at runtime, at any tier.

**Growth.** The hierarchy is what makes growth cheap: adding capability means adding *items*, never changing the rules about what the model may do with them.

**Revisit if.** We find a genuinely valuable capability that cannot be expressed as selection-and-filling. That would be evidence the model is too constrained for the problem — and would need an explicit, argued exception, not a quiet loosening.

---

## D3. Maturity ladder

**Question.** Everything at once, or staged? And if staged, what is the stopping point?

**Options.**
1. Build the full vision, ship when complete.
2. Stage by capability, each stage earning the next.
3. Stage by risk, starting with the highest-value use case regardless of blast radius.

**Decision.** Option 2, strictly sequential, three stages, with a hard ceiling.

| Stage | Shape | Trigger | Ceiling |
|---|---|---|---|
| **1** | Read-only Q&A | A human asks | The agent answers with evidence. Nothing else. |
| **2** | Event-driven RCA | Syslog / SNMP wakes it | It runs the checks and reports. It does not act. |
| **3** | Standard procedures | Either | Pre-approved actions only — bounce an interface, shut a flapping link — then hand to a human. |

**Why.** Before we let an agent change anything, we have to trust how it observes. Stage 1 is where that trust is built, and it costs almost nothing to constrain: every early use case — alert triage, output parsing, troubleshooting support, documentation — is read-only anyway. Stage 2 is where the boundaries stop being backed by a human's presence, which is a genuine category change and deserves its own bar. Stage 3 exists to cut MTTR on known-shape problems, not to remove humans.

The ceiling matters as much as the ladder. There is no Stage 4. The agent does not diagnose novel failures and act on them; when the descent bottoms out, it reports and hands over.

**Growth.** Each stage is additive to the one below, not a replacement. Stage 2 reuses Stage 1's entire evidence pipeline; the only new machinery is triggering, deduplication, and delivery. Stage 3 adds a proposal path and an approval gate — it does not touch the read path.

**Revisit if.** Stage 1 turns out to be so reliable that Stage 2 feels like overhead — in which case the ladder was right and we accelerate. Or Stage 1 produces confident-but-wrong answers, in which case we do not proceed at all until that is understood.

---

# Part 2 — Architecture

## D4. What sits on top: agent loop or workflow engine?

**Question.** Does the agent orchestrate the flows, or does a workflow engine (n8n) orchestrate the agent?

**Options.**
1. **n8n on top.** Deterministic graph, versioned, replayable. Free scheduling, retries, queues, credential store, visual run history, native per-execution audit.
2. **Agent loop on top.** Adaptive. Selects tools and flows as evidence develops.
3. **Split by path.**

**Decision.** Option 3 — and this is the resolution of what initially looked like a straight trade-off.

| | Agent loop on top | n8n on top |
|---|---|---|
| Control flow | Model-influenced | Deterministic |
| Adaptive investigation | Native | Every branch pre-enumerated |
| Reproducibility | Same trigger may vary | Byte-identical replay |
| Infrastructure | Build it yourself | Native |
| Open-ended chat | Natural | Awkward |
| Event triggers | Build the listener | Home turf |

**Why.** Read against the organising principle, n8n looks like it should win outright. It cannot, because a fully deterministic graph *is* an automation script, and D1 already established where those fail: when the next step depends on what you find.

The resolution is that these are not competing for the same position:

- **Diagnosis is adaptive.** The next check genuinely depends on the last result. Agent loop on top.
- **Action is not adaptive.** Bouncing an interface is a known procedure with known preconditions and rollback. Deterministic flow on top; the agent selects it and fills parameters.

Which gives the rule that follows directly from D2: **the model may compose an investigation; it must never compose an action.**

**Growth.** Stage 1 needs no n8n at all — there is nothing to schedule when a human is asking a question. n8n earns its place at Stage 2 as the *envelope around* a run: catch the syslog, deduplicate, rate-limit, invoke the agent, deliver the result to Slack or a ticket. It never becomes something the agent calls.

**Revisit if.** At Stage 2, firing the same event repeatedly produces high variance in the agent's tool sequence. That would mean the loop is not safe on top and investigations need to be constrained into fixed flows sooner than planned. This is a cheap experiment and should be run before Stage 2 ships.

---

## D5. How are flows indexed?

**Question.** As the flow library grows, how does the model find the right one? This is the question that nearly derailed the design.

**Options.**
1. **Flat list in the prompt.** Works to perhaps a dozen; degrades silently after, selecting something plausible and wrong.
2. **Vector retrieval over flow descriptions.** Scales, but puts a probabilistic selector in the one place determinism matters most.
3. **Hierarchical classification.** Classify domain first, then select within it. Two easy choices instead of one hard one.
4. **Restructure so the problem does not exist.**

**Decision.** Option 4, with option 3 held in reserve.

**Why.** The worry was hundreds of flows. But hundreds of flows is a symptom of indexing by *symptom*. BGP-down, BGP-flapping, BGP-wrong-prefix-count and BGP-peer-unreachable are not four flows — they are one flow with four terminal findings.

Index by **object type** instead:

`interface` · `IGP adjacency` · `BGP session` · `LDP / MPLS` · `L3VPN service` · `device health` · `topology`

That is seven for this fabric. Seven fits in a prompt with room to spare, and the selection problem evaporates without embeddings, retrieval, or hierarchy. Symptoms become findings *inside* a flow.

There is a useful test for any proposed flow: **is this an object, or a symptom of one?** If it is a symptom, it belongs inside an existing flow as a terminal finding.

**Growth.** Object types grow slowly and roughly with protocol surface, not with failure modes. Adding EVPN or SRv6 adds one object type each. Even a substantially larger estate is unlikely to exceed fifteen. If it ever does, hierarchical classification (option 3) is a drop-in: classify domain, then select within domain, without changing any flow definition.

**Revisit if.** Flow count passes about fifteen, or two flows for the same object type appear and cannot be merged.

---

## D6. What happens inside a flow?

**Question.** Given a flow has been selected, what is the shape of the investigation?

**Options.**
1. **Free agent loop.** Iterate until the model is satisfied. Maximally adaptive, unbounded, non-reproducible.
2. **Fixed two-pass funnel.** Wide summary → reason → narrow detail → reason → evidence. Bounded, but assumes the answer is always one level deeper in the same protocol.
3. **Deterministic dependency descent.** Walk down the protocol stack, stop at the first broken layer.
4. **Descent with a reasoning gate.**

**Decision.** Option 4 — the descent is the backbone; the funnel's gate survives as the mechanism for narrowing *within* a layer.

**Why.** The two-pass funnel was the original design and it is correct about one thing: investigation should go wide before it goes narrow, and it should have a bounded number of reasoning checkpoints rather than an open loop.

But the real troubleshooting sequence for a BGP session — confirm the session state, check the route to the peer, check TCP reachability, check the IGP adjacency, check the interface, read the logs — is not wide-then-narrow. It is a **descent down the protocol dependency stack**. BGP sits on TCP, which sits on a route to the peer, which sits on the IGP, which sits on an interface, which sits on physical.

Two consequences follow, and both are significant.

**First: the dependency graph does not need designing.** It is the protocol layering, which is already known, stable, and vendor-independent. Each protocol declares what it stands on. This is why the cross-flow escalation graph proposed earlier was removed — it was reinventing something that already exists, at the wrong level of abstraction. The ladder belongs *inside* a flow definition, not between flows.

**Second: the descent has a natural stopping rule.** The lowest broken layer is the root cause, almost by definition. You do not run all the checks; you descend while each layer is healthy and stop at the first that is not.

And the observation that changes where the model sits: **every check in that descent is deterministic.** Is the state `Established`? Is the prefix in the RIB? Is the adjacency `Up`? Is line protocol up and are error counters under threshold? Parse and compare. No model required, anywhere in the descent.

So the model's real contribution is not diagnosis. It is:

- **correlating the finding with the log timeline** — the descent says the interface is down; the logs say when, how many times, and whether it coincided with a commit. That is the difference between a finding and a root cause;
- **separating cause from symptom** when several layers look unhealthy simultaneously;
- **writing the RCA** so an engineer absorbs it in ten seconds;
- **the hard residue** — everything checks healthy and the session is still down. Policy, MTU, authentication, intermittent. This is where reasoning is genuinely needed, and where the correct output is often "I cannot determine this from the available evidence."

**Growth.** Adding a protocol means declaring what it depends on and writing its layer checks. The descent machinery is written once. Notably, the interface layer is shared by every protocol above it — the checks compound rather than multiply.

**Revisit if.** A significant share of real cases end with the descent finding everything healthy. That would mean the ladders are checking the wrong things, and the answer is better checks, not more model.

---

## D7. The reasoning gate

**Question.** When the model is consulted mid-flow, how does it signal what it wants, and what stops it inventing a target?

**Options.**
1. **Free text.** "I think we should look at GigabitEthernet0/0/0/1 next." Parsed by regex or by another model call.
2. **Typed decision object**, validated against a schema.
3. **Typed decision object, validated against a runtime-enumerated set.**

**Decision.** Option 3.

**Why.** The model returns exactly one of two shapes:

```
{ "decision": "sufficient", "finding": <enum>, "object": <id> }
{ "decision": "narrow",     "target": { "type": <enum>, "id": <id> } }
```

Nothing else parses. That is D2 applied to control flow.

The important part is the second half. Code enumerates the candidate objects out of the validated wide-pass envelopes — the interfaces that actually appeared in the brief, the neighbours that actually appeared in the summary — and the target is checked against that set. The model cannot ask about an interface that was never observed.

This is what keeps "the model chooses the object" from quietly becoming "the model invents an object." The choice is still from a pre-approved list; the list is simply computed at runtime by deterministic code rather than written in advance. **Enumerability is preserved without being static.**

**Bias the gate toward narrowing.** "I have enough" is the dangerous claim — it is precisely the confident summary that misses something important. So make it the harder claim to make: to return `sufficient`, the model must name the finding and the object, and a deterministic checker confirms that finding is consistent with the wide-pass data. Fails the check, or ambiguous, and it is forced to narrow.

The costs are asymmetric. An unnecessary narrow pass costs a few seconds. A premature conclusion costs the team's trust, which is spent once.

**Growth.** The decision schema is fixed. New finding types are new enum values. New object types come from D5 and are already enumerated.

**Revisit if.** The gate returns `narrow` on nearly everything — meaning the sufficiency check is too strict and the wide pass is under-informative — or the deterministic consistency check turns out to be trivially satisfiable, meaning it is not a real gate.

---

## D8. The evidence model

**Question.** What counts as evidence, and where does it come from?

**Options.**
1. **Observed state only.** Whatever the `show` commands return.
2. **Observed plus intended.** Compare against configuration.
3. **Observed, intended, and historical.**

**Decision.** Option 3, with a possible fourth axis deferred (see D14).

**Why.** Observed state alone tells you what *is*, which is rarely enough to say what is *wrong*. Comparing against intent — the running configuration, or better, the source of truth — tells you something is wrong. But the timeline is usually what identifies the cause: an interface that has been down for three weeks and an interface that went down four minutes after a commit are the same observation and completely different incidents.

| Axis | Source | Answers |
|---|---|---|
| **Intended** | `show run`, golden config, Nautobot | What should this be? |
| **Observed** | Status output, wide and narrow passes | What is it? |
| **Historical** | Logs, bounded window, filtered to the object | When did it change, and how often? |

RCA is the reconciliation of the three. Configuration is the axis that will not fit naively, and D16 governs how it is bounded. Logs are collected in the wide pass scoped to the device, then re-filtered to the object in the narrow pass — so narrowing sharpens the timeline as well as the state.

**Growth.** Additional sources (telemetry, streaming counters, change records) slot in as further axes without disturbing the three. Each new axis needs a retrieval tool, an envelope, and a place in the grounding check — nothing else.

**Revisit if.** Log volume makes the historical axis unusable at Stage 2 — in which case the answer is a log-processing pipeline upstream, not dropping the axis.

---

# Part 3 — Tool surface

## D9. Vendor decomposition

**Question.** With Cisco today and Juniper later, how do vendor differences get expressed in the tool surface? One BGP tool for both? One tool set per vendor? Per vendor per domain?

**Options.**
1. **Server or tool per vendor** — `cisco-mcp`, `juniper-mcp`.
2. **Per vendor per domain** — `cisco-routing`, `juniper-routing`.
3. **Vendor-agnostic, resolved internally** — one tool per capability; the server resolves platform from inventory and dispatches.

**Decision.** Option 3, without qualification. This is the single most consequential decision in the document.

**Why.** If the model must choose between a Cisco tool and a Juniper tool, then the model must know that PE2 is IOS-XR. Vendor identification has just become a model judgement — on the exact axis where its pretraining is most contaminated. The earlier configuration-generation work established that IOS/IOS-XE dominance is a *pretraining prior*, not a knowledge gap. Making vendor a visible choice re-admits that failure mode through the tool surface instead of the token stream.

Stated as a principle: **vendor differences terminate at the lowest possible layer. If a vendor difference is visible to the model, the architecture has leaked.**

Option 2 is worse than option 1 — the model chooses vendor *and* domain, and the manifest grows as vendors × domains.

Vendor is a property of the device, resolved from inventory by code, before any command is chosen.

**Growth.** This is what makes adding Juniper cheap. See Part 6.

**Revisit if.** Nothing plausible. A vendor difference that genuinely cannot be normalised is a signal to add a capability flag to the normalised schema, not to expose vendor upward.

---

## D10. Tool granularity

**Question.** One tool per command? Per domain? Something else?

**Options.**
1. **One tool per command** — `get_bgp_summary`, `get_isis_adjacency`, `get_interfaces_brief`…
2. **One tool per domain** — `bgp_tools`, `isis_tools`…
3. **Tools shaped by investigation stage, parameterised by a closed domain enum.**

**Decision.** Option 3.

| Tool | Purpose |
|---|---|
| `get_intent(scope, domain, object_id?)` | Intended state — configuration section or source of truth |
| `get_status_summary(scope, domain)` | Wide pass |
| `get_status_detail(scope, domain, object_id)` | Narrow pass |
| `get_logs(scope, window, object_id?)` | Historical axis |
| `get_topology(scope)` | Relationships, multi-device flows |

**Why.** Under option 1, the manifest grows with every command added, and every tool description consumes context. That is context rot arriving through the tool manifest — the same degradation identified in the earlier context-engineering work, in a different costume.

Five tools. That number does not change when Juniper is added. It does not change when LDP, MPLS or EVPN are added — those are values in the `domain` enum. The model's selection burden stays constant while the estate grows.

**Is this a generic command runner in disguise?** No, and the distinction is worth stating precisely because it superficially resembles one. `run_command(device, command)` has an infinite argument space and moves the safety decision to *runtime*. Here `domain` is a closed enum, and each `(domain, platform, stage)` triple maps to a fixed, human-authored command list plus its parse template. The argument space is finite and enumerable *before the model runs*. That is the whole test, and this passes it.

A useful side effect: the model never states topology relationships. It names a scope and code resolves the real attachment. In this fabric, where CE numbering is deliberately crossed — CE2 attaches to PE3, CE3 to PE2 — a name-pattern-matching model gets two of four services wrong. Scope resolution removes the opportunity.

**Growth.** New domains are enum values. New stages would be new tools, but the stage set is stable because it comes from D6.

**Revisit if.** A domain needs an argument the five signatures cannot carry. Extend the signature; do not add a sixth tool without arguing it.

---

## D11. What becomes a tool at all?

**Question.** Where is the line between a tool and a function?

**Decision.** One rule: **something becomes a tool only if the model must decide when to call it. Everything else is library code.**

**Why.** Inventory resolution, scope expansion, dialect gating, template rendering, envelope validation, grounding checks, flow definitions — the model never decides when those run. They are internal functions. This rule is what keeps the manifest at five and is the main defence against surface creep.

**Growth.** Apply the rule to every proposed addition. Most candidates fail it.

**Revisit if.** The model repeatedly needs information that only exists inside library code — which usually means a *tool return value* is under-populated, not that a new tool is needed.

---

## D12. MCP — whether, when, and what it may carry

**Question.** Do we need MCP, how do we split servers, and can n8n flows sit behind it?

**Options.**
1. MCP for everything, from day one.
2. MCP for nothing; plain function calls.
3. Design the contract now, adopt MCP when a second consumer exists.

**Decision.** Option 3, with a hard constraint on what MCP may ever expose.

**Why.** MCP is a distribution and contract mechanism, not an architectural necessity. It earns its place when a *second consumer* appears — a desktop client, a colleague's IDE, another agent — or when process isolation with separate credentials is wanted. For a single-purpose agent where the flow definition names which collector runs at which stage, those collectors can be plain Python.

The book's own advice applies to our own architecture: if a plain function solves it reliably, use the function.

Two separate questions were being conflated, and separating them dissolves most of the difficulty:

- **What tool surface does the model see?** A model-behaviour question. Answered by D9, D10, D11.
- **How many server processes are deployed?** An operations question about credentials and blast radius, answered against the same tool names, later, as a refactor.

**The constraint.** Anything behind MCP is callable by the model. Therefore **MCP exposes read tools and proposal tools only.**

At Stage 3, a procedure may be exposed as `propose_procedure(name, params)` returning `{proposal_id, plan, preconditions, rollback, status: "awaiting_approval"}` — never `{status: "done"}`. Execution happens outside the model's reach, triggered by a human or a deterministic policy engine reading the approved proposal. The model can write the request; it cannot pull the trigger. That is D2's "never compose an action" enforced at the transport boundary rather than by prompt discipline.

**Are n8n flows behind MCP?** For the read path, no — n8n is upstream of the agent, and things upstream do not belong on a surface the agent calls. For the write path, only in proposal form, per the constraint above.

The resulting picture is three surfaces:

| Surface | Visibility | Contents |
|---|---|---|
| **MCP tools** | Model may call | Five tools. Read and propose only. |
| **Internal library** | Model never sees | Inventory, scope resolution, dialect gate, parse templates, envelope validation, grounding, flow definitions. |
| **Orchestration** | Out of model reach | n8n triggers, deduplication, notification, ticketing. Write execution after approval. |

**Growth.** Adopting MCP later is mechanical because the contract already exists. Splitting one server into several is a deployment change against unchanged tool names.

**Revisit if.** A second consumer appears — that is the trigger to adopt, not a date.

---

## D13. Passive reads versus active probes — **open**

**Question.** Are `ping` and a TCP port check the same class of operation as `show interfaces brief`?

**Position (not yet locked).** They are both non-mutating, but an active probe *generates traffic from the device*. That is a different risk profile: at Stage 2 an event storm could have the agent probing hundreds of times unprompted, with no human in the loop.

The proposal is a separate allowlist class with its own budget and its own audit line. Passive reads unlimited; active probes rate-limited per device and per run.

This is recorded as open rather than decided because it adds machinery before we have evidence the descent needs active probes at all — several of the layer checks have passive equivalents. Worth deciding when the first descent is built against a real failure.

---

# Part 4 — Data and state

## D14. Memory — **deferred to Stage 2, shape decided now**

**Question.** Should the agent retain significant events per router or per protocol, and retrieve them in later investigations?

**Options.**
1. **No memory.** Every investigation starts from nothing.
2. **Model-written memory files** — the model decides what is significant and writes summaries.
3. **Code-derived event store**, schema'd, keyed by object.

**Decision.** Option 3, built at Stage 2, designed now.

**Why the idea is right.** "Is this new, or part of a pattern?" is the highest-yield triage question there is, and no point-in-time `show` command can answer it. *This interface has flapped fourteen times this month* is frequently the entire answer. Chronic versus acute is often the whole diagnosis.

**Why the shape matters more than the idea.** Three rules, and violating any one turns an asset into a liability:

**Memory is derived, never authored.** Entries are written by deterministic code from validated envelopes: `{timestamp, device, object, event_type, from_state, to_state, evidence_ref}`. Schema'd, not prose. If the model decides what is significant and writes summaries, the system reads its own hallucinations back as evidence and compounds them over time. That is the one failure mode that would undermine everything else in this document.

**Memory is historical only. The device always wins on current state.** Memory answers *has this happened, how often, when last*. It never answers *what is it now*.

**Key by object, not by router or protocol.** `PE2:GigabitEthernet0/0/0/1`, `PE2:bgp:10.0.0.31`. Router-level and protocol-level views become queries over that, not separate stores. Files-per-router become unqueryable the first time someone asks for "every interface that flapped more than five times this week" — so even SQLite beats markdown here.

**Why Stage 2 and not Stage 1.** At Stage 1 a human is asking the question; they are present and they know whether it is new. Memory earns its place when the agent is woken by an event and nobody is there to say "this again?" Building it later also means designing the event schema after seeing which events actually mattered, rather than guessing now.

**Growth.** Slots in as a fourth evidence axis alongside D8 — retrieved through a tool, into an envelope, cited in the grounding check like everything else.

**Revisit if.** Stage 1 investigations repeatedly stall on questions only history can answer. That would pull it forward.

---

## D15. Parsing strategy

**Question.** How does semi-structured device output become data?

**Options.**
1. **Model extraction.** Give the model the raw output and ask for JSON.
2. **Template parsing.** TextFSM / Genie / TTP templates per `(domain, platform)`.
3. **Structured APIs.** NETCONF / YANG / gNMI where available.

**Decision.** Option 2 as the primary mechanism, with option 3 preferred wherever the platform supports it cleanly and option 1 excluded from the evidence path entirely.

**Why.** Option 1 puts a probabilistic step between the device and the evidence, which contaminates every downstream claim — including the grounding check, which would then be verifying against possibly-invented data. The model may *summarise* validated evidence; it may not *produce* it.

Option 3 is better than option 2 where available, because it removes parsing entirely. It is not universally available or uniformly modelled across vendors, which is exactly the kind of difference that must terminate below the model (D9).

The atomic unit of work is therefore not a command. It is:

```
(domain, platform, stage) → command list
                          + parse template
                          + normalised schema conformance
                          + safety test
```

**Growth.** This quadruple is the unit that gets authored, reviewed, and tested. It is contained enough to be a single piece of work and complete enough that nothing else needs touching.

**Revisit if.** A platform's structured API covers a domain completely — then that domain's rows become API calls, invisible above the dialect gate.

---

## D16. Configuration retrieval and the context budget

**Question.** The intent axis means fetching configuration. On a production route reflector that is thousands of lines. How is that bounded?

**Options.**
1. **Fetch flat `show running-config`** and let the model read it.
2. **Fetch the domain section only.**
3. **Section scoping, device-side filtering, template parsing, and projection to scope** — with a hard budget in code.

**Decision.** Option 3, plus a preference for the source of truth over the device wherever it exists.

**Why.** Status output is naturally bounded — a summary is a summary. Configuration is not. It grows with the estate, and it is the single largest context risk in this design. A flat `show run` from a route reflector carrying several hundred sessions will exceed any practical window, and it will do so *silently* — degrading reasoning rather than failing. Silent degradation is the worst failure mode available to us, because nothing in the pipeline reports it.

Five reductions, applied in order, cheapest first:

**1 — Section scoping.** `get_intent(scope, domain)` already carries the domain, so the domain selects the section: `domain=bgp` retrieves the BGP configuration and nothing else. There is no mode of this tool that returns the whole configuration. That prohibition has exactly the same standing as the prohibition on `run_command`.

**2 — Object scoping.** `get_intent` also accepts an optional `object_id`, mirroring `get_status_detail`. The wide pass fetches the section; the narrow pass fetches one neighbour's configuration.

**3 — Device-side filtering.** Output modifiers (`| include`, `| section`, `| begin`) reduce before the data crosses the wire, which is the cheapest reduction available. **The filter is never a model-supplied string.** It is authored by a human as part of the `(domain, platform, stage)` row, with at most a slot for a validated object id. A model-supplied pipe expression would be a command-injection surface wearing a different hat.

**4 — Template parsing to the normalised schema.** The largest single reduction. A BGP section with two hundred neighbours becomes a typed table. Text becomes structure, and the structure is vendor-neutral — the same shape whether it came from IOS-XR or Junos.

**5 — Projection to scope.** Parse everything, which is cheap and happens in code; send only what the flow is investigating. For one session that means the global BGP attributes, that neighbour's effective configuration, and the names of the policies it references — not the other one hundred and ninety-nine neighbours.

Two consequences deserve stating separately.

**Inheritance must be resolved in code.** IOS-XR `neighbor-group`, `session-group` and `af-group` mean a neighbour's effective configuration is inherited rather than written out. If the model sees `use neighbor-group RR-CLIENT` it has to guess what that expands to — and it will guess from its majority-dialect prior, which is the failure mode this whole architecture exists to prevent. Resolving inheritance is deterministic and removes the guessing surface entirely.

**The model should rarely see configuration at all.** Once intent and observation share the normalised schema, comparison becomes a *diff produced by code*, not a reading task performed by the model. The model reads the reconciliation — which is small — instead of holding two large documents in mind simultaneously. This is the real answer to the context problem, and the other four reductions mostly exist to make it possible.

The exception: a small, named, bounded artefact whose semantics resist parsing — a single route-policy body, for instance — may be passed as text under budget. Never a whole section.

**Budget is enforced, not hoped for.** Every envelope carries its size. A per-run context budget lives in code. A projection that exceeds it returns a structured truncation error, loudly. It does not silently ship forty thousand tokens into the window.

**Growth.** The reductions compound rather than compete, and none of them is vendor-specific above the dialect gate. Adding Juniper adds section commands and templates; the projection, diff and budget machinery is written once. Where a source of truth already holds intended state in structured form, it displaces the device entirely for this axis — and configuration retrieval narrows to drift detection, which is itself a deterministic comparison.

**Revisit if.** Projection discards context the model needed — which would surface as conclusions that are right about the object and wrong about its interaction with something adjacent.

---

## D17. Source of truth for inventory and scope

**Question.** Where does the system learn what exists and who connects to whom?

**Position.** Scope resolution must be deterministic and must come from a single authority. The candidates in this environment are Nautobot and the Containerlab topology data, and the decision of which is authoritative — or how they reconcile — needs the repo review before it can be made properly.

What is already decided: the model never enumerates devices or asserts relationships. It names a scope; code resolves it. That holds regardless of which source wins.

---

# Part 5 — Safety mechanics

## D18. Where the allowlist lives

**Question.** Prompt, code, or tests?

**Decision.** All three, with only one of them actually enforcing.

| Location | Role |
|---|---|
| **Prompt / agent context** | Sets expectation. Does not enforce. |
| **Code** — `APPROVED_COMMANDS`, keyed by `(domain, platform, stage)` | The single chokepoint. This is what enforces. |
| **Tests** | Proves the chokepoint still holds. |

**Why.** A prompt-level list shapes behaviour and costs nothing, but cannot be relied on. Code is the enforcement point. Tests are what stop the enforcement point silently regressing during a refactor — which is the realistic failure mode, not malice.

**Growth.** Adding commands means adding rows in one place and a test that the row is reachable and that unapproved siblings are not.

**Revisit if.** Never. This is defence in depth with a clearly identified load-bearing layer.

---

## D19. Guardrails as tests

**Question.** How is a safety property proven rather than asserted?

**Decision.** Every safety property is an executable test, with a quality bar: **a guardrail test must fail if the guarded behaviour is removed.**

Rejected: tests that check their own hardcoded output, and tests that weaken production validation in order to pass.

The property set for Stage 1:

- no configuration-mode command is reachable through any code path;
- no generic command execution function exists;
- an unapproved command is rejected *before* transport;
- scope authority is enforced — a request cannot reach a device outside the resolved scope;
- credentials never appear in output or logs;
- MCP exposes no write or execute tool;
- every reported claim carries an evidence reference.

**Growth.** Each new capability adds its properties to this list before it ships, not after.

**Revisit if.** The suite goes green while a real safety gap exists — meaning the properties are wrong, not the mechanism.

---

## D20. Grounding

**Question.** What stops the model asserting something the evidence does not support?

**Decision.** A deterministic grounding check between the conclusion and the report. Every claim in the RCA must cite an evidence key from a validated envelope. An uncited claim fails the check and the report does not go out.

**Why.** This is the structural answer to "how would you tell whether the model invented a fact." It is a check, not a review — it runs every time, without anyone's attention.

The output shape supports it: observation, interpretation, and recommendation are separated, never blended. Observations cite evidence. Interpretations cite observations. Recommendations are explicitly the model's, and explicitly for a human to act on.

**Growth.** Applies unchanged to every new evidence axis and every new flow.

**Revisit if.** Grounding passes trivially because claims are written vaguely enough to always cite something. That is a real risk and worth watching in the first RCAs.

---

# Part 6 — How this grows

The point of every decision above is that growth should be additive. Here is what each kind of growth actually costs.

### Adding a vendor (Juniper)

| Touch | Do not touch |
|---|---|
| Platform value in inventory | Flow definitions |
| `(domain, platform, stage)` rows: command lists | Tool surface |
| Parse templates per row | Prompts |
| Schema conformance tests per row | The descent ladders |
| Dialect gate test | The model |

The normalised schema is vendor-neutral, so flows, reasoning, and reporting are written once. This is the entire payoff of D9, and the reason "MCP per vendor" was rejected: it would have pushed vendor differences upward into the model, making every one of the right-hand column entries a maintenance surface.

### Adding a command

Add to the command list for its `(domain, platform, stage)`. Add or extend its parse template. If it introduces a new normalised field, extend the schema *additively*. Add the safety test. Nothing above the dialect gate changes.

### Adding a domain (e.g. LDP)

Add the enum value. Write the flow definition, including what LDP depends on so the descent knows where to go next. Add `(domain, platform, stage)` rows per platform. Tool surface unchanged — still five tools. Model prompt gains one line in the flow manifest.

### Adding a trigger

At Stage 2, a new syslog mnemonic is a row in a lookup table mapping mnemonic → flow. Flow selection for event-driven runs is a lookup, not a model judgement — syslog mnemonics are stable, enumerable, documented identifiers, and there is no reason to spend model judgement on them.

### Moving Stage 1 → Stage 2

Add: trigger listener, deduplication, rate limiting, delivery. Add: the memory event store (D14). The entire evidence pipeline is reused unchanged. The new risk is that boundaries now hold without a human present, which is why D19's property set is a gate on this transition.

### Moving Stage 2 → Stage 3

Add: procedure definitions with preconditions and rollback. Add: the proposal path and approval gate. Add: `propose_procedure` on the MCP surface. The read path is not modified. The model gains exactly one new capability — selecting a procedure and filling its parameters — and gains no ability to execute one.

### Scaling device count

Inventory only. Scope resolution absorbs it. The tool surface, flow count, and manifest size are all independent of device count by construction.

---

# Part 7 — Open questions

These are genuinely undecided, and deciding them from first principles would be guessing.

1. **Active probes** (D13) — separate class and budget, or ordinary reads?
2. **Is one narrowing pass enough?** Only running the descent against a real broken session on `sota-xrd` will say.
3. **Inventory authority** (D17) — Nautobot, Containerlab topology, or a reconciliation. Needs the repo.
4. **Which flow ships first.** The comprehensive health-check pipeline is a different shape — breadth-first collection, no diagnostic question, then summarise. It has no funnel, no gate, no descent, and requires zero diagnostic judgement, which makes it the safest possible first use of the model and a plausible better Stage 1 than "why is BGP down." Currently parked by choice.
5. **Model and serving stack.** Current local model is a placeholder; a tool-calling-capable model is expected shortly. The gate's typed-output requirement (D7) is the binding constraint on that choice, and grammar-constrained decoding remains the fallback if instruction-following alone proves unreliable.

---

# Appendix A — The nine locked decisions

1. Vendor never reaches the model — resolved from inventory by code.
2. Five stage-shaped tools, parameterised by a closed domain enum.
3. Something is a tool only if the model must decide when to call it.
4. The gate emits a typed decision object; narrowing targets must come from objects observed in the wide pass.
5. Bias the gate toward narrowing — `sufficient` requires a named finding that passes a deterministic check.
6. Evidence is three-axis: intended, observed, historical.
7. MCP is read-and-propose only. Execution is never behind it.
8. n8n is upstream of the agent, not a tool it calls.
9. Flows are indexed by object type, never by symptom.

**Explicitly removed:** the cross-flow escalation graph. The protocol dependency ladder lives inside a single flow definition. When the descent bottoms out, the agent reports and hands to a human.

---

# Appendix B — Rules of thumb

Short forms, for arguing with in future design sessions.

- If a basic script solves it reliably, use the script.
- The model may compose an investigation. It must never compose an action.
- Select and fill. Never compose.
- Vendor differences terminate at the lowest possible layer.
- Something is a tool only if the model decides when to call it.
- The lowest broken layer is the root cause.
- Index flows by object, not by symptom.
- Memory is derived, never authored. The device always wins on current state.
- A guardrail test must fail if the guard is removed.
- The model reads the reconciliation, not the documents.
- Over-engineering is building *N* of something before validating one.
