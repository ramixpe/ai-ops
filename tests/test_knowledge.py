"""B-478 -- the repo's own knowledge: search, mnemonics, operator notes."""

from __future__ import annotations

from agent_nettools import knowledge as K
from agent_nettools.knowledge import explain_mnemonic, load_mnemonic_table, search_knowledge

# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_a_known_term_returns_its_source_with_a_citation():
    result = search_knowledge("coherence")

    assert result["results"], "the docs demonstrably discuss coherence"
    top = result["results"][0]
    assert ":" in top["source"], "citations are path:line, openable by a human"
    assert top["snippet"]


def test_an_operator_note_outranks_prose_about_the_project():
    """The notes twice held answers nobody read (OBS-139). When a note matches,
    it is about THIS fabric and must surface above general documentation."""

    result = search_knowledge("prefixes advertised")

    sources = [r["source"] for r in result["results"]]
    assert any(s.startswith("inventory:") for s in sources)


def test_an_unknown_term_returns_empty_not_an_error():
    result = search_knowledge("xyzzy-plugh-nothing")
    assert result["results"] == []


def test_the_query_is_capped_and_escaped():
    # A regex metacharacter must be matched LITERALLY, not compiled as a
    # pattern -- the previous version only checked capping, so removing
    # re.escape passed it identically (2026-08-18 review, vacuous companion).
    # The glossary contains the literal word "intent"; a query of ".ntent"
    # (regex: any char + ntent) must NOT match it if the query is escaped.
    literal = search_knowledge("intent")
    regexy = search_knowledge(".ntent")
    assert literal["results"], "the term 'intent' is demonstrably in the docs"
    assert not any("intent" in r["snippet"].lower() and "ntent" not in r["snippet"].lower()
                   for r in regexy["results"]), \
        ".ntent matched 'intent' -- the query was compiled as a regex, not escaped"
    # And capping still holds.
    capped = search_knowledge("(a+)+" + "x" * 10_000)
    assert isinstance(capped["results"], list)
    assert len(capped["query"]) <= K.MAX_QUERY_LENGTH


def test_a_missing_corpus_is_a_stated_fact(monkeypatch):
    monkeypatch.setattr(K, "_repo_root", lambda: None)
    result = search_knowledge("coherence")
    assert "note" in result and "not present" in result["note"]


# --------------------------------------------------------------------------- #
# Mnemonics
# --------------------------------------------------------------------------- #


def test_every_table_entry_carries_the_required_keys():
    """The schema test -- the table cannot rot a key at a time."""

    entries = load_mnemonic_table()
    assert len(entries) >= 10
    for entry in entries:
        for key in ("mnemonic", "meaning", "typical_causes", "severity_note"):
            assert key in entry, f"{entry.get('mnemonic')}: missing {key}"
        assert "investigate_with" in entry  # may be None, must be present


def test_every_event_routing_mnemonic_has_a_knowledge_entry():
    """The two tables must not drift: what routing can route, knowledge can
    explain."""

    from agent_nettools.event_routing import MNEMONIC_FLOW_TABLE

    known = {e["mnemonic"] for e in load_mnemonic_table()}
    for mnemonic, _flow, _extract in MNEMONIC_FLOW_TABLE:
        assert mnemonic in known


def test_a_known_mnemonic_explains_fully():
    result = explain_mnemonic("%ROUTING-BGP-5-ADJCHANGE")
    assert result["known"] is True
    assert result["investigate_with"]["flow"] == "bgp_session"


def test_an_unknown_mnemonic_still_yields_its_parts():
    result = explain_mnemonic("PLATFORM-FANTRAY-2-FAILURE")
    assert result["known"] is False
    assert result["facility"] == "PLATFORM-FANTRAY"
    assert result["severity"] == 2
    assert result["code"] == "FAILURE"


def test_garbage_is_a_structured_unknown():
    result = explain_mnemonic("not a mnemonic")
    assert result["known"] is False
    assert "facility" not in result
