# B-706 Trigger Acceptance Campaign

**Status:** Promoted investigation-only; notification remains disabled  
**Candidate:** `ROUTING-BGP-5-ADJCHANGE` -> `bgp_session`  
**Trigger table:** `src/agent_nettools/data/mnemonics.yaml`

## Decision Rule

Promotion is permitted only when this campaign records **zero losses** across
all controlled `Down` events and every item below holds. A diagnosed loss is
still a failed acceptance run; diagnosis informs remediation, never an
exception to the release bar.

The promoted trigger remains investigation-only. Notification stays disabled
under B-707.

## Why This Candidate

`ROUTING-BGP-5-ADJCHANGE` is the first candidate because it has:

- a direct, validated IPv4 peer extractor;
- a deterministic `bgp_session` descent;
- recovery refusal (`Up` does not route);
- a closed Loki mnemonic query;
- a controlled fault/restore contract for PE2 fault 13.

`PKT_INFRA-LINK-3-UPDOWN` remains a future candidate. It needs equivalent
multi-device restoration contracts before it can be used for this acceptance
campaign.

## Preconditions

- B-717 collector delivery remediation is active: syslog-ng destinations use
  `throttle(10000)`.
- For every injected fault, the injector verifies configuration restoration and
  the declared protocol effect before the next fault begins.
- The event identity/pacing/age gate is active: replay suppression, live event
  budgets, and stale Loki-event refusal run before MCP/model use.
- The campaign uses the consumer path: `event_watch` -> shared routing ->
  `run_event`. Raw Loki queries may diagnose delivery, but cannot constitute
  trigger acceptance evidence.
- Active probes and notifications remain disabled.
- The independent chaos-harness supervisor is armed for every live fault.

## Controlled Corpus

Run at least one BGP `Down` from each of three devices. The final corpus must
cover a single fault and a bounded repeated/replay delivery for the same event
identity.

| Case | Required evidence | Expected result |
|---|---|---|
| PE2 BGP Down | router buffer, syslog-ng file, Loki, event watcher | exactly one routable Down decision |
| Second device BGP Down | same four observations | exactly one routable Down decision |
| Third device BGP Down | same four observations | exactly one routable Down decision |
| Recovery Up | buffer, Loki, event watcher | refusal; no MCP/model call |
| Replay / duplicate | repeated delivery of one identical event ID | replay refusal; no second MCP/model run |
| Unknown device | injected/constructed receiver input | refusal; no MCP/model call |
| Stale event | event older than the configured age policy | refusal; no MCP/model call |

Fault 13 resolves its restore-effect contract by explicit target: PE2, PE3,
and PE4 each declare the RR1 neighbor, settle window, and approved recovery.
PE2, PE3, and PE4 have each passed a live controlled restore proof. PE3's
transient core-interface anomaly was resolved before its renewed preflight;
that preflight showed both core links configured and two IS-IS adjacencies.

## Consumer-Path Receipt Run

The three captured controlled Downs were replayed through the live consumer
path using a process-local `fires: true` override and a deterministic caller;
the reviewed table and persisted environment remained unchanged. The process
used a temporary ticket/admission directory and a 14,400-second age ceiling
only because the captured events were older than the production 300-second
ceiling when the receipt was run.

| Device | Event ID | Collapsed Down occurrences | Result |
|---|---|---:|---|
| PE2 | `e009a5dc36a13531cdca05c6` | 6 | live MCP read completed; ticket `a0c34773a2fe46adb85996e1e84d0ceb` |
| PE3 | `6de89c3fb7e43d89c327742d` | 2 | live MCP read completed; ticket `34acff5a1fa742d58b9ec11d1f0f7d85` |
| PE4 | `dcaf15f68a44d52e302a59cb` | 4 | live MCP read completed; ticket `c23695f584ce46ffb0b65f756d0e06f6` |

The PE4 replay was refused at `event_idempotency` before caller/MCP activity.
An actual PE3 recovery was refused as `transition=up`; a `PE99` receiver input
was refused by inventory validation; and a forged-old Loki ingest timestamp
was refused by the event-age gate. This proves the consumer mechanisms, not
promotion: a production event remains subject to the 300-second age policy and
the reviewed trigger remains `fires: false`.

## Per-Event Receipt

For every controlled Down, retain a receipt containing:

```text
event_id
source_kind
device
mnemonic
transition
device timestamp
ingest timestamp
process timestamp
router-buffer observation
syslog-ng local-file observation
Loki observation
routing decision
run_event outcome
ticket run ID
restore verification result
```

The implementation carries a deterministic `event_id`, raw trigger, routing
transition, ticket provenance, and Loki ingest timestamp. Loki events older
than `NETTOOLS_MAX_EVENT_AGE_SECONDS` (default 300 seconds), or with an
unusable ingest timestamp, are refused before MCP/model activity.

## Acceptance Assertions

For every controlled Down:

1. The event appears in the device buffer, syslog-ng file, and Loki.
2. `event_watch` observes it through its sanctioned query path.
3. The routing decision has `transition="down"`, the expected device, peer,
   flow, and a stable event ID.
4. The first event ID delivery may run. Replays may not create a second live
   MCP/model run or ticket.
5. The model cannot change a pinned identifier. Any collision is refused and
   ticketed.
6. The ticket contains the literal raw triggering event as untrusted text.
7. The injector restores both configuration and declared protocol state before
   the next corpus case.

For every controlled Up, unknown-device, or stale event:

1. A refusal is recorded.
2. No MCP tool is called.
3. No model call is made.
4. No ticket/page is created unless the refusal receipt itself is the explicit
   campaign output.

## Promotion Record

The corpus passed and the following policy field was changed on 2026-08-23:

```yaml
- mnemonic: ROUTING-BGP-5-ADJCHANGE
  trigger:
    fires: true
```

This promotion updated:

- its `reason` and `measured` evidence;
- the acceptance receipt location;
- B-706's authoritative backlog row;
- the implementation-review checklist;
- the consumer-path regression test.

`B-707` notifications are not enabled by this change. The promoted event may
open an investigation ticket through `run_event`; it may not page or execute
remediation.

## Known Open Risks

- The FDB-aging unknown-unicast forwarding loop is identified, but its durable
  platform remediation and post-aging retest remain required before
  notification work.
- D2 transport delivery is zero-loss for the observed isolated and concurrent
  corpus, including the former peer-closing counterexample. A host-bridge pcap
  reports $n=4$, $p50=4.922$ ms and nearest-rank $p95=10.665$ ms for device-clock
  to bridge-arrival timing. The accepted evidence authorizes this investigation
  trigger only; it does not authorize notification.
- The Loki full-retention proof for B-712 was rate-limited by the live service.
- Route and policy identifiers do not yet have B-703 evidence contracts.
- Fault 13 has live B-716 recovery proofs on PE2, PE3, and PE4. The remaining
  B-706 requirement is B-717's zero-loss transport corpus. The previously
  missing RR1 `Peer closing down the session` class now has one successful
  PE2 fault-7 delivery proof, but still needs corpus-level measurement.
