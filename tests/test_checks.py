"""Contract tests for checks.py (T-019).

The five checks themselves land at T-020. What is pinned here is the shape
every one of them must satisfy, and above all the rule the module exists to
enforce: **a check may only answer `healthy` about a field it actually read.**
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from agent_nettools import checks, parsers

# --------------------------------------------------------------------------- #
# Purity -- no I/O, no device, no inventory
# --------------------------------------------------------------------------- #


def test_checks_imports_nothing_that_can_touch_a_device():
    """T-019's acceptance criterion, tested the only way that actually works.

    The obvious implementation -- asserting `agent_nettools.network_tools` is
    absent from sys.modules -- would fail however pure checks.py is, because
    `agent_nettools/__init__.py` eagerly imports agent_loop, which pulls in
    network_tools and inventory. Importing *any* submodule imports the package.
    That trap is recorded as OBS-033.

    So inspect the module's own import statements instead, which is what the
    criterion actually means.
    """

    source = Path(inspect.getfile(checks)).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level:
            imported.update(alias.name for alias in node.names)

    forbidden = {
        "inventory",
        "network_tools",
        "lab",
        "inventory_model",
        "credential_resolver",
        "evidence_store",
        "netmiko",
        "os",
        "socket",
        "subprocess",
        "requests",
    }
    assert not (imported & forbidden), f"checks.py imports {sorted(imported & forbidden)}"


def test_checks_reads_no_environment_and_opens_no_files():
    """A check is a pure function of the evidence handed to it."""

    source = Path(inspect.getfile(checks)).read_text(encoding="utf-8")
    for forbidden in ("os.environ", "getenv", "open(", "Path(", "datetime.now", "time.time"):
        assert forbidden not in source, f"checks.py contains {forbidden!r}"


# --------------------------------------------------------------------------- #
# CheckResult
# --------------------------------------------------------------------------- #


def test_check_result_is_frozen():
    """A verdict is a record of what was observed.

    Mutable, a caller could turn an `unevaluated` into a `healthy` several
    frames from the evidence, and the grounding check would never know.
    """

    result = checks.healthy(subject="x", evidence_keys=("RR1:bgp",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = checks.BROKEN  # type: ignore[misc]


def test_only_three_statuses_exist():
    assert checks.STATUSES == frozenset({"healthy", "broken", "unevaluated"})


def test_an_unknown_status_is_rejected_at_construction():
    with pytest.raises(ValueError, match="must be one of"):
        checks.CheckResult("degraded", evidence_keys=("RR1:bgp",))


@pytest.mark.parametrize("status", [checks.HEALTHY, checks.BROKEN])
def test_a_conclusive_verdict_must_cite_evidence(status):
    """D20: every claim cites an evidence key. A verdict citing nothing cannot
    be grounded, so it is refused at construction rather than downstream."""

    with pytest.raises(ValueError, match="must cite at least one evidence key"):
        checks.CheckResult(status, reason="because", subject="x", evidence_keys=())


def test_unevaluated_may_cite_nothing():
    """The one exception, and the reason for it: there may genuinely have been
    nothing to read."""

    result = checks.unevaluated(reason="no bgp section")
    assert result.evidence_keys == ()
    assert result.status == checks.UNEVALUATED


def test_is_conclusive_excludes_unevaluated():
    assert checks.healthy(subject="x", evidence_keys=("k",)).is_conclusive
    assert checks.broken(reason="r", subject="x", evidence_keys=("k",)).is_conclusive
    assert not checks.unevaluated(reason="r").is_conclusive


def test_evidence_key_matches_the_operational_memory_convention():
    """glossary.md keys by object, device first: PE2:bgp:10.255.0.31."""

    assert checks.evidence_key("PE2", "bgp", "10.255.0.31") == "PE2:bgp:10.255.0.31"
    assert checks.evidence_key("PE2", "interfaces") == "PE2:interfaces"


# --------------------------------------------------------------------------- #
# The rule: absence is unevaluated
# --------------------------------------------------------------------------- #


def _section(parse_status, records=None):
    return {"data": {"parse_status": parse_status, "parsed": {"records": records or []}}}


def test_require_parsed_passes_a_parsed_section_through():
    evidence = {"bgp": _section(parsers.PARSE_OK, [{"neighbor": "10.255.0.12"}])}
    section, bail = checks.require_parsed(evidence, "bgp")
    assert bail is None
    assert section is evidence["bgp"]


@pytest.mark.parametrize(
    ("parse_status", "expected_reason"),
    [
        (parsers.PARSE_FAILED, "did not parse"),
        (parsers.PARSE_UNAVAILABLE, "not available on this platform"),
        (None, "no parse status"),
    ],
)
def test_a_section_that_did_not_parse_is_unevaluated(parse_status, expected_reason):
    """Never healthy -- nothing was verified. Never broken -- nothing was
    observed. And each failure keeps its own reason, because collapsing them
    throws away the only clue about what to do next."""

    evidence = {"bgp": _section(parse_status)}
    section, bail = checks.require_parsed(evidence, "bgp", subject="10.255.0.12")

    assert section is None
    assert bail is not None
    assert bail.status == checks.UNEVALUATED
    assert expected_reason in bail.reason
    assert bail.subject == "10.255.0.12"


def test_a_missing_section_is_unevaluated_not_healthy():
    """The OBS-044 shape: the evidence simply is not there."""

    section, bail = checks.require_parsed({}, "interfaces", subject="Gi0/0/0/0")
    assert section is None
    assert bail.status == checks.UNEVALUATED
    assert "no 'interfaces' section" in bail.reason


def test_parsed_records_cannot_distinguish_absent_from_empty():
    """Pinning the hazard that makes require_parsed necessary.

    parsed_records() returns [] for both "parsed fine, no records" and "never
    parsed at all". A check calling it without gating on the parse status
    first is exactly how absence gets read as zero -- OBS-044.
    """

    parsed_fine_but_empty = _section(parsers.PARSE_OK, [])
    never_parsed = _section(parsers.PARSE_FAILED)

    assert checks.parsed_records(parsed_fine_but_empty) == []
    assert checks.parsed_records(never_parsed) == []
    # Indistinguishable by records alone -- which is the whole point.
    assert checks.parsed_records(parsed_fine_but_empty) == checks.parsed_records(never_parsed)
    # require_parsed is what tells them apart.
    assert checks.require_parsed({"x": parsed_fine_but_empty}, "x")[1] is None
    assert checks.require_parsed({"x": never_parsed}, "x")[1].status == checks.UNEVALUATED


def test_unevaluated_always_carries_a_reason():
    """An unexplained 'unevaluated' is only marginally better than a wrong
    answer: the operator still cannot tell whether to re-collect, fix a
    parser, or look elsewhere."""

    with pytest.raises(TypeError):
        checks.unevaluated()  # type: ignore[call-arg]
