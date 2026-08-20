"""Regression test for the ``make help`` drift defect (sanity round, item 3).

``make help`` was a hand-maintained ``@echo`` list that fell out of sync with
the real targets: ``audit``, ``audit-fixtures``, ``config-check`` and
``route-event`` all existed in the Makefile and worked, but none of them
appeared in ``make help`` -- the echo list was simply never updated when the
2026-08-18 OPS wave added them. The fix made ``help`` self-maintaining,
derived (via an ``awk`` one-liner over ``$(MAKEFILE_LIST)``) from each
target's own ``## description`` comment, rather than adding four more
hand-written echo lines to the list that already drifted once.

No network, no credentials -- ``make help`` only echoes and greps the
Makefile itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _make_help() -> str:
    result = subprocess.run(
        ["make", "help"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _make_dry_run(*args: str) -> str:
    """``make -n`` prints the recipe line a target WOULD run without running
    it -- no network, no credentials, same no-side-effects posture as the
    rest of this file.
    """

    result = subprocess.run(
        ["make", "-n", *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "target",
    ["audit", "audit-fixtures", "config-check", "route-event"],
)
def test_make_help_lists_every_ops_wave_target(target):
    """The four targets the sanity round found missing from ``make help``."""

    assert f"make {target}" in _make_help()


def test_make_help_is_derived_from_the_makefiles_own_doc_comments():
    """Pin the self-maintaining property, not just today's four names.

    Every target carrying a ``## description`` comment in the Makefile must
    show up in ``make help``'s output, so a *future* target cannot silently
    repeat this drift by being added without also updating a second,
    hand-written list -- there is no longer a second list to forget.
    """

    makefile_text = (_REPO_ROOT / "Makefile").read_text()
    documented_targets = re.findall(r"(?m)^([a-zA-Z0-9_-]+):.*?##", makefile_text)
    assert documented_targets  # sanity: the Makefile does carry some

    help_text = _make_help()
    for target in documented_targets:
        assert f"make {target}" in help_text, target


# --- OBS-558/559: `investigate` and `ledger` had no `make` wrapper ----------
# Two commands (`nettools investigate DEVICE SUBJECT ...` and `nettools
# ledger summary` / `nettools ledger verdict ID OUTCOME [--by NAME]`) shipped
# on the CLI with no corresponding `make` target. `make -n <target> VAR=...`
# renders the exact recipe line without running it, so these tests pin the
# wrapping without touching a device, the ledger file, or a ticket.


def test_make_investigate_renders_device_and_subject():
    output = _make_dry_run("investigate", "DEVICE=PE1", "SUBJECT=10.255.0.31")
    assert "nettools investigate PE1 10.255.0.31" in output


def test_make_investigate_passes_through_args():
    output = _make_dry_run(
        "investigate",
        "DEVICE=RR1",
        "SUBJECT=10.255.0.12",
        "ARGS=--flow bgp_session --from-fixtures",
    )
    assert "nettools investigate RR1 10.255.0.12 --flow bgp_session --from-fixtures" in output


def test_make_investigate_has_real_defaults_like_the_other_two_arg_targets():
    """DEVICE/SUBJECT default the same way DEVICE/PREFIX etc. do for `route`,
    `bgp-neighbor` and friends -- so plain `make investigate` renders a
    complete, runnable command, not a target that silently drops its second
    positional under shell word-splitting (see the DEVICE comment at the top
    of the Makefile).
    """

    output = _make_dry_run("investigate")
    assert "nettools investigate PE1 10.255.0.31" in output


def test_make_ledger_summary_renders():
    output = _make_dry_run("ledger-summary")
    assert "nettools ledger summary" in output


def test_make_ledger_verdict_renders_id_and_outcome():
    output = _make_dry_run(
        "ledger-verdict", "DIAGNOSIS_ID=281a03a74e2a4413b7342057fb2c7c61", "OUTCOME=confirmed_correct"
    )
    assert "nettools ledger verdict 281a03a74e2a4413b7342057fb2c7c61 confirmed_correct" in output


def test_make_ledger_verdict_passes_through_by():
    output = _make_dry_run(
        "ledger-verdict",
        "DIAGNOSIS_ID=281a03a74e2a4413b7342057fb2c7c61",
        "OUTCOME=incorrect",
        "ARGS=--by rami",
    )
    assert (
        "nettools ledger verdict 281a03a74e2a4413b7342057fb2c7c61 incorrect --by rami" in output
    )


def test_make_ledger_verdict_missing_required_vars_is_a_clear_refusal_not_a_silent_no_op():
    """DIAGNOSIS_ID and OUTCOME have no `?=` default in the Makefile -- unlike
    DEVICE/SUBJECT there is no real placeholder value for a ledger id, so a
    missing var is left to collapse to an empty argv and get refused by
    `nettools`'s own argparse, the same way a raw `nettools ledger verdict`
    invocation with no arguments would. This actually *runs* the target
    (not `-n`) to prove the refusal is real, not just rendered -- ledger
    verdict with no id/outcome cannot touch a device, credentials, or a live
    ledger file before argparse rejects it.
    """

    result = subprocess.run(
        ["make", "ledger-verdict"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode != 0
    assert "the following arguments are required: diagnosis_id, outcome" in result.stderr


def test_make_help_lists_investigate_and_ledger():
    """The two new targets follow the same self-maintaining `## ` convention
    as everything else, so they show up in `make help` for free.
    """

    help_text = _make_help()
    for target in ("investigate", "ledger-summary", "ledger-verdict"):
        assert f"make {target}" in help_text, target
