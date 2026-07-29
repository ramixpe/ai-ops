"""Cisco IOS-XR lab device inventory (single user).

This module holds the static list of IOS-XR devices in the lab. Only device
names and management IP addresses live here. Credentials are supplied at
runtime through environment variables and are never stored in the repository.
"""

from __future__ import annotations

from .platforms import DEFAULT_PLATFORM

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


# Per-device platform overrides. Every node in this lab is IOS-XR, so the map is
# empty and every lookup falls through to the default; a mixed fabric adds
# entries here. Platform lives beside the device map rather than in
# ``inventory.py`` so it can be resolved *without* loading credentials, which is
# what lets the command allowlist be checked before any credential access.
# Phase 3 replaces this with per-device data from a declarative inventory.
PLATFORMS: dict[str, str] = {}


def all_devices() -> dict[str, str]:
    """Return a copy of ``{device_name: management_ip}`` for every lab device."""

    return dict(DEVICES)


def platform_for(device_name: str) -> str:
    """Return a device's platform without touching credentials.

    Unknown device names resolve to the default platform; the device itself is
    rejected later by ``inventory.get_device``. This function must never require
    credentials -- see the note on ``PLATFORMS``.
    """

    return PLATFORMS.get(device_name, DEFAULT_PLATFORM)
