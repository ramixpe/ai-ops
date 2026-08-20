"""EER-003 -- the packaged fallback copies of the six runtime-required prompts.

`prompt_library.PROMPTS_DIR` (``src/agent_nettools/prompt_library.py``) is
three parents up from that file -- the repo root's ``prompts/`` directory --
so it does not exist once this package is installed from a built wheel with
no source checkout anywhere nearby, and ``load_prompt`` would raise
``PromptNotFoundError`` for every caller, including the deterministic
``investigate --no-model`` path (``build_report_prompt`` is reachable from a
plain descent report, with no LLM involved at all).

``src/agent_nettools/data/prompts/*.txt`` are checked-in byte-identical
copies of the six runtime-required versions -- ``report.v1``, ``report.v2``,
``correlate.v1`` through ``correlate.v4`` -- declared in ``pyproject.toml``'s
``[tool.setuptools.package-data]`` and resolved through
``importlib.resources`` so they ship inside the wheel. Superseded prompt
versions stay reachable on purpose (``load_prompt`` accepts any version), so
all six, not just the two named in ``CURRENT_VERSION``, are packaged.

``prompts/tests/cases/*.json`` and ``prompts/README.md`` are test-only and
are deliberately NOT packaged -- nothing at runtime reads them.

Same two things this file must keep true as ``test_packaging_inventory.py``:

1. Every packaged copy is byte-identical to its source-tree original --
   nothing enforces that at packaging time, so a future prompt edit that
   forgets its packaged twin must fail loudly here.
2. The fallback is actually reachable and loadable when the source tree is
   unavailable, and a real source-tree copy still wins when both exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nettools import prompt_library
from agent_nettools.prompt_library import (
    PROMPTS_DIR,
    PromptNotFoundError,
    _packaged_prompts_dir,
    load_prompt,
)

#: The six runtime-required prompt files. Not just ``CURRENT_VERSION``'s two
#: (``report.v2``, ``correlate.v4``): every version `load_prompt` can be
#: asked for is reachable, and superseded versions are deliberately retained
#: (see `prompt_library.py`'s `CURRENT_VERSION` docstring), so all six are
#: packaged.
RUNTIME_REQUIRED_PROMPTS = (
    "report.v1.txt",
    "report.v2.txt",
    "correlate.v1.txt",
    "correlate.v2.txt",
    "correlate.v3.txt",
    "correlate.v4.txt",
)


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


def test_every_runtime_required_prompt_is_packaged_and_byte_identical():
    """Drift guard: if this ever fails, a prompt under ``prompts/`` was
    edited (or superseded) without updating its packaged twin under
    ``src/agent_nettools/data/prompts/`` to match -- copy the canonical file
    over the packaged one and commit both."""

    packaged_dir = _packaged_prompts_dir()

    for filename in RUNTIME_REQUIRED_PROMPTS:
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
    ``load_prompt`` must still succeed, for every runtime-required version,
    via the packaged copy."""

    monkeypatch.setattr(prompt_library, "PROMPTS_DIR", tmp_path / "no-such-prompts-dir")

    for name, version in (
        ("report", 1),
        ("report", 2),
        ("correlate", 1),
        ("correlate", 2),
        ("correlate", 3),
        ("correlate", 4),
    ):
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
