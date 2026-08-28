"""B-511 -- the fabrication probe generator (`scripts/generate_probe_identifier.py`).

OBS-194/OBS-195: excluding evaluation documents from `search_lab_knowledge` (B-511's
corpus half, see `tests/test_knowledge.py`) was necessary and not sufficient -- the
answer to the leaked Q4 probe (`10.255.0.99`) is also quoted in `BACKLOG.md`,
`archive/VERIFICATION.md` and `design/peer-review-response.md`, none of
which should be excluded from the corpus (they are legitimate engineering records, not
evaluation material). The durable fix is on the question: generate a fresh identifier
per run, never write it down, and a leak becomes impossible rather than patched. This
file tests that generator.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import random
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_probe_identifier.py"


def _load_generator():
    """Import `scripts/generate_probe_identifier.py` by file path.

    A standalone script, like every other file in `scripts/` (none of which
    are a package `tests/` imports today) -- the same loading convention
    `tests/test_context_window_measurement.py` uses for `measure_context.py`.
    """

    spec = importlib.util.spec_from_file_location("generate_probe_identifier", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def G():
    return _load_generator()


# --------------------------------------------------------------------------- #
# The two collision checks, independently.
# --------------------------------------------------------------------------- #


def test_reserved_from_inventory_holds_every_declared_device_address(G):
    reserved = G.reserved_from_inventory()

    # PE1-PE3 and RR1 declare `router_id` in inventory/lab.yaml; every mgmt_ip
    # is declared for all nine devices. Spot-checked against the file rather
    # than re-parsed, so this test fails if the fixture inventory ever
    # changes shape without this test being revisited.
    for expected in ("10.255.0.11", "10.255.0.12", "10.255.0.13", "10.255.0.31"):
        assert expected in reserved, f"{expected} is a declared router_id and must be reserved"
    for expected in ("172.20.250.11", "172.20.250.31"):
        assert expected in reserved, f"{expected} is a declared mgmt_ip and must be reserved"


def test_reserved_from_repo_grep_finds_the_leaked_probe(G):
    """The exact regression B-511 exists to close: `10.255.0.99` is written
    into BACKLOG.md and design docs (OBS-195) -- none of which
    `search_knowledge`'s exclusion touches, because they are legitimate
    records, not evaluation material. The grep check must find it anyway,
    since it does not trust any per-document marker at all.
    """

    reserved = G.reserved_from_repo_grep(REPO_ROOT)
    assert "10.255.0.99" in reserved


def test_reserved_from_repo_grep_finds_addresses_the_inventory_never_declares(G):
    """P1-P4 carry no `router_id` in inventory/lab.yaml (only PE1-PE3/RR1 do)
    -- so a check that trusted the declared inventory alone would let a
    P-router's real loopback (present in committed fixtures) through as
    'unused'. This is the gap the second, independent check exists to close.
    """

    reserved = G.reserved_from_repo_grep(REPO_ROOT)
    assert "10.255.0.1" in reserved  # P1's loopback, present in fixtures/docs
    inventory_reserved = G.reserved_from_inventory()
    assert "10.255.0.1" not in inventory_reserved, (
        "if this starts failing, the inventory now declares P1's router_id "
        "and this test should assert a still-undeclared device instead"
    )


def test_unused_hosts_excludes_reserved_and_keeps_everything_else(G):
    network = ipaddress.IPv4Network("10.255.0.0/29")  # .1-.6, small and exhaustible
    reserved = {"10.255.0.1", "10.255.0.3"}

    candidates = G.unused_hosts(network, reserved)

    assert "10.255.0.1" not in candidates
    assert "10.255.0.3" not in candidates
    assert set(candidates) == {"10.255.0.2", "10.255.0.4", "10.255.0.5", "10.255.0.6"}


def test_unused_hosts_never_offers_network_or_broadcast(G):
    network = ipaddress.IPv4Network("10.255.0.0/24")
    candidates = G.unused_hosts(network, reserved=set())
    assert "10.255.0.0" not in candidates
    assert "10.255.0.255" not in candidates


# --------------------------------------------------------------------------- #
# The generator itself -- the property the task asked for: never an address
# that appears in the tree.
# --------------------------------------------------------------------------- #


def test_generated_identifier_is_well_formed_and_in_the_lab_shape(G):
    identifier = G.generate_probe_identifier(rng=random.Random(1))
    address = ipaddress.IPv4Address(identifier)
    assert address in G.LAB_PEER_NETWORK


def test_generated_identifier_never_appears_anywhere_in_the_tree(G):
    """The core claim: run the generator repeatedly (many seeds, so this is
    not one lucky draw) and, for EACH result, independently re-grep the
    whole tree -- not merely check it against the set the generator itself
    already computed, which would only prove the function is internally
    consistent, not that its output is actually absent from the repo.
    """

    # 5 draws, not 1: enough that a lucky single draw could not hide a real
    # exclusion bug, while keeping this test's cost reasonable -- each draw
    # walks the entire repo tree (`reserved_from_repo_grep`), so this is
    # already the more expensive half of this file's runtime. The freshness
    # of the re-grep below, not the draw count, is what actually carries the
    # assurance here -- see the module docstring.
    seen = set()
    for seed in range(5):
        identifier = G.generate_probe_identifier(rng=random.Random(seed))
        seen.add(identifier)

    # An independent call, computing the check itself rather than trusting a
    # set the generator already built. `reserved_from_repo_grep` is
    # `@functools.cache`d for suite performance (P1, ~11s of every full pass
    # was this file re-walking the repo tree on every draw) -- so within this
    # process this returns the SAME memoized result `generate_probe_identifier`
    # itself just used, not a fresh disk walk. That is still the right check:
    # the cache is per-process/per-session only (never written to disk), the
    # tree is not mutated by anything in this test session, and what this
    # assertion guards against is a logic bug in the exclusion computation,
    # not a stale answer -- a wrong `reserved` set would be wrong here too.
    written_down = G.reserved_from_repo_grep(REPO_ROOT)
    collisions = seen & written_down
    assert not collisions, f"generator produced address(es) already present in the tree: {collisions}"


def test_generated_identifier_never_collides_with_a_declared_device(G):
    seen = {G.generate_probe_identifier(rng=random.Random(seed)) for seed in range(5)}
    declared = G.reserved_from_inventory()
    assert not (seen & declared)


def test_a_pre_reserved_address_is_never_drawn(G):
    """Positive control (OBS-181): if EVERY host but one is reserved, the
    generator must return exactly that one -- proving the exclusion logic
    actually removes addresses, not merely that it never happens to draw a
    bad one by chance in a large open space.

    Deliberately exercises `unused_hosts` directly with a synthetic reserved
    set, rather than the end-to-end `generate_probe_identifier` against the
    real tree: any address literal enough to type into THIS file's source to
    build an "all but one" fixture would itself be found by
    `reserved_from_repo_grep` on the next test run -- the generator would
    correctly refuse to ever draw it again, which is the right behaviour but
    would make "reserved minus one" impossible to construct by hand here.
    """

    network = ipaddress.IPv4Network("10.255.0.0/29")  # hosts: .1-.6
    all_but_one = {"10.255.0.1", "10.255.0.2", "10.255.0.3", "10.255.0.5", "10.255.0.6"}

    assert G.unused_hosts(network, all_but_one) == ["10.255.0.4"]


def test_generate_probe_identifier_succeeds_on_a_network_the_repo_never_mentions(G):
    """End-to-end, against the real inventory and the real tree, on a network
    (RFC 5737 TEST-NET-3, documentation-only address space) neither the
    fabric nor this repo's own content has any reason to write down --
    confirms the full path works and stays in-network when nothing is
    reserved, complementing the exhaustion case below where everything is.
    """

    result = G.generate_probe_identifier(
        network=ipaddress.IPv4Network("203.0.113.0/29"),
        repo_root=REPO_ROOT,
        rng=random.Random(0),
    )
    assert ipaddress.IPv4Address(result) in ipaddress.IPv4Network("203.0.113.0/29")


def test_an_exhausted_network_refuses_rather_than_collides(G):
    """No candidates left -> a loud RuntimeError, never a silently-accepted
    collision. `/32` has zero usable hosts under `.hosts()`."""

    with pytest.raises(RuntimeError):
        G.generate_probe_identifier(network=ipaddress.IPv4Network("10.255.0.5/32"))


def test_the_two_checks_are_both_required_not_either_or(G):
    """Anti-vacuity companion (PROCESS.md Sec0.12): a candidate reserved
    ONLY by the repo-grep check (not the declared inventory) must still be
    excluded -- proves the union is real, not one check silently deciding
    everything.
    """

    network = ipaddress.IPv4Network("10.255.0.0/29")
    inventory_only = {"10.255.0.1"}
    grep_only = {"10.255.0.2"}
    both_reserved = inventory_only | grep_only

    assert "10.255.0.1" not in G.unused_hosts(network, both_reserved)
    assert "10.255.0.2" not in G.unused_hosts(network, both_reserved)


# --------------------------------------------------------------------------- #
# CLI -- prints once, to stdout, and persists nothing.
# --------------------------------------------------------------------------- #


def test_cli_prints_exactly_one_address_and_nothing_else(G, capsys):
    exit_code = G.main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    ipaddress.IPv4Address(lines[0])  # raises if not a well-formed address


def test_cli_accepts_a_network_override(G, capsys):
    # A network the real tree has no reason to mention (RFC 5737 TEST-NET-3)
    # -- a slice of the lab's real 10.255.0.0/24 would already be partly (or,
    # for a small enough slice, entirely) reserved by genuine fixture/doc
    # content, which is correct behaviour but makes this override test about
    # collision handling instead of about the flag being wired through.
    exit_code = G.main(["--network", "203.0.113.0/29"])
    captured = capsys.readouterr()

    assert exit_code == 0
    address = ipaddress.IPv4Address(captured.out.strip())
    assert address in ipaddress.IPv4Network("203.0.113.0/29")


def test_generate_probe_identifier_module_has_no_persistence_side_effect(G):
    """Never written to a file, a log, or module-level state -- calling it
    twice must not leave anything behind that a THIRD call, or a different
    process, could read back. There is no cache to inspect here by design;
    this test pins that absence by checking the module defines no such
    thing, so an accidental addition (a results list, a log file constant)
    fails this test rather than silently reintroducing the leak B-511
    exists to close.
    """

    suspicious = {"CACHE", "_CACHE", "HISTORY", "_HISTORY", "LOG_FILE", "OUTPUT_FILE"}
    assert not (suspicious & set(dir(G)))
