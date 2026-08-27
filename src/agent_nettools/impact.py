"""Conservative service-impact projection over the derived topology graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .graph import Graph
from .graph_snapshot import GraphCoverage, GraphSnapshot

__all__ = ["ImpactResult", "connectivity_impact"]


@dataclass(frozen=True)
class ImpactResult:
    """Observed reachability impact, never a claim about unmodeled services."""

    subject: str
    connected: tuple[str, ...]
    disconnected: tuple[str, ...]
    limitations: tuple[str, ...]
    unknown: tuple[str, ...] = ()
    source_evidence_keys: tuple[str, ...] = ()
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    coverage: GraphCoverage | None = None


def connectivity_impact(graph: Graph | GraphSnapshot, subject: str) -> ImpactResult:
    """Project IS-IS connectivity from evidence with explicit scope limits."""

    snapshot = graph if isinstance(graph, GraphSnapshot) else None
    derived_graph = snapshot.graph if snapshot is not None else graph
    names = {node.name for node in derived_graph.nodes}
    if subject not in names:
        return ImpactResult(
            subject=subject,
            connected=(),
            disconnected=(),
            limitations=("subject is absent from the derived graph",),
            source_evidence_keys=snapshot.source_evidence_keys if snapshot else (),
            observed_at=snapshot.observed_at if snapshot else None,
            valid_until=snapshot.valid_until if snapshot else None,
            coverage=snapshot.coverage if snapshot else None,
        )

    adjacency: dict[str, set[str]] = {name: set() for name in names}
    for edge in derived_graph.edges:
        if edge.protocol == "isis" and edge.state == "Up":
            adjacency[edge.device_a].add(edge.device_b)
            adjacency[edge.device_b].add(edge.device_a)

    visited = {subject}
    frontier = [subject]
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency[current] - visited:
            visited.add(neighbor)
            frontier.append(neighbor)

    limitations = [
        "impact covers only derived IS-IS adjacency connectivity",
        "no service, VRF, forwarding-plane, or traffic-demand impact is modeled",
    ]
    if not any(edge.protocol == "isis" for edge in derived_graph.edges):
        limitations.append("no parsed IS-IS edges were available")
    disconnected = names - visited
    unknown: set[str] = set()
    if snapshot is not None:
        limitations.extend(snapshot.limitations)
        if snapshot.coverage is not GraphCoverage.COMPLETE:
            limitations.append(f"graph evidence coverage is {snapshot.coverage.value}")
            unknown = disconnected
            disconnected = set()
    return ImpactResult(
        subject=subject,
        connected=tuple(sorted(visited)),
        disconnected=tuple(sorted(disconnected)),
        limitations=tuple(limitations),
        unknown=tuple(sorted(unknown)),
        source_evidence_keys=snapshot.source_evidence_keys if snapshot else (),
        observed_at=snapshot.observed_at if snapshot else None,
        valid_until=snapshot.valid_until if snapshot else None,
        coverage=snapshot.coverage if snapshot else None,
    )