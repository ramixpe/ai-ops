"""The neo4j topology collector: the pure projection, and the thin writer.

Structured the same way the module is: fixture-backed tests against the real
``healthy``/``t0``/``isis-broken`` captures first (nothing here is invented --
every expected count and edge below is independently readable from the
fixture files by eye, exactly like ``tests/test_topology.py``), then synthetic
guard tests that isolate one behaviour each, then the writer tests, which use
a fake driver (``driver_factory=``) so they run with **no neo4j package
installed and no database reachable** -- the same seam convention this repo
already uses for netmiko (``install_fake_netmiko``) and the transport
(``sender=``).
"""

from __future__ import annotations

import subprocess
import sys

from helpers import set_device_environment

from agent_nettools import parsers
from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.graph import (
    NODE_LABEL,
    RELATIONSHIP_TYPE,
    Graph,
    GraphEdge,
    GraphNode,
    build_graph,
    write_graph,
)
from agent_nettools.lab import all_devices

# --------------------------------------------------------------------------- #
# Fixture-backed tests.
# --------------------------------------------------------------------------- #


def _evidence_by_device(monkeypatch, label: str = "healthy") -> dict[str, dict]:
    set_device_environment(monkeypatch)
    return {name: load_fixture_evidence(name, label=label) for name in all_devices()}


def test_every_device_becomes_a_node(monkeypatch):
    evidence = _evidence_by_device(monkeypatch, label="healthy")

    graph = build_graph(evidence)

    assert {node.name for node in graph.nodes} == set(all_devices())
    # Sorted -- a caller (or a diff between two runs) can rely on the order.
    assert [node.name for node in graph.nodes] == sorted(all_devices())


def test_node_carries_platform_and_configured_hostname(monkeypatch):
    evidence = _evidence_by_device(monkeypatch, label="healthy")

    graph = build_graph(evidence)

    by_name = {node.name: node for node in graph.nodes}
    assert by_name["PE1"].platform == "cisco_xr"
    assert by_name["PE1"].configured_hostname == "PE1"


def test_p1_pe2_is_both_an_lldp_and_an_isis_edge(monkeypatch):
    """Cross-checked against the raw fixture text (P1/healthy, PE2/healthy):

    P1's LLDP table: ``PE2  GigabitEthernet0/0/0/3 ... GigabitEthernet0/0/0/0``
    PE2's LLDP table: ``P1   GigabitEthernet0/0/0/0 ... GigabitEthernet0/0/0/3``
    Both ends agree, so the edge carries both interfaces. Same shape in IS-IS,
    with the short interface spelling that protocol uses.
    """

    evidence = _evidence_by_device(monkeypatch, label="healthy")

    graph = build_graph(evidence)

    by_key = {(e.protocol, e.device_a, e.device_b): e for e in graph.edges}

    lldp = by_key[("lldp", "P1", "PE2")]
    assert lldp.interface_a == "GigabitEthernet0/0/0/3"  # P1's own local interface
    assert lldp.interface_b == "GigabitEthernet0/0/0/0"  # PE2's own local interface
    assert lldp.observed_by == ("P1", "PE2")
    assert lldp.state is None  # LLDP carries no adjacency state

    isis = by_key[("isis", "P1", "PE2")]
    assert isis.interface_a == "Gi0/0/0/3"
    assert isis.interface_b == "Gi0/0/0/0"
    assert isis.observed_by == ("P1", "PE2")
    assert isis.state == "Up"


def test_pe2_is_no_longer_isolated_at_t0_degree_four_not_zero(monkeypatch):
    """B-591 (2026-08-19 refresh): PE2 was this fabric's isolation case for
    the life of the project -- topology.py's docstring said 0/0 at t0/t1,
    isolated at the link layer but not absent from the fabric. That is no
    longer true: PE2 now carries live IS-IS adjacencies to P1 and P3, and LLDP
    confirms both links from both ends, so the graph carries one `isis` and
    one `lldp` edge per link -- four edges touching PE2, not zero. The node
    itself was never missing either way; what changed is its degree."""

    evidence = _evidence_by_device(monkeypatch, label="t0")

    graph = build_graph(evidence)

    assert "PE2" in {node.name for node in graph.nodes}
    touching_pe2 = {
        (e.protocol, e.device_a, e.device_b) for e in graph.edges if "PE2" in (e.device_a, e.device_b)
    }
    assert touching_pe2 == {
        ("isis", "P1", "PE2"),
        ("isis", "P3", "PE2"),
        ("lldp", "P1", "PE2"),
        ("lldp", "P3", "PE2"),
    }


def test_isis_broken_pe3_p2_is_lldp_only(monkeypatch):
    """THE acceptance test (B-496's fixture). PE3 and P2 are cabled and see each
    other in LLDP on both ends; IS-IS has no adjacency between them on either
    end (P2's neighbours are P1/P4/P3/PE1; PE3's is P4 alone). A graph that
    shows this link as simply "up" has lost the one fact worth building this
    collector for."""

    set_device_environment(monkeypatch)
    evidence = {
        name: load_fixture_evidence(name, label="isis-broken") for name in ("P2", "PE3")
    }

    graph = build_graph(evidence)

    lldp_pairs = {(e.device_a, e.device_b) for e in graph.edges if e.protocol == "lldp"}
    isis_pairs = {(e.device_a, e.device_b) for e in graph.edges if e.protocol == "isis"}

    assert ("P2", "PE3") in lldp_pairs
    assert ("P2", "PE3") not in isis_pairs

    lldp_edge = next(e for e in graph.edges if e.protocol == "lldp" and (e.device_a, e.device_b) == ("P2", "PE3"))
    assert lldp_edge.observed_by == ("P2", "PE3")  # both ends corroborate the cabling

    # The companion the vacuity rule (BUILD-PLAN.md §0.12) demands: this is only
    # a real assertion if the two protocols are capable of disagreeing in the
    # first place. They do, right here -- so a stub that always returned "no
    # edges" or always merged the two protocols into one would fail this test.
    assert lldp_pairs != isis_pairs


# --------------------------------------------------------------------------- #
# Guards, isolated with hand-built evidence (same convention as
# tests/test_topology.py's `_fabricated`).
# --------------------------------------------------------------------------- #


def _fabricated(lldp_by_device, isis_by_device=None, hostnames=None):
    isis_by_device = isis_by_device or {}
    hostnames = hostnames or {}
    out = {}
    names = set(lldp_by_device) | set(isis_by_device)
    for name in names:
        out[name] = {
            "platform": "cisco_xr",
            "facts": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {"hostname": hostnames.get(name, name)}, "records": []},
                }
            },
            "lldp": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {}, "records": lldp_by_device.get(name, [])},
                }
            },
            "isis": {
                "data": {
                    "parse_status": parsers.PARSE_OK,
                    "parsed": {"meta": {}, "records": isis_by_device.get(name, [])},
                }
            },
        }
    return out


def test_g1a_an_unresolvable_neighbor_is_not_a_phantom_node_or_edge():
    """A LLDP-reported neighbour this collection has no evidence for (a
    "foreign" device, or simply a device this run did not collect) must not
    become a node -- that would let the graph invent topology it never
    observed. topology.find_neighbors_not_in_inventory's job, not this one's."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "some-switch",
               "neighbor_interface": "Gi1/0/1"}],
    })

    graph = build_graph(evidence)

    assert {node.name for node in graph.nodes} == {"A"}
    assert graph.edges == ()


def test_g1b_a_device_naming_itself_is_not_a_self_loop_edge():
    """Defensive: a malformed or self-referential record must never produce a
    self-loop edge, which would make an isolated single-device collection look
    like it has an adjacency."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })

    graph = build_graph(evidence)

    assert {node.name for node in graph.nodes} == {"A"}
    assert graph.edges == ()


def test_g2_an_unparsed_section_contributes_no_edge():
    """A section that failed to parse must contribute nothing -- never a
    guessed edge built from a record that was never really read."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/0"}],
        "B": [{"local_interface": "Gi0/0/0/0", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })
    evidence["B"]["lldp"]["data"]["parse_status"] = parsers.PARSE_FAILED

    graph = build_graph(evidence)

    lldp_edges = [e for e in graph.edges if e.protocol == "lldp"]
    # A's own report is still honoured -- this is G1's asymmetric-observation
    # case, not a reason to drop the edge entirely.
    assert len(lldp_edges) == 1
    edge = lldp_edges[0]
    assert edge.observed_by == ("A",)
    assert edge.interface_b is None  # B's own claim was never read


def test_a_link_only_one_side_reports_is_still_an_edge():
    """An asymmetric observation (only one end's evidence names the link) is a
    real, honest fact -- not something to hide until both ends agree."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [],
    })

    graph = build_graph(evidence)

    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert (edge.device_a, edge.device_b) == ("A", "B")
    assert edge.interface_a == "Gi0/0/0/0"
    assert edge.interface_b is None
    assert edge.observed_by == ("A",)


def test_build_graph_is_order_independent():
    """Re-keying the input dict in the opposite order must not change the
    result -- the canonical (sorted) pair key is what makes this true, not
    "whichever device happened to be read first"."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })
    reversed_evidence = {name: evidence[name] for name in reversed(list(evidence))}

    assert build_graph(evidence) == build_graph(reversed_evidence)


def test_build_graph_is_idempotent():
    """Calling it twice on unchanged evidence produces an equal Graph -- no
    accumulation, no hidden state, no duplicate edges from a second call."""

    evidence = _fabricated({
        "A": [{"local_interface": "Gi0/0/0/0", "neighbor": "B", "neighbor_interface": "Gi0/0/0/1"}],
        "B": [{"local_interface": "Gi0/0/0/1", "neighbor": "A", "neighbor_interface": "Gi0/0/0/0"}],
    })

    first = build_graph(evidence)
    second = build_graph(evidence)

    assert first == second
    assert len(first.edges) == 1  # not two


def test_g3_the_module_imports_with_no_neo4j_package_installed():
    """Real proof, not a simulation: this venv genuinely has no `neo4j`
    package installed (it is an optional extra), so importing the module in a
    fresh subprocess either proves the lazy-import discipline holds or fails
    with exactly the ImportError a module-level `import neo4j` would produce."""

    result = subprocess.run(
        [sys.executable, "-c", "import agent_nettools.graph"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result_modules = subprocess.run(
        [sys.executable, "-c", "import agent_nettools.graph, sys; assert 'neo4j' not in sys.modules"],
        capture_output=True,
        text=True,
    )
    assert result_modules.returncode == 0, result_modules.stderr


def test_write_graph_without_neo4j_installed_fails_at_the_call_not_the_import():
    """No `driver_factory` given, and no `neo4j` package installed: the
    ImportError must happen inside `write_graph`, never at module import."""

    graph = Graph(nodes=(GraphNode(name="A"),), edges=())
    try:
        write_graph(graph, uri="bolt://example.invalid:7687", user="neo4j", password="x")
    except ModuleNotFoundError as exc:
        assert "neo4j" in str(exc)
    else:
        raise AssertionError("expected ModuleNotFoundError: neo4j is not installed in this venv")


# --------------------------------------------------------------------------- #
# The writer, exercised against a fake driver -- no neo4j package, no database.
# --------------------------------------------------------------------------- #


class _FakeTx:
    def __init__(self):
        self.queries: list[tuple[str, dict]] = []

    def run(self, query, **params):
        self.queries.append((query, params))


class _FakeSession:
    def __init__(self, tx, kwargs_sink):
        self._tx = tx
        self._kwargs_sink = kwargs_sink

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute_write(self, fn, *args):
        return fn(self._tx, *args)


class _FakeDriver:
    def __init__(self):
        self.tx = _FakeTx()
        self.closed = False
        self.session_kwargs: dict | None = None

    def session(self, **kwargs):
        self.session_kwargs = kwargs
        return _FakeSession(self.tx, kwargs)

    def close(self):
        self.closed = True


def _sample_graph() -> Graph:
    return Graph(
        nodes=(GraphNode(name="A", platform="cisco_xr"), GraphNode(name="B", platform="cisco_xr")),
        edges=(
            GraphEdge(
                protocol="lldp", device_a="A", device_b="B",
                interface_a="Gi0/0/0/0", interface_b="Gi0/0/0/1",
                state=None, observed_by=("A", "B"),
            ),
        ),
    )


def test_g4_missing_credentials_refuse_rather_than_default(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    graph = _sample_graph()
    try:
        write_graph(graph, driver_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")))
    except RuntimeError as exc:
        assert "NEO4J_URI" in str(exc)
        assert "NEO4J_PASSWORD" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing credentials")


def test_credentials_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://labnet-test:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-only-password")

    captured = {}

    def factory(uri, auth):
        captured["uri"] = uri
        captured["auth"] = auth
        return _FakeDriver()

    write_graph(_sample_graph(), driver_factory=factory)

    assert captured["uri"] == "bolt://labnet-test:7687"
    assert captured["auth"] == ("neo4j", "test-only-password")


def test_g5_every_call_deletes_before_recreating(monkeypatch):
    """The whole idempotency mechanism: DETACH DELETE first, in the same
    transaction as the CREATEs that follow -- never MERGE, never append."""

    driver = _FakeDriver()
    counts = write_graph(
        _sample_graph(),
        uri="bolt://x:7687", user="neo4j", password="x",
        driver_factory=lambda *a, **k: driver,
    )

    queries = driver.tx.queries
    assert queries, "no queries were run"
    first_query = queries[0][0]
    assert "DETACH DELETE" in first_query
    assert NODE_LABEL in first_query

    node_creates = [q for q, _ in queries if q.startswith(f"CREATE (:{NODE_LABEL}")]
    edge_creates = [q for q, _ in queries if RELATIONSHIP_TYPE in q]
    assert len(node_creates) == 2  # A, B
    assert len(edge_creates) == 1  # the one lldp edge

    # Order matters: delete, then nodes, then edges (an edge CREATE MATCHes on
    # nodes that must already exist).
    kinds = []
    for query, _ in queries:
        if "DETACH DELETE" in query:
            kinds.append("delete")
        elif RELATIONSHIP_TYPE in query:
            kinds.append("edge")
        elif query.startswith(f"CREATE (:{NODE_LABEL}"):
            kinds.append("node")
    assert kinds == ["delete", "node", "node", "edge"]

    assert counts == {"nodes": 2, "edges": 1}
    assert driver.closed is True


def test_write_graph_twice_produces_the_same_delete_then_recreate_sequence(monkeypatch):
    """Running the collector twice must not duplicate anything -- each run
    independently deletes-then-recreates, so the *net* result of running twice
    is identical to running once."""

    driver = _FakeDriver()
    factory = lambda *a, **k: driver  # noqa: E731 -- same fake driver both times

    write_graph(_sample_graph(), uri="bolt://x:7687", user="neo4j", password="x", driver_factory=factory)
    first_run_queries = list(driver.tx.queries)

    driver.tx = _FakeTx()  # fresh recorder; a real DB's *state* would persist, its query log would not
    write_graph(_sample_graph(), uri="bolt://x:7687", user="neo4j", password="x", driver_factory=factory)
    second_run_queries = list(driver.tx.queries)

    assert first_run_queries == second_run_queries
