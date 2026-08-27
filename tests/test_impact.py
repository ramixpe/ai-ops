from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_nettools.graph import Graph, GraphEdge, GraphNode
from agent_nettools.graph_snapshot import (
    GraphCoverage,
    GraphSnapshot,
    build_graph_snapshot,
    path_between,
)
from agent_nettools.impact import connectivity_impact


def test_connectivity_impact_reports_only_isis_observed_partition():
    graph = Graph(
        nodes=(GraphNode("PE1"), GraphNode("P1"), GraphNode("PE2")),
        edges=(GraphEdge("isis", "P1", "PE1", None, None, "Up", ("P1", "PE1")),),
    )

    impact = connectivity_impact(graph, "PE1")

    assert impact.connected == ("P1", "PE1")
    assert impact.disconnected == ("PE2",)
    assert any("no service" in limitation for limitation in impact.limitations)


def test_connectivity_impact_refuses_an_unknown_subject():
    impact = connectivity_impact(Graph(nodes=(), edges=()), "PE9")

    assert impact.connected == ()
    assert impact.disconnected == ()
    assert impact.limitations == ("subject is absent from the derived graph",)


def test_path_between_returns_a_deterministic_isis_path_with_evidence_metadata():
    observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    snapshot = GraphSnapshot(
        graph=Graph(
            nodes=(GraphNode("PE1"), GraphNode("P1"), GraphNode("PE2")),
            edges=(
                GraphEdge("isis", "P1", "PE1", None, None, "Up", ("P1", "PE1")),
                GraphEdge("isis", "P1", "PE2", None, None, "Up", ("P1", "PE2")),
            ),
        ),
        observed_at=observed_at,
        valid_until=observed_at + timedelta(minutes=5),
        coverage=GraphCoverage.COMPLETE,
        source_evidence_keys=("P1:isis", "PE1:isis", "PE2:isis"),
    )

    result = path_between(snapshot, "PE1", "PE2")

    assert result.status == "found"
    assert result.path == ("PE1", "P1", "PE2")
    assert result.source_evidence_keys == ("P1:isis", "PE1:isis", "PE2:isis")


def test_path_between_refuses_to_treat_partial_coverage_as_disconnection():
    observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    snapshot = GraphSnapshot(
        graph=Graph(nodes=(GraphNode("PE1"), GraphNode("PE2")), edges=()),
        observed_at=observed_at,
        valid_until=observed_at + timedelta(minutes=5),
        coverage=GraphCoverage.PARTIAL,
        source_evidence_keys=("PE1:isis",),
        limitations=("PE2 IS-IS evidence was not collected",),
    )

    result = path_between(snapshot, "PE1", "PE2")

    assert result.status == "incomplete"
    assert result.path == ()
    assert result.limitations == ("PE2 IS-IS evidence was not collected",)


def test_connectivity_impact_keeps_unobserved_partition_unknown():
    observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    snapshot = GraphSnapshot(
        graph=Graph(
            nodes=(GraphNode("PE1"), GraphNode("P1"), GraphNode("PE2")),
            edges=(GraphEdge("isis", "P1", "PE1", None, None, "Up", ("P1", "PE1")),),
        ),
        observed_at=observed_at,
        valid_until=observed_at + timedelta(minutes=5),
        coverage=GraphCoverage.PARTIAL,
        source_evidence_keys=("P1:isis", "PE1:isis"),
        limitations=("PE2 IS-IS evidence was not collected",),
    )

    impact = connectivity_impact(snapshot, "PE1")

    assert impact.connected == ("P1", "PE1")
    assert impact.disconnected == ()
    assert impact.unknown == ("PE2",)
    assert impact.source_evidence_keys == ("P1:isis", "PE1:isis")
    assert any("coverage is partial" in limitation for limitation in impact.limitations)


def test_build_graph_snapshot_derives_partial_coverage_from_parse_failure():
    observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    evidence = {
        "PE1": {
            "lldp": {"data": {"parse_status": "ok", "parsed": {"records": []}}},
            "isis": {"data": {"parse_status": "ok", "parsed": {"records": []}}},
        },
        "PE2": {
            "lldp": {"data": {"parse_status": "failed", "parsed": {"records": []}}},
            "isis": {"data": {"parse_status": "ok", "parsed": {"records": []}}},
        },
    }

    snapshot = build_graph_snapshot(evidence, observed_at=observed_at, validity_seconds=300)

    assert snapshot.coverage is GraphCoverage.PARTIAL
    assert snapshot.source_evidence_keys == ("PE1:isis", "PE1:lldp", "PE2:isis")
    assert snapshot.limitations == ("PE2 LLDP evidence did not parse cleanly",)