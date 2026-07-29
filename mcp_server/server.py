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
    get_bgp_neighbor,
    get_device_facts,
    get_interface,
    get_logging,
    get_route,
    list_devices,
    ping_device,
    traceroute_device,
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


@mcp.tool()
def get_lab_route(device_name: str, prefix: str) -> dict:
    """Look up a specific route on a lab device.

    ``prefix`` must be an IPv4 address or CIDR prefix, e.g. "10.255.0.31" or
    "10.0.0.0/24" -- validated and rendered from its parsed, canonical form
    (never passed through as text); anything else is rejected before any
    connection is made. Narrow this after seeing a route-related anomaly in
    other evidence (e.g. a missing or unexpected next hop).
    """

    return get_route(device_name, prefix)


@mcp.tool()
def get_lab_bgp_neighbor(device_name: str, address: str) -> dict:
    """Look up a specific BGP neighbor on a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". Use this to
    narrow in on one peer after ``check_lab_bgp_neighbors`` shows it Idle or
    otherwise not Established.
    """

    return get_bgp_neighbor(device_name, address)


@mcp.tool()
def get_lab_interface(device_name: str, name: str) -> dict:
    """Look up a specific interface's status on a lab device.

    ``name`` must be a valid interface name, e.g. "GigabitEthernet0/0/0/1",
    "Gi0/0/0/2.300", or "Loopback0" -- validated against an anchored
    letters/digits/``._/-`` charset, so it can never carry a shell or CLI
    metacharacter.
    """

    return get_interface(device_name, name)


@mcp.tool()
def get_lab_logging(device_name: str, count: int = 20) -> dict:
    """Show a lab device's most recent log lines.

    ``count`` must be a plain integer from 1 to 500 (default 20).
    """

    return get_logging(device_name, count)


@mcp.tool()
def get_lab_ping(device_name: str, address: str) -> dict:
    """Ping an IPv4 address from a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". This is an
    active probe: it generates ICMP traffic (unlike every other tool here)
    even though it changes no device configuration, and is refused when the
    server has ``NETTOOLS_ALLOW_ACTIVE_PROBES`` set to a falsy value.
    """

    return ping_device(device_name, address)


@mcp.tool()
def get_lab_traceroute(device_name: str, address: str) -> dict:
    """Traceroute to an IPv4 address from a lab device.

    ``address`` must be a plain IPv4 address, e.g. "10.255.0.31". An active
    probe like ``get_lab_ping``: generates traffic, changes no device state,
    and is refused when ``NETTOOLS_ALLOW_ACTIVE_PROBES`` is set to a falsy
    value.
    """

    return traceroute_device(device_name, address)


def main() -> None:
    """Console-script entry point: start the MCP server over stdio."""

    mcp.run()


if __name__ == "__main__":
    main()
