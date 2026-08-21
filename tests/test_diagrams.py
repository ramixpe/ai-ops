"""Pin docs/diagrams/*.svg to the tree they claim to be generated from.

`docs/diagrams/README.md` and `CLAUDE.md` both say the seven SVGs under
`docs/diagrams/` are "Generated from the tree; regenerate, never hand-edit."
That promise is only true if something notices when a committed SVG no
longer matches what its own generator produces right now — otherwise a code
change ships without a diagram regeneration and the SVG quietly goes stale,
which is the defect `docs/diagrams/facts.py` exists to fix in the generators
and this file exists to fix in the safety net around them.

Two tiers:

- `test_diagram_matches_its_generator` (parametrized, one per SVG) actually
  regenerates each SVG via its `dN.py` script and compares it byte-for-byte
  against the committed file, restoring the committed bytes afterward
  either way — so the test has no side effect on the working tree. The
  first regeneration in a process is slow (~30s: one full `pytest -q` run,
  paid inside `facts.py`'s cache-miss path); the rest are fast, reading
  `facts.py`'s on-disk cache. `facts.py` guards against this test recursing
  into itself when it is itself the thing that spawned that inner run — see
  `facts._SUBPROCESS_GUARD_ENV`.
- `test_key_facts_appear_in_the_committed_svgs` is the fallback the task
  that added this file explicitly allowed for if full regeneration proved
  too slow: no regeneration, just confirms a handful of the most
  load-bearing measured numbers (guard count, backlog total, findings
  count, the intent and flow vocabularies) are present, in their
  currently-correct form, in the SVGs as committed. Cheap, and useful on
  its own if the first tier is ever skipped or fails for an unrelated
  reason (e.g. cairosvg/mcp not installed in some minimal environment).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS_DIR = REPO_ROOT / "docs" / "diagrams"

if str(DIAGRAMS_DIR) not in sys.path:
    sys.path.insert(0, str(DIAGRAMS_DIR))
import facts  # noqa: E402  (docs/diagrams/facts.py -- a local script, not an installed package)

DIAGRAMS = (
    ("d1.py", "01-repo-anatomy.svg"),
    ("d2.py", "02-call-path.svg"),
    ("d3.py", "03-trust-boundary.svg"),
    ("d4.py", "04-capabilities.svg"),
    ("d5.py", "05-current-state.svg"),
    ("d6.py", "06-descent.svg"),
    ("d7.py", "07-stage2-architecture.svg"),
    ("d8.py", "08-event-loop.svg"),
    ("d9.py", "09-model-boundary.svg"),
)


@pytest.mark.parametrize("script, svg", DIAGRAMS, ids=[svg for _script, svg in DIAGRAMS])
def test_diagram_matches_its_generator(script, svg):
    """Regenerating `svg` from `script` must reproduce it byte-for-byte.

    A failure here means someone changed something the diagram claims to
    measure (a flow, an intent, a guard, a line count, the backlog, ...)
    without regenerating the diagram to match — run
    `cd docs/diagrams && python3 {script}` and commit the result.
    """

    if os.environ.get(facts._SUBPROCESS_GUARD_ENV):
        pytest.skip(
            "running inside facts.py's own pytest subprocess (measuring the "
            "suite for another diagram's KPI tile) -- regenerating here would "
            "recurse into that same subprocess call forever."
        )

    svg_path = DIAGRAMS_DIR / svg
    assert svg_path.is_file(), f"{svg} is missing -- run `cd docs/diagrams && python3 {script}`"
    committed = svg_path.read_bytes()

    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=DIAGRAMS_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, (
            f"{script} exited {proc.returncode} instead of regenerating {svg}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        regenerated = svg_path.read_bytes()
        assert regenerated == committed, (
            f"{svg} is stale: regenerating it from {script} produces different "
            f"bytes than what is committed. Run `cd docs/diagrams && "
            f"python3 {script}` and commit the result."
        )
    finally:
        svg_path.write_bytes(committed)


def test_key_facts_appear_in_the_committed_svgs():
    """A fast, no-regeneration sanity check on top of the byte-pin above.

    Cheap because every fact it checks is either parsed straight out of a
    docs/build/*.md file, parsed out of scripts/mutate_guards.py without
    running it, or a plain import of already-installed project code — none
    of it shells out to pytest or to `nettools investigate`.
    """

    def svg_text(name: str) -> str:
        return (DIAGRAMS_DIR / name).read_text(encoding="utf-8")

    all_svgs = "\n".join(svg_text(svg) for _script, svg in DIAGRAMS)

    guard_n = facts.guard_count()
    assert f"{guard_n}/{guard_n} verified" in all_svgs or f"{guard_n} / {guard_n}" in all_svgs

    backlog_n = facts.backlog_total()
    assert str(backlog_n) in svg_text("05-current-state.svg")

    findings_n = facts.findings_count()
    assert str(findings_n) in svg_text("05-current-state.svg")

    flows = facts.flows_summary()
    for name in flows["implemented"]:
        assert name in svg_text("06-descent.svg")
    for name in flows["refused"]:
        assert name in svg_text("06-descent.svg")
        # the refusal is shown as a distinct thing from "not yet built"
        # (OBS-186) -- not just present, but labelled as refused.
        assert "refused" in svg_text("06-descent.svg")
    for name in flows["unbuilt"]:
        assert name in svg_text("06-descent.svg")

    intents = facts.cisco_xr_intents()
    for intent in intents:
        assert intent in svg_text("04-capabilities.svg")

    classic_n = len(facts.classic_mcp_tools())
    staged_n = len(facts.staged_mcp_tools())
    assert f"{classic_n} MCP tools on the classic surface, {staged_n} on the staged one" in svg_text(
        "04-capabilities.svg"
    )

    cmd_counts = facts.approved_command_counts()
    assert f"cisco_xr {cmd_counts['cisco_xr']}" in svg_text("01-repo-anatomy.svg")

    tool_classes = facts.classic_mcp_tool_class_counts()
    assert f"one of {tool_classes['total']} registered MCP tools" in svg_text("09-model-boundary.svg")

    write_path = facts.write_path_backlog()
    for wid, _item in write_path:
        assert wid in svg_text("08-event-loop.svg")
        assert "DEFERRED" in svg_text("08-event-loop.svg")


def test_no_generator_bakes_a_volatile_fact():
    """OBS-697. `facts.VOLATILE_FACTS` names every value that is true of this
    history, this run, or this machine rather than of the tree's content. A
    byte-pinned SVG may not show one: committing the regenerated diagram
    changes the number the diagram shows, so the pin can never be satisfied.

    This existed only as a comment until it was broken. `d2.py` rendered
    `investigate_walkthrough_duration_bucket_s()` -- a wall-clock measurement
    bucketed to half a second -- which passed on the machine that wrote it,
    whose samples all landed in one bucket, and failed intermittently on CI
    runners slow enough to reach the next one. Two red pushes, and a finding
    filed as "unexplained" because regenerating at the same commit on the same
    machine could never reproduce it.

    Scanned with `ast` rather than `grep` so a name inside a docstring or a
    comment (this file's own prose names all four) is not a false positive.
    """

    import ast

    sys.path.insert(0, str(DIAGRAMS_DIR))
    import facts  # noqa: E402

    offenders = []
    for generator in sorted(DIAGRAMS_DIR.glob("d*.py")):
        tree = ast.parse(generator.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "facts":
                    name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in facts.VOLATILE_FACTS:
                offenders.append(f"{generator.name}:{node.lineno} calls {name}()")

    assert offenders == [], (
        "a byte-pinned diagram generator calls a volatile fact: "
        f"{offenders}. Its value depends on this history, this run, or this "
        "machine, so the committed SVG cannot stay in step with it. Show a "
        "content fact instead -- something derived from the tree or from a "
        "replayed payload, not from a clock or from git."
    )


def test_the_volatile_fact_list_names_only_real_functions():
    """Positive control for the guard above.

    A tuple naming a symbol that no longer exists would make the scan quietly
    match nothing and pass over an empty set. That is not hypothetical here:
    the prose this list replaced named `tests_passed`, which was removed when
    `tests_collected` superseded it, and the comment outlived the function by
    long enough for nobody to notice.
    """

    sys.path.insert(0, str(DIAGRAMS_DIR))
    import facts  # noqa: E402

    assert facts.VOLATILE_FACTS, "the list is empty, so the guard checks nothing"
    missing = [n for n in facts.VOLATILE_FACTS if not hasattr(facts, n)]
    assert missing == [], f"VOLATILE_FACTS names symbols facts.py does not define: {missing}"
