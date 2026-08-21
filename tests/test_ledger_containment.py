"""B-700: the ledger's stored free text, and the consumer it has not met yet.

`ledger.py` stores a descent's `reason` verbatim, and that sentence embeds the
far end's own words -- `checks.py`'s `_last_reset_note` (B-430) splices a BGP
peer's unauthenticated `last_reset_reason` into it, and OBS-691 measured three
such fragments in a single run. It also stores an operator/event-derived
`subject`, the axis B-482 found reachable through an Alertmanager-forwarded
alert.

None of that is contained, and **that is correct today**: no MCP tool exposes
the ledger, and `incident_correlation.from_ledger_diagnoses` matches on a row's
literal `subject`, so wrapping the existing readers would break correlation
while protecting nobody.

What this file guards is the moment that stops being true. `CLAUDE.md`'s own
statement of invariant 4:

    An invariant that holds for every internal caller is not an invariant;
    it is a convention that has not yet met a new consumer.

The ticket path had this identical shape until B-680 gave it one. So the rule
here is not "contain the ledger" -- it is **"if the ledger becomes
model-reachable, it must go through `contained_diagnoses`"**, checked by
reading the MCP server's source rather than by anyone remembering.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent_nettools import ledger, model_egress

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = REPO_ROOT / "mcp_server" / "server.py"

#: The ledger readers that return stored free text uncontained. If a tool
#: reaches for one of these, it is handing a model unmarked device words.
_RAW_LEDGER_READERS = ("diagnoses", "entries", "summary", "render_summary_text")


def _mcp_source() -> str:
    return MCP_SERVER.read_text(encoding="utf-8")


def test_no_mcp_tool_reads_the_ledger_through_an_uncontained_accessor():
    """The guard itself.

    Deliberately a source scan, not a registry walk: a tool that imports
    `ledger.diagnoses` and inlines the rows into its own response dict would
    not name "ledger" anywhere the registry can see, and this is exactly the
    shape that went eight phases unnoticed on the MCP surface before OBS-111.
    """

    source = _mcp_source()
    tree = ast.parse(source)

    imported_raw = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "ledger" in node.module:
            for alias in node.names:
                if alias.name in _RAW_LEDGER_READERS:
                    imported_raw.append(alias.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "ledger" and node.attr in _RAW_LEDGER_READERS:
                imported_raw.append(f"ledger.{node.attr}")

    assert imported_raw == [], (
        f"mcp_server/server.py reaches the ledger through {imported_raw}, which "
        "returns stored `reason`/`subject`/`note` text uncontained. That text "
        "carries the far end's own words (B-430/OBS-691). Use "
        "`ledger.contained_diagnoses()` instead, which wraps them in "
        "untrusted-content delimiters -- or, if this tool genuinely must have "
        "the literal values the way `incident_correlation` does, say so here "
        "with the reason."
    )


def test_contained_diagnoses_wraps_every_untrusted_field(tmp_path, monkeypatch):
    """The safe path actually contains, on a row built the way a real run
    builds one -- with genuine captured device text, not a hand-written string
    that has nothing in it to contain. That distinction is OBS-691's whole
    lesson: a containment test whose fixture carries no device words cannot
    fail whatever the code does.
    """

    monkeypatch.setenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    ledger.reset()

    device_words = "BGP Notification sent: hold time expired"
    ledger.record_diagnosis(
        device="RR1",
        subject="10.255.0.12",
        flow="bgp_session",
        finding="transport_blocked",
        trustworthy=True,
        source="live",
        reason=f"no TCP transport; the device last recorded a reset with reason '{device_words}'",
    )

    rows = ledger.contained_diagnoses()
    assert len(rows) == 1
    row = rows[0]

    for field in ("reason", "subject"):
        assert model_egress.DEVICE_TEXT_OPEN in row[field], f"{field} is not contained"
        assert model_egress.DEVICE_TEXT_CLOSE in row[field], f"{field} is not closed"
    assert device_words in row["reason"], "containment lost the text it was carrying"

    # Positive control (OBS-181): containment must not be indiscriminate. A
    # code-declared field in the same row stays bare -- without this, a walker
    # that wrapped every string would satisfy the assertions above while
    # telling a reader nothing about which half came off the wire.
    assert row["finding"] == "transport_blocked"
    assert model_egress.DEVICE_TEXT_OPEN not in row["finding"]
    assert model_egress.DEVICE_TEXT_OPEN not in row["device"]


def test_the_raw_reader_is_still_raw_because_correlation_depends_on_it(tmp_path, monkeypatch):
    """The other half of the design, pinned so nobody "fixes" it.

    `incident_correlation.from_ledger_diagnoses` matches on a row's literal
    `subject` (`incident_correlation.py:401,406`). If `diagnoses()` ever
    started wrapping, that comparison would silently stop matching -- a
    correlation that finds nothing looks exactly like a fabric with nothing to
    correlate. The split is the point: raw for our own code, contained for a
    model, the same shape `ticket.read_ticket` and `ticket_read` already have.
    """

    monkeypatch.setenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    ledger.reset()
    ledger.record_diagnosis(
        device="RR1", subject="10.255.0.12", flow="bgp_session",
        finding="transport_blocked", trustworthy=True, source="live",
        reason="peer reports 'administrative shutdown'",
    )

    raw = ledger.diagnoses()[0]
    assert raw["subject"] == "10.255.0.12", "a correlator matching on this would break"
    assert model_egress.DEVICE_TEXT_OPEN not in raw["reason"]
