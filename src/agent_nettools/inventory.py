"""Build the live device inventory from the lab definition and environment credentials.

There is a single user. Every Cisco IOS-XR device in ``agent_nettools.lab`` is
available. Credentials come from ``DEVICE_USERNAME`` and ``DEVICE_PASSWORD`` and
are never stored in the repository.
"""

from __future__ import annotations

import os
from typing import Any

from .lab import all_devices

DEFAULT_DEVICE_NAME = "PE1"


class InventoryError(ValueError):
    """Raised when the device inventory environment is missing or invalid."""


def _required_environment(name: str) -> str:
    """Return one required environment variable or raise a clear error."""

    value = os.getenv(name, "").strip()
    if not value:
        raise InventoryError(f"Required environment variable is not set: {name}")
    return value


def load_inventory() -> list[dict[str, Any]]:
    """Return every IOS-XR lab device with credentials from the environment.

    Authentication is by shared password by default. If ``DEVICE_SSH_KEYFILE``
    is set, key-based auth is used and ``DEVICE_PASSWORD`` becomes an optional
    key passphrase.
    """

    username = _required_environment("DEVICE_USERNAME")
    key_file = os.getenv("DEVICE_SSH_KEYFILE", "").strip()
    if key_file:
        password = os.getenv("DEVICE_PASSWORD", "").strip()
    else:
        password = _required_environment("DEVICE_PASSWORD")

    return [
        {
            "name": device_name,
            "hostname": management_ip,
            "platform": "cisco_xr",
            "username": username,
            "password": password,
            "key_file": key_file or None,
            "port": 22,
        }
        for device_name, management_ip in all_devices().items()
    ]


def get_device(device_name: str) -> dict[str, Any]:
    """Return one lab device by name."""

    for device in load_inventory():
        if device["name"] == device_name:
            return device

    raise InventoryError(f"Device is not in the lab inventory: {device_name}")


def get_default_device_name() -> str:
    """Return the default device (PE1), or the first device when PE1 is absent."""

    devices = load_inventory()
    for device in devices:
        if device["name"] == DEFAULT_DEVICE_NAME:
            return str(device["name"])
    return str(devices[0]["name"])
