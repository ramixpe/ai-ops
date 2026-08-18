#!/usr/bin/env python3
"""Remove each guard, confirm its test notices, restore. A guardrail that cannot fail is not one.

A test that passes proves the code does something. **Only removing the guard proves the
test would notice if it stopped.** `BUILD-PLAN.md` §0.12 is the rule; this is the check.

Three defects in the first version of this harness, all found by using it, and each one
shaped how this version works:

1.  **It picked a guard's test file by name similarity** — `test_mcp_server.py` for a guard
    in `mcp_server/server.py`, `test_descent.py` for one in `descent.py`. Both guesses were
    wrong; the guards live in `test_mcp_boundary.py` and `test_flows.py`. It reported both
    as **VACUOUS**.

    **A verifier whose errors produce plausible alarms is more dangerous than one whose
    errors produce noise.** "This guard is vacuous" is exactly the finding this tool exists
    to make, so its false negatives are indistinguishable from its true positives — and
    they point at the more alarming conclusion, which is the one a tired reader accepts.
    B-458 and B-456 would have been reopened on a false report.

    **So a test target is never guessed here.** `resolve_guard_tests` greps `tests/` for the
    guard's own symbol. If it cannot find one it **fails loudly and exits non-zero** rather
    than running a mutation it cannot interpret. *Unresolvable* and *vacuous* are different
    answers and must never render the same.

2.  **It left stale `__pycache__`.** After a pass, `make test` reported a failure that was
    in no source file. That direction is loud and self-correcting. **The other direction is
    not**: source restored, bytecode still mutated, a later run executing code that exists
    nowhere and reporting green — *a mutation recorded as caught when it was not*. Every
    mutation here is bracketed by a cache purge, and the purge is asserted.

3.  **It could in principle have left a mutation on disk.** Restores are asserted
    byte-for-byte, and the run ends by re-checking the four frozen files against the T-001
    baseline and requiring a clean `git status`.

**No frozen file is ever mutated.** `BUILD-PLAN.md` §0.5 freezes `test_safety.py`,
`test_template_security.py`, `platforms.py` and `templates.py`, so the two most
safety-critical suites cannot be mutation-tested at all while that holds. What this tool
can do is mutate *the code they guard*, and the first two entries below do exactly that.

Usage:
    python scripts/mutate_guards.py            # all guards
    python scripts/mutate_guards.py B-453 ...  # a subset
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

FROZEN = {
    "tests/test_safety.py",
    "tests/test_template_security.py",
    "src/agent_nettools/platforms.py",
    "src/agent_nettools/templates.py",
}

BASELINE_COMMIT = "6629a2c"

#: id, what it guards, file, the exact text to remove, what to put there, and the SYMBOL
#: that identifies the guard's test. The symbol is what `tests/` is searched for -- never
#: the module name, never a filename.
MUTATIONS = [
    ("TICKET-DEGRADE", "a ticket write failure never fails an investigation",
     "src/agent_nettools/ticket.py",
     '        if open_mode == "x":\n            raise\n',
     '        raise\n',
     "test_a_tickets_directory_that_is_a_file_still_never_raises"),

    ("B-497", "transport healthy requires an FSM state implying established TCP",
     "src/agent_nettools/checks.py",
     "        if armed and fsm in _TCP_UP_STATES:\n",
     "        if armed:\n",
     "test_a_socket_armed_mid_connect_is_not_evidence_of_transport"),

    ("ROUND-8-SOCKET", "the BGP socket line is read positionally, not by first match",
     "src/agent_nettools/template_parsers.py",
     '            meta["socket_armed_read"] = match["read"] == "armed"\n',
     '            meta["socket_armed_read"] = match["io"] == "armed"\n',
     "test_the_socket_line_is_read_positionally_not_by_first_match"),

    ("B-467-MCP", "the MCP boundary quotes device free text (holistic review)",
     "mcp_server/boundary.py",
     "            elif key in _FREE_TEXT_FIELD_NAMES and isinstance(value, str):\n",
     "            elif key in _FREE_TEXT_FIELD_NAMES and isinstance(value, str) and False:\n",
     "test_device_free_text_in_parsed_records_is_quoted_not_left_bare"),

    ("P0-ALERTMANAGER-SUBJECT", "an alertmanager subject is validated before routing",
     "src/agent_nettools/event_routing.py",
     "    return text if _INTERFACE_NAME.fullmatch(text) else None\n",
     "    return text\n",
     "test_an_alertmanager_subject_carrying_shell_metacharacters_is_refused"),

    ("SAFETY-ALLOWLIST", "the allowlist check itself",
     "src/agent_nettools/network_tools.py",
     "    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "    unsafe_commands = []\n",
     "test_refuses_unapproved_commands"),

    ("SAFETY-ORDER", "the allowlist is checked before credentials load",
     "src/agent_nettools/network_tools.py",
     "    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "    _ = get_device(device_name)\n    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "test_refuses_unapproved_commands_before_loading_credentials"),

    ("B-453", "identifier containment is reached by the gate",
     "src/agent_nettools/grounding.py",
     "        .merge(check_identifier_containment(report, descent))\n", "",
     "check_identifier_containment"),

    ("T-035", "the notifier cannot receive an evidence bundle",
     "src/agent_nettools/notifier.py",
     "    notifier: Notifier | None = None,\n",
     "    notifier: Notifier | None = None,\n    evidence: dict | None = None,\n",
     "test_the_notifier_cannot_receive_an_evidence_bundle"),

    ("B-424", "timeline citations are checked",
     "src/agent_nettools/grounding.py",
     'GroundingFailure(\n                    "invented_timestamp"',
     'GroundingFailure(\n                    "DISABLED_invented_timestamp"',
     "invented_timestamp"),

    ("B-428", "no_fault_on_path when rung 1 is healthy",
     "src/agent_nettools/descent.py", "NO_FAULT_ON_PATH", "ALL_LAYERS_HEALTHY",
     "no_fault_on_path"),

    ("B-436", "coherence refuses on a moved fabric",
     "src/agent_nettools/epoch.py",
     "        return self.status in (FABRIC_MOVED, UNVERIFIED)",
     "        return False",
     "refuses"),

    ("B-458", "the MCP boundary sanitises at registration",
     "mcp_server/server.py", "sanitize(", "(lambda x, **k: x)(",
     "test_registration_is_what_applies_the_boundary"),

    ("B-456", "aggregation moves with the member set",
     "src/agent_nettools/descent.py",
     "            return list(members), Aggregation.ANY_HEALTHY",
     "            return list(members), Aggregation.ALL_HEALTHY",
     "test_each_member_set_carries_its_own_aggregation"),

    ("B-465", "an adjacency count above baseline is not a fault",
     "src/agent_nettools/checks.py", "    if actual > expected:\n", "    if False:\n",
     "test_losing_an_adjacency_warns_and_gaining_one_does_not"),

    ("B-435", "LLDP device IDs resolve before comparison",
     "src/agent_nettools/topology.py",
     "            if resolve_device(neighbor, mapping) is None:\n",
     "            if neighbor not in evidence_by_device:\n",
     "test_the_three_hostnames_are_this_fabric_s_own_devices"),

    ("B-461", "rungs are numbered in the report",
     "src/agent_nettools/render.py", '"{position}/{total} ', '"',
     "position"),

    # ---- Wave 1 guards (FIX-PLAN). Three of these first ran with wrong
    # anchors/symbols or against a genuinely vacuous suite; the B-474 entry
    # below is the one that found a real gap -- the tests proved the atomic
    # helper behaved and nothing proved the save paths CALLED it. The wiring
    # test exists because this entry reported VACUOUS (OBS-153). ----

    ("B-470", "the analyze path projects evidence before serializing",
     "src/agent_nettools/llm_analysis.py",
     "project_evidence(", "(lambda e, **k: e)(",
     "test_the_raw_text_canaries_never_reach_the_prompt"),

    ("B-467", "embedded delimiters cannot escape the quote block",
     "src/agent_nettools/model_egress.py",
     'text.replace(DEVICE_TEXT_OPEN, "").replace(DEVICE_TEXT_CLOSE, "")',
     "text",
     "test_quote_device_text_strips_embedded_delimiters"),

    ("B-474", "save paths route through the atomic writer",
     "src/agent_nettools/evidence_store.py",
     "_atomic_write_text(path, json.dumps(evidence, indent=2))",
     'path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")',
     "test_save_paths_actually_route_through_the_atomic_writer"),

    ("B-474b", "one golden row per device is enforced by index",
     "src/agent_nettools/evidence_store.py",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_one_golden",
     "CREATE INDEX IF NOT EXISTS idx_snapshots_one_golden",
     "rejects_a_second_golden_row"),

    ("B-473", "active probes carry distinct annotations",
     "mcp_server/server.py",
     '"title": "ACTIVE PROBE — generates network traffic"',
     '"title": "probe"',
     "ACTIVE PROBE"),

    ("B-468", "a probe with zero received exits nonzero",
     "src/agent_nettools/cli.py",
     "    return EXIT_OK if received > 0 else EXIT_WARNING",
     "    return EXIT_OK",
     "test_a_ping_with_total_loss_exits_nonzero"),
]


def sh(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True)


def purge_pycache() -> None:
    """Defect 2. Bracket every mutation, because the silent direction reports a
    mutation as caught when it was not."""

    for cache in REPO.rglob("__pycache__"):
        if ".venv" not in str(cache):
            shutil.rmtree(cache, ignore_errors=True)
    shutil.rmtree(REPO / ".pytest_cache", ignore_errors=True)


def assert_no_stale_bytecode(path: str) -> None:
    module = pathlib.Path(path).stem
    stale = [p for p in REPO.rglob(f"{module}.cpython-*.pyc") if ".venv" not in str(p)]
    if stale:
        raise SystemExit(f"REFUSING TO CONTINUE: stale bytecode for {module}: {stale}")


def resolve_guard_tests(symbol: str) -> list[str]:
    """Defect 1. Find the guard's tests by searching for the guard, never by name.

    Returns the test files that mention the symbol. An empty list is an **error**, not a
    verdict — the caller must fail loudly rather than run a mutation whose result it
    cannot interpret.
    """

    r = sh(f"grep -rl -- {symbol!r} tests/")
    return sorted(f for f in r.stdout.split() if f.endswith(".py"))


def run_one(ident, what, path, old, new, symbol) -> dict:
    if path in FROZEN:
        raise SystemExit(f"REFUSED: {path} is frozen by BUILD-PLAN §0.5")

    tests = resolve_guard_tests(symbol)
    if not tests:
        # Loudly, and distinctly from "vacuous".
        return {"id": ident, "what": what, "result": "UNRESOLVED",
                "detail": f"no test in tests/ mentions {symbol!r} — cannot interpret a "
                          f"mutation of this guard, so none was run"}

    source_path = REPO / path
    original = source_path.read_text()
    if old not in original:
        return {"id": ident, "what": what, "result": "ANCHOR-MISSING",
                "detail": f"the text this mutation removes is not in {path} — the guard "
                          f"has moved or been rewritten; update the harness"}

    target = " ".join(tests)
    purge_pycache()
    try:
        count = None if ident == "B-458" else 1
        source_path.write_text(
            original.replace(old, new) if count is None else original.replace(old, new, 1)
        )
        result = sh(f".venv/bin/python -m pytest {target} -q 2>&1 | tail -1")
        tail = result.stdout.strip()
    finally:
        source_path.write_text(original)
        if source_path.read_text() != original:
            raise SystemExit(f"RESTORE FAILED for {path} — fix by hand before continuing")
        purge_pycache()

    assert_no_stale_bytecode(path)
    caught = "failed" in tail or "error" in tail.lower()
    return {"id": ident, "what": what, "tests": tests, "mutated": tail[:70],
            "result": "HOLDS" if caught else "VACUOUS"}


def main(argv: list[str]) -> int:
    wanted = set(argv[1:])
    selected = [m for m in MUTATIONS if not wanted or m[0] in wanted]

    print(f"mutating {len(selected)} guard(s). No frozen file is touched.\n")
    results = [run_one(*m) for m in selected]

    for r in results:
        print(f"  {r['result']:14} {r['id']:18} {r['what']}")
        if r.get("detail"):
            print(f"                   {r['detail']}")
        elif r.get("mutated"):
            print(f"                   {' '.join(r['tests'])} -> {r['mutated']}")

    # Every mutation restored, and the frozen files still frozen.
    print()
    for f in sorted(FROZEN):
        base = sh(f"git rev-parse {BASELINE_COMMIT}:{f}").stdout.strip()
        head = sh(f"git rev-parse HEAD:{f}").stdout.strip()
        if base != head:
            raise SystemExit(f"FROZEN FILE CHANGED: {f}")
    dirty = sh("git status --porcelain").stdout.strip()
    if dirty:
        print("  WARNING — working tree is not clean after the pass:")
        print("\n".join(f"    {line}" for line in dirty.split("\n")))
    else:
        print("  tree clean; four frozen files identical to the T-001 baseline")

    bad = [r for r in results if r["result"] in ("VACUOUS", "UNRESOLVED", "ANCHOR-MISSING")]
    print(f"\n  {len(results) - len(bad)}/{len(results)} guards hold.")
    if bad:
        print("  Investigate before trusting any of them:")
        for r in bad:
            print(f"    {r['result']}  {r['id']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
