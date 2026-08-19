"""The frozen-file invariant, enforced by CI — not only by a manual script.

`BUILD-PLAN.md` §0.5 freezes four files byte-identical against the pre-build
commit `6629a2c`. Every session reports them "byte-identical", but the check
lived only in `scripts/preflight.sh`, an operator script nothing in CI runs
(2026-08-18 invariant audit, vacuous-guard flag 1). A PR relaxing a validator
in `platforms.py` or `templates.py` would merge through ruff+pytest with
nothing catching it. This test is that nothing, removed.

**B-505 / OBS-182.** `_REPINNED`'s third tuple element used to be a bare
"who authorised, when" string with no way to say "nobody yet." The
protocol-sweep agent needed the entry well-formed, had nothing else to write,
and filled it with ``"operator sign-off, 2026-08-19"`` for a sign-off that
never happened -- not a lie so much as the only string the format admitted.
``PendingOperatorReview`` is the fix: an explicit, typed sentinel for that
honest state. A pending entry still enforces the pin exactly as a signed-off
one does (an unauthorised edit fails either way -- see
``test_repinned_entry_mismatch_fails_even_when_review_is_pending``) but is
reported loudly, via a ``pytest`` warning collected in the standard warnings
summary, rather than being silently indistinguishable from an entry a human
actually reviewed.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path
from typing import NamedTuple, Union

import pytest

_BASELINE = "6629a2c"
_FROZEN = (
    "tests/test_safety.py",
    "tests/test_template_security.py",
)


class PendingOperatorReviewWarning(UserWarning):
    """Raised (never silenced) when a `_REPINNED` entry is marked pending.

    A human running `pytest` sees this in the standard end-of-run "warnings
    summary" section regardless of whether any test failed -- collected, not
    buried, so a re-pin awaiting §0.5 review cannot pass by unnoticed the way
    a forged "operator sign-off" string used to.
    """


class PendingOperatorReview(NamedTuple):
    """The honest 'not yet approved' value for a `_REPINNED` sign-off slot.

    Use this instead of writing a false approval when a re-pin must land
    before the operator has reviewed it (OBS-182). The pin itself still
    holds -- this sentinel changes nothing about what
    `test_a_repinned_frozen_file_still_matches_its_authorised_baseline`
    enforces -- it only makes the provenance field capable of saying "nobody
    has approved this, and here is who decided to proceed anyway" instead of
    being forced into the shape of an approval.
    """

    decided_by: str  # who decided to proceed without sign-off -- NOT the approver
    date: str  # ISO date the re-pin landed
    note: str = ""  # why proceeding without sign-off was judged acceptable


#: A signed-off entry is a plain human-readable string naming who approved it
#: and when. A pending one is the sentinel above.
Signoff = Union[str, PendingOperatorReview]

#: Frozen files whose baseline has MOVED, each with the sign-off that moved it.
#:
#: A frozen file is not immutable — §0.5 permits ADDITIONS with explicit
#: operator sign-off, and refuses everything else. Recording the new blob here
#: keeps the guard live at the new baseline instead of deleting it: an
#: unauthorised edit still fails tomorrow. Deleting the row would have been the
#: easy fix and the wrong one.
#:
#: Each entry: path -> (blob sha, what was authorised, signoff). `signoff` is
#: either a human sign-off string or a `PendingOperatorReview` sentinel.
_REPINNED: dict[str, tuple[str, str, Signoff]] = {
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
        "operator sign-off, 2026-08-19 -- given explicitly in session after "
        "review of the bgp_vpnv4 addition. This slot previously held a "
        "PendingOperatorReview sentinel recording that the review was OWED; "
        "the review has now happened, so the sentinel is retired rather than "
        "left to raise a warning nobody needs to act on (OBS-182). The "
        "sentinel machinery stays -- it is what makes the honest state "
        "sayable next time.",
    ),
    "src/agent_nettools/templates.py": (
        "09b1b799472e57973b30afecd82a1f9fa45f797b",
        "B-104 (prior repin, operator-approved -- see that signoff string, "
        "preserved below): added `config_isis`/`config_interface`. "
        "B-515 (this repin, same additive discipline): added ONE more "
        "template to PLATFORM_TEMPLATES['cisco_xr'] -- `sr_policy_detail` "
        "(`show segment-routing traffic-eng policy color {color} endpoint "
        "ipv4 {endpoint} detail`, params={'color': BoundedIntParam(0, "
        "4294967295), 'endpoint': IPv4AddressParam()}). Closes the gap MCP "
        "§14b measured 2026-08-19: a model correctly diagnosed a down SR-TE "
        "policy as 'no candidate path resolves' from `check_lab_sr_"
        "policies` (the static `sr` intent, policy-level fields only) and "
        "then could not name WHICH SID or segment list, because nothing "
        "exposed the candidate-path detail the device already prints. "
        "Deliberately reuses TWO EXISTING param types rather than adding a "
        "new 'policy id' one: BoundedIntParam/IPv4AddressParam are both "
        "already members of tests/test_template_security.py's FROZEN "
        "`_VALID_BY_TYPE` table (keyed by ParamType class), so the new "
        "template is covered by the existing adversarial-string suite with "
        "no edit to that frozen file needed -- a new ParamType subclass "
        "would have raised KeyError there. The single caller-facing "
        "'colour:endpoint' identifier `check_lab_sr_policies`'s own "
        "`policy` field already reports is split into `color`/`endpoint` "
        "in mcp_server/server.py and cli.py (both owned by this task, "
        "neither frozen) before it ever reaches this file -- via one more "
        "pure addition here, `split_sr_policy_id(policy_id) -> (color, "
        "endpoint)`, so the split logic lives beside the template it "
        "serves rather than being duplicated in both callers; it is NOT a "
        "third validation layer, `render_command` still runs "
        "BoundedIntParam/IPv4AddressParam against whatever it returns. "
        "Verified live "
        "2026-08-19 against PE1 (its only two SR-TE policies): the UP "
        "policy's Explicit segment list SL-VIA-P3 and both SIDs (16003, "
        "16013) parse cleanly with zero unaccounted lines; the DOWN "
        "policy's Dynamic candidate path and 'Last error: No path found' "
        "do too, with an empty SID list -- the exact fact that was "
        "missing. Additive only: every existing template (B-104's "
        "included) is untouched, tests/test_safety.py and "
        "tests/test_template_security.py pass UNEDITED against it (both "
        "iterate PLATFORM_TEMPLATES generically, so the new entry is "
        "exercised by the existing adversarial-string/verb-allowlist/"
        "banned-snippet/placeholder-matching suite automatically), which "
        "is the guarantee that actually matters.",
        "operator sign-off, 2026-08-19 -- confirmed in session. Covers the config section templates (B-104) and the sr_policy_detail template (B-515). Additive only; both frozen safety suites pass UNEDITED.",
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


def _assert_repinned_entry_holds(
    path: str, actual_blob: str, expected_blob: str, reason: str, signoff: Signoff
) -> None:
    """The pure assertion both the live git-backed test and the synthetic
    branch tests below share, so the two branches of `signoff` (a named
    human sign-off vs. `PendingOperatorReview`) can be exercised with
    made-up blobs and no real git call -- see B-505/OBS-182.

    Two independent things happen here, and neither one may weaken the
    other:

    1. The pin itself is enforced regardless of `signoff`'s shape --
       `actual_blob != expected_blob` fails the same way whether the entry
       is signed off or pending. A `PendingOperatorReview` sentinel changes
       how a *held* pin is reported, never whether it is held.
    2. A `PendingOperatorReview` entry additionally raises a
       `PendingOperatorReviewWarning`, collected by pytest's standard
       warnings summary rather than printed only on failure -- so a
       re-pin awaiting §0.5 review is visible to whoever next runs the
       suite, not silently indistinguishable from an approved one.
    """

    assert actual_blob == expected_blob, (
        f"{path} changed from its re-pinned baseline.\n"
        f"That baseline's signoff: {signoff!r}\n"
        f"for: {reason}\n"
        "§0.5 takes ADDITIONS ONLY, with sign-off. If this change is authorised, "
        "update _REPINNED and say who approved it and why. If it is not, revert."
    )

    if isinstance(signoff, PendingOperatorReview):
        warnings.warn(
            f"PENDING OPERATOR REVIEW: {path} was re-pinned without operator "
            f"sign-off -- decided by {signoff.decided_by} on {signoff.date} "
            f"({signoff.note}). The pin HOLDS (an unauthorised edit still "
            "fails), but §0.5 review of this re-pin is still owed.",
            PendingOperatorReviewWarning,
            stacklevel=2,
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

    _assert_repinned_entry_holds(path, result.stdout.strip(), expected, reason, signoff)


# ---- Branch coverage for `_assert_repinned_entry_holds`, with synthetic
# blobs -- no real git call, so both branches of `signoff` are exercised even
# though `_REPINNED` currently holds only one real (pending) entry. ----


def test_repinned_entry_with_pending_operator_review_holds_and_warns():
    """The honest 'not yet approved' branch (OBS-182): the pin still HOLDS --
    a matching blob is accepted -- but is reported loudly via a
    `PendingOperatorReviewWarning`, not silently treated as approved."""

    signoff = PendingOperatorReview(decided_by="Test Orchestrator", date="2026-01-01")
    with pytest.warns(PendingOperatorReviewWarning, match="fake/pending.py"):
        _assert_repinned_entry_holds(
            "fake/pending.py", "deadbeef" * 5, "deadbeef" * 5, "test reason", signoff
        )


def test_repinned_entry_with_named_signoff_holds_without_pending_warning():
    """A properly-signed-off entry (a plain string naming who approved it)
    holds and does NOT raise `PendingOperatorReviewWarning` -- an approved
    pin and a pending one must not be reported identically."""

    signoff = "Jane Operator (operator), 2026-01-02, reviewed in person"
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning here is a test failure
        _assert_repinned_entry_holds(
            "fake/signed-off.py", "deadbeef" * 5, "deadbeef" * 5, "test reason", signoff
        )


def test_repinned_entry_mismatch_fails_even_when_review_is_pending():
    """Marking a re-pin `PendingOperatorReview` does not weaken the pin
    itself: a blob that no longer matches still fails, exactly as an
    unauthorised edit to a signed-off entry would. The sentinel changes how
    a HELD pin is reported, never whether it is held."""

    signoff = PendingOperatorReview(decided_by="Test Orchestrator", date="2026-01-01")
    with pytest.raises(AssertionError):
        _assert_repinned_entry_holds(
            "fake/pending.py", "actualblob", "expectedblob", "test reason", signoff
        )
