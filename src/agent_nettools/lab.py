"""Cisco IOS-XR lab device inventory (single user).

This module holds the static list of IOS-XR devices in the lab. Only device
names and management IP addresses live here. Credentials are supplied at
runtime through environment variables and are never stored in the repository.
"""

from __future__ import annotations

# Device name -> management IPv4 address.
# Only the Cisco IOS-XR nodes are listed here. The linux CE nodes are not
# reachable with the cisco_xr driver and are intentionally excluded.
DEVICES = {
    "P1": "172.20.250.11",
    "P2": "172.20.250.12",
    "P3": "172.20.250.13",
    "P4": "172.20.250.14",
    "PE1": "172.20.250.21",
    "PE2": "172.20.250.22",
    "PE3": "172.20.250.23",
    "PE4": "172.20.250.24",
    "RR1": "172.20.250.31",
}


def all_devices() -> dict[str, str]:
    """Return a copy of ``{device_name: management_ip}`` for every lab device."""

    return dict(DEVICES)
