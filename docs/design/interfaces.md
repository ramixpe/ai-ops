# Human Interaction Interfaces

How people interact with the agent, staged against what the architecture can actually support at each point. Companion to `design-thinking.md` D3 (the maturity ladder) and `BUILD-PLAN.md` T-035.

---

## The principle

**The interface must not promise more than the architecture can deliver.**

A command-line invocation promises a command. A chat window promises a conversation. Shipping a chat window over a system that can only answer one shape of question means the first thing a user learns about the tool is what it cannot do — and that impression is expensive to undo.

So the interface follows the maturity ladder rather than leading it.

---

## The ladder

| Stage | Interface | Direction | Why here |
|---|---|---|---|
| **MVP-0** | `nettools` CLI | Pull | The interaction *is* a command: name a device and a subject, get a report. Already built. Zero new work. |
| **MVP-0 fast-follow** | Report relay to a team channel | Push, outbound only | Gets output in front of engineers without an inbound surface. One task, no auth question. `BUILD-PLAN.md` T-035. |
| **Stage 2** | Same channel, agent-initiated | Push | Where a channel genuinely earns its place: the network wakes the agent, the agent tells the team. Nobody had to ask. |
| **MVP-1** | Inbound commands on the channel | Pull, conversational | Only once the gate exists and free-text flow selection works is there a real conversation to have. |
| **Stage 3** | Inline approve / reject on a proposal | Bidirectional | Requires corporate identity. See "Identity" below. |

---

## What already exists and is easy to miss

**The MCP server is a chat interface with no new code.** `mcp_server/server.py` exposes read-only tools over stdio. Point any MCP client at it — Claude Desktop, Cowork, an IDE — and engineers can explore the fabric conversationally today.

Two caveats: the tool manifest needs the consolidation described in the LLD (§4.1, ~22 tools down to five stage-shaped ones), and MCP clients are a developer-shaped surface rather than a NOC-shaped one. But if the near-term goal is "let someone poke at this interactively this week", it is by far the cheapest path and it is already written.

---

## Three decisions a chat channel forces

These are not objections to Telegram. They are decisions that are cheap now and expensive after the team has built habits.

### 1. Data residency

A hosted messenger means device names, management IPs, and RCA text leave the estate and land on a third party's servers.

Acceptable for `sota-xrd`, which is a lab. Likely a policy problem the moment this points at production — and switching channels after a team has adopted one means retraining everybody.

**Mattermost is the self-hosted alternative**, same UX, SSO available, data stays inside. The operator already runs a Docker platform stack (Prometheus, Loki, Alertmanager, syslog-ng), so the deployment shape is familiar.

**Decide before implementing, and record the choice.** T-035 implements exactly one provider.

### 2. Identity

Messenger user IDs are not corporate identity. A group chat grants whatever the bot has to whoever gets added to it.

The repository is already careful here. `README.md` states the position plainly: the audit log's `actor` field is **provenance, not authorization** — it answers "who ran this" for a reviewer after the fact, and never gates anything. Real RBAC needs an identity provider that a caller cannot simply set an environment variable to become: a verified SSO or OIDC token, a signed client certificate, checked *before* any command runs.

That position is tolerable while the system is read-only. **It is not tolerable at Stage 3**, where a procedure can bounce an interface. Approval buttons therefore sit behind identity, not beside it.

### 3. Egress

The chosen provider needs outbound network from wherever `nettools` runs. That is available from a workstation; it frequently is not from a management network. Confirm the actual deployment host, not the development one.

---

## Why the relay is outbound-only

T-035 deliberately has no inbound path — no command handler, no webhook listener, no polling loop. Three reasons:

**It sidesteps identity entirely.** Nothing can be triggered from the channel, so who is in the channel decides only who *reads* output, not who *causes* actions. That is a much smaller question.

**It matches what MVP-0 can do.** The descent answers one shape of question. Pushing its result is honest; accepting arbitrary questions is not.

**It bounds egress structurally.** The notifier receives the **report object only** — observations, interpretations, recommendation, rung path, finding. It never receives the evidence bundle, so raw command output and configuration fragments cannot leak through it by accident. A test pins this, and it is a stronger guarantee than a redaction filter that someone eventually gets wrong.

---

## Stage 2 note

Alertmanager ships a Telegram receiver. If a hosted messenger is the chosen channel, part of the Stage 2 delivery path may be configuration rather than code.

`BUILD-PLAN.md` T-005 discovers whether an Alertmanager webhook receiver already exists and what its payload looks like. Read that finding before building anything in this area — it may reduce T-035 to a config change, or it may make the notifier redundant for the alert path while remaining useful for CLI-initiated runs.

---

## Open

| # | Question | Blocking | Where |
|---|---|---|---|
| 1 | Telegram or Mattermost — residency decision | T-035 | Record as a finding |
| 2 | Which host runs `nettools`, and does it have egress? | T-035 | Confirm before implementing |
| 3 | Does Alertmanager already have a webhook receiver? | Stage 2 | `BUILD-PLAN.md` T-005 |
| 4 | Identity provider for Stage 3 approvals | Stage 3 | Not before MVP-1 review |
