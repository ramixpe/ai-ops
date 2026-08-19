"""Tests for `scripts/variance_experiment.py` (B-207).

`scripts/` is deliberately not on `pythonpath` (see `test_mutate_guards.py`'s
own module docstring for why), so the script is loaded by path, the same
`importlib.util.spec_from_file_location` pattern that module already
established for this repo.

Every test here is offline: fixture replay for the deterministic-path
functions, and a scripted fake analyst (never a real model, never Ollama) for
the model-path functions -- the same `sender=`/scripted-callable seams
CLAUDE.md's "Testing seams" section names as preferred over mocking transport
internals. `scripts/variance_experiment.py` itself calls a real local Ollama
server when actually run; nothing in this file does.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "variance_experiment.py"
_spec = importlib.util.spec_from_file_location("variance_experiment", _PATH)
assert _spec is not None and _spec.loader is not None
ve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ve)


# --------------------------------------------------------------------------- #
# _decode / _prose_for_scoring
# --------------------------------------------------------------------------- #


def test_decode_parses_plain_json():
    assert ve._decode('{"a": 1}') == {"a": 1}


def test_decode_strips_a_markdown_fence():
    assert ve._decode('```json\n{"a": 1}\n```') == {"a": 1}


def test_decode_returns_none_for_malformed_json():
    assert ve._decode("not json at all") is None


def test_prose_for_scoring_prefers_decoded_json_text():
    decoded = {"observations": [{"claim": "x"}]}
    prose = ve._prose_for_scoring(decoded, raw="ignored")
    assert json.loads(prose) == decoded


def test_prose_for_scoring_falls_back_to_raw_when_undecodable():
    assert ve._prose_for_scoring(None, raw="the raw text") == "the raw text"


# --------------------------------------------------------------------------- #
# _payload_signature
# --------------------------------------------------------------------------- #


def _payload(finding="all_layers_healthy", cause=None, rungs=None, coherence_status=None):
    return {
        "finding": finding,
        "cause": cause,
        "rungs": rungs or [{"rung": "bgp_session", "device": "RR1", "status": "healthy"}],
        "coherence": {"status": coherence_status} if coherence_status else None,
    }


def test_payload_signature_is_stable_for_identical_payloads():
    a = _payload()
    b = _payload()
    assert ve._payload_signature(a) == ve._payload_signature(b)


def test_payload_signature_differs_when_a_rung_status_differs():
    a = _payload(rungs=[{"rung": "bgp_session", "device": "RR1", "status": "healthy"}])
    b = _payload(rungs=[{"rung": "bgp_session", "device": "RR1", "status": "broken"}])
    assert ve._payload_signature(a) != ve._payload_signature(b)


def test_payload_signature_distinguishes_coherence_status():
    """`window_limited` vs `coherent` is exactly the "variance in the fabric,
    not the answer" distinction B-207 is asked to make -- the signature must
    carry it, or a qualifying re-read and a genuinely different finding would
    collapse into the same bucket."""

    a = _payload(coherence_status="coherent")
    b = _payload(coherence_status="window_limited")
    assert ve._payload_signature(a) != ve._payload_signature(b)


# --------------------------------------------------------------------------- #
# run_deterministic_condition -- fixture replay, fully offline
# --------------------------------------------------------------------------- #


def test_run_deterministic_condition_is_zero_variance_on_a_fixture():
    """The claim this whole condition exists to check, at a small n a unit
    test can afford: repeated `investigate()` calls against the same
    committed fixture agree on every run."""

    from agent_nettools.fixtures import fixture_sender

    result = ve.run_deterministic_condition(
        "RR1", "10.255.0.12", "bgp_session", n=5,
        sender=fixture_sender(label="healthy"), label="test",
    )

    assert result["n"] == 5
    assert result["distinct_signatures"] == 1
    assert result["modal_signature_count"] == 5
    assert result["modal_signature_fraction"] == 1.0
    assert len(result["payloads"]) == 5
    assert all(p["finding"] == "all_layers_healthy" for p in result["payloads"])


def test_run_deterministic_condition_records_every_payload_not_only_the_signature():
    """evidence-archive/README.md's own rule -- "a parsed field is a
    conclusion; the input is the text it was parsed from" -- applied to this
    script's own output: the full payload travels, not only the derived
    signature, so a later question about *why* two runs agreed (or did not)
    can be answered from what is written to disk."""

    from agent_nettools.fixtures import fixture_sender

    result = ve.run_deterministic_condition(
        "RR1", "10.255.0.12", "bgp_session", n=2,
        sender=fixture_sender(label="broken"), label="test",
    )
    assert result["payloads"][0]["rungs"] == result["payloads"][1]["rungs"]


# --------------------------------------------------------------------------- #
# _live_lab_available -- the gate, both ways (OBS-181 positive control)
# --------------------------------------------------------------------------- #


def test_live_lab_unavailable_when_flag_unset(monkeypatch):
    monkeypatch.delenv("NETTOOLS_LIVE_LAB", raising=False)
    reason = ve._live_lab_available()
    assert reason is not None
    assert "NETTOOLS_LIVE_LAB" in reason


def test_live_lab_unavailable_when_flag_set_but_no_credentials(monkeypatch):
    monkeypatch.setenv("NETTOOLS_LIVE_LAB", "1")
    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)
    monkeypatch.delenv("DEVICE_SSH_KEYFILE", raising=False)
    reason = ve._live_lab_available()
    assert reason is not None
    assert "credentials" in reason


def test_live_lab_available_when_flag_and_credentials_are_both_set(monkeypatch):
    """Positive control (OBS-181): the same gate, satisfied both ways."""

    monkeypatch.setenv("NETTOOLS_LIVE_LAB", "1")
    monkeypatch.setenv("DEVICE_USERNAME", "u")
    monkeypatch.setenv("DEVICE_PASSWORD", "p")
    assert ve._live_lab_available() is None


def test_live_lab_available_with_an_ssh_keyfile_instead_of_a_password(monkeypatch):
    monkeypatch.setenv("NETTOOLS_LIVE_LAB", "1")
    monkeypatch.setenv("DEVICE_USERNAME", "u")
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", "/path/to/key")
    assert ve._live_lab_available() is None


# --------------------------------------------------------------------------- #
# _ollama_available -- reachability gate, both ways
# --------------------------------------------------------------------------- #


class _FakeUnreachable:
    def __enter__(self):
        raise ConnectionRefusedError("no server")

    def __exit__(self, *a):
        return False


class _FakeReachable:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ollama_unavailable_when_unreachable(monkeypatch):
    import urllib.error
    import urllib.request as real_urllib_request

    def fake_urlopen(*a, **k):
        raise urllib.error.URLError("connection refused")

    # `_ollama_available` imports `urllib.request` locally on each call, so
    # patching the real module object (not a name on `ve`, which never
    # imports `urllib` at module scope) is what a local import will see.
    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)
    reason = ve._ollama_available()
    assert reason is not None
    assert "could not reach Ollama" in reason


def test_ollama_available_when_reachable(monkeypatch):
    import urllib.request as real_urllib_request

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda *a, **k: _FakeReachable())
    assert ve._ollama_available() is None


# --------------------------------------------------------------------------- #
# run_model_condition -- offline, with a scripted fake analyst
# --------------------------------------------------------------------------- #


class _ScriptedAnalyst:
    """Stands in for `_RecordingAnalyst`: same call shape
    (`Callable[[RenderedPrompt], str]`, a `.usage` attribute), never opens a
    socket. `responses` is consumed one per call, repeating the last entry
    once exhausted."""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._i = 0
        self.usage = None

    def __call__(self, prompt) -> str:
        response = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return response


def test_run_model_condition_reports_zero_variance_for_a_constant_analyst(monkeypatch):
    """A scripted analyst that always returns the identical (syntactically
    valid, but citation-empty) JSON must be reported as 1 distinct raw
    response across every run, and -- since an empty `observations` list
    cannot cover a five-rung descent -- consistently withheld by grounding.
    This is the offline proof that the script's own variance bookkeeping is
    correct, independent of whether a real model is deterministic."""

    constant = json.dumps({
        "observations": [], "interpretations": [],
        "recommendation": {"requires_human": True},
    })
    monkeypatch.setattr(ve, "_RecordingAnalyst", lambda: _ScriptedAnalyst([constant]))

    result = ve.run_model_condition("healthy_summary", n=4)

    assert result["report_calls_completed"] == 4
    assert result["distinct_raw_responses"] == 1
    assert result["distinct_raw_response_fraction"] == 0.25
    assert result["report_grounding_ok_rate"] == 0.0
    assert result["rung_coverage_named_count"]["max"] == 0


def test_run_model_condition_counts_distinct_responses_when_they_actually_vary(monkeypatch):
    """The positive control for the property above: when the analyst's
    responses genuinely differ, the script must say so rather than always
    reporting 1.

    `run_model_condition` builds a fresh `_RecordingAnalyst()` per run (one
    analyst object per investigation, matching the real CLI's own lifecycle),
    so the counter driving which variant comes back has to live OUTSIDE any
    one instance -- a naive `lambda: _ScriptedAnalyst(variants)` would hand
    every run a fresh iterator starting at index 0 and (wrongly) always
    return the first variant.
    """

    variants = [
        json.dumps({"observations": [], "interpretations": [], "recommendation": {"requires_human": True}}),
        json.dumps({"observations": [], "interpretations": [], "recommendation": {"requires_human": True}, "x": 1}),
        json.dumps({"observations": [], "interpretations": [], "recommendation": {"requires_human": True}, "x": 2}),
    ]
    calls = {"i": 0}

    class _SharedCounterAnalyst:
        def __init__(self):
            self.usage = None

        def __call__(self, prompt) -> str:
            variant = variants[calls["i"] % len(variants)]
            calls["i"] += 1
            return variant

    monkeypatch.setattr(ve, "_RecordingAnalyst", _SharedCounterAnalyst)

    result = ve.run_model_condition("healthy_summary", n=3)

    assert result["report_calls_completed"] == 3
    assert result["distinct_raw_responses"] == 3
    assert result["distinct_raw_response_fraction"] == 1.0


def test_run_model_condition_scores_a_grounded_response_as_grounding_ok(monkeypatch):
    """A response that actually restates every rung the fixture's descent
    walked, citing real evidence keys, passes grounding -- proving the
    pipeline can report `grounding_ok_rate == 1.0`, not only `0.0`."""

    from agent_nettools import model_eval as me

    question = me.question_by_id("healthy_summary")
    payload = me.ground_truth_payload(question)
    observations = [
        {"claim": f"{r['rung']} on {r['device']} is {r['status']}",
         "evidence_key": r["evidence_keys"][0]}
        for r in payload["rungs"] if r["evidence_keys"]
    ]
    grounded = json.dumps({
        "observations": observations,
        "interpretations": [{
            "claim": "Every layer is healthy.",
            "based_on": [f"obs-{i}" for i in range(1, len(observations) + 1)],
        }],
        "recommendation": {
            "next_check": payload["report"]["content"]["recommendation"]["next_check"]
            if payload.get("report") else None,
            "requires_human": True,
        },
    })
    monkeypatch.setattr(ve, "_RecordingAnalyst", lambda: _ScriptedAnalyst([grounded]))

    result = ve.run_model_condition("healthy_summary", n=2)

    assert result["report_calls_completed"] == 2
    assert result["report_grounding_ok_rate"] == 1.0
