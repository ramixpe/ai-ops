# T-007 — Fixture gap analysis and capture manifest

**Task:** `docs/archive/BUILD-PLAN.md` T-007 (archived 2026-08-20; Part 0's rules live on at `docs/build/PROCESS.md`)
**Consumed by:** T-011 (capture), then T-012–T-017 (the parsers), then T-025 (acceptance)
**Written:** 2026-08-15

---

## Summary

| | |
|---|---|
| **What exists** | 9 devices × 2 labels × 7 files = **126 fixture files**, all static intents. Perfectly uniform. |
| **What is missing** | **Every template capture.** `PLATFORM_TEMPLATES` has six templates and not one has a fixture. |
| **Can `nettools capture` do it?** | **No — T-011 needs new code.** But less than expected: the path and filename layer already works unchanged. |
| **Labels to produce** | `healthy` and `broken` (OBS-019). `t0`/`t1` are frozen and are **not** extended. |

---

## 1. What exists today

`tests/fixtures/cisco_xr/<device>/<label>/<command-slug>.txt`

Nine devices (`P1 P2 P3 P4 PE1 PE2 PE3 PE4 RR1`) × two labels (`t0`, `t1`) × seven files. Verified uniform: all 18 directories hold the identical filename set.

| File | Intent |
|---|---|
| `show-running-config-hostname.txt` | `facts` |
| `show-version.txt` | `facts` |
| `show-interfaces-brief.txt` | `interfaces` |
| `show-bgp-summary.txt` | `bgp` |
| `show-lldp-neighbors.txt` | `lldp` |
| `show-isis-neighbors.txt` | `isis` |
| `show-segment-routing-traffic-eng-policy.txt` | `sr` |

Six intents, seven commands (`facts` carries two). **Zero template output.**

### Physical shape, from the `t0` fixtures

| Device class | Interfaces | Notes |
|---|---|---|
| PE1–PE4 | `Gi0/0/0/0`, `Gi0/0/0/1`, `Gi0/0/0/2` | `0` and `1` core-facing; **`2` is CE-facing** in a VRF (T-006) |
| P1–P4 | `Gi0/0/0/0` … `Gi0/0/0/4` | core mesh |
| RR1 | `Gi0/0/0/0`, `Gi0/0/0/1` | |

Loopback0 addresses that matter as BGP subjects: `10.255.0.11` PE1, `.12` PE2, `.13` PE3, `.14` PE4, `.31` RR1. P1–P4 run no BGP process.

## 2. The `bgp_session` descent: RR1 → `10.255.0.12`

Per T-023's ladder. **"Device" is the open question** — see §4.

| # | Rung | Check | Reads | Command | Device | Status |
|---|---|---|---|---|---|---|
| 1 | `bgp_session` | `bgp_session_state` | `bgp` intent | `show bgp summary` | RR1 | ✅ exists |
| 2 | `transport` | `bgp_transport` | `bgp_neighbor` template | `show bgp neighbor 10.255.0.12` | RR1 | ❌ **missing** |
| 3 | `route_to_peer` | `route_present` | `route` template | `show route 10.255.0.12/32` | RR1 | ❌ **missing** |
| 4 | `igp_adjacency` | `isis_adjacency` | `isis` intent | `show isis neighbors` | **RR1 or PE2 — Q-013** | ✅ exists on both |
| 5 | `interface` | `interface_state` | `interfaces` intent | `show interfaces brief` | **RR1 or PE2 — Q-013** | ✅ exists on both |
| 5 | `interface` | `interface_state` | `interface` template | `show interfaces <name>` | **RR1 or PE2 — Q-013** | ❌ **missing** |

**Two of five rungs cannot run at all today**, and a third is only half-covered. The blocking gap the LLD names in §4.2 is exactly this: rungs 2 and 3 are pure template reads, and `run_template` attaches no parsed data.

The `t0` ground truth that makes this descent worth testing: RR1 reports `10.255.0.12` **Idle**, PE2 has **zero** IS-IS adjacencies (`show-isis-neighbors.txt` for PE2 at `t0` is empty of adjacency interfaces), and PE2's `Gi0/0/0/0`/`Gi0/0/0/1` exist but carry no adjacency.

## 3. The `interface` descent on a PE

T-022 implements `interface` alongside `bgp_session`, but **the plan never specifies its rungs** — unlike `bgp_session`, which T-023 spells out. What it can read is nevertheless clear:

| Reads | Command | Status |
|---|---|---|
| `interfaces` intent | `show interfaces brief` | ✅ exists |
| `interface` template | `show interfaces GigabitEthernet0/0/0/0` | ❌ **missing** |

`show interfaces brief` gives admin/line state for every interface in one read; the template gives one interface's counters, MTU, description and bandwidth — which is what `interface_state`'s error-counter threshold (Q-005) needs. So the flow plausibly has a state rung on the intent and a counter rung on the template, but **that is inference, not specification**, and T-022 must settle it.

## 4. The device question — why this manifest over-captures deliberately

Rungs 4 and 5 have no unambiguous device. This is Q-013 / OBS-020: below `route_to_peer`, the descent stops being about RR1's local state and starts being about the *path*. For `RR1 → 10.255.0.12`, the fault in `t0` is **PE2's** isolation; RR1's own IS-IS adjacencies are healthy and say nothing about it.

**The manifest does not wait for that decision.** Capture is per-device anyway, so capturing every template on **all nine devices** costs one extra command per device per template and makes the fixture set correct under every candidate answer to Q-013 — subject-device, local-device, path, or two-phase.

This is the cheap direction of the asymmetry. Under-capturing means discovering at T-024 that the walker needs output nobody recorded, and the `broken` label cannot be recaptured without another operator break window. Over-capturing costs a few kilobytes of text.

## 5. The manifest

Captured for **each of `healthy` and `broken`**, complete in one pass per label.

### 5a. Static intents — all 9 devices

Unchanged from what `capture_device` already does. 7 files × 9 devices = **63 files per label**.

### 5b. Templates — all 9 devices

| Template | Parameter | Value(s) | Files/device | Why |
|---|---|---|---|---|
| `bgp_neighbor` | `address` | the **4 other** loopbacks from the set `{10.255.0.11, .12, .13, .14, .31}` | 4 | Rung 2. T-012's parser needs real `Established` **and** `Idle` output |
| `route` | `prefix` | same 4, as `/32` | 4 | Rung 3. T-013 needs both a found and a not-found result |
| `interface` | `interface` | every interface on the device (2–5) | 2–5 | Rung 5 and the whole `interface` flow. T-014 |
| `logging` | `count` | `200` | 1 | T-015. **The mnemonic field** — Stage 2 routing (OBS-013) |
| `ping` | `address` | `10.255.0.31` (RR1 loopback); on RR1, `10.255.0.11` | 1 | T-016. Active probe |
| `traceroute` | `address` | same as `ping` | 1 | T-017. Active probe |

Roughly **13–16 template files per device**, so ~**120–140 files per label**, plus the 63 intent files. Two labels ≈ **370–400 new fixture files**.

Verified renderable — every command below was produced by the real `render_command('cisco_xr', ...)`, and its filename by the real `command_slug`:

```
show bgp neighbor 10.255.0.12          -> show-bgp-neighbor-10-255-0-12.txt
show route 10.255.0.12/32              -> show-route-10-255-0-12-32.txt
show interfaces GigabitEthernet0/0/0/0 -> show-interfaces-gigabitethernet0-0-0-0.txt
show logging last 200                  -> show-logging-last-200.txt
ping 10.255.0.12                       -> ping-10-255-0-12.txt
traceroute 10.255.0.12                 -> traceroute-10-255-0-12.txt
```

### Notes on specific parameters

- **`route` takes a prefix, not an address.** `IPv4PrefixParam` renders `10.255.0.12/32`. A bare `10.255.0.12` is a different template's parameter type; do not mix them up when writing the manifest driver.
- **P1–P4 run no BGP process.** Their `bgp_neighbor` captures will return `% BGP instance 'default' not active` — **capture it anyway.** That is exactly the "command succeeded, content says no" case T-012's parser must classify, and inventing it later would violate the plan's rule against writing parsers against invented output.
- **`ping`/`traceroute` are `active_probe=True`.** They generate traffic and are gated by `NETTOOLS_ALLOW_ACTIVE_PROBES`, which defaults enabled. Capturing them is deliberate: T-016 and T-017 need real output, and both are non-mutating.
- **`traceroute` has a 60s `read_timeout`** and will be the slowest step by far. Nine of them, serially, is minutes — budget for it inside the `broken` window.
- **`logging last 200`** on a fabric whose log corpus is 97% duplicated SSH noise (OBS-014) will mostly capture that noise. Capture it regardless — T-015's job is parsing the line format, and the noise lines are perfectly well-formed examples carrying a real `%MNEMONIC`.

## 6. Can `nettools capture` do this? — **No, but the gap is small**

Reading `src/agent_nettools/fixtures.py`:

**What already works, unchanged:**

- `command_slug(command)` is generic over any command string and produces correct, collision-free names for every rendered template command (verified above, including the `/32` in a prefix and the slashes in an interface name).
- `fixture_path(device, command, label=...)` keys on the *command*, not on an intent, so it already addresses template files correctly.
- `fixture_sender(label=...)` reads by rendered command, and **`run_template()` accepts `sender=`** (`network_tools.py:657`). So replay of template fixtures will work the moment the files exist — no change to the read path at all.

**What does not work:**

`capture_device()` iterates `collect_evidence(device_name)`, which runs only the static intents and returns intent-keyed sections. **There is no code path that runs a template during capture.** It cannot be coaxed into it with arguments; it needs a second loop.

**What T-011 must add** — deliberately narrow:

1. A manifest structure — `(template_name, params)` per device, derived from §5b rather than hardcoded.
2. A capture loop calling `run_template(device, template, **params)` and writing each result through the **existing** `fixture_path`/`command_slug`/`scrub_output` pipeline.
3. A `--label` already exists; a way to select templates (e.g. `--templates`) is new.
4. Honest partial-capture reporting, matching `capture_device`'s existing contract that failures are listed so a partial capture is never mistaken for a complete one.

One caution: `capture_device` uses `collect_evidence`, which is **one SSH session for all seven commands** because IOS-XR rate-limits repeated logins. A naive template loop calling `run_template` once per command would open **13–16 sessions per device**, 9 devices, twice. That is exactly the login pattern the existing design avoids, and it is also the likely source of the `%SECURITY-SSHD_SYSLOG_PRX` churn in OBS-014. T-011 should reuse one session per device, or at minimum be measured before being run against the `broken` window.

## 7. Risks for T-011

| Risk | Mitigation |
|---|---|
| **Silent `.gitignore` exclusion** — ~400 new files, and this project has already lost a directory this way (OBS-002) | `git status --ignored` and assert the expected file count before committing, not `git add`'s exit code |
| **Session storm** (§6) | Reuse one SSH session per device; measure before the break window |
| **Mixed state inside one label** — the OBS-017 failure | Each label captured complete in one pass; do not interleave with the break/restore |
| **`broken` window is one-shot** | The manifest over-captures (§4) so a second window is not needed to answer Q-013 |
| **Fixtures are permanent** | Review by eye before committing; `scrub_output` is a net, not a reviewer |
| **`t0`/`t1` contamination** | Never pass `--label t0`. They are frozen (`tests/fixtures/README.md`) |
