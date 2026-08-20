"""R2 -- the packaged fallback copy of ``inventory/lab.yaml``.

A non-editable install (a real ``pip install`` of a built wheel, with no
source checkout anywhere nearby) cannot see the repo-root ``inventory/``
directory -- it never ships in the wheel. ``src/agent_nettools/data/lab.yaml``
is a checked-in copy that DOES ship (via ``pyproject.toml``'s existing
``[tool.setuptools.package-data] agent_nettools = ["data/*.yaml"]`` glob),
and ``inventory_model._packaged_fallback_path`` resolves it through
``importlib.resources`` so it is findable in both the editable and the
installed case, with no wheel build needed to prove it: for an editable
install, ``importlib.resources.files("agent_nettools")`` already resolves to
this same ``src/agent_nettools`` directory.

Two things this file must keep true:

1. The packaged copy is a byte-identical duplicate of the canonical file --
   nothing enforces that at packaging time, so a future edit to
   ``inventory/lab.yaml`` that forgets its packaged twin must fail loudly
   here rather than silently shipping a stale fallback.
2. The fallback is actually reachable and parseable when the primary path
   (cwd-relative ``./inventory/lab.yaml``) is unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nettools.inventory_model import (
    _packaged_fallback_path,
    load_inventory_file,
    reset_inventory_cache,
    resolve_inventory_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PATH = REPO_ROOT / "inventory" / "lab.yaml"


@pytest.fixture(autouse=True)
def _clear_inventory_cache():
    """This module monkeypatches cwd/env around inventory resolution --
    the module-level cache in ``inventory_model.py`` is keyed by resolved
    path, so a stale entry from a previous test would mask a real failure
    here rather than the cache simply being irrelevant."""

    reset_inventory_cache()
    yield
    reset_inventory_cache()


def test_the_packaged_copy_exists_and_matches_the_canonical_file_byte_for_byte():
    """Drift guard: if this ever fails, ``inventory/lab.yaml`` was edited
    without updating ``src/agent_nettools/data/lab.yaml`` to match -- copy the
    canonical file over the packaged one and commit both."""

    packaged = _packaged_fallback_path()

    assert packaged.is_file(), "the packaged fallback copy is missing"
    assert CANONICAL_PATH.is_file(), "the canonical inventory file is missing"
    assert packaged.read_bytes() == CANONICAL_PATH.read_bytes(), (
        "src/agent_nettools/data/lab.yaml has drifted from inventory/lab.yaml -- "
        "these must be byte-identical (see this module's docstring)"
    )


def test_packaged_fallback_path_lives_inside_the_installed_package():
    """Resolved via importlib.resources, not the old source-tree-relative
    ``parents[2]`` trick -- so it is findable whether or not a source
    checkout exists around the installed package."""

    packaged = _packaged_fallback_path()

    assert packaged.parts[-3:] == ("agent_nettools", "data", "lab.yaml")


def test_resolve_inventory_path_falls_back_to_the_packaged_copy_when_cwd_has_none(
    monkeypatch, tmp_path
):
    """No explicit path, no NETTOOLS_INVENTORY, and no ./inventory/lab.yaml in
    cwd (invariant 1 unaffected: still no credential is read to get here)."""

    monkeypatch.delenv("NETTOOLS_INVENTORY", raising=False)
    monkeypatch.chdir(tmp_path)  # tmp_path deliberately has no inventory/ of its own

    resolved = resolve_inventory_path()

    assert resolved == _packaged_fallback_path()
    assert resolved.is_file()


def test_the_fallback_actually_parses_as_a_working_inventory(monkeypatch, tmp_path):
    """End-to-end: with the primary path monkeypatched away, the fallback is
    not just *findable* but *loadable* -- the same 9-device fabric a normal
    run sees. No wheel build: an editable install's package-data glob already
    resolves to the same file importlib.resources would find in a real one."""

    monkeypatch.delenv("NETTOOLS_INVENTORY", raising=False)
    monkeypatch.chdir(tmp_path)

    inventory = load_inventory_file()

    names = {d.name for d in inventory.devices}
    assert names == {"P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"}


def test_a_real_cwd_relative_file_still_wins_over_the_packaged_copy(monkeypatch, tmp_path):
    """The packaged copy is a last resort, never a shadow of a live file --
    an operator's own ./inventory/lab.yaml (even a deliberately different one)
    must still be what a plain, unconfigured run reads."""

    monkeypatch.delenv("NETTOOLS_INVENTORY", raising=False)
    local_inventory_dir = tmp_path / "inventory"
    local_inventory_dir.mkdir()
    local_lab_yaml = local_inventory_dir / "lab.yaml"
    local_lab_yaml.write_text(CANONICAL_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = resolve_inventory_path()

    assert resolved == Path("inventory/lab.yaml")
    assert resolved != _packaged_fallback_path()
