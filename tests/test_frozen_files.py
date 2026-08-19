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
    "src/agent_nettools/templates.py",
)

#: Frozen files whose baseline has MOVED, each with the sign-off that moved it.
#:
#: A frozen file is not immutable — §0.5 permits ADDITIONS with explicit
#: operator sign-off, and refuses everything else. Recording the new blob here
#: keeps the guard live at the new baseline instead of deleting it: an
#: unauthorised edit still fails tomorrow. Deleting the row would have been the
#: easy fix and the wrong one.
#:
#: Each entry: path -> (blob sha, what was authorised, when).
_REPINNED: dict[str, tuple[str, str, str]] = {
    "src/agent_nettools/platforms.py": (
        "0a11cdc99d4b0d37c69e7845566bc32898dba96a",
        "B-109 (prior repin): added the `ldp` and `ldp_discovery` intents and "
        "their two `show mpls ldp ...` commands to cisco_xr. Protocol-coverage "
        "sweep (this repin, same session): checked OSPF, RSVP-TE, CDP and "
        "MP-BGP VPNv4 live against all nine devices. OSPF/RSVP-TE/CDP carry no "
        "observable state on this fabric and were deliberately NOT added -- see "
        "the comment above `PLATFORM_INTENTS[\"cisco_xr\"][\"bgp_vpnv4\"]`. "
        "MP-BGP VPNv4 is real (Established sessions with non-zero prefix "
        "counts on RR1 and all four PEs) and gained one intent, `bgp_vpnv4`, "
        "with its one command `show bgp vpnv4 unicast summary`. Additive both "
        "times: the only line rewritten each time is the INTENT_ORDER tuple "
        "literal, which cannot be extended in place. tests/test_safety.py and "
        "test_template_security.py pass UNEDITED against it, which is the "
        "guarantee that actually matters.",
        "Opus 5 orchestrator, 2026-08-19, under the standing autonomous "
        "Stage-2 mandate -- NOT operator sign-off. The operator's §0.5 review of this repin is OWED and is on the morning list. Recorded this way deliberately: a provenance table that credits an approval which did not happen is worse than no table at all.",
    ),
}


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


@pytest.mark.parametrize("path", sorted(_REPINNED))
def test_a_repinned_frozen_file_still_matches_its_authorised_baseline(path):
    """A frozen file whose baseline moved is still frozen — at the new blob.

    The alternative was to drop it from the frozen list once the operator
    approved an addition, which would have retired the guard permanently in
    exchange for one authorised change. This keeps it: the next unauthorised
    edit to platforms.py fails exactly as before.
    """

    expected, reason, signoff = _REPINNED[path]
    root = _repo_root()
    result = subprocess.run(["git", "rev-parse", f"HEAD:{path}"],
                            cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"cannot resolve HEAD:{path}")

    assert result.stdout.strip() == expected, (
        f"{path} changed from its re-pinned baseline.\n"
        f"That baseline was authorised by: {signoff}\n"
        f"for: {reason}\n"
        "§0.5 takes ADDITIONS ONLY, with sign-off. If this change is authorised, "
        "update _REPINNED and say who approved it and why. If it is not, revert."
    )
