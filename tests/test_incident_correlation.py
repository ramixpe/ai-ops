"""Cross-alert correlation (B-486).

The design this file pins down: correlation only ever asserts what it can
defend. ``correlate_by_cause`` is the one basis strong enough to produce an
``Incident`` claiming shared root cause, and it is deliberately conservative
about it -- the positive-control pair right below (OBS-181) proves the
mechanism merges when it legitimately can, and the test beside it proves the
mechanism refuses to guess when the discriminating fact (the cause's
subject) is missing, rather than silently treating "missing" as "matches
anything".
"""

from __future__ import annotations

from agent_nettools import incident_correlation as C


def _diag(id, device, subject, flow="bgp_session", finding="bgp_session_down",
          cause_rung=None, cause_device=None, cause_subject=None,
          run_id=None) -> C.DiagnosisRef:
    return C.DiagnosisRef(
        id=id, device=device, subject=subject, flow=flow, finding=finding,
        cause_rung=cause_rung, cause_device=cause_device, cause_subject=cause_subject,
        run_id=run_id,
    )


# --------------------------------------------------------------------------- #
# correlate_by_cause -- the strongest basis, and its positive control
# --------------------------------------------------------------------------- #


def test_two_diagnoses_on_the_identical_broken_rung_are_one_incident():
    """Positive control (OBS-181): proves the merge mechanism actually fires
    when the shared cause AND the shared run_id are genuinely known, so the
    refusal tests below (missing subject, missing run_id) are not merely a
    mechanism that never merges anything."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", finding="bgp_session_down",
              cause_rung="interface_state", cause_device="PE2",
              cause_subject="Gi0/0/0/0", run_id="run-1"),
        _diag("d2", "RR2", "10.255.0.12", finding="bgp_session_down",
              cause_rung="interface_state", cause_device="PE2",
              cause_subject="Gi0/0/0/0", run_id="run-2"),
    ]

    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)

    assert len(incidents) == 1
    assert excluded_subject == []
    assert excluded_run_id == []
    incident = incidents[0]
    assert incident.basis == C.BASIS_SAME_CAUSE
    assert {d.id for d in incident.members} == {"d1", "d2"}
    assert "interface_state" in incident.reason
    assert "PE2" in incident.reason


def test_a_singleton_cause_produces_no_incident():
    diagnoses = [_diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
                       cause_device="PE2", cause_subject="Gi0/0/0/0", run_id="run-1")]
    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)
    assert incidents == []
    assert excluded_subject == []
    assert excluded_run_id == []


def test_a_diagnosis_with_no_cause_never_participates():
    """`all_layers_healthy` / `undetermined` runs (`cause_rung is None`)."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12"),
        _diag("d2", "RR2", "10.255.0.12"),
    ]
    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)
    assert incidents == []
    assert excluded_subject == []
    assert excluded_run_id == []


def test_same_rung_and_device_but_different_subjects_are_not_merged():
    """The dangerous case: two DIFFERENT interfaces on the same device
    tripping the same rung name are two different faults, not one."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id="run-1"),
        _diag("d2", "RR2", "10.255.0.13", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/1", run_id="run-2"),
    ]
    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)
    assert incidents == []
    assert excluded_subject == []
    assert excluded_run_id == []


def test_same_rung_but_different_cause_device_is_not_merged():
    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id="run-1"),
        _diag("d2", "RR2", "10.255.0.13", cause_rung="interface_state",
              cause_device="PE3", cause_subject="Gi0/0/0/0", run_id="run-2"),
    ]
    incidents, _, _ = C.correlate_by_cause(diagnoses)
    assert incidents == []


def test_an_unknown_cause_subject_is_excluded_never_treated_as_a_wildcard():
    """The safety property the whole design rests on. Two diagnoses that
    WOULD merge if their subjects were known and equal (same device, same
    rung -- exactly the positive control above, minus the subject) must NOT
    merge just because both subjects happen to be unrecorded: that would
    silently hide a genuine second incident (a different interface on the
    same device, same rung) inside a merged one. run_id is present on both
    here so this test isolates the subject-missing path from the
    run_id-missing one tested separately below."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject=None, run_id="run-1"),
        _diag("d2", "RR2", "10.255.0.13", cause_rung="interface_state",
              cause_device="PE2", cause_subject=None, run_id="run-2"),
    ]

    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)

    assert incidents == []
    assert {d.id for d in excluded_subject} == {"d1", "d2"}
    assert excluded_run_id == []


def test_an_unknown_run_id_is_excluded_never_merged_despite_matching_cause():
    """The run_id counterpart of the test above: two diagnoses whose device,
    rung AND subject all agree (they WOULD merge per the positive control)
    are still refused when run_id is unrecorded -- an incident this module
    asserts must be traceable back to the concrete investigation that
    produced each member, and a diagnosis with no run_id cannot be."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id=None),
        _diag("d2", "RR2", "10.255.0.13", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id=None),
    ]

    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)

    assert incidents == []
    assert excluded_subject == []
    assert {d.id for d in excluded_run_id} == {"d1", "d2"}


def test_a_diagnosis_missing_both_subject_and_run_id_is_counted_in_both_lists():
    """Absence is never zero: a row missing two facts is missing both of
    them, and must be visible in both exclusion counts -- not just the first
    one checked."""

    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject=None, run_id=None),
    ]

    _, excluded_subject, excluded_run_id = C.correlate_by_cause(diagnoses)

    assert [d.id for d in excluded_subject] == ["d1"]
    assert [d.id for d in excluded_run_id] == ["d1"]


def test_ledger_adapter_never_fabricates_a_cause_subject_or_run_id_so_correlation_stays_conservative():
    """End-to-end through the real adapter, against an OLD-format ledger row
    (no `cause.subject`, no top-level `run_id` -- exactly what every row
    written before W3a looks like): two ledger-sourced diagnoses that share a
    rung and device must still refuse to merge, exactly like the direct
    tests above, and both exclusions must be counted."""

    ledger_rows = [
        {"id": "d1", "device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session",
         "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down"}},
        {"id": "d2", "device": "RR2", "subject": "10.255.0.13", "flow": "bgp_session",
         "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down"}},
    ]
    refs = C.from_ledger_diagnoses(ledger_rows)
    assert all(r.run_id is None for r in refs), "old-format rows carry no run_id"

    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(refs)

    assert incidents == []
    assert len(excluded_subject) == 2
    assert len(excluded_run_id) == 2


def test_ledger_adapter_merges_new_format_rows_carrying_subject_and_run_id():
    """Positive control at the adapter level: once a ledger row carries both
    `cause.subject` and `run_id` (W3a's schema), two rows sharing a cause
    correlate exactly as the direct positive control above does -- the gap
    this module's docstring described is actually closed, not just excluded
    more gracefully."""

    ledger_rows = [
        {"id": "d1", "run_id": "run-1", "device": "RR1", "subject": "10.255.0.12",
         "flow": "bgp_session", "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down",
                    "subject": "Gi0/0/0/0"}},
        {"id": "d2", "run_id": "run-2", "device": "RR2", "subject": "10.255.0.13",
         "flow": "bgp_session", "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down",
                    "subject": "Gi0/0/0/0"}},
    ]
    refs = C.from_ledger_diagnoses(ledger_rows)

    incidents, excluded_subject, excluded_run_id = C.correlate_by_cause(refs)

    assert excluded_subject == []
    assert excluded_run_id == []
    assert len(incidents) == 1
    assert {d.id for d in incidents[0].members} == {"d1", "d2"}


def test_correlate_over_a_mixed_old_and_new_format_ledger_file():
    """The realistic case: one ledger file accumulating rows from before and
    after W3a. New rows sharing a cause merge; old rows never do, and both
    kinds of absence are counted, never silently dropped from the picture."""

    ledger_rows = [
        # Two OLD-format rows: same device+rung, no subject, no run_id.
        {"id": "old1", "device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session",
         "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down"}},
        {"id": "old2", "device": "RR2", "subject": "10.255.0.13", "flow": "bgp_session",
         "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE2", "reason": "down"}},
        # Two NEW-format rows sharing a genuine cause: these must merge.
        {"id": "new1", "run_id": "run-a", "device": "RR3", "subject": "10.255.0.14",
         "flow": "bgp_session", "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE3", "reason": "down",
                    "subject": "Gi0/0/0/2"}},
        {"id": "new2", "run_id": "run-b", "device": "RR4", "subject": "10.255.0.15",
         "flow": "bgp_session", "finding": "interface_line_down",
         "cause": {"rung": "interface_state", "device": "PE3", "reason": "down",
                    "subject": "Gi0/0/0/2"}},
    ]
    refs = C.from_ledger_diagnoses(ledger_rows)

    result = C.correlate(refs)

    assert len(result.incidents) == 1
    assert {d.id for d in result.incidents[0].members} == {"new1", "new2"}
    assert {d.id for d in result.excluded_for_missing_subject} == {"old1", "old2"}
    assert {d.id for d in result.excluded_for_missing_run_id} == {"old1", "old2"}
    assert result.counts == {"rows_without_subject": 2, "rows_without_run_id": 2}


# --------------------------------------------------------------------------- #
# correlate_by_subject / correlate_by_device -- clusters, no causal claim
# --------------------------------------------------------------------------- #


def test_same_subject_different_causes_clusters_without_claiming_shared_cause():
    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", flow="bgp_session",
              cause_rung="bgp_transport", cause_device="RR1", cause_subject="10.255.0.12"),
        _diag("d2", "PE2", "10.255.0.12", flow="interface",
              cause_rung="interface_state", cause_device="PE2", cause_subject="Gi0/0/0/0"),
    ]
    clusters = C.correlate_by_subject(diagnoses)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.basis == C.BASIS_SAME_SUBJECT
    assert {d.id for d in cluster.members} == {"d1", "d2"}
    assert "does not assert" in cluster.reason


def test_different_subjects_are_not_clustered_together():
    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12"),
        _diag("d2", "RR2", "10.255.0.99"),
    ]
    assert C.correlate_by_subject(diagnoses) == []


def test_same_device_different_faults_clusters_for_triage_without_claiming_shared_cause():
    diagnoses = [
        _diag("d1", "PE2", "10.255.0.12", flow="bgp_session",
              cause_rung="bgp_transport", cause_device="PE2", cause_subject="10.255.0.12"),
        _diag("d2", "PE2", "Gi0/0/0/1", flow="interface",
              cause_rung="interface_state", cause_device="PE2", cause_subject="Gi0/0/0/1"),
    ]
    clusters = C.correlate_by_device(diagnoses)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.basis == C.BASIS_SAME_DEVICE
    assert {d.id for d in cluster.members} == {"d1", "d2"}
    assert "does not assert" in cluster.reason


# --------------------------------------------------------------------------- #
# correlate -- the tiered orchestration
# --------------------------------------------------------------------------- #


def test_correlate_puts_shared_cause_in_incidents_and_the_rest_in_clusters():
    diagnoses = [
        _diag("d1", "RR1", "10.255.0.12", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id="run-1"),
        _diag("d2", "RR2", "10.255.0.13", cause_rung="interface_state",
              cause_device="PE2", cause_subject="Gi0/0/0/0", run_id="run-2"),
        _diag("d3", "PE2", "10.255.0.14"),  # no cause; still clusters by device with... nobody
    ]
    result = C.correlate(diagnoses)

    assert len(result.incidents) == 1
    assert {d.id for d in result.incidents[0].members} == {"d1", "d2"}
    assert result.incidents[0].basis == C.BASIS_SAME_CAUSE
    # d3 shares no device or subject with anything else, so it forms no
    # cluster either -- it simply does not appear in any group's members.
    clustered_ids = {m.id for cluster in result.clusters for m in cluster.members}
    assert "d3" not in clustered_ids


def test_excluded_bases_document_the_refused_correlations():
    """Time-proximity correlation is named as refused, and this module
    genuinely never computes a timestamp delta anywhere in its public API."""

    result = C.correlate([])
    joined = " ".join(result.excluded_bases).lower()
    assert "time" in joined or "proximity" in joined
    assert "30 seconds" in " ".join(result.excluded_bases)
    # No function in this module's public surface takes two timestamps and a
    # window -- the exclusion is structural, not just documented prose.
    assert not any("time" in name.lower() or "window" in name.lower() for name in C.__all__)


def test_correlate_with_no_diagnoses_is_empty_not_an_error():
    result = C.correlate([])
    assert result.incidents == ()
    assert result.clusters == ()
    assert result.excluded_for_missing_subject == ()
    assert result.excluded_for_missing_run_id == ()
    assert result.counts == {"rows_without_subject": 0, "rows_without_run_id": 0}
