# T-004 — Loki discovery

**Task:** `docs/build/BUILD-PLAN.md` T-004 `[NON-BLOCKING]`
**Question it answers:** Q-002 — is syslog-ng shipping to Loki, and do IOS-XR mnemonics survive into a queryable label?
**Measured:** 2026-08-15, against the live `sota-lab-platform` stack.

---

## Verdict

**Loki is receiving device logs from all nine routers.** The label scheme is documented below and a sample query works.

But three properties make the historical axis much weaker than the plan assumes, and all three matter to T-028 (`correlate`) and to Stage 2:

1. **Only severity `err` and `warning` arrive.** Nothing at `notice` or `informational`. IOS-XR logs BGP and IS-IS adjacency changes at severity 5 (`notice`) — **those events are not in Loki at all.**
2. **Mnemonics survive, but only inside the message body** — not as a label. Routing by mnemonic is possible but needs extraction, not a label selector.
3. **The data is dominated by duplicated, self-generated noise.** In a 2,000-line sample there are **15 distinct messages**; one event is stored 1,346 times. 97% of all lines are the tool's own SSH sessions disconnecting.

---

## 1. Reachability

Loki publishes **no host port** — `docker ps` shows `3100/tcp` unmapped. It is reachable from the host anyway, because both Docker bridges are routed:

| Address | Network | Result |
|---|---|---|
| `http://172.20.250.103:3100` | `sota_mgmt` — **the same /24 as the lab devices** | `/ready` → 200 |
| `http://172.19.0.6:3100` | `sota-lab-platform_labnet` | `/ready` → 200 |
| `http://localhost:3100` | — | connection refused |

```
ip route → 172.20.250.0/24 dev br-081212e31813 src 172.20.250.1
           172.19.0.0/16   dev br-a5b72fa41872 src 172.19.0.1
```

**Use `172.20.250.103`.** It is the management-network address, the same network `nettools` already reaches the devices on, so it needs no new network path. Container-internal callers use `loki:3100` (HTTP) / `loki:9096` (gRPC).

A caveat worth stating: this is a container IP, not a stable service address. It survives a container restart only if Docker reassigns the same address. If `get_logs` is ever wired to this, the URL belongs in an environment variable (`NETTOOLS_LOKI_URL`), never a literal.

## 2. Label scheme

`GET /loki/api/v1/labels` → `host`, `job`, `severity`, `source_ip`. Four labels, no more.

| Label | Cardinality | Values |
|---|---|---|
| `job` | 1 | `sota-routers` |
| `host` | 9 | `P1.sota-xrd` … `PE4.sota-xrd`, `RR1.sota-xrd` |
| `source_ip` | 9 | `172.20.250.11`–`.14`, `.21`–`.24`, `.31` |
| `severity` | 2 | `err`, `warning` |

**All nine devices are present, under both `host` and `source_ip`.**

### The join key — `source_ip` is the better one

`nettools` names devices `RR1`; Loki names them `RR1.sota-xrd`. The `.sota-xrd` suffix is applied by syslog-ng, which hardcodes a rewrite rule per management IP:

```
set("RR1.sota-xrd", value("HOST"), condition(netmask("172.20.250.31/32")));
```

So the mapping is deterministic and total — but it is a **string convention maintained in a config file this repository does not own.** `source_ip` joins directly to `inventory/lab.yaml`'s existing `mgmt_ip` field with no string manipulation and no shared convention to keep in step. Prefer it; fall back to `host` only for human-readable output.

## 3. Sample query

```bash
curl -sG http://172.20.250.103:3100/loki/api/v1/query_range \
  --data-urlencode '{host="RR1.sota-xrd"}' \
  --data-urlencode "start=<ns>" --data-urlencode "end=<ns>" --data-urlencode "limit=5"
```

Stream labels and one line, verbatim:

```json
{"host":"RR1.sota-xrd","job":"sota-routers","severity":"err","source_ip":"172.20.250.31"}
```
```
RR1.sota-xrd RP/0/RP0/CPU0:Aug 15 13:50:46.954 UTC: ssh_syslog_proxy[1191]:
%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL : sshd[148258]: Read error from remote
host 172.20.250.2 port 51530: Connection reset by peer
```

### Two timestamps, and only one of them is the event time

syslog-ng sets `timestamp("current")` on the Loki destination, so **Loki's timestamp is ingest time, not event time.** The device's own timestamp (`Aug 15 13:50:46.954 UTC`) is inside the message body.

This is load-bearing for T-028. Correlating "the interface went down four minutes after a commit" against the ingest timestamp measures when syslog-ng got round to it, not when the router observed it. The `logging` parser (T-015) must extract the in-body timestamp, and correlation must use that.

## 4. Pipeline: syslog-ng writes to a file **and** ships to Loki

`sota-lab-platform-syslog-ng-1` (`balabit/syslog-ng:4.5.0`, healthy, up 11 days), listening on `514/udp`, `601/tcp`, `6514/tcp`, bound to `172.20.250.101`.

Both destinations sit on one log path, so they receive identical content:

| Destination | Detail |
|---|---|
| `d_file` | `/var/log/syslog-ng/sota-routers.log`, `throttle(200)`, **no rotation configured** |
| `d_loki` | native driver to `loki:9096` (gRPC), `timestamp("current")`, `throttle(200)`, `batch-lines(10)` |

An ingress filter accepts only the nine management addresses, so no other container on the management network can enter the pipeline. Labels are set exactly as observed in §2, with `severity` taken from `${LEVEL}` — the syslog PRI severity, *not* the digit inside the IOS-XR mnemonic.

One inconsistency, harmless but worth noting: the config's own header comment says "forwards to Loki via HTTP push API", while the destination it actually declares is the native gRPC driver on `:9096`.

### Retention windows differ by an order of magnitude

| Store | Window | Size |
|---|---|---|
| Loki | `retention_period: 168h` (**7 days**), compactor + table_manager both enabled | — |
| File | **2026-05-13 → 2026-08-15**, ~3 months, unrotated | **407 MiB** |

## 5. Mnemonics — Q-002 answered

**Every line carries one.** Across 1,219 lines sampled over 7 days, lines with no `%MNEMONIC`: **0**. The regex `%([A-Z0-9_\-]+-\d-[A-Z0-9_]+)` matched all of them.

**They are in the message body, not a label.** There is no `mnemonic` label and syslog-ng does not parse one out.

So Stage 2's mnemonic → flow routing can still be a lookup rather than a model judgement — the identifier is present and reliably formatted — but it needs an extraction step first. Three options, in preference order:

1. **Parse it out of the retrieved line** with the T-015 `logging` parser. No change to infrastructure this repository does not own, and the parser is being written anyway.
2. **Extract at query time** with LogQL (`| regexp "%(?P<mnemonic>...)"`). Keeps it server-side; ties the code to LogQL.
3. **Promote it to a label in syslog-ng.** Cheapest to query, but it changes a shared platform config and adds label cardinality — and cardinality is the standard way to make a Loki install unwell.

### What is actually in there

Three distinct mnemonics in seven days:

| Count | Mnemonic | Severity |
|---:|---|---|
| 1,187 | `%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL` | 3 |
| 31 | `%ROUTING-FIB-4-RETRYDB_NONEMPTY` | 4 |
| 1 | `%ROUTING-ISIS-3-FLEX_ALGO_DEF_CHANGED` | 3 |

Per host: RR1 150, PE1 119, PE2 131, PE3 239, PE4 122, P1 122, P2 127, P3 85, P4 124.

## 6. Three problems the plan should know about

### 6.1 The severity floor hides exactly the events the descent cares about

Severities observed: **3 and 4 only.** Nothing at 5 or 6, across nine routers over seven days.

syslog-ng applies no severity filter — the config filters on source address only — so the floor is upstream, on the devices. The most likely cause is the routers' own `logging trap` level being set to `warning` or higher. **I did not confirm this**, because doing so needs an SSH session with device credentials and T-004 does not sanction one; T-006 does, and can settle it in a single extra command while a session is already open.

Either way the consequence for MVP-0 stands: `%BGP-5-ADJCHANGE` and IS-IS adjacency transitions are severity 5, so **the events T-028 wants to correlate against are not in Loki today.**

### 6.2 Duplication makes any event count meaningless

In the last 2,000 lines of the file there are **15 distinct messages**. A single PE2 event, device timestamp `13:51:17.506`, appears **1,346 times** with ingest timestamps spread across several seconds.

The device timestamp is identical across all copies, so this is one event re-delivered, not a recurring event. Any consumer that counts occurrences — "this interface flapped fourteen times" is precisely the D14 use case — **must deduplicate on (device, device-timestamp, message)** before counting, or it will report a storm that never happened.

### 6.3 97% of the corpus is the tool's own footprint

`%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL` is an SSH read error, and the peer in every instance is `172.20.250.2` — the host running the polling. These are netmiko sessions closing abruptly and the routers logging it, at severity 3, once per collection, then re-delivered hundreds of times.

The observability pipeline is mostly observing the observer. Not this build's problem to fix, but it means the historical axis is close to empty of signal in its current state.

### 6.4 Flow is intermittent

Last line ingested `13:51:26`; measured at `17:00:38` — **no logs for over three hours.** Daily counts from Loki: today 187, yesterday 1,032, and **zero on each of the five days before that**. The file destination grew 0 bytes over a 10-second observation.

## 7. What this means for the plan

| Task | Consequence |
|---|---|
| **T-015** (`logging` parser) | Must extract the **in-body** device timestamp and the mnemonic. Both formats confirmed above. Cross-check the mnemonic regex against the three real samples. |
| **T-028** (`correlate` prompt) | **The premise is weak today.** The severity floor means adjacency-change events are absent, duplication corrupts counts, and there is no traffic for hours at a time. The plan's own fallback — use `show logging` template output instead — is the right call, and the finding records why. |
| **Stage 2 routing** | Still viable as a lookup. The mnemonic is present on 100% of lines and reliably formatted; it needs extraction, not judgement. |
| **`get_logs` (MVP-1)** | Use `source_ip` as the join key, `NETTOOLS_LOKI_URL` for the endpoint, dedupe on device timestamp, and treat Loki's timestamp as ingest-only. |
