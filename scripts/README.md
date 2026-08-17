# scripts/

Helpers that run **against** this repository. Read-only with respect to devices.

| Script | What it does |
|---|---|
| `preflight.sh` | Everything §2 of `docs/build/OPERATOR-RUNBOOK.md` asks for before a lab window. Exits non-zero on anything unexpected and logs to a file. Prints the two known-benign findings by name so they do not read as alarms |
| `archive.sh` | `archive.sh <round> <dir>` — copies a round's payload into `evidence-archive/`, warns if `.gitignore` would swallow any of it, stages, and **verifies with `git ls-files` that every file is tracked** before reporting success |
| `round5_sampler.py`, `round7_sampler.py` | Round samplers. Read-only: they invoke `investigate` in a loop and never touch a fault |
| `probe_minimax.py` | T-002's provider contract checks |

## What is deliberately not here

**The fault injector.** `fault_lab.py` and the per-round drivers live in
`~/ai-agent-ops/faultlab/`, outside this repository, and `chaos-harness.md` §3.1
requires that: *"The injector runs as a separate process, operated by a human or
a scheduler. It lives outside the repository."* The reason is not tidiness — it
is that the process applying faults must share no context with the one
diagnosing them, and a copy in here is a step toward the agent being able to
reach it.

**A tracked copy of the injector was briefly added here and removed the same
day.** The intent was that §6.1d wants the instrument archived with its payload,
which is true. But a live second copy is not an archive: it is a **duplicate**,
and two copies of a script agree right up until nobody is looking at both —
`BUILD-PLAN.md` §0.13's duplication face, created while implementing the fix for
the procedure face.

The correct mechanism is already in `archive.sh` and in `round8b.py`: the
instrument is copied **into the round's own dated payload directory** under
`evidence-archive/`, where it is a snapshot of what ran on that date and can
never drift from it, because nothing ever runs it again.
