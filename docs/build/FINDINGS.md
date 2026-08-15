# Findings Log Book

Append-only record of everything learned during the build of the investigation layer.

**Rules**

1. Never edit or delete an entry. To correct one, write a new entry that references the old ID.
2. Number entries sequentially from `OBS-001`.
3. Write an entry whenever any of the following is true:
   - Something in `BUILD-PLAN.md` turned out to be wrong, incomplete, or based on a false assumption.
   - Real device or fixture output did not match what the plan predicted.
   - A decision had to be made that the plan did not specify.
   - Something was implemented in a way you are not confident about.
   - A test passes but you suspect it does not really test the thing.
   - You noticed a defect, smell, or risk outside the current task's scope. **Do not fix it — log it.**
   - A dependency, version, or environment detail differed from expectation.
   - You wanted to change a file frozen by `BUILD-PLAN.md` §0.5.
4. Anything with `Needs human review: yes` must also appear in the **Open Questions** table at the bottom.
5. This log is reviewed with the human after Part 4 completes, before any MVP-1 work begins.

**Kinds:** `assumption-wrong` · `surprise` · `decision-made` · `risk` · `defect` · `deferred` · `environment` · `consultation`

`consultation` records a Fable 5 second opinion: the question asked, the answer received, and what Opus 5 did with it. Per `BUILD-PLAN.md` §0.9, an unlogged consultation is an unreviewable decision.

**Escalation.** Every finding carries the level it resolved to under §0.11: `HALT`, `DECIDE-AND-LOG`, or `NOTE`.

---

## OBS-000 · T-000 · Log book created

- **Kind:** environment
- **What happened:** Log book initialised from the template in `BUILD-PLAN.md` §PART 6. No build work has started.
- **Evidence:** This file.
- **What I did:** Created the file. Delete this entry's placeholder status once `OBS-001` is written.
- **Needs human review:** no
- **Blocks:** none

---

<!--
Copy this block for each new entry.

## OBS-nnn · T-xxx · <short title>

- **Kind:**
- **Escalation:** HALT | DECIDE-AND-LOG | NOTE
- **Model:** opus-5 | sonnet-5 | fable-5
- **What happened:**
- **Evidence:**
- **What I did:**
- **Needs human review:**
- **Blocks:**

---
-->

# Open Questions

Anything logged with `Needs human review: yes` is mirrored here so the review has one list to work from.

| ID | Raised in | Question | Blocking? | Status |
|----|-----------|----------|-----------|--------|
| Q-001 | T-002 | Does `reasoning_split: true` fully suppress `<think>` in `content`? If not, is a stripping step acceptable, or should the gate use the Anthropic-compatible route instead? | Yes — gate depends on it | Open |
| Q-002 | T-004 | Is syslog-ng shipping to Loki, and do IOS-XR mnemonics survive into a queryable label? | No — affects Stage 2 only | Open |
| Q-003 | T-005 | Does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger? | No — Stage 2 | Open |
| Q-004 | T-006 | What is the subject naming scheme for an L3VPN service object? | No — flow not in MVP-0 | Open |
| Q-005 | T-020 | What error-counter threshold should `interface_state` treat as broken? | No — default chosen, needs review | Open |
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | Open |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | Open |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.
