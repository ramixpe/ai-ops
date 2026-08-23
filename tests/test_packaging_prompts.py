"""EER-003 -- the packaged fallback copies of every runtime-required prompt.

`prompt_library.PROMPTS_DIR` (``src/agent_nettools/prompt_library.py``) is
three parents up from that file -- the repo root's ``prompts/`` directory --
so it does not exist once this package is installed from a built wheel with
no source checkout anywhere nearby, and ``load_prompt`` would raise
``PromptNotFoundError`` for every caller, including the deterministic
``investigate --no-model`` path (``build_report_prompt`` is reachable from a
plain descent report, with no LLM involved at all).

``src/agent_nettools/data/prompts/*.txt`` are checked-in byte-identical
copies, declared in ``pyproject.toml``'s ``[tool.setuptools.package-data]``
and resolved through ``importlib.resources`` so they ship inside the wheel.
Superseded prompt versions stay reachable on purpose (``load_prompt`` accepts
any version), so every version under ``prompts/`` is packaged, not just the
ones named in ``CURRENT_VERSION``.

``prompts/tests/cases/*.json`` and ``prompts/README.md`` are test-only and
are deliberately NOT packaged -- nothing at runtime reads them.

2026-08-23 -- derivation, not a hand list (docs/findings_gpt_23aug.md H1)
---------------------------------------------------------------------------
This module used to hardcode ``RUNTIME_REQUIRED_PROMPTS`` as a tuple of six
filenames -- ``report.v1``/``v2``, ``correlate.v1``..``v4``. That list went
stale the moment `troubleshooting`/`fabric_analysis`/`agent_system` moved
into `prompts/` (2026-08-21) without anyone updating it, and stayed stale
through `event_agent.v1.txt` landing with no packaged twin at all: the list
did not know a tenth prompt existed, so this file's own drift guard could not
see the gap it exists to catch.

``_runtime_required_prompt_filenames()`` below replaces the hand list by
scanning ``PROMPTS_DIR`` itself -- the exact directory ``load_prompt``
resolves the source-tree copy against -- for every ``*.txt`` file, the same
glob ``tests/test_prompts.py``'s ``_prompt_files()`` already uses (and that
file's own ``test_every_prompt_file_is_versioned`` pins every one of those
filenames to the ``name.vN.txt`` shape, so nothing here needs to re-validate
that). A **file-derived** list, not a call-site-derived one, deliberately:
``report``/``correlate`` ship every superseded version on purpose (see above),
and the only call sites for those two (`prompt_library.build_report_prompt`/
`build_correlate_prompt`) request a version through `CURRENT_VERSION` or a
caller-supplied int -- neither literally names `report.v1` anywhere once
`report.v2` becomes current, so scanning call sites would under-count exactly
the versions this module's job is to keep shippable. Scanning the tree itself
has no such blind spot: whatever exists under `prompts/` is, by this file's
own rule, required in the wheel.

Same two things this file must keep true as ``test_packaging_inventory.py``:

1. Every packaged copy is byte-identical to its source-tree original --
   nothing enforces that at packaging time, so a future prompt edit (or a new
   prompt file) that forgets its packaged twin must fail loudly here.
2. The fallback is actually reachable and loadable when the source tree is
   unavailable, and a real source-tree copy still wins when both exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_nettools import prompt_library
from agent_nettools.prompt_library import (
    PROMPTS_DIR,
    PromptNotFoundError,
    _packaged_prompts_dir,
    load_prompt,
)

#: ``name.vN.txt`` -- the one shape every file in ``PROMPTS_DIR`` is required
#: to have (`tests/test_prompts.py::test_every_prompt_file_is_versioned`).
_VERSIONED_FILENAME = re.compile(r"^(?P<name>[a-z][a-z0-9_]*)\.v(?P<version>\d+)\.txt$")


def _runtime_required_prompt_filenames() -> tuple[str, ...]:
    """Every prompt filename actually present in the source tree, sorted.

    Deliberately a scan of ``PROMPTS_DIR``, not a maintained list -- see the
    module docstring's 2026-08-23 section for why a hand list is exactly what
    let ``event_agent.v1.txt`` ship unpackaged, and why call-site scanning
    would not have caught it either. Adding a prompt file without packaging
    it now fails ``test_every_runtime_required_prompt_is_packaged_and_byte_identical``
    below instead of silently shipping a wheel that cannot load it.
    """

    return tuple(sorted(p.name for p in PROMPTS_DIR.glob("*.txt")))


def _runtime_required_name_version_pairs() -> tuple[tuple[str, int], ...]:
    """``(name, version)`` for every filename `_runtime_required_prompt_filenames`
    finds -- what `test_load_prompt_falls_back_to_the_packaged_copy_when_the_source_tree_is_gone`
    below actually calls `load_prompt` with."""

    pairs = []
    for filename in _runtime_required_prompt_filenames():
        match = _VERSIONED_FILENAME.match(filename)
        assert match, f"{filename} does not match name.vN.txt -- can't derive a load_prompt() call"
        pairs.append((match.group("name"), int(match.group("version"))))
    return tuple(pairs)


@pytest.fixture(autouse=True)
def _clear_prompt_cache():
    """``load_prompt`` is ``@lru_cache``'d by ``(name, version)`` alone -- a
    call in an earlier test (against the real ``PROMPTS_DIR``) would silently
    satisfy a later call here that monkeypatches ``PROMPTS_DIR`` away to
    exercise the packaged fallback, without ever touching the fallback path
    at all. Clear around every test in this module so each one observes the
    resolution it actually set up."""

    load_prompt.cache_clear()
    yield
    load_prompt.cache_clear()


def test_the_derivation_is_not_vacuous():
    """§0.12 guardrail: a scan that finds nothing would make every test below
    pass by measuring nothing. Pinned to the two things known true right now
    -- more than the old hardcoded six, and `event_agent.v1.txt` specifically
    named, so a regression that silently drops it back out of the scan (or
    out of `PROMPTS_DIR`) fails here even if every other assertion in this
    file is somehow satisfied."""

    found = _runtime_required_prompt_filenames()
    assert len(found) >= 7, "fewer prompt files than before H1 -- the scan found less, not more"
    assert "event_agent.v1.txt" in found


def test_every_runtime_required_prompt_is_packaged_and_byte_identical():
    """Drift guard: if this ever fails, a prompt was added or edited under
    ``prompts/`` without updating (or creating) its packaged twin under
    ``src/agent_nettools/data/prompts/`` to match -- copy the canonical file
    over the packaged one and commit both."""

    packaged_dir = _packaged_prompts_dir()

    for filename in _runtime_required_prompt_filenames():
        canonical = PROMPTS_DIR / filename
        packaged = packaged_dir / filename
        assert canonical.is_file(), f"canonical prompt missing: {canonical}"
        assert packaged.is_file(), f"packaged prompt missing: {packaged}"
        assert packaged.read_bytes() == canonical.read_bytes(), (
            f"src/agent_nettools/data/prompts/{filename} has drifted from "
            f"prompts/{filename} -- these must be byte-identical"
        )


def test_packaged_prompts_dir_lives_inside_the_installed_package():
    packaged_dir = _packaged_prompts_dir()

    assert Path(str(packaged_dir)).parts[-2:] == ("agent_nettools", "data") or (
        # importlib.resources.files() may return a Traversable rather than a
        # plain Path; str() always renders the real filesystem path for a
        # normal (non-zipped) install, which is the only case this project
        # ships in.
        "agent_nettools" in str(packaged_dir) and str(packaged_dir).endswith("prompts")
    )


def test_load_prompt_falls_back_to_the_packaged_copy_when_the_source_tree_is_gone(
    monkeypatch, tmp_path
):
    """No source-tree ``prompts/`` at all (the installed-wheel case) --
    ``load_prompt`` must still succeed, for every runtime-required
    ``(name, version)`` the scan finds, via the packaged copy."""

    monkeypatch.setattr(prompt_library, "PROMPTS_DIR", tmp_path / "no-such-prompts-dir")

    for name, version in _runtime_required_name_version_pairs():
        packaged_text = (_packaged_prompts_dir() / f"{name}.v{version}.txt").read_text(
            encoding="utf-8"
        )
        assert load_prompt(name, version) == packaged_text


def test_a_real_source_tree_prompt_still_wins_over_the_packaged_copy(monkeypatch, tmp_path):
    """The packaged copy is a last resort, never a shadow of a live file --
    an operator's own in-progress edit under ``prompts/`` must still be what
    a normal checkout loads."""

    fake_prompts_dir = tmp_path / "prompts"
    fake_prompts_dir.mkdir()
    distinctive_text = "DISTINCTIVE SOURCE-TREE COPY, NOT THE PACKAGED ONE\n"
    (fake_prompts_dir / "report.v1.txt").write_text(distinctive_text, encoding="utf-8")
    monkeypatch.setattr(prompt_library, "PROMPTS_DIR", fake_prompts_dir)

    assert load_prompt("report", 1) == distinctive_text


def test_a_version_absent_from_both_locations_still_raises_prompt_not_found(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(prompt_library, "PROMPTS_DIR", tmp_path / "no-such-prompts-dir")

    with pytest.raises(PromptNotFoundError):
        load_prompt("report", 999)
