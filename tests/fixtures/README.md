# Fixtures — what each label means

Captured output from the real `sota-xrd` lab, replayed by `fixtures.load_fixture_evidence(device, label=...)`.
Fixtures are how every parser, diff, health-rule and descent test runs with **no SSH and no reachable lab**.

```
tests/fixtures/<platform>/<device>/<label>/<command-slug>.txt
```

Nine devices: `P1 P2 P3 P4 PE1 PE2 PE3 PE4 RR1`. One platform so far: `cisco_xr`.

**A fixture is a recording, not a mirror.** It keeps describing the fabric as it was at capture time, however the live lab changes afterwards. That is the point — it is what makes the suite deterministic — but it means a label's value depends entirely on knowing *which* fabric it recorded. Hence this file.

---

## Labels

| Label | Fabric state | Contents | Status |
|---|---|---|---|
| `t0` | **Partly broken** (original) | 7 static intents | Committed — **frozen** |
| `t1` | **Partly broken**, ~90s after `t0` | 7 static intents | Committed — **frozen** |
| `healthy` | **Clean** — the rebuilt fabric | intents **+ templates** | Planned, T-011 |
| `broken` | **Deliberate PE2 core-interface shutdown** | intents **+ templates** | Planned, T-011 |

### `t0` / `t1` — frozen, do not recapture

Two captures ~90 seconds apart of the fabric as it was originally found. The pair exists so `diff_evidence` can be tested against a *quiet* fabric: nothing genuinely changed in 90 seconds, so any reported change is a false positive. `test_quiet_fabric_pair_reports_no_change` pins that, parametrized over all nine devices.

What they record:

- **PE2 and PE4 have zero IS-IS adjacencies** — isolated from the core.
- Consequently neither can reach RR1's loopback, so their iBGP sessions toward RR1 sit **Idle**.
- **RR1 independently reports those same two sessions Idle from the other side** — `10.255.0.12` (PE2) and `10.255.0.14` (PE4).
- `inventory/lab.yaml` records `isis_adjacencies: 0` for both, because `nettools learn-topology` derived the baseline *from* this broken state. `health.py`'s `suspicious_baseline` meta rule exists precisely to stop that being read as healthy.

**These two labels are frozen.** `test_health.py`, `test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers`, the `diff` suite and the T-025 acceptance test are all pinned to exactly this state. Do not recapture them, do not add template captures to them, do not "refresh" them after a lab rebuild — a rebuild produces a *different fabric*, and overwriting `t0` with it would silently invalidate every test that depends on the brokenness. New states get new labels.

> The live fabric was rebuilt on ~2026-08-13 and came up clean: all 16 BGP sessions Established, PE2/PE4 back to 2 IS-IS adjacencies. `t0`/`t1` still describe the pre-rebuild fabric and remain correct as recordings. See `docs/build/FINDINGS.md` OBS-017.

### `healthy` — the clean fabric

The rebuilt fabric with nothing wrong: every IS-IS adjacency up, all 16 BGP sessions `Established`, all interface error counters at zero.

Its job is to be the **negative** case. A descent whose whole purpose is finding the lowest broken rung must also be shown to walk all the way down and report `all_layers_healthy` when nothing is wrong. Without this label, "stops at the right rung" is untested against the possibility that it stops everywhere.

### `broken` — a deliberate, known-cause fault

A PE2 core-interface shutdown, run by the operator, captured while down, then restored.

This is a better test subject than `t0`'s brokenness even though both look similar, because **the cause is known exactly rather than inferred**. With `t0` we observe that PE2 is isolated and reason backwards about why. With `broken` we know precisely which interface was shut and at what time, so a descent's verdict can be checked against ground truth rather than against an interpretation of ground truth.

Expected causal chain, which is exactly the `bgp_session` descent's ladder:

```
PE2 core interface down
  → PE2 loses its IS-IS adjacency
  → RR1 loses its route to 10.255.0.12 (PE2's loopback)
  → the TCP session to that loopback cannot establish
  → RR1's iBGP session to 10.255.0.12 goes Idle
```

---

## Reproducing `broken`

Run by the operator against `PE2`. Recorded here so the state can be recreated after any future rebuild.

> **Not runnable by the build agent.** Configuring a device is a state change, and `BUILD-PLAN.md` §0.11 makes that an absolute HALT. The tool's allowlist cannot express it either — there is no config mode, no `run_command`, and `VERB_ALLOWLIST` is `{show, ping, traceroute}`. This is an operator action, deliberately outside anything `nettools` can do.

```bash
# 1. Identify PE2's core-facing interface (the one carrying the IS-IS adjacency)
ssh <user>@172.20.250.22
  show isis adjacency
  show interfaces brief

# 2. Shut it
configure terminal
  interface <core-interface>
    shutdown
  commit
end

# 3. Let the fault propagate before capturing.
#    IS-IS holdtime must expire and BGP must fall back to Idle -- allow ~60s
#    and confirm rather than assume:
show isis adjacency          # the adjacency should be gone
show bgp summary             # on RR1: 10.255.0.12 should read Idle

# 4. Capture happens here (build agent, `nettools capture ... --label broken`)

# 5. Restore
configure terminal
  interface <core-interface>
    no shutdown
  commit
end
show isis adjacency          # adjacency back
```

**Fill in the exact interface name and the capture timestamp once the break is run** — `<core-interface>` is a placeholder until then, and this file is only reproducible when it names the real one.

### Capture protocol

`healthy` and `broken` must each be captured **complete in one pass** — every device, every intent, *and* every template — before the lab changes state again.

This matters more than it sounds. Mixing states inside one label is the specific failure OBS-017 warned about: healthy `show bgp neighbor 10.255.0.12` output sitting beside a broken `show bgp summary` under the same label gives a descent two contradictory answers about the same session, which is a worse foundation than having no fixture at all.

Sequence:

1. Build agent captures `healthy` against the current clean fabric.
2. Build agent signals ready; **operator runs the break** (above) and confirms it propagated.
3. Build agent captures `broken`.
4. Build agent signals capture complete; **operator restores**.

---

## Adding or refreshing fixtures

```bash
nettools capture --all --label <label>
```

- **Review every captured file by eye before committing.** Fixtures are permanent once pushed. `scrub_output` covers credential- and serial-shaped material, but current XRd output contains none, so the scrubber's only real coverage is its own unit test — it is a safety net, not a reviewer.
- **Check nothing was silently excluded.** `git status --ignored` and confirm the file count, rather than trusting `git add`'s exit code. An unanchored `.gitignore` rule already swallowed a whole directory once in this project (OBS-002), and `git add` reported success while doing it.
- **Never overwrite an existing label to record a new state.** Add a label.
- Filenames derive from the command, slugified — e.g. `show-bgp-summary.txt`, `show-bgp-neighbor-10-255-0-12.txt` for parameterized templates.

## Related

| Where | What |
|---|---|
| `src/agent_nettools/fixtures.py` | `load_fixture_evidence`, built on the `sender=` seam so replay reuses real envelope construction |
| `docs/build/FINDINGS.md` | OBS-017 (the rebuild), OBS-019 (this label scheme and the capture protocol) |
| `docs/build/capture-manifest.md` | T-007 — which templates and parameters each label must contain |
| `CLAUDE.md` → "Testing seams" | How fixture replay sits alongside `sender=` and the fake netmiko module |
