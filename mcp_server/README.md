# Read-Only IOS-XR MCP Server

This server exposes reviewed network inspection functions as MCP tools. It reads
the device inventory from `agent_nettools.lab` and device credentials from the
environment (`DEVICE_USERNAME`, `DEVICE_PASSWORD`).

## Exposed Tools

- `list_lab_devices`
- `get_lab_device_facts`
- `check_lab_interfaces`
- `check_lab_bgp_neighbors`
- `check_lab_bgp_vpnv4_neighbors`
- `check_lab_lldp_neighbors`
- `check_lab_isis_neighbors`
- `check_lab_ldp_neighbors`
- `check_lab_ldp_discovery`
- `check_lab_sr_policies`
- `get_lab_sr_policy_detail`
- `check_lab_fabric`
- `collect_lab_evidence`
- `get_lab_route`
- `get_lab_bgp_neighbor`
- `get_lab_interface`
- `get_lab_logging`
- `get_lab_ping`
- `get_lab_traceroute`
- `diff_lab_device_against_latest`
- `diff_lab_device_against_golden`
- `assess_lab_device_health`
- `assess_lab_fabric_health`
- `detect_lab_flaps`
- `investigate_lab_session`
- `search_lab_knowledge`
- `explain_lab_mnemonic`
- `get_lab_logs`
- `get_lab_interface_rate_history`
- `get_lab_isis_adjacency_history`
- `get_lab_ldp_session_history`
- `get_lab_device_uptime_history`
- `get_lab_netbox_inventory`
- `get_lab_netbox_topology`
- `get_lab_graph_topology`
- `list_lab_tickets`
- `read_lab_ticket`

There is no shell, configuration tool, or generic command runner.

`check_lab_bgp_vpnv4_neighbors`/`check_lab_ldp_neighbors`/
`check_lab_ldp_discovery` (B-512, job 1) close a gap B-508 found: `bgp_vpnv4`,
`ldp`, and `ldp_discovery` were already collected on every device read
(`PLATFORM_INTENTS["cisco_xr"]`, `CHECK_TOOLS`) with CLI checks and no MCP
tool naming them, so a model could only reach that evidence by asking for
`collect_lab_evidence` and reading the block back out -- the right answer
through the only door available. Same shape, same `_read_only_tool`
registration, as `check_lab_isis_neighbors`/`check_lab_lldp_neighbors`.

The `get_lab_route`/`get_lab_bgp_neighbor`/`get_lab_interface`/
`get_lab_logging`/`get_lab_ping`/`get_lab_traceroute` tools are validated,
parameterized templates (Phase 5): each accepts one caller-supplied value (an
IPv4 address/prefix, an interface name, or a bounded count) which is parsed
into a typed object and the command is rendered from that object's own
canonical form -- never passed through as text. `get_lab_ping` and
`get_lab_traceroute` are active probes: they generate traffic (unlike every
other tool listed) even though they change no device state, and are gated by
**two** environment variables (`NETTOOLS_ALLOW_ACTIVE_PROBES`, default
enabled, and `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`, default **disabled** --
both must allow a probe for one to run; see below).

A client auto-approving purely on `readOnlyHint` cannot otherwise tell these
two apart from a passive `show` read (B-473, expert review P1-03): they are
registered through `_active_probe_tool` instead of `_read_only_tool`, which
keeps `read_only_hint=True` (still true -- neither changes device state) but
also sets `open_world_hint=True` and an annotation `title` of "ACTIVE PROBE —
generates network traffic", and prefixes both tools' descriptions with
"ACTIVE PROBE: sends ICMP/UDP traffic to the target." for a client that reads
only descriptions. **The annotations are signalling, not enforcement** --
they widen what a client *can* know without inspecting this server's source,
not what the server *allows*.

**The enforcement (B-493).** Measured 2026-08-18 (`MCP-EXPERIMENT.md` §12.3):
a 31B model followed a clean `investigate_lab_session` descent with an
UNPROMPTED `get_lab_ping` -- the first time a model generated traffic on this
fabric without being asked. `NETTOOLS_ALLOW_ACTIVE_PROBES` alone did not stop
it, because it defaults to enabled and nothing on this surface actually
checked it against the *caller being a model*. A human typing `nettools ping`
has asked for the probe explicitly; an MCP client is a model deciding to
generate traffic on its own, possibly mid-incident. So there is now a second,
MCP-only gate:

- `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES` -- default **disabled**. Set it to
  `1`/`true`/`yes`/`on` to allow `get_lab_ping`/`get_lab_traceroute` (classic
  surface) or `probe_lab` (staged surface) to run at all. Unlike
  `NETTOOLS_ALLOW_ACTIVE_PROBES`, an unrecognized value -- a typo included --
  stays **disabled**, not enabled: a gate whose whole purpose is "off unless
  asked for" must not reopen on a misspelling.
- Enforced at **registration**, inside `_register_sanitized_tool`/
  `_active_probe_tool`, the same place the raw-text boundary is applied --
  a tool registered through it is gated by construction, so a future
  active-probe tool inherits the check with no diff to `server.py`.
- A refusal never silently returns nothing: it is a structured, classified
  error (`mcp_server/boundary.py`'s `ERROR_KINDS`, the "active probes ...
  are disabled" entries) naming both environment variables and how to set
  them -- never "an unclassified error", and never a call that just quietly
  did nothing.

### External-source tools: Loki and Prometheus (B-512)

`get_lab_logs`, `get_lab_interface_rate_history`,
`get_lab_isis_adjacency_history`, `get_lab_ldp_session_history` and
`get_lab_device_uptime_history` expose the temporal evidence axis as MCP tools:
history is read as
*context*, never as a descent rung -- nothing in `flows.py`/`checks.py`/
`investigation.py` imports these modules, and neither does this server file
outside these five tool bodies.

**`get_lab_ldp_session_history`/`get_lab_device_uptime_history` (B-530 TSDB
survey).** `ldp_session_history` wraps LDP's own `ta_up_time_seconds`
(seconds since a session last came up, one series per session) -- chosen
over the two metrics an earlier draft of this survey named
(`peer_holdtime`, session-protection `spht_remaining`/`sp_duration`) after
measuring all three live: `peer_holdtime` is a static configured value
(180s on every session on this fabric, never varying) and the
session-protection pair reads a constant 0 everywhere because this fabric
configures session protection nowhere -- neither would ever have shown a
session degrading, the property this tool exists to surface.
`device_uptime_history` wraps `system_time_uptime_uptime` and answers "did
this device reboot, and when" -- nothing else in this build reads a
device's own uptime. Both reuse `isis_adjacency_history`'s own flap-
detection logic (a value that drops between samples means a reset/reboot)
via one shared helper, `metrics_prometheus._samples_and_resets`, rather
than a second implementation.

**BGP metrics are still not exposed.** Re-verified live for this survey,
not merely reasserted: every one of `Cisco_IOS_XR_ipv4_bgp_oper`'s 257
metric names carries `reset_reason`/`peer_reset_reason` labels, and those
labels took 8 and 2 distinct values (respectively) over Prometheus's
retention window -- short protocol-code tokens, not prose, so the
disqualifying property is not "free text" as originally characterised. It
is that a label changing value at all means Prometheus stores multiple
historical series per neighbor where every other query here has exactly
one stable series per identity for its whole lifetime -- and this survey's
existence/coverage machinery (`_series_known`, `records_available`) has no
notion of "which of several matched series is the current one." See
`metrics_prometheus.py`'s module docstring, "Why BGP is still not queried",
for the full measurement.

**Named queries only, always.** Each tool wraps exactly one entry from
`logs_loki.LOKI_QUERIES` / `metrics_prometheus.PROMETHEUS_QUERIES`, with
that query's own declared, validated slots (`device`, `interface`,
`counter`, `since_seconds`, ...) as its **only** parameters. There is no
`query_name`, `logql`, or `promql` parameter anywhere on this surface -- a
model selects a query by selecting a *tool*, so "pass a raw query string"
is not a call shape that can even be constructed, never mind refused.

**Absence is never zero.** Every result carries `data.coverage`
(`complete`, `gaps`, `records_returned`, `records_available`), built from
`logs_loki.coverage_from_loki`/`metrics_prometheus.
coverage_from_prometheus_history` -- the same "declared, not derived"
discipline `checks.py` already applies to `unevaluated` vs `broken`. A
window with zero records is not reported as a bare empty list: `coverage.
gaps` says whether that is a real negative or a reason this read cannot
support one (a scrape gap, a series never observed, a severity Loki's
pipeline never carries, or a failed query).

**A third registration class.** `_read_only_tool` (a passive device read)
and `_active_probe_tool` (B-493, traffic toward a caller-chosen address)
both existed; neither honestly describes a tool that changes no device
state, generates no device-chosen traffic, and never reaches a device at
all -- it reaches a *different*, operator-configured subsystem outside the
per-platform command allowlist. `_external_source_tool` is that third
class: same sanitisation boundary (`_register_sanitized_tool`), a
distinguishing annotation `title` ("EXTERNAL SOURCE — queries
Loki/Prometheus, not a device") without `open_world_hint` (the destination
is one fixed, configured address, not an arbitrary one), and its own gate:

- `NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES` -- default **enabled**, the
  opposite posture from `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`. Unlike an
  active probe, a model cannot choose *where* these calls go (Loki/
  Prometheus's URL is operator-configured, `NETTOOLS_LOKI_URL`/
  `NETTOOLS_PROMETHEUS_URL`, never a tool parameter), so the risk B-493
  exists to close -- a model steering traffic toward an address it chose
  -- does not apply. The gate still exists for a real, non-hypothetical
  reason: a deployment with no Loki/Prometheus reachable would otherwise
  pay a timeout on every call for a tool that can never succeed there. Set
  it to `0`/`false`/`no`/`off` to disable; an unrecognized value stays
  **enabled** (the ordinary, non-B-493 convention -- see `settings.py`).
- Enforced at **registration**, inside `_register_sanitized_tool`/
  `_external_source_tool`, the same mechanical place the other two gates
  live -- a tool registered through it inherits the check with no diff to
  `server.py`.
- A refusal is classified (`mcp_server/boundary.py`'s `ERROR_KINDS`,
  "external evidence sources are disabled"), never a silent no-op.

### External-source tools: NetBox (this task)

`get_lab_netbox_inventory` and `get_lab_netbox_topology` are the third
external source registered through `_external_source_tool`, the same class
and the same `NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES` gate Loki/Prometheus
already use above. Each wraps exactly one entry of
`netbox.NETBOX_READ_QUERIES` (`device_inventory`/`cable_topology`); neither
takes a parameter at all -- this lab's whole recorded inventory (nine
devices, fifteen cables) fits one HTTP call each, so there is no
`device_name`/filter slot to add, let alone one a caller could fill with a
query string.

**Derived, never authoritative -- stated in both tool descriptions, not just
here.** NetBox is populated by this project's own collector
(`netbox.write_records`, `src/agent_nettools/netbox.py`) from a past parsed
device evidence collection; it is a *recording*, not a live read, and it can
be stale or -- since NetBox is a shared system this collector does not own
exclusively -- hand-edited by something else. `get_lab_netbox_inventory` is
also deliberately distinct from `list_lab_devices`: that tool reads the
*declared* inventory (`inventory/lab.yaml`) and contacts nothing; this tool
reads what was last *collected* from the live devices. Every record carries
its own `last_updated`, and each read's `data.parsed.meta.oldest_last_updated`/
`newest_last_updated` summarise the whole batch, so a caller can judge
staleness without scanning every row. `data.parsed.meta.truncated` is `true`
only if NetBox reports more records than one call returned (never observed
at this lab's scale, but checked rather than assumed).

**Free text crosses the same boundary, by reusing a name rather than adding
one.** A NetBox device/cable's `description` field is operator-editable —
empty today at this lab, but not guaranteed to stay that way, since NetBox is
shared. `description` was already a member of `mcp_server.boundary`'s flat
free-text field-name set (from the `interface` template's own
`("interface", "description")` entry in
`agent_nettools.model_egress.FREE_TEXT_FIELDS`), so reusing that exact field
name here adds **zero** new entries to that table and the quoting is
automatic — the same move `logs_loki.py` made for `text`/`code`. NetBox's
second free-text field, `comments`, is deliberately **not** exposed in v1;
see `netbox.py`'s "What is (and is not) free text here" section for why.

`get_lab_netbox_topology`'s cables are held to a **stricter** bar than a live
`check_lab_lldp_neighbors` read: `netbox.write_records` only ever records a
cable when both ends' LLDP evidence mutually agreed at collection time (see
`netbox.py`'s "Cabling" section) -- a one-sided or disagreeing report never
becomes a NetBox `Cable` at all.

### External-source tool: neo4j (B-517)

`get_lab_graph_topology` is the fourth external source, the same
`_external_source_tool` class and gate as the three above. It wraps
`graph.NEO4J_READ_QUERIES["topology"]`, one fixed pair of Cypher statements
(nodes, then edges) -- no filter parameter, no Cypher parameter, the same
"the whole recorded graph fits one call" reasoning `get_lab_netbox_inventory`/
`get_lab_netbox_topology` give for taking none either.

Checked live before building anything: 2026-08-19, `docker exec ...
cypher-shell` against the running container found the graph EMPTY --
`MATCH (n) RETURN count(n)` and `MATCH ()-[r]->() RETURN count(r)` both
returned `0`. A read tool over an empty graph would have told a model "no
topology exists" in a fabric that plainly has one -- worse than no tool at
all, B-509's precedent for NetBox itself before that store had real data. So
no tool was built then. Credentials moved from the neo4j container's own
`NEO4J_AUTH` into the gitignored `.env`, `graph.build_graph`/`graph.write_graph`
were run against this lab's live evidence, and the write was verified two
ways (`write_graph`'s own return value, and directly with `cypher-shell`):
**9 nodes, 29 relationships.** This tool is the read half that comment said
belonged here once the graph held real data.

**Derived, never authoritative -- stated in the tool's own description, same
as NetBox's.** The graph is a *projection* of this project's own parsed
LLDP/IS-IS evidence, replaced wholesale each time the collector runs, never a
live read and never a second place topology
gets typed in. `data.parsed.nodes` lists every device the graph has evidence
for, **including one with zero edges on every protocol** -- a real and
interesting shape (an isolated device), deliberately not hidden by an
edges-only response. `data.parsed.edges` lists every LLDP or IS-IS adjacency
separately, each carrying which `protocol` reported it; LLDP and IS-IS are
**never merged into one edge**, because a link can be a clean LLDP edge with
no IS-IS edge at all and merging them would erase that fact. Each edge's
`interface_a`/`interface_b` is that end's own reported local interface, in
whatever spelling that device used -- may differ in abbreviation from the
other end's, or from another tool's report of the same port; see
`interface_kind.canonical` before comparing it against one from a different
source.

### `get_lab_sr_policy_detail` (B-515)

`check_lab_sr_policies` reports policy-level state only (colour, endpoint,
admin/operational state, binding SID) -- enough to say a policy is down, not
enough to say *why*. Measured live (MCP §14b, 2026-08-19): a model correctly
diagnosed a down SR-TE policy as "no candidate path resolves" and then could
not name which SID or segment list was involved, because nothing exposed the
candidate-path detail the device already prints for
`show segment-routing traffic-eng policy color <n> endpoint ipv4 <ip> detail`.

`get_lab_sr_policy_detail(device_name, policy_id)` is that command, as a
validated, parameterised template (`templates.PLATFORM_TEMPLATES['cisco_xr']
['sr_policy_detail']`) rather than a static allowlist entry -- it takes one
caller-supplied value. `policy_id` is `"<color>:<endpoint>"`, e.g.
`"20:10.255.0.13"`, the exact shape `check_lab_sr_policies`' own `policy`
field already reports for each row, so a value copied from that tool's
output is a valid call here. It is split into the template's two declared
parameters (`color`: `BoundedIntParam`, `endpoint`: `IPv4AddressParam` --
both already-existing param types, not a new one) by
`templates.split_sr_policy_id` before rendering; a malformed id is refused,
classified, before any device is contacted.

`data.parsed.records` holds the resolved SID stack, in order, for an
Explicit candidate path over a valid segment list -- empty for a Dynamic
path that has not resolved. `data.parsed.meta.last_error` (e.g. "No path
found") is device-authored free text and crosses the boundary quoted, the
same treatment `bgp_neighbor`'s `last_reset_reason` already gets.
`data.parsed.meta.found` is `false` -- distinguishably from a transport
error -- when this device has no policy at that colour/endpoint at all;
IOS-XR answers a non-matching colour/endpoint with nothing, not an error.

### Reading a ticket back: `list_lab_tickets` / `read_lab_ticket` (B-680)

The operator's own scenario is *event > notification > investigation > rca >
notification, then the operator picks up the ticket through LM Studio for
more investigation*. Without a read tool, that pickup starts from nothing and
re-asks a model what `nettools investigate`'s flight recorder
(`ticket.py`, `NETTOOLS_TICKET_DIR`) already recorded. These two tools close
that gap, and only that gap -- both are `_read_only_tool`, like every check
above: a ticket is a local file, not an external service or a device, so
neither gate (`NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`/
`NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES`) applies to either.

`list_lab_tickets(limit=20, include_closed=false)` takes **zero required
parameters** -- an operator should not need to already know a `run_id` to ask
"what needs attention". It defaults to OPEN tickets only, most recently
opened first. `read_lab_ticket(run_id)` takes exactly one parameter, and it
is deliberately `run_id`, **never a path**: `ticket.read_ticket` takes a path
because its existing callers already have one, but an MCP tool's caller is a
model, and a path-shaped parameter on a tool a model can call is an
arbitrary-file-read vulnerability. `run_id` is `ticket.TicketRecorder.open`'s
own existing join key, and `notifier.py`'s notification (B-681, below)
carries the exact string this parameter wants, so an operator reading a
Telegram alert already has what they need to type here.

**The containment is a new guarantee, not an inherited one.**
`ticket.py`'s own forgery defenses (`_heading_safe`/`_blockquote`, OBS-176)
were proven on the WRITE path: a caller string or a model's own response
carrying a forged `## Outcome update` heading and a fenced
`json-ticket-section` block cannot become a real section when the file is
parsed back. That says nothing about what happens once the resulting dict is
handed to a SECOND model through a tool call -- a JSON string is
syntactically inert, but a model reading its *content* does not parse JSON,
it reads prose, and unmarked prose that looks like a forged verdict is
exactly the shape of thing an instruction-following model can be steered by.
Nothing before this tool tested that path. `agent_nettools.ticket_read`
wraps every operator/event/model-authored field (`question`, `subject`, a
device-adjacent `excerpt`, and -- uniquely to a ticket -- a prior model's own
`response_text`) in `model_egress.quote_device_text`'s untrusted-content
delimiters, the SAME primitive `mcp_server.boundary.sanitize` already uses
for a parsed record's free text, before either tool returns anything.
Mutation-tested by `scripts/mutate_guards.py`'s `TICKET-READ-CONTAINMENT`
entry; the adversarial round trip is
`tests/test_ticket_read.py::test_a_forged_verdict_inside_a_models_prior_
response_is_contained_on_read`.

**Code-observed stays apart from model-claimed, on read too.**
`ticket.py`'s central rule -- the ticket records what the CODE observed,
never what a model SAYS it did (OBS-165) -- only means something here if the
read tool preserves the split rather than flattening every section into one
blob. `read_lab_ticket`'s `data.ticket` carries `code_observed` (the
question, the tool/device timeline, evidence provenance, and `answer` --
`ticket.record_answer`'s own docstring: "the deterministic descent's own
answer... never a model's", returned unwrapped, exactly as trustworthy as
when the code wrote it) separately from `model_claimed` (every
`record_model_exchange`, under a static, tool-authored `warning` restating
what OBS-165 measured -- present even when there is nothing to warn about, so
"no warning shown" is never mistaken for "nothing to be careful of"). A
model reading its own predecessor's prior claims as if they were established
fact is OBS-165 with an extra hop; this tool hands over the claim, but never
without the label.

`data.found` is `false` -- not an error -- when no ticket matches a
`run_id`; that is a normal outcome (stale, mistyped, or a pruned ticket), the
same "absence is a normal answer" shape `get_lab_sr_policy_detail` already
uses. Neither tool ever populates `errors`: there is nothing in either call
path that can fail in a way `mcp_server.boundary.ERROR_KINDS` needs to
classify.

### Snapshots, diffing, health, and flap detection (Phase 8)

Through Phase 7 these were CLI-only (`nettools diff`/`baseline`/`health`/
`flaps`) -- the MCP client is an LLM, the primary consumer of this server,
and it could not do drift detection or get a health verdict at all. Every
tool below is a thin wrapper over an existing, already-safe
`network_tools.py`/`health.py` function; none of them open a new device-access
path.

- `diff_lab_device_against_latest` / `diff_lab_device_against_golden` --
  collect fresh evidence, save it as the new "latest" snapshot, and diff it
  against the device's most recent snapshot or its pinned golden baseline.
  `data.has_previous` is `false` (and `data.diff` is `null`) the first time
  there is nothing to compare against yet -- not an error.
- `assess_lab_device_health` / `assess_lab_fabric_health` -- deterministic
  health verdicts (Phase 4 role invariants + baseline drift), for one device
  or the whole fabric, rule-based rather than an LLM call.
- `detect_lab_flaps` -- report fields that oscillated across a device's
  entire saved snapshot history (min 3 transitions by default), which a
  single pairwise diff cannot see.

### `investigate_lab_session` -- the one to reach for first

`investigate_lab_session(device, subject, flow="bgp_session")` walks a
dependency ladder beneath a symptom -- session, transport, route, IGP
adjacency, physical interface -- and reports the **lowest** broken layer as the
cause, with the broken layers above it as the causal chain explaining the
symptom. Every verdict is code comparing parsed fields; **no model is involved
and none is called**, and the report is rendered from the descent's own typed
fields rather than written by one.

It exists because the other tools (36 at last count — the list above is authoritative) answer *what is the state of X*, and
the question an operator actually has is *why is this broken*. Answering that by
calling six tools and reasoning over the results is exactly where a model
invents a plausible chain; this returns one that was derived.

Read `finding` first: `all_layers_healthy`, `no_fault_on_path` (the session is
fine and the broken layers listed under `off_path` are **not** on the path --
do not report them as a cause), `cause_not_localised`, `undetermined`,
`temporally_incoherent` (the fabric moved while being read), or the terminal
finding for the lowest broken layer. Check `trustworthy` before reporting
anything, and repeat `coherence.caveat` to the user when it is present.

### Read-only annotations, resources, and a prompt (Phase 8)

Every tool above is registered with a `readOnlyHint` annotation
(`mcp.types.ToolAnnotations(read_only_hint=True)`) so a client can act on the
safety guarantee without inspecting this server's source -- see
`server.READ_ONLY_ANNOTATIONS_SUPPORTED` for whether the installed MCP SDK
accepted it (an older SDK's `tool()` decorator without an `annotations=`
parameter degrades to the bare decorator instead of crashing the server).
`get_lab_ping`/`get_lab_traceroute` additionally carry `open_world_hint=True`
and a distinguishing `title` (`server.ACTIVE_PROBE_ANNOTATIONS_SUPPORTED`,
B-473) -- see "Active probes" above. `get_lab_logs`/
`get_lab_interface_rate_history`/`get_lab_isis_adjacency_history`/
`get_lab_ldp_session_history`/`get_lab_device_uptime_history`/
`get_lab_netbox_inventory`/`get_lab_netbox_topology`/`get_lab_graph_topology`
carry a distinguishing `title` of their own without `open_world_hint`
(`server.EXTERNAL_SOURCE_ANNOTATIONS_SUPPORTED`, B-512) -- see "External-
source tools" above.

Two MCP **resources** let a client ground itself without spending a tool
call:

- `lab://inventory` -- the same data `list_lab_devices` returns.
- `lab://topology/expected` -- each device's derived expected topology counts
  (`nettools learn-topology`); a device's value is `null` when no baseline
  has ever been derived for it, never a fabricated zero.

One MCP **prompt**, `troubleshooting_prompt`, exposes this project's own
network-troubleshooting system prompt (`llm_analysis.TROUBLESHOOTING_PROMPT`,
the same text `nettools analyze` sends to whichever LLM provider is
configured) so a client can reuse it instead of inventing its own framing.

## Start Manually

From the project root with the virtual environment active:

```bash
make mcp
```

The server communicates over standard input/output and may appear quiet while
waiting for a client. Stop it with `Ctrl+C`.

## MCP Client Configuration

After `pip install -e .` the server is a console script, so no `PYTHONPATH` is
needed. Use absolute paths and replace `/path/to/project` with the repo path.

```json
{
  "mcpServers": {
    "ios-xr-lab": {
      "command": "/path/to/project/.venv/bin/nettools-mcp"
    }
  }
}
```

Or run it in Docker (see the top-level `Dockerfile`):

```json
{
  "mcpServers": {
    "ios-xr-lab": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--network", "host",
               "--env-file", "/path/to/project/.env", "ios-xr-nettools-mcp"]
    }
  }
}
```

Start the client from an environment where `DEVICE_USERNAME` and
`DEVICE_PASSWORD` are set so the MCP process inherits them. Never copy the
password into this JSON.

## Authenticated HTTP Docker API

For LAN/VPN MCP clients, the same image can expose the native MCP
`streamable-http` transport. It is protected by a required bearer token; keep
the token in an environment file or secret manager, never in an image or
client configuration committed to source control.

```bash
docker run --rm -p 8000:8000 --env-file /path/to/project/.env \
  -e NETTOOLS_MCP_TRANSPORT=streamable-http \
  -e NETTOOLS_MCP_HOST=0.0.0.0 \
  -e NETTOOLS_MCP_PORT=8000 \
  -e NETTOOLS_MCP_HTTP_BEARER_TOKEN="$(openssl rand -hex 32)" \
  ios-xr-nettools-mcp
```

The MCP endpoint is `http://host:8000/mcp`. Clients must send
`Authorization: Bearer <token>` for every request; missing or invalid tokens
receive `401` before MCP sees the request. Use `NETTOOLS_MCP_TRANSPORT=sse`
only for older MCP clients that do not support streamable HTTP.

This mode is for trusted LAN/VPN deployment. Public-internet exposure, TLS
termination, multi-user identity, and RBAC remain unsupported.

## Persistent Compose Deployment

`docker-compose.mcp-http.yml` is the reproducible deployment for this lab. It
joins `sota_mgmt` for router SSH plus `sota-lab-platform_labnet` and
`sota-lab-platform_default` for platform services. It resolves NetBox and
Neo4j through Docker service DNS, not transient subnet IPs, persists
tickets/evidence/event state in the `ios-xr-nettools-mcp-state` volume, and
restarts the MCP container after reboot.

On its first run, the initializer copies the existing local `tickets/`
directory into an otherwise empty persistent ticket store. Later starts never
overwrite the volume's tickets.
```bash
cp .env.mcp-http.example .env.mcp-http
# Set a real NETTOOLS_MCP_HTTP_BEARER_TOKEN in .env.mcp-http.
docker compose -f docker-compose.mcp-http.yml up -d --build
```

The default port publication is `0.0.0.0:8000`, so trusted LAN/VPN clients can
connect directly:

```bash
http://<a4000-host-or-ip>:8000/mcp
```

This endpoint uses plain HTTP. Bearer authentication remains required, but
there is no transport encryption; never expose it directly to the public
internet.

Before the first start, refresh the dedicated strict SSH trust store only after
verifying recreated-router fingerprints through the lab control plane:

```bash
python3 scripts/enroll_host_keys.py
```

## Verify

```text
[ ] list_lab_devices returns the nine IOS-XR devices.
[ ] Device results are structured dictionaries.
[ ] Credentials do not appear in tool output.
[ ] run_command is not available.
[ ] Shell and configuration tools are not available.
[ ] Every listed tool carries a readOnlyHint annotation (server.READ_ONLY_ANNOTATIONS_SUPPORTED).
[ ] lab://inventory and lab://topology/expected resources are listed and readable.
[ ] The troubleshooting_prompt prompt is listed and returns text.
```

## Why there is no snapshot-writing tool here

`save_lab_snapshot` and `pin_lab_golden_snapshot` were exposed until B-438 and
have been removed. Both performed a **persistent write** from behind a decorator
named `_read_only_tool`, and `pin_lab_golden_snapshot` wrote to the thing the
system uses as its own epistemic ground truth: a model could pin an outage state
as golden, after which drift comparison suppresses that fault indefinitely.

That contradicted the rules that execution is never behind MCP and memory is
derived, never authored. The external-review correction remains in Git history.

The diff tools also stopped persisting their fresh collection, which the CLI
still does. Snapshot history is what `detect_lab_flaps` reads, so a model
calling diff in a loop was reshaping the evidence a later flap analysis would
see. Repeated diffs here now compare against a **stable** baseline.

**Pinning a golden snapshot is a human action.** `nettools baseline pin DEVICE`.

The guarantee is structural rather than a decorator's name: this module does not
import `save_snapshot` or `save_golden_snapshot` at all, so no tool it exposes
can reach one. `tests/test_mcp_server.py` asserts that.

## Two surfaces (B-479)

`NETTOOLS_MCP_SURFACE` selects what this server registers:

- **`classic`** (default) — the full per-function tool set listed above,
  byte-identical in behaviour to before the flag existed. This is the
  **expert** profile for manual, granular inspection.
- **`staged`** — five stage-shaped tools plus a probe (`explore_lab`,
  `check_lab`, `lookup_lab`, `investigate_lab`, `history_lab`, `probe_lab`),
  each a thin composition of the same already-safe functions through the same
  sanitisation boundary, with the active probe still separately annotated.
  This is the **guided** profile for model-assisted troubleshooting.
  `investigate_lab` supports all four implemented flows: `bgp_session`,
  `interface`, `isis_adjacency`, and `ldp_session`. Event routing currently
  produces only BGP and interface flows; IS-IS and LDP are available through
  explicit guided MCP calls until measured event mappings exist.

Both exist because the historical tool-selection A/B needs both surfaces
measurable; the staged manifest is
deliberately smaller than the classic one — a test asserts it. Guided/staged
is the recommended LM Studio profile; classic remains the compatibility default
until the outstanding surface-level measurement supports a default flip.
