# B-717 Syslog Duplication Handoff

**Date:** 2026-08-23  
**Status:** Collector-loss remediation validated; FDB-aging forwarding-loop root cause identified  
**Scope:** PE2 IOS-XR syslog delivery to syslog-ng/Loki in the SOTA lab

## Executive Summary

A controlled BGP max-prefix fault proved that PE2 records the BGP `Down` event
in its local IOS-XR buffer and emits at least one matching UDP syslog datagram
onto the management bridge. Before remediation, syslog-ng's local file and Loki
persisted only the later `Up` event. Packet capture showed the `Up` event was
replicated thousands of times while the `Down` appeared once:

```text
BGP Down packets on bridge: 1
BGP Up packets on bridge:   4693
```

The duplicate Up storm exceeded syslog-ng's configured `200` messages/second
throttle at both the local-file and Loki destinations. The valid Down packet
therefore crossed the bridge but was not persisted. The source of the packet
duplication is still unresolved; the collector throttle was the demonstrated
loss mechanism.

## Topology

```text
PE2 (172.20.250.22)
  UDP source port 514
      |
      | Docker management bridge: br-081212e31813 / 172.20.250.0/24
      |
      v
syslog-ng (172.20.250.101:514)
  local file: /var/log/syslog-ng/sota-routers.log
  Loki destination: loki:9096
      |
      v
Loki (172.20.250.103)
```

The syslog-ng container is dual-homed:

```text
172.20.250.101 on the router management network
172.19.0.9 on labnet
```

PE2 has one management address on the router network:

```text
172.20.250.22
```

## Controlled Fault Procedure

The fault was `faultlab/fault_lab.py` option 13:

```text
router bgp 65000
 neighbor 10.255.0.31
  address-family vpnv4 unicast
   maximum-prefix 1
```

Expected effect: the RR1 BGP session transitions from `Established` to `Idle`
and IOS-XR emits `ROUTING-BGP-5-ADJCHANGE Down`.

Restore removes `maximum-prefix`. Fault 13 now also declares a B-716
protocol-effect contract:

```text
verify: show bgp neighbor 10.255.0.31 -> BGP state = Established
approved recovery: clear bgp 10.255.0.31
```

The first live validation discovered that `clear bgp neighbor <peer>` is
invalid IOS-XR syntax. IOS-XR CLI help established the correct command above.
A repeat validated:

```text
Idle -> config restored -> clear bgp 10.255.0.31 -> Established
RESTORE_VERIFIED=True
```

No fault remains active at handoff time.

## Evidence Timeline

### Pre-remediation controlled run

PE2 local logging buffer contained:

```text
Aug 23 16:18:34.310 UTC
%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Down
Peer exceeding maximum prefix limit
```

A later recovery occurred:

```text
Aug 23 16:19:19.769 UTC
%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Up
```

A root-owned packet capture was taken on the host bridge:

```bash
sudo tcpdump -ni br-081212e31813 -s 0 -w /tmp/pe2-syslog.pcap \
  'udp and src host 172.20.250.22 and dst host 172.20.250.101 and dst port 514'
```

Offline pcap analysis:

```bash
sudo tcpdump -nn -A -r /tmp/pe2-syslog.pcap
```

Results:

```text
Down packets: 1
Up packets:   4693
```

The first Down packet payload was present on the bridge:

```text
<189>... PE2.sota-xrd RP/0/RP0/CPU0:Aug 23 16:18:34.310 UTC:
bgp[1084]: %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Down
- Peer exceeding maximum prefix limit ...
```

The Up packet payload was also present:

```text
<189>... PE2.sota-xrd RP/0/RP0/CPU0:Aug 23 16:19:19.769 UTC:
bgp[1084]: %ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Up ...
```

### Collector persistence before remediation

syslog-ng local file contained repeated Up messages but no matching Down:

```text
/var/log/syslog-ng/sota-routers.log
  Up at 16:19:19.769: present repeatedly
  Down at 16:18:34.310: absent
```

The closed-set Loki query also returned only Up events:

```python
run_named_query(
    "logs_for_device_mnemonic",
    device="PE2",
    mnemonic="ROUTING-BGP-5-ADJCHANGE",
    since_seconds=1800,
    limit=1000,
)
```

The host and collector counters did not report packet drops:

```text
Host /proc/net/snmp:
  InErrors=0
  RcvbufErrors=0

syslog-ng:
  source s_net_udp: processed >10M
  d_loki dropped=0
  d_loki queued=0
  d_loki written >10M
```

These counters do not contradict the observed loss: the packet was accepted
at the bridge, then a destination throttle discarded/paced messages under a
much larger duplicate flood.

## Duplication Findings

### What is proven

1. PE2 emits syslog UDP datagrams to the correct destination:

   ```text
   172.20.250.22:514 -> 172.20.250.101:514
   ```

2. The BGP Down datagram crossed the Docker management bridge once.

3. The later Up datagram crossed the same bridge 4,693 times.

4. The repeated copies showed varying TTL values in the live terminal capture.
   They are not a single tcpdump rendering artifact.

5. syslog-ng persisted repeated Up packets but not the valid Down packet.

6. PE2's buffer also reports repeated internal queue pressure:

   ```text
   %PKT_INFRA-PQMON-6-QUEUE_DROP : Taildrop on XIPC queue 4 owned by ipv4_io
   ```

### What is not proven

1. The origin of the 4,693 Up copies is not yet identified.

2. It is not yet known whether duplication occurs entirely inside XRd/IOS-XR,
   inside containerlab/Docker networking, or through a network topology loop.

3. The original collector throttle was proven capable of losing the Down under
   the flood, but it was not the source of the duplicate packets.

4. The post-remediation validation outcome has not yet been recorded in this
   handoff. It must be completed before closing B-717.

## Collector Configuration Before Remediation

Durable source file:

```text
/home/rami/network_lab/infra/log-server/syslog-ng/syslog-ng.conf
```

Relevant pre-remediation configuration:

```conf
destination d_file {
    file(
        "/var/log/syslog-ng/sota-routers.log"
        template("${ISODATE} ${MSGHDR}${MSG}\n")
        create-dirs(yes)
        throttle(200)
    );
};

destination d_loki {
    loki(
        url("loki:9096")
        workers(2)
        batch-lines(10)
        batch-timeout(1000)
        throttle(200)
    );
};
```

A 4,693-packet burst cannot be fully delivered through a 200 messages/second
output path. The local file and Loki destination shared this throttle shape,
which explains why both missed the same valid Down message.

## Remediation Applied

The durable syslog-ng configuration was changed to:

```conf
destination d_file { throttle(10000); };
destination d_loki { throttle(10000); };
```

Only the `syslog-ng` Compose service was recreated:

```bash
cd /home/rami/network_lab
docker compose up -d --force-recreate syslog-ng
```

The new container became healthy and its live mounted config showed both
throttles at `10000`.

A post-remediation bounded fault was run at:

```text
Down: Aug 23 16:26:21.596 UTC
```

PE2 was restored successfully:

```text
RESTORE_VERIFIED=True
BGP state = Established
```

## Post-Remediation Acceptance: Passed

After raising both syslog-ng destination throttles from `200` to `10000`
messages/second and recreating only the collector, the bounded fault at
`16:26:21.596 UTC` was observed in every required location:

```text
PE2 buffer:
  ROUTING-BGP-5-ADJCHANGE Down at 16:26:21.596

syslog-ng local file:
  2026-08-23T16:26:21+00:00 ... Down at 16:26:21.596

Loki closed mnemonic query:
  Aug 23 16:26:21.596 UTC | neighbor 10.255.0.31 Down ...
  Aug 23 16:26:54.849 UTC | neighbor 10.255.0.31 Up ...
```

PE2's automatic B-716 recovery again completed with `RESTORE_VERIFIED=True`
and the BGP FSM returned to `Established`.

This validates the collector throttle remediation for B-717's delivery gate.
It does **not** explain or resolve the upstream Up duplication.

## Peer-Closing Class Reproduction: Delivered

The known missing RR1 message class was reproduced with fault 7 on PE2:
`remote-as 65001` plus `ebgp-multihop 5` toward RR1. Historical sealed truth
links the earlier missing event at `10:16:54.385` to that exact fault. Fault 7
now has a PE2-only B-716 restore-effect contract; its repeat restored config
and the BGP FSM on the first attempt using only `clear bgp 10.255.0.31`.

The controlled RR1 event was observed at every hop:

```text
RR1 buffer:      17:46:11.891 Down - Peer closing down the session
syslog-ng file:  17:46:11.891 same literal event
Loki:            17:46:11.891 same literal event
Loki window:     records_before_dedup=3, duplicates_removed=0
```

This closes the known 100%-lost message-class counterexample. It does not
close B-717: the D2/Peer A requirement remains a measured three-device,
zero-unexplained-loss corpus, including isolated and concurrent cases plus
latency percentiles.

## D2 Concurrent Corpus: Zero Loss, Timing Precision Pending

Three independent fault-13 harnesses applied `maximum-prefix 1` concurrently
to PE2, PE3, and PE4. Their config applies completed within 9 ms of one
another; each held for 15 seconds and completed config plus BGP-FSM restore on
its first attempt. The burst generated six expected Down events: one local
maximum-prefix Down on each PE and the three corresponding RR1 notification
Downs.

All six were observed in every delivery stage:

```text
router buffers:  6 / 6
syslog-ng file:  6 / 6
Loki:            6 / 6, records_before_dedup=6, duplicates_removed=0
```

The concurrent burst therefore has zero observed losses. A fresh isolated PE2
Down also completed `event_watch -> run_event -> live MCP` under the normal
300-second event-age policy, with temporary receipt ticket
`cf6983b2718f49638de46a64abfe689a`; no persistent trigger or notification was
enabled.

The seconds-only Loki ingest values are not used for latency. A host-bridge
pcap then captured the PE2 timing probe with eight packets, zero kernel drops,
and one copy of each BGP transition. Device-clock to bridge-arrival samples:

```text
PE2 Down: 10.665 ms     RR1 Down: 4.593 ms
PE2 Up:    4.861 ms     RR1 Up:   4.984 ms
n=4, p50=4.922 ms, nearest-rank p95=10.665 ms
```

This is transport ingress latency, not an invented end-to-end persistence
latency: collector and Loki persistence are separately proven above. The
temporary seconds-only collector audit destination was removed after proving it
could not improve precision. D2's zero-loss and separately reported timing
requirements are now met; B-717 awaits review-ledger acceptance and
authoritative backlog reconciliation.

## Remaining Duplicate Investigation

### Root cause established by follow-up investigation

The duplicate source is an FDB-aging unknown-unicast forwarding loop:

1. syslog-ng's MAC ages out of the Docker bridge FDB.
2. PE2's next UDP syslog packet becomes unknown unicast and floods across
  management ports.
3. XRd nodes route the flooded packet back toward `172.20.250.101`, changing
  Ethernet source MAC and decrementing IP TTL.
4. Those copies flood again, causing exponential amplification.

The same IP ID and payload appeared first with PE2's source MAC and TTL 255,
then with other XRd management MACs at TTL 254, followed by later copies at
TTL 253 and lower. The first 10,000 observed copies arrived in roughly 46 ms.
CE MACs did not participate. PE2's ARP entry for syslog-ng remained valid
while the syslog-ng MAC was absent from the bridge FDB; one diagnostic ping
restored that MAC to its correct veth.

This excludes duplicate emission by PE2, duplicate router attachments, and a
tcpdump rendering artifact. The durable platform remediation remains pending:
apply unknown-unicast flooding policy to dynamically discovered XRd-facing
ports, reapply it after container recreation using endpoint identity rather
than veth names, and retest after more than 300 seconds of aging plus a
syslog-ng recreation. Do not globally disable FDB aging.

### Locate the duplicate emitter

Use pcap output without hex dumps and save a bounded sample:

```bash
sudo timeout 30 tcpdump -ni br-081212e31813 -s 0 -A \
  'udp and src host 172.20.250.22 and dst host 172.20.250.101 and dst port 514' \
  > /tmp/pe2-syslog-sample.txt
```

Then count normalized message bodies, timestamps, IP IDs, and TTLs. Important
questions:

- Does one IOS-XR event timestamp appear in many distinct UDP datagrams?
- Are repeated copies generated with different IP IDs or only observed via
  bridge replication?
- Does the PE2 XRd container have multiple paths or interfaces into
  `172.20.250.0/24`?
- Does containerlab attach duplicate management links or create an L2 loop?

Useful read-only platform inspection:

```bash
docker inspect clab-sota-xrd-PE2 sota-lab-platform-syslog-ng-1
docker network inspect <management-network-id>
bridge link show master br-081212e31813
bridge fdb show br br-081212e31813
ip route get 172.20.250.101
```

### Treat queue-drop messages as a separate symptom

PE2 repeatedly logs:

```text
PKT_INFRA-PQMON-6-QUEUE_DROP : Taildrop on XIPC queue 4 owned by ipv4_io
```

Determine whether the queue taildrops correlate with duplicate syslog bursts
or are merely a consequence of frequent SSH collection. Avoid assuming they
are the same failure without a timestamped correlation.

## Safety Notes

- Fault 13 uses a validated restore contract. It may be used for controlled
  reproduction.
- Restoration requires config comparison plus BGP FSM `Established`.
- The only allowed automatic recovery for fault 13 is exactly:

  ```text
  clear bgp 10.255.0.31
  ```

- All other fault definitions are now fail-closed until each declares its own
  protocol-effect verification contract.
- No event trigger has been promoted and no notification is enabled.

## Related Backlog Gates

```text
B-717: transport fidelity -- still open
B-706: trigger promotion -- blocked by B-717
B-707: notification -- blocked by B-706 and event delivery/idempotency gates
B-716: fault 13 contract complete; other fault contracts remain
B-710: two-provider adversarial pinning measurement complete
```
