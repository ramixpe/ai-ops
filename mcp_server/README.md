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

There is no shell, configuration tool, or generic command runner.

The last six tools above are validated, parameterized templates (Phase 5):
each accepts one caller-supplied value (an IPv4 address/prefix, an interface
name, or a bounded count) which is parsed into a typed object and the command
is rendered from that object's own canonical form -- never passed through as
text. `get_lab_ping` and `get_lab_traceroute` are active probes: they generate
traffic (unlike every other tool listed) even though they change no device
state, and are gated by the `NETTOOLS_ALLOW_ACTIVE_PROBES` environment
variable (default enabled).

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
```
