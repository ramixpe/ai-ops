# Adversarial Architecture Review

## 1. Verdict

**HIGH confidence.** I would not allow this to drive production paging, ticket closure, or remediation decisions today. I would allow an attended shadow deployment using rate-limited read credentials, with every result compared against an engineer’s diagnosis and with the MCP state-mutating tools disabled. That answer changes only when observations are made temporally coherent, the model-callable baseline mutation path is removed, authoritative prose is rendered deterministically, the BGP rungs use independently discriminating evidence, and randomized holdout trials demonstrate false-positive, wrong-device, multi-fault, and load behavior on the production IOS-XR releases. The implementation is a credible deterministic fault classifier for one carefully bounded lab question; it is not yet a production troubleshooting system.

## 2. The central claim — is it true?

**WRONG — HIGH confidence — D2, D4, D12, D14, D20, and LLD §4.4.** The claim is false as written and only partly true under a much narrower interpretation.

The narrow claim that does hold is:

> Given a caller-selected `bgp_session` flow, device, and subject, the finding enum produced by `run_descent` does not depend on a model.

That path is real:

```text
CLI arguments
  → investigate()
  → run_descent()
  → parsed predicates
  → finding enum
```

The broader repository-level claim fails through three paths.

First, the shipped general agent bypasses the deterministic investigation entirely:

```text
nettools agent
  → run_agent_loop()
  → model selects tools and evidence
  → model writes free-text diagnosis
  → CLI prints it directly
```

`run_agent_loop()` has no descent and no grounding gate. The CLI exposes it as a supported command. The design noticed that this path was free-form in LLD §4.4, but the organizing claim never excludes it.

Second, an MCP-connected model can modify the evidence against which future diagnoses are judged:

```text
model
  → pin_lab_golden_snapshot()
  → save_golden_snapshot()
  → overwrite the device’s golden baseline
  → future diff-against-golden treats that state as expected
```

`pin_lab_golden_snapshot()` is exposed through a decorator named `_read_only_tool`, despite writing persistent state. If invoked while the network is faulty, it can normalize the fault into the baseline. The action is enumerable, but “enumerable” is not the same as “safe,” and this contradicts D12’s promise that execution remains outside model reach and D14’s rule that memory is derived rather than authored.

Third, D20 does not ground claims semantically. It proves that an observation cites an evidence key, that interpretations reference observations, and that the chain is represented. It never determines whether the sentence is true of that evidence. `check_grounding()` does not inspect the relationship between `claim` and the cited result. A report can cite an interface-down key while asserting a chassis power failure; an interpretation can assert fabric-wide impact; a recommendation can name an arbitrary next check. It will pass if its references are structurally valid. Timeline grounding similarly validates timestamp and mnemonic, not the generated event description, recurrence, interval, or `followed_a_commit` claim.

Therefore:

- **HIGH confidence:** the model does not influence the deterministic finding enum on the `investigate` path.
- **HIGH confidence:** the model does influence what operators are told the diagnosis means.
- **HIGH confidence:** the model can influence future evidence interpretation by changing a golden baseline.
- **HIGH confidence:** the repository’s complete action and assertion space is not human-authored in advance.

The defensible replacement claim is: *“The model cannot alter device configuration, and one investigation path localizes a finding using deterministic predicates.”*

## 3. What is strongest

### D18/D19: command containment

**AGREE — HIGH confidence.** Exact command allowlisting, platform resolution before credential loading, and parameter canonicalization by reconstruction are the strongest parts of the system.

The obvious objection is that all commands are read-only, so an exact allowlist is needless ceremony. That objection fails. Read commands differ substantially in cost, output sensitivity, and injection surface; `show` also does not imply harmlessness under concurrency. Exact rendering prevents a model-controlled parameter from becoming a pipe expression or second command. The tests that prove rejection occurs before credentials or transport add real value because they test the enforcement boundary, not prompt behavior.

This supports a claim about command safety. It does not support the broader claim about diagnostic correctness.

### The `unevaluated` state

**AGREE — HIGH confidence — D6, D15, D19.** Treating absence or failed parsing as `unevaluated`, never healthy, is correct.

The obvious objection is that stopping on an unread rung can hide a genuine lower failure. It can, but continuing and naming that lower failure as *the cause* would silently assume the missing dependency. Lower observations may still be collected and reported as independent facts; they cannot safely be included in the causal chain. The current refusal direction is the right one.

### Model-free predicates

**AGREE — HIGH confidence — D6 and D15.** Session state, route presence, adjacency state, and interface state should be evaluated by code where their semantics are actually known.

The obvious objection is that CLI output is messy enough that an LLM may parse more formats than templates. That objection fails for authoritative evidence. Greater apparent format tolerance is purchased by making extraction probabilistic and unreplayable. A failed parser announces loss of coverage; an LLM extraction error normally looks like valid data.

### Blind precommitment and true negatives

**AGREE — HIGH confidence — chaos-harness §§3–6.** Sealing the hand diagnosis before the agent runs and including true negatives is the correct evaluation method.

The obvious objection is that four trials are far too few. That objection applies to the strength of the result, not to the method. The method already found a deterministic false-positive class that the fixture suite did not. The sample is inadequate; the protocol is sound.

### D9: resolving vendor from inventory

**AGREE — HIGH confidence.** The model should not choose the vendor-specific tool.

The obvious objection is that modern models can usually infer IOS-XR from output or device metadata. That adds no information: inventory already knows the platform, while model inference adds a new failure mode. Vendor selection belongs below the model even if vendor semantics cannot all be normalized away.

## 4. Material weaknesses

Ranked by cost of being wrong.

### 1. The descent combines different moments into one causal chain

**WRONG — HIGH confidence — D6, D8, and `investigation.py`’s runner.**

Each rung calls `_collect_for_rung()`, which calls `collect_evidence()` for every rung. A BGP descent therefore executes up to five full seven-command collections, plus detail templates, over multiple SSH sessions and devices. The measured runs take roughly two minutes.

The walker discards each collection’s timestamp and treats the resulting rung verdicts as one state. During convergence, those states may never have coexisted. It can report a perfectly grounded cause assembled from several incompatible network epochs.

The failure is a wrong root cause with valid evidence keys. Section 7 traces it in detail.

### 2. The model can overwrite epistemic ground truth

**WRONG — HIGH confidence — D12, D14, D18, and MCP Phase 8.**

`pin_lab_golden_snapshot` is a model-callable persistent write presented as read-only. A model can pin an outage state as golden, after which future drift comparisons suppress that fault. It can also alter snapshot history used for flap detection.

The failure is persistent false reassurance across later investigations. The network remains read-only while the diagnostic system’s memory has been changed. The architecture protects the managed network more carefully than it protects its own source of truth.

### 3. A supported path gives the model full diagnostic authority

**WRONG — HIGH confidence — D2, D4, and LLD §4.4.**

The general `agent` command gives the model raw tool results, lets it choose the next checks, and emits its free-text answer without D20 grounding. Tool validation prevents unauthorized commands; it does not prevent an incomplete or false diagnosis.

The failure is exactly the believable wrong answer the organizing principle claims to exclude. If this path is intentionally exploratory, its output needs a different trust class and must not share product language with the deterministic investigator.

### 4. Grounding proves citation topology, not truth

**WRONG — HIGH confidence — D20.**

A valid evidence key proves only that an observation existed. It does not prove that the model’s sentence describes that observation. An `obs-N` reference proves only that an interpretation points to another string. It does not prove entailment.

The correlation gate has the same hole. It verifies that a timestamp and mnemonic occur together, but not that the generated `event`, `summary`, `recurrence`, or `followed_a_commit` fields match the log record.

The failure is a false causal narrative or unsafe next check stamped “grounded.” This is worse than an ungrounded answer because the label invites reliance.

### 5. The BGP ladder is not a set of independently discriminating causal rungs

**WRONG — HIGH confidence — D6 and the `BGP_SESSION_FLOW` section.**

The session rung reads the BGP FSM from summary. The transport rung reads a BGP-owned socket/FSM view from neighbor detail. The code now prefers `socket_armed_read`, but falls back to the same connection state used above. No captured fault separates socket arming from session establishment.

This is not just a duplicate-command defect. It means rungs are defined by available CLI views rather than by independently falsifiable dependency hypotheses. The live admin-shutdown trial demonstrated the consequence: the system classified it as `transport_blocked` even though the neighbor detail carried the administrative reset reason.

The unreachable finding is therefore a symptom of the model. A useful rung must be able to disagree with the rung above for a known class of faults and must observe a different subsystem. Otherwise it adds apparent depth without localization information.

### 6. “Passive reads unlimited” will make the observer an incident participant

**WRONG — HIGH confidence — D13 and D6’s collection strategy.**

D13 proposes budgets for active probes while treating passive reads as unlimited. The repository already records that IOS-XR rate-limits repeated SSH logins. The current descent opens repeated sessions and runs full evidence collection at every rung.

At 2,000 devices, an event burst can produce VTY exhaustion, authentication churn, RP CPU load, delayed command responses, and parser timeouts. Those failures then become `unevaluated`, precisely while the fabric is under stress.

The failure is an incident-amplifying diagnostic system whose own load reduces its evidence quality. “Read-only” is a mutation boundary, not a resource-consumption boundary.

### 7. The BGP object identity does not survive real routing contexts

**WRONG — HIGH confidence — D5, D17, and `BGP_SESSION_FLOW.subject_schema`.**

The current subject is a peer IPv4 address, and `inventory_resolver()` resolves it by matching a device router ID. That works for this lab’s loopback-based iBGP sessions.

It fails for:

- eBGP peers whose neighbor address is a link address rather than a router ID;
- duplicate RFC1918 peer addresses in different VRFs;
- multiple BGP instances;
- IPv6 neighbors;
- separate AFI/SAFI state on the same session;
- anycast or shared endpoints.

The failure is either refusal to investigate a valid session or collection from the wrong RIB/device context. A production BGP session identity needs at least local device, routing instance, BGP instance, peer address, local address where material, and AFI/SAFI.

### 8. The two proposed multi-fault signals are not sufficient

**UNPROVEN — HIGH confidence — D6’s open multi-fault section and chaos-harness §7.**

Timeline separation is neither necessary nor sufficient. Two faults can enter in one commit, an older fault may predate retention, and a single failure can generate events separated by hold timers. Missing or skewed logs further weaken it.

Forward consistency is a useful positive detector, not a completeness proof. Several independent causes produce the same upper-layer state, and some masked faults leave no unique signature. “No mismatch found” does not imply “one cause.”

Evidence that would settle their value is a sealed two-fault matrix covering:

- same and different configuration transactions;
- upper and lower faults with identical rung vectors;
- persistent pre-existing plus acute faults;
- hardware plus configuration faults;
- missing-log and incomplete-coverage cases.

The third signal should be **direct violated-intent evidence per rung**. In the canonical example, effective configuration can independently show both the interface shutdown and the neighbor shutdown even though operational verdicts are masked. This detects active independent administrative causes without relying on temporal inference. It will not catch every hardware or transient combination. The only generally decisive mechanism is an intervention followed by re-descent: remove one cause and observe whether another remains. That belongs behind explicit human control.

### 9. Control-plane truth has been substituted for service truth

**TRADE-OFF — HIGH confidence — D8, D13, and the L3VPN growth claim.**

The system gives up end-to-end forwarding validation. A BGP session, route, IGP adjacency, and interface can all be healthy while traffic is blackholed by RIB/FIB divergence, missing MPLS label programming, a bad ECMP member, ACL/uRPF behavior, SR policy programming, or EVPN split-horizon/DF inconsistency.

The capability traded away is the ability to say whether the service works, rather than whether selected control-plane objects look healthy. The trade is worth it for a narrowly named `bgp_session` classifier. It is not worth it if the same architecture is presented as an L3VPN, EVPN, or SR troubleshooter without forwarding-plane evidence.

### 10. Seven object types do not remain seven—or fifteen

**WRONG — HIGH confidence — D5 and Part 6 “Adding a domain.”**

EVPN and SR do not each add one object. Operationally meaningful objects include an Ethernet segment, DF role, MAC/IP binding, EVPN route type, VTEP, SR policy, candidate path, segment list, SID, FEC/label, transport tunnel, RIB entry, FIB entry, VRF attachment, and end-to-end service endpoint.

The alternative is to hide these distinctions as subtypes, findings, and branches inside one large “EVPN” or “SR” flow. That does not remove the cardinality; it moves it into a less visible combinatorial structure.

The failure is either wrong flow selection because materially different objects share one label, or an overgrown flow whose branching and normalized schema are no longer enumerable in a useful review sense.

### 11. Vendor-neutral flow reuse is unsupported

**UNPROVEN — HIGH confidence — D9 and D15.**

Keeping vendor choice away from the model is right. The stronger claim—that adding Juniper leaves flows and checks untouched—is unsupported. Normalized fields do not automatically normalize semantics around graceful restart, add-path, BFD integration, policy evaluation, inactive routes, route recursion, or forwarding installation.

Evidence that would settle it is outcome equivalence across identical injected fault classes on IOS-XR and Junos, including cases where one platform exposes a field the other does not. Schema conformance tests alone will not settle semantic equivalence.

### 12. Four blind trials establish feasibility, not reliability

**UNPROVEN — HIGH confidence — chaos-harness §§2, 5, 11 and MVP-0 Review §4.**

Three correct on-path localizations across different rungs are useful. They do not estimate a production error rate. The trials were selected by people who knew the ladder, every fault was single, and one true-negative class already produced a deterministic false positive before repair.

Evidence that would settle reliability is the harness the document itself specifies: randomized development and independently constructed holdout sets, controls, true negatives, multi-fault cases, and per-class confusion matrices. Until then, “provisionally a pattern” is the strongest supportable claim.

### 13. The constraint makes novel diagnosis deliberately hard

**TRADE-OFF — HIGH confidence — D2, D10, D11, D15.**

The architecture gives up open-world hypothesis generation: inspecting an unexpected field, asking an unplanned but safe `show` question, following an anomaly into an undeclared object, or recognizing a new relationship between two otherwise valid states.

The repository’s own “shape 7”—evidence parsed and carried but not consumed—is a direct example. A less constrained investigator would notice `last_reset_reason`; the deterministic predicate ignored it because the hypothesis was not encoded.

The trade is worth it for authoritative, machine-consumable conclusions. It is not worth pretending the constrained result is a complete troubleshooting workflow. The correct product boundary is a trusted deterministic classifier beside an explicitly untrusted exploratory assistant, not one trust label applied to both.

## 5. What is missing

A real troubleshooting workflow needs the following capabilities, not merely more flows.

- **HIGH confidence — W1, WRONG, D6/D8:** a coherent “as-of” evidence view. The system must know whether observations can coexist before using them in one causal argument.

- **HIGH confidence — W5/W8, WRONG and UNPROVEN, D6:** competing explanations and violated invariants, not one lowest rung. Troubleshooting needs to say “these two causes remain consistent with the evidence” and identify what would distinguish them.

- **HIGH confidence — W9, TRADE-OFF, D8/D13:** service-level validation. For MPLS, EVPN, and SR, the workflow needs evidence from both control-plane intent and installed forwarding behavior.

- **HIGH confidence — W7, WRONG, D5/D17:** stable object identity across device, network instance, protocol instance, address family, and endpoint role.

- **HIGH confidence — W2, WRONG, D12/D14:** authority separation for epistemic state. A model may query baselines; it must not establish, replace, or bless them.

- **HIGH confidence — W6, WRONG, D13:** admission control based on device load and investigation fan-out. The system needs a guarantee about how much pressure it can apply during an event storm.

- **HIGH confidence — W13, TRADE-OFF, D2/D10:** an explicit handoff from bounded classification to exploratory diagnosis. “No applicable flow” and “cause not localized” need to preserve the evidence and enumerate the untested hypotheses without promoting model prose to fact.

The hardest capability traded away is **novel fault discovery**. The trade is worth making for authoritative automation. It is not worth making for the only interface an on-call engineer has during an unfamiliar outage.

## 6. Scale and generalisation

**HIGH confidence — W6, WRONG, D13/Part 6.** Device count is not “inventory only.” At 2,000 devices, the dominant variables are concurrent investigations, sessions per router, event amplification, command latency, and how far a service question fans out. A flat inventory lookup solves none of them.

**HIGH confidence — W1, WRONG, D6/D8.** Temporal skew worsens with scale. SR and EVPN state changes during convergence; the longer collection takes, the less defensible it is to treat the collected fields as one snapshot.

**HIGH confidence — W7, WRONG, D5/D17.** Scope resolution becomes contextual. A peer IP or VRF name is not globally unique. The current router-ID resolver is a lab convention disguised as an object model.

**HIGH confidence — W10, WRONG, D5.** The catalogue grows with operational invariants and relationships, not merely with protocol names. Composite services require several object identities and cross-object checks.

**HIGH confidence — W4/W5, WRONG, D20/D6.** More evidence does not make the current grounding stronger. It creates more valid keys with which a model can support semantically invalid prose, and more same-subsystem rungs that appear independent.

**MEDIUM confidence — W11, UNPROVEN, D9/D15.** Parser and normalized-schema maintenance will grow with platform release and feature combinations, not merely vendor count. I cannot quantify that growth without the production software matrix, but the single-release lab cannot support the stated additive-growth claim.

## 7. The failure mode nobody has thought of

**WRONG — HIGH confidence — D6, D8, and the investigation runner: the RCA can describe a network state that never existed.**

This is more dangerous than a parser failure. Every individual observation is real. Every evidence key resolves. Every deterministic predicate returns the expected answer. The error is in treating observations from different times as a single causal graph.

The implementation performs a full evidence collection at each rung. The live rounds took approximately 114–122 seconds. Consider an ordinary transient convergence:

```text
t0   BGP summary is read: session Idle
t20  interface recovers
t35  IS-IS returns
t50  route is reinstalled
t70  BGP neighbor detail is read while the session is still reconnecting
t85  route rung is read: healthy
t100 IGP rung is read: healthy
t115 interface rung is read: healthy
```

The resulting vector can be:

```text
BGP session       broken
transport         broken
route             healthy
IGP               healthy
interface         healthy
```

The tool reports `transport_blocked`. The actual cause may have been the now-recovered interface. “Lowest broken rung” has localized collection order, not causality.

A worse variant needs only one unrelated event:

```text
t0    original BGP outage is observed
t50   original fault recovers
t105  an unrelated physical interface fails
t115  interface rung sees that new failure
```

The assembled vector can make the newly failed interface appear to explain the earlier BGP outage. All citations are valid. The chain is deterministic. Grounding passes. The model turns it into fluent causal prose.

This survives the existing test strategy for structural reasons:

- Fixtures are static, so every rung reads the same frozen epoch.
- The chaos protocol deliberately waits until propagation settles before invoking the agent.
- Parser tests validate each observation independently.
- Chain tests validate rung order and citations, not temporal overlap.
- The per-collection timestamp exists but is not preserved in `DescentResult`.
- Even one `collect_evidence` bundle is sequential across commands, so merely reusing one bundle does not create atomicity.

The harness has optimized away the exact condition production troubleshooting encounters: a fabric changing while it is being observed.

The required correctness rule is not “all reads must be atomic”; CLI access cannot provide that. It is:

> A causal finding may be asserted only when the observations have a bounded, recorded skew and the symptom and proposed cause remain stable across the observation interval.

If that cannot be established, the result is not `transport_blocked`, `igp_isolated`, or `interface_line_down`. It is `temporally_incoherent`: real observations were collected, but they do not support a single present-tense causal claim.

This is the highest-cost defect because it produces the system’s preferred output shape—a complete, cited, deterministic causal chain—while being wrong about what happened. Every existing trust signal makes the answer more persuasive.

## 8. Recommendations

Ranked by risk reduction.

### 1. Add an evidence-epoch contract before any production shadow run

**Estimated cost: M, 2–4 engineering weeks.**

Collect only the evidence required by the selected flow, reuse it across rungs, batch commands in one session per device, and collect devices in parallel within explicit limits. Preserve per-command start/end times. Re-read the initiating symptom and proposed cause at the end. If either changed, or maximum skew exceeds a protocol-specific bound, emit `temporally_incoherent`.

This buys protection against synthetic causal chains and substantially reduces SSH load and investigation latency.

### 2. Remove persistent writes from the model-visible MCP surface

**Estimated cost: S, 1–3 days.**

Remove `pin_lab_golden_snapshot`, snapshot saves, and any diff operation that implicitly saves state from model-callable tools. Expose a read-only diff that has no write side effect. Baseline creation should be an authenticated operator operation with provenance and an immutable previous version. Stop annotating persistent writes as read-only.

This buys back D12 and D14’s authority boundary and prevents persistent model-induced false negatives.

### 3. Make the authoritative report deterministic

**Estimated cost: M, 1–2 engineering weeks.**

Render observations, causal chain, qualification, and next admissible check from typed fields and reviewed templates. If an LLM paraphrase remains, label it non-authoritative and prevent it from supplying machine-consumed fields or recommendations. Correlation fields such as recurrence and commit coincidence should be computed in code.

This makes the organizing claim materially true and eliminates semantic “grounding” that only checks references.

### 4. Redefine a rung as a discriminating causal contract

**Estimated cost: M, 3–6 engineering weeks for the BGP flow.**

For every rung, require:

- a dependency assertion;
- an observation from a distinct subsystem;
- at least one sealed case where the rung above is broken and this rung is healthy;
- at least one case where this rung is broken and the rung below is healthy;
- the expected upper-layer signature if this rung alone is the cause.

Replace the current transport proxy with independently observed TCP reachability or explicitly rename it as BGP connection state. Do not retain a layer name the evidence cannot distinguish.

This buys meaningful localization rather than a sequence of correlated views.

### 5. Build the configuration/intent axis before claiming multi-fault handling

**Estimated cost: M, 3–5 engineering weeks for effective BGP and interface configuration.**

Resolve inheritance and produce typed active-condition records such as `interface administratively disabled` and `neighbor administratively disabled`. Test these against the two-fault matrix, including old-plus-new and same-commit combinations.

This buys a third independent signal for masked administrative faults. It does not replace post-intervention verification for hardware or transient combinations.

### 6. Run a workload experiment shaped like the proposed 2,000-device deployment

**Estimated cost: M, 2–3 engineering weeks plus representative routers or a faithful SSH/control-plane simulator.**

Measure the exact current collection pattern under realistic event bursts. Record per-router concurrent sessions, authentication failures, RP CPU, command latency, incomplete reads, and end-to-end completion relative to BGP/IGP convergence timers. Establish a hard per-device concurrency ceiling and a global queue policy from those results.

This buys proof that the observer will not amplify an outage and provides the bounds needed by the evidence-epoch contract.

### 7. Replace peer-address scope with a contextual object key

**Estimated cost: M, 2–4 engineering weeks before additional BGP address families.**

Use a typed identity such as:

```text
(device, network-instance, bgp-instance, peer-address, afi-safi)
```

Add local address or interface where it disambiguates the relationship. Resolve the remote owner only when the flow actually needs it; do not assume peer address equals router ID.

This buys correct operation across VRFs, eBGP, IPv6, and multiple protocol instances.

### 8. Complete the evaluation instrument before adding EVPN or SR flows

**Estimated cost: L, 4–8 engineering weeks depending on injector coverage.**

Run randomized single- and double-fault development sets, an independently authored holdout set, controls, and true negatives. Include faults not chosen to align with an existing rung. Report per-class confusion matrices and wrong-device rates.

This buys a characterized failure envelope. Adding protocols before this would multiply unmeasured assumptions.

## 9. What I could not assess, and what I would need

- **HIGH confidence:** I could not assess real accuracy or false-positive rate. I need the raw payload, sealed ground truth, exact injection, pre/post configuration, and scorer output for every trial—not only the findings-log narrative.

- **HIGH confidence:** I could not assess temporal behavior during an actual incident because every recorded trial waits for convergence. I need command-level timestamps from a run started during failure propagation and recovery.

- **HIGH confidence:** I could not assess production load safety. I need the expected alert burst distribution, per-device SSH/VTY limits, AAA behavior, RP CPU headroom, and the intended investigation concurrency at 2,000 devices.

- **HIGH confidence:** I could not assess object and scope correctness for the target estate. I need representative BGP, VRF, MPLS, EVPN, and SR configurations, including peer-address reuse, multiple AFI/SAFI, route reflectors, and eBGP attachments.

- **HIGH confidence:** I could not assess parser portability. I need the IOS-XR release and hardware matrix, captured outputs for each supported release, and any available YANG/gNMI equivalents against which parsed CLI fields can be compared.

- **HIGH confidence:** I could not assess vendor normalization. I need equivalent fault captures from at least one Junos deployment and a field-by-field account of where semantics differ despite matching normalized names.

- **HIGH confidence:** I could not determine which interface is intended to be production-authoritative: `investigate`, `agent`, `analyze`, or MCP. I need the deployment diagram, enabled commands/tools, caller identities, and the exact output consumed by paging or ticket automation.

- **HIGH confidence:** I could not assess the security consequences of raw device/log text reaching external models. I need the selected providers, data-retention terms, prompt payload samples, production data classification, and whether interface descriptions, banners, usernames, or customer-controlled strings can enter model context.

- **MEDIUM confidence:** I could not assess whether independent TCP state is available on the target IOS-XR releases. I need the relevant `show tcp`/PCB outputs—or a documented reason they cannot safely be queried—during BGP administrative shutdown, MD5 mismatch, ACL drop, no-route, and successful establishment.

- **HIGH confidence:** I could not assess baseline governance. I need to know who may pin a golden snapshot, how approval is authenticated, whether prior versions are immutable, and whether production health or drift decisions currently consume that baseline.
