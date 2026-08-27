"""Versioned metadata and bounded queries for a derived topology graph.

This module deliberately wraps :class:`graph.Graph` rather than making the
derived graph an independent topology source. A query may report a path that
the supplied evidence proves exists, but it never treats an absent path as a
disconnection when collection coverage is partial or unknown.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from . import parsers
from .graph import Graph, build_graph

__all__ = [
    "GraphCoverage",
    "GraphSnapshot",
    "PathResult",
    "build_graph_snapshot",
    "path_between",
]


class GraphCoverage(StrEnum):
    """Completeness of the evidence used to build a graph snapshot."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GraphSnapshot:
    """A rebuildable graph projection with explicit evidence provenance."""

    graph: Graph
    observed_at: datetime
    valid_until: datetime
    coverage: GraphCoverage
    source_evidence_keys: tuple[str, ...]
    schema_version: int = 1
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("graph snapshot timestamps must be timezone-aware")
        if self.valid_until < self.observed_at:
            raise ValueError("graph snapshot validity cannot end before observation")
        if self.schema_version != 1:
            raise ValueError(f"unsupported graph snapshot schema version: {self.schema_version}")
        if not self.source_evidence_keys:
            raise ValueError("graph snapshots require source evidence keys")


@dataclass(frozen=True)
class PathResult:
    """A bounded IS-IS path query result, including evidence limits."""

    status: str
    source: str
    destination: str
    path: tuple[str, ...]
    source_evidence_keys: tuple[str, ...]
    limitations: tuple[str, ...]


def build_graph_snapshot(
    evidence_by_device: dict[str, dict[str, Any]],
    *,
    observed_at: datetime,
    validity_seconds: int,
) -> GraphSnapshot:
    """Build a graph snapshot whose provenance is derived from collected evidence.

    Both LLDP and IS-IS section parses are required for complete link-layer
    coverage. A clean but empty section remains useful evidence and receives a
    source key; an absent or failed section is a limitation, never an empty
    adjacency claim.
    """

    if validity_seconds < 0:
        raise ValueError("graph snapshot validity must not be negative")

    source_keys: list[str] = []
    limitations: list[str] = []
    for device, evidence in sorted(evidence_by_device.items()):
        for intent in ("isis", "lldp"):
            section = evidence.get(intent, {}) if isinstance(evidence, dict) else {}
            data = section.get("data", {}) if isinstance(section, dict) else {}
            if isinstance(data, dict) and data.get("parse_status") == parsers.PARSE_OK:
                source_keys.append(f"{device}:{intent}")
            else:
                label = "IS-IS" if intent == "isis" else "LLDP"
                limitations.append(f"{device} {label} evidence did not parse cleanly")

    coverage = GraphCoverage.COMPLETE if not limitations else GraphCoverage.PARTIAL
    return GraphSnapshot(
        graph=build_graph(evidence_by_device),
        observed_at=observed_at,
        valid_until=observed_at + timedelta(seconds=validity_seconds),
        coverage=coverage,
        source_evidence_keys=tuple(source_keys),
        limitations=tuple(limitations),
    )


def path_between(snapshot: GraphSnapshot, source: str, destination: str) -> PathResult:
    """Return one deterministic shortest path over observed ``IS-IS Up`` edges.

    A found path remains a positive observation under partial coverage. A
    missing path is only reported as ``not_found`` when coverage is complete;
    otherwise it is ``incomplete`` because missing collection can hide an
    edge. Nodes absent from the graph are ``unknown_subject`` rather than a
    topology finding.
    """

    names = {node.name for node in snapshot.graph.nodes}
    if source not in names or destination not in names:
        return PathResult(
            status="unknown_subject",
            source=source,
            destination=destination,
            path=(),
            source_evidence_keys=snapshot.source_evidence_keys,
            limitations=("one or both path endpoints are absent from the derived graph",),
        )

    adjacency: dict[str, set[str]] = {name: set() for name in names}
    for edge in snapshot.graph.edges:
        if edge.protocol == "isis" and edge.state == "Up":
            adjacency[edge.device_a].add(edge.device_b)
            adjacency[edge.device_b].add(edge.device_a)

    predecessors: dict[str, str | None] = {source: None}
    frontier = deque([source])
    while frontier:
        current = frontier.popleft()
        if current == destination:
            break
        for neighbor in sorted(adjacency[current]):
            if neighbor not in predecessors:
                predecessors[neighbor] = current
                frontier.append(neighbor)

    if destination in predecessors:
        path: list[str] = []
        current: str | None = destination
        while current is not None:
            path.append(current)
            current = predecessors[current]
        return PathResult(
            status="found",
            source=source,
            destination=destination,
            path=tuple(reversed(path)),
            source_evidence_keys=snapshot.source_evidence_keys,
            limitations=snapshot.limitations,
        )

    if snapshot.coverage is not GraphCoverage.COMPLETE:
        return PathResult(
            status="incomplete",
            source=source,
            destination=destination,
            path=(),
            source_evidence_keys=snapshot.source_evidence_keys,
            limitations=snapshot.limitations or ("graph evidence coverage is incomplete",),
        )

    return PathResult(
        status="not_found",
        source=source,
        destination=destination,
        path=(),
        source_evidence_keys=snapshot.source_evidence_keys,
        limitations=snapshot.limitations,
    )