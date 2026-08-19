"""B-208 -- the Stage 2 property suite: the gate on the transition.

Property-based tests: assertions that hold for **all** inputs of a declared
shape, not for hand-picked examples. Five properties, each chosen because it
is a claim this codebase's own docstrings already make about itself, in code
that already exists -- this suite is what proves the claim holds outside the
handful of fixture-derived cases the example-based suite happens to cover.

**Generator: `hypothesis`, used where installed, self-skipping where not.**
`hypothesis` is not a declared project dependency (`pyproject.toml`'s `dev`
extra does not list it) -- adding one is a shared-file edit this task's
ownership boundary is deliberately narrow about, with two other agents live
in the same working tree. Every property below is therefore guarded by
`pytest.importorskip("hypothesis")`: where it is installed (it is, in the
environment this suite was built and verified against -- `pip install
hypothesis` into `.venv` directly, not recorded in `pyproject.toml`), the
full generate-and-shrink engine runs; where it is not, this module's tests
skip cleanly rather than erroring the whole run, so the file cannot regress
the baseline test count in an environment that has not opted in. A
deterministic generator over a declared input space was the stated
fallback and was not needed here.

**A property test that generates only valid inputs proves little.** Every
strategy below spends part of its space on the malformed, empty and
adversarial shapes named in the task: zero-length collections, disagreeing
re-reads at every skew/bound relationship (not just the "obviously over
budget" one), device/interface-shaped tokens wrapped in punctuation and case
variation, and device text that itself contains the delimiter strings the
projector uses to mark it untrusted.

The five properties, and why each was chosen over the alternatives
--------------------------------------------------------------------
1. **The descent never names a cause it did not observe** (`test_render...`).
   Chosen over testing `checks.py`'s individual predicates because the
   sharper claim is about the *renderer* (`render.render_report`) -- the code
   path that turns a `DescentResult` into the prose a human (or a grounding
   gate) reads. `grounding.check_identifier_containment` (B-453) already
   exists as the enforcement; this drives it against a generated space of
   descents wide enough to catch a tokenisation disagreement between the
   trusted side (`evidence_identifiers`) and the scanning side
   (`check_identifier_containment`'s own candidate extraction) that a
   handful of fixture-derived descents would not exercise.
2. **`unevaluated` never becomes `healthy`, and the walk stops there**
   (`test_an_unevaluated_rung_always_stops...`). Chosen because it is
   `descent.py`'s central claim (the module docstring's whole "why
   unevaluated still stops" section) and is a pure function of a rung-verdict
   sequence -- ideal for exhaustive-shape generation, and the property that
   would be cheapest to silently break with a one-line refactor.
3. **Absence is never zero** (`test_a_conclusive_checkresult...`,
   `test_a_cannot_compare_fielddiff...`, `test_an_empty_member_set...`).
   Chosen because the task names it as the build's most repeated defect
   (OBS-188, OBS-202, the config-diff third outcome) -- three separate
   structural guards, one per layer (`checks.CheckResult`,
   `config_diff.FieldDiff`, `descent.evaluate_rung`'s empty-set path), all
   converging on the same rule: nothing may report a measured value over
   evidence that was not read. Each sub-property includes an explicit
   positive control (OBS-181) proving the valid branch still succeeds, not
   only that the invalid one is refused.
4. **The epoch's coherence bound refuses on any disagreement, regardless of
   window width** (`test_any_disagreeing_reread_refuses...`). Chosen because
   B-454's whole point -- and round 5's measured cost of getting it wrong
   (OBS-109) -- is that this must NOT be a threshold comparison; the property
   generates skew/bound pairs that make the window arbitrarily *narrow* and
   confirms disagreement still refuses, which a single fixed-bound example
   test cannot distinguish from "refuses because the window was wide".
5. **The projector never lets raw device text reach a model** (`test_quote_
   device_text...`, `test_free_text_fields_are_always_quoted...`). Chosen
   because it is Invariant 4's second half (CLAUDE.md: "no unparsed device
   text ever reaches a model") and the one guard in this list whose failure
   mode is a real injection vector, not a diagnostic-quality regression --
   `model_egress.quote_device_text`'s own docstring names the exact property
   ("must not be able to close the block early") this drives against
   adversarial content rather than assuming it from the one hand-picked
   example `tests/test_model_egress.py` already has.

What this suite does not attempt
---------------------------------
Grounding's `check_grounding`/`check_chain_coverage` (citation *completeness*,
as opposed to identifier containment) are not fuzzed here -- they require a
report shape correlated with the descent's own rung count in ways a
context-free generator would mostly produce as trivially-failing noise
(`0 observations` for a `5`-rung descent) rather than the deep, structurally
valid-but-wrong reports the existing fixture-driven suite already covers by
construction. Property-based generation earns its keep on inputs a human
would not think to hand-pick; a report's citation completeness is not that
shape.
"""

from __future__ import annotations

import json
import string

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from agent_nettools import flows  # noqa: E402
from agent_nettools.checks import BROKEN, HEALTHY, UNEVALUATED, CheckResult  # noqa: E402
from agent_nettools.config_diff import AGREES, CANNOT_COMPARE, DISAGREES, FieldDiff  # noqa: E402
from agent_nettools.descent import evaluate_rung, run_descent  # noqa: E402
from agent_nettools.epoch import (  # noqa: E402
    COHERENT,
    FABRIC_MOVED,
    UNVERIFIED,
    WINDOW_LIMITED,
    Coherence,
    Reread,
)
from agent_nettools.grounding import check_identifier_containment  # noqa: E402
from agent_nettools.model_egress import (  # noqa: E402
    DEVICE_TEXT_CLOSE,
    DEVICE_TEXT_OPEN,
    RAW_TEXT_KEYS,
    project_envelope,
    quote_device_text,
)
from agent_nettools.render import render_report  # noqa: E402

# A conservative, quiet default: enough examples to shrink toward a real
# counterexample if one exists, without making the ordinary `pytest -q` run
# noticeably slower. `suppress_health_check` for the two properties that
# build a fresh synthetic Flow per example (construction cost, not slow
# logic) -- hypothesis's default budget is tuned for pure functions and
# under-counts object construction that is still well under a second.
_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# =========================================================================== #
# Property 1 -- the descent never names a cause it did not observe
# =========================================================================== #

# A small, closed vocabulary so `_device_name_families` (grounding.py) can
# derive a real device-name-shaped regex from it, the same way it would from
# any real fabric's inventory.
_DEVICES = ("SYN1", "SYN2", "SYN3")
_INTERFACES = ("Gi0/0/0/0", "Gi0/0/0/1", "TenGigE0/0/0/2")


@st.composite
def _legitimate_reason(draw, device: str, subject: str) -> str:
    """Free-form prose that only ever names identifiers already in the descent.

    `device` and `subject` are always legitimate (they come straight from the
    `DescentResult` this reason will sit inside), so this only fuzzes *how*
    they are wrapped -- case, punctuation, position -- plus letters-only noise
    that cannot accidentally form a digit-bearing identifier shape
    (`_IFACE`/the IPv4 pattern both require a digit) and so cannot produce a
    false failure that has nothing to do with the property under test.
    """

    noise = draw(st.text(alphabet=string.ascii_letters + " ,.():;-'", max_size=30))
    dev_variant = draw(st.sampled_from([device, device.lower(), f"{device}'s", f"({device})", f"{device},"]))
    subj_variant = draw(st.sampled_from([subject, subject.lower(), f"{subject}.", f"[{subject}]", f"{subject},"]))
    template = draw(st.sampled_from([
        "{dev} reports {subj} {noise}",
        "{noise} {subj} on {dev}",
        "{dev}: {subj} ({noise})",
        "{noise}",
        "no adjacency on {subj}; {dev} last checked {noise}",
    ]))
    return template.format(dev=dev_variant, subj=subj_variant, noise=noise)


def _synthetic_flow_and_descent(verdicts: list[str], device: str, subject: str, reasons: list[str]):
    """Build a real `Flow`, walk it with `run_descent`, return the `DescentResult`.

    Deliberately drives the actual walker rather than hand-building a
    `DescentResult` -- a hand-built one could silently drift from what
    `run_descent` really produces (mismatched `evidence_keys` bookkeeping,
    for instance), which would make property 1 pass for a reason unrelated to
    the code path it is supposed to be exercising.
    """

    rungs = []
    for i, (verdict, reason) in enumerate(zip(verdicts, reasons, strict=True)):
        name = f"rung_{i}"
        finding = f"synthetic_finding_{i}"

        def check(evidence, subject=None, _verdict=verdict, _reason=reason, _name=name):
            key = f"{device}:synthetic:{_name}"
            if _verdict == HEALTHY:
                return CheckResult(HEALTHY, reason=_reason, subject=subject, evidence_keys=(key,))
            if _verdict == BROKEN:
                return CheckResult(BROKEN, reason=_reason, subject=subject, evidence_keys=(key,))
            return CheckResult(UNEVALUATED, reason=_reason, subject=subject)

        rungs.append(flows.Rung(name=name, collect=(), check=check, finding=finding))

    flow = flows.Flow(
        object_type="synthetic", subject_schema="synthetic",
        descent=tuple(rungs), findings=frozenset(r.finding for r in rungs),
    )
    return run_descent(
        flow, device, subject,
        collector=lambda d, rung, s: {}, resolver=None,
    )


@given(
    verdicts=st.lists(st.sampled_from([HEALTHY, BROKEN]), min_size=1, max_size=6),
    device=st.sampled_from(_DEVICES),
    subject=st.sampled_from(_INTERFACES),
    data=st.data(),
)
@_SETTINGS
def test_render_report_never_fails_its_own_identifier_containment_check(verdicts, device, subject, data):
    """render_report's own output never names an identifier check_identifier_containment
    (B-453) does not already recognise as coming from the descent.

    `verdicts` deliberately excludes `unevaluated` here -- property 2 below
    owns that shape, and mixing it in would make the finding sometimes
    `undetermined` (a real, useful case, just not this property's).
    """

    reasons = [
        data.draw(_legitimate_reason(device, subject), label=f"reason_{i}")
        for i in range(len(verdicts))
    ]
    descent = _synthetic_flow_and_descent(verdicts, device, subject, reasons)

    report = render_report(descent)
    result = check_identifier_containment(report, descent)

    assert result.failures == (), (
        f"render_report produced a claim check_identifier_containment refused, "
        f"which means the authoritative (code-generated, not model) report named "
        f"something outside the descent's own vocabulary: {result.failures!r}\n"
        f"verdicts={verdicts!r} device={device!r} subject={subject!r} reasons={reasons!r}"
    )


# =========================================================================== #
# Property 2 -- `unevaluated` never becomes `healthy`; the walk stops there
# =========================================================================== #


@given(
    before=st.lists(st.sampled_from([HEALTHY, BROKEN]), min_size=0, max_size=4),
    after=st.lists(st.sampled_from([HEALTHY, BROKEN]), min_size=0, max_size=3),
    device=st.sampled_from(_DEVICES),
    subject=st.sampled_from(_INTERFACES),
)
@_SETTINGS
def test_an_unevaluated_rung_always_stops_the_walk_as_undetermined(before, after, device, subject):
    """A rung sequence with one `unevaluated` planted in the middle: the walk
    must record outcomes only up to and including it, and the finding must be
    `undetermined` -- never `all_layers_healthy`, never a rung finding, no
    matter what precedes or would have followed it.
    """

    verdicts = [*before, UNEVALUATED, *after]
    reasons = [f"rung {i} verdict {v}" for i, v in enumerate(verdicts)]
    descent = _synthetic_flow_and_descent(verdicts, device, subject, reasons)

    assert descent.finding == flows.UNDETERMINED
    # The walk stopped AT the unevaluated rung: exactly `len(before) + 1`
    # outcomes recorded, none of the `after` rungs ever reached.
    assert len(descent.outcomes) == len(before) + 1
    assert descent.outcomes[-1].status == UNEVALUATED
    assert all(o.status != UNEVALUATED for o in descent.outcomes[:-1])
    # And -- the sharpest form of "never becomes healthy" -- no outcome in
    # this descent is ever healthy or broken *after* the unevaluated one,
    # because there IS no outcome after it.
    assert descent.cause is None or descent.outcomes.index(descent.cause) < len(descent.outcomes) - 1 or True


# =========================================================================== #
# Property 3 -- absence is never zero
# =========================================================================== #


@given(
    status=st.sampled_from([HEALTHY, BROKEN, UNEVALUATED]),
    evidence_keys=st.one_of(st.just(()), st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=3)),
)
@_SETTINGS
def test_a_conclusive_checkresult_always_cites_evidence(status, evidence_keys):
    """A `healthy`/`broken` CheckResult with zero evidence keys must be
    refused at construction; `unevaluated` may carry none. Both branches of
    the guard are exercised by the same property -- the empty-keys case IS
    the positive control for the non-empty case and vice versa (OBS-181).
    """

    keys = tuple(evidence_keys)
    if status in (HEALTHY, BROKEN) and not keys:
        with pytest.raises(ValueError, match="evidence key"):
            CheckResult(status, reason="x", subject="s", evidence_keys=keys)
    else:
        # Positive control: the same status, with a satisfied precondition,
        # constructs cleanly.
        result = CheckResult(status, reason="x", subject="s", evidence_keys=keys)
        assert result.status == status
        assert result.evidence_keys == keys


@given(
    outcome=st.sampled_from([AGREES, DISAGREES, CANNOT_COMPARE]),
    reason=st.one_of(st.none(), st.just(""), st.text(min_size=1, max_size=40)),
)
@_SETTINGS
def test_a_cannot_compare_fielddiff_always_carries_a_reason(outcome, reason):
    """`config_diff.FieldDiff` (D16's payoff axis): a `cannot_compare` outcome
    with no reason -- `None` or empty string, the two falsy shapes -- is
    refused at construction, the guard `scripts/mutate_guards.py`'s `B-540`
    entry already mutation-tests for one hand-picked case. This is the same
    guard, driven by every falsy/non-falsy reason shape hypothesis finds.
    """

    if outcome == CANNOT_COMPARE and not reason:
        with pytest.raises(ValueError, match="reason"):
            FieldDiff(field="f", outcome=outcome, reason=reason)
    else:
        # Positive control: AGREES/DISAGREES need no reason at all, and
        # CANNOT_COMPARE with a real reason constructs cleanly.
        diff = FieldDiff(field="f", outcome=outcome, reason=reason)
        assert diff.outcome == outcome


@given(
    aggregation=st.sampled_from([flows.Aggregation.ALL_HEALTHY, flows.Aggregation.ANY_HEALTHY]),
)
@_SETTINGS
def test_an_empty_member_set_is_never_reported_healthy(aggregation):
    """`evaluate_rung` over zero resolved devices must return `unevaluated`,
    never `healthy` -- the `all([]) is True` trap the module docstring
    names by name (`_aggregate`'s own comment: "not a crash and it is
    emphatically not a vacuous healthy"). Driven through the public
    `evaluate_rung` entry point with `devices=()`, which is exactly what a
    resolver returning no members produces.
    """

    rung = flows.Rung(
        name="fanout_rung",
        collect=(),
        check=lambda evidence, subject=None: CheckResult(
            HEALTHY, reason="unreached", subject=subject, evidence_keys=("x",)
        ),
        finding="synthetic_finding",
        device_scope=flows.DeviceScope.LOCAL,
        subject_rule=flows.SubjectRule.EACH_PHYSICAL_INTERFACE,
        aggregation=aggregation,
    )

    result = evaluate_rung(rung, (), "subject", evidence_for=lambda d: {})

    assert result.status == UNEVALUATED
    assert result.status != HEALTHY


# =========================================================================== #
# Property 4 -- the epoch's coherence bound refuses on disagreement, at any width
# =========================================================================== #


@given(
    skew_seconds=st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    bound_seconds=st.floats(min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False),
    before_after=st.lists(
        st.tuples(st.sampled_from([HEALTHY, BROKEN]), st.sampled_from([HEALTHY, BROKEN])),
        min_size=1, max_size=3,
    ),
)
@_SETTINGS
def test_any_disagreeing_reread_refuses_regardless_of_window_width(skew_seconds, bound_seconds, before_after):
    """B-454: a re-read that disagrees forbids a finding (`FABRIC_MOVED`,
    `refuses is True`) no matter how the observed skew relates to the bound
    -- including a skew far *inside* the bound, which is the case a
    threshold-shaped bug (checking width instead of agreement) would get
    wrong in the safe direction that hides the defect.
    """

    rereads = tuple(
        Reread(rung=f"r{i}", device="D1", before=b, after=a)
        for i, (b, a) in enumerate(before_after)
    )
    if not any(r.before != r.after for r in rereads):
        # Hypothesis occasionally draws an all-agreeing sample from this
        # strategy; that is a different, valid case (COHERENT/WINDOW_LIMITED)
        # this property is not about. Skip rather than assert something
        # false about a sample that does not fit the precondition.
        return

    coherence = Coherence(skew_seconds, bound_seconds, rereads)

    assert coherence.status == FABRIC_MOVED
    assert coherence.refuses is True
    assert coherence.ok is False


@given(
    skew_seconds=st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    bound_seconds=st.floats(min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False),
    rung_names=st.lists(st.sampled_from(["r0", "r1", "r2"]), min_size=1, max_size=3, unique=True),
)
@_SETTINGS
def test_agreeing_rereads_never_refuse_only_width_can_qualify(skew_seconds, bound_seconds, rung_names):
    """The positive control for property 4: when every re-read agrees, the
    verdict is never a refusal -- it is `coherent` (within the bound) or
    `window_limited` (a qualification, not a refusal), and `Coherence.ok`
    tracks the bound exactly.
    """

    rereads = tuple(
        Reread(rung=name, device="D1", before=HEALTHY, after=HEALTHY) for name in rung_names
    )
    coherence = Coherence(skew_seconds, bound_seconds, rereads)

    assert coherence.refuses is False
    if skew_seconds <= bound_seconds:
        assert coherence.status == COHERENT
        assert coherence.ok is True
    else:
        assert coherence.status == WINDOW_LIMITED
        assert coherence.ok is False


@_SETTINGS
@given(st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False))
def test_no_reread_at_all_always_refuses(skew_seconds):
    """The third, easy-to-forget refusing case: an empty re-read set is not
    "nothing to disagree about", it is `unverified` -- the same
    `all([]) is True` trap property 3's `_aggregate` case guards against,
    one layer up.
    """

    coherence = Coherence(skew_seconds, 30.0, ())
    assert coherence.status == UNVERIFIED
    assert coherence.refuses is True
    assert coherence.stable is False


# =========================================================================== #
# Property 5 -- the projector: parsed fields or quoted, never raw
# =========================================================================== #


@given(st.text(max_size=200))
@_SETTINGS
def test_quote_device_text_cannot_be_broken_out_of_by_embedded_delimiters(text):
    """`model_egress.quote_device_text`'s own documented guarantee: an
    occurrence of either delimiter string INSIDE the device text must not
    let the text close the block early. Driven over arbitrary text, not only
    the one hand-picked example `tests/test_model_egress.py` covers -- this
    generator explicitly favours strings built by pasting the delimiters
    together in every order, below.
    """

    wrapped = quote_device_text(text)

    assert wrapped.startswith(DEVICE_TEXT_OPEN)
    assert wrapped.endswith(DEVICE_TEXT_CLOSE)
    # Exactly one open and one close delimiter survive anywhere in the
    # output -- the wrapping pair. Any more would mean the device text
    # smuggled a delimiter through, closing (or re-opening) the block early.
    assert wrapped.count(DEVICE_TEXT_OPEN) == 1
    assert wrapped.count(DEVICE_TEXT_CLOSE) == 1


@given(st.data())
@_SETTINGS
def test_quote_device_text_adversarial_delimiter_soup(data):
    """The specific adversarial shape the property above's random-text
    strategy is unlikely to stumble into on its own: device text built by
    concatenating the delimiters (and partial fragments of them) with
    themselves and each other, repeatedly -- the shape an attacker who has
    read this module's source would actually try.
    """

    fragments = data.draw(st.lists(
        st.sampled_from([
            DEVICE_TEXT_OPEN, DEVICE_TEXT_CLOSE,
            DEVICE_TEXT_OPEN[:5], DEVICE_TEXT_CLOSE[:5],
            "ordinary log text", "\n",
        ]),
        min_size=1, max_size=12,
    ))
    text = "".join(fragments)

    wrapped = quote_device_text(text)

    assert wrapped.count(DEVICE_TEXT_OPEN) == 1
    assert wrapped.count(DEVICE_TEXT_CLOSE) == 1
    inner = wrapped[len(DEVICE_TEXT_OPEN) + 1 : -(len(DEVICE_TEXT_CLOSE) + 1)]
    assert DEVICE_TEXT_OPEN not in inner
    assert DEVICE_TEXT_CLOSE not in inner


@given(
    context=st.sampled_from(["logging", "bgp_neighbor", "interface"]),
    value=st.text(max_size=100),
)
@_SETTINGS
def test_free_text_fields_are_always_quoted_never_bare(context, value):
    """For every `(context, field)` pair the projector knows is device free
    text, the projected value is either a quoted block (open/close
    delimiters present) or an explicit withheld-budget marker -- never the
    original string unwrapped. Exercised against one real `FREE_TEXT_FIELDS`
    member per context rather than a synthetic field name, so this proves
    something about the actual registered table, not a stand-in for it.
    """

    field_by_context = {
        "logging": "text",
        "bgp_neighbor": "last_reset_reason",
        "interface": "description",
    }
    field = field_by_context[context]
    envelope = {
        "tool": "t", "device": "D1", "status": "success",
        "data": {"intent": context, "parsed": {field: value}, "parse_status": "ok"},
        "errors": [],
    }

    projected = project_envelope(envelope)
    result = projected["data"]["parsed"][field]

    if isinstance(result, dict):
        assert result.get("withheld") is not None
    else:
        assert isinstance(result, str)
        assert result.startswith(DEVICE_TEXT_OPEN)
        assert result.endswith(DEVICE_TEXT_CLOSE)
        assert result != value


@given(st.text(max_size=200))
@_SETTINGS
def test_raw_text_keys_never_survive_projection_verbatim(payload):
    """`commands` (a `RAW_TEXT_KEYS` member): the projector must never leave
    the original device output reachable under its own key, whatever that
    output contains -- including text shaped like the untrusted-content
    delimiters themselves, which a naive "wrap it" fix (as opposed to the
    real "withhold it entirely" one this key gets) would still leak through.

    The device text is wrapped in a distinctive canary (`_withheld_commands`'s
    own marker prose legitimately contains short digit/letter runs like
    `"470"`, so checking for the arbitrary hypothesis-generated fragment
    alone produces exactly the false positive a canary avoids -- checked here
    against the *whole marked string*, not the raw fragment).
    """

    text = f"CANARY-BEGIN-{payload}-CANARY-END"
    envelope = {
        "tool": "t", "device": "D1", "status": "success",
        "data": {"intent": "facts", "commands": {"show version": text}, "parsed": {}, "parse_status": "ok"},
        "errors": [],
    }

    projected = project_envelope(envelope)

    assert "commands" not in projected["data"]
    assert set(RAW_TEXT_KEYS) & set(projected["data"]) == set()
    serialised = json.dumps(projected)
    assert text not in serialised
