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

Approved read-only commands:

- `show running-config hostname`
- `show version`
- `show interfaces brief`
- `show bgp summary`
- `show lldp neighbors`
- `show isis neighbors`
- `show segment-routing traffic-eng policy`

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
make mcp         # Start the MCP server over stdio
make inspect     # Smoke-test the MCP server
```

Target another device:

```bash
make facts DEVICE=RR1
# or directly:
nettools facts RR1
```

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
src/agent_nettools/lab.py          IOS-XR device inventory (name -> mgmt IP)
src/agent_nettools/inventory.py    Env credentials + lab -> device dicts
src/agent_nettools/network_tools.py  Allowlist, SSH, evidence, fabric, diff
src/agent_nettools/llm_analysis.py   Provider selection + analysis
src/agent_nettools/cli.py          The `nettools` command-line entry point
mcp_server/                        Read-only MCP server
tests/                             Inventory, tool, provider, docs, safety tests
docs/                              devices.md and the code review
```
