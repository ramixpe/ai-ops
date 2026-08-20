"""Measured values for d1.py .. d7.py — every number computed from the tree.

Before this module existed, `docs/diagrams/README.md` and `CLAUDE.md` both
claimed these SVGs were "Generated from the tree; regenerate, never
hand-edit" while every number in d1..d7 was a typed-once string literal.
Re-running the generators produced byte-identical output regardless of what
had changed in the repo — the promise was false. This module is the fix:
every function below re-measures its answer from the tree (source files,
`docs/build/*.md`, git, or by actually importing and running project code),
so a regenerate after any change reflects that change.

Two kinds of number are deliberately NOT here, and stay as literals inside
d1..d7 (each with a comment explaining why):

  - Worked-example narrative content that is illustrative rather than a
    running total — e.g. d2.py's command-bar wall-clock time, or the exact
    prose quoted inside d3.py's "what a model sees" code sample. Measuring
    these would either be nondeterministic (wall time varies run to run in a
    way even caching cannot fully smooth out for a *first* run) or would
    require reproducing internals (e.g. `model_egress`'s exact withholding
    text for one hand-picked field) far out of proportion to what a reader
    gets from the number being "real" rather than "representative".
  - Design claims and decisions ("Option C, decided 2026-08-18", backlog IDs
    named as evidence) — these are records of what was decided, not
    properties of the current tree, and re-measuring them makes no sense.

Two things make the *expensive* measurements (a full `pytest -q` run, an
actual `nettools investigate --from-fixtures` invocation) practical to keep:

  1. Every generator is a separate `python3 dN.py` process (per
     docs/diagrams/README.md), so an in-process ``functools.lru_cache``
     alone would not share work across d1..d7. A small on-disk cache
     (`.facts_cache.json`, gitignored) keyed by a signature of every `.py`
     file under `src/`, `mcp_server/`, `tests/` and `scripts/` (plus the
     fixtures and inventory the tests and the walkthrough read) makes a
     second generator in the same tree state reuse the first one's result
     instead of re-running pytest.
  2. That same cache is what keeps "regenerate twice, diff nothing" true
     even for the one genuinely time-varying measurement (wall-clock
     duration): the first run measures it, the second run in an unchanged
     tree reads the same cached number back rather than re-measuring and
     drifting by a few hundred milliseconds.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SRC = REPO_ROOT / "src" / "agent_nettools"
MCP = REPO_ROOT / "mcp_server"
TESTS_DIR = REPO_ROOT / "tests"
SCRIPTS = REPO_ROOT / "scripts"
DOCS_BUILD = REPO_ROOT / "docs" / "build"
CACHE_FILE = HERE / ".facts_cache.json"

for _p in (str(REPO_ROOT), str(REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def fmt(n: int) -> str:
    """Thousands-separated, matching every hand-typed number these replace."""
    return f"{n:,}"


def assert_file_exists(relpath: str, context: str) -> None:
    """Fail loudly if a diagram's code pointer has drifted off the tree.

    Several diagrams cite a specific module or function as part of their own
    prose (e.g. d3.py's five egress paths). That prose is hand-written and
    not otherwise re-derived from the tree, so this is the cheap half-measure:
    not re-deriving the claim, but refusing to render a diagram that quietly
    points at a file which no longer exists.
    """
    if not (REPO_ROOT / relpath).is_file():
        raise SystemExit(f"{context}: {relpath!r} no longer exists — update the diagram, not just this check.")


def assert_contains(relpath: str, pattern: str, context: str) -> None:
    text = (REPO_ROOT / relpath).read_text()
    if not re.search(pattern, text):
        raise SystemExit(
            f"{context}: {pattern!r} no longer found in {relpath!r} — update the diagram, not just this check."
        )


# --------------------------------------------------------------------------
# line / file counts — wc -l semantics (a count of newline characters), the
# method docs/diagrams/README.md already claimed was in use.
# --------------------------------------------------------------------------


def _wc_l(path: Path) -> int:
    return path.read_text().count("\n")


@functools.lru_cache(maxsize=None)
def module_lines(relpath: str) -> int:
    """Line count of one file, given a path relative to the repo root."""
    return _wc_l(REPO_ROOT / relpath)


@functools.lru_cache(maxsize=None)
def dir_lines(relpath: str) -> int:
    """Summed line count of every ``*.py`` file under one repo-relative dir."""
    total = 0
    for p in sorted((REPO_ROOT / relpath).rglob("*.py")):
        total += _wc_l(p)
    return total


def src_lines() -> int:
    return dir_lines("src/agent_nettools")


def mcp_lines() -> int:
    return dir_lines("mcp_server")


def tests_lines() -> int:
    return dir_lines("tests")


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


@functools.lru_cache(maxsize=1)
#: ---------------------------------------------------------------------------
#: VOLATILE facts: true of this *history* or of one test RUN, not of the tree's
#: content. A byte-pinned diagram (tests/test_diagrams.py) must not display
#: these, because committing the regenerated diagram changes them -- the commit
#: count increments *because* you committed the diagram that shows it, so the
#: pin can never be satisfied and every commit lands red. Measured the hard way
#: on 2026-08-19: two red pushes in a row, 287 -> 289 commits (OBS-189).
#:
#: `tests_passed()` is volatile for a subtler reason: it is the outcome of a
#: run, so it drops whenever anything is failing -- including the diagram test
#: itself, which then reports a number that disagrees with the diagram that
#: caused the disagreement. `tests_collected()` is the content fact -- how many
#: tests EXIST -- and is stable under pass/fail, so that is what the diagrams
#: show.
#:
#: These stay exported: they are honest measurements and useful to a human
#: running facts.py directly. They simply may not be baked into a pinned SVG.
#: ---------------------------------------------------------------------------
def commit_count() -> int:
    return int(_git("rev-list", "--count", "HEAD"))


@functools.lru_cache(maxsize=1)
def last_commit_date() -> str:
    return _git("log", "-1", "--format=%cd", "--date=short")


@functools.lru_cache(maxsize=1)
def current_branch() -> str:
    """The branch a human would call this tree — not a worktree artifact.

    Isolated agent sessions (as used by this project's own build harness)
    check out each session onto a synthetic per-worktree branch
    (``worktree-agent-<id>``) that shares history with, but is not, the
    branch a human reading the diagram means. ``git worktree list
    --porcelain`` lists the *main* worktree first regardless of how many
    linked ones exist, so when the branch matches that synthetic pattern,
    this resolves to the main worktree's branch instead — still measured,
    just measuring the name a reader would recognise rather than a detail of
    how this particular regeneration was invoked.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return "detached HEAD"
    if re.fullmatch(r"worktree-agent-[0-9a-f]+", branch):
        try:
            porcelain = _git("worktree", "list", "--porcelain")
            first_branch_line = next(
                line for line in porcelain.splitlines() if line.startswith("branch ")
            )
            main_branch = first_branch_line.split("refs/heads/", 1)[-1]
            if main_branch:
                return main_branch
        except (subprocess.CalledProcessError, StopIteration):
            pass
    return branch


def header_tag() -> str:
    """The version string -- a CONTENT fact, safe in a byte-pinned SVG.

    The previous form, `last_commit_date() · current_branch()`, baked two
    VOLATILE facts (see the block comment above `commit_count`) into every
    pinned diagram: a day rollover, a branch rename, or tagging a release
    changed bytes the pin had frozen, so the very act of releasing turned CI
    red (R1, release-1.0 campaign). The version is read from
    pyproject.toml's bytes, so an unchanged tree regenerates identically on
    any day, branch, or detached-HEAD checkout.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise UnmeasuredError("pyproject.toml has no version line")
    return f"v{m.group(1)}"


# --------------------------------------------------------------------------
# on-disk cache for the expensive measurements (a pytest collection, a real
# `nettools investigate --from-fixtures` invocation) — keyed by a signature
# of everything those measurements depend on, so a change anywhere in the
# code or fixtures they exercise invalidates it, and an unchanged tree reuses
# the prior result instead of re-running a 30-second suite seven times.
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _tree_signature() -> str:
    h = hashlib.sha256()
    py_roots = (SRC, MCP, TESTS_DIR, SCRIPTS)
    for root in py_roots:
        for p in sorted(root.rglob("*.py")):
            h.update(str(p.relative_to(REPO_ROOT)).encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    extra_files = (REPO_ROOT / "inventory" / "lab.yaml", REPO_ROOT / "pyproject.toml")
    for f in extra_files:
        if f.is_file():
            h.update(str(f.relative_to(REPO_ROOT)).encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    fixtures_dir = TESTS_DIR / "fixtures"
    if fixtures_dir.is_dir():
        for p in sorted(fixtures_dir.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(REPO_ROOT)).encode())
                h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _cache_load() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _cache_save(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass  # best-effort speedup; a read-only tree just runs uncached


def _cached(key: str, compute):
    cache = _cache_load()
    sig = _tree_signature()
    entry = cache.get(key)
    if entry and entry.get("signature") == sig:
        return entry["result"]
    result = compute()
    cache[key] = {"signature": sig, "result": result}
    _cache_save(cache)
    return result


# --------------------------------------------------------------------------
# pytest — collection is fast (~3s) and answers "how many tests exist"; a
# full run is slow (~30s) and answers "how many pass", a different claim.
# The diagrams have always claimed "tests passing", so that is the number
# measured for them — but the two are not interchangeable, which is why both
# are exposed here under names that say which one they are.
# --------------------------------------------------------------------------

_ENV_OVERRIDES = {
    # investigate/pytest runs triggered by fact-gathering must not pollute the
    # repo with tickets or a diagnosis-ledger file. NETTOOLS_TICKET_DIR gets a
    # throwaway directory per invocation below; the ledger env var is simply
    # unset, which ledger.py/cli.py already treat as "record in memory, write
    # nothing" (see cli.py's `_diagnosis_ledger_path`).
}


#: Set in the environment of the pytest subprocess this module spawns, so
#: `tests/test_diagrams.py`'s own regeneration test can tell "I was collected
#: by facts.py measuring the suite" from "I was collected by the real run a
#: human or CI started" and skip in the former case. Without this, that test
#: would try to regenerate a diagram, which calls back into `_run_pytest`,
#: which spawns a pytest that collects that same test again — unbounded
#: recursion, not just slowness.
_SUBPROCESS_GUARD_ENV = "NETTOOLS_DIAGRAM_FACTS_SUBPROCESS"


def _run_pytest(*extra_args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("NETTOOLS_DIAGNOSIS_LEDGER_FILE", None)
    env[_SUBPROCESS_GUARD_ENV] = "1"
    with tempfile.TemporaryDirectory() as td:
        env["NETTOOLS_TICKET_DIR"] = td
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *extra_args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )


class UnmeasuredError(RuntimeError):
    """A fact could not be measured.

    Raised instead of returning a placeholder. These diagrams exist to state
    measured facts, and a measurement that failed is not a measurement of zero
    -- the same distinction `metrics_prometheus.py` draws for an absent sample
    (OBS-188). A `.get(key, 0)` here renders "0 tests" in a green KPI tile,
    which is a confident false claim; failing to generate the diagram at all is
    the honest outcome, and the one a human notices.
    """


def _compute_pytest_collect() -> dict:
    proc = _run_pytest("--collect-only")
    m = re.search(r"(\d+)\s+tests? collected", proc.stdout)
    if not m:
        raise UnmeasuredError(
            "pytest --collect-only produced no 'N tests collected' line "
            f"(returncode={proc.returncode})"
        )
    return {"collected": int(m.group(1))}


@functools.lru_cache(maxsize=1)
def pytest_collect() -> dict:
    return _cached("pytest_collect", _compute_pytest_collect)


def tests_collected() -> int:
    return pytest_collect()["collected"]


# --------------------------------------------------------------------------
# scripts/mutate_guards.py — parsed, never run (it is slow and it mutates
# source on disk mid-run, restoring it after; a diagram generator must not
# do that as a side effect of being regenerated).
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _mutate_guards_module():
    spec = importlib.util.spec_from_file_location(
        "_diagrams_facts_mutate_guards", SCRIPTS / "mutate_guards.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # module scope only: MUTATIONS/FROZEN are
    # plain constants; main() is behind `if __name__ == "__main__":` in the
    # source, so importing this way never runs a mutation pass.
    return mod


def guard_count() -> int:
    return len(_mutate_guards_module().MUTATIONS)


def frozen_file_count() -> int:
    return len(_mutate_guards_module().FROZEN)


# --------------------------------------------------------------------------
# docs/build/BACKLOG.md
# --------------------------------------------------------------------------

_BACKLOG_ROW_RE = re.compile(
    r"^\|\s*\*\*(?P<id>B-\d+[a-zA-Z]?)\*\*\s*\|\s*"
    r"(?P<status>`[A-Z0-9-]+`(?:\s*\([^)]*\))?)\s*\|",
    re.MULTILINE,
)


@functools.lru_cache(maxsize=1)
def backlog_rows() -> tuple[tuple[str, str, str | None], ...]:
    """Every ``| **B-NNN** | `STATUS` |`` row: (id, base_status, qualifier).

    B-475's row reads `` `DONE` (scoped) `` — a qualified status. The
    qualifier (``"scoped"``) is captured separately rather than silently
    dropped, so ``B-475`` still counts once under ``DONE`` and a reader who
    cares can see it was not an unqualified one.
    """
    text = (DOCS_BUILD / "BACKLOG.md").read_text()
    rows = []
    for m in _BACKLOG_ROW_RE.finditer(text):
        base = re.match(r"`([A-Z0-9-]+)`", m.group("status")).group(1)
        qm = re.search(r"\(([^)]*)\)", m.group("status"))
        rows.append((m.group("id"), base, qm.group(1) if qm else None))
    return tuple(rows)


def backlog_total() -> int:
    return len(backlog_rows())


def backlog_status_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for _id, base, _q in backlog_rows():
        counts[base] = counts.get(base, 0) + 1
    return counts


def backlog_buckets() -> list[tuple[str, int]]:
    """The 4 originally-drawn states, plus one bucket for everything else.

    `docs/build/BACKLOG.md`'s own "State vocabulary" section documents 5
    states (DONE / OPEN / DEFERRED / BLOCKED / OUT-OF-SCOPE); in practice two
    more appear once each (CLOSED-AS-REFUSED, CLOSED-AS-MEASURED — see
    B-108, B-414). Folding all three of those into one "OTHER" bucket keeps
    the four-way bar this diagram already draws instead of redesigning it,
    while still accounting for every row.
    """
    counts = backlog_status_counts()
    primary = ["DONE", "DEFERRED", "OPEN", "BLOCKED"]
    buckets = [(name, counts.get(name, 0)) for name in primary]
    other = sum(n for status, n in counts.items() if status not in primary)
    if other:
        buckets.append(("OTHER", other))
    return buckets


# --------------------------------------------------------------------------
# docs/build/FINDINGS.md
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def findings_count() -> int:
    text = (DOCS_BUILD / "FINDINGS.md").read_text()
    return len(re.findall(r"^## OBS-\d+", text, re.MULTILINE))


# --------------------------------------------------------------------------
# tests/fixtures — the committed offline lab
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def fixture_stats() -> dict:
    fixtures_dir = TESTS_DIR / "fixtures"
    devices: set[str] = set()
    labels: set[str] = set()
    n = 0
    for p in fixtures_dir.rglob("*.txt"):
        n += 1
        parts = p.relative_to(fixtures_dir).parts
        # <platform>/<device>/<label>/<command-slug>.txt
        if len(parts) >= 4:
            devices.add(parts[1])
            labels.add(parts[2])
    return {"captures": n, "devices": len(devices), "labels": len(labels)}


# --------------------------------------------------------------------------
# agent_nettools.platforms — intents, approved commands
# --------------------------------------------------------------------------


def intent_order() -> tuple[str, ...]:
    from agent_nettools.platforms import INTENT_ORDER

    return INTENT_ORDER


def cisco_xr_intents() -> tuple[str, ...]:
    from agent_nettools.platforms import PLATFORM_INTENTS

    return tuple(PLATFORM_INTENTS["cisco_xr"].keys())


def approved_command_counts() -> dict[str, int]:
    from agent_nettools.platforms import APPROVED_COMMANDS

    return {platform: len(commands) for platform, commands in APPROVED_COMMANDS.items()}


# --------------------------------------------------------------------------
# agent_nettools.templates — the verb allowlist, the parameterised templates
# --------------------------------------------------------------------------


def verb_allowlist_display() -> str:
    from agent_nettools.templates import VERB_ALLOWLIST

    return "{" + ", ".join(sorted(VERB_ALLOWLIST)) + "}"


def cisco_xr_templates() -> dict:
    from agent_nettools.templates import PLATFORM_TEMPLATES

    names = tuple(PLATFORM_TEMPLATES["cisco_xr"].keys())
    probes = tuple(n for n in names if n in ("ping", "traceroute"))
    lookups = tuple(n for n in names if n not in ("ping", "traceroute"))
    return {"lookups": lookups, "probes": probes}


# --------------------------------------------------------------------------
# agent_nettools.flows — declared object types, implemented flows, the
# device_health refusal (distinct from an unbuilt stub — OBS-186).
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def flows_summary() -> dict:
    from agent_nettools import flows as flows_mod

    declared = flows_mod.OBJECT_TYPES
    implemented = tuple(flows_mod.FLOWS.keys())
    refused: list[str] = []
    unbuilt: list[str] = []
    for obj in declared:
        if obj in flows_mod.FLOWS:
            continue
        try:
            flows_mod.flow_for(obj)
        except NotImplementedError as exc:
            # `flow_for` special-cases exactly one refusal today, and raises
            # `_DEVICE_HEALTH_REFUSAL` verbatim for it — comparing against
            # that exact message (rather than sniffing for the word
            # "refused", which the generic not-yet-built message also
            # mentions in passing) is what correctly separates B-108's
            # refusal from B-110/B-111's plain "not built yet".
            if str(exc) == flows_mod._DEVICE_HEALTH_REFUSAL:
                refused.append(obj)
            else:
                unbuilt.append(obj)
        except KeyError:
            unbuilt.append(obj)
    return {
        "declared": declared,
        "implemented": implemented,
        "refused": tuple(refused),
        "unbuilt": tuple(unbuilt),
    }


def flow_rung_counts() -> dict[str, int]:
    from agent_nettools.flows import FLOWS

    return {name: len(flow.descent) for name, flow in FLOWS.items()}


def bgp_session_ladder() -> list[dict]:
    from agent_nettools.flows import FLOWS

    out = []
    for r in FLOWS["bgp_session"].descent:
        out.append(
            {
                "name": r.name,
                "scope": r.device_scope.name,
                "collects": " + ".join(c.name for c in r.collect),
            }
        )
    return out


# --------------------------------------------------------------------------
# agent_nettools.audit / settings — rule and setting counts
# --------------------------------------------------------------------------


def audit_rule_count() -> int:
    from agent_nettools.audit import AUDIT_RULES

    return len(AUDIT_RULES)


def settings_count() -> int:
    from agent_nettools.settings import SETTINGS

    return len(SETTINGS)


# --------------------------------------------------------------------------
# cli.py — the real argparse tree, not a re-typed list of its subcommands
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def cli_subcommands() -> tuple[str, ...]:
    cli = importlib.import_module("agent_nettools.cli")
    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions if a.choices)
    return tuple(action.choices.keys())


# --------------------------------------------------------------------------
# mcp_server — both surfaces
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def classic_mcp_tools() -> tuple[str, ...]:
    server = importlib.import_module("mcp_server.server")
    tools = server.mcp._tool_manager.list_tools()
    return tuple(t.name for t in tools)


@functools.lru_cache(maxsize=1)
def classic_mcp_probe_tools() -> tuple[str, ...]:
    """Classic-surface tool names registered via ``_active_probe_tool()``.

    Detected the same way an MCP client would see it: FastMCP's own
    ``annotations.title`` is set to "ACTIVE PROBE — generates network
    traffic" only for tools that decorator wraps (see server.py's
    ``_active_probe_tool``), so this is a real signal rather than a
    name-shaped guess.
    """
    server = importlib.import_module("mcp_server.server")
    tools = server.mcp._tool_manager.list_tools()
    return tuple(
        t.name for t in tools if t.annotations and t.annotations.title and "ACTIVE PROBE" in t.annotations.title
    )


@functools.lru_cache(maxsize=1)
def staged_mcp_tools() -> tuple[str, ...]:
    """The staged surface's tool names, in source order.

    Parsed from the AST rather than imported-and-applied: `staged_surface
    .apply()` destructively clears the live server's tool manager (by
    design, so the two surfaces cannot both be registered at once), which
    is not something a diagram generator should trigger as a side effect.
    """
    tree = ast.parse((MCP / "staged_surface.py").read_text())
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and node.name != "apply"
    )


# --------------------------------------------------------------------------
# inventory/lab.yaml — the fabric's own device list
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def lab_devices() -> tuple[tuple[str, str], ...]:
    """(device name, role) for every device in the inventory, in file order."""
    inventory_model = importlib.import_module("agent_nettools.inventory_model")
    inv = inventory_model.load_inventory_file()
    return tuple((d.name, d.role) for d in inv.devices)


# --------------------------------------------------------------------------
# an actual `nettools investigate --from-fixtures` run — the walkthrough
# d2.py narrates. Run for real, sandboxed against writing a ticket or a
# ledger entry into the repo, and cached like the pytest runs above.
# --------------------------------------------------------------------------

WALKTHROUGH_DEVICE = "RR1"
WALKTHROUGH_SUBJECT = "10.255.0.12"
WALKTHROUGH_FLOW = "bgp_session"
#: `investigate`'s own `--label` default (cli.py) — not passed explicitly by
#: the walkthrough command d2.py shows, so this is what it actually replays.
WALKTHROUGH_LABEL = "broken"


def _compute_investigate_walkthrough() -> dict:
    env = dict(os.environ)
    env.pop("NETTOOLS_DIAGNOSIS_LEDGER_FILE", None)
    with tempfile.TemporaryDirectory() as td:
        env["NETTOOLS_TICKET_DIR"] = td
        start = time.monotonic()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_nettools.cli",
                "investigate",
                WALKTHROUGH_DEVICE,
                WALKTHROUGH_SUBJECT,
                "--from-fixtures",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        duration = time.monotonic() - start
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {}
    return {"exit_code": proc.returncode, "duration_s": round(duration, 2), "payload": payload}


@functools.lru_cache(maxsize=1)
def investigate_walkthrough() -> dict:
    return _cached("investigate_walkthrough", _compute_investigate_walkthrough)


def investigate_walkthrough_duration_bucket_s() -> float:
    """Wall-clock time for the walkthrough subprocess, bucketed to 0.5s.

    `tests_run_duration_s`'s docstring covers the general problem (a
    subprocess's wall time on a machine shared with concurrent work is not
    reproducible enough to print raw). This one measurement stayed usable
    where the full-suite one did not: unlike a 2500-test run, a single
    fixture-replay investigation is dominated by fixed process-startup cost
    rather than CPU contention, so eight measurements across several
    regenerations (0.25s-0.41s) all landed in the same half-second bucket.
    Rounding to one decimal instead of half a second was tried first and was
    not coarse enough — 0.26s and 0.28s once straddled that boundary.
    """
    return max(0.5, round(investigate_walkthrough()["duration_s"] / 0.5) * 0.5)


@functools.lru_cache(maxsize=1)
def raw_prose_chars_for_walkthrough() -> int:
    """Total raw command-output characters collected for the walkthrough.

    Sums every intent's raw ``data.commands`` output text for the two
    devices the walkthrough touches (the local device and the subject's
    device) under the fixture label the walkthrough replays — the same
    quantity `model_egress.py` withholds/quotes before anything reaches a
    model.
    """
    fixtures = importlib.import_module("agent_nettools.fixtures")
    fixtures_dir = str(TESTS_DIR / "fixtures")
    total = 0
    for device in (WALKTHROUGH_DEVICE, "PE2"):
        evidence = fixtures.load_fixture_evidence(
            device, label=WALKTHROUGH_LABEL, base_dir=fixtures_dir
        )
        for intent in intent_order():
            section = evidence.get(intent) or {}
            data = section.get("data") or {}
            commands = data.get("commands") or {}
            for output in commands.values():
                total += len(output)
    return total


# --------------------------------------------------------------------------
# mcp_server.server — the third registration class (B-512): tools that reach
# an external evidence store (Loki, Prometheus, NetBox, neo4j) instead of a
# lab device. Added for d8/d9, and used to correct d1/d3/d4/d7's now-stale
# "two kinds of tool" framing.
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def classic_mcp_external_source_tools() -> tuple[str, ...]:
    """Classic-surface tool names registered via ``_external_source_tool()``.

    Same detection method `classic_mcp_probe_tools()` already uses for the
    active-probe class: FastMCP's own ``annotations.title`` reads "EXTERNAL
    SOURCE — ..." only for tools that decorator wraps (server.py's
    ``_external_source_tool``), so this is a real signal, not a name guess.
    """
    server = importlib.import_module("mcp_server.server")
    tools = server.mcp._tool_manager.list_tools()
    return tuple(
        t.name
        for t in tools
        if t.annotations and t.annotations.title and "EXTERNAL SOURCE" in t.annotations.title
    )


def classic_mcp_tool_class_counts() -> dict[str, int]:
    """How the classic surface's tools split across all three registration
    classes. Mutually exclusive by construction — every tool is registered
    through exactly one of `_read_only_tool`/`_active_probe_tool`/
    `_external_source_tool` — so ``read_only`` is derived by subtraction
    rather than a fourth independent scan needing its own annotation check.
    """
    total = len(classic_mcp_tools())
    probe = len(classic_mcp_probe_tools())
    external = len(classic_mcp_external_source_tools())
    return {
        "total": total,
        "read_only": total - probe - external,
        "active_probe": probe,
        "external_source": external,
    }


# --------------------------------------------------------------------------
# Non-device evidence adapters — logs_loki.py / metrics_prometheus.py /
# netbox.py / graph.py. Each is a named-query allowlist over an external
# service, `templates.py`'s discipline carried to a query language with no
# allowlist of its own — so its length is exactly as measurable as
# `approved_command_counts()` already is for device commands.
# --------------------------------------------------------------------------


def evidence_source_query_counts() -> dict[str, int]:
    from agent_nettools import graph, logs_loki, metrics_prometheus, netbox

    return {
        "loki": len(logs_loki.LOKI_QUERIES),
        "prometheus": len(metrics_prometheus.PROMETHEUS_QUERIES),
        "netbox": len(netbox.NETBOX_READ_QUERIES),
        "neo4j": len(graph.NEO4J_READ_QUERIES),
    }


@functools.lru_cache(maxsize=1)
def prometheus_metric_count() -> int:
    """The metric-name count `metrics_prometheus.py`'s own module docstring
    records from its 2026-08-19 live re-verification against this lab's
    Prometheus.

    Parsed out of that docstring rather than queried live here, for the same
    reason `backlog_rows()` parses `BACKLOG.md` instead of re-deriving it: a
    diagram generator must run offline, and this is a recorded measurement —
    a property of the tree's text — not something this process can re-check
    without a reachable Prometheus.
    """
    text = (SRC / "metrics_prometheus.py").read_text()
    m = re.search(r"(\d[\d,]*) metric names", text)
    if not m:
        raise SystemExit(
            "facts.prometheus_metric_count: the metric-name count is no "
            "longer stated in metrics_prometheus.py's module docstring — "
            "update the parser or the source."
        )
    return int(m.group(1).replace(",", ""))


@functools.lru_cache(maxsize=1)
def neo4j_graph_counts() -> dict[str, int]:
    """Node/relationship counts `graph.py`'s own module comment records from
    the 2026-08-19 live write-and-verify against this lab's neo4j container.
    Parsed, not re-queried live — see `prometheus_metric_count()`.
    """
    text = (SRC / "graph.py").read_text()
    m = re.search(r"populated\s+(\d+)\s+nodes\s+and\s+(\d+)\s*\n?#?\s*relationships", text)
    if not m:
        raise SystemExit(
            "facts.neo4j_graph_counts: the node/relationship count is no "
            "longer stated in graph.py — update the parser or the source."
        )
    return {"nodes": int(m.group(1)), "relationships": int(m.group(2))}


@functools.lru_cache(maxsize=1)
def netbox_fabric_counts() -> dict[str, int]:
    """Device/interface/IP/cable counts `FINDINGS.md` records from the
    2026-08-19 live NetBox population (B-509).

    Parsed the same way `findings_count()` already reads that file.
    FINDINGS.md is append-only, so a past entry's own numbers are stable
    under a regeneration even though the file keeps growing — the same
    property that makes `findings_count()` itself safe to pin.
    """
    text = (DOCS_BUILD / "FINDINGS.md").read_text()
    m = re.search(
        r"NetBox now holds (\d+) devices, (\d+) interfaces, "
        r"(\d+) IP addresses and (\d+) cables",
        text,
    )
    if not m:
        raise SystemExit(
            "facts.netbox_fabric_counts: the NetBox population line is no "
            "longer stated in FINDINGS.md — update the parser or the source."
        )
    return {
        "devices": int(m.group(1)),
        "interfaces": int(m.group(2)),
        "ip_addresses": int(m.group(3)),
        "cables": int(m.group(4)),
    }


# --------------------------------------------------------------------------
# Structural "who actually imports this" checks — parsed from each file's
# AST import statements, not grepped for the bare module name, so a
# docstring or comment that merely *mentions* a module (as
# `session_memory.py`'s does for `config_diff`) is never mistaken for a real
# dependency. Used to state, and enforce, that `reasoning_gate.py` and
# `config_diff.py` are built and tested but not yet wired into a live
# investigation — a diagram claiming that fails loudly the day it stops
# being true instead of quietly going stale.
# --------------------------------------------------------------------------


def _module_importers(module_name: str, roots: tuple[Path, ...]) -> tuple[str, ...]:
    importers: list[str] = []
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            if p.stem == module_name:
                continue
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:
                continue
            found = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == module_name:
                    found = True
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[-1] == module_name:
                            found = True
            if found:
                importers.append(str(p.relative_to(REPO_ROOT)))
    return tuple(importers)


@functools.lru_cache(maxsize=1)
def reasoning_gate_consumers() -> tuple[str, ...]:
    """Every file under ``src/``/``mcp_server/`` (tests excluded — this
    scans only ``SRC``/``MCP``) that imports `reasoning_gate.py` — empty
    today. B-103, the narrowing pass that would call `parse_decision`
    against a live descent, has not been built; see that module's own
    docstring ("Wiring either into a live investigation is a separate,
    later task").
    """
    return _module_importers("reasoning_gate", (SRC, MCP))


@functools.lru_cache(maxsize=1)
def config_diff_consumers() -> tuple[str, ...]:
    """Every file under ``src/``/``mcp_server/`` that imports
    `config_diff.py` — empty today. See that module's own "Not a rung"
    section: the reconciliation is deliberately not called from
    `flows.py`/`checks.py`/`investigation.py`/`cli.py`/the MCP surface.

    `config_section.py` (the parsers `config_diff.py` reads) is a different
    claim and is *not* checked here — it is imported by `template_parsers.py`,
    and its two templates are real and callable (through `nettools agent`'s
    generic template tool only — neither has its own CLI subcommand or MCP
    tool, shown as such in 04-capabilities.svg); only the comparison in
    `config_diff.py` itself is unwired.
    """
    return _module_importers("config_diff", (SRC, MCP))


# --------------------------------------------------------------------------
# config_section.py / config_diff.py — the config axis (B-104) and the
# intent-vs-observed diff (B-106).
# --------------------------------------------------------------------------


def config_axis_summary() -> dict:
    from agent_nettools import config_diff, config_section

    return {
        "templates": tuple(name for _platform, name in config_section.CONFIG_TEMPLATE_PARSERS.keys()),
        "diff_functions": tuple(sorted(n for n in dir(config_diff) if n.startswith("diff_"))),
        "outcomes": tuple(sorted(config_diff.OUTCOMES)),
    }


# --------------------------------------------------------------------------
# reasoning_gate.py — the two shapes, code-enumerated candidate kinds
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def reasoning_gate_candidate_kinds() -> tuple[str, ...]:
    """The candidate ``kind`` literals `reasoning_gate.py` actually
    constructs — parsed from its own source rather than imported and called,
    since (unlike `flows.FLOWS`) there is no already-built registry object to
    introspect: each kind is a literal first argument to one of three
    private, per-kind helper functions.
    """
    text = (SRC / "reasoning_gate.py").read_text()
    kinds = tuple(re.findall(r'Candidate\("(\w+)"', text))
    if not kinds:
        raise SystemExit(
            "facts.reasoning_gate_candidate_kinds: no Candidate(\"kind\", ...) "
            "constructions found — reasoning_gate.py changed shape; update the parser."
        )
    return kinds


# --------------------------------------------------------------------------
# grounding.py / model_egress.py — the verification and projection tables
# --------------------------------------------------------------------------


def grounding_check_names() -> tuple[str, ...]:
    from agent_nettools import grounding

    return tuple(sorted(n for n in dir(grounding) if n.startswith("check_")))


def model_egress_table_sizes() -> dict[str, int]:
    from agent_nettools import model_egress

    return {
        "raw_text_keys": len(model_egress.RAW_TEXT_KEYS),
        "free_text_fields": len(model_egress.FREE_TEXT_FIELDS),
        "error_kinds": len(model_egress.ERROR_KINDS),
    }


# --------------------------------------------------------------------------
# event_routing.py / event_watch.py — deterministic event-to-flow routing
# --------------------------------------------------------------------------


def event_routing_table_sizes() -> dict[str, int]:
    from agent_nettools import event_routing

    return {
        "mnemonic": len(event_routing.MNEMONIC_FLOW_TABLE),
        "alertname": len(event_routing.ALERTNAME_FLOW_TABLE),
    }


# --------------------------------------------------------------------------
# admission.py — per-device / per-fabric concurrency gate defaults
# --------------------------------------------------------------------------


def admission_defaults() -> dict[str, int]:
    from agent_nettools import admission

    return {
        "per_device": admission.DEFAULT_MAX_CONCURRENT_PER_DEVICE,
        "fabric": admission.DEFAULT_MAX_CONCURRENT_FABRIC,
    }


# --------------------------------------------------------------------------
# incident_correlation.py — the three correlation bases, strongest first,
# and how many co-occurrence-shaped heuristics are refused outright
# --------------------------------------------------------------------------


def incident_correlation_summary() -> dict:
    from agent_nettools import incident_correlation as ic

    return {
        "bases": (ic.BASIS_SAME_CAUSE, ic.BASIS_SAME_SUBJECT, ic.BASIS_SAME_DEVICE),
        "excluded_count": len(ic.EXCLUDED_CORRELATIONS),
    }


# --------------------------------------------------------------------------
# docs/design/interfaces.md — the staged human-interaction ladder
# --------------------------------------------------------------------------

_STAGE_ROW_RE = re.compile(
    r"^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$",
    re.MULTILINE,
)


@functools.lru_cache(maxsize=1)
def interaction_ladder() -> tuple[tuple[str, str, str], ...]:
    """(stage, interface, direction) for every row of interfaces.md's own
    ladder table — e.g. ``("Stage 3", "Inline approve / reject on a
    proposal", "Bidirectional")``. Parsed like `backlog_rows()` parses
    BACKLOG.md: a markdown table this project already maintains by hand is a
    property of the tree, not something to re-type here.
    """
    text = (REPO_ROOT / "docs" / "design" / "interfaces.md").read_text()
    if "## The ladder" not in text or "## What already exists" not in text:
        raise SystemExit(
            "facts.interaction_ladder: interfaces.md's '## The ladder' "
            "section markers moved or were reworded — update the parser."
        )
    section = text.split("## The ladder", 1)[1].split("## What already exists", 1)[0]
    rows = tuple(
        (m.group(1).strip(), m.group(2).strip(), m.group(3).strip())
        for m in _STAGE_ROW_RE.finditer(section)
    )
    if not rows:
        raise SystemExit(
            "facts.interaction_ladder: no rows parsed from interfaces.md's "
            "ladder table — its format changed; update the parser."
        )
    return rows


# --------------------------------------------------------------------------
# docs/build/BACKLOG.md — the Stage 3 write-path table (B-301..B-307), every
# one of them DEFERRED until an identity provider exists. This is the
# measured evidence for the event-loop diagram's "where it stops" panel.
# Read-only, like `backlog_rows()`/`findings_count()` above — BACKLOG.md is
# owned by another track and is never written here.
# --------------------------------------------------------------------------

_WRITE_PATH_ITEM_RE = re.compile(
    r"^\|\s*\*\*(B-30[1-7])\*\*\s*\|\s*\*\*([^*]+)\*\*",
    re.MULTILINE,
)


@functools.lru_cache(maxsize=1)
def write_path_backlog() -> tuple[tuple[str, str], ...]:
    """(id, item) for B-301..B-307 under BACKLOG.md's "Stage 3 — standard
    procedures" heading — "The write path. Everything here is gated on
    identity, and identity does not exist yet."

    Fails loudly, rather than rendering a stale claim, in two distinct ways:
    if the seven rows cannot be found at all (the section moved or was
    reworded), and if any one of them is no longer `DEFERRED` in
    `backlog_rows()` (the write path started moving) — the second check is
    exactly the situation where this diagram's central honesty claim would
    need a human's re-review before it may regenerate silently.
    """
    text = (DOCS_BUILD / "BACKLOG.md").read_text()
    marker = "# Stage 3"
    idx = text.find(marker)
    if idx == -1:
        raise SystemExit(
            "facts.write_path_backlog: BACKLOG.md's 'Stage 3' section "
            "heading is missing — update the parser or the source."
        )
    section = text[idx : idx + 4000]
    rows = tuple(_WRITE_PATH_ITEM_RE.findall(section))
    expected_ids = tuple(f"B-30{n}" for n in range(1, 8))
    if tuple(r[0] for r in rows) != expected_ids:
        raise SystemExit(
            f"facts.write_path_backlog: expected rows {expected_ids}, parsed "
            f"{tuple(r[0] for r in rows)} — BACKLOG.md's Stage 3 table changed; "
            "update the diagram and this parser together."
        )
    statuses = {i: base for i, base, _q in backlog_rows()}
    not_deferred = [i for i in expected_ids if statuses.get(i) != "DEFERRED"]
    if not_deferred:
        raise SystemExit(
            f"facts.write_path_backlog: {not_deferred} are no longer DEFERRED "
            "in BACKLOG.md — the write path has moved. This diagram's "
            "'gated on identity, nothing built' framing needs a human review "
            "before it may regenerate silently over that change."
        )
    return rows
