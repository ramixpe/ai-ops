"""Render ``docs/devices.md`` from the inventory, so the table cannot drift.

The device table used to be a hand-maintained copy of ``lab.DEVICES`` plus
roles typed in by hand; nothing enforced that it stayed in sync once the
inventory became data-driven. ``render_devices_doc()`` renders the whole file
from the same ``InventoryFile`` that ``lab.py`` and ``inventory.py`` read, and
``tests/test_docs.py`` asserts the committed file is exactly its output --
so an inventory change that is not reflected in the doc fails CI instead of
quietly drifting.
"""

from __future__ import annotations

from .inventory_model import InventoryFile, load_inventory_file

# Human-readable labels for the machine-readable `role` values. Anything not
# in this table falls back to the raw role string, so a new role added to the
# schema still renders instead of raising.
_ROLE_LABELS: dict[str, str] = {
    "core": "Core (P)",
    "edge": "Provider Edge",
    "route-reflector": "Route Reflector",
}


def _table_lines(inventory: InventoryFile) -> list[str]:
    """Render the device table, column widths sized to the actual data."""

    columns = ["Name", "Role", "Management IP", "Platform"]
    rows = [
        [
            device.name,
            _ROLE_LABELS.get(device.role, device.role),
            device.mgmt_ip,
            device.platform or inventory.defaults.platform,
        ]
        for device in inventory.devices
    ]

    widths = [
        max(len(columns[i]), *(len(row[i]) for row in rows)) for i in range(len(columns))
    ]

    def _render_row(cells: list[str]) -> str:
        return (
            "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)) + " |"
        )

    lines = [_render_row(columns), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(_render_row(row) for row in rows)
    return lines


def render_devices_doc(inventory: InventoryFile | None = None) -> str:
    """Render the full contents of ``docs/devices.md``."""

    inventory = inventory or load_inventory_file()

    lines = [
        "# Lab Devices",
        "",
        "Single-user Cisco IOS-XR lab. Management network `172.20.250.0/24`.",
        "Credentials are provided through `DEVICE_USERNAME` and `DEVICE_PASSWORD` and are",
        "never stored here.",
        "",
        *_table_lines(inventory),
        "",
        "`PE1` is the default device used when a command is run without an explicit",
        "device name.",
        "",
    ]
    return "\n".join(lines)
