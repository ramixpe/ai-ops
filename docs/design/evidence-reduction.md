# Evidence Reduction

How large evidence sources are made model-readable without a model reading them.

Companion to `design-thinking.md` D8 (the three evidence axes) and D16 (the context budget). This document generalises D16 beyond configuration and specifies the historical axis in detail, because logs are where the problem is worst in production.

> **Reconciled against the implementation, T-028.** `log_window.py` was built before this document landed and measured things the document assumes. Where the two disagreed, §10 records which way each one went and why. Two of the disagreements corrected this document; one corrected the implementation, and it was the most valuable of the three.

---

## 1. The principle

> **The payload never enters the context. Reduction happens in code; the model reads the result.**

Every large evidence source in this system is handled the same way. Nothing is "summarised by the model to save space" — that would put a probabilistic step between the device and the evidence, contaminating every downstream claim including the grounding check, which would then be verifying against possibly-invented data.

Reduction is deterministic, testable, and free of model calls. It also runs before the budget is consulted, so the budget is almost never hit rather than always hit.

### Three instances of one principle

| Source | Problem | Reduction | Decision |
|---|---|---|---|
| **Configuration** | A route reflector's config is thousands of lines | Section scoping → object scoping → device-side filter → template parsing → projection to subject | D16 |
| **Logs** | A production window is thousands of lines, mostly repeated | Window scoping → noise filtering → template extraction → aggregation → projection to *device and window* (**not** subject — see §3 reduction 5) | this document |
| **Tool manifest** | One tool per command grows the manifest with the catalogue | Five stage-shaped tools parameterised by a closed enum | D10 |

Different sources, identical shape: **the model's input is derived, never raw.**

The generalisation worth stating once:

> If the model is reading something large, the reduction step is missing. Adding context is never the fix.

---

## 2. Why logs are the hard case

Configuration is large but *structured and stable*. It has a grammar, a hierarchy, and a schema. It can be parsed once and projected.

Logs are large, *unstructured in the body*, and — the part that surprises people — **overwhelmingly repetitive**. The volume is not information. It is the same information restated.

### Measured in the `sota-xrd` lab

A 2,000-line sample from the log platform (OBS-014):

| Observation | Value |
|---|---|
| Lines in sample | 2,000 |
| Distinct event types | 15 |
| Most frequent single event, occurrences | 1,346 |
| Share of corpus that is the collector's own SSH churn | 97% |
| Severity-5 events present (BGP `ADJCHANGE`, IS-IS adjacency) | none — dropped upstream |

Two things follow.

**The corpus is 0.75% distinct.** 2,000 lines carry 15 event types. Everything else is repetition. Shipping raw lines spends the entire budget restating fifteen facts.

**Most of it is self-inflicted.** The polling tool's own SSH sessions disconnecting account for 97%. The system was filling the log store it later wanted to read.

Neither is a limitation of the log platform. Both are reducible in code, and one of them (the severity drop) is a platform misconfiguration rather than a fact about logs — tracked as B-206a and B-206b.

### The same window, read off the device instead

**Amended, T-028.** Both numbers above are properties of the *log platform*, not of logs. Measured on PE2's `show logging last 200`, the same events read from the device's own circular buffer:

| Observation | Loki sample | Device buffer |
|---|---|---|
| Most frequent single event, occurrences | 1,346 | 1 |
| Records removed by deduplication | most of the corpus | **zero** |
| Severity-5 events present | none | present, and they are the whole story |

The 1,346 duplicates are introduced **by the syslog pipeline**. They are not in the source. This matters beyond arithmetic: it means the corpus OBS-014 measured was not a log corpus, it was a pipeline artefact, and a reducer tuned to it would be fitting itself to a defect. The device buffer is 200 entries in and 28 out, and the reduction that does the work is noise filtering, not deduplication.

Deduplication stays in the pipeline regardless — B-206 reads the same records back out of Loki, where the duplication is real. It is a no-op on this source and load-bearing on the next.

---

## 3. The five reductions

Applied in order, cheapest first, mirroring D16's structure for configuration.

### 1 — Window and source scoping

The descent already knows the device and the object under investigation. The log query is bounded by both before anything is fetched.

- Time window derived from the investigation, not a fixed default.
- Device from the rung's scope (see `Rung.device_scope`).
- Severity floor where the platform supports it.

This is the only reduction that happens at the source. The rest happen after retrieval, because the source cannot be trusted to filter correctly.

### 2 — Noise filtering

Drop event classes that are known not to be evidence. In this estate that is, first and foremost, **the collector's own session churn**.

**Filter by source, not by content.** A rule that drops "SSH session disconnected" would also drop a genuine SSH problem on a device. A rule that drops sessions originating from the collector's own address drops only self-inflicted noise. The distinction matters: the first is a filter on what the message says, the second on who caused it.

Filter rules are declared, named, and reviewable — the same discipline as §0.10's ignore rules. A regex that quietly swallows unrecognised lines defeats the mechanism.

#### This rule caught a real defect (T-028)

The first implementation dropped every record in two *facilities* — `SECURITY-SSHD_SYSLOG_PRX` and `SYSDB-SYSDB`. That is a content rule wearing a provenance label: it deletes SSH events because they are *usually* the collector here, never having established that any particular one was. It passed every test written for it, because those tests were written from the same assumption.

Measured cost on PE2's `broken` window: **eight** entries removed that nothing attributes to this tool. All eight are severity 3 — the highest-severity records on the device — and one of them is

```
Aug 16 07:41:32.006 UTC  sshd[202504]: process_output:
  ssh_packet_write_poll: Connection reset by peer
```

an interactive session dying **22 seconds before the interfaces went down**. On this fabric that was the capture script itself. In production the same line is an operator's session dropping mid-change, and it belongs on the timeline. *A noise filter that can silently delete the tool's own damage is the wrong filter.*

The corrected rule requires each `NoiseRule` to name how it attributes a record, and there are exactly two honest ways to do that:

| Attribution | Used for | What it proves |
|---|---|---|
| `SOURCE_ADDRESS` | `SECURITY-SSHD_SYSLOG_PRX` | Every address the record names is a known management host. Measured: all 2,723 SSH records fabric-wide name only `.1`, `.2`, `.5`, `.6`; the devices are `.11`–`.31`, so none is device-to-device |
| `GENERATING_PROCESS` | `SYSDB-SYSDB` | The record names the process **and** the session object that produced it. Measured: 843 of 843 name both `client 'exec'` and `/vty/` |

Note what the source set is *not*: a subnet test on `172.20.250.0/24`. The devices' own management interfaces are in that /24, so a subnet rule would attribute a device-sourced session to the collector, silently. The set is enumerated, and a test asserts no device address is in it.

**A record whose provenance cannot be established is kept**, and the count is reported as `unattributed_kept` rather than absorbed. That is the conservative direction, and making the cost visible is what stops the conservative direction quietly becoming folklore about how many entries "the filter leaves behind".

The downstream consequence is real and was worth paying for: the `healthy` window went from zero retained entries to nine, so the refusal golden case now shows the model nine severity-3 lines reading `Connection closed by remote host` and requires it to still answer `found: false`. An empty window tests the easy constraint. This tests the one that matters.

### 3 — Template extraction

Group lines into event *patterns*. The variable parts — addresses, interface names, counters, timestamps — become slots; the invariant text is the template.

**For network syslog this is far easier than the general case.** Generic log-template algorithms such as Drain exist to *discover* structure in unstructured text. IOS-XR syslog is semi-structured: the vendor has already assigned every event type an identifier.

```
RP/0/RP0/CPU0:Aug 13 17:02:11.334 UTC: bgp[1053]:
  %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12 Down - BGP Notification sent
   ^^^^^^^^^^^^^^^^^^^^^^^
   the template key already exists
```

So template extraction reduces to grouping by `(mnemonic, normalised body)`, where normalisation replaces addresses, interface names and numerals with slots. No clustering algorithm is required for the common case.

This matters twice over. It is deterministic where clustering is not, and the mnemonic is the same identifier that Stage 2's trigger→flow lookup keys on — one parsing effort, two consumers.

Reserve statistical clustering for genuinely unstructured sources, and treat it as a fallback rather than the default.

**Confirmed against the parser (T-028).** The `logging` template parser already emits `mnemonic`, `facility`, `severity` and `code` as separate fields on every record, so the template key is not merely discoverable — it is parsed out and available before any reduction runs. Grouping is `Counter(r["mnemonic"] for r in records)`. There is no clustering step, no Drain, and nothing algorithmic being deferred.

**Not implemented in `shape_window`, deliberately.** The grouping is trivial; what it would produce is not yet worth producing. At 28 records the aggregate carries no information the records do not, and collapsing them would cost the verbatim ordering the timeline is built from. Reductions 3 and 4 earn their place when a window is large enough that counts and rates *are* the evidence — which this source is not and B-206's Loki source will be. Tracked as **B-414**, which is where normalisation gets generalised across devices and sources.

One property of reduction 4 is pinned in `log_window.py` now rather than deferred with the rest: no step in the module has a minimum-count threshold, and a test asserts a single `ROUTING-BGP-5-ADJCHANGE` survives a corpus of 3,000 routine records. That property has to be true of B-414 on day one, and a test that already exists is harder to forget than a paragraph in a backlog item.

### 4 — Aggregation

Collapse each template into one record carrying its statistics.

**Count is not a compression artifact. It is evidence.** An event occurring 1,346 times is a flap; the same event occurring once is a transition. A raw window buries that distinction under volume; aggregation surfaces it. The reduction step makes the evidence *better*, not merely smaller.

Preserve, per template: occurrence count, first and last seen, and a rate. Rate is what distinguishes a dying optic from a historical incident, and it is the same reasoning behind the interface error-counter check being a rate rather than a total.

**Singletons are never aggregated away.** An event that occurred exactly once, in a corpus of thousands, is frequently the most important line in the window. Aggregation must have no minimum-count threshold below which an event is dropped.

**Amended, T-028.** "Count is evidence" is right, and on this source the count is 1,346 times smaller than §2 first suggested, because the duplication was the pipeline's. On a device buffer, aggregation compresses almost nothing — see §2's amended table. That does not weaken the argument for reduction 4; it relocates it. Counts and rates become evidence at the point where a source *aggregates across time or devices*, which is B-206, not `show logging`.

### 5 — Projection to subject

Send only templates whose subjects intersect the object under investigation, plus device-level events in the window.

This is the same step as D16's projection, applied to a different axis.

#### Amended, T-028 — the historical axis does not project by subject

**On the historical axis, projection is by DEVICE and WINDOW, never by subject identifier.** Subject filtering exists, it is opt-in, and it must never be enabled for a causal search.

The reason is structural rather than incidental. On the configuration axis, projecting to the subject is safe because a subject's configuration is *about* the subject — its name is in it. On the historical axis the opposite holds: **the events that explain a subject are usually the ones that do not name it.** A peer goes unreachable because an interface was shut, and the interface's log line names `GigabitEthernet0/0/0/0`, not `10.255.0.12`.

Measured on the `broken` label: filtering PE2's shaped window to the subject `10.255.0.12` retains **0 of 28** records. Every event that explains the finding is discarded, and the remaining window would license exactly one honest answer — "no correlating events" — about an incident whose complete timeline was sitting in the buffer.

So the projection that does the work here is the one reduction 1 already performs: scope by device and by time window. `filter_to_subject` stays in the module because narrowing to a subject is a legitimate thing to want when the question is "what happened to *this* interface", and it stays off by default because a causal search is never that question.

The general form, which is the part worth carrying to other axes:

> A projection is safe only where the evidence names its subject. Where causation runs from things that do not name it, projecting by subject is a filter on the answer.

---

## 4. What survives

The normalised event record. This — never a log line — is what reaches a model.

```json
{
  "template_id":   "%ROUTING-BGP-5-ADJCHANGE",
  "severity":      5,
  "template":      "neighbor {peer} Down - {reason}",
  "count":         14,
  "first_seen":    "2026-08-13T17:02:11.334Z",
  "last_seen":     "2026-08-13T17:44:03.891Z",
  "rate_per_hour": 20.1,
  "subjects":      ["10.255.0.12"],
  "sample":        "RP/0/RP0/CPU0:Aug 13 17:02:11.334 UTC: bgp[1053]: %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12 Down - BGP Notification sent",
  "evidence_key":  "logs:RR1:%ROUTING-BGP-5-ADJCHANGE:0"
}
```

**One verbatim sample per template is kept deliberately.** It is the receipt — the thing an engineer checks when they doubt the summary, and the anchor the grounding check cites. A reduction with no verbatim trace back to source is a claim, not evidence.

### Timestamp discipline

**Use the device's own timestamp, never the platform's ingest time.**

Ingest time is when the collector noticed. Device time is when the network changed. Correlating "when did it break" against ingest time produces confident, wrong timelines — and no downstream check catches it, because the timeline is internally consistent and simply refers to the wrong events.

Where both are available, keep both and label them. Where they disagree by more than a threshold, that disagreement is itself a finding: it means clock skew, collector backlog, or a dropped batch.

---

## 5. What must not happen

| Anti-pattern | Why |
|---|---|
| The model deduplicates | Puts a probabilistic step between device and evidence. Grounding would then verify against possibly-invented data |
| The model reads raw lines | Violates invariant 4. Raw log lines are unparsed device text |
| Aggregation drops rare events | The singleton is often the answer |
| Filtering by message content | Drops real problems that read like noise. Filter by source |
| Dropping a whole facility because its events are *usually* noise | The same anti-pattern wearing a provenance label. "Usually" is a content judgement about a class, not an attribution of an instance — and it is the form the mistake actually takes, because it looks like the rule above rather than like a violation of it |
| Retention read as relevance | An aggressive-but-honest filter leaves unattributable records in the window, and some will be high severity. If the model treats "it survived the filter" as "it is related", the filter's honesty becomes a hazard. `correlate.v2` states both halves explicitly |
| Ingest time used for correlation | Produces internally consistent, wrong timelines |
| Reduction tuned until output "looks reasonable" | That is fitting the reducer to a sample. Test against a corpus with a known answer |
| Undeclared ignore rules | The coverage becomes invisible, exactly as with parser templates |

---

## 6. Interaction with the budget

`evidence_budget.py` enforces per-source and total limits with middle truncation.

Reduction changes the budget's role entirely. **Before reduction, the budget is always the binding constraint, and truncation is routine** — meaning evidence is being discarded on every run, by position rather than by relevance. Middle truncation cannot know which lines mattered.

**After reduction, the budget is a backstop that rarely fires.** When it does fire, that is a signal: either the window is too wide, projection is too loose, or the fabric is genuinely producing many distinct event types — all three of which are worth knowing.

A budget that fires routinely is not protecting anything; it is silently deciding what the model sees. That is the same silent-degradation shape this build has hit repeatedly, and reduction is what removes it.

---

## 7. Failure modes to test for

Each of these should have a test with a corpus whose correct answer is known in advance.

**Template collision.** Two genuinely different events normalising to the same template and merging. Detect by asserting that distinct mnemonics never share a template ID.

**Over-aggregation.** A unique event lost among repeats. Test with a corpus containing exactly one occurrence of a critical event among thousands of routine ones, and assert it survives to the output.

**Clock skew across devices.** Multi-device correlation breaks when device clocks disagree. Detect and report rather than silently ordering by a skewed timestamp.

**Noise filter over-reach.** A filter written for the collector's churn also removing genuine session failures. Test with a corpus containing both.

**Empty result.** A window with no relevant events must produce an explicit "no correlating events in window", never an empty set that reads as "nothing was wrong." Absence is not evidence of absence — the same `unevaluated` discipline as everywhere else.

### Coverage as of T-028

| Failure mode | Status | Where |
|---|---|---|
| Template collision | **covered, in its current form** | `dedupe` keys on the mnemonic as well as the text, so two event types cannot merge however similar they read. `test_two_distinct_events_never_collapse_into_one`. Re-assert against real template IDs when B-414 introduces them |
| Over-aggregation | **covered** | `test_a_singleton_survives_a_window_of_thousands_of_repeats` — one `ROUTING-BGP-5-ADJCHANGE` among 3,000 routine records, asserted to survive. No reduction in the module has a minimum-count threshold |
| Noise filter over-reach | **covered, and it fired** | The defect this document found; see §3 reduction 2. `test_noise_filtering_attributes_rather_than_assuming` runs a corpus containing collector churn, a genuine auth failure from a foreign address, and an unattributable session error, and asserts only the first is dropped |
| Clock skew across devices | **not covered** — filed as **B-415** | Untestable today and for an honest reason: every window this layer reads is single-device, so there are no two clocks to disagree. It becomes real with B-206 (cross-device correlation), and the test belongs in the same change |
| Empty result | **covered, twice** | `empty_window` pins the degenerate case. The `no_correlating_events` case now pins the harder one — nine severity-3 records present and none of them relevant, which is where a model actually fails |

The last row is the one to read carefully. Correcting the noise filter is what turned that case from "assert the model handles an empty list" into "assert the model does not reach for the loudest line in front of it." **The fix to one failure mode produced the test for another**, which is worth noticing: over-reach and empty-result are the same failure seen from two ends, because a filter aggressive enough to guarantee an empty window has already deleted the evidence that would have filled it.

---

## 8. Where this sits

Reduction is **library code, not a tool** (D11) — the model never decides when it runs.

It belongs between the log parser (T-015) and the correlate prompt (T-028):

```
get_logs  ->  logging parser  ->  REDUCTION  ->  evidence envelope  ->  correlate prompt
   |              |                   |                |                      |
 fetch      structured lines    normalised records   budgeted           model reads
                                                                        records only
```

The reduced records carry evidence keys and are cited by the grounding check like any other evidence. Nothing about the downstream contract changes — which is the point. Reduction is a step, not an architecture.

---

## 9. Scope note

This document specifies the mechanism. The build items are tracked separately:

- **B-414** — log normalisation: template extraction, aggregation, noise filtering
- **B-415** — clock-skew detection across devices (§7, row 4)
- **B-206** — Loki-backed `get_logs`, blocked on B-206a and B-206b
- **B-206a** — the severity drop is downstream of the devices; their trap level is already informational
- **B-206b** — the collector's own SSH churn is 97% of the corpus

MVP-0's `correlate` prompt takes the `show logging` fallback and applies reductions 1, 2 and 5 in code (`log_window.shape_window`); 3 and 4 are B-414, for the reason in reduction 3. The reduction is not conditional on the source: a 2,000-line `show logging last 500` needs it as much as a Loki query does.

### Amended, T-028 — what B-206 is actually for

Loki is **not an upgrade to the local buffer**, and this document's framing of `show logging` as "the fallback" had it backwards for this fabric. Measured: every event that explains the `broken` label is severity 5 or 6, and per OBS-041 / B-206a severity 5 and 6 never reach Loki at all. On the incident this build actually captured, the local buffer is *strictly better* — the Loki path would have returned nothing.

B-206 earns its place on two things the device buffer structurally cannot do, neither of which is fidelity:

1. **Cross-device correlation.** A device buffer answers "what happened on this device". An incident spanning PE2, P1 and RR1 needs one ordered timeline across three buffers, which means one store — and the clock-skew problem of §7 arrives with it, which is why B-415 is scoped to the same change.
2. **Reading a device that is not reachable.** The case where the historical axis matters most is the one where `show logging` cannot be run, because the device is down or isolated. A log store holds what the device said before it stopped saying anything.

Both are about *reach*, not about resolution. Neither is a reason to prefer Loki for a single reachable device, and B-206a must be fixed before Loki is trusted for anything, since a store that silently drops the severities carrying the evidence is worse than no store — it answers confidently and wrongly.

---

## 10. Divergence log

Each place this document and `log_window.py` disagreed, and which way it went. Recorded rather than silently merged, because a reconciliation that leaves no trace is indistinguishable from a document that was right all along.

| # | Divergence | Resolution | Why |
|---|---|---|---|
| 1 | §2: "the most frequent event occurs 1,346 times"; measured on the device buffer it occurs once | **Document amended** | The 1,346 duplicates are introduced by the syslog pipeline. OBS-014 measured a pipeline artefact and generalised it to logs |
| 2 | §3 r4: aggregation is a major reduction; measured, dedup removes **zero** records at source | **Document amended** | Same root cause as #1. The reduction that does the work here is noise filtering |
| 3 | §3 r5: project to subject on the historical axis; measured, that retains 0 of 28 records and discards the entire cause | **Document amended** | The events that explain a subject are the ones that do not name it. Projection is by device and window |
| 4 | §9: `show logging` is the fallback, Loki the real source | **Document amended** | Every causal event on this fabric is severity 5/6 and never reaches Loki. B-206 is for reach, not fidelity |
| 5 | §3 r2: filter by source, not by content; the implementation dropped two whole facilities | **Implementation corrected** | The document was right and the code was wrong. Eight severity-3 records were being deleted unattributed, one of them a session dying 22s before the incident |
| 6 | §3 r3: template extraction as a required step; `shape_window` does not do it | **Neither — scope stated** | The mnemonic already *is* the template key, so nothing algorithmic is deferred. At 28 records the aggregate adds nothing. Tracked as B-414 with the singleton property pinned now |

Four to one is not a score. Divergence 5 is worth more than the other five together, because it is the only one where the disagreement was hidden — the implementation had tests, they passed, and they passed because they encoded the same wrong assumption the code did. The document caught it precisely by not having been written from the code.

The generalisation:

> A test written from the same premise as the implementation confirms the premise, not the implementation. An independent specification is the only thing that catches a premise.
