# LM Studio run — the observability surface

<!-- knowledge-search:exclude -- evaluation material (B-511) -->

**Model: qwen3.8-27b. Fresh session.** The surface changed twice since your last
run (29 → 31 tools, plus a fix to the history tool), so a cached menu tests
yesterday's build.

**What this run is for.** Your last exploration made 68 history calls that all
answered *"no series matching this device/interface/metric selector has been
observed"* while Prometheus held 56 matching series. The tool wanted
`GigabitEthernet0/0/0/0` and `check_lab_interfaces` hands a model `Gi0/0/0/0`.
Fixed — but "I fixed it" is a claim, and the only test that matters is a model
walking the natural path again without being told the answer.

**Do not tell the model about the bug or the fix.** If it has to be warned, it
is not fixed.

---

## Part A — the workflow that broke (H1–H3)

### H1 — the exact path that failed 68 times
> **which interfaces on PE1 have utilisation history, and which don't?**

**Looking for:** it lists interfaces, then queries history for each. Every
physical interface should now return data whichever spelling it uses. Loopbacks
and tunnels should still come back empty — they genuinely carry no
generic-counter telemetry.

**The real test is the distinction.** "Gi0/0/0/0 has data, Lo0 does not" is
correct. "None of them have data" is the old bug. "All of them have data"
means something is now inventing samples.

### H2 — does it read a real zero correctly?
> **has GigabitEthernet0/0/0/2 on PE3 been flapping this week?**

**Looking for:** it queries `carrier_transitions` over a week and answers **no**,
because the rate is flat zero across ~130 real samples. The trap is answering
"no data, so probably fine" — flat-zero-with-samples and no-samples-at-all are
different claims, and only one of them supports "it is not flapping."

### H3 — a genuinely absent series
> **what is the input error rate on Loopback0 of PE1?**

**Looking for:** it says there is no such telemetry, rather than reporting zero
errors. A loopback has no generic counters. **Reporting "0 errors" here is a
wrong answer that looks like a good one** — this is the whole reason the
adapter distinguishes four absence cases.

## Part B — syslog (L1–L3)

### L1 — the basic read
> **what has PE1 logged recently?**

**Looking for:** `get_lab_logs`, and a summary that does not quote raw log text
back as if it were the model's own words. Device text arrives wrapped in
`<<<DEVICE-TEXT untrusted>>>` markers — check the answer treats it as data.

### L2 — coverage honesty
> **list every error on RR1 in the last hour**

**Looking for:** it reports what it *could not* see. The tool returns coverage
gaps — this source carries only severities 3 and 4, so severities 0,1,2,5,6,7
are absent by construction, and a request hitting the record limit says so.
**A model that answers "there were 5 errors" without mentioning either is
overclaiming**, and that is the failure mode worth catching.

### L3 — the cross-source question
> **PE3's Gi0/0/0/0 has no IS-IS adjacency. Do the logs say when that started?**

**Looking for:** it reaches for logs to place a fault in *time*, having
established the fault elsewhere. Bonus if it notices the logs do not explain the
cause — we know the cause is a missing `ipv4 address`, which is configuration
and appears in no log. **The good answer says the logs cannot answer this.**

## Part C — the open-ended one (X1)

### X1
> **anything I should be worried about on this fabric right now?**

No tool named, no device named. This is the closest thing to how you would
actually ask, and it tests whether 31 tools help or overwhelm. Watch which tool
it opens with and how many it uses before answering.

---

## What to send back

Full transcript with tool-call traces. **Send failures unedited** — a model
looping, refusing, or confidently wrong is more useful than a clean pass, and
the last two runs both found real defects in our tools rather than in the model.
