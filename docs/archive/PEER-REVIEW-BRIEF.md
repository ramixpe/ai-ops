# Peer review brief — what to read, and what to attack

**For a reviewer of the whole repository, 2026-08-17, at the current HEAD.**

Three external reviews have already run, on the *design*. `docs/design/peer-review-response.md`
records what they found and which of our claims we withdrew. **This brief is for a review
of the repository as it now stands**, which is a different job: the code, the tests, the
evidence, and whether the documents say true things about them.

---

## 1. Read this much, in this order

Roughly two hours to be able to attack it properly.

| # | Read | Why |
|---|---|---|
| 1 | `README.md`, first 110 lines | The claim being made. Includes the one blind trial and the model error it produced |
| 2 | `docs/design/glossary.md` | 137 lines. `intent` means *a question name* here, which collides with normal usage |
| 3 | `docs/build/MVP0-REVIEW.md` §5 | What it does **not** do, by the people who built it |
| 4 | `docs/archive/VERIFICATION.md` | Every claim with its evidence, and the six places verification found the claim overstated |
| 5 | `src/agent_nettools/descent.py` | The module the entire architectural claim rests on |
| 6 | `docs/design/peer-review-response.md` §4 | The five claims already withdrawn under review |
| 7 | `docs/build/BUILD-PLAN.md` §0.12–§0.14 | The three rules this build derived from its own failures |

**Skip on a first pass:** `FINDINGS.md` (≈3,900 lines), the round documents, and
`BACKLOG.md`. They are evidence, not argument — come back to them to check a specific
claim.

---

## 2. The claim, stated so it can be attacked

> Given a BGP session that is down, this reports **which layer is broken** and shows its
> work — and **no model reaches that answer.** The descent is parse-and-compare. Run it
> with `--no-model` and you still get the diagnosis.

Everything else is support for that sentence. Two things follow that a reviewer should
hold us to:

- **`nettools agent` is a different path** and is explicitly untrusted — a bounded
  tool-calling loop where the model chooses. The trust language applies to `investigate`
  alone. If you find that distinction blurred anywhere, that is a finding.
- **The read-only guarantee had a hole until B-438**: `pin_lab_golden_snapshot` performed a
  persistent write from behind a decorator named `_read_only_tool`. It is closed
  structurally now (`mcp_server/server.py` does not import a writer), but it is the best
  evidence that a decorator's *name* guarantees nothing.

---

## 3. Verify it yourself in ten seconds

```bash
make setup && source .venv/bin/activate
nettools investigate RR1 10.255.0.12 --from-fixtures --format table
```

No lab, no credentials, no API key. CI's `offline-demo` job runs exactly this in a clean
checkout with seven credential variables asserted unset.

```bash
./scripts/preflight.sh --skip-lab   # frozen files by hash, suite, lint, offline demo
```

---

## 4. Where to attack — the load-bearing claims, ranked

These are the places where being wrong would matter most. **They are listed because we
believe them, not because we are confident about them.**

| # | Claim | Where to check | What would falsify it |
|---|---|---|---|
| 1 | **No model reaches the diagnosis** | `descent.py`, `checks.py`, `investigation.py` | Any model call reachable from `run_descent()`. Also: `--no-model` producing a different *finding*, not just less prose |
| 2 | **A command cannot be injected** | `templates.render_command`, `platforms.APPROVED_COMMANDS` | Any path from caller text to a rendered command that is not reconstruction from a typed object |
| 3 | **The allowlist is checked before credentials load** | `network_tools._run_approved_commands`, `lab.platform_for` | Anything making platform resolution need an environment variable that is a credential |
| 4 | **No unparsed device text reaches a model** | `prompt_library`, `mcp_server/boundary.py` | A third path to a model. This invariant held for every internal caller for eight phases and then broke when a new consumer arrived (OBS-111) |
| 5 | **A failed report is not emitted** | `grounding.py` — `GroundingFailure` has no field prose can occupy | A caller printing `result.paraphrase` without checking status |
| 6 | **The lowest broken rung is the cause** | `descent.py`, D6, Q-017 | A fault where fixing the named rung does not restore the session. **We know one class exists** — see §5 |

---

## 5. What we already know is wrong or unproven — do not spend time rediscovering these

Listed so the review can go past them. Each is filed.

| | Status |
|---|---|
| **Two simultaneous faults are indistinguishable from one** | Q-019, open. A single interface fault and an interface fault plus a BGP shut produce a byte-identical rung table. The report is *correct and incomplete*, which is indistinguishable from correct in every output this produces |
| **The `bgp_session ∣ transport` boundary has never been separated by a captured fault** | B-463. Round 8 tried; its instrument was broken and the round is void, not refuted (OBS-133). Round 8b is sealed and unrun |
| **Grounding proves citation topology, not truth** | Reviewer C, accepted. A report can cite real evidence for a wrong relation. B-453 catches an invented *entity*; nothing catches a wrong relation between real ones |
| **Accuracy is not measured** | There is no estimand. The trials were designed by someone who knows the ladder and were not sampled from any defined population. `chaos-harness.md` §3.5 |
| **One vendor is verified** | IOS-XR. The IOS-XE and Junos command strings are declared from vendor docs and have never touched a device |
| **The evaluation corpus does not exist** | B-452, deferred, with the leakage routes named |
| **Every IS-IS baseline is stale** | B-465. Derived from the broken fabric; the drift rule was reporting the repair until this was fixed |
| **The coherence bound runs at 77% on a healthy fabric** | B-466. 23.1 s of a 30 s bound. A slow device could make a healthy fabric refuse |

---

## 6. The unusual thing, and please do not recommend removing it

`docs/build/FINDINGS.md` is ≈3,900 lines, append-only, out of order, and contains wrong
turns corrected in place with the original left visible. `BUILD-PLAN.md` §0.12–§0.14 are
three rules derived from those failures, including a seven-face taxonomy of *"evidence
bounds conclusion and the bound is invisible from inside"*.

**A reviewer's instinct will be that this should be tidied into a narrative. It should
not.** The order things were learned in is the content — the reason a rule exists is a
better guide to when it applies than the rule is. `docs/README.md` says so, and an earlier
documentation pass considered narrativising it and declined.

**What is fair game:** whether a rule is *correctly stated*, whether a face is really
distinct from its neighbours, and whether any of them is a post-hoc pattern imposed on
unrelated events. The taxonomy has never been reviewed by anyone outside this build.

---

## 7. Known repository-shape issues, so they are not findings

| | |
|---|---|
| `CLAUDE.md` | Agent instructions. 216 lines since the architecture half moved to `docs/design/architecture.md` |
| `tests/` is 609 files | 563 are captured device output. 45 are test modules |
| `evidence-archive/` | Committed round payloads. Excluded from `ruff` — a formatter must not rewrite an artefact that has to match what ran |
| `Dockerfile` | Untouched since 2026-07-28 and **built by nothing in CI**. Genuine gap, recorded in `REPO-INVENTORY.md` |
| Superseded prompt versions stay in the tree | `correlate.v1`, `v2`. Deliberate — a version is an artefact, not a draft |
| The fault injector is **not** in this repository | `chaos-harness.md` §3.1 requires it: the process applying faults must share no context with the one diagnosing them |

---

## 8. What a useful review would tell us

In rough order of value:

1. **A path to a device write we have not found.** That is the only claim the architecture
   actually makes.
2. **A place where a document says something the code does not do.** Verification found six
   in one pass and the pass was not exhaustive.
3. **A guardrail that passes for the wrong reason** — the §0.12 shape. We have found several
   and expect more.
4. **Whether the seven faces are real.** They are the most transferable thing here and the
   least externally scrutinised.
5. **Whether "the lowest broken rung is the cause" survives contact with a fabric that is
   not this one.** Thirteen nodes, one vendor, one topology.

**Least useful:** style, structure, test count, and anything that begins *"you should
consider adding"*. The backlog has 98 items and 20 of them are open; the constraint is not
ideas.
