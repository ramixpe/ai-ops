# Evidence Reduction

How large evidence sources are made model-readable without a model reading them.

Companion to `design-thinking.md` D8 (the three evidence axes) and D16 (the context budget). This document generalises D16 beyond configuration and specifies the historical axis in detail, because logs are where the problem is worst in production.

> **Merged revision, 2026-08-16.** Revision 1 was amended during T-028/T-029 by six divergences measured against captured output; revision 2 restructured the document and added five capabilities. This is the merge. §16 logs both sets of changes and the three places revision 2's prose was corrected against measurement. **Measured numbers in this document come from the committed fixtures and can be re-derived; where a claim is projection rather than measurement it says so.**

---

## 1. The principle

> **The payload never enters the context. Reduction happens in code; the model reads the result.**

Nothing is summarised by a model to save space. That would put a probabilistic step between the device and the evidence, contaminating every downstream claim — including the grounding check, which would then be verifying against possibly-invented data.

Reduction is deterministic, testable, and free of model calls. It runs before the budget is consulted, so the budget is a backstop rather than a routine filter.

### Instances of one principle

| Source | Problem | Reduction | Decision |
|---|---|---|---|
| **Configuration** | A route reflector's config runs to thousands of lines | Section → object → device-side filter → parse → project to subject | D16 |
| **Logs** | A window is thousands of lines, mostly repeated | Window → denoise → template → aggregate → episode → project to *device, window and neighbourhood* | this document |
| **Tool manifest** | One tool per command grows with the catalogue | Five stage-shaped tools, closed enum | D10 |

Different sources, identical shape: **the model's input is derived, never raw.**

> **If the model is reading something large, the reduction step is missing. Adding context is never the fix.**

---

## 2. Why logs are the hard case

Configuration is large but *structured and stable*. It has a grammar, a hierarchy and a schema; it can be parsed once and projected.

Logs are large, unstructured in the body, temporally ordered, continuously generated, polluted by operational tooling, and — the part that surprises people — **overwhelmingly repetitive**.

> **Log volume is not information volume.**

### Measured in the `sota-xrd` lab

A 2,000-line sample from the log platform (OBS-014):

| Observation | Value |
|---|---|
| Lines in sample | 2,000 |
| Distinct event types | 15 |
| Most frequent single event, occurrences | 1,346 |
| Share of corpus that is the collector's own SSH churn | 97% |
| Severity-5 events present (BGP `ADJCHANGE`, IS-IS adjacency) | none — dropped upstream |

**The corpus is 0.75% distinct**, and **most of it is self-inflicted** — the polling tool's own SSH sessions. The system was filling the log store it later wanted to read.

### The same window, read off the device instead

**Amended at T-028.** Both headline numbers above are properties of the *log platform*, not of logs. Measured on PE2's `show logging last 200`, the same events read from the device's own circular buffer:

| Observation | Loki sample | Device buffer |
|---|---|---|
| Most frequent single event, occurrences | 1,346 | 1 |
| Records removed by deduplication | most of the corpus | **zero** — across all 18 committed fixtures, 3,600 records, none removed |
| Severity-5 events present | none | present, and they are the whole story |

The 1,346 duplicates are introduced **by the syslog pipeline**. They are not in the source. This matters beyond arithmetic: the corpus OBS-014 measured was not a log corpus, it was a pipeline artefact, and **a reducer tuned to it would be fitting itself to a defect.**

Repetition is still not worthless — frequency, timing and rate are evidence (§3.4). But on this source the reduction that does the work is noise filtering, not deduplication: 200 entries in, 28 out.

---

## 3. The five reductions

Applied in order, cheapest first, mirroring D16's structure for configuration.

### 3.1 — Window and source scoping

The descent already knows the device and the object under investigation. Bound the query by both before anything is fetched:

- time window derived from the investigation, not a fixed default;
- device from the rung's scope (`Rung.device_scope`);
- severity floor **only where the platform's floor is trustworthy** — see §4, where this fabric's is not.

This is the only reduction that happens at the source. Everything after it happens post-retrieval, because a source cannot be trusted to filter correctly — B-206a is that failure, live.

*Implemented in part.* `log_window` inherits the device from the rung and the size bound from `show logging last <n>`. A time window derived from the investigation is not yet wired.

### 3.2 — Noise filtering

Drop event classes that are known not to be evidence. In this estate that is, first and foremost, the collector's own session churn.

**Every filter rule must declare how it attributes.** This is the correction that matters most in this document, and it exists because the original phrasing — *"filter by source, not by content"* — was right as a rule and was implemented as a facility drop, **which is a content rule wearing a provenance label**.

Two legitimate attribution methods:

| Method | Basis | Measured on this fabric |
|---|---|---|
| `SOURCE_ADDRESS` | Every address the record names is in an **enumerated** set of known tooling hosts | 2,558 of 2,723 SSH records name only `.1`, `.2`, `.5`, `.6`; the devices are `.11`–`.31`, so none is device-to-device |
| `GENERATING_PROCESS` | The record names the process **and** the session object that produced it | 843 of 843 SYSDB records name both `client 'exec'` and `/vty/` |

**Enumerated sets, never subnet tests.** Devices and tooling routinely share a management range — here both are in `172.20.250.0/24` — so a subnet test attributes a device-originated session to the collector, silently. A test asserts no device address appears in the tooling set.

**Unattributable records are kept and counted.** The remaining 165 SSH records name no address at all. Nothing proves them to be the tool's, so they survive, and the count travels with the window as `unattributed_kept`. Making the cost visible is what stops the conservative choice quietly becoming folklore about how many entries "the filter leaves behind".

#### The measured cost of getting this wrong

Eight records deleted from PE2's `broken` window that nothing attributed to this tool. **All eight are severity 3 — the highest-severity records on the device** — and one of them is

```
Aug 16 07:41:32.006 UTC  sshd[202504]: process_output:
  ssh_packet_write_poll: Connection reset by peer
```

an interactive session dying **22.7 seconds before the interfaces went down**. On this fabric that was the capture script itself. In production the same line is an operator's session dropping mid-change — plausibly the most diagnostic line in the window.

> **A noise filter that can silently delete the tool's own damage is the wrong filter.**

Filter rules are declared, named and reviewable — the same discipline as §0.10's ignore rules, and `NoiseRule.describe()` puts the attribution method in the audit trail rather than in a comment.

#### The downstream consequence, which was worth paying for

Correcting this took PE2's `healthy` window from **zero** retained records to **nine**, all severity 3. The refusal golden case therefore no longer asserts "the model handles an empty list"; it asserts the model is shown the nine loudest lines on the device and still answers `found: false`. See §12, where over-reach and empty-result turn out to be the same failure seen from two ends.

### 3.3 — Template extraction

Group lines into event *patterns*: variable parts become slots, invariant text is the template.

**Network syslog needs no clustering algorithm for the common case.** Generic algorithms such as Drain exist to *discover* structure in unstructured text. IOS-XR has already assigned every event type an identifier:

```
RP/0/RP0/CPU0:Aug 16 07:44:28.097 UTC: bgp[1053]:
  %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Down - hold time expired
   ^^^^^^^^^^^^^^^^^^^^^^^
   the template key already exists
```

Extraction reduces to grouping by `(mnemonic, normalised body)`. Deterministic where clustering is not, and the mnemonic is the same identifier Stage 2's trigger→flow lookup keys on — one parsing effort, two consumers.

Reserve statistical clustering for genuinely unstructured sources, as a fallback rather than the default.

**Confirmed against the parser (T-028).** The `logging` parser already emits `mnemonic`, `facility`, `severity` and `code` as separate fields on every record. The template key is not merely discoverable — it is parsed out before any reduction runs, and grouping is `Counter(r["mnemonic"] for r in records)`.

**Not implemented in `shape_window`, deliberately.** The grouping is trivial; what it produces is not yet worth producing. At 28 records the aggregate carries no information the records do not, and collapsing them costs the verbatim ordering §6's episodes are built from. Tracked as **B-414**.

### 3.4 — Aggregation

Collapse each template into one record carrying its statistics.

**Count is evidence, not a compression artifact.** An event occurring 1,346 times is a flap; the same event once is a transition. A raw window buries that distinction under volume; aggregation surfaces it. The reduction makes the evidence *better*, not merely smaller.

**Deduplication is a separate step and removes nothing at source.** Measured across all 18 committed `show logging` fixtures — 3,600 records — deduplication removes **zero**. Keep `dedupe` as a documented no-op on the device path: it is load-bearing for B-206, where the duplication is real, and dead-looking until then.

That relocates rather than weakens the argument for aggregation. Counts and rates become evidence at the point where a source aggregates across time or devices, which is B-206 — not a 200-line device buffer.

**Singletons are never aggregated away.** An event that occurred exactly once in a corpus of thousands is frequently the most important line in the window. **No minimum-count threshold, ever.** Pinned by test today rather than described for later: one `ROUTING-BGP-5-ADJCHANGE` among 3,000 routine records, asserted to survive shaping. That property must hold on B-414's first day, and a test that already exists is harder to forget than a paragraph in a backlog item.

**Temporal shape, not just count.** Two records with identical counts can mean opposite things: 60 events evenly spread over an hour is a chronic condition; 60 events in ninety seconds is an incident. Preserve `max_rate_1m` and a burst count alongside the total. Rate is what distinguishes a dying optic from a historical incident, and it is the same reasoning behind the interface error-counter check being a rate rather than a total. **B-418.**

### 3.5 — Projection

**On the historical axis, project by device and window — never by subject identifier.**

This is the correction measurement forced. On the configuration axis, projecting to the subject is safe because a subject's configuration is *about* the subject — its name is in it. On the historical axis the opposite holds: **the cause sits upstream of the subject in the dependency stack, so the events that explain it name a different object by construction.**

Measured on the `broken` label: filtering PE2's shaped window to the subject `10.255.0.12` retains **0 of 28** records. The causal lines say `GigabitEthernet0/0/0/0` and `P1`. Every event that explains the finding is discarded, and the remaining window would license exactly one honest answer — "no correlating events" — about an incident whose complete timeline was sitting in the buffer.

Subject filtering may exist as an opt-in narrowing aid, for the different question *"what happened to this interface"*. **It must never be enabled for a causal search.**

> **A projection is safe only where the evidence names its subject. Where causation runs from things that do not name it, projecting by subject is a filter on the answer.**

**Relationship-aware projection (§5) generalises this properly**, by projecting over a topology neighbourhood rather than an identifier match. Device-and-window is that generalisation's fallback, not a competing rule.

---

## 4. Source selection

`show logging` on the device is **not a fallback** for local correlation — for that case it is strictly better, and this document's original framing had it backwards.

Every event in the measured causal sequence is severity 5 or 6:

```
07:41:54.688  sev5  PKT_INFRA-LINK-5-CHANGED    Gi0/0/0/0 -> Administratively Down
07:41:54.688  sev5  PKT_INFRA-LINK-5-CHANGED    Gi0/0/0/1 -> Administratively Down
07:41:54.688  sev5  ROUTING-ISIS-5-ADJCHANGE    Adjacency to P1 (Gi0/0/0/0) L2 Down
07:41:54.690  sev5  ROUTING-ISIS-5-ADJCHANGE    Adjacency to P3 (Gi0/0/0/1) L2 Down
07:41:54.712  sev6  MGBL-CONFIG-6-DB_COMMIT     Configuration committed by user 'clab'
07:44:28.097  sev5  ROUTING-BGP-5-ADJCHANGE     neighbor 10.255.0.31 Down - hold time expired
```

Per B-206a only severity 3 and 4 reach the platform, so **none of these arrive.**

### What Loki would actually show, which is worse than nothing

The window is not empty on the platform side. Two records in it are severity 3 and would be delivered:

```
Aug 15 23:07:29.168  sev3  PKT_INFRA-LINK-3-UPDOWN
Aug 15 23:07:29.189  sev3  PKT_INFRA-LINK-3-UPDOWN
```

Those are line-state changes from the **previous day's restore** — a different incident. So an investigator querying the platform for this device and a generous window receives two real, correctly-timestamped link events belonging to the wrong event, and nothing at all from the isolation being investigated.

**That is the failure shape this build keeps meeting.** An empty result is honest and visibly incomplete. A partial result is neither: it is plausible, internally consistent, and silently about something else. A severity filter that removes the consequences of an event while retaining superficially similar events from elsewhere in the buffer does not degrade a timeline, it fabricates one.

It is also the argument for §7. Coverage metadata is what turns this from an undetectable wrong answer into a stated gap.

### What a centralised platform is actually for

**Reach, not fidelity:**

- correlating across devices in one query — a device buffer answers "what happened *here*", and an incident spanning PE2, P1 and RR1 needs one ordered timeline across three buffers (which is where §12's clock-skew mode becomes real, hence **B-415**);
- reading logs about a device that is **unreachable** — the case where the historical axis matters most is exactly the one where `show logging` cannot be run;
- windows longer than a device buffer retains.

Choose the source by what the investigation needs, not by which is more modern. And **fix B-206a before trusting the platform for anything**: a store that silently drops the severities carrying the evidence is worse than no store, because it answers confidently.

---

## 5. Relationship-aware projection

Projection by identifier match is too narrow, and §3.5 records why. The generalisation is to project over an **evidence neighbourhood** derived from topology.

For an investigation into BGP peer `10.255.0.12`, the neighbourhood at increasing depth:

```
depth 0   the peer itself
depth 1   the interfaces and IGP adjacencies the session depends on
depth 2   the adjacent devices those interfaces terminate on
```

The measured case lands at depth 2: the events explaining RR1's session with `10.255.0.12` are interface and IS-IS events on **PE2**, and the IS-IS lines name **P1** and **P3**. Nothing at depth 0 explains anything.

Neighbourhood resolution is arithmetic over inventory and topology — deterministic, never a model inference — and it belongs beside the flow registry with the other subject-resolution code, which already solves the same problem for `DeviceScope.SUBJECT` and `PATH`.

**Depth is bounded and declared.** An unbounded neighbourhood is the whole fabric, which is no projection at all. Record the depth used in the evidence envelope so a reader knows what was searched — and so an empty result at depth 1 is distinguishable from an empty result at depth 3.

This subsumes rather than replaces §3.5: device-and-window is the default when topology resolution is unavailable. **B-417.**

*One caution from this fabric.* `learn-topology` reports that P1 and P2 disagree about the link between them. ~~LLDP data here is self-contradictory.~~ **Corrected 2026-08-17 (OBS-103): it is not.** P1 was configured `LEAF05_DHCP_SERVER` at capture time, so both ends were telling the truth and the disagreement is between LLDP's device-reported names and this inventory's labels (B-435). **The caution below survives the correction**, because a neighbourhood builder cannot tell a naming disagreement from a wiring disagreement either. A neighbourhood built from a source that contradicts itself will silently include or exclude the wrong device. Derive it from the inventory's declared topology, and treat LLDP disagreement as a reason to widen the neighbourhood rather than to pick a side.

---

## 6. Episodes — preserving sequence

Independent aggregation destroys causal chronology, and the ordering is frequently the strongest evidence in the window.

An **episode** is a deterministically constructed, time-bounded sequence of events related by device, topology object, protocol dependency and temporal proximity. Aggregated independently, the sequence below becomes six records with `count: 1` — all true, and the thing that identifies the cause is gone.

```json
{
  "episode_id": "EP-0001",
  "device": "PE2",
  "subject": "GigabitEthernet0/0/0/0",
  "start": "Aug 16 07:41:54.688 UTC",
  "end":   "Aug 16 07:44:28.097 UTC",
  "events": ["PKT_INFRA-LINK-5-CHANGED", "PKT_INFRA-LINK-5-CHANGED",
             "ROUTING-ISIS-5-ADJCHANGE", "ROUTING-ISIS-5-ADJCHANGE",
             "MGBL-CONFIG-6-DB_COMMIT",  "ROUTING-BGP-5-ADJCHANGE"]
}
```

The model receives both aggregated records and episodes. They answer different questions: aggregation answers *how often*, episodes answer *in what order*.

### Why this matters here specifically

**An episode is the log-side mirror of the dependency descent**, and this fabric proves it rather than illustrating it. The descent walks *down* the protocol stack asking "is this layer healthy"; the episode is that same stack observed propagating *upward* in time:

| Descent rung (state, top-down) | Episode event (history, bottom-up) |
|---|---|
| `bgp_session` broken | `ROUTING-BGP-5-ADJCHANGE` @ 07:44:28.097 |
| `transport` broken | — (no log event; state only) |
| `route_to_peer` broken | — (no log event; state only) |
| `igp_adjacency` broken | `ROUTING-ISIS-5-ADJCHANGE` @ 07:41:54.688 |
| **`interface` broken — the cause** | **`PKT_INFRA-LINK-5-CHANGED` @ 07:41:54.688** |

Two independent views of one dependency graph, one from state and one from history, agreeing on which layer is the cause. That is corroboration neither axis produces alone. **When they disagree, the disagreement is itself a finding** — and note that the correspondence is deliberately partial: two rungs have no log event at all, so an episode is evidence *for* a descent and never a substitute.

Episode construction is deterministic: no model call, and the same window yields the same episodes.

### A measured design constraint for B-416

**The gap between the interface event and the BGP event is 154 seconds** — the hold timer expiring, not a delay in anything's processing. A naive temporal-proximity threshold of a few seconds splits this into two episodes and severs the link the episode exists to preserve.

So proximity thresholds must accommodate **protocol timers, not human intuitions about "at the same time"**: BGP's default hold timer is 180s, IS-IS's is 30s, and an episode window shorter than the slowest timer in the dependency chain will systematically break exactly the chains that matter. Derive the bound from the protocols in the flow, and record which bound was used.

---

## 7. Coverage metadata

**This is the `unevaluated` discipline applied to evidence sources.** It was the highest-priority item here when this section was written, because the gap existed then; B-420 closed it 2026-08-19 — see the note after §7's design below for what shipped.

"No BGP events were found" is an incomplete statement unless the completeness of the source is known. Logs can be missing because of severity filtering, a collector outage, source configuration, transport loss, ingestion delay, parsing failure, or a wrapped device buffer.

### Why this is not the same problem as `unevaluated`

Every other guard in this system catches **absence read as presence**: something was not measured, the gap is invisible, and a green result fills the space. `unevaluated`, §0.12's vacuity rule, grounding's citation requirement — all of them work by noticing that something is *missing*.

A filtered source does not produce absence. Measured on this fabric (§4): querying the log platform for PE2's isolation window returns two genuine, correctly-timestamped severity-3 link events **from the previous day's restore**, and nothing from the isolation. The data is real. The timestamps are right. The mnemonics are right for a link event. A timeline built from them reads as competent work.

> **A source that filters what it delivers does not return less of the truth; it returns a different, complete-looking truth.**

That is why no consistency check can catch it — there is nothing inconsistent to find — and why coverage metadata is the only thing that can. Coverage does not ask "is anything missing from this result", which is unanswerable from inside the result. It states what the source **could not have carried**, which is a fact about the source, knowable without knowing what the answer should have been.

Recorded as the sixth silent-failure shape in `docs/build/PROCESS.md` §0.13, and the only one whose polarity is presence rather than absence.

**One correction to how that reads.** A filtered source is *an instance* of the shape, not its definition — §0.13 now states the general form, which is **inference from partial evidence**, and lists four instances of which only this one involves a filter. Another is a hand diagnosis reading a transit next-hop as a destination owner (OBS-089); another is a developer's own successful test runs answering *"does this work in my environment"* when the question was *"does this work with nothing"* (OBS-072).

That matters here because it bounds what coverage metadata can do. **Coverage closes the source-side instance and no other.** It states what a source could not have carried; it cannot state that a correctly-read value is being asked the wrong question. For the source axis it is the right and sufficient answer — but a reader who takes "coverage metadata solves shape 6" away from this section has taken the wrong lesson.

Every evidence response carries coverage:

```json
{
  "coverage": {
    "device": "PE2",
    "window": "Aug 16 07:00-08:00",
    "source": "device_buffer",
    "query_complete": true,
    "buffer_wrapped": false,
    "severity_available": [0,1,2,3,4,5,6],
    "severity_missing": [],
    "records_dropped_unattributable": 0,
    "records_kept_unattributable": 8,
    "device_clock_skew_ms": 31
  }
}
```

Which lets a report say

> No correlating events were observed **in the available coverage**

instead of

> There were no correlating events.

The difference is the one `health.py` already enforces between `unevaluated` and `healthy`.

**Grounding must enforce it.** A claim of absence without coverage metadata behind it fails, exactly as an uncited claim of presence does.

> Grounding, before B-420, enforced citation for claims of **presence** and
> nothing for claims of **absence** — so "no correlating events in window"
> passed with nothing behind it, on a source already known to drop severity 5
> and 6.

That asymmetry is the same one `check_chain_coverage` exists to close, arriving on a different axis: **a check that inspects only what is present cannot see what was omitted.** Which is why absence enforcement is a *peer* check rather than a rule inside the existing one — the input it needs (the coverage record) is not in the report.

The correction in §3.2 raised the stakes: `correlate.v2`'s refusal case is now nine irrelevant records rather than an empty list, so the model has material to build a wrong answer out of, and the refusal is a positive claim rather than the absence of output. **T-029a / B-420**, and it lands before T-032.

**Shipped, verified 2026-08-20.** `grounding.check_absence_coverage` exists,
is exported, and is merged into `check_chain_coverage`'s result exactly as
this section specifies — a peer check reading the coverage record, not a rule
folded into the presence check. The gap this section calls urgent above is
closed; the design below is what was actually built, not a proposal.

---

## 8. What survives — the evidence record

This — never a log line — is what reaches a model.

```json
{
  "template_id":   "%ROUTING-BGP-5-ADJCHANGE",
  "severity":      5,
  "entity":        { "device": "RR1", "type": "bgp_peer", "id": "10.255.0.12" },
  "window":        { "first": "17:02:11.334Z", "last": "17:44:03.891Z" },
  "statistics":    { "count": 14, "rate_per_hour": 20.1, "max_rate_1m": 6, "bursts": 3 },
  "relationship":  { "subject": "10.255.0.12", "depth": 0 },
  "episode":       "EP-0001",
  "sample":        "neighbor 10.255.0.12 Down - BGP Notification sent",
  "evidence_key":  "logs:RR1:%ROUTING-BGP-5-ADJCHANGE:0"
}
```

**One verbatim sample per template is kept deliberately.** It is the receipt — what an engineer checks when they doubt the summary, and the anchor the grounding check cites. A reduction with no verbatim trace back to source is a claim, not evidence.

### Timestamp discipline

**Device clock for correlation, always. Ingest time is secondary metadata.**

Ingest time is when the collector noticed; device time is when the network changed. Correlating "when did it break" against ingest time produces a timeline that is confidently wrong and that nothing downstream detects — the ordering looks plausible and the causality is invented.

Keep both, labelled. A large or growing gap between them is itself evidence: clock skew, collector backlog, or a dropped batch. `dedupe` already keys on the device timestamp for the same reason — the same event re-delivered carries one device clock reading and several arrival times, so keying on arrival preserves every copy.

---

## 9. Progressive disclosure

Reduction is mandatory and outside model control. **Investigation scope is not.**

The model never receives `get_raw_logs()`. It may receive a bounded drill-down:

```
expand_evidence(evidence_key) -> first occurrence, last occurrence,
                                 neighbouring raw records, storage pointer
```

This passes D11's tool test — the model genuinely must decide when it needs more proof — and it removes the incentive to send everything "just in case".

| Tier | Content |
|---|---|
| 0 | Summary: the finding and its chain |
| 1 | Normalised evidence records |
| 2 | Representative verbatim samples |
| 3 | Raw records, via `expand_evidence` only |

Each tier reachable only from the one above. **B-419.**

---

## 10. Rarity and baselines

Raw frequency is only meaningful against a baseline. Thirty-one CPU threshold events in an hour is routine if the device normally produces twenty-eight to thirty-five; three BGP adjacency changes is severe if the historical expectation is zero.

```json
{ "count": 14,
  "historical": { "hourly_mean": 0.03, "p95": 0, "last_seen": "2026-07-11" },
  "anomaly":    { "frequency_ratio": 466, "rare_event": true } }
```

The model does not calculate the anomaly. It reasons about the result.

**This is operational memory (D14) arriving from another direction.** Both need schema'd events keyed by object, written by code from validated envelopes, and queryable for "how often does this normally happen". **Build it once, at Stage 2, as B-203/B-204 — not twice.**

---

## 11. What must not happen

| Anti-pattern | Why |
|---|---|
| The model deduplicates or counts | A probabilistic step between device and evidence. Grounding would then verify against possibly-invented data |
| The model reads raw lines | Invariant 4 — raw log lines are unparsed device text |
| The model decides what is noise | Noise rules must be declared, attributable and reviewable |
| Aggregation drops rare events | The singleton is often the answer. No minimum-count threshold, ever |
| Filtering by message content | Drops real problems that read like noise |
| **Dropping a whole facility because its events are *usually* noise** | The same anti-pattern wearing a provenance label — and the form the mistake actually takes, because it looks like the rule above rather than like a violation of it. §3.2 |
| Subnet tests for tooling attribution | Devices and tooling share management ranges. Enumerate |
| **Retention read as relevance** | An honest filter leaves unattributable records in the window, some of them high severity. If "it survived the filter" reads as "it is related", the filter's honesty becomes a hazard. `correlate.v2` states both halves |
| Ingest time used for correlation | Internally consistent, wrong timelines |
| Silent truncation | Evidence discarded by position rather than relevance |
| Composite relevance scores that gate | A tuned heuristic deciding what the model sees. Rank to order, never to exclude |
| Tuning until output looks reasonable | Fits the reducer to a sample, not to truth. Test against a corpus with a known answer |
| Undeclared ignore rules | The coverage becomes invisible, exactly as with parser templates |

---

## 12. Failure modes that must be tested

Each needs a corpus whose correct answer is known in advance.

| Mode | Test | Status |
|---|---|---|
| **Template collision** | Distinct mnemonics must never share a template ID | **Covered in its current form** — `dedupe` keys on the mnemonic, so two event types cannot merge however similar their text. Re-assert against real template IDs at B-414 |
| **Over-aggregation** | Exactly one occurrence of a critical event among thousands of routine ones — assert the singleton survives | **Covered** — one `ROUTING-BGP-5-ADJCHANGE` among 3,000 routine records |
| **Noise filter over-reach** | A corpus containing both tooling churn and a genuine failure of the same facility — assert only attributable records are dropped | **Covered, and it fired.** §3.2 |
| **Empty result** | An explicit "no correlating events in the available coverage", never an empty set reading as "nothing was wrong" | **Covered twice** — the degenerate empty window, and the harder case of nine present-but-irrelevant records |
| **Sequence loss** | A known causal chain must appear as one episode in the correct order, across a 154-second gap | **Not covered** — B-416 |
| **Clock skew** | Devices with disagreeing clocks must be detected and reported, never silently ordered | **Not covered** — B-415. Untestable today for an honest reason: every window this layer reads is single-device, so there are no two clocks to disagree |
| **Incomplete coverage** | A window missing a severity class must report it, and a claim of absence must fail grounding without it | **Covered — B-420 shipped 2026-08-19.** `grounding.check_absence_coverage` |
| **Projection failure** | A cause outside the subject identifier but inside the topology neighbourhood must be retained | **Partially covered** — the identifier-match failure is measured (0 of 28); neighbourhood retention is B-417 |

Two of these are the same failure seen from two ends. **A filter aggressive enough to guarantee an empty window has already deleted the evidence that would have filled it** — which is why correcting over-reach is what produced the harder empty-result test rather than merely fixing a filter.

---

## 13. What is deliberately not here

| Rejected or deferred | Why |
|---|---|
| Composite evidence-ranking score | A weighted sum of six factors with no ground truth will be tuned until output looks reasonable. If used, it orders — it never gates |
| Evidence graph | Right long-term shape, substantial infrastructure, and over-engineering at thirteen nodes |
| A general "evidence engine" across eleven source types | One reduction exists and shipped recently. Generalising before validating one is the failure the governing rule names |
| Data / evidence / reasoning plane restructure | A useful mental model; a risky code structure now. Keep it as a diagram |
| A seven-tool evidence surface | Conflicts with the five-tool decision. Most collapse into existing tools with different `intent` values; `expand_evidence` is the defensible exception |
| Statistical log clustering as the default | The mnemonic already is the template key. Reserve clustering for genuinely unstructured sources |

---

## 14. Where this sits

Reduction is **library code, not a tool** (D11) — the model never decides when it runs.

```
get_logs -> parser -> denoise -> template -> aggregate -> episodes
                                                             |
                                              project (neighbourhood)
                                                             |
                                              coverage + budget
                                                             |
                                                    evidence envelope
                                                             |
                                                     correlate prompt
```

Today `log_window.shape_window` occupies `denoise` and the device-and-window half of `project`. Everything else in that pipeline is a backlog item, and the shape is stated here so each one lands in a known slot rather than being designed again.

Reduced records carry evidence keys and are cited by grounding like any other evidence. Nothing about the downstream contract changes — which is the point. **Reduction is a step, not an architecture.**

---

## 15. Build items

| Item | Scope | Stage |
|---|---|---|
| **B-414** | Log normalisation — template extraction and aggregation as a general capability | MVP-1 |
| **B-415** | Clock-skew detection and reporting | with B-206 |
| **B-416** | Event episodes — deterministic sequence construction | MVP-1 |
| **B-417** | Relationship-aware projection over a bounded topology neighbourhood | MVP-1 |
| **B-418** | Temporal shape — `max_rate_1m`, burst detection | MVP-1 |
| **B-419** | `expand_evidence` and the four evidence tiers | MVP-1 |
| **B-420** | Coverage metadata, and grounding enforcement of absence claims | **DONE, 2026-08-19** — the only row in this table that is not deferred any more |
| **B-203/204** | Historical baselines and rarity — the same capability as operational memory | Stage 2 |
| **B-206** | Centralised log source, for reach rather than fidelity | blocked |
| **B-206a/b** | Platform fixes blocking B-206 | — |

**B-420 was the one pulled forward, and it has since shipped.** The others are still MVP-1 or Stage 2. Coverage metadata closed a gap that existed on a source measured to return a plausible wrong answer rather than an empty one, with grounding as the enforcement point — see the "Shipped, verified 2026-08-20" note in §7 above.

---

## 16. Divergence and merge log

Recorded rather than silently merged, because a reconciliation that leaves no trace is indistinguishable from a document that was right all along.

### Revision 1 → amended (T-028, T-029)

| # | Divergence | Resolution | Why |
|---|---|---|---|
| 1 | "The most frequent event occurs 1,346 times"; measured on the device buffer it occurs once | **Document amended** | The duplication is introduced by the syslog pipeline. OBS-014 measured a pipeline artefact and generalised it to logs |
| 2 | Aggregation is a major reduction; measured, dedup removes **zero** records at source | **Document amended** | Same root cause as #1. The reduction that does the work here is noise filtering |
| 3 | Project to subject on the historical axis; measured, that retains 0 of 28 records and discards the entire cause | **Document amended** | The events that explain a subject are the ones that do not name it |
| 4 | `show logging` is the fallback, Loki the real source | **Document amended** | Every causal event is severity 5/6 and never reaches Loki. B-206 is for reach, not fidelity |
| 5 | "Filter by source, not by content"; the implementation dropped two whole facilities | **Implementation corrected** | The document was right and the code was wrong. Eight severity-3 records deleted unattributed, one a session dying 22.7s before the incident |
| 6 | Template extraction as a required step; `shape_window` does not do it | **Neither — scope stated** | The mnemonic already *is* the template key. Tracked as B-414 with the singleton property pinned now |

Divergence 5 is worth more than the other five together, because it is the only one where the disagreement was hidden — the implementation had tests, they passed, and they passed because they encoded the same wrong assumption the code did.

> **A test written from the same premise as the implementation confirms the premise, not the implementation. An independent specification is the only thing that catches a premise.**

Now `docs/build/PROCESS.md` §0.13.

### Revision 2 → merged

Revision 2 restructured the document and added §5 (relationship-aware projection), §6 (episodes), §7 (coverage metadata), §9 (progressive disclosure) and §10 (rarity and baselines), all incorporated. Three of its claims were corrected against measurement during the merge:

| Claim in revision 2 | Measured | Correction |
|---|---|---|
| "All fabric SSH records naming one of four management hosts" | 2,558 of 2,723. **165 name no address at all** | The unattributable remainder is the point of the rule, not a rounding error. §3.2 now gives both numbers |
| "Every causal event was severity 5 or 6, and none reach the platform" — true, but read as implying the platform returns nothing | Two severity-3 `PKT_INFRA-LINK-3-UPDOWN` records **do** reach it, from the **previous day's restore** | §4 rewritten. The platform returns a plausible, correctly-timestamped, non-empty answer about the wrong incident — worse than an empty one, and the strongest argument for §7 |
| §6's episode example used invented mnemonics (`OPTICS_RX_LOW`, `BFD_SESSION_DOWN`) | The fabric produced a real six-event episode | Replaced with the measured sequence, plus the descent-to-episode correspondence table — including the two rungs with **no** log event, which is what makes an episode corroboration rather than a substitute |

One thing revision 2 did not have, added during the merge: **the 154-second gap** between the interface event and the BGP event is the hold timer, and it is a hard design constraint on B-416. An episode window shorter than the slowest protocol timer in the chain systematically severs exactly the chains that matter.

The reconciliation table in revision 2's header also attributed the §3.2 correction to T-029; it was found during the post-T-028 reconciliation and is logged as OBS-063.
