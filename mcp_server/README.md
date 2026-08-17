# Read-Only IOS-XR MCP Server

This server exposes reviewed network inspection functions as MCP tools. It reads
the device inventory from `agent_nettools.lab` and device credentials from the
environment (`DEVICE_USERNAME`, `DEVICE_PASSWORD`).

## Exposed Tools

- `list_lab_devices`
- `get_lab_device_facts`
- `check_lab_interfaces`
- `check_lab_bgp_neighbors`
- `check_lab_lldp_neighbors`
- `check_lab_isis_neighbors`
- `check_lab_sr_policies`
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

There is no shell, configuration tool, or generic command runner.

The `get_lab_route`/`get_lab_bgp_neighbor`/`get_lab_interface`/
`get_lab_logging`/`get_lab_ping`/`get_lab_traceroute` tools are validated,
parameterized templates (Phase 5): each accepts one caller-supplied value (an
IPv4 address/prefix, an interface name, or a bounded count) which is parsed
into a typed object and the command is rendered from that object's own
canonical form -- never passed through as text. `get_lab_ping` and
`get_lab_traceroute` are active probes: they generate traffic (unlike every
other tool listed) even though they change no device state, and are gated by
the `NETTOOLS_ALLOW_ACTIVE_PROBES` environment variable (default enabled).

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

### Read-only annotations, resources, and a prompt (Phase 8)

Every tool above is registered with a `readOnlyHint` annotation
(`mcp.types.ToolAnnotations(read_only_hint=True)`) so a client can act on the
safety guarantee without inspecting this server's source -- see
`server.READ_ONLY_ANNOTATIONS_SUPPORTED` for whether the installed MCP SDK
accepted it (an older SDK's `tool()` decorator without an `annotations=`
parameter degrades to the bare decorator instead of crashing the server).

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

That contradicted **D12** (execution is never behind MCP) and **D14** (memory is
derived, never authored). External review, `docs/design/peer-review-response.md`
§3.1 — *"the architecture protects the managed network more carefully than it
protects its own source of truth."*

The diff tools also stopped persisting their fresh collection, which the CLI
still does. Snapshot history is what `detect_lab_flaps` reads, so a model
calling diff in a loop was reshaping the evidence a later flap analysis would
see. Repeated diffs here now compare against a **stable** baseline.

**Pinning a golden snapshot is a human action.** `nettools baseline pin DEVICE`.

The guarantee is structural rather than a decorator's name: this module does not
import `save_snapshot` or `save_golden_snapshot` at all, so no tool it exposes
can reach one. `tests/test_mcp_server.py` asserts that.
