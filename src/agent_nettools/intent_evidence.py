"""Static, versioned operator intent shaped as investigation evidence."""

from __future__ import annotations

from typing import Any

from .inventory_model import load_inventory_file
from .template_parsers import PARSE_FAILED, PARSE_OK

__all__ = ["collect_static_evidence"]


def collect_static_evidence(name: str, device: str) -> dict[str, Any]:
    """Return one declared-intent envelope without opening a device session."""

    if name != "ldp_intent":
        raise ValueError(f"unknown static evidence source {name!r}")

    try:
        record = next(item for item in load_inventory_file().devices if item.name == device)
    except Exception as exc:  # inventory errors are evidence failures, not intent absence
        return {
            "tool": "inventory_intent",
            "device": device,
            "status": "error",
            "data": {"parsed": None, "parse_status": PARSE_FAILED},
            "errors": [f"inventory intent unavailable: {type(exc).__name__}"],
        }

    interfaces = (record.intended.ldp_interfaces if record.intended else None) or []
    return {
        "tool": "inventory_intent",
        "device": device,
        "status": "success",
        "data": {
            "parsed": {
                "meta": {"source": "inventory", "declared": record.intended is not None},
                "records": [{"interface": interface} for interface in interfaces],
            },
            "parse_status": PARSE_OK,
        },
        "errors": [],
    }