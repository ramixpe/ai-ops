# T-005 — Alertmanager and Prometheus discovery

**Historical task:** T-005 `[NON-BLOCKING]` from the retired build plan (available in Git history)
**Question it answers:** Q-003 — does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger?
**Measured:** 2026-08-15, against the live `sota-lab-platform` stack.

---

## Verdict

**Yes — Alertmanager can be the Stage 2 trigger. n8n is not needed for it.** A webhook receiver already exists and is the default route; deduplication, grouping, inhibition and repeat suppression are all configured and native.

The gap is not the transport. It is that **no current alert rule identifies a device**, so nothing today could tell `investigate()` what to investigate.

Separately, this task turned up something that outranks it — see §5 and OBS-017. **The lab is no longer broken.**

---

## 1. Reachability

Neither service publishes a host port; both are routed on the management bridge.

| Service | Address | Check |
|---|---|---|
| Alertmanager 0.27.0 | `http://172.20.250.105:9093` | `/api/v2/status` → 200 |
| Prometheus v2.51.2 | `http://172.20.250.102:9090` | `/-/ready` → 200 |

## 2. Alertmanager routing — Q-003 answered

```yaml
route:
  receiver: telegram-bot
  group_by: [alertname, component]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h
  routes:
    - receiver: telegram-bot
      matchers: [severity="critical"]
      repeat_interval: 1h
receivers:
  - name: telegram-bot
    webhook_configs:
      - send_resolved: true
        url: <secret>          # redacted by the API; not extracted
```

**A webhook receiver exists.** It is a generic `webhook_configs` receiver — not Alertmanager's native `telegram_configs` — so the payload is the standard Alertmanager webhook JSON (`version`, `groupKey`, `status`, `receiver`, `groupLabels`, `commonLabels`, `commonAnnotations`, `externalURL`, `alerts[]`, each alert carrying `status`, `labels`, `annotations`, `startsAt`, `endsAt`, `generatorURL`, `fingerprint`). `send_resolved: true`, so resolution fires too.

The URL is redacted as `<secret>` by the API and **was deliberately not extracted**. The stack runs a `sota-lab-platform-telegram-bot-1` container listening on `8080/tcp`, which is the evident target: Alertmanager posts a webhook, that service relays to Telegram.

**Inhibition rules** — two, both real:

| Source | Inhibits | Equal on |
|---|---|---|
| `LabCompletelyDown` | `LabRoutersDown` | `component` |
| `LabControllerUnreachable` | `LabFleetGaugesStale` | — |

So the four Stage 2 primitives the plan would otherwise have built — dedupe, group, silence, deliver — are already present and configured. **Q-003: yes.**

### What it cannot do yet

`GET /api/v2/alerts` → **0 alerts**, 0 groups. Nothing is firing, so no live label set could be sampled; the shape below comes from the rule definitions, which are authoritative anyway.

Six rules, all `inactive`:

| Group | Rule | `for` | Labels |
|---|---|---|---|
| lab-availability | `LabRoutersDown` | 300s | `component=lab, severity=critical` |
| lab-availability | `LabCompletelyDown` | 300s | `component=lab, severity=critical` |
| lab-availability | `LabControllerUnreachable` | 300s | `component=control-plane, severity=critical` |
| lab-availability | `LabFleetGaugesStale` | 600s | `component=control-plane, severity=warning` |
| lab-traffic | `TrafficFlowFailed` | 600s | `component=traffic, severity=warning` |
| lab-traffic | `TrafficFlowNotRunningAsDesired` | 900s | `component=traffic, severity=warning` |

**The only label keys any rule sets are `component` and `severity`.** None carries a device, interface, or peer.

This is the real Stage 2 gap. `investigate(device, subject, flow)` needs both a device and a subject; a `LabRoutersDown` webhook supplies neither. Wiring Stage 2 to Alertmanager therefore needs **new alert rules that carry a device label** — which is cheap, because the telemetry underneath them is per-device and already flowing (§3). It is a rule-authoring job, not an infrastructure one.

## 3. Prometheus — what is measurable

570 distinct metric names. 316 are `Cisco_*` — streamed YANG oper data via gNMI, and **all 316 have current samples**, 6,891 samples per scrape.

Three scrape targets, all `up`: `localhost:9090` (prometheus), `gnmic:7890` (`job="xrd-telemetry"`), `lab-controller:8000`.

### The device join key is exact

| Label | Values |
|---|---|
| `source` | **`P1 P2 P3 P4 PE1 PE2 PE3 PE4 RR1`** |
| `host` | `d964ff1e8737` — the gnmic *container* ID, not a device |
| `router_id` | `0.0.0.0`, `10.255.0.11`–`.14`, `10.255.0.31` |

**Use `source`.** It carries the bare device names exactly as `inventory/lab.yaml` and the CLI already spell them — no mapping, no suffix. This is strictly better than Loki, where `host` is `RR1.sota-xrd` and needs translation (see `discovery-loki.md` §2).

### Step 5: the three metrics the plan asked about

| Asked for | Available? | Where |
|---|---|---|
| **BGP session state** | **Yes — as a label, not a metric** | `connection_state="bgp-st-estab"` on the BGP neighbor series, alongside `previous_connection_state`, `peer_reset_reason`, `reset_reason` |
| **Interface oper-state** | **No** | The interface model exposed is `infra_statsd_oper` — counters only. No admin/oper status metric is present anywhere in the 570 |
| **Interface error counters** | **Yes** | `input_errors`, `crc_errors`, `framing_errors_received`, `input_aborts`, `input_overruns`, `input_drops`, `giant_packets_received`, `carrier_transitions`, and output equivalents. **All currently 0 across all nine devices** |

The BGP one deserves emphasis because it is easy to miss: there is no `..._connection_state` *metric*. The session state rides as a **label** on metrics like `..._connection_established_time`. A query has to group by it:

```promql
count by (source, neighbor_address, connection_state) (
  Cisco_IOS_XR_ipv4_bgp_oper:..._neighbors_neighbor_connection_established_time
)
```

`Cisco_IOS_XR_clns_isis_oper:..._neighbor_uptime` gives IS-IS adjacencies, one series per adjacency, labelled with `source` and `interface_name` — so an adjacency count per device is a `count by (source)`.

### A cardinality smell, logged not fixed

A single BGP neighbor series carries **192 labels** — every YANG leaf promoted to a Prometheus label, including free-text fields like `peer_reset_reason` and `reset_reason` that *change value on state transitions*. A label whose value changes creates a new series each time. Sixteen sessions is harmless; this shape does not scale, and it is the standard way a gNMI-to-Prometheus pipeline becomes unwell. Not this build's to fix — recorded in OBS-018.

## 4. Do not read the descent off Prometheus

The plan says to note this and not act on it, and that is the right call. Restating why, because the telemetry is genuinely tempting: it is per-device, structured, already parsed, and free of the SSH noise that ruins the Loki axis.

It is still the wrong source for a rung verdict. Scraped metrics lag by a scrape interval and are subject to staleness; the design's position is that **the device wins on current state** (D8, and `glossary.md` under `operational memory`). A descent that reads a stale "Established" from Prometheus while the session is down produces a confident wrong answer — the exact failure the `unevaluated` discipline exists to prevent.

Where it *is* the right source is corroboration and history: "has this peer flapped this week" is a Prometheus question, and `previous_connection_state` plus `connection_established_time` answer it directly. That is D14 territory, Stage 2, not MVP-0.

## 5. The lab is no longer broken — this outranks everything above

Cross-checking live telemetry against the committed fixture ground truth turned up a discrepancy that affects the build plan directly.

**All 16 BGP sessions are `bgp-st-estab`**, including the two the fixtures and the LLD record as Idle:

```
RR1 -> 10.255.0.12   state=bgp-st-estab   prev=bgp-st-idle
RR1 -> 10.255.0.14   state=bgp-st-estab   prev=bgp-st-idle
```

**Every device's IS-IS adjacency count has changed**, PE2 and PE4 most of all:

| Device | Live | `inventory/lab.yaml` expected |
|---|---:|---:|
| P1 | 5 | 2 |
| P2 | 5 | 4 |
| P3 | 5 | 1 |
| P4 | 5 | 3 |
| PE1 | 2 | 1 |
| **PE2** | **2** | **0** |
| PE3 | 2 | 2 |
| **PE4** | **2** | **0** |
| RR1 | 2 | 1 |

Every adjacency reports ~**47.8 hours** uptime, and the `clab-sota-xrd-*` containers report "Up 2 days". The fabric was rebuilt about two days ago and came up healthy.

Consequences are recorded in OBS-017. In short: **everything fixture-based is unaffected and still green** (567 tests, including `test_health.py` and `test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers`), so **T-025 and the M3 milestone are safe**. What breaks is T-011's instruction to "capture against the current, broken state" — that state no longer exists to capture, and capturing now would put healthy template output alongside broken intent output *under the same fixture label*, which is worse than either. T-007 is where that gets resolved.
