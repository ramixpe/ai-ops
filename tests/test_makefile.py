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
