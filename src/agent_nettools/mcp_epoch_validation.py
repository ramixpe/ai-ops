"""Asserted-object validation against a supplied fresh evidence epoch."""

from __future__ import annotations

from datetime import datetime

from .checks import HEALTHY, bgp_peer_exists, interface_exists
from .evidence_cache import EvidenceCache, EvidenceCacheMiss

__all__ = ["validate_asserted_object"]


def validate_asserted_object(
    cache: EvidenceCache,
    *,
    device: str,
    kind: str,
    identifier: str,
    maximum_age_seconds: float,
    now: datetime | None = None,
) -> str | None:
    """Return an explicit refusal reason, or ``None`` for a fresh present object."""

    if kind not in {"bgp_peer", "interface"} or not identifier:
        return "identifier is unusable"
    try:
        evidence = cache.require_device_fresh(
            device=device,
            maximum_age_seconds=maximum_age_seconds,
            now=now,
        )
    except EvidenceCacheMiss as exc:
        return f"fresh evidence is unavailable: {exc}"
    result = bgp_peer_exists(evidence, identifier) if kind == "bgp_peer" else interface_exists(evidence, identifier)
    if result.status == HEALTHY:
        return None
    return "validation could not evaluate fresh evidence" if not result.is_conclusive else "object absent"