"""Project parsed device evidence into a topology graph, and (optionally) push it into neo4j.

**neo4j is DERIVED, never authored** (docs/design/stage-2-architecture.md
§2.3). It is a projection of this fabric's own parsed LLDP/IS-IS evidence into
a graph -- not a second place topology gets typed in. Two authored sources of
topology that can disagree is OBS-103's shape exactly (the LLDP-hostname
drift, corrected in B-435): P1 ran the configured hostname
``LEAF05_DHCP_SERVER`` for the life of the project, and comparing LLDP's
device-reported name against the inventory label without resolving it first
read as a fabric-wide inconsistency that never existed. If this graph and the
evidence it was built from ever disagree, the evidence wins and the graph is
rebuilt from it -- there is no path in this module that edits a node or an
edge by hand.

Two layers, deliberately kept apart:

- :func:`build_graph` -- **pure**. Evidence in, a :class:`Graph` out. No
  network access, no import of the ``neo4j`` package anywhere in its call
  path. Testable against committed fixtures with zero lab access, and this is
  the half worth testing hard -- it is where the topology logic actually
  lives.
- :func:`write_graph` -- **thin**. Takes a :class:`Graph` already built and
  pushes it into neo4j. Imports the ``neo4j`` driver lazily, inside the
  function body, so a caller that only ever calls :func:`build_graph` -- every
  test in ``tests/test_graph.py`` but a handful -- never needs the optional
  dependency installed, and importing this module at all never requires it
  either.

Node identity is the inventory device name -- the same stable key
:mod:`topology` resolves every LLDP/IS-IS-reported name back to via
:func:`topology.hostname_map`/:func:`topology.resolve_device` (B-435), reused
here rather than re-derived: the LLDP neighbour resolution problem is already
solved there. A neighbour that does not resolve to a device this collection
has evidence for is left out of the graph entirely -- that is exactly
:func:`topology.find_neighbors_not_in_inventory`'s "foreign neighbour" class,
and reporting it is that module's job, not this one's to guess a node for.

LLDP and IS-IS are walked **independently** and never merged into one edge.
That independence is the entire reason this module exists: an interface can be
a clean LLDP edge and simultaneously have no IS-IS edge at all, and collapsing
the two into a single "link is up" edge would erase exactly the fact worth
having a graph for. See the ``isis-broken`` fixture and
``test_isis_broken_pe3_p2_is_lldp_only`` in ``tests/test_graph.py`` (B-496):
PE3 and P2 see each other in LLDP on both ends and do not see each other in
IS-IS on either end.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from . import parsers, topology

# --------------------------------------------------------------------------- #
# The graph model -- plain, frozen, comparable data. No network, no neo4j.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GraphNode:
    """One device, as it will appear as a neo4j node.

    ``platform`` and ``configured_hostname`` are carried for readability in
    the graph (a Cypher query can show them without a join back to the
    inventory); neither is part of the node's identity. ``name`` -- the
    inventory device name -- is: it is what every edge's ``device_a``/
    ``device_b`` refers to, and it is stable across a device's own hostname
    drifting (B-435's whole point).
    """

    name: str
    platform: str | None = None
    configured_hostname: str | None = None


@dataclass(frozen=True)
class GraphEdge:
    """One adjacency between two devices, as observed by one protocol.

    ``device_a``/``device_b`` are always ordered lexicographically
    (``device_a < device_b``), regardless of which end's evidence produced the
    edge or which order the caller's ``evidence_by_device`` happens to
    iterate in. That canonical ordering -- not "whichever device was read
    first" -- is what makes the same physical link collapse to exactly one
    edge on every rebuild; see the module's idempotency note on
    :func:`build_graph`.

    ``interface_a``/``interface_b`` are each that device's *own* report of its
    local interface facing the other end -- never one side's claim about the
    other's interface name, which two disagreeing devices could get wrong.
    Either may be ``None`` if only one side's evidence named this link (a
    genuinely asymmetric observation, not a bug to paper over).

    ``observed_by`` names which device(s) actually reported this link --
    ``(device_a,)``, ``(device_b,)``, or both -- so "this edge exists" and
    "both ends corroborate it" stay distinguishable facts.
    """

    protocol: str
    device_a: str
    device_b: str
    interface_a: str | None
    interface_b: str | None
    state: str | None
    observed_by: tuple[str, ...]


@dataclass(frozen=True)
class Graph:
    """A whole topology projection: every device given evidence, every adjacency it named."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


# --------------------------------------------------------------------------- #
# build_graph -- the pure half.
# --------------------------------------------------------------------------- #


def _clean_records(evidence: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    """Records for one intent, or ``[]`` if the section did not parse cleanly.

    The same "clean parse or nothing" contract :mod:`topology`'s own
    ``_parsed_records`` enforces (G2, mutation-tested below): a section that
    errored, was unsupported on this platform, or failed to parse contributes
    no edges -- never a guessed one built from partial or absent data.
    """

    section = evidence.get(intent, {}) if isinstance(evidence, dict) else {}
    data = section.get("data", {}) if isinstance(section, dict) else {}
    if not isinstance(data, dict) or data.get("parse_status") != parsers.PARSE_OK:
        return []
    parsed = data.get("parsed") or {}
    return list(parsed.get("records") or []) if isinstance(parsed, dict) else []


def _own_interface_and_state(
    evidence_by_device: dict[str, dict[str, Any]],
    mapping: dict[str, str],
    device: str,
    neighbor: str,
    intent: str,
    *,
    device_field: str,
    neighbor_field: str,
) -> tuple[str | None, str | None]:
    """``(device``'s own local interface, reported state``)`` for its link to ``neighbor``.

    ``(None, None)`` if ``device`` never reported an ``intent`` record naming
    ``neighbor`` -- absence, not a guess. ``state`` is simply whatever the
    record carries under that key (IS-IS records have one; LLDP records do
    not, so this is always ``None`` for LLDP -- there is no protocol notion of
    "state" to lose).
    """

    for record in _clean_records(evidence_by_device.get(device, {}), intent):
        if topology.resolve_device(record.get(neighbor_field), mapping) == neighbor:
            return record.get(device_field), record.get("state")
    return None, None


def _protocol_edges(
    evidence_by_device: dict[str, dict[str, Any]],
    mapping: dict[str, str],
    *,
    protocol: str,
    intent: str,
    device_field: str,
    neighbor_field: str,
) -> dict[tuple[str, str], GraphEdge]:
    """Every ``protocol`` adjacency this evidence set can name, keyed by the canonical pair.

    Both ends' reports of one physical link collapse to a single dict entry
    because the key is the *sorted* device pair, not "whoever was iterated
    first" -- this is what makes :func:`build_graph` order-independent (see
    ``test_build_graph_is_order_independent``).
    """

    pairs: set[tuple[str, str]] = set()
    for device, evidence in evidence_by_device.items():
        for record in _clean_records(evidence, intent):
            neighbor = topology.resolve_device(record.get(neighbor_field), mapping)
            # G1: a neighbour that does not resolve at all (foreign --
            # topology.find_neighbors_not_in_inventory's class to report, not
            # this module's to guess a node for) or that resolves to the
            # reporting device itself is not turned into an edge. Note what is
            # *not* checked here: whether `neighbor` is a key of
            # `evidence_by_device`. It always is, once it is not `None` --
            # `mapping` is built by `topology.hostname_map(evidence_by_device)`,
            # whose values are, by construction, exactly that dict's keys -- so
            # a third `neighbor not in evidence_by_device` clause would be
            # unreachable dead code, not a second guard.
            if neighbor is None or neighbor == device:
                continue
            pairs.add(tuple(sorted((device, neighbor))))

    edges: dict[tuple[str, str], GraphEdge] = {}
    for device_a, device_b in pairs:
        interface_a, state_a = _own_interface_and_state(
            evidence_by_device, mapping, device_a, device_b, intent,
            device_field=device_field, neighbor_field=neighbor_field,
        )
        interface_b, state_b = _own_interface_and_state(
            evidence_by_device, mapping, device_b, device_a, intent,
            device_field=device_field, neighbor_field=neighbor_field,
        )
        observed_by = tuple(
            d for d, iface in ((device_a, interface_a), (device_b, interface_b)) if iface is not None
        )
        edges[(device_a, device_b)] = GraphEdge(
            protocol=protocol,
            device_a=device_a,
            device_b=device_b,
            interface_a=interface_a,
            interface_b=interface_b,
            state=state_a or state_b,
            observed_by=observed_by,
        )
    return edges


def build_graph(evidence_by_device: dict[str, dict[str, Any]]) -> Graph:
    """Project one fabric-wide evidence collection into a :class:`Graph`.

    Pure: no network access, and no import of ``neo4j`` anywhere in this
    function's call path. Every device in ``evidence_by_device`` becomes a
    node -- even one reporting zero adjacencies on every protocol, which is a
    real and interesting shape (PE2 in the ``t0``/``t1`` fixtures: isolated at
    the link layer, still carrying a BGP router-id). LLDP and IS-IS are walked
    independently into separate edges; see the module docstring for why they
    are never merged.

    **Idempotent and rebuildable.** Nodes are sorted by name and edges by
    ``(protocol, device_a, device_b)``, and every edge's pair key is the
    lexicographically sorted ``(device_a, device_b)`` -- so calling this twice
    on the same evidence, or on the same evidence with its dict keys in a
    different order, returns an equal :class:`Graph` (frozen dataclasses over
    tuples, so ``==`` is structural). Nothing here accumulates state between
    calls; there is no cache, no counter, no file written. Re-running the
    collector is exactly re-computing this function and, if a writer is used,
    replacing neo4j's copy wholesale with the result -- never appending to it.
    """

    mapping = topology.hostname_map(evidence_by_device)

    nodes = tuple(
        GraphNode(
            name=name,
            platform=evidence.get("platform") if isinstance(evidence, dict) else None,
            configured_hostname=topology.configured_hostname(evidence),
        )
        for name, evidence in sorted(evidence_by_device.items())
    )

    lldp_edges = _protocol_edges(
        evidence_by_device, mapping,
        protocol="lldp", intent="lldp",
        device_field="local_interface", neighbor_field="neighbor",
    )
    isis_edges = _protocol_edges(
        evidence_by_device, mapping,
        protocol="isis", intent="isis",
        device_field="interface", neighbor_field="system_id",
    )

    all_edges = list(lldp_edges.values()) + list(isis_edges.values())
    edges = tuple(sorted(all_edges, key=lambda e: (e.protocol, e.device_a, e.device_b)))

    return Graph(nodes=nodes, edges=edges)


# --------------------------------------------------------------------------- #
# write_graph -- the thin half. Imports neo4j lazily; nothing above this line
# in the module does, or ever should.
# --------------------------------------------------------------------------- #

# The label/relationship-type this collector owns exclusively. Deliberately
# not the generic `Device`/`ADJACENT` a future NetBox- or n8n-fed backend
# sharing this same neo4j instance (stage-2-architecture.md §2.1's backend
# table) might reasonably also want to use -- scoping the DETACH DELETE below
# to a name only this collector writes is what makes "replace everything" safe
# rather than a landmine for whoever adds the next writer.
NODE_LABEL = "NettoolsDevice"
RELATIONSHIP_TYPE = "NETTOOLS_ADJACENT_VIA"

# Environment variables write_graph reads credentials from. No default is
# offered for the URI or the password -- guessing either wrong means silently
# writing to (or reading "success" from) the wrong database, which is worse
# than refusing to run. NEO4J_USER defaults to "neo4j" only because that is
# the fixed, non-secret account name every stock neo4j deployment uses (the
# *password* is the secret; the username is not).
_ENV_URI = "NEO4J_URI"
_ENV_USER = "NEO4J_USER"
_ENV_PASSWORD = "NEO4J_PASSWORD"
_ENV_DATABASE = "NEO4J_DATABASE"
_DEFAULT_USER = "neo4j"


def _replace_graph_tx(tx: Any, graph: Graph) -> dict[str, int]:
    """Run inside one write transaction: delete this collector's whole prior graph, recreate it.

    ``CREATE``, not ``MERGE`` -- there is no need to match against anything
    that might already exist, because the ``DETACH DELETE`` immediately above
    guarantees nothing does, within this same transaction. That single
    sequenced pair (G5, mutation-tested below) is the whole idempotency
    mechanism: no natural key to get subtly wrong, no accumulation across
    reruns, no orphaned edge left over when the fabric changes and a link
    that used to exist stops being reported. It reads as expensive for a
    graph this size (nine devices, a couple dozen edges) and is not a
    strategy that scales past a lab -- see "what I chose not to build".
    """

    tx.run(f"MATCH (n:{NODE_LABEL}) DETACH DELETE n")

    for node in graph.nodes:
        tx.run(
            f"CREATE (:{NODE_LABEL} {{name: $name, platform: $platform, "
            f"configured_hostname: $configured_hostname}})",
            name=node.name,
            platform=node.platform,
            configured_hostname=node.configured_hostname,
        )

    for edge in graph.edges:
        tx.run(
            f"MATCH (a:{NODE_LABEL} {{name: $device_a}}), (b:{NODE_LABEL} {{name: $device_b}}) "
            f"CREATE (a)-[:{RELATIONSHIP_TYPE} {{protocol: $protocol, "
            f"interface_a: $interface_a, interface_b: $interface_b, "
            f"state: $state, observed_by: $observed_by}}]->(b)",
            device_a=edge.device_a,
            device_b=edge.device_b,
            protocol=edge.protocol,
            interface_a=edge.interface_a,
            interface_b=edge.interface_b,
            state=edge.state,
            observed_by=list(edge.observed_by),
        )

    return {"nodes": len(graph.nodes), "edges": len(graph.edges)}


def write_graph(
    graph: Graph,
    *,
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    driver_factory: Callable[[str, tuple[str, str]], Any] | None = None,
) -> dict[str, int]:
    """Replace neo4j's whole topology projection with ``graph``, in one transaction.

    Credentials are read from ``NEO4J_URI``/``NEO4J_USER``/``NEO4J_PASSWORD``
    (``NEO4J_DATABASE`` optionally selects a non-default database) when not
    passed explicitly, and only from there -- never a hardcoded fallback,
    never logged, never included in any exception message this function
    raises (G4, mutation-tested below: a missing variable is named, its value
    never is). Raises ``RuntimeError`` if a required value is missing from
    both the argument and the environment.

    ``driver_factory``, if given, replaces ``neo4j.GraphDatabase.driver`` --
    the seam the test suite uses to exercise this function's Cypher and
    transaction sequencing against a fake driver with **no neo4j package
    installed and no database running** (mirroring this repo's existing
    ``sender=`` seam convention rather than mocking a third-party driver's
    internals). Leave it unset to talk to a real neo4j.
    """

    resolved_uri = uri or os.environ.get(_ENV_URI)
    resolved_user = user or os.environ.get(_ENV_USER) or _DEFAULT_USER
    resolved_password = password or os.environ.get(_ENV_PASSWORD)
    resolved_database = database or os.environ.get(_ENV_DATABASE)

    missing = [
        name
        for name, value in ((_ENV_URI, resolved_uri), (_ENV_PASSWORD, resolved_password))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "write_graph needs neo4j credentials; missing environment variable(s): "
            + ", ".join(missing)
        )

    if driver_factory is not None:
        make_driver = driver_factory
    else:
        # Imported here, not at module scope: this is the only line in the
        # module that touches the `neo4j` package, so the pure half
        # (`build_graph` and everything it calls) never requires the optional
        # `graph` extra to be installed (G3, mutation-tested below).
        from neo4j import GraphDatabase

        def make_driver(target_uri: str, auth: tuple[str, str]) -> Any:
            return GraphDatabase.driver(target_uri, auth=auth)

    driver = make_driver(resolved_uri, (resolved_user, resolved_password))
    try:
        session_kwargs = {"database": resolved_database} if resolved_database else {}
        with driver.session(**session_kwargs) as session:
            return session.execute_write(_replace_graph_tx, graph)
    finally:
        driver.close()
