"""Cisco IOS-XR lab device inventory (single user).

Device names, management IPs, and platform now live in the declarative YAML
file (``inventory/lab.yaml`` by default; see ``inventory_model.py``) rather
than a hardcoded dict. This module stays the credential-free read path onto
that data: ``platform_for()`` parses only the YAML, never touches an
environment variable, and that is what lets the command allowlist in
``network_tools._run_approved_commands`` be checked *before* credentials are
loaded (see CLAUDE.md, "The safety boundary"). Credentials are joined in only
by ``inventory.load_inventory()``.
"""

from __future__ import annotations

from .inventory_model import (  # noqa: F401 - re-exported
    find_device,
    load_inventory_file,
    reset_inventory_cache,
)
from .platforms import DEFAULT_PLATFORM

# Per-device platform overrides, consulted *before* the YAML. Every node in
# this lab is IOS-XR, so the map starts empty and every lookup falls through
# to the inventory file. Tests use this to simulate a multi-vendor fabric
# (``monkeypatch.setitem(lab.PLATFORMS, "PE1", "juniper_junos")``) without
# needing a second inventory file; it is checked first specifically so that
# seam keeps working unchanged.
PLATFORMS: dict[str, str] = {}


def all_devices() -> dict[str, str]:
    """Return a fresh ``{device_name: management_ip}`` dict for every lab device."""

    return {device.name: device.mgmt_ip for device in load_inventory_file().devices}


class _LazyDevices(dict):
    """``DEVICES``, loaded on first *use* instead of at import.

    The eager ``DEVICES = all_devices()`` this replaces ran at import time —
    before argparse, before anything. A malformed ``NETTOOLS_INVENTORY``
    therefore crashed **every** invocation with a raw traceback, including
    ``nettools --help``, burying the excellent ``InventoryError`` message the
    validator writes (found by the 2026-08-18 operator walkthrough, stumble 3).

    Laziness moves the failure to first real access, where it surfaces as the
    same ``InventoryError`` as before — which ``cli.main()``'s handler renders
    cleanly. **Deliberately not try/except-at-import**: leaving ``DEVICES``
    silently empty on a bad inventory would be absence-as-health, handing
    every consumer a plausible empty dict instead of the error.
    """

    _loaded = False

    def _ensure(self) -> None:
        if not self._loaded:
            super().update(all_devices())
            self._loaded = True

    def __getitem__(self, key):            # noqa: D105
        self._ensure()
        return super().__getitem__(key)

    def __iter__(self):                    # noqa: D105
        self._ensure()
        return super().__iter__()

    def __len__(self):                     # noqa: D105
        self._ensure()
        return super().__len__()

    def __contains__(self, key):           # noqa: D105
        self._ensure()
        return super().__contains__(key)

    def keys(self):                        # noqa: D102
        self._ensure()
        return super().keys()

    def values(self):                      # noqa: D102
        self._ensure()
        return super().values()

    def items(self):                       # noqa: D102
        self._ensure()
        return super().items()

    def get(self, key, default=None):      # noqa: D102
        self._ensure()
        return super().get(key, default)


#: Lazy: populated on first use. Call ``all_devices()`` for a value that
#: tracks a later ``NETTOOLS_INVENTORY`` change plus ``reset_inventory_cache()``.
DEVICES = _LazyDevices()


def platform_for(device_name: str) -> str:
    """Return a device's platform without touching credentials.

    Checks the ``PLATFORMS`` override first, then the inventory file (an O(1)
    name lookup, not a linear scan -- see ``inventory_model.find_device``),
    then falls back to the default platform for a name the inventory does not
    know at all -- the device itself is rejected later by
    ``inventory.get_device``. This function must never require credentials;
    see the module docstring.
    """

    if device_name in PLATFORMS:
        return PLATFORMS[device_name]

    device = find_device(device_name)
    if device is not None:
        return device.platform or load_inventory_file().defaults.platform
    return DEFAULT_PLATFORM
