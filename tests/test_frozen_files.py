"""The frozen-file invariant, enforced by CI — not only by a manual script.

`BUILD-PLAN.md` §0.5 freezes four files byte-identical against the pre-build
commit `6629a2c`. Every session reports them "byte-identical", but the check
lived only in `scripts/preflight.sh`, an operator script nothing in CI runs
(2026-08-18 invariant audit, vacuous-guard flag 1). A PR relaxing a validator
in `platforms.py` or `templates.py` would merge through ruff+pytest with
nothing catching it. This test is that nothing, removed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_BASELINE = "6629a2c"
_FROZEN = (
    "tests/test_safety.py",
    "tests/test_template_security.py",
    "src/agent_nettools/platforms.py",
    "src/agent_nettools/templates.py",
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    pytest.skip("not a git checkout — cannot verify frozen blobs")


@pytest.mark.parametrize("path", _FROZEN)
def test_frozen_file_is_byte_identical_to_the_baseline(path):
    root = _repo_root()
    def blob(ref):
        r = subprocess.run(["git", "rev-parse", f"{ref}:{path}"],
                           cwd=root, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"cannot resolve {ref}:{path} — shallow clone or worktree")
        return r.stdout.strip()

    baseline, head = blob(_BASELINE), blob("HEAD")
    assert baseline == head, (
        f"{path} has changed from the frozen baseline {_BASELINE} "
        f"({baseline[:12]} -> {head[:12]}). BUILD-PLAN §0.5: these take "
        "additions only, never a relaxed validator. If the change is genuinely "
        "additive and necessary, HALT and get explicit sign-off before "
        "updating this test's baseline."
    )
