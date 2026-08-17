# Operational Review — Would I Use This at 3am?

## 1. Would you use it?

Not yet as a tool whose answer I trust. I would run it in shadow mode on BGP-down alerts and compare it with what the on-call engineer found, but I would not let it page, close an alert, select an escalation team, or drive a change. On the narrow case where monitoring has already given me the local device and peer address, it could save me several logins and produce a useful first-pass ladder. Today, however, an experienced operator can usually run `show bgp neighbor`, check the last reset reason, verify the route and inspect the relevant interface in less than the roughly 103–122 seconds these trials took. If the tool then reports only `transport_blocked`, or points to an unrelated down interface, it has cost time rather than saved it. My answer changes when it proves that it identifies the correct device and cause—not merely the correct layer—across faults selected by people who did not design the ladder, and when its result arrives fast enough to affect the next action.

## 2. Where it helps

### It is a useful second step when the first step has already been done

If Alertmanager says:

```text
RR1 — neighbor 10.255.0.12 — BGP session down
```

then `investigate RR1 10.255.0.12` is a reasonable interface. The question is specific, the identifiers already exist, and the output follows the checks an operator would perform anyway.

That is not how many incidents arrive. They arrive as:

- “CE2 cannot reach a service in CUSTA”;
- “VPN traffic is intermittent in one direction”;
- “three sites disappeared after a maintenance window”;
- “latency rose, then several BGP peers reset”;
- “the customer sees loss but all NMS sessions are green.”

In those incidents, naming the correct device, VRF, peer, address family, and direction is much of the work. This tool does not do that part. It assumes the operator has already converted a service symptom into a BGP-session question.

That is acceptable if it is described honestly as a BGP-session diagnostic command. It is not an incident-triage entry point.

### It gathers evidence across devices consistently

The most useful operational behavior is that it does not stop at “BGP is Idle.” It checks beneath the symptom and can move from the route reflector to the device that owns the peer address. On an unfamiliar fabric, or for a less experienced primary on-call, that can prevent the common failure where someone spends twenty minutes staring at the RR while the actual problem is on the PE.

The full rung table is more useful than a one-line conclusion. I can see what it checked, what passed, what failed, and where it stopped. That makes the answer challengeable rather than magical.

### Read-only operation matters

I would be comfortable letting a junior operator run this command because it cannot enter configuration mode. That removes one source of 3am hesitation: “Is this diagnostic command going to change something?”

The practical benefit is not architectural elegance. It is that I can put the command in a runbook without adding an approval step merely to collect evidence.

### `undetermined` is the right failure direction

I would rather receive:

```text
undetermined — route detail did not parse
```

than a guessed interface cause. A refusal preserves the option to continue manually. A confident wrong answer sends the investigation down the wrong branch and is harder to recover from because people anchor on the first plausible explanation they see.

The honesty is worth the friction if the refusal says exactly which check failed and the refusal rate stays low. If the tool takes two minutes and frequently returns `undetermined` because a parser does not recognize a harmless IOS-XR output variation, I will stop running it. A refusal caused by genuinely missing evidence is useful. A refusal caused by routine tool brittleness is merely a slow error message.

### The team has treated observed failures seriously

The false-positive trial is documented instead of being hidden, and the resulting behavior was changed so an established BGP session with an unrelated down link no longer produces a path fault. Operationally, that matters: it shows the team is willing to preserve embarrassing results and correct the machine-consumed answer, not just explain the mistake in prose.

Four trials still do not create trust, but this is the right behavior for earning it.

## 3. Where it would waste your time

### It starts after the hardest scoping decision

For a clean `BGPNeighborDown` alert with device and peer labels, the entry point is fine. For a customer or service incident, the hard work is usually identifying which control-plane object is relevant. The tool cannot replace:

```text
customer symptom
  → affected service and direction
  → ingress and egress PE
  → VRF / address family
  → expected route and label path
  → relevant BGP session, if BGP is actually the problem
```

By the time I can supply the exact device and peer, I may already have enough context to run the five checks myself.

### Two minutes is long for the amount of information returned

The recorded live runs took 114.2, 121.7, 118.8 and 113.7 seconds, with a 103-second healthy baseline. That is too slow for an interactive command that answers one session question.

At 3am, two minutes is not inherently unacceptable. Waiting two minutes for a complete cross-device evidence bundle could be worthwhile. Waiting two minutes to learn “transport blocked” when `show bgp neighbor` already contains a reset reason is not.

I would run this asynchronously while continuing the incident. I would not sit at the prompt waiting for it before taking the next diagnostic step.

### A layer is not always an actionable cause

`transport_blocked` tells me where the ladder stopped. It does not tell me whether the cause is:

- an ACL;
- control-plane policing;
- MD5 mismatch;
- TTL security;
- wrong update source;
- wrong remote AS;
- a remote administrative shutdown;
- BGP process trouble;
- packet loss severe enough to prevent establishment.

Those have different owners and different next actions. If the tool reports only the layer, I still have to perform the decisive investigation. Calling that result an RCA overstates its operational value.

### A current-state tool is weak on intermittent incidents

Many BGP incidents are over by the time the engineer logs in. The useful questions then are:

- when did it reset;
- how many times;
- what reason did both ends record;
- what changed immediately before it;
- did BFD, the interface, or the routing process move first;
- did the service actually drop or did graceful restart hide it?

The current log window and correlation are a start, but a bounded device buffer with incomplete coverage is not enough for a shift handover or a recurring-flap problem. If the session recovered during the two-minute run, the output can be less useful than the alert that started it.

### The exit codes are sensible locally and dangerous globally

The distinction between “network fault” and “answer not trustworthy” is good:

- `1` means the tool believes it found a real fault;
- `2` means the run did not produce an answer that should be acted on.

That is operationally better than treating collection failure as a network outage.

The problem is exit `0`. It means “no fault on this dependency path,” not “the service is healthy,” “the incident is over,” or even “this device is healthy.” A generic automation system will eventually flatten exit `0` into success. The repository also documents that another command uses exit `2` for a different meaning, which makes suite-wide shell automation error-prone.

I would not wire alerting to the exit code alone. I would parse the JSON fields and use the command only as enrichment:

```text
existing BGP-down alert remains authoritative
  → run investigation asynchronously
  → exit 1: attach suspected layer and rung evidence to the incident
  → exit 2: attach “automation inconclusive” and continue the normal runbook
  → exit 0: attach “no current path fault found”; do not close or suppress the alert
```

I would wire exit `2` to a service-quality metric for the tool, not to the network pager. A rising rate of inconclusive runs is something the tool owner should investigate during working hours unless the enrichment service itself has an agreed operational SLO.

### The polished report can be more persuasive than the evidence deserves

A fluent explanation with a cause label and complete-looking chain will anchor the incident. I would trust the rung table before the narrative. If the narrative and the device’s last reset reason disagree, the device evidence wins.

For the first months of use, I would want reports visibly labeled as “automated hypothesis,” not “root cause,” even when the ladder completed.

## 4. What would make you stop trusting it

This incident would do it:

> A route-reflector session to a PE goes down during a maintenance window. The PE also has an old, intentionally shut spare interface. The actual BGP failure is a neighbor shutdown or authentication mismatch. The tool reports `interface_line_down` on the PE, marks the answer trustworthy, and gives me a complete causal chain. I page or call the transport team, ask someone to inspect the wrong circuit, and twenty minutes later another engineer finds the BGP configuration problem in the neighbor detail that was available from the start.

After that, I would quietly stop using the tool. I might still run it for evidence collection, but I would ignore its cause field. This is how operational tools lose adoption: not through a dramatic outage, but by wasting one bad night with a confident, specific, plausible answer.

Other trust-ending incidents would be:

- exit `0` causes automation to close or suppress an alert while customer traffic is still failing;
- it identifies the right layer but the wrong device, causing escalation to the wrong team or site;
- the same evidence produces materially different conclusions on repeated runs;
- it contributes enough SSH or control-plane load during an event storm to make collection failures worse;
- a report says “no correlating event” and an engineer immediately finds the missing event in the authoritative logging platform.

A single loud `undetermined` would not damage trust. A single confident wrong cause probably would.

## 5. Three faults you would bet it gets wrong, and why

I would bet against it on at least these five.

### 1. BFD tears down BGP while the interface and IGP remain healthy

Real case: a BFD session flaps because of microbursts, line-card scheduling, or a timer mismatch. BGP drops immediately, while the physical interface stays up and the IGP may never move.

The current ladder has no BFD evidence. It is likely to stop at a generic session or transport finding and miss the mechanism that explains the fast teardown and recurrence. The operator still has to inspect BFD on both ends and correlate the exact timestamps.

### 2. TCP/179 is filtered asymmetrically by an ACL or control-plane policy

Real case: the route to the peer is present, IGP is healthy, interfaces are clean, ICMP may work, but TCP SYNs or return traffic to port 179 are dropped on one side. A control-plane policer can produce the same outward symptom under load.

The tool may correctly say `transport_blocked`, but it cannot localize which device or policy is responsible. On a large backbone, that is the difference between a useful diagnosis and a category label.

### 3. The session is down because of effective BGP configuration

Concrete examples:

- MD5 keys differ after a partial rollout;
- `update-source` is wrong;
- the remote AS is wrong on one side;
- TTL security is applied asymmetrically;
- a neighbor or peer-group is administratively shut;
- a capability or address-family mismatch prevents the session from completing.

The underlay can be completely healthy. Some of these produce nearly identical FSM behavior. Without a resolved effective-configuration comparison on both ends, the tool will either return `transport_blocked` or `cause_not_localised`. It will not reliably name the setting or the owner who must fix it.

### 4. The outage is transient and recovers during collection

Real case: an interface or IGP adjacency flaps once, BGP follows after its timers, and everything recovers before or during the investigation. This is one of the most common overnight incidents.

The current-state ladder can return all healthy or produce a mixed picture while the network converges. The useful diagnosis lives in event ordering, reset reasons, counter changes and recurrence—not in the final state alone.

I would expect this class to be especially weak until it is exercised by starting the tool while the fault is propagating rather than after the lab has settled.

### 5. A service is broken while the BGP session is healthy

Real case: the VPN route is filtered, the wrong route target is imported, the label is missing from the forwarding plane, one ECMP member blackholes packets, or an EVPN MAC/IP entry is stale. Every session and underlay check can be green.

The command will correctly say the named BGP session is not down. It will still be useless for the incident the operator is handling. This is why the entry point must remain explicitly session-scoped and must not be sold as service troubleshooting.

## 6. What is missing before this is part of a workflow

### An alert must be able to supply the inputs

Before I adopt it, I need to see it launched from the actual BGP alert with the exact local device, peer, routing instance, and address family. If a human has to translate every alert into the command’s object naming convention, the automation has not removed enough toil.

### A ticket-ready evidence package

The one-line summary is useful, but it is not what an escalation team will ask for. I need a persistent run bundle containing:

- incident/run ID and tool version;
- start and finish time, plus device clock information;
- local device, remote device, peer, VRF/BGP instance, AFI/SAFI and local source;
- current state and uptime;
- last reset reason and age from both ends where available;
- exact route table checked, next hop and outgoing interface;
- relevant interface state and recent transition time;
- the commands that succeeded, failed or were not supported;
- the cited evidence or compact command excerpts—not only opaque evidence-key names;
- log coverage and every known gap;
- what the tool did not check;
- the proposed cause clearly separated from confirmed observations.

That is what I can paste into a ticket without another engineer replying, “What did you actually run, and when?”

### A handover view

For shift change, I need to hand over state, not prose:

```text
what is still broken
what recovered
when the last transition occurred
which devices and tables were checked
which checks were inconclusive
what changed since the previous run
what hypothesis remains
what the next operator should verify
```

A saved report with no stable run reference or before/after comparison becomes another screenshot in chat. The next shift repeats the work.

### Both-end evidence for session faults

For BGP, one side’s view is often not enough. If the result is going to be escalated, I need the local and remote session state, reset reason, effective neighbor configuration, and time alignment. Otherwise the first question from the remote router or security team will be, “What does the other side say?”

### A known operating envelope

I need measured answers to:

- how often it is correct by fault class;
- how often it refuses;
- how often it names the wrong device;
- how long it takes at the 50th and 95th percentile;
- how many concurrent runs a router can tolerate;
- which IOS-XR versions and output variants are supported;
- whether the result remains valid during convergence.

Without those numbers, I cannot decide whether to trust it, retry it, or ignore it.

### What I would actually let it replace

It could replace:

- the first page of a BGP-down runbook;
- repetitive collection of summary, neighbor, route, IGP and interface evidence;
- a junior operator manually logging into several devices to identify the broken layer;
- hand-written boilerplate in a ticket for a known-shape incident;
- the first-pass question, “Is this obviously an underlay failure?”

It should not replace:

- turning a customer symptom into the correct network scope;
- deciding whether two events are causally related;
- judging service impact and blast radius;
- choosing which team owns an ambiguous fault;
- interpreting conflicting evidence from two devices;
- deciding to roll back, shut, bounce or reconfigure anything;
- the incident commander’s judgement about whether the current hypothesis is good enough to act on;
- a human post-incident root-cause review.

No tool should replace those decisions merely because it can produce a plausible paragraph.

## 7. What you would tell the person who built it, if you were being kind but honest

You have built a safer diagnostic command than most LLM network demos. The read-only boundary means I can run it without worrying that curiosity will become a change. The rung table is better than a black-box answer. Stopping at `undetermined` is frustrating but operationally correct. Preserving the failed trial and fixing the machine-readable result is exactly how trust should be earned.

But you are calling the output more than it currently is. A broken layer is not always a root cause. A complete-looking chain is not proof that the named component caused the incident. Four faults chosen around the ladder do not tell me how it behaves on the faults that fill real overnight queues: flaps, partial rollouts, BFD, policy, control-plane load, asymmetric filtering, stale forwarding state and two simultaneous problems.

Today I would use it as an automated second set of eyes after a precise BGP-down alert. I would let it gather evidence and suggest where to look. I would not let it decide what is broken, who to page, whether to close the incident, or what to change.

The shortest path to adoption is not more protocols or a better model. Make the current command fast, give me both-end ticket-grade evidence, prove it on faults you did not choose, and be exact about the difference between “layer found” and “cause established.” If it repeatedly saves me ten minutes without once sending me to the wrong device, the team will start using it without being told. If it wastes one serious incident with a confident answer, no amount of architecture documentation will bring that trust back.
