# Security

This is the canonical statement of the threat model, what is actually enforced,
what is not, and where to report a problem. `CONTRIBUTING.md` points here rather
than repeating it.

## The threat model

A single operator, a lab they already control, read-only by construction, with
no authentication story of its own. There is no production deployment and
nothing here is exposed to an untrusted network.

**The only claim the architecture makes is that this tool cannot change device
state.** `platforms.APPROVED_COMMANDS[platform]` is an exact-match allowlist of
`show`/`ping`/`traceroute` commands, checked *before* credentials are loaded or
a connection is opened; there is no `run_command(device, command)`, no config
mode, and no shell (see CLAUDE.md, "The safety boundary"). If you find a way to
make this tool write to a device, that is the bug worth reporting — it is the
one thing that would falsify the project's stated safety property. Everything
else below is honesty about the much narrower guarantee that leaves.

## What is enforced

- **The allowlist runs before credentials.** Platform resolution
  (`lab.platform_for()`) reads static inventory data only, so the exact-match
  check in `_run_approved_commands` happens before a credential is read or a
  socket opened. `test_refuses_unapproved_commands_before_loading_credentials`
  pins this by running with no credentials set at all.
- **Commands are built by reconstruction, never interpolation.** A
  caller-supplied value is parsed into a typed object and the command is
  rendered from that object's canonical form — not validated-then-passed-through.
  A regex broad enough to accept every legitimate value is also broad enough to
  admit a lookalike nobody anticipated.
- **The notifier has no inbound surface.** `notifier.py` ships delivery only —
  no command handler, no webhook, no polling loop. It also cannot leak the
  evidence bundle: `notify()`'s signature takes a rendered `report: dict` and
  has no parameter a raw command envelope could arrive in, so nothing beyond
  the report can travel through this path by construction, not by a redaction
  filter someone has to remember. The Telegram bot token is redacted from every
  log line, audit record, exception message and repr (`notifier._redact`).
- **MCP tools are sanitised at registration, not by convention.**
  `mcp_server/boundary.sanitize()` strips raw device text (`commands`,
  `unaccounted_lines`) and replaces it with a withheld-record — count and byte
  size, not silence — applied by the registration decorator so a tool is
  covered by the act of being registered, not by its author remembering to call
  something.

## What is not enforced

- **No identity or RBAC.** Native MCP HTTP/SSE mode requires a configured
  bearer token before any request reaches the MCP surface, and uses a
  constant-time comparison. This is shared-secret authentication for a
  trusted LAN/VPN, not user identity, tenant isolation, or authorization:
  every valid token holder receives the same full read surface. The CLI and
  stdio MCP modes remain local-process trust boundaries. `NETTOOLS_ACTOR`
  (`network_tools._resolve_actor`) is **provenance, not authorization** — a
  label for "who ran this" in the audit log, never consulted before a command
  runs, and trivially spoofable by anyone who can set an environment variable.
  Real RBAC needs an identity provider this project does not have: something a
  caller cannot simply declare itself to be, e.g. a verified SSO/OIDC token or
  a signed client certificate checked before any command runs.
- **Telegram chat IDs are delivery, not authorization.**
  `TELEGRAM_CHAT_ID`'s comma-separated allowlist controls where a report is
  *sent*; it grants nothing, because there is no inbound path to grant
  anything on. If this module ever grows one, that allowlist must not be
  mistaken for the thing guarding it (`notifier.TelegramNotifier`).
- **Prompt injection via device free text is bounded, not eliminated.**
  Every model path now routes through one egress projector
  (`src/agent_nettools/model_egress.py`, B-470): raw command output is
  withheld with a counted record, and device-authored free text (a syslog
  line's `text`, a BGP reset reason) is wrapped in explicit untrusted-content
  delimiters with a per-prompt budget (B-467). The projector bounds *where*
  device text can appear and *how much*; it does not validate its meaning —
  an injected log line can still steer non-authoritative prose. What stops it
  mattering is output-side: grounded timelines, identifier containment
  (B-453), and non-authoritative labelling mean an injected line cannot
  fabricate a **cited** finding. Delimiters are a mitigation for prompt
  steering, not a proof against it — the projector's own docstring says so.

## Unsupported deployment modes

None of the following have been designed for, tested against, or reviewed:

- **Multi-user access.** One set of device credentials, one audit trail, no
  per-user scoping. Two people using the same deployment are indistinguishable
  in the audit log.
- **An internet-exposed MCP server.** Bearer-token HTTP mode is designed only
  for a trusted LAN/VPN. Public exposure still lacks TLS termination, key
  rotation, per-user identity, RBAC, rate limits, and a deployment review.
- **Production paging or incident closure.** The deterministic `investigate`
  path is the only authoritative one; `analyze`, fabric analysis and the
  free-form `agent` are exploratory and not grounded to the same standard.
  None of the outputs here should drive automated remediation.

## Reporting a security issue

**Please report privately first**, via [GitHub's private vulnerability
reporting](https://github.com/ramixpe/ai-ops/security/advisories/new) on this
repository. A public issue is fine for anything you have already confirmed is
not exploitable, but start private if you are unsure.

This reverses the policy this file carried until 2026-08-20, which said to open
a public issue because "there is nothing sensitive to withhold: there are no
production deployments and no secrets in this repository." Both halves of that
are true and both are beside the point. The question is not whether *this
repository* holds a secret — it is what a defect here does in *someone's
deployment*. This tool reads device credentials from the environment, opens SSH
sessions to production-shaped infrastructure, and writes evidence, tickets and
logs to local disk. EER-001 is the worked example: a path-traversal bug in
`evidence prune` deleted a JSON file outside the evidence root, and it was found
by an external reviewer reading the tagged release. A finding of that shape
deserves a fix released before it is described publicly.

What to expect: an acknowledgement, a fix or an explicit "won't fix, here is
why", and credit in `CHANGELOG.md` unless you would rather not be named.
