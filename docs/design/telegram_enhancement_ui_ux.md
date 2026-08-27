# Telegram Enhancement UI/UX

## Modern AI Investigation Experience for Network Operations Using Telegram, LLMs, MCP, Tool Calling, and n8n

---

## 1. Purpose

This document defines a practical architecture and UX design for transforming a traditional Telegram notification bot into a modern AI-style investigation interface for network operations.

The target environment assumes a system where:

- An LLM performs guided troubleshooting and investigation.
- MCP servers expose operational tools and data sources.
- n8n orchestrates workflows.
- Network systems, inventory platforms, monitoring systems, and APIs provide evidence.
- Telegram is used as the primary operator-facing notification and interaction channel.
- Faults, alarms, syslogs, or incidents trigger automated investigations.

The goal is to move away from a noisy stream of independent Telegram messages such as:

```text
Fault detected.

Checking inventory.

Found device IP.

Running show interfaces.

Interface is down.

Checking BGP.

BGP peer is down.

Checking optical levels.

Optics look bad.

Likely fiber issue.
```

toward a single continuously updated AI investigation experience that feels closer to ChatGPT, Claude, Copilot, or a modern observability assistant.

---

# 2. Core UX Principle

## One Incident = One Living Investigation Surface

The single most important design principle is:

> Do not treat every tool call, observation, or workflow step as a Telegram message.

Instead:

> Treat one Telegram message as a live investigation card that evolves throughout the incident lifecycle.

The operator should see one clear message representing the current state of the investigation.

Example:

```text
🚨 Network Investigation
INC-2841 · PE1 · BGP Down

⏳ Investigation in progress...

✓ Device resolved
  PE1 → 10.10.10.1 · IOS-XR

✓ Interface check
  TenGigE0/0/0/1 → DOWN
  Last change: 14:21:37

✓ BGP check
  192.0.2.2 → Idle
  Prefixes received: 0

🔧 Optical diagnostics
  Checking transceiver levels...

────────────────────
3 checks complete · 1 running
```

The same Telegram object is continuously refreshed as the investigation progresses.

When completed:

```text
🚨 Network Investigation
INC-2841 · PE1 · BGP Down

✓ Device resolved
✓ Interface check
✓ BGP check
✓ Optical check

────────────────────

🔴 Probable Cause

Physical connectivity failure on
TenGigE0/0/0/1.

Evidence:
• Interface operational state: DOWN
• BGP peer 192.0.2.2: Idle
• Rx optical power: -40 dBm

Confidence: HIGH

Recommended next check:
Verify the remote-end interface and physical fiber path.

🏁 Investigation completed
14:24:08
```

This creates a dramatically calmer and more professional operator experience.

---

# 3. Why the Existing Message-Per-Step Model Fails

A traditional Telegram bot usually follows this pattern:

```text
Trigger
  ↓
Send message
  ↓
Tool call
  ↓
Send message
  ↓
Tool call
  ↓
Send message
  ↓
Final message
```

This creates several UX and operational problems.

## 3.1 Message Flooding

One incident can generate:

- 10 messages
- 20 messages
- 50 messages
- more if there are retries or parallel tool calls

During a major outage, several concurrent investigations can make the chat unreadable.

## 3.2 Loss of Context

The operator must mentally reconstruct which message belongs to which incident.

This becomes especially difficult when multiple devices or faults are investigated simultaneously.

## 3.3 Poor Visual Hierarchy

Telegram displays every message with almost equal importance.

A harmless intermediate check visually competes with:

- the root cause,
- the severity,
- the final recommendation,
- the actual alarm.

## 3.4 High Cognitive Load

Operators should not have to assemble an incident timeline from 20 disconnected chat messages.

The UI should do this for them.

## 3.5 Weak AI Experience

A modern AI assistant should feel like a continuously evolving investigation, not a collection of webhook notifications.

---

# 4. Telegram Capabilities to Use

Telegram now supports patterns that are much better suited for AI applications.

There are two implementation approaches.

---

# 5. Preferred Approach: Native AI Streaming with `sendMessageDraft`

Telegram introduced AI-oriented draft streaming for bots.

The main idea is:

1. Create a draft.
2. Keep updating the same draft.
3. Show partial progress.
4. Finalize the investigation with a persistent message.

Conceptually:

```text
Telegram private chat

┌───────────────────────────────────┐
│ 🚨 INC-2841                       │
│                                   │
│ ✓ Inventory                       │
│ ✓ Interface                       │
│ 🔧 Checking BGP...                │
│                                   │
│ Investigation in progress...      │
└───────────────────────────────────┘
```

Then:

```text
┌───────────────────────────────────┐
│ 🚨 INC-2841                       │
│                                   │
│ ✓ Inventory                       │
│ ✓ Interface                       │
│ ✓ BGP                             │
│ 🔧 Checking optics...             │
│                                   │
│ Investigation in progress...      │
└───────────────────────────────────┘
```

Then final:

```text
┌───────────────────────────────────┐
│ 🚨 INC-2841                       │
│                                   │
│ 🔴 Root cause                     │
│ Fiber / physical interface issue  │
│                                   │
│ Confidence: HIGH                  │
│                                   │
│ Recommended next check:           │
│ Remote interface + fiber path     │
└───────────────────────────────────┘
```

## 5.1 Why this approach is ideal

It provides:

- streaming-style UX,
- fewer permanent chat messages,
- less notification noise,
- smoother visual updates,
- clearer investigation ownership,
- better fit for AI assistants.

## 5.2 Draft identity

Every investigation should have a stable draft identifier.

Example:

```text
incident_id = INC-2841
draft_id    = 2841
chat_id     = 123456789
```

The same `draft_id` should be reused throughout that investigation.

## 5.3 Draft lifecycle

Recommended lifecycle:

```text
Incident detected
      ↓
Create investigation state
      ↓
Create/update Telegram draft
      ↓
Run first checks
      ↓
Update draft
      ↓
Run tools
      ↓
Update draft
      ↓
Root cause / next action determined
      ↓
Send final persistent message
      ↓
Close investigation state
```

---

# 6. Fallback Approach: `editMessageText`

If Telegram's AI draft API is not available in the exact bot context, library, or n8n integration you are using, the same UX can be implemented using ordinary messages plus edits.

Flow:

```text
Telegram sendMessage
        ↓
Store message_id
        ↓
Investigation event
        ↓
editMessageText
        ↓
Investigation event
        ↓
editMessageText
        ↓
Final edit
```

Example n8n state:

```json
{
  "incident_id": "INC-2841",
  "chat_id": "123456789",
  "telegram_message_id": "9121",
  "status": "investigating"
}
```

Every renderer update uses the same `message_id`.

This is less elegant than native draft streaming, but it still solves almost all of the message-flooding problem.

---

# 7. Recommended System Architecture

The most important architectural recommendation is:

> Do not connect Telegram directly to each tool or n8n node.

Instead introduce an intermediate event model.

The architecture should be:

```text
                       ┌────────────────────┐
                       │     Telegram       │
                       │    Operator UI     │
                       └─────────▲──────────┘
                                 │
                         Telegram Renderer
                                 ▲
                                 │
                     Investigation Event Stream
                                 ▲
                ┌────────────────┼────────────────┐
                │                │                │
                │                │                │
            LLM Agent         MCP Tools        n8n Flows
                │                │                │
                │                │                │
                ▼                ▼                ▼
          Investigation      Inventory         SSH/API
          decisions          NetBox            SNMP
          summarization      Grafana           Syslog
          next checks        DB queries        Telemetry
```

Telegram becomes only one presentation layer.

---

# 8. Why an Investigation Event Stream Matters

Without an event abstraction, every component starts knowing Telegram-specific details.

Bad architecture:

```text
SSH node
  └─ sends Telegram

NetBox node
  └─ sends Telegram

LLM
  └─ sends Telegram

Optics workflow
  └─ sends Telegram
```

This quickly becomes difficult to maintain.

A better architecture is:

```text
SSH node
  └─ emits event

NetBox node
  └─ emits event

LLM
  └─ emits event

Optics workflow
  └─ emits event

Events
  ↓
Renderer
  ↓
Telegram
```

This provides:

- channel independence,
- consistent UX,
- easier logging,
- cleaner auditing,
- better observability,
- support for future web UI,
- support for Slack/Teams,
- support for NOC dashboards,
- easier testing.

---

# 9. Standard `InvestigationEvent` Model

Every meaningful system action should produce a structured event.

Recommended baseline:

```json
{
  "event_id": "evt-7e928",
  "run_id": "INC-2841",
  "timestamp": "2026-08-24T14:22:19Z",

  "source": "network-investigator",
  "stage": "diagnostics",

  "event_type": "tool_completed",

  "tool": {
    "name": "get_bgp_neighbor",
    "target": "PE1",
    "arguments": {
      "neighbor": "192.0.2.2"
    }
  },

  "display": {
    "title": "BGP check",
    "summary": "Neighbor 192.0.2.2 is Idle",
    "severity": "warning"
  },

  "evidence": {
    "state": "Idle",
    "received_prefixes": 0
  }
}
```

---

# 10. Recommended Event Types

The event type should be deterministic and machine-readable.

Suggested set:

```text
investigation_started

stage_started
stage_completed

tool_started
tool_progress
tool_completed
tool_failed

observation
anomaly_found
evidence_added

hypothesis_created
hypothesis_rejected
hypothesis_strengthened

root_cause_candidate
root_cause_confirmed

next_check_selected

human_input_required

investigation_paused
investigation_resumed

investigation_completed
investigation_failed
```

Avoid creating dozens of arbitrary event names.

Keep a controlled schema.

---

# 11. Separate Internal Reasoning from Operator-Visible Activity

This is a critical design principle.

The agent can internally maintain:

- hypotheses,
- reasoning state,
- tool selection logic,
- decision paths,
- uncertainty,
- context.

But the Telegram UI should not expose the model's raw internal chain-of-thought.

Instead show operationally meaningful activity.

Good:

```text
🧠 Analysing BGP and interface evidence...
```

Good:

```text
🔎 Comparing alarm timing against interface flap history...
```

Good:

```text
🔧 Running BGP neighbor check...
```

Avoid:

```text
I think perhaps the interface caused the BGP failure because first I considered...
```

Operators need:

- what is being checked,
- why it matters,
- what was found,
- what happens next.

They do not need raw model deliberation.

---

# 12. Three Logical Output Channels

The agent architecture should explicitly separate three outputs.

```text
LLM / Investigation Agent
         │
         ├── 1. INTERNAL STATE
         │      hidden reasoning
         │      hypotheses
         │      planning
         │      agent memory
         │
         ├── 2. OPERATOR ACTIVITY
         │      tool_started
         │      tool_completed
         │      anomaly
         │      stage status
         │
         └── 3. FINAL FINDINGS
                root cause
                evidence
                confidence
                recommendation
```

Telegram should consume only channels 2 and 3.

This makes the product safer, cleaner, and easier to understand.

---

# 13. Investigation State Object

In addition to events, maintain one current state per investigation.

Example:

```json
{
  "run_id": "INC-2841",

  "status": "investigating",

  "incident": {
    "title": "BGP Neighbor Down",
    "severity": "major",
    "device": "PE1",
    "service": "Internet Transit"
  },

  "progress": {
    "completed_checks": 3,
    "running_checks": 1,
    "failed_checks": 0
  },

  "checks": [
    {
      "id": "inventory",
      "title": "Device resolution",
      "status": "completed",
      "summary": "PE1 → 10.10.10.1"
    },
    {
      "id": "interface",
      "title": "Interface check",
      "status": "completed",
      "summary": "Te0/0/0/1 DOWN"
    },
    {
      "id": "bgp",
      "title": "BGP check",
      "status": "completed",
      "summary": "192.0.2.2 Idle"
    },
    {
      "id": "optics",
      "title": "Optical diagnostics",
      "status": "running",
      "summary": "Reading transceiver levels"
    }
  ],

  "root_cause": null,

  "telegram": {
    "chat_id": "123456789",
    "draft_id": "2841",
    "message_id": null,
    "last_render_hash": "..."
  }
}
```

The renderer converts this state into Telegram text.

---

# 14. Telegram Renderer

The Telegram renderer should be a separate logical component.

Its responsibility:

```text
Investigation State
        ↓
Formatting
        ↓
Telegram-compatible output
```

It should not perform network investigation.

It should only decide:

- what to display,
- in what order,
- with what symbols,
- how much detail,
- when to update.

---

# 15. Suggested Message Structure

Use a stable hierarchy.

Recommended structure:

```text
[Incident Header]

[Current status]

[Completed and active checks]

[Important observation]

[Progress]

[Final findings when available]

[Completion status]
```

Example:

```text
🚨 Network Investigation
INC-2841 · PE1
BGP Neighbor Down

🔎 Investigation in progress

✓ Inventory
  PE1 · 10.10.10.1 · IOS-XR

✓ Physical interface
  Te0/0/0/1 · DOWN

✓ BGP
  192.0.2.2 · Idle

🔧 Optical level
  Reading transceiver diagnostics...

⚠ Current observation
BGP failure appears secondary to the
physical interface failure.

────────────────────
3 complete · 1 running
```

---

# 16. Recommended UX Status Vocabulary

Use a small, consistent icon vocabulary.

```text
⏳ queued
🔎 investigating
🧠 analysing
🔧 tool running
✓ completed / healthy
⚠ anomaly / warning
✗ failed
❓ human input needed
🔴 root cause / critical
🟡 degraded / uncertain
🟢 healthy
↻ retrying
⏸ paused
🏁 completed
```

Consistency is more important than using many icons.

---

# 17. Stage-Based Investigation UX

For complex incidents, organize investigation into stages.

Example:

```text
1. Context
2. Inventory
3. Connectivity
4. Protocol
5. Performance
6. Correlation
7. Root Cause
```

Telegram representation:

```text
🚨 INC-2841 · PE1

✓ Context
✓ Inventory
✓ Connectivity
🔧 Protocol
○ Performance
○ Correlation
○ Root Cause
```

This gives operators a clear sense of progress.

---

# 18. Tool Calling Presentation

Do not expose raw tool-call JSON unless the operator explicitly asks for debugging mode.

Normal view:

```text
🔧 BGP neighbor check
PE1 → 192.0.2.2
```

Result:

```text
✓ BGP neighbor check
State: Idle
Prefixes: 0
```

Avoid:

```json
{
  "tool": "get_bgp_neighbor",
  "arguments": {
    "device": "PE1",
    "neighbor": "192.0.2.2"
  }
}
```

That belongs in engineering logs, not the standard NOC user experience.

---

# 19. Optional Engineering / Debug Mode

For developers or senior engineers, support a verbose mode.

Example:

```text
🔧 TOOL
get_bgp_neighbor

Target:
PE1

Arguments:
neighbor = 192.0.2.2

Duration:
482 ms

Result:
state = Idle
prefixes_received = 0
```

Possible user command:

```text
/debug on
```

or:

```text
/details
```

Normal operators should get the concise version.

---

# 20. Streaming Frequency

Do not update Telegram for every LLM token.

That creates unnecessary traffic and makes the UI jittery.

Recommended update triggers:

```text
Update when:

• important tool starts
• important tool completes
• anomaly is found
• stage changes
• root cause confidence changes significantly
• human input is required
• investigation completes
```

Additionally, while textual AI output is streaming, batch it.

A practical interval is approximately:

```text
750 ms – 1.5 seconds
```

depending on load.

A good debounce model:

```text
Incoming event
      ↓
State updated
      ↓
Renderer marked dirty
      ↓
Wait 750 ms
      ↓
Collect additional events
      ↓
Render once
```

This prevents Telegram API flooding.

---

# 21. Update Deduplication

Do not send an update if the rendered content is unchanged.

Use a hash.

Example:

```text
rendered_message
      ↓
SHA-256
      ↓
compare with last_render_hash
      ↓
same?
  yes → skip
  no  → send update
```

State:

```json
{
  "last_render_hash": "19a2e..."
}
```

This greatly reduces unnecessary API calls.

---

# 22. Rate Limiting and Backpressure

Telegram APIs have per-chat and bot-level limits.

Your system should therefore include:

- debounce,
- deduplication,
- retry queues,
- exponential backoff,
- event collapsing.

Example:

Instead of rendering:

```text
Tool started
Tool progress 10%
Tool progress 20%
Tool progress 30%
Tool progress 40%
```

collapse this into:

```text
🔧 Running optical diagnostics...
```

Only show progress percentages when they are meaningful to the operator.

---

# 23. Parallel Tool Calls

Parallel execution is common in AI investigations.

Example:

```text
                Agent
                  │
          ┌───────┼─────────┐
          ▼       ▼         ▼
       BGP      Optics    Interface
```

The Telegram UI should not become chaotic.

Render them as parallel active checks:

```text
🔧 BGP neighbor
   querying PE1...

🔧 Optical levels
   reading transceiver...

🔧 Remote interface
   querying PE2...
```

As results arrive:

```text
✓ BGP neighbor
  Idle

⚠ Optical levels
  Rx -40 dBm

🔧 Remote interface
  querying PE2...
```

---

# 24. Event Ordering

Parallel workflows mean events may arrive out of order.

Therefore the renderer should not simply append text.

Instead:

```text
Event
  ↓
Update canonical investigation state
  ↓
Re-render complete message
```

Do not build the Telegram text by concatenating each arriving event.

Bad:

```text
current_text += new_event
```

Better:

```text
state.update(event)
render(state)
```

This makes the UI deterministic.

---

# 25. Evidence Model

Every important conclusion should be connected to evidence.

Example:

```json
{
  "evidence_id": "ev-101",
  "source": "show_interface",
  "device": "PE1",
  "timestamp": "2026-08-24T14:22:22Z",
  "fact": "interface_oper_status",
  "value": "down"
}
```

Another:

```json
{
  "evidence_id": "ev-102",
  "source": "show_bgp_neighbor",
  "device": "PE1",
  "fact": "bgp_state",
  "value": "Idle"
}
```

Root cause:

```json
{
  "root_cause": "physical_connectivity_failure",
  "evidence_refs": [
    "ev-101",
    "ev-102",
    "ev-103"
  ]
}
```

This supports traceability and reduces unsupported LLM conclusions.

---

# 26. Confidence Representation

Do not overcomplicate confidence.

Recommended:

```text
Confidence: HIGH
Confidence: MEDIUM
Confidence: LOW
```

Optionally:

```text
High confidence
3 independent evidence points
```

Avoid pretending that arbitrary percentages such as `93.7%` are scientifically meaningful unless your system has a calibrated confidence model.

---

# 27. Root Cause vs Observation vs Hypothesis

These should be distinct.

Example:

```text
Observation:
Te0/0/0/1 is DOWN

Observation:
BGP neighbor is Idle

Hypothesis:
The BGP failure is caused by loss of physical connectivity

Root Cause:
Fiber / remote interface failure

Confidence:
HIGH
```

This prevents the LLM from prematurely presenting a hypothesis as a confirmed diagnosis.

---

# 28. Progressive Disclosure

Telegram has limited space.

Do not show every detail by default.

The main investigation card should contain:

- incident identity,
- important checks,
- anomalies,
- root cause,
- recommendation.

Additional evidence can be exposed using buttons or commands.

Example inline buttons:

```text
[ Evidence ] [ Raw Output ]
[ Timeline ] [ Retry Check ]
```

Potential actions:

```text
/evidence INC-2841
/raw INC-2841
/timeline INC-2841
```

---

# 29. Inline Keyboard Actions

Telegram inline keyboards can significantly improve the operator workflow.

Recommended actions:

```text
[ View Evidence ]
[ Run Next Check ]

[ Ask AI ]
[ Escalate ]

[ Open Ticket ]
[ Mark Resolved ]
```

Potential second-level actions:

```text
[ Check Remote End ]
[ Check Optics ]
[ Check BGP ]
[ Check History ]
```

These actions can trigger n8n webhooks.

---

# 30. Human-in-the-Loop Interaction

The AI should sometimes stop and request missing information.

Example:

```text
❓ Additional information required

I cannot determine the customer-facing
interface from the current inventory.

Please choose:

[ Gi0/0/0/4 ]
[ Gi0/0/0/7 ]
[ Cancel ]
```

The response becomes an event:

```json
{
  "event_type": "human_input_received",
  "value": "Gi0/0/0/7"
}
```

The investigation then resumes.

---

# 31. Recommended n8n Architecture

A clean n8n design could contain the following workflows.

## Workflow A — Incident Trigger

Sources:

- syslog,
- monitoring alarm,
- webhook,
- Telegram command,
- ticketing system,
- SNMP trap.

Output:

```json
{
  "run_id": "INC-2841",
  "trigger": "bgp_down",
  "device": "PE1"
}
```

Then call:

```text
Investigation Orchestrator
```

---

# 32. Workflow B — Investigation Orchestrator

Responsibilities:

```text
Receive incident
      ↓
Create state
      ↓
Resolve inventory
      ↓
Select investigation flow
      ↓
Call MCP tools
      ↓
Emit events
      ↓
Ask LLM for next safe check
      ↓
Repeat
      ↓
Determine completion
```

Important:

The LLM should not fabricate network commands.

The available actions should come from:

- predefined n8n flows,
- MCP tool schemas,
- approved command templates.

The LLM selects tools and fills approved parameters.

---

# 33. Workflow C — Event Processor

Input:

```json
{
  "run_id": "INC-2841",
  "event_type": "tool_completed",
  ...
}
```

Responsibilities:

```text
validate event
      ↓
load investigation state
      ↓
apply state transition
      ↓
store event
      ↓
trigger renderer
```

---

# 34. Workflow D — Telegram Renderer

Responsibilities:

```text
Load state
      ↓
Render Telegram message
      ↓
Compare render hash
      ↓
If changed:
     update draft/message
```

This workflow should not know how BGP works.

It only understands generic investigation state.

---

# 35. Workflow E — Finalizer

Triggered when:

```text
investigation_completed
```

Responsibilities:

```text
Generate final concise report
      ↓
Send persistent Telegram message
      ↓
Store final investigation
      ↓
Optionally update ticket
      ↓
Optionally send dashboard event
```

---

# 36. Example n8n Logical Flow

```text
[Syslog Trigger]
      ↓
[Normalize Event]
      ↓
[Create run_id]
      ↓
[Emit investigation_started]
      ↓
[Telegram Renderer]
      ↓
[Inventory Lookup]
      ↓
[Emit tool_completed]
      ↓
[Agent Decide Next Tool]
      ↓
[MCP Execute]
      ↓
[Normalize Evidence]
      ↓
[Emit observation]
      ↓
[Agent Decide]
      ↓
     ...
      ↓
[Root Cause Confirmed]
      ↓
[Final Report]
      ↓
[Telegram Finalizer]
```

---

# 37. Telegram API from n8n

If the native n8n Telegram node does not expose the newest Telegram API operation, use an HTTP Request node.

Recommended abstraction:

```text
Telegram Adapter Subworkflow
```

Inputs:

```json
{
  "operation": "update_investigation",
  "chat_id": "123456789",
  "run_id": "INC-2841",
  "text": "..."
}
```

The subworkflow decides whether to use:

```text
sendMessageDraft
```

or:

```text
editMessageText
```

This keeps Telegram API details out of the main investigation workflow.

---

# 38. Storage Requirements

Do not rely on n8n execution memory alone for long-running investigations.

Store state externally.

Possible options:

- Redis
- PostgreSQL
- SQLite for prototypes
- n8n Data Store for small-scale use
- document store
- event database

Recommended key:

```text
investigation:INC-2841
```

Possible Redis structure:

```json
{
  "status": "investigating",
  "telegram_chat_id": "...",
  "draft_id": "...",
  "last_render_hash": "...",
  "current_stage": "physical_layer",
  "started_at": "...",
  "updated_at": "..."
}
```

---

# 39. Event Persistence

Persist events separately from current state.

Example:

```text
incident_events

run_id
event_id
event_type
timestamp
source
payload
```

Benefits:

- audit trail,
- debugging,
- replay,
- post-incident review,
- analytics,
- model evaluation.

---

# 40. Event Sourcing Option

For a more robust architecture, the entire investigation can use event sourcing.

```text
Events
  ↓
Reducer
  ↓
Investigation State
```

Example:

```text
investigation_started
inventory_resolved
tool_started
tool_completed
anomaly_found
hypothesis_created
tool_started
tool_completed
root_cause_confirmed
investigation_completed
```

The current state can always be rebuilt from the event history.

---

# 41. Tool Safety

Because this system interacts with real network infrastructure, tool governance is essential.

Tools should be categorized.

Example:

```text
READ_ONLY
SAFE_DIAGNOSTIC
CHANGE_LOW_RISK
CHANGE_HIGH_RISK
```

Initial autonomous investigations should ideally use only:

```text
READ_ONLY
SAFE_DIAGNOSTIC
```

Example:

```text
show interface
show bgp
show alarms
show optics
query inventory
query metrics
query logs
```

Configuration changes should require an explicit separate policy.

---

# 42. Command Control

Do not allow the LLM to freely write CLI commands.

Preferred model:

```text
LLM
  ↓
selects approved tool
  ↓
fills parameters
  ↓
tool creates command
```

Example MCP tool:

```json
{
  "name": "get_bgp_neighbor",
  "parameters": {
    "device": "PE1",
    "neighbor": "192.0.2.2"
  }
}
```

Internally:

```text
show bgp neighbor 192.0.2.2
```

The LLM does not create the command string itself.

---

# 43. Tool Result Normalization

Raw device output should be normalized before being sent to the LLM where possible.

Raw:

```text
RP/0/RSP0/CPU0:PE1#show bgp neighbor ...
...
```

Normalized:

```json
{
  "neighbor": "192.0.2.2",
  "state": "Idle",
  "uptime": null,
  "received_prefixes": 0,
  "last_error": "Connection rejected"
}
```

Benefits:

- fewer tokens,
- easier reasoning,
- less noise,
- more consistent tools,
- improved model reliability.

---

# 44. Raw Evidence Retention

Do not discard raw output.

Store both:

```text
normalized evidence
raw evidence
```

The LLM mainly consumes normalized evidence.

The operator can request raw output if needed.

---

# 45. Example Full Investigation

Trigger:

```text
BGP peer 192.0.2.2 down on PE1
```

Initial Telegram:

```text
🚨 Network Investigation
INC-2841 · PE1

Alarm:
BGP neighbor 192.0.2.2 DOWN

🔎 Starting investigation...
```

Inventory event:

```text
✓ Device resolved
PE1 · 10.10.10.1 · IOS-XR
```

Physical check starts:

```text
🔧 Physical interface
Checking associated interface...
```

Result:

```text
⚠ Physical interface
Te0/0/0/1 is DOWN
```

BGP check:

```text
🔧 BGP
Validating neighbor state...
```

Result:

```text
⚠ BGP
192.0.2.2 · Idle
Prefixes: 0
```

Optics:

```text
🔧 Optical diagnostics
Reading transceiver...
```

Result:

```text
⚠ Optical diagnostics
Rx Power: -40 dBm
Tx Power: -2.1 dBm
```

Agent conclusion:

```text
🔴 Probable Root Cause

Loss of optical receive signal on
Te0/0/0/1.

Evidence:
• Interface DOWN
• BGP neighbor Idle
• Rx power -40 dBm
• Tx power normal

Confidence: HIGH

Recommended next check:
Verify remote-end Tx and physical fiber path.
```

---

# 46. Investigation Timeline View

A timeline can be useful for post-incident debugging.

Example:

```text
14:21:02  🚨 Alarm received
14:21:03  ✓ Device resolved
14:21:05  🔧 Interface check started
14:21:06  ⚠ Interface DOWN
14:21:07  🔧 BGP check started
14:21:08  ⚠ Neighbor Idle
14:21:09  🔧 Optics started
14:21:11  ⚠ Rx -40 dBm
14:21:13  🔴 Root cause identified
14:21:14  🏁 Investigation completed
```

This can be hidden from the main card and exposed through:

```text
[ Timeline ]
```

---

# 47. Multi-Incident Handling

Each incident should have its own state and message.

Example:

```text
INC-2841 → Telegram draft/message 9121
INC-2842 → Telegram draft/message 9124
INC-2843 → Telegram draft/message 9128
```

Never share one message between unrelated incidents.

Use:

```text
run_id
```

as the universal correlation ID across:

- Telegram,
- n8n,
- MCP,
- LLM logs,
- tools,
- tickets,
- observability.

---

# 48. Correlation ID Strategy

Recommended format:

```text
INC-YYYYMMDD-XXXX
```

Example:

```text
INC-20260824-0142
```

Or reuse the NMS/ticket identifier when available:

```text
TT-839201
```

Everything should contain that ID.

---

# 49. Handling Investigation Branches

AI troubleshooting may branch.

Example:

```text
               BGP Down
                  │
             Interface?
              /       \
          DOWN         UP
           │            │
       Optics       Routing policy
```

Do not display the entire decision tree.

Only show the active branch:

```text
✓ Interface state
  DOWN

🔧 Investigating physical layer...
```

If a hypothesis is rejected:

```text
✓ Local interface
  UP

○ Physical failure unlikely

🔧 Checking routing policy...
```

---

# 50. Retry Handling

Tools sometimes fail because of:

- SSH timeout,
- API timeout,
- device busy,
- authentication issue,
- MCP server unavailable.

UX:

```text
↻ Interface check
First attempt timed out.
Retrying...
```

After failure:

```text
✗ Interface check
Unable to reach PE1 after 2 attempts.

Impact:
Interface state unavailable.
```

Do not silently retry forever.

---

# 51. Partial Investigation Result

Sometimes the investigation cannot reach a definitive root cause.

The final state should admit uncertainty.

Example:

```text
🟡 Investigation Inconclusive

Confirmed:
• BGP neighbor is Idle
• Local interface is UP
• No local packet errors detected

Unable to verify:
• Remote PE status
• Fiber path telemetry

Most likely next check:
Validate remote PE interface and BGP logs.

Confidence: LOW
```

An inconclusive result is better than an invented diagnosis.

---

# 52. Human Escalation

Provide a clear handoff.

Example:

```text
❓ Human review recommended

Reason:
The available evidence does not distinguish between:
• remote interface issue
• upstream filtering
• transport path failure

[ Escalate to L2 ]
```

The escalation event should include the investigation evidence automatically.

---

# 53. Final Report Structure

Recommended final response template:

```text
🚨 INCIDENT
Identifier · Device · Service

STATUS

ROOT CAUSE

KEY EVIDENCE

IMPACT

CONFIDENCE

RECOMMENDED NEXT ACTION

INVESTIGATION DURATION
```

Example:

```text
🚨 INC-2841 · PE1
Internet Transit

🔴 Root Cause
Loss of receive optical signal on
Te0/0/0/1.

Evidence:
• Interface operationally DOWN
• BGP peer 192.0.2.2 Idle
• Rx power -40 dBm
• Tx power normal

Impact:
Transit path unavailable.

Confidence:
HIGH

Recommended next action:
Check remote-end transmitter and fiber path.

🏁 Completed in 12 seconds
```

---

# 54. Avoid Excessive Animation

Do not make the UI theatrical.

Avoid continuously changing:

```text
Thinking.
Thinking..
Thinking...
```

or excessive icons.

The interface should feel like an operational console.

Use calm status updates.

---

# 55. Notification Strategy

Not every update should trigger a Telegram notification.

Ideal behavior:

Initial incident:

```text
push notification
```

Intermediate edits:

```text
silent
```

Final critical result:

```text
optional notification
```

This prevents an investigation from generating repeated phone alerts.

---

# 56. Severity-Aware Presentation

Use severity carefully.

Example:

```text
🔴 Critical
🟠 Major
🟡 Minor
🔵 Informational
```

The severity should come from the incident system or deterministic policy where possible, not be invented by the LLM.

---

# 57. UX for Multiple Services

Example:

```text
🚨 INC-2841

Device:
PE1

Affected services:
• Internet Transit
• Business VPN
• CDN uplink

Current finding:
Physical interface failure
```

Do not duplicate an investigation for each service if they share the same root network fault.

---

# 58. AI Follow-Up Chat

After completion, Telegram can become conversational.

Operator:

```text
Why are you confident this is fiber?
```

AI:

```text
Three observations support a physical-layer issue:

1. The local interface is DOWN.
2. BGP is Idle rather than Established.
3. Rx optical power is -40 dBm while Tx is normal.

This combination strongly suggests loss of incoming optical signal.
```

Operator:

```text
Check the remote router.
```

System:

```text
🔧 Remote-end check
Querying PE2...
```

The existing investigation can resume.

---

# 59. Conversation Context

Each Telegram reply should carry:

```text
chat_id
user_id
run_id
```

Do not rely only on natural-language context to decide which incident the user means.

Inline buttons are especially helpful because they can encode the `run_id`.

Example callback:

```json
{
  "action": "check_remote_end",
  "run_id": "INC-2841"
}
```

---

# 60. Threading and Group Chats

If used in a group or forum-like Telegram environment, consider one topic/thread per major incident where supported.

If native AI draft streaming is restricted in your deployment context, use the single-message editing pattern in groups.

The UX principle remains unchanged:

```text
one incident
=
one primary investigation surface
```

---

# 61. Operator Commands

Useful Telegram commands might include:

```text
/status
/incidents
/evidence
/timeline
/raw
/retry
/cancel
/escalate
/debug
/help
```

Example:

```text
/evidence INC-2841
```

---

# 62. Cancel Behavior

The operator should be able to stop a runaway investigation.

Example:

```text
[ Stop Investigation ]
```

Final state:

```text
⏸ Investigation stopped by operator

Completed checks:
• Inventory
• Interface
• BGP

Pending:
• Optics
• Remote peer
```

---

# 63. Timeout Behavior

Every investigation needs an upper bound.

Possible rules:

```text
tool timeout: 10 seconds
stage timeout: 60 seconds
investigation soft timeout: 3 minutes
```

These values depend on the environment.

The system should terminate cleanly and summarize what was learned.

---

# 64. State Machine

Recommended investigation state machine:

```text
CREATED
   ↓
QUEUED
   ↓
INVESTIGATING
   ↓
WAITING_TOOL
   ↓
ANALYSING
   ↓
┌───────────────┐
│               │
▼               ▼
WAITING_HUMAN   INVESTIGATING
│
▼
INVESTIGATING
   ↓
COMPLETED

Alternative terminal states:

FAILED
CANCELLED
INCONCLUSIVE
```

---

# 65. Rendering State Machine

Telegram rendering can be simpler:

```text
INITIAL
   ↓
ACTIVE
   ↓
WAITING
   ↓
FINAL
```

---

# 66. Data Privacy

Telegram should contain only the information operators need.

Avoid exposing:

- passwords,
- SNMP communities,
- API keys,
- device credentials,
- sensitive customer records,
- full confidential configuration.

Sanitize tool outputs before rendering.

---

# 67. Credential Handling

Credentials should never enter the LLM prompt when they are not required.

Better pattern:

```text
LLM selects device
      ↓
MCP/n8n resolves credentials internally
      ↓
tool executes
```

The model sees:

```text
device = PE1
```

not:

```text
username/password
```

---

# 68. Prompt Injection Resistance

Network evidence can contain untrusted text.

Examples:

- syslog messages,
- banners,
- descriptions,
- SNMP strings,
- hostnames,
- CLI output.

Treat all device-originated text as data.

The agent should never follow instructions appearing inside network evidence.

Architecture:

```text
raw evidence
   ↓
sanitize / delimit
   ↓
structured parser
   ↓
LLM
```

---

# 69. Observability for the AI System

The AI investigation platform itself should be observable.

Track:

```text
investigations started
investigations completed
average duration
tools per investigation
tool failures
LLM calls
tokens used
root-cause confidence
operator overrides
incorrect diagnoses
time to first useful finding
time to root cause
Telegram updates per incident
```

---

# 70. Important Product Metrics

Useful UX metrics include:

```text
Messages per incident
```

Target:

```text
Before: 10–50
After: 1–3
```

Other metrics:

```text
Telegram API calls per incident
Operator interactions per incident
Time to first evidence
Time to diagnosis
Investigation completion rate
Human escalation rate
```

---

# 71. AI Quality Metrics

Capture whether the final result was:

```text
correct
partially correct
incorrect
inconclusive
```

Also track:

```text
root cause category
evidence quality
tool efficiency
number of unnecessary checks
```

This creates a dataset for improving the agent.

---

# 72. Recommended MVP

Do not implement every feature initially.

A strong MVP:

## Phase 1

```text
One incident → one editable Telegram message
```

Implement:

- unique `run_id`,
- central investigation state,
- 5–8 standard event types,
- message renderer,
- edit/draft updates,
- final result.

## Phase 2

Add:

- inline buttons,
- evidence view,
- retries,
- human input,
- parallel tools.

## Phase 3

Add:

- event persistence,
- timeline,
- investigation replay,
- observability metrics,
- web UI.

## Phase 4

Add:

- multi-agent investigations,
- automated ticket updates,
- cross-domain service correlation,
- closed-loop remediation with governance.

---

# 73. Suggested MVP Event Types

For the first implementation, only use:

```text
investigation_started
tool_started
tool_completed
tool_failed
observation
root_cause_confirmed
investigation_completed
human_input_required
```

This is enough for a very good first product.

---

# 74. Suggested MVP State

```json
{
  "run_id": "INC-2841",
  "status": "investigating",
  "title": "BGP Neighbor Down",
  "device": "PE1",

  "checks": [],

  "observations": [],

  "root_cause": null,

  "telegram": {
    "chat_id": "...",
    "message_id": "...",
    "draft_id": "..."
  }
}
```

---

# 75. Recommended Rendering Function

Conceptually:

```python
def render_investigation(state):
    output = []

    output.append(render_header(state))
    output.append(render_status(state))
    output.append(render_checks(state))
    output.append(render_observations(state))

    if state.root_cause:
        output.append(render_root_cause(state))

    output.append(render_footer(state))

    return "\n\n".join(output)
```

The exact rendering implementation can live in:

- n8n Code node,
- Python microservice,
- Node.js service,
- backend application.

---

# 76. Renderer Should Be Deterministic

Given the same state:

```json
state_A
```

the renderer should always produce the same Telegram output.

Do not ask the LLM to rewrite the full Telegram card after every tool call.

That would introduce:

- unnecessary cost,
- unpredictable formatting,
- accidental loss of facts,
- inconsistent wording.

Use deterministic formatting for the card.

Use the LLM only where language generation is valuable:

- final summary,
- explanation,
- root-cause narrative,
- operator Q&A.

---

# 77. Separate Presentation from Reasoning

This is a key architectural boundary:

```text
LLM determines:
What does the evidence mean?

Renderer determines:
How should Telegram display it?
```

Do not combine these responsibilities.

---

# 78. Example Complete Data Flow

```text
1. BGP alarm enters n8n

2. n8n creates:
   run_id = INC-2841

3. Event:
   investigation_started

4. State stored

5. Telegram renderer creates live card

6. Agent asks inventory MCP:
   resolve_device("PE1")

7. Event:
   tool_started

8. Telegram card updates

9. Tool returns:
   10.10.10.1

10. Event:
    tool_completed

11. State updated

12. Agent chooses:
    get_interface_status

13. Event:
    tool_started

14. Tool returns:
    DOWN

15. Event:
    anomaly_found

16. Agent checks:
    BGP
    optics
    remote peer

17. Parallel events arrive

18. State reducer processes them

19. Telegram renderer updates the same card

20. Agent evaluates evidence

21. Event:
    root_cause_confirmed

22. Final report generated

23. Telegram persistent final message sent

24. Investigation archived
```

---

# 79. Future Web UI

Once you introduce the event stream, Telegram is no longer the architecture.

It is simply one client.

Future:

```text
                    Investigation Events
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
      Telegram          Web AI UI         NOC Wallboard
          │                 │                 │
      compact           detailed            aggregate
      mobile            interactive         fleet view
```

The web UI could provide:

- investigation graph,
- evidence explorer,
- raw CLI,
- topology,
- timeline,
- tool execution history,
- agent decisions,
- incident comparison.

---

# 80. Recommended Design Philosophy

The overall principle should be:

> Hide machine chatter. Show operational meaning.

The agent may execute:

```text
30 tool calls
100 internal events
12 LLM calls
3 retries
5 data lookups
```

The operator may only need:

```text
✓ 6 checks complete
⚠ 2 anomalies
🔴 1 probable root cause
→ 1 recommended action
```

That is good AI UX.

---

# 81. Recommended Architecture Summary

Final recommended architecture:

```text
Alarm / Syslog / User
         │
         ▼
     n8n Trigger
         │
         ▼
 Investigation Orchestrator
         │
         ├─────────────┐
         │             │
         ▼             ▼
      LLM Agent     MCP Tools
         │             │
         └──────┬──────┘
                │
                ▼
       Investigation Events
                │
                ▼
          State Reducer
                │
          ┌─────┴─────┐
          │           │
          ▼           ▼
     Event Store   Current State
                       │
                       ▼
                Telegram Renderer
                       │
                       ▼
              Live Telegram Card
```

---

# 82. Final Recommendation

For this use case, Telegram should not behave like a notification bus.

It should behave like a lightweight incident console.

The strongest design is:

1. **One incident = one live Telegram investigation card.**
2. **Use native Telegram AI draft streaming where practical.**
3. **Fallback to editing one persistent message where required.**
4. **Create a standardized `InvestigationEvent` contract.**
5. **Maintain canonical state separately from Telegram text.**
6. **Render the full message from state rather than appending events.**
7. **Show tool activity, not raw chain-of-thought.**
8. **Use concise operational states and stable iconography.**
9. **Separate evidence, hypotheses, and confirmed root cause.**
10. **Normalize tool output before sending it to the LLM.**
11. **Keep raw evidence for audit and troubleshooting.**
12. **Use deterministic approved MCP/n8n tools rather than allowing the LLM to invent commands.**
13. **Throttle and deduplicate Telegram updates.**
14. **Make `run_id` the universal correlation identifier.**
15. **Design the event stream so future channels can consume the same investigation.**

The long-term architecture should therefore be thought of as:

```text
Network AI Investigation Platform
```

rather than:

```text
Telegram Bot
```

Telegram is simply the first operator-facing UI.

That architectural distinction is what will make the system scalable, maintainable, and capable of eventually supporting a full modern NOC AI experience.
