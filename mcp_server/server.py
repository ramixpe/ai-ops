"""MCP server exposing approved read-only IOS-XR network tools."""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv

try:
    # MCP SDK >= 2.0 renamed the high-level server to MCPServer.
    from mcp.server.mcpserver import MCPServer as FastMCP
except ModuleNotFoundError:
    # MCP SDK < 2.0 exposed it as FastMCP.
    from mcp.server.fastmcp import FastMCP

from agent_nettools.network_tools import (
    check_bgp_neighbors,
    check_fabric,
    check_interfaces,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    get_device_facts,
    list_devices,
)

# MCP clients (and `make mcp` / `nettools-mcp`) launch this server directly, so it
# has to load .env itself — otherwise every tool fails on a missing
# DEVICE_USERNAME / DEVICE_PASSWORD. Two lookups so it works both from the
# current directory upward and next to an editable install; a no-op in Docker,
# where the credentials arrive via -e / --env-file.
load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

mcp = FastMCP("IOS-XR Read-Only Network Tools")


@mcp.tool()
def list_lab_devices() -> dict:
    """List the IOS-XR devices available in the lab inventory."""

    return list_devices()


@mcp.tool()
def get_lab_device_facts(device_name: str) -> dict:
    """Collect basic read-only facts from a lab device."""

    return get_device_facts(device_name)


@mcp.tool()
def check_lab_interfaces(device_name: str) -> dict:
    """Collect read-only interface status from a lab device."""

    return check_interfaces(device_name)


@mcp.tool()
def check_lab_bgp_neighbors(device_name: str) -> dict:
    """Collect read-only BGP neighbor state from a lab device."""

    return check_bgp_neighbors(device_name)


@mcp.tool()
def check_lab_lldp_neighbors(device_name: str) -> dict:
    """Collect read-only LLDP neighbor state from a lab device."""

    return check_lldp_neighbors(device_name)


@mcp.tool()
def check_lab_isis_neighbors(device_name: str) -> dict:
    """Collect read-only IS-IS neighbor state from a lab device."""

    return check_isis_neighbors(device_name)


@mcp.tool()
def check_lab_sr_policies(device_name: str) -> dict:
    """Collect read-only Segment Routing TE policy state from a lab device."""

    return check_sr_policies(device_name)


@mcp.tool()
def check_lab_fabric(check: str = "bgp") -> dict:
    """Run one read-only check (facts|interfaces|bgp|lldp|isis|sr) across the fabric."""

    return check_fabric(check)


@mcp.tool()
def collect_lab_evidence(device_name: str) -> dict:
    """Collect the full read-only evidence bundle from a lab device in one session."""

    return collect_evidence(device_name)


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""

    mcp.run()


if __name__ == "__main__":
    main()
