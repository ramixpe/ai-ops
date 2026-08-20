"""Cross-module envelope-family sync guard.

This codebase deliberately has five independent, private, module-level
envelope-constructing functions instead of one shared one --
`network_tools._base_result`, `netbox._read_base_envelope`,
`graph._read_base_envelope`, `logs_loki._base_envelope`, and
`metrics_prometheus._base_envelope`. Each module's own docstring gives the
same reason for not importing a shared helper: the candidate is
module-private by convention, and `network_tools.py` is out of scope for
edits. That decision is not revisited here.

What *is* missing is a test that would catch the failure mode five
independent copies actually invite: one copy's shape silently drifts from
the other four because there is no shared type and nothing compares them.
`tests/test_mcp_boundary.py` extensively tests `mcp_server.boundary.sanitize`
and individual registered tools' hand-built envelopes (see its own
`_envelope`-shaped call sites), but nothing anywhere calls all five
envelope-constructing functions side by side and checks they agree. This
file is that missing guard.

**The invariant, verified by direct measurement, not assumed:** called with
minimal valid arguments, all five functions return a dict with *exactly* the
same seven keys --

    tool, device, status, timestamp, source, data, errors

-- with `data == {}` (a dict) and `errors == []` (a list) before any caller
mutates the fresh envelope. That is the whole "base envelope" contract: the
five keys `tool`/`device`/`status`/`data`/`errors` are also exactly what
`mcp_server/server.py`'s own hand-built `_envelope()` returns for a
boundary-registered tool result (the shape `mcp_server/boundary.sanitize`
walks), and `timestamp`/`source` ride alongside them consistently across all
five families on top of that minimum. If a future edit to any one of the
five accidentally drops, renames, or adds a key -- the F1-shaped bug this
guard is written against -- this test fails on that module alone.
"""

from __future__ import annotations

from agent_nettools import graph, logs_loki, metrics_prometheus, netbox, network_tools

# The measured invariant: every fresh, unmutated envelope from any of the
# five families carries exactly this key set. Pinned literally (not just
# "whatever the first one returns") so a change that breaks the invariant
# fails with a readable diff instead of a tautology.
ENVELOPE_FAMILY_KEYS = frozenset({"tool", "device", "status", "timestamp", "source", "data", "errors"})


def _family_envelopes() -> dict[str, dict]:
    """One freshly-built envelope from each of the five construction
    functions, called directly (private, but importable from tests --
    the same convention `tests/test_network_tools.py::test_base_result_...`
    already uses for `network_tools._base_result`) with the minimal valid
    arguments needed to produce a real envelope."""

    return {
        "network_tools._base_result": network_tools._base_result(
            "get_device_facts", "PE1"
        ),
        "netbox._read_base_envelope": netbox._read_base_envelope("device_inventory"),
        "graph._read_base_envelope": graph._read_base_envelope(),
        "logs_loki._base_envelope": logs_loki._base_envelope("logs_for_device", "PE1"),
        "metrics_prometheus._base_envelope": metrics_prometheus._base_envelope(
            "device_uptime_history", "PE1"
        ),
    }


def _matches_family_shape(envelope: dict) -> bool:
    """The one check this whole file exists to apply: does ``envelope``'s
    key set match the family invariant, exactly (no more, no fewer)?

    Reused verbatim against both the five real envelopes and the
    deliberately-broken one below, so the anti-vacuity check and the real
    guard are provably the same code path.
    """

    return set(envelope.keys()) == ENVELOPE_FAMILY_KEYS


def test_all_five_envelope_families_agree_on_the_pinned_key_set():
    """The guard: five independent function bodies, zero shared code, and
    their outputs must still agree on the base envelope shape.

    This is the test that would have caught an F1-shaped bug (one copy's
    envelope silently losing or gaining a key) before it shipped -- it is
    exactly the comparison nothing in `test_mcp_boundary.py` or the
    per-module test files makes.
    """

    envelopes = _family_envelopes()

    mismatched = {
        name: sorted(env.keys())
        for name, env in envelopes.items()
        if not _matches_family_shape(env)
    }
    assert not mismatched, (
        f"these envelope constructors disagree with the pinned family key "
        f"set {sorted(ENVELOPE_FAMILY_KEYS)}: {mismatched}"
    )

    # Restated as pairwise agreement, not only against the literal pin above:
    # even if the pinned constant itself were ever wrong, five independent
    # bodies producing five different key sets from each other must fail.
    distinct_shapes = {frozenset(env.keys()) for env in envelopes.values()}
    assert len(distinct_shapes) == 1, (
        f"envelope constructors do not all agree with each other: {distinct_shapes}"
    )


def test_all_five_fresh_envelopes_share_the_same_data_and_errors_shape():
    """Same-named keys are not enough if their *types* diverge -- a `data`
    that is a list in one family and a dict in another would still pass a
    bare key-set check while breaking every downstream reader (including
    `mcp_server.boundary.sanitize`, which special-cases `commands` and
    `errors` by assuming `data` is a dict and `errors` is a list).

    A fresh, unmutated envelope from any of the five families has not yet
    had a caller attach real content -- `data` is always `{}` and `errors`
    is always `[]` at this point, before `_attach_parsed`/`_safe_error`/
    equivalents in each module do their work.
    """

    for name, envelope in _family_envelopes().items():
        assert envelope["data"] == {}, f"{name}: expected an empty dict data, got {envelope['data']!r}"
        assert envelope["errors"] == [], f"{name}: expected an empty list errors, got {envelope['errors']!r}"
        assert envelope["status"] == "success", f"{name}: a fresh envelope must start success"
        assert isinstance(envelope["tool"], str) and envelope["tool"], f"{name}: tool must be a non-empty string"
        assert isinstance(envelope["source"], str) and envelope["source"], f"{name}: source must be a non-empty string"
        assert isinstance(envelope["timestamp"], str) and envelope["timestamp"], f"{name}: timestamp must be a non-empty string"


def test_a_deliberately_broken_envelope_fails_the_same_shape_check():
    """Anti-vacuity: prove `_matches_family_shape` actually discriminates
    rather than being true of anything handed to it.

    Hand-builds a broken copy in the exact shape the F1 drift bug took --
    one family's envelope silently missing a key (`source`, here) the other
    four all carry -- and asserts the *same* check the real guard test uses
    rejects it. If this assertion ever passed with `assert
    _matches_family_shape(broken)`, the guard above would be worthless.
    """

    real_envelopes = _family_envelopes()
    for envelope in real_envelopes.values():
        assert _matches_family_shape(envelope)

    # The mutation: drop `source`, simulating a fifth copy that forgot the
    # field the other four agree on. This is a real dict, not a mock -- it
    # is what one of the five functions would return if a future edit
    # silently dropped a key.
    broken = dict(network_tools._base_result("get_device_facts", "PE1"))
    del broken["source"]

    assert not _matches_family_shape(broken), (
        "the shape check must reject an envelope missing a key the family shares"
    )

    # And the inverse mutation -- an extra, unrecognised key -- must also
    # be rejected: the invariant is exact key-set equality, not "at least".
    extra_key = dict(network_tools._base_result("get_device_facts", "PE1"))
    extra_key["retries_used"] = {}

    assert not _matches_family_shape(extra_key), (
        "the shape check must reject an envelope carrying an extra key the family does not share"
    )
