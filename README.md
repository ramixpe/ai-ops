# IOS-XR Read-Only Network Tools

Safe, read-only network automation for a single-user Cisco IOS-XR lab, driven by
Python, Netmiko, an LLM reasoning layer, and MCP.

> Good agents are built on boring tools that work.

## What This Does

- Loads a fixed inventory of IOS-XR devices from `agent_nettools.lab`.
- Takes device credentials from the environment (`DEVICE_USERNAME`, `DEVICE_PASSWORD`).
- Runs only a small allowlist of approved read-only `show` commands.
- Returns structured evidence and, optionally, grounded LLM analysis.
- Exposes the same narrow tools through MCP, with no shell or config access.

## Environment

Two variables must be set (locally or in an ignored `.env`):

```text
DEVICE_USERNAME
DEVICE_PASSWORD
```

There is no per-user selection. The whole IOS-XR inventory is
available. `PE1` is the default device when none is given.

## Inventory

Management network `172.20.250.0/24`:

| Name | Management IP |
|------|---------------|
| P1   | 172.20.250.11 |
| P2   | 172.20.250.12 |
| P3   | 172.20.250.13 |
| P4   | 172.20.250.14 |
| PE1  | 172.20.250.21 |
| PE2  | 172.20.250.22 |
| PE3  | 172.20.250.23 |
| PE4  | 172.20.250.24 |
| RR1  | 172.20.250.31 |

The full device table (with roles) lives in [docs/devices.md](docs/devices.md).
The linux CE nodes are not IOS-XR and are intentionally excluded.

## Quick Start

```bash
make setup
source .venv/bin/activate
make test
make inventory
make facts
```

`make inventory` lists the nine devices without showing credentials.
`make facts` contacts PE1 using only `show running-config hostname` and
`show version`.

## Safety Boundary

Commands are declared per platform, so a check runs across a mixed fabric without
one vendor's syntax ever reaching another's device. The allowlist is exact-match
and is verified before credentials are loaded or a socket is opened.

Approved read-only commands, by platform:

### cisco_xr

- `show running-config hostname`
- `show version`
- `show interfaces brief`
- `show bgp summary`
- `show lldp neighbors`
- `show isis neighbors`
- `show segment-routing traffic-eng policy`

### cisco_iosxe

- `show version`
- `show ip interface brief`
- `show ip bgp summary`
- `show lldp neighbors`
- `show isis neighbors`

### juniper_junos

- `show version`
- `show interfaces terse`
- `show bgp summary`
- `show lldp neighbors`
- `show isis adjacency`

Only `cisco_xr` is verified against a live device and captured in
`tests/fixtures/`. The other two are declared from vendor documentation and have
no device to verify against yet.

There is no configuration mode, reload, commit, rollback, shell access, or a
generic `run_command(device, command)` tool.

## Common Commands

Everything is driven by the `nettools` CLI; the Makefile targets are thin
wrappers. Run `make help` for the full list.

```bash
make help        # Show the command menu
make test        # Run all tests
make lint        # Run Ruff
make inventory   # List devices without credentials
make facts       # hostname + version on PE1
make interfaces  # interface status on PE1
make bgp         # BGP summary on PE1
make lldp        # LLDP neighbors on PE1
make isis        # IS-IS neighbors on PE1
make sr          # SR-TE policies on PE1
make fabric-bgp  # BGP summary across the whole inventory
make analyze     # Collect evidence and analyze with the selected LLM
make demo        # Narrated agent demo
make diff        # Diff evidence against the last snapshot
make capture     # Recapture test fixtures from the whole lab
make mcp         # Start the MCP server over stdio
make inspect     # Smoke-test the MCP server
```

Target another device:

```bash
make facts DEVICE=RR1
# or directly:
nettools facts RR1
```

## Test Fixtures

`tests/fixtures/` holds real IOS-XR output captured from all nine devices as two
snapshots ~90s apart (`t0` and `t1`) on a quiet fabric. Parser, diff, and
health-rule work is developed and tested against these, so it needs no lab
access. `nettools capture` refreshes them; review the git diff by eye before
committing, since fixtures are permanent once pushed.

Replay a capture offline with `load_fixture_evidence("PE1", label="t0")` — it
returns the same structure a live `collect_evidence` call would.

## BYOK Reasoning Provider

`nettools analyze` and `nettools demo` need a reasoning provider: Anthropic,
OpenAI, or a local keyless Ollama model. Keep keys in the local environment or
an ignored `.env` file (`cp .env.example .env` to start).

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
```

or:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

or a local, keyless model served by Ollama:

```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=ornith:9b-q8_0
```

## Repository Map

```text
src/agent_nettools/platforms.py    Per-platform allowlist and intent table
src/agent_nettools/lab.py          Device inventory (name -> mgmt IP) + platform
src/agent_nettools/inventory.py    Env credentials + lab -> device dicts
src/agent_nettools/network_tools.py  Allowlist, SSH, evidence, fabric, diff
src/agent_nettools/llm_analysis.py   Provider selection + analysis
src/agent_nettools/cli.py          The `nettools` command-line entry point
src/agent_nettools/fixtures.py     Capture real device output; replay it offline
mcp_server/                        Read-only MCP server
tests/                             Inventory, tool, provider, docs, safety tests
tests/fixtures/                    Captured real IOS-XR output (t0/t1 pairs)
docs/                              devices.md and the code review
```
