#!/usr/bin/env python3
"""B-207 -- how much does the answer vary when nothing about the network has
changed?

Three conditions, because "the model path" and "the deterministic path" are
different claims and deserve different instruments:

1.  **deterministic-fixtures** -- `investigate(... --no-model)` against a
    committed, byte-identical capture, run N times. `descent.py` has no model
    call (CLAUDE.md's own words: "if this file ever needs a model call,
    something above it has been designed wrong"), so this condition's variance
    should be exactly zero *by construction*. This script measures that rather
    than assuming it.

2.  **deterministic-live** -- the same descent, N times, against the real lab.
    The fixture condition proves the code is deterministic; this one asks
    whether the *fabric* is quiet enough for that to be observable in
    practice, since the two are different claims (module docstring on
    `epoch.py`: "skew does not establish coherence -- the re-read does"). A
    run that changed its finding because the fabric genuinely moved between
    samples is not variance in the tool -- it is `epoch.Coherence` doing its
    job (`window_limited` qualifies a stable-but-slow read; `fabric_moved`
    refuses a read that caught a real transition). Needs
    `DEVICE_USERNAME`/`DEVICE_PASSWORD` and a reachable lab; self-skips with a
    clear reason otherwise, the same convention `tests/test_live_lab.py`
    already uses for the identical gap.

3.  **model** -- `investigate(analyst=...)` against a fixture, N times, with a
    real model in the loop (local Ollama; keyless, per `llm_analysis.py`'s own
    docstring, and the reason it is the only real-model provider this script
    can assume is present without an operator's API key). Unlike the
    deterministic path, variance here is not a defect by construction -- it is
    the thing being measured, and `model_eval.py`'s six dimensions are how it
    is measured on more than raw text equality. Ground truth is one fixture
    replay per condition (`model_eval.ground_truth_payload`, deterministic,
    computed once); what varies across the N calls is the paraphrase.

No fault is injected and no fault needs to be: the point of this experiment is
what the tool says when *nothing* about the network has changed, which is the
one condition B-492 was refused for measuring at n=1. Every condition here
runs at n >= 10 (BACKLOG.md B-492: "5-10 runs ... produce a rate rather than
an anecdote").

Writes one JSONL file per condition plus a `summary.json`, under
`evidence-archive/variance-experiment/<timestamp>/` -- the same shape
`round5_sampler.py`/`round7_sampler.py` already use for a repeated-sample
run, and the same reason evidence-archive/README.md gives for keeping the
raw payload beside any derived number: "a parsed field is a conclusion; the
input is the text it was parsed from."

Read-only. No fault is ever applied or removed; §0.11 does not apply because
nothing here touches the fault path at all.

Usage:
    python scripts/variance_experiment.py deterministic-fixtures [--n 20]
    python scripts/variance_experiment.py deterministic-live [--n 10]
    python scripts/variance_experiment.py model [--n 10]
    python scripts/variance_experiment.py all [--n 10]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import statistics
import sys
import time
from collections import Counter
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()

from agent_nettools import model_eval as me  # noqa: E402
from agent_nettools.fixtures import fixture_sender  # noqa: E402
from agent_nettools.investigation import investigate  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

# Same fence-stripping rule as `investigation._decode` -- a markdown fence is a
# known, deterministic transport wrapper that carries no signal about the
# model's judgement, so it is stripped before parsing rather than counted as
# "a different answer". Reimplemented rather than imported: `_decode` is a
# private symbol of `investigation.py`, and this script's decode is used for
# scoring only, never fed back into anything the descent's grounding gate
# would see.
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)


def _decode(text: str) -> Any:
    """Best-effort JSON decode of a model's raw response, for scoring only."""

    match = _FENCE.match(text)
    body = match["body"] if match else text
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def _prose_for_scoring(decoded: Any, raw: str) -> str:
    """What `model_eval.py`'s dimension scorers read.

    `model_eval.py`'s own docstring is explicit that extracting prose from a
    transcript is a transport concern the module deliberately keeps out of
    itself (see its `ModelTranscript` docstring); this is that transport step
    for a live model call rather than a hand-transcribed MCP session. The
    decoded JSON's string content, serialised back to text, is what a human
    reading the paraphrase would see -- `score_rung_coverage`/`score_invention`
    are substring matches over lowercased text and do not care about the
    surrounding JSON punctuation.
    """

    if decoded is None:
        return raw
    return json.dumps(decoded)


# --------------------------------------------------------------------------- #
# Condition 1/2 -- the deterministic path
# --------------------------------------------------------------------------- #


def _payload_signature(payload: dict) -> tuple:
    """A hashable summary of one descent's answer.

    Finding, cause, the full rung-by-rung status vector, and the coherence
    verdict's status -- everything B-207 is asked to distinguish "variance in
    the answer" from "variance in the fabric" over. Two runs whose signature
    differs only in `coherence.status` (`coherent` vs `window_limited`) are
    the qualifying case the module docstring describes, not the refusing one;
    the full payload (written to the JSONL line beside this) still carries the
    detail for a reader who wants to look closer than the signature does.
    """

    coherence = payload.get("coherence") or {}
    return (
        payload["finding"],
        json.dumps(payload["cause"], sort_keys=True),
        tuple((r["rung"], r["device"], r["status"]) for r in payload["rungs"]),
        coherence.get("status"),
    )


def run_deterministic_condition(
    device: str,
    subject: str,
    flow: str,
    *,
    n: int,
    sender=None,
    label: str,
) -> dict:
    """Repeat a no-model descent `n` times against one (device, subject, flow).

    Every run is independent -- a fresh `investigate()` call, nothing carried
    over -- so a repeated signature is evidence of determinism, not of shared
    state accidentally making two calls agree.
    """

    payloads: list[dict] = []
    durations: list[float] = []
    for _ in range(n):
        started = time.monotonic()
        result = investigate(device, subject, flow=flow, sender=sender)
        durations.append(time.monotonic() - started)
        payloads.append(result.to_payload())

    signatures = [_payload_signature(p) for p in payloads]
    counts = Counter(signatures)
    # The modal (most common) signature and how many runs matched it -- the
    # single number this condition is really being asked for: "of N identical
    # questions, how many got the modal answer".
    modal_signature, modal_count = counts.most_common(1)[0]

    return {
        "condition": label,
        "device": device,
        "subject": subject,
        "flow": flow,
        "n": n,
        "distinct_signatures": len(counts),
        "modal_signature_count": modal_count,
        "modal_signature_fraction": round(modal_count / n, 4),
        "signature_counts": [
            {"signature": list(sig), "count": c} for sig, c in counts.most_common()
        ],
        "duration_seconds": {
            "min": round(min(durations), 3),
            "max": round(max(durations), 3),
            "mean": round(statistics.mean(durations), 3),
        },
        "payloads": payloads,
    }


# The four flow/label pairs measured by default -- two flows, two labels each,
# so the fixture condition is not just one lucky pairing. `isis-broken`
# exercises a THIRD label on `isis_adjacency` because that is the one flow in
# this build whose corroboration tiers (LLDP, then interface state) give the
# walk the most internal branching to be non-deterministic about, if it were
# going to be.
DETERMINISTIC_FIXTURE_CONDITIONS: tuple[dict, ...] = (
    {"device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session", "label": "healthy"},
    {"device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session", "label": "broken"},
    {"device": "PE2", "subject": "Gi0/0/0/0", "flow": "isis_adjacency", "label": "healthy"},
    {"device": "PE3", "subject": "Gi0/0/0/0", "flow": "isis_adjacency", "label": "isis-broken"},
)

# The single condition run against the live lab. One flow is enough here: this
# condition is not asking "does every flow behave", it already knows that from
# the fixture conditions above -- it is asking "does a fabric that genuinely
# moves under you make the point-in-time descent disagree with itself", which
# is a property of the epoch's coherence machinery and the fabric's actual
# instability, not of which flow is walked.
DETERMINISTIC_LIVE_CONDITION: dict = {
    "device": "RR1", "subject": "10.255.0.12", "flow": "bgp_session", "label": "live",
}


def _live_lab_available() -> str | None:
    """`None` if the live lab looks usable; otherwise the reason it is not.

    Same two-part gate `tests/test_live_lab.py::_require_live_lab` uses
    (opt-in flag first, so CI/an ordinary checkout never silently attempts a
    real SSH session), plus an explicit credential check so the failure this
    script reports is "no credentials configured" rather than a raw
    authentication traceback from three layers down.
    """

    flag = os.getenv("NETTOOLS_LIVE_LAB", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return (
            "NETTOOLS_LIVE_LAB is not set to a truthy value; skipping the live-lab "
            "condition by default, same convention as tests/test_live_lab.py. Run "
            "with NETTOOLS_LIVE_LAB=1 plus real DEVICE_USERNAME/DEVICE_PASSWORD to "
            "opt in."
        )
    if not os.getenv("DEVICE_USERNAME") or not (
        os.getenv("DEVICE_PASSWORD") or os.getenv("DEVICE_SSH_KEYFILE")
    ):
        return (
            "DEVICE_USERNAME plus DEVICE_PASSWORD or DEVICE_SSH_KEYFILE are not "
            "set; this environment has no credentials for the live lab, so the "
            "live-lab deterministic condition cannot be run. This is a gap in "
            "what this run could measure, not a claim that the fabric is quiet."
        )
    return None


# --------------------------------------------------------------------------- #
# Condition 3 -- the model path
# --------------------------------------------------------------------------- #


class _RecordingAnalyst:
    """A `(RenderedPrompt) -> str` callable bound to the configured provider.

    The same shape `cli.py`'s `_UsageRecordingAnalyst` is (a callable object
    so `investigation.Analyst`'s signature, `Callable[[RenderedPrompt], str]`,
    stays untouched), reimplemented here rather than imported from `cli.py`
    because that class is a leading-underscore module-private symbol of a
    module this script otherwise has no reason to import.
    """

    def __init__(self):
        from agent_nettools.llm_analysis import TokenUsage, complete_prompt

        self._complete = complete_prompt
        self.usage = TokenUsage()
        self.calls = 0

    def __call__(self, prompt) -> str:
        completion = self._complete(prompt)
        self.usage = self.usage + completion.usage
        self.calls += 1
        return completion.text


def _exchange_metrics(exchange) -> dict:
    decoded = _decode(exchange.response_text)
    return {
        "purpose": exchange.purpose,
        "grounding_ok": exchange.grounding_ok,
        "grounding_summary": exchange.grounding_summary,
        "response_chars": len(exchange.response_text),
        "response_json_valid": decoded is not None,
        "usage": exchange.usage,
    }


def run_model_condition(question_id: str, *, n: int) -> dict:
    """Repeat one `EvalQuestion` `n` times through a real model, and score each.

    Ground truth is computed once (fixture replay, deterministic, no model);
    what varies across the `n` calls is the model's paraphrase of it. Every
    call is scored on `model_eval.py`'s dimensions 2/3/4 (rung coverage,
    invention, premise handling -- dimension 1, tool selection, does not apply
    here: this calls `investigate()`'s own internal paraphrase step directly,
    not an agentic tool-selecting loop, so there is no tool call to score),
    and the raw response text is kept so exact-string variance -- the
    simplest and most literal answer to "does it give a different answer to
    the same question" -- is measured too, not only the six-dimension scores.
    """

    question = me.question_by_id(question_id)
    if question is None:
        raise ValueError(f"no such EvalQuestion: {question_id!r}")

    payload = me.ground_truth_payload(question)

    raw_responses: list[str] = []
    report_grounding: list[bool] = []
    correlate_grounding: list[bool] = []
    rung_coverage_named: list[int] = []
    invention_counts: list[int] = []
    invention_kinds: Counter = Counter()
    premise_classifications: Counter = Counter()
    response_chars: list[int] = []
    exchanges_by_run: list[list[dict]] = []

    for _ in range(n):
        analyst = _RecordingAnalyst()
        result = investigate(
            question.device, question.subject, flow=question.flow,
            sender=fixture_sender(label=question.fixture_label), analyst=analyst,
        )
        run_exchanges = [_exchange_metrics(e) for e in result.exchanges]
        exchanges_by_run.append(run_exchanges)

        report_exchange = next(
            (e for e in result.exchanges if e.purpose == "report_paraphrase"), None
        )
        if report_exchange is None:
            continue

        raw_responses.append(report_exchange.response_text)
        response_chars.append(len(report_exchange.response_text))
        report_grounding.append(report_exchange.grounding_ok)

        decoded = _decode(report_exchange.response_text)
        prose = _prose_for_scoring(decoded, report_exchange.response_text)

        coverage = me.score_rung_coverage(payload, prose)
        rung_coverage_named.append(coverage.named_count)

        invention = me.score_invention(payload, prose)
        invention_counts.append(len(invention))
        invention_kinds.update(f.kind for f in invention)

        premise = me.score_premise_handling(question, payload, prose)
        if premise.applicable:
            premise_classifications[premise.classification] += 1

        correlate_exchange = next(
            (e for e in result.exchanges if e.purpose == "correlate_paraphrase"), None
        )
        if correlate_exchange is not None:
            correlate_grounding.append(correlate_exchange.grounding_ok)

    distinct_responses = len(set(raw_responses))

    def _dist(values: list[int]) -> dict:
        if not values:
            return {}
        return {
            "min": min(values), "max": max(values),
            "mean": round(statistics.mean(values), 3),
            "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
            "counts": dict(sorted(Counter(values).items())),
        }

    return {
        "condition": question_id,
        "question": question.question,
        "device": question.device,
        "subject": question.subject,
        "flow": question.flow,
        "fixture_label": question.fixture_label,
        "n": n,
        "report_calls_completed": len(raw_responses),
        "distinct_raw_responses": distinct_responses,
        "distinct_raw_response_fraction": (
            round(distinct_responses / len(raw_responses), 4) if raw_responses else None
        ),
        "report_grounding_ok_rate": (
            round(sum(report_grounding) / len(report_grounding), 4)
            if report_grounding else None
        ),
        "correlate_grounding_ok_rate": (
            round(sum(correlate_grounding) / len(correlate_grounding), 4)
            if correlate_grounding else None
        ),
        "rung_coverage_named_count": _dist(rung_coverage_named),
        "invention_finding_count": _dist(invention_counts),
        "invention_kinds": dict(invention_kinds),
        "premise_classification_counts": dict(premise_classifications),
        "response_chars": _dist(response_chars),
        "exchanges_by_run": exchanges_by_run,
        "raw_responses": raw_responses,
    }


MODEL_CONDITIONS: tuple[str, ...] = ("healthy_summary", "broken_confirm", "pe2_bgp_why")


def _ollama_available() -> str | None:
    """`None` if a local Ollama server looks reachable; otherwise why not."""

    import urllib.error
    import urllib.request

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5):
            pass
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"could not reach Ollama at {host}: {exc}"
    return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _out_dir() -> pathlib.Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = REPO / "evidence-archive" / "variance-experiment" / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def cmd_deterministic_fixtures(args: argparse.Namespace) -> int:
    out = _out_dir()
    conditions = []
    for spec in DETERMINISTIC_FIXTURE_CONDITIONS:
        label = f"{spec['flow']}:{spec['device']}->{spec['subject']}:{spec['label']}"
        print(f"== deterministic-fixtures: {label} (n={args.n}) ==")
        result = run_deterministic_condition(
            spec["device"], spec["subject"], spec["flow"], n=args.n,
            sender=fixture_sender(label=spec["label"]), label=label,
        )
        conditions.append(result)
        print(
            f"   {result['distinct_signatures']} distinct signature(s) across "
            f"{result['n']} runs; modal fraction {result['modal_signature_fraction']}"
        )
    _write_jsonl(out / "deterministic_fixtures.jsonl", conditions)
    print(f"\nwrote {out / 'deterministic_fixtures.jsonl'}")
    return 0


def cmd_deterministic_live(args: argparse.Namespace) -> int:
    reason = _live_lab_available()
    out = _out_dir()
    if reason is not None:
        print(f"SKIPPED deterministic-live: {reason}")
        (out / "deterministic_live_SKIPPED.json").write_text(
            json.dumps({"skipped": True, "reason": reason}, indent=2)
        )
        return 0

    spec = DETERMINISTIC_LIVE_CONDITION
    print(f"== deterministic-live: {spec['flow']}:{spec['device']}->{spec['subject']} (n={args.n}) ==")
    result = run_deterministic_condition(
        spec["device"], spec["subject"], spec["flow"], n=args.n,
        sender=None, label="live",
    )
    print(
        f"   {result['distinct_signatures']} distinct signature(s) across "
        f"{result['n']} runs; modal fraction {result['modal_signature_fraction']}"
    )
    _write_jsonl(out / "deterministic_live.jsonl", [result])
    print(f"\nwrote {out / 'deterministic_live.jsonl'}")
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    reason = _ollama_available()
    out = _out_dir()
    if reason is not None:
        print(f"SKIPPED model: {reason}")
        (out / "model_SKIPPED.json").write_text(
            json.dumps({"skipped": True, "reason": reason}, indent=2)
        )
        return 0

    os.environ.setdefault("LLM_PROVIDER", "ollama")
    conditions = []
    for question_id in MODEL_CONDITIONS:
        print(f"== model: {question_id} (n={args.n}, provider={os.environ['LLM_PROVIDER']}) ==")
        started = time.monotonic()
        result = run_model_condition(question_id, n=args.n)
        elapsed = time.monotonic() - started
        conditions.append(result)
        print(
            f"   {result['distinct_raw_responses']}/{result['report_calls_completed']} distinct "
            f"raw responses; report grounding_ok rate "
            f"{result['report_grounding_ok_rate']}; {elapsed:.1f}s"
        )
    _write_jsonl(out / "model_ollama.jsonl", conditions)
    print(f"\nwrote {out / 'model_ollama.jsonl'}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    p1 = sub.add_parser("deterministic-fixtures")
    p1.add_argument("--n", type=int, default=20)
    p1.set_defaults(func=cmd_deterministic_fixtures)

    p2 = sub.add_parser("deterministic-live")
    p2.add_argument("--n", type=int, default=10)
    p2.set_defaults(func=cmd_deterministic_live)

    p3 = sub.add_parser("model")
    p3.add_argument("--n", type=int, default=10)
    p3.set_defaults(func=cmd_model)

    p4 = sub.add_parser("all")
    p4.add_argument("--n", type=int, default=10)

    args = ap.parse_args(argv[1:])
    if args.mode == "all":
        rc = 0
        args.n = args.n
        fixtures_args = argparse.Namespace(n=max(args.n, 20))
        rc |= cmd_deterministic_fixtures(fixtures_args)
        rc |= cmd_deterministic_live(args)
        rc |= cmd_model(args)
        return rc
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
