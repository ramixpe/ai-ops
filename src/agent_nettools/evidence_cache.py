"""Freshness-aware cache contract for evidence epochs.

The cache is intentionally an optimization layer, not an authority: callers
must ask for a maximum age and receive an explicit miss when the observation is
missing, malformed, or stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .epoch import EvidenceEpoch

__all__ = ["CachedEvidence", "EvidenceCache", "EvidenceCacheMiss"]


class EvidenceCacheMiss(LookupError):
    """Evidence is absent or cannot satisfy the caller's freshness contract."""


@dataclass(frozen=True)
class CachedEvidence:
    key: str
    device: str
    envelope: dict[str, Any]
    collected_at: str


class EvidenceCache:
    """Process-local observation cache with explicit freshness refusal."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], CachedEvidence] = {}

    def put_epoch(self, epoch: EvidenceEpoch) -> None:
        for observation in epoch.observations:
            if isinstance(observation.envelope, dict):
                self._entries[(observation.device, observation.key)] = CachedEvidence(
                    observation.key,
                    observation.device,
                    dict(observation.envelope),
                    observation.collected_at,
                )

    def require_fresh(
        self,
        *,
        device: str,
        key: str,
        maximum_age_seconds: float,
        now: datetime | None = None,
    ) -> CachedEvidence:
        if maximum_age_seconds < 0:
            raise EvidenceCacheMiss("freshness maximum must be non-negative")
        entry = self._entries.get((device, key))
        if entry is None:
            raise EvidenceCacheMiss("evidence is not cached")
        try:
            collected = datetime.fromisoformat(entry.collected_at)
        except ValueError as exc:
            raise EvidenceCacheMiss("cached evidence has invalid collection time") from exc
        if collected.tzinfo is None:
            raise EvidenceCacheMiss("cached evidence collection time lacks timezone")
        age = (now or datetime.now(timezone.utc)) - collected
        if age.total_seconds() > maximum_age_seconds:
            raise EvidenceCacheMiss("cached evidence is stale")
        return entry

    def require_device_fresh(
        self,
        *,
        device: str,
        maximum_age_seconds: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a checks-compatible fresh evidence bundle for one device."""

        entries = [entry for (entry_device, _key), entry in self._entries.items() if entry_device == device]
        if not entries:
            raise EvidenceCacheMiss("device evidence is not cached")
        result: dict[str, Any] = {}
        for entry in entries:
            fresh = self.require_fresh(
                device=device,
                key=entry.key,
                maximum_age_seconds=maximum_age_seconds,
                now=now,
            )
            result[fresh.key] = fresh.envelope
        return result