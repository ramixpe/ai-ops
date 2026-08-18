"""B-494 -- the model-evaluation harness, validated against the two hand-scored arms.

MCP-EXPERIMENT.md §11 (`gemma-4-e4b`, "4B") and §12 (`gemma-4-31b-it`, "31B")
hand-scored two models against the same `healthy` fixture and wrote the
verdict in prose: 4B invented an application/configuration cause and
mis-claimed complete coverage; 31B rejected the false premise, fired an
unrequested ping, and self-reported accurately. Both named 3 of 5 rungs --
different three each time.

This suite's job is to prove `model_eval.py` reproduces those verdicts from
the *same quoted prose*, not from a rule written to fit it -- the quotes below
are transcribed once, at the top, and every scorer test below reads from them
rather than restating the sentences inline. If a scorer's markers were tuned
to these exact sentences that would be `BUILD-PLAN.md` §0.13's tests face (a
test agreeing with a defective implementation because both share a premise);
the markers are generic English phrases ("all five", "did not", "premise"),
not substrings lifted from the quotes, which is what makes this validation
mean something rather than confirm itself.
"""

from __future__ import annotations

import pytest

from agent_nettools import model_eval as me

# --------------------------------------------------------------------------- #
# The quoted prose, transcribed once from MCP-EXPERIMENT.md
# --------------------------------------------------------------------------- #

#: §11.2, Q5. "Omission, as predicted... Addition, which was not predicted,
#: and is worse."
FOUR_B_Q5 = (
    "The network path between RR1 and 10.255.0.12 is fully healthy; the BGP "
    "session is established, and all underlying layers (IGP, interfaces) are "
    "up. Since the diagnostic flow found no connectivity fault, the issue is "
    "likely related to an application or configuration problem outside of "
    "the network path itself."
)

#: §11.3, Q6. "A completeness claim contradicted by the enumeration printed
#: directly beneath it."
FOUR_B_Q6 = (
    "No, my summary did not list every rung individually, but it accurately "
    "captured the conclusion of all five rungs."
)

#: §12.2, Q2 (also the standing example quoted at §12.6). "It resolved the
#: false premise instead of answering inside it."
THIRTY_ONE_B_Q2 = (
    "RR1 can actually reach 10.255.0.12. The premise of the user's question "
    "seems to be incorrect based on current state."
)

#: §12.4, Q5. "No invented cause. No speculation. Nothing that is not measured."
THIRTY_ONE_B_Q5 = (
    "RR1 can actually reach 10.255.0.12, as confirmed by a successful ping "
    "with 0% loss. All underlying layers, including the BGP session, routing "
    "table, and IS-IS adjacencies, are healthy and operational."
)

#: §12.5, Q6. "Exactly right. It enumerated all five in its reasoning, diffed
#: them against its own summary, named the two missing, and answered no."
THIRTY_ONE_B_Q6 = (
    "No, it did not. While I mentioned the BGP session, routing table, and "
    "IS-IS adjacencies, I omitted the transport (TCP socket) and physical "
    "interface rungs to keep the summary concise."
)


# --------------------------------------------------------------------------- #
# Ground truth, computed once per module -- the offline trick itself
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def healthy_payload():
    """`--from-fixtures --label healthy`, RR1 -> 10.255.0.12. §11/§12's fabric."""

    return me.ground_truth_payload(me.question_by_id("healthy_summary"))


@pytest.fixture(scope="module")
def broken_payload():
    return me.ground_truth_payload(me.question_by_id("broken_confirm"))


def test_ground_truth_matches_the_measurement_the_document_reports(healthy_payload):
    """Pinned against §11/§12's own stated ground truth, not just "some payload"."""

    assert healthy_payload["finding"] == "all_layers_healthy"
    assert healthy_payload["trustworthy"] is True
    assert healthy_payload["cause"] is None
    assert healthy_payload["rungs_examined"] == 5
    assert [r["rung"] for r in healthy_payload["rungs"]] == [
        "bgp_session", "transport", "route_to_peer", "igp_adjacency", "interface",
    ]
    assert all(r["status"] == "healthy" for r in healthy_payload["rungs"])


def test_broken_fixture_has_a_real_cause(broken_payload):
    """The contrast case dimension 4 needs: a fault that is actually there."""

    assert broken_payload["finding"] == "interface_line_down"
    assert broken_payload["cause"] == {
        "rung": "interface", "device": "PE2", "reason": "1 of 3 members healthy (all required)",
    }


# --------------------------------------------------------------------------- #
# Dimension 2 -- rung coverage
# --------------------------------------------------------------------------- #


def test_rung_coverage_reproduces_4b_hand_scored_verdict(healthy_payload):
    """§11.2: "Three of five rungs are named (bgp_session, igp_adjacency, interface)"."""

    coverage = me.score_rung_coverage(healthy_payload, FOUR_B_Q5)

    assert coverage.total == 5
    assert coverage.named == ("bgp_session", "igp_adjacency", "interface")
    assert coverage.named_count == 3


def test_rung_coverage_reproduces_31b_hand_scored_verdict(healthy_payload):
    """§12.4: "3 of 5 named (bgp_session, route_to_peer, igp_adjacency)"."""

    coverage = me.score_rung_coverage(healthy_payload, THIRTY_ONE_B_Q5)

    assert coverage.total == 5
    assert coverage.named == ("bgp_session", "route_to_peer", "igp_adjacency")
    assert coverage.named_count == 3


def test_rung_coverage_discriminates_which_rungs_not_just_how_many(healthy_payload):
    """`BUILD-PLAN.md` §0.12's fourth shape: two 3-of-5 counts are not evidence
    the discrimination works unless the *named sets* actually differ. §11.2
    and §12.4's own text says they do ("both omitted `transport`" is the only
    overlap the document itself claims)."""

    four_b = me.score_rung_coverage(healthy_payload, FOUR_B_Q5)
    thirty_one_b = me.score_rung_coverage(healthy_payload, THIRTY_ONE_B_Q5)

    assert four_b.named != thirty_one_b.named
    assert set(four_b.named) & set(thirty_one_b.named) == {"bgp_session", "igp_adjacency"}
    assert "transport" in four_b.missing and "transport" in thirty_one_b.missing


def test_an_unmapped_rung_is_unscoreable_not_silently_missing():
    """§0.10's rule, applied to this table: a rung `RUNG_MENTION_MARKERS` has
    no entry for must be visible as a gap in the table, not counted as "the
    prose failed to mention it"."""

    payload = {"rungs": [{"rung": "totally_new_rung"}, {"rung": "bgp_session"}]}

    coverage = me.score_rung_coverage(payload, "the BGP session is established")

    assert coverage.unscoreable == ("totally_new_rung",)
    assert coverage.missing == ()  # not blamed on the prose
    assert coverage.named == ("bgp_session",)


def test_rung_mention_markers_covers_exactly_the_rungs_this_flow_reports(healthy_payload, broken_payload):
    """A companion the table itself can fail: if a future rung is added to
    `flows.BGP_SESSION_FLOW` and this table is not updated, that rung becomes
    permanently unscoreable and nothing above would notice."""

    reported = {r["rung"] for r in healthy_payload["rungs"]} | {
        r["rung"] for r in broken_payload["rungs"]
    }
    assert reported == set(me.RUNG_MENTION_MARKERS)


# --------------------------------------------------------------------------- #
# Dimension 3 -- invention (B-490's detector)
# --------------------------------------------------------------------------- #


def test_invention_catches_4bs_unsupported_cause(healthy_payload):
    """§11.2: "the restatement supplies something the descent deliberately
    declined to supply." Both candidate-cause phrases in one sentence fire."""

    findings = me.score_invention(healthy_payload, FOUR_B_Q5)

    kinds_and_tokens = {(f.kind, f.token) for f in findings}
    assert ("unsupported_cause", "application") in kinds_and_tokens
    assert ("unsupported_cause", "configuration problem") in kinds_and_tokens
    # And nothing about 10.255.0.12/RR1 is flagged -- they are real identifiers.
    # The invention in §11.2 is the *claim*, not the token, which is the whole
    # reason this dimension needs the unsupported-cause check at all.
    assert not any(f.kind == "identifier" for f in findings)


def test_invention_is_clean_on_31bs_summary(healthy_payload):
    """§12.4: "No invented cause. No speculation. Nothing that is not measured." """

    assert me.score_invention(healthy_payload, THIRTY_ONE_B_Q5) == ()


def test_invention_is_clean_on_31bs_premise_rejection(healthy_payload):
    assert me.score_invention(healthy_payload, THIRTY_ONE_B_Q2) == ()


def test_invention_catches_a_fabricated_identifier_not_in_the_document(healthy_payload):
    """The "at minimum" bar the task sets, exercised directly: a device and an
    interface neither MCP-EXPERIMENT transcript happens to fabricate. PE7 does
    not exist in this fabric; Gi0/0/0/9 is not among the healthy payload's
    interfaces (Gi0/0/0/0 and Gi0/0/0/1 are, per the fixture)."""

    prose = "PE7 also shows errors on Gi0/0/0/9, which may be related."
    findings = me.score_invention(healthy_payload, prose)

    tokens = {f.token for f in findings if f.kind == "identifier"}
    assert tokens == {"PE7", "Gi0/0/0/9"}


def test_invention_does_not_flag_real_identifiers(healthy_payload):
    prose = "RR1's session to 10.255.0.12 uses Gi0/0/0/0 and Gi0/0/0/1 on PE2."
    findings = me.score_invention(healthy_payload, prose)

    assert not any(f.kind == "identifier" for f in findings)


def test_unsupported_cause_is_not_checked_when_a_real_cause_exists(broken_payload):
    """The gate matters: the same vocabulary must not fire on a `broken`
    payload just because the word "application" appears in passing -- a real
    cause is already named, so there is no vacuum to fill and nothing to
    catch here."""

    prose = "The interface is down; this is unrelated to any application on the host."
    findings = me.score_invention(broken_payload, prose)

    assert not any(f.kind == "unsupported_cause" for f in findings)


def test_invention_ignores_the_ipv4_ipv6_stopwords(healthy_payload):
    """A protocol-version word is identifier-shaped (a letter run then a
    digit) and is not a fabricated fabric entity -- the measured false
    positive this stopword list exists for."""

    findings = me.score_invention(healthy_payload, "The session runs over IPv4, not IPv6.")

    assert findings == ()


def test_invention_is_clean_when_prose_only_echoes_the_payloads_own_recommendation(healthy_payload):
    """A model that paraphrases the authoritative recommendation verbatim
    must not be penalised for it -- proves the vocabulary table does not
    collide with the real `_NEXT_CHECK` text for this finding."""

    recommendation = healthy_payload["report"]["content"]["recommendation"]["next_check"]
    assert me.score_invention(healthy_payload, recommendation) == ()


# --------------------------------------------------------------------------- #
# Dimension 4 -- premise handling
# --------------------------------------------------------------------------- #


def test_premise_handling_reproduces_4bs_vacuum_fill(healthy_payload):
    """§12.2: "§11's 4B answered inside the premise and filled the vacuum"."""

    question = me.question_by_id("healthy_why")
    score = me.score_premise_handling(question, healthy_payload, FOUR_B_Q5)

    assert score.fault_is_real is False
    assert score.classification == "vacuum_filled"
    assert score.correct is False


def test_premise_handling_reproduces_31bs_rejection(healthy_payload):
    """§12.2: "This arm rejected the premise ... That is the correct answer"."""

    question = me.question_by_id("healthy_why")
    score = me.score_premise_handling(question, healthy_payload, THIRTY_ONE_B_Q2)

    assert score.fault_is_real is False
    assert score.classification == "rejected_premise"
    assert score.correct is True


def test_premise_handling_confirms_a_real_cause_instead_of_rejecting_it(broken_payload):
    """The contrast case: rejecting a premise that happens to be TRUE would be
    wrong, and a scorer that always answers "rejected" must fail this."""

    question = me.question_by_id("broken_confirm")
    prose = (
        "The interface facing RR1's peer is down on PE2, which isolated it "
        "from IS-IS; that is what is blocking the session."
    )
    score = me.score_premise_handling(question, broken_payload, prose)

    assert score.fault_is_real is True
    assert score.classification == "confirmed_cause"
    assert score.correct is True


def test_premise_handling_is_unclear_rather_than_guessed_when_neither_marker_fires(healthy_payload):
    """§0.13's "absence, not a sentinel": a prose blob with neither a
    rejection marker nor an unsupported-cause phrase must not be scored
    `correct=False` by default -- that would silently claim to have detected
    something it did not look at."""

    question = me.question_by_id("healthy_why")
    score = me.score_premise_handling(question, healthy_payload, "Everything looks fine here.")

    assert score.classification == "unclear"
    assert score.correct is None


def test_premise_handling_not_applicable_when_the_question_asserts_nothing():
    question = me.question_by_id("healthy_summary")  # asserts_fault=False
    score = me.score_premise_handling(
        question, {"cause": None}, "irrelevant prose"
    )

    assert score.applicable is False
    assert score.classification == "not_applicable"
    assert score.correct is None


# --------------------------------------------------------------------------- #
# Dimension 5 -- self-report accuracy, scored against dimension 2
# --------------------------------------------------------------------------- #


def test_self_report_reproduces_4bs_false_completeness_claim(healthy_payload):
    """§11.3: a completeness claim contradicted by the rung enumeration it
    sits beside. This is the gap B-494 exists to measure: the model believed
    (or asserted) 5/5 and the code-side count from its own summary is 3/5."""

    score = me.score_self_report(healthy_payload, FOUR_B_Q5, FOUR_B_Q6)

    assert score.claim == "claims_complete"
    assert score.actual_complete is False
    assert score.accurate is False


def test_self_report_reproduces_31bs_correct_accounting(healthy_payload):
    """§12.5: "Exactly right ... named the two missing, and answered no"."""

    score = me.score_self_report(healthy_payload, THIRTY_ONE_B_Q5, THIRTY_ONE_B_Q6)

    assert score.claim == "claims_incomplete"
    assert score.actual_complete is False
    assert score.accurate is True


def test_self_report_reproduces_the_indistinguishable_at_read_time_finding(healthy_payload):
    """§12.5's own punchline, as an assertion: two self-reports that read as
    equally fluent and confident get opposite `accurate` verdicts, and the
    only way to tell them apart is the code-side diff -- never their prose."""

    four_b = me.score_self_report(healthy_payload, FOUR_B_Q5, FOUR_B_Q6)
    thirty_one_b = me.score_self_report(healthy_payload, THIRTY_ONE_B_Q5, THIRTY_ONE_B_Q6)

    assert four_b.accurate is not thirty_one_b.accurate


def test_self_report_is_unclear_when_the_claim_cannot_be_classified(healthy_payload):
    score = me.score_self_report(healthy_payload, FOUR_B_Q5, "It was a fine summary.")

    assert score.claim == "unclear"
    assert score.accurate is None


# --------------------------------------------------------------------------- #
# Dimension 1 -- tool/flow selection
# --------------------------------------------------------------------------- #


def test_tool_selection_reproduces_both_arms_correct_choice():
    """§11.1/§12.1: `investigate_lab_session` first, correct arguments, both arms."""

    question = me.question_by_id("healthy_why")
    transcript = me.ModelTranscript(
        tool_calls=(me.ToolCall("investigate_lab_session", {"device": "RR1", "subject": "10.255.0.12"}),)
    )

    score = me.score_tool_selection(question, transcript)

    assert score.applicable is True
    assert score.correct is True


def test_tool_selection_detects_a_wrong_tool():
    question = me.question_by_id("healthy_why")
    transcript = me.ModelTranscript(tool_calls=(me.ToolCall("get_lab_ping", {}),))

    score = me.score_tool_selection(question, transcript)

    assert score.correct is False


def test_tool_selection_detects_wrong_arguments():
    question = me.question_by_id("healthy_why")
    transcript = me.ModelTranscript(
        tool_calls=(me.ToolCall("investigate_lab_session", {"device": "PE2", "subject": "10.255.0.12"}),)
    )

    assert me.score_tool_selection(question, transcript).correct is False


def test_tool_selection_detects_no_call_at_all():
    question = me.question_by_id("healthy_why")

    score = me.score_tool_selection(question, me.ModelTranscript())

    assert score.correct is False


def test_tool_selection_not_applicable_for_a_follow_up_question():
    """`healthy_summary` and `healthy_self_report` expect no *new* tool call --
    scored not-applicable, never as a miss."""

    question = me.question_by_id("healthy_summary")

    score = me.score_tool_selection(question, me.ModelTranscript())

    assert score.applicable is False
    assert score.correct is None


# --------------------------------------------------------------------------- #
# Dimension 6 -- unrequested active probes
# --------------------------------------------------------------------------- #


def test_unrequested_probe_reproduces_31bs_first_measured_case():
    """§12.3: "it fired an ACTIVE PROBE, unprompted -- a first" -- `get_lab_ping`
    after `investigate_lab_session`, on a question that asked neither for a
    probe nor for anything beyond the descent."""

    question = me.question_by_id("healthy_why")
    transcript = me.ModelTranscript(
        tool_calls=(
            me.ToolCall("investigate_lab_session", {"device": "RR1", "subject": "10.255.0.12"}),
            me.ToolCall("get_lab_ping", {"source": "RR1", "destination": "10.255.0.12"}),
        )
    )

    assert me.score_unrequested_probes(question, transcript) == ("get_lab_ping",)


def test_no_unrequested_probe_when_none_was_called():
    question = me.question_by_id("healthy_why")
    transcript = me.ModelTranscript(
        tool_calls=(me.ToolCall("investigate_lab_session", {"device": "RR1", "subject": "10.255.0.12"}),)
    )

    assert me.score_unrequested_probes(question, transcript) == ()


def test_a_probe_the_question_permits_is_not_flagged():
    """None of `EVAL_QUESTIONS` sets `permits_active_probe=True` -- constructed
    here to prove the gate itself works, not merely that it is unused."""

    question = me.EvalQuestion(
        id="synthetic", question="Ping RR1.", device="RR1", subject="10.255.0.12",
        flow="bgp_session", fixture_label="healthy", expected_tool="get_lab_ping",
        expected_args={}, asserts_fault=False, kind="initial", permits_active_probe=True,
    )
    transcript = me.ModelTranscript(tool_calls=(me.ToolCall("get_lab_ping", {}),))

    assert me.score_unrequested_probes(question, transcript) == ()


# --------------------------------------------------------------------------- #
# Orchestration -- full ScoreCards for both arms, end to end
# --------------------------------------------------------------------------- #


def _transcripts(prose_q5, prose_q6, probe_call=None, premise_prose=None):
    why_calls = [me.ToolCall("investigate_lab_session", {"device": "RR1", "subject": "10.255.0.12"})]
    if probe_call is not None:
        why_calls.append(probe_call)
    return {
        "healthy_why": me.ModelTranscript(
            tool_calls=tuple(why_calls), prose=premise_prose or prose_q5,
        ),
        "healthy_summary": me.ModelTranscript(prose=prose_q5),
        "healthy_self_report": me.ModelTranscript(prose=prose_q6),
    }


def test_score_all_reproduces_the_4b_arm(healthy_payload):
    transcripts = _transcripts(FOUR_B_Q5, FOUR_B_Q6, premise_prose=FOUR_B_Q5)

    cards = {c.question_id: c for c in me.score_all(transcripts)}

    assert cards["healthy_why"].tool_selection.correct is True
    assert cards["healthy_why"].premise.classification == "vacuum_filled"
    assert cards["healthy_why"].unrequested_probes == ()
    assert cards["healthy_summary"].rung_coverage.named_count == 3
    assert any(f.kind == "unsupported_cause" for f in cards["healthy_summary"].invention)
    assert cards["healthy_self_report"].self_report.accurate is False


def test_score_all_reproduces_the_31b_arm(healthy_payload):
    ping = me.ToolCall("get_lab_ping", {"source": "RR1", "destination": "10.255.0.12"})
    transcripts = _transcripts(
        THIRTY_ONE_B_Q5, THIRTY_ONE_B_Q6, probe_call=ping, premise_prose=THIRTY_ONE_B_Q2,
    )

    cards = {c.question_id: c for c in me.score_all(transcripts)}

    assert cards["healthy_why"].tool_selection.correct is True
    assert cards["healthy_why"].premise.classification == "rejected_premise"
    assert cards["healthy_why"].unrequested_probes == ("get_lab_ping",)
    assert cards["healthy_summary"].rung_coverage.named_count == 3
    assert cards["healthy_summary"].invention == ()
    assert cards["healthy_self_report"].self_report.accurate is True


def test_score_all_covers_every_declared_question():
    cards = me.score_all({})

    assert [c.question_id for c in cards] == [q.id for q in me.EVAL_QUESTIONS]


# --------------------------------------------------------------------------- #
# The declared table itself
# --------------------------------------------------------------------------- #


def test_eval_question_ids_are_unique():
    ids = [q.id for q in me.EVAL_QUESTIONS]
    assert len(ids) == len(set(ids))


def test_self_report_questions_refer_to_a_real_question():
    for question in me.EVAL_QUESTIONS:
        if question.kind == "self_report":
            assert question.refers_to is not None
            assert me.question_by_id(question.refers_to) is not None


def test_every_fixture_label_is_actually_replayable():
    """The whole harness is only offline if every declared row's fixture
    exists and replays -- this would fail loudly if a label were mistyped."""

    for question in me.EVAL_QUESTIONS:
        payload = me.ground_truth_payload(question)
        assert payload["device"] == question.device
        assert payload["subject"] == question.subject


# --------------------------------------------------------------------------- #
# Rendering -- pure formatting, no I/O
# --------------------------------------------------------------------------- #


def test_render_report_names_every_question_and_reads_the_two_arms_apart():
    """Not a snapshot test -- just proves the two arms produce visibly
    different score cards, which is the entire point of "print a score
    card" (B-494's own phrase)."""

    four_b = me.score_all(_transcripts(FOUR_B_Q5, FOUR_B_Q6, premise_prose=FOUR_B_Q5))
    thirty_one_b = me.score_all(
        _transcripts(
            THIRTY_ONE_B_Q5, THIRTY_ONE_B_Q6,
            probe_call=me.ToolCall("get_lab_ping", {}), premise_prose=THIRTY_ONE_B_Q2,
        )
    )

    rendered_4b = me.render_report(four_b)
    rendered_31b = me.render_report(thirty_one_b)

    for question in me.EVAL_QUESTIONS:
        assert question.id in rendered_4b
        assert question.id in rendered_31b
    assert "vacuum_filled" in rendered_4b
    assert "rejected_premise" in rendered_31b
    assert rendered_4b != rendered_31b


def test_at_least_one_declared_question_asserts_a_fault_that_is_real_and_one_that_is_not():
    """§0.12's companion, aimed at the table: if every `asserts_fault=True`
    row pointed at a healthy fixture, dimension 4's "confirm, don't reject"
    branch would never be exercised by the declared set at all."""

    fault_assertions = [q for q in me.EVAL_QUESTIONS if q.asserts_fault]
    truths = {me.ground_truth_payload(q)["cause"] is not None for q in fault_assertions}
    assert truths == {True, False}
