# T-006 — L3VPN discovery

**Task:** `docs/build/BUILD-PLAN.md` T-006
**Question it answers:** Q-004 — what is the subject naming scheme for an L3VPN service object?
**Measured:** 2026-08-15, read-only SSH to PE1–PE4.

> `l3vpn_service` is **not in MVP-0.** Nothing here is implemented. This exists so the flow registry's shape at T-022 is informed by the real fabric rather than invented, and so the naming decision is made against evidence.

**Method.** Read-only `show` commands over a manual netmiko session, as T-006 sanctions. **No command was added to the allowlist**, no code was changed, and the probe script lives in the scratchpad, uncommitted. Credentials came from `.env` via dotenv and were never placed on a command line.

---

## 1. The VRF / RT map

Three VRFs across four PEs. One AS, `65000`, throughout.

| VRF | RD | Import RT | Export RT | On | Address families |
|---|---|---|---|---|---|
| **CUSTA** | `65000:100` | `65000:100` | `65000:100` | PE1, PE3 | IPv4 + IPv6 |
| **CUSTB** | `65000:200` | `65000:200` | `65000:200` | PE2, PE4 | IPv4 + IPv6 |
| **SHARED-SVCS** | `65000:300` | `65000:100`, `65000:300` | `65000:100`, `65000:300` | **PE1 only** | IPv4 only |

A plain symmetric hub-less design for the two customer VRFs — import RT equals export RT equals the VRF's own value — plus one deliberate asymmetry.

### `SHARED-SVCS` is a route-leaking demo

PE1's config carries its own explanatory comment:

```
! T4-3: VRF route-leaking demo — SHARED-SVCS VRF (PE1 only)
! Leak path: CUSTA (RT 65000:100) <-> SHARED-SVCS (RT 65000:300)
! Verify on PE3: show route vrf CUSTA 172.30.30.1
```

It imports *and* exports both `65000:100` and `65000:300`, so CUSTA and SHARED-SVCS leak bidirectionally. Its only interface is `Loopback150` = **`172.30.30.1/32`**, which should therefore be reachable from CUSTA on PE3 — a PE that does not carry SHARED-SVCS at all.

That makes it the most interesting object in the fabric for a future flow: **a service whose reachability depends on RT policy rather than on the protocol stack.** The `bgp_session` descent's ladder — transport, route, IGP, interface — would find every rung healthy and still not explain a leak failure, because the fault would live in RT import/export. Worth remembering when the `l3vpn_service` descent is designed; it is not simply the BGP ladder with a VRF attached.

Note also the asymmetry: SHARED-SVCS is IPv4-only, while CUSTA and CUSTB carry both families. Any flow must not assume a VRF has both.

## 2. Per-PE addressing

`GigabitEthernet0/0/0/2` is the CE-facing interface on all four PEs, addressed as a /31.

| PE | VRF | Loopback100 | CE-facing /31 | Extra |
|---|---|---|---|---|
| PE1 | CUSTA | `172.16.100.1/32` | `172.16.10.0/31` | `Loopback150` `172.30.30.1/32` in SHARED-SVCS |
| PE2 | CUSTB | `172.16.200.2/32` | `172.16.20.0/31` | `BVI200` `172.20.200.2/24` |
| PE3 | CUSTA | `172.16.100.3/32` | `172.16.10.2/31` | — |
| PE4 | CUSTB | `172.16.200.4/32` | `172.16.20.2/31` | `BVI200` `172.20.200.4/24` |

`Loopback100`'s last octet is the PE number, and the second octet group encodes the VRF (`100` = CUSTA, `200` = CUSTB). Convenient, and exactly the kind of pattern a model would be tempted to extrapolate from — see §4.

`BVI200` on PE2 and PE4 puts both in a shared `172.20.200.0/24` L2 segment inside CUSTB; `address-family l2vpn evpn` is configured on the PEs. Out of scope here, noted so it is not a surprise later.

## 3. CE attachment — the crossing is real, and confirmed

**The PEs record nothing about which CE is attached.** No interface descriptions, no naming convention on the link. The attachment is only derivable from the /31 pairing — which is deterministic, and which code can compute.

Verified from both ends:

| CE | CE address | Pairs with | PE | VRF |
|---|---|---|---|---|
| CE1 | `172.16.10.1/31` | `172.16.10.0/31` | **PE1** | CUSTA |
| CE2 | `172.16.10.3/31` | `172.16.10.2/31` | **PE3** | CUSTA |
| CE3 | `172.16.20.1/31` | `172.16.20.0/31` | **PE2** | CUSTB |
| CE4 | `172.16.20.3/31` | `172.16.20.2/31` | **PE4** | CUSTB |

**This matches the documented crossing exactly: CE1→PE1, CE2→PE3, CE3→PE2, CE4→PE4.**

So the services are:

- **CUSTA** = PE1 + PE3, serving **CE1 and CE2**
- **CUSTB** = PE2 + PE4, serving **CE3 and CE4**

### Why this matters more than it looks

`design-thinking.md` D10 predicted precisely this: *"In this fabric, where CE numbering is deliberately crossed — CE2 attaches to PE3, CE3 to PE2 — a name-pattern-matching model gets two of four services wrong."*

The fabric confirms the prediction, and adds a sharper point. A model asked "which PE serves CE2?" has three tempting wrong signals: the numeric match (CE2→PE2), the `Loopback100` last-octet convention, and the VRF's own numbering. All three agree with each other and all three are wrong. There is no textual clue anywhere on PE3 that CE2 is its customer.

The only reliable derivation is arithmetic on the /31 — pair `172.16.10.3` with `172.16.10.2`. That is code's job, and it is why D10's rule holds: **the model names a scope; code resolves the attachment.** This fabric would punish any other arrangement immediately, twice out of four.

## 4. Subject naming — Q-004

The plan offers three candidates. Measured against this fabric:

### ✗ `<vrf>:<rd>` — **eliminated by evidence**

**RD is not unique across PEs here.** PE1 and PE3 both use `65000:100` for CUSTA; PE2 and PE4 both use `65000:200` for CUSTB. So `CUSTA:65000:100` names *two different VRF instances on two different routers* and cannot tell them apart.

This is legal configuration — a type-0 RD reused fabric-wide is common, if not the practice that keeps VPNv4 best-path selection cleanest — but it disqualifies the scheme outright. A subject identifier that silently conflates two devices is worse than a verbose one. The three-colon spelling is also ambiguous to parse.

### ~ `<vrf-name>` — right granularity for the service, wrong for an investigation

`CUSTA` names the service as a customer thinks of it, and a customer-facing question ("is CUSTA up?") is genuinely fabric-wide.

But it carries no device, and every collect step underneath needs one. It would force the walker to resolve "which PEs carry CUSTA" at every rung, and it cannot express the common real question — "CUSTA is broken *at PE3*".

### ✓ `<pe>:<vrf>` — **recommended**

`PE1:CUSTA`, `PE3:CUSTA`, `PE1:SHARED-SVCS`.

1. **Unique across the fabric**, which `<vrf>:<rd>` is not.
2. **It already matches the repository's own convention.** `glossary.md` keys operational memory by object as `PE2:GigabitEthernet0/0/0/1` and `PE2:bgp:10.255.0.31` — device first, object second. `PE1:CUSTA` is the same shape, so a future D14 event store needs no second convention.
3. **It carries the device the collect steps need**, so no rung has to re-resolve scope.
4. It expresses the asymmetric case honestly: `PE1:SHARED-SVCS` exists and `PE3:SHARED-SVCS` does not, which is the truth.

**The fabric-wide question becomes a fan-out**, not a different naming scheme: "is CUSTA healthy?" resolves — in code, from the inventory and config — to `{PE1:CUSTA, PE3:CUSTA}`, and the flow runs per instance. That keeps subject resolution deterministic and keeps the model naming a scope rather than asserting membership, which is D10 again.

**Recommended, not implemented.** T-022 fixes the registry shape and is where this is decided; `l3vpn_service` remains a `NotImplementedError` stub for MVP-0.

### One thing the scheme does not solve

A CE-oriented question — "why can't CE2 reach CE1?" — names neither a PE nor a VRF. Answering it needs the /31 arithmetic in §3 to resolve `CE2 → PE3:CUSTA` first. That resolution step belongs in code beside the flow registry, not in the naming scheme, and it is the same mechanism D10 calls scope resolution. Worth building deliberately, because as §3 shows, this is exactly where a plausible guess is wrong half the time.

## 5. Also observed

- `router bgp 65000` on PE1 carries `ipv4 unicast`, `vpnv4 unicast`, `ipv6 unicast`, `vpnv6 unicast` and `l2vpn evpn` address families; the RR neighbour `10.255.0.31` uses `update-source Loopback0`, `route-policy PASS in/out`, and BFD (`fast-detect`, multiplier 3, min-interval 100ms).
- PE4 has **no** `router-id`/`local_as` in `inventory/lab.yaml` yet carries VRF CUSTB and an active BGP session — consistent with the inventory's documented rule of recording those as *absent* rather than zero, and unaffected by the rebuild.
- No interface on any PE carries a `description`. Nothing in the fabric self-documents its CE attachment.
