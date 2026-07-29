"""Build the live device inventory from the declarative YAML file plus environment credentials.

There is a single user. Every device in ``inventory/lab.yaml`` (see
``inventory_model.py`` for the schema and resolution order) is available.
Credentials are named indirectly, by ``credential_group``: each group names the
environment variables to read, and are resolved here, at ``load_inventory()``
call time -- never stored in the repository, and never read by
``inventory_model.py`` or ``lab.py`` (that credential-free split is what lets
``lab.platform_for()`` resolve a platform before any credential access).
"""

from __future__ import annotations

from typing import Any

from .credential_resolver import get_resolver
from .inventory_model import (
    CredentialGroup,
    Defaults,
    Device,
    InventoryError,
    find_device,
    load_inventory_file,
)

DEFAULT_DEVICE_NAME = "PE1"

__all__ = [
    "InventoryError",
    "load_inventory",
    "get_device",
    "get_default_device_name",
]


def _resolve_credentials(group: CredentialGroup) -> dict[str, str | None]:
    """Resolve one credential group via the configured pluggable resolver.

    ``NETTOOLS_CREDENTIAL_PROVIDER`` selects *how* (env value directly, or a
    file path per the Docker/Kubernetes secrets convention); see
    ``credential_resolver.py``. Authentication is by shared password by
    default -- if the group's ``ssh_keyfile_env`` resolves to a path,
    key-based auth is used and the password becomes an optional passphrase
    instead of a required password, exactly as before this indirection
    existed.
    """

    return get_resolver().resolve(group)


def _device_record(
    device: Device,
    defaults: Defaults,
    credential_groups: dict[str, CredentialGroup],
    *,
    resolved_groups: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Join one parsed ``Device`` with its group's credentials from the environment.

    ``resolved_groups`` is an optional cache the caller keeps across several
    devices in one call (``load_inventory()``) so a shared credential group's
    environment is read at most once even for many devices; ``get_device()``
    passes nothing, since it only ever resolves one device's group.
    """

    group_name = device.credential_group or defaults.credential_group
    if resolved_groups is None:
        credentials = _resolve_credentials(credential_groups[group_name])
    else:
        if group_name not in resolved_groups:
            resolved_groups[group_name] = _resolve_credentials(credential_groups[group_name])
        credentials = resolved_groups[group_name]

    return {
        "name": device.name,
        "hostname": device.mgmt_ip,
        "platform": device.platform or defaults.platform,
        "username": credentials["username"],
        "password": credentials["password"],
        "key_file": credentials["key_file"],
        "port": defaults.port,
    }


def load_inventory() -> list[dict[str, Any]]:
    """Return every lab device joined with credentials resolved from the environment."""

    inventory = load_inventory_file()

    # Each named credential group's environment is read at most once, even
    # when several devices share it.
    resolved_groups: dict[str, dict[str, str | None]] = {}

    return [
        _device_record(
            device, inventory.defaults, inventory.credential_groups, resolved_groups=resolved_groups
        )
        for device in inventory.devices
    ]


def get_device(device_name: str) -> dict[str, Any]:
    """Return one lab device by name.

    O(1): an indexed lookup (``inventory_model.find_device``) plus resolving
    just *this* device's own credential group -- not the whole inventory's.
    Before Phase 7 this called ``load_inventory()`` and linear-scanned the
    result, which made a whole-fabric check (one ``get_device()`` per device)
    quadratic in the device count. Credentials are still read fresh from the
    environment on every call, exactly as before -- only the device lookup
    itself got cheaper.
    """

    inventory = load_inventory_file()
    device = find_device(device_name)
    if device is None:
        raise InventoryError(f"Device is not in the lab inventory: {device_name}")

    return _device_record(device, inventory.defaults, inventory.credential_groups)


def get_default_device_name() -> str:
    """Return the default device (PE1), or the first device when PE1 is absent."""

    devices = load_inventory()
    for device in devices:
        if device["name"] == DEFAULT_DEVICE_NAME:
            return str(device["name"])
    return str(devices[0]["name"])
