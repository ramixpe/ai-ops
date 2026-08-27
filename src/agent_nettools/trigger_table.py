"""Reviewed event-trigger policy, shared without event-source dependencies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .knowledge import load_mnemonic_table

__all__ = [
    "REVIEWED_FIRING_MNEMONICS",
    "TriggerTableInconsistency",
    "build_trigger_index",
    "validate_trigger_table",
]

REVIEWED_FIRING_MNEMONICS = frozenset({"ROUTING-BGP-5-ADJCHANGE"})


class TriggerTableInconsistency(RuntimeError):
    """A reviewed trigger marked runnable without a declared route."""


def build_trigger_index(
    table: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ``mnemonic -> trigger`` from the reviewed knowledge table."""

    entries = table if table is not None else load_mnemonic_table()
    return {
        entry["mnemonic"]: entry["trigger"]
        for entry in entries
        if isinstance(entry, Mapping) and "trigger" in entry
    }


def validate_trigger_table(
    table: Iterable[Mapping[str, Any]] | None = None,
    *,
    routable_mnemonics: Iterable[str],
) -> None:
    """Reject a reviewed ``fires: true`` trigger that cannot be routed."""

    entries = tuple(table) if table is not None else tuple(load_mnemonic_table())
    routable = frozenset(routable_mnemonics)
    firing_mnemonics = frozenset(
        entry.get("mnemonic")
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(entry.get("trigger"), Mapping)
        and entry["trigger"].get("fires")
    )
    if table is None and firing_mnemonics != REVIEWED_FIRING_MNEMONICS:
        raise TriggerTableInconsistency(
            "the live trigger set must equal "
            f"{sorted(REVIEWED_FIRING_MNEMONICS)!r}, got {sorted(firing_mnemonics)!r}"
        )
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        trigger = entry.get("trigger")
        if not isinstance(trigger, Mapping) or not trigger.get("fires"):
            continue
        mnemonic = entry.get("mnemonic")
        if not entry.get("investigate_with"):
            raise TriggerTableInconsistency(
                f"{mnemonic!r}: trigger.fires is true but investigate_with is "
                "null -- there is no flow to route to"
            )
        if mnemonic not in routable:
            raise TriggerTableInconsistency(
                f"{mnemonic!r}: trigger.fires is true but it has no "
                "event_routing.MNEMONIC_FLOW_TABLE entry -- there is no "
                "subject extractor, so this mnemonic cannot actually be routed"
            )