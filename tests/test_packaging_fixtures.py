"""EER-004 -- the packaged demo fixture subset, and its read/write split.

``fixtures.DEFAULT_FIXTURE_DIR`` (``"tests/fixtures"``) is resolved against
the process cwd, with no packaged fallback rung -- unlike
``inventory_model.resolve_inventory_path``'s four. A `pip install`ed user
with no source checkout nearby cannot run the documented flagship demo
(``nettools investigate RR1 10.255.0.12 --from-fixtures``, README's own
"offline: the flagship demo" line) or any of the other `--from-fixtures`
commands.

The full ~3.4 MB / 641-file ``tests/fixtures/`` corpus stays test-only.
``src/agent_nettools/data/fixtures/`` carries only the minimum subset that
makes the README-documented ``--from-fixtures`` commands work from an
install: ``investigate RR1 10.255.0.12`` (labels ``broken`` -- the default --
``healthy`` and ``t0``, all three demoed explicitly in the README),
``health --all`` (label ``t0``), ``audit`` (label ``healthy``), and
``learn-topology`` (label ``t0``) -- 180 files, ~144 KiB, determined by
tracing exactly which fixture files those commands read (and, for the
``t0`` investigate demo, which they *attempt to read and are documented to
find missing* -- that variant is README-documented to exit 2 for exactly
that reason, so its packaged set correctly omits the peer-specific template
files ``t0`` never captured).

Two things this file must keep true, same shape as
``test_packaging_inventory.py``:

1. Every packaged copy is byte-identical to its ``tests/fixtures/`` original.
2. The fallback is reachable and the documented demo commands actually run
   against it once cwd has no ``./tests/fixtures`` of its own -- and a real
   ``./tests/fixtures`` (or an explicit/env-configured root) still wins.

A third property is specific to fixtures, unlike the read-only inventory
file: ``nettools capture`` *writes* into the resolved fixture root, so the
read-side fallback (``_fixture_read_dir``) must never be what a capture
resolves to -- capturing into a read-only ``site-packages`` copy would fail
outright. ``_fixture_dir`` (the write resolver) is intentionally unchanged
by this item and is asserted here to stay that way.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_nettools import cli
from agent_nettools.fixtures import (
    DEFAULT_FIXTURE_DIR,
    _fixture_dir,
    _fixture_read_dir,
    _packaged_fixture_root,
    fixture_path,
)
from agent_nettools.inventory_model import reset_inventory_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_FIXTURES = REPO_ROOT / "tests" / "fixtures"
PACKAGED_FIXTURES = REPO_ROOT / "src" / "agent_nettools" / "data" / "fixtures"


def _packaged_files() -> list[Path]:
    return sorted(p for p in PACKAGED_FIXTURES.rglob("*.txt"))


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_inventory_cache()
    yield
    reset_inventory_cache()


# --------------------------------------------------------------------------- #
# 1. Byte-identity drift guard
# --------------------------------------------------------------------------- #


def test_the_packaged_subset_is_not_empty():
    """A guardrail that can pass by measuring nothing needs a companion that
    fails when the set is empty (see prior packaging precedent)."""

    assert _packaged_files(), "no fixture files were packaged at all"


def test_every_packaged_fixture_file_matches_its_canonical_original_byte_for_byte():
    """Drift guard: if this ever fails, a committed fixture under
    ``tests/fixtures/`` was recaptured/edited without updating its packaged
    twin under ``src/agent_nettools/data/fixtures/`` -- copy the canonical
    file over the packaged one and commit both."""

    for packaged in _packaged_files():
        relative = packaged.relative_to(PACKAGED_FIXTURES)
        canonical = CANONICAL_FIXTURES / relative
        assert canonical.is_file(), f"packaged file has no canonical original: {canonical}"
        assert packaged.read_bytes() == canonical.read_bytes(), (
            f"src/agent_nettools/data/fixtures/{relative} has drifted from "
            f"tests/fixtures/{relative} -- these must be byte-identical"
        )


def test_the_packaged_subset_is_small():
    """Operator decision: a small demo subset, not the whole corpus. Pinned
    loosely (well under 1 MB) so a future addition stays deliberate rather
    than silently ballooning back toward the full ~3.4 MB corpus."""

    total = sum(p.stat().st_size for p in _packaged_files())
    assert total < 512 * 1024, f"packaged fixture subset grew to {total} bytes"


# --------------------------------------------------------------------------- #
# 2. Root resolution: read gets the packaged fallback, write never does
# --------------------------------------------------------------------------- #


def test_fixture_read_dir_falls_back_to_the_packaged_copy_when_cwd_has_none(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # tmp_path deliberately has no tests/fixtures of its own

    resolved = _fixture_read_dir(None)

    assert resolved == Path(str(_packaged_fixture_root()))
    assert resolved.is_dir()


def test_fixture_read_dir_prefers_a_real_cwd_relative_directory(monkeypatch, tmp_path):
    """The packaged copy is a last resort, never a shadow of a live corpus."""

    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    local_fixtures = tmp_path / DEFAULT_FIXTURE_DIR
    local_fixtures.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    resolved = _fixture_read_dir(None)

    assert resolved == Path(DEFAULT_FIXTURE_DIR)
    assert resolved != Path(str(_packaged_fixture_root()))


def test_fixture_dir_write_resolver_never_falls_back_to_the_packaged_copy(monkeypatch, tmp_path):
    """`nettools capture` must create `./tests/fixtures/...` fresh, never
    resolve into the read-only packaged copy under site-packages, when cwd
    has no fixtures directory of its own yet."""

    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = _fixture_dir(None)

    assert resolved == Path(DEFAULT_FIXTURE_DIR)
    assert resolved != Path(str(_packaged_fixture_root()))


def test_fixture_path_for_write_stays_on_the_write_resolver(monkeypatch, tmp_path):
    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    path = fixture_path(
        {"platform": "cisco_xr", "name": "PE1"}, "show version", label="t0", for_write=True
    )

    assert str(path).startswith(DEFAULT_FIXTURE_DIR)


def test_fixture_path_for_read_reaches_the_packaged_copy(monkeypatch, tmp_path):
    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    path = fixture_path({"platform": "cisco_xr", "name": "RR1"}, "show version", label="t0")

    assert path.is_file()
    assert "cisco_xr/RR1" in str(path) or "cisco_xr" + os.sep + "RR1" in str(path)


# --------------------------------------------------------------------------- #
# 3. End-to-end: the README-documented demo commands, from a relocated cwd
# --------------------------------------------------------------------------- #


def _run(argv):
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


@pytest.fixture
def _installed_like_environment(monkeypatch, tmp_path):
    """No source-tree ``inventory/`` or ``tests/fixtures/`` in cwd, and no
    env override for either -- the shape of a real `pip install` with no
    checkout nearby. Both `inventory_model` and `fixtures` must fall back to
    their packaged copies for this to work at all (EER-003's own inventory
    fallback is a precondition here, not something this item re-proves)."""

    monkeypatch.delenv("NETTOOLS_INVENTORY", raising=False)
    monkeypatch.delenv("NETTOOLS_FIXTURE_DIR", raising=False)
    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_PASSWORD", "test-password")
    monkeypatch.chdir(tmp_path)
    reset_inventory_cache()
    yield
    reset_inventory_cache()


def test_investigate_flagship_demo_runs_from_a_relocated_cwd(_installed_like_environment):
    """README's own "offline: the flagship demo" line, label ``broken``
    (the flag's default -- no ``--label`` given)."""

    rc = _run(
        ["investigate", "RR1", "10.255.0.12", "--from-fixtures", "--format", "json", "--quiet"],
    )

    assert rc == cli.EXIT_WARNING  # a real, deterministically-found fault


def test_investigate_healthy_label_exits_ok_from_a_relocated_cwd(_installed_like_environment):
    """README lines 49: ``--label healthy`` is documented to exit 0."""

    rc = _run(
        [
            "investigate", "RR1", "10.255.0.12", "--from-fixtures",
            "--label", "healthy", "--format", "json", "--quiet",
        ],
    )

    assert rc == cli.EXIT_OK


def test_investigate_t0_label_exits_2_from_a_relocated_cwd(_installed_like_environment):
    """README line 50: ``--label t0`` is documented to exit 2 -- `t0` is a
    quiet-fabric capture with no peer-specific template evidence, so the
    descent correctly reports it cannot be determined. The packaged subset
    deliberately does not carry those files under `t0` for RR1/PE2 either,
    matching the canonical corpus exactly (see this module's docstring)."""

    rc = _run(
        [
            "investigate", "RR1", "10.255.0.12", "--from-fixtures",
            "--label", "t0", "--format", "json", "--quiet",
        ],
    )

    assert rc == cli.EXIT_CRITICAL


def test_health_all_from_fixtures_runs_from_a_relocated_cwd(_installed_like_environment):
    rc = _run(["health", "--all", "--from-fixtures", "--format", "json", "--quiet"])

    assert rc in (cli.EXIT_OK, cli.EXIT_WARNING, cli.EXIT_CRITICAL)  # ran to completion at all


def test_audit_from_fixtures_runs_from_a_relocated_cwd(_installed_like_environment):
    rc = _run(["audit", "--from-fixtures", "--format", "json", "--quiet"])

    assert rc == cli.EXIT_OK


def test_learn_topology_from_fixtures_runs_from_a_relocated_cwd(_installed_like_environment, capsys):
    rc = _run(["learn-topology", "--from-fixtures"])

    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"isis_adjacencies"' in out
