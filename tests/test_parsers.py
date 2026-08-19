"""Section 0.10 line accounting for parsers.py (B-404).

``template_parsers.py`` was built with section 0.10's completeness rule from
the start; the six hand-written parsers in ``parsers.py`` predate it and were
deliberately deferred (BUILD-PLAN.md 0.10: "It does not apply retroactively to
the six hand-written parsers in ``parsers.py`` -- do not rewrite working,
tested code. Log a finding..."). This is that scheduled retrofit's test suite.

The tests below drive the *committed fixtures* -- the ground truth per B-404's
own instructions -- rather than synthetic input, and require
``unaccounted_lines == []``/``unparsed_rows == 0`` for every one of the six
zero-argument intents against every device and every label
(``t0``/``t1``/``healthy``/``broken``). That is the same "every committed
fixture round-trips clean" shape ``test_template_parsers.py`` already
established per template.
"""

from __future__ import annotations

import pytest
from helpers import FIXTURE_DIR

from agent_nettools import parsers

# One or more command files per intent, in the order collect_evidence renders
# them (matters only for `facts`, the sole multi-command intent -- see
# platforms.PLATFORM_INTENTS["cisco_xr"]["facts"]).
INTENT_FILES: dict[str, tuple[str, ...]] = {
    "facts": ("show-running-config-hostname.txt", "show-version.txt"),
    "interfaces": ("show-interfaces-brief.txt",),
    "bgp": ("show-bgp-summary.txt",),
    "lldp": ("show-lldp-neighbors.txt",),
    "isis": ("show-isis-neighbors.txt",),
    # B-109. Only captured under t0/t1 so far (see fixtures/README review
    # notes) -- unlike the six above, present under every label -- so these
    # two intents' fixture cases are a strict subset of the others'.
    "ldp": ("show-mpls-ldp-neighbor.txt",),
    "ldp_discovery": ("show-mpls-ldp-discovery.txt",),
    "sr": ("show-segment-routing-traffic-eng-policy.txt",),
}

# Intents whose command carries a table -- used by the anti-vacuity test below
# to make sure the corpus actually exercises record-producing rows, not just
# meta-only output (`facts` legitimately never produces records at all).
_RECORD_BEARING_INTENTS = ("interfaces", "bgp", "lldp", "isis", "ldp", "ldp_discovery", "sr")


def _discover_fixture_cases() -> list[tuple[str, str, str]]:
    """Every (intent, device, label) combo with all of that intent's command
    files present on disk.

    Discovered from the filesystem, like ``test_template_parsers.py``'s own
    fixture globs, rather than a hardcoded device/label list -- a future
    ``nettools capture`` run that adds a device or a label is covered without
    editing this file.
    """

    cases: list[tuple[str, str, str]] = []
    xr_dir = FIXTURE_DIR / "cisco_xr"
    for device_dir in sorted(p for p in xr_dir.iterdir() if p.is_dir()):
        for label_dir in sorted(p for p in device_dir.iterdir() if p.is_dir()):
            for intent, files in INTENT_FILES.items():
                if all((label_dir / fname).exists() for fname in files):
                    cases.append((intent, device_dir.name, label_dir.name))
    return cases


FIXTURE_CASES = _discover_fixture_cases()


def _load_outputs(intent: str, device: str, label: str) -> dict[str, str]:
    xr_dir = FIXTURE_DIR / "cisco_xr" / device / label
    return {fname: (xr_dir / fname).read_text() for fname in INTENT_FILES[intent]}


# --------------------------------------------------------------------------- #
# Anti-vacuity companion (BUILD-PLAN.md 0.12)
# --------------------------------------------------------------------------- #
#
# The round-trip test below could pass "by measuring nothing" two different
# ways: an empty parametrize list (a glob that silently stopped matching), or
# a non-empty one whose every case still yields zero records (every fixture
# happening to be the trivial "nothing configured" shape -- lldp/isis/sr all
# have one on this fabric). Both companions are required per 0.12's own
# wording: "assert the collection is currently empty ... or assert the corpus
# exercises every outcome the test discriminates between."


def test_the_fixture_corpus_is_non_empty_and_covers_every_intent():
    """If this ever fails, the round-trip test below would be silently
    parametrized over an empty or lopsided case list instead of the full
    six-intent x nine-device x four-label surface."""

    assert len(FIXTURE_CASES) >= 8 * 9  # at least one label per device per intent
    intents_covered = {intent for intent, _device, _label in FIXTURE_CASES}
    assert intents_covered == set(INTENT_FILES)


def test_the_fixtures_actually_produce_records_not_just_meta():
    """The other half: a corpus of fixtures that all parse to zero records
    would let the round-trip test pass while never exercising a single
    record's worth of line-consumption, only headers and banners.

    Checked per record-bearing intent (`facts` is deliberately excluded --
    per its own docstring it never produces records, meta-only is its correct
    shape) so a single always-empty intent cannot hide behind the others.
    """

    produced: dict[str, bool] = dict.fromkeys(_RECORD_BEARING_INTENTS, False)
    for intent, device, label in FIXTURE_CASES:
        if intent not in _RECORD_BEARING_INTENTS or produced[intent]:
            continue
        outputs = _load_outputs(intent, device, label)
        parsed, status = parsers.parse_intent("cisco_xr", intent, outputs)
        assert status is parsers.PARSE_OK, f"{device}/{label}/{intent}: {status}"
        if parsed["records"]:
            produced[intent] = True

    missing = [intent for intent, seen in produced.items() if not seen]
    assert not missing, f"no fixture produced any records for: {missing}"


# --------------------------------------------------------------------------- #
# The round-trip test required by BUILD-PLAN.md 0.10
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", FIXTURE_CASES, ids=lambda c: f"{c[0]}/{c[1]}/{c[2]}")
def test_every_committed_fixture_accounts_for_every_line(case: tuple[str, str, str]):
    """Section 0.10, pinned against every real fixture on disk for all six
    zero-argument intents -- the exact shape BUILD-PLAN.md 0.10 specifies:

        parsed = parse(raw)
        assert parsed["meta"]["unaccounted_lines"] == []
        assert parsed["meta"]["unparsed_rows"] == 0

    ``unaccounted_lines`` and ``unparsed_rows`` are different failures (see
    the ``parsers`` module docstring): the first means the parser does not
    know what a line is, the second means it knows and the line did not fit.
    Both must be clean on every committed capture.
    """

    intent, device, label = case
    outputs = _load_outputs(intent, device, label)

    parsed, status = parsers.parse_intent("cisco_xr", intent, outputs)

    assert status is parsers.PARSE_OK, f"{device}/{label}/{intent}: status={status}"
    assert parsed["meta"]["unaccounted_lines"] == [], (
        f"{device}/{label}/{intent}: {parsed['meta']['unaccounted_lines']}"
    )
    assert parsed["meta"]["unparsed_rows"] == 0, (
        f"{device}/{label}/{intent}: unparsed_rows={parsed['meta']['unparsed_rows']}"
    )


# --------------------------------------------------------------------------- #
# Every declared IgnoreRule must carry a specific, non-empty reason
# --------------------------------------------------------------------------- #


def _declared_ignore_rules() -> list[tuple[str, object]]:
    """Every ``IgnoreRule`` in every ``<INTENT>_IGNORES`` constant this module
    declares, discovered the same way ``test_field_audit.py`` discovers
    ``template_parsers``' own ``_IGNORES`` constants -- by name convention,
    not a hand-maintained list that could silently stop tracking a new one.
    """

    return [
        (name, rule)
        for name in dir(parsers)
        if name.endswith("_IGNORES")
        for rule in getattr(parsers, name)
    ]


# Reasons that would technically satisfy "non-empty" while defeating 0.10's
# actual requirement -- a reviewer being able to tell *what each pattern is
# for* from the reason alone, not just that a reason exists.
_NON_SPECIFIC_REASONS = {"misc", "other", "n/a", "na", "unknown", "todo", "tbd", "various"}


def test_every_declared_ignore_rule_pinned_by_count():
    """Anti-vacuity companion for the reason check below: if every
    ``<INTENT>_IGNORES`` constant were ever emptied out, the reason check
    would pass over nothing. Pinned by count like
    ``test_field_audit.py``'s ``EXPECTED_DEFERRED`` so the corpus cannot
    shrink silently -- update the number and say why in the commit if it
    ever needs to move.

    54 -> 67 at the protocol-coverage sweep (2026-08-19): ``BGP_VPNV4_IGNORES``
    is a new, deliberately separate 13-rule constant for ``show bgp vpnv4
    unicast summary`` -- see its module comment for why it is not merged into
    ``BGP_IGNORES`` even though 12 of its 13 patterns are identical.
    """

    rules = _declared_ignore_rules()
    assert len(rules) == 67, (
        f"{len(rules)} ignore rules declared across parsers.py's seven intents, expected 67"
    )


def test_every_declared_ignore_rule_has_a_specific_reason():
    """0.10's own wording: "Keep ignore patterns in one named constant per
    template with a comment explaining what each one is, so a reviewer can
    see the full accounting in one place." A blank or generic placeholder
    reason defeats that as completely as no reason at all.
    """

    rules = _declared_ignore_rules()
    assert rules, "no <INTENT>_IGNORES constants found on agent_nettools.parsers"

    for name, rule in rules:
        reason = rule.reason
        assert reason and reason.strip(), f"{name}: {rule.pattern!r} has an empty reason"
        assert reason.strip().lower() not in _NON_SPECIFIC_REASONS, (
            f"{name}: {rule.pattern!r} has a non-specific reason: {reason!r}"
        )
        # A reason shorter than this is very unlikely to explain *why* a line
        # carries nothing extractable, as distinct from merely restating the
        # pattern -- a cheap proxy for "specific", not a rewrite of 0.10.
        assert len(reason.strip()) >= 15, f"{name}: {rule.pattern!r} has a suspiciously short reason: {reason!r}"


def test_ignore_rule_reasons_are_not_reused_verbatim_across_unrelated_lines():
    """A reason copy-pasted across every rule in a constant is the same
    failure as a single broad catch-all regex -- it stops being reviewable
    per-line. Some repetition is legitimate (closely related line shapes
    sharing one explanation, as ``template_parsers.BGP_NEIGHBOR_IGNORES``'s
    write-pulse-bookkeeping family already does); this only guards against
    reasons under this module being used *literally everywhere* within their
    own intent, which is what a lazy catch-all would look like here.
    """

    from collections import Counter

    by_intent: dict[str, Counter] = {}
    for name, rule in _declared_ignore_rules():
        by_intent.setdefault(name, Counter())[rule.reason] += 1

    for name, counter in by_intent.items():
        total = sum(counter.values())
        if total < 2:
            continue  # nothing to "reuse" with only one rule declared
        most_common_reason, most_common_count = counter.most_common(1)[0]
        assert most_common_count < total, (
            f"{name}: every rule shares the exact reason {most_common_reason!r} -- "
            "not distinguishable per line"
        )


# --------------------------------------------------------------------------- #
# The cycle B-404 created, pinned rather than trusted
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "first", ["agent_nettools.parsers", "agent_nettools.template_parsers"]
)
def test_the_two_parser_modules_import_in_either_order(first):
    """`parsers` and `template_parsers` now import each other (B-404).

    `parsers` needs the accounting primitives; `template_parsers` needs the
    status vocabulary. Python tolerates that only because the accounting
    primitives are fully defined *before* `template_parsers` reaches back into
    `parsers` — an ordering constraint that lives in a comment and is invisible
    at the point where someone would break it, by tidying a mid-file import back
    to the top.

    `ruff` does not currently move it, which is not the same as safe. **Where a
    condition is checkable, check it** (T-029c): a subprocess importing each
    module first, so the failure is a red test rather than an `ImportError` in
    whichever caller happens to import first.
    """

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", f"import {first}; import agent_nettools.parsers, "
         f"agent_nettools.template_parsers; print('ok')"],
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, (
        f"importing {first} first breaks the cycle:\n{result.stderr[-1500:]}"
    )
    assert "ok" in result.stdout


def test_the_accounting_primitives_carry_no_dependency_on_parsers():
    """Why the cycle is resolvable at all, asserted.

    If `IgnoreRule`/`account_lines`/`finalize` ever grow a dependency on
    `parsers`, the ordering that makes this work stops existing and the fix is
    a third module rather than a comment. This fails at that moment instead of
    at some importer's.
    """

    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "agent_nettools" / "template_parsers.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    parsers_import_line = min(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "parsers"
    )
    finalize_line = max(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"account_lines", "finalize"}
    )

    assert finalize_line < parsers_import_line, (
        "the accounting primitives must be defined before template_parsers "
        "reaches into parsers, or the cycle breaks"
    )
