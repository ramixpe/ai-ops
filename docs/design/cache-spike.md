# Cache spike — M4, the epoch-aware cache

**Status: SPIKE ONLY. No cache was built.** This document answers the four
questions in the M4 brief with file:line evidence, and concludes (§4) that
Phase 2 is not justified today. That conclusion is itself the deliverable —
see §4 for why building a cache with no cacheable subject would be worse than
not building one.

Baseline at the time of this spike: 2302 passed, 24 skipped, `ruff check .`
clean, on this worktree.

> **[PARTIALLY SUPERSEDED, checked 2026-08-20]** This spike's central premise —
> "the config axis (B-104/105/106) is unbuilt, so there is nothing to cache" —
> is no longer true. B-104 and B-106 shipped 2026-08-19: `config_section.py`
> parses `show running-config router isis` / `show running-config interface
> <name>` (two new templates in `templates.py`, not the single
> `platforms.py:74` hostname command this spike's evidence trail checked), and
> `config_diff.py` reconciles that intent against observed state, wired to
> `nettools investigate --reconcile-config`. B-105 (inheritance resolution)
> was investigated and refused on evidence — this fabric uses no
> `neighbor-group`/`session-group`/`af-group`, so there was nothing to
> resolve. **This does not by itself reopen the cache question**: the new
> config reads are fetched fresh on every `--reconcile-config` invocation
> (an explicit second SSH login, opt-in, never cached), so §4's Q4 answer
> ("nothing is cacheable") is now factually wrong but the practical
> conclusion it fed — no cache exists, none is being requested — still holds
> by a different route. Read §1-§3's mechanism analysis (age-bounds skew,
> the clock problem, the re-read problem) as still-accurate engineering; read
> §4's specific claim that config is unbuilt, and the "Conclusion" paragraph
> that depends on it, as **historical, true of 2026-08-1x, not of today**.

---

## 1. Where does a cached value's age enter the skew computation? It doesn't, today.

`EvidenceEpoch.skew_seconds` is a **loop bracket**, not a bound over
observations:

```python
# src/agent_nettools/epoch.py:288-296
@property
def skew_seconds(self) -> float:
    """How wide the observation window was.

    Not a measure of coherence — see the module docstring. It is the size of
    the interval in which an unobserved transition could hide.
    """

    return self.closed - self.opened
```

`opened` and `closed` are two `dataclass` fields (`epoch.py:271-272`), set at
exactly two points in `collect_epoch`:

- `opened = clock()` at `epoch.py:639`, called once, **before** the
  per-device collection loop (`for target, steps in _plan(...).items():`,
  `epoch.py:641`).
- `closed=clock()` at `epoch.py:721`, in the `EvidenceEpoch(...)`
  constructor call that is the function's `return` statement — evaluated
  once, **after** the loop has finished.

Inside the loop, each `Observation` gets its own `started`/`completed`
(`epoch.py:668`, `epoch.py:704`), and because `clock` defaults to
`time.monotonic` (`epoch.py:619`, monotonic non-decreasing), today it is
true by construction that
`opened <= min(o.started for o in observations)` and
`max(o.completed for o in observations) <= closed`. That is why the bracket
and a max-over-observations formulation currently agree — **today's all-live
collection makes them numerically indistinguishable**, but they are not the
same computation, and the difference is exactly where a cache would break
the guarantee.

**Confirmed: a cached observation would not widen skew at all under the
current formula.** If a future collector inserted an `Observation` into
`epoch.observations` whose data was read 40 minutes ago, `skew_seconds`
would still just be `closed - opened` — the wall-clock span of *this
process's own collection loop*, typically 4-40s (`epoch.py:120-131`,
B-466's measured distribution). The 40-minute-old value would be invisible
to the bound that exists specifically to catch it.

### Minimal correct change

Replace the loop-bracket with a bracket **over observations**, exactly as
the brief's hint suggests, but the constraint from §2 below means it cannot
be a naive `min(o.started)`/`max(o.completed)` — a cached observation has no
`started`/`completed` that means anything in this process's monotonic frame
(§2). The minimal correct change is:

1. Give every `Observation` an **effective monotonic start**, computed once
   at the moment it enters the epoch:
   - live observation: `effective_started = o.started` (unchanged).
   - cached observation: `effective_started = opened - age_seconds`, where
     `age_seconds = wall_clock_now - collected_at` is computed **once**, at
     the single instant the cache is consulted, anchored to this process's
     own `opened` — i.e. one wall-clock subtraction establishing "this value
     is `age_seconds` older than right now," then that duration is
     transplanted onto the current process's monotonic timeline by
     subtracting it from `opened`. This is the only wall-clock arithmetic
     anywhere in the design; see §2 for why it cannot be avoided and why it
     is safe to bound.
2. Compute `skew_seconds` as
   `max(closed, max(o.completed for o in observations)) -
   min(opened, min(effective_started for o in observations))`
   instead of `closed - opened`.

For the all-live case (today), every `effective_started == o.started`,
which is already `>= opened`, so `min(...)` reduces back to `opened`
exactly — **behaviour-unchanged**, which is the thing that must be
mutation-tested if this is ever built (rule 2 of the brief). For a cached
observation, `effective_started < opened` by exactly its age, so the cached
item's age becomes real skew, exactly as `stage-2-architecture.md:162-164`
requires:

> A cached value carries its collection timestamp, and its age counts
> against the coherence skew bound exactly as a live read's staleness would.

---

## 2. The clock problem: `collected_at` exists, and the module is explicit that it does not change what skew means — yet.

`Observation` already carries a wall-clock field, added ahead of this
milestone for exactly this purpose:

```python
# src/agent_nettools/epoch.py:254-259
key: str
device: str
started: float
completed: float
envelope: dict
collected_at: str
```

Its docstring (`epoch.py:233-251`) states the intent and the current
boundary in the same breath:

> `collected_at` is a **second, independent** stamp: wall-clock, taken at
> the same moment as `started`. It exists because monotonic time has no
> meaning outside the process that read it — `time.monotonic()`'s own clock
> has an arbitrary, process-specific epoch, so `started`/`completed` cannot
> be compared against another run's, which a future cache/invalidation
> milestone (the shared-evidence layer `stage-2-architecture.md` sketches)
> will need to do... **This does not change what skew means or how it is
> computed** — `EvidenceEpoch.skew_seconds` and `Coherence` still do their
> arithmetic on `started`/`completed` only, on purpose... `collected_at` is
> additional information riding alongside, not a replacement input to that
> calculation.

So: the field is present, unused for arithmetic, and the docstring already
names this exact milestone as its consumer. That is strong evidence the
design intends `collected_at` to be the bridge, not `started`/`completed`
themselves — confirming §1's approach rather than a competing one.

**The actual clock problem, stated precisely.** `time.monotonic()` is
immune to an NTP *step* (a discontinuous correction, as opposed to a slew)
because it never resets — but it is also **incomparable across processes**
by Python's own contract ("the reference point of the returned value is
undefined"). A cache write happens in a different `nettools` invocation
(possibly a different process entirely, e.g. a syslog-triggered
`route-event` run versus an interactive `investigate` run). There is
**no monotonic value from that write that means anything in this process's
`time.monotonic()` frame.** Wall-clock (`collected_at` vs.
`datetime.now(timezone.utc)` at read time) is the only bridge across a
process boundary — full stop. This is not a design choice to weigh against
alternatives; it is what "process-specific epoch" in the existing docstring
already rules out.

**So the honest framing is not "avoid wall-clock," it is "bound the
wall-clock exposure to the smallest possible surface":**

- Use wall-clock for exactly one purpose: converting `collected_at` into an
  `age_seconds` duration, via a single subtraction
  (`wall_clock_now - collected_at`) performed once, at cache-read time.
- Never let that duration touch `started`/`completed` as *absolute*
  timestamps. It is folded in as an *offset* against this process's own
  `opened` (§1's `effective_started`), so the live arithmetic
  (`completed - started`, and now `max(...) - min(...)`) stays 100%
  monotonic, exactly as today.
- The exposure that remains: if NTP steps the wall clock during the
  interval between `collected_at` (write) and the read, `age_seconds` is
  wrong by the step size. This is smaller in kind than what monotonic was
  built to prevent (an NTP step corrupting *this process's own* multi-second
  collection-window arithmetic) but **larger in magnitude of opportunity**:
  a live epoch's monotonic window is ~4-40s (`epoch.py:120-131`); a cache's
  age can legitimately be minutes to hours, which is a much longer window
  for a step correction to land in. This is worth stating plainly: **a
  cache does not just reopen the epoch's hole, it widens the specific clock
  hazard monotonic time exists to close**, because the interval a wall
  clock has to stay honest over is no longer bounded by one investigation's
  runtime.
- A partial mitigation, not a fix: treat `age_seconds` as untrusted the
  same way `checks.py` treats `unevaluated` — a maximum plausible age past
  which a cached value is refused outright regardless of what the clock
  says (e.g. reject anything the cache itself did not also bound by a TTL
  enforced independently of wall-clock arithmetic, such as a monotonic
  clock kept alive inside the cache's own long-running process, if it has
  one). This only helps if the cache is a persistent service with its own
  monotonic clock spanning write-to-read; a cache implemented as files on
  disk, read by a fresh short-lived process each time, has no such option
  and is fully exposed to this hazard for as long as its TTL allows.

**Conclusion for §2:** the field to use is `collected_at`, exactly as its
docstring anticipates; the arithmetic must be a single bounded wall-clock
subtraction folded in as a monotonic offset, never a wall-clock value
compared directly against another wall-clock value inside the coherence
window; and the exposure this reopens is proportional to cache TTL, which
is itself a reason to keep any future TTL short and the cache's own
staleness bound tight.

---

## 3. The re-read problem: the constraint holds, and it holds for a structural reason stronger than "should."

**Verified: `check_coherence` always re-reads live, unconditionally, for
both re-read targets.**

```python
# src/agent_nettools/epoch.py:771-825 (check_coherence)
```

The two re-read targets are rung 1 (the symptom) and the lowest broken rung
(the cause) (`epoch.py:800-803`: `targets = [outcomes[0]]`, then
`broken[-1]` appended if it differs). Both are read through
`_collect_one_rung` (`epoch.py:732-764`), which calls `run_intent` and
`run_templates_split` directly (`epoch.py:741`, `epoch.py:760`) — the same
live-collection primitives `collect_epoch` itself uses
(`network_tools.run_intent`, `network_tools.run_templates_split`,
imported at `epoch.py:87-91`). `_collect_one_rung` has **no cache
parameter, no epoch parameter, and no code path that reads
`EvidenceEpoch.for_device()`** — it is entirely independent of however the
epoch's own evidence was obtained.

This is confirmed again from the caller side, in `investigation.py`:

```python
# src/agent_nettools/investigation.py:659-662
coherence = lambda outcomes: check_coherence(  # noqa: E731
    the_flow, outcomes, epoch, subject,
    device=device, resolver=resolve, sender=sender,
)
```

`check_coherence` is handed `sender` (the live-transport seam) and the
`epoch` only to read its `skew_seconds`/`bound_seconds` for the `Coherence`
object (`epoch.py:796`) — never to source re-read evidence from it.

**So the brief's conclusion is right, and for a stronger reason than "the
cache should not serve these rungs": it structurally cannot, without a
change to `_collect_one_rung` that does not exist today.** If a cache were
wired into `collect_epoch`'s device-collection loop only (the natural
integration point — see §4), the re-read path is untouched by construction
and stays 100% live. Threading a cache into `_collect_one_rung` as well
would require a deliberate, separate change, and doing so would be a
mistake: it would let the *comparison* value itself be stale, which
defeats the entire purpose of a re-read (the re-read's whole job is to be a
second, independent, live sample — `epoch.py:44-46`,
`evidence-epoch.md:80-102`, D3 at `evidence-epoch.md:163-169`, "every rung"
was explicitly rejected as an option there because it "doubles the
descent's cost and re-introduces the skew problem inside the re-read
itself" — a cache-served re-read would reintroduce that exact problem by a
different door).

**Stated as the design constraint this spike verifies:**

> The cache, if built, may only be consulted inside `collect_epoch`'s
> initial per-device collection loop (`epoch.py:641-716`) — never inside
> `_collect_one_rung` (`epoch.py:732-764`). The symptom rung and the cause
> rung must always be either (a) genuinely fresh at epoch-build time, which
> the age-into-skew mechanism in §1 makes safe even if some other rung on
> the same device came from cache, or (b) re-read live at `check_coherence`
> time regardless — which already happens unconditionally today and must
> stay that way. No code change is needed to enforce this; the enforcement
> is the *absence* of a cache parameter on `_collect_one_rung`, and a future
> change that adds one is the thing to refuse in review.

One subtlety worth naming: a cache serving part of rung 1 or the cause
rung's *own* collection inside `collect_epoch` (not the re-read — the
initial build) is not forbidden by this constraint, and is still safe,
*because* the re-read always happens live afterward and `check_coherence`
compares live-after against whatever-was-recorded-before regardless of
whether "before" was itself cache-derived. A cached "before" that disagrees
with the live "after" is `FABRIC_MOVED` exactly as intended — the mechanism
does not need to know whether "before" was cached or fresh, only that it
must widen skew by the cached portion's age (§1) so the bound is measuring
the true window, not a fiction where the cached fraction of the epoch cost
zero time.

---

## 4. What may be cached at all: nothing, today — the cache has no subject.

The architecture's premise (`stage-2-architecture.md:134-138`) is that
**config** is cacheable and **status** is always fresh:

> A user's question plus the device's **running config** tells us *how
> many* BGP sessions, IS-IS adjacencies, or anything else there *should*
> be. If that config was collected recently AND no syslog shows a config
> change since, then no re-collection is needed... **Operational status is
> still collected at every trigger.**

This build reads **no config**, anywhere, today. Evidence, exhaustively:

- **The allowlist has exactly one running-config command, and it extracts
  one field.** `platforms.py:74`:
  `"facts": ("show running-config hostname", "show version")`. Every other
  platform entry (`platforms.py:71-103`) reads operational state only:
  interfaces, BGP summary, LLDP, IS-IS neighbors, SR-TE policy. There is no
  `show running-config` (full), no `show run router bgp`, no
  `show run interface`, nothing that would let `config_section.py`
  (B-104's own named deliverable) exist.
- **`audit.py` says this explicitly, in its own docstring**
  (`audit.py:9-13`):
  > Config-hygiene auditing (is NTP configured, does the ACL match the
  > standard) needs the config axis, which is B-104 and Part 2; an audit
  > built on `show running-config` before that axis exists would be a
  > second, weaker config reader.
- **`BACKLOG.md` confirms B-104/B-105/B-106 are all `OPEN`**
  (`docs/build/BACKLOG.md:115-117`, `:252-254`): config axis, inheritance
  resolution, and intent-vs-observed diff are all unbuilt, and B-105/B-106
  both declare B-104 as a hard dependency.
- **`nettools config show|check` is not device config.** It is the tool's
  *own* settings surface — `settings.py:24`: "...suitable for
  `nettools config show`" is about `NETTOOLS_*` environment variables, not
  a device's running-config. `cli.py:34` and the `config_sub` parser at
  `cli.py:1677-1684` confirm the same: this is settings validation, not
  network evidence.
- **No cache of any kind exists in the codebase.** `grep -rl "cache" src/`
  turns up `llm_analysis.py`/`prompt_library.py` (Anthropic prompt-prefix
  caching, an unrelated mechanism entirely — API cost, not evidence
  freshness) and the `collected_at` docstring in `epoch.py` itself, which
  is a forward-looking comment, not an implementation.
- **The syslog-invalidation trigger has nothing to invalidate.**
  `event_routing.py` (B-480, `docs/build/BACKLOG.md:223`) routes an
  inbound syslog/alert event to *which flow and device to investigate* — a
  pure `RoutingDecision` table lookup. It has no concept of a cache entry
  to expire, because there is no cache entry.

**Conclusion: the honest answer is "nothing is cacheable right now, the
cache has no subject until the config axis (B-104) exists."** Everything
this build reads today — interfaces, BGP, LLDP, IS-IS, SR-TE, route,
bgp-neighbor, logging — is exactly the class of data
`stage-2-architecture.md` says must **never** be cached ("operational
status is still collected at every trigger"). Building a cache today would
mean either:

1. Caching operational status anyway, which is precisely the failure mode
   `evidence-epoch.md` exists to prevent and `stage-2-architecture.md:156-168`
   names as "the one thing that must not be gotten wrong" — or
2. Building an empty shell (a `ConfigCache` ABC with no config reader ever
   calling it), which cannot be tested against anything real, cannot be
   mutation-tested the way the brief requires (`§Phase 2`: "the age→skew
   path mutation-tested BEFORE anything else" — there would be nothing to
   feed it but synthetic data invented for the occasion), and would sit in
   the tree as an unused surface that the next person to touch it has to
   trust was designed against a real shape rather than a guess.

Both are worse than not building. This matches
`evidence-epoch.md`'s own discipline: `epoch.py`'s docstring (`epoch.py:1-74`)
was written *after* the defect it fixes was measured (§1 there: "each rung
calls `collect_evidence()` afresh... measured"), not spec'd in advance of a
need. A cache built now would invert that: spec'd in advance of a need,
against a subject (config) that is a full milestone (B-104-106, sized `M`
each per `BACKLOG.md:252-254`) away from existing.

**What this spike recommends instead of Phase 2:**

- Leave `Observation.collected_at` exactly as it is — present, unused for
  arithmetic, documented as this milestone's future hook. It already says
  the right thing and needs no change.
- When B-104 lands and there is an actual config reader, revisit this spike
  as the starting point: §1's bracket change and §3's constraint are ready
  to implement against a real `ConfigStore`, and §2's TTL-vs-NTP-exposure
  tradeoff becomes a concrete number to pick (informed by how stale
  `stage-2-architecture.md`'s "collected recently" is willing to mean, once
  an operator states it) rather than an abstract hazard to describe.
- Do not build `evidence_store.py`-shaped scaffolding for this ahead of
  time. `evidence_store.py` (`src/agent_nettools/evidence_store.py:1-25`)
  is the right *pattern* to imitate (ABC + `get_store()` env-selector) but
  it is solving history/pruning for **snapshots already taken** — a
  fundamentally different granularity and lifecycle than "is this one
  device's BGP neighbor-group config still fresh enough to trust," which
  needs an age-bounded read and an invalidation hook `evidence_store.py`
  deliberately does not have (per the brief). Building that shape now,
  against no real config reader, would be guessing at the shape of a
  contract nobody has written yet.

---

## 5. Summary

| Question | Answer |
|---|---|
| Q1: where does age enter skew? | Nowhere today — `skew_seconds = closed - opened` is a loop bracket, not a max over observations (`epoch.py:288-296`). Minimal fix: bracket over `effective_started`/`completed` per observation, where a cached item's `effective_started = opened - age_seconds`. Behaviour-preserving for the all-live case. |
| Q2: the clock problem | `collected_at` (`epoch.py:259`) is the intended bridge, already present, already documented as unused pending this milestone (`epoch.py:233-251`). Wall-clock arithmetic cannot be avoided across a process boundary — monotonic time is explicitly incomparable across processes — but it can be bounded to one subtraction, folded in as a monotonic offset rather than compared directly. The exposure window is proportional to cache TTL, which argues for keeping any future TTL short. |
| Q3: the re-read problem | Verified true, and structurally so: `_collect_one_rung` (`epoch.py:732-764`) has no cache path and is independent of `collect_epoch`. State it as a design constraint: cache only inside `collect_epoch`'s build loop, never inside `_collect_one_rung`. No enforcement code needed — the absence of a parameter is the enforcement. |
| Q4: what is cacheable now | **Nothing.** The config axis (B-104/105/106) is unbuilt (`BACKLOG.md:115-117`); the only running-config command in the allowlist extracts a hostname (`platforms.py:74`); `audit.py` says outright it is not reading config for exactly this reason (`audit.py:9-13`); `nettools config show|check` is tool settings, not device config (`settings.py:24`). Everything actually collected today is operational status, which the architecture itself says must never be cached. |

**Phase 2 is not justified.** No code was written beyond this document.
