"""Declared, versioned procedure contracts for dry-run workflow proposals.

This registry contains no execution implementation. Adding a definition here
does not grant a procedure the ability to collect data or mutate a device.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ProcedureDefinition", "procedure_definition"]


@dataclass(frozen=True)
class ProcedureDefinition:
    name: str
    version: int
    required_parameters: frozenset[str]


_PROCEDURES = {
    "collect_device_evidence": ProcedureDefinition(
        name="collect_device_evidence",
        version=1,
        required_parameters=frozenset({"device"}),
    ),
}


def procedure_definition(name: str) -> ProcedureDefinition | None:
    """Return the exact current contract for a named dry-run procedure."""

    return _PROCEDURES.get(name)