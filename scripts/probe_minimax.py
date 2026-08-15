#!/usr/bin/env python3
"""Probe the MiniMax API for the behaviours the investigation layer depends on.

T-002 in ``docs/build/BUILD-PLAN.md``. A throwaway diagnostic, committed so the
result is reproducible, deliberately with **no dependency on agent_nettools**
and no dependency outside the standard library -- it must be runnable before
the package is wired to a MiniMax provider (T-003), and it must not be able to
break when that wiring changes.

Six checks, printed as a table. What matters and why:

* Check 4 is the one the gate depends on. MVP-1's gate returns a typed decision
  object that code parses as JSON. If reasoning leaks into ``content`` inside
  ``<think>`` tags, that parse fails, and the failure is a malformed control
  decision rather than a clean error. Checks 3 and 4 measure whether
  ``reasoning_split`` suppresses it.
* Check 5 measures determinism, not correctness. The number is recorded to set
  expectations for prompt tests, never to gate anything.
* Check 6 is informational for MVP-0 -- the descent needs no tool calling -- but
  it constrains MVP-1.

**Two routes, two different error conventions.** Measured against the live
endpoint with no credentials:

* ``/v1/text/chatcompletion_v2`` (MiniMax native) answers **HTTP 200** with the
  real status buried in ``base_resp.status_code`` (1004 = auth failure).
* ``/v1/chat/completions`` (OpenAI-compatible) answers a truthful **HTTP 401**
  with an OpenAI-shaped error envelope.

This probe uses the OpenAI-compatible route, because that is what T-003 wires
up. It still inspects ``base_resp`` defensively on every response: reading HTTP
200 as success on this vendor is exactly the mistake that would report a broken
endpoint as healthy, which is the same class of error ``health.py`` avoids with
``unevaluated``.

The API key is read from ``MINIMAX_API_KEY`` (environment first, then ``.env``)
and is **redacted from every line this script prints**, including tracebacks and
echoed request bodies. It is never written to a file and never logged.

Usage::

    python scripts/probe_minimax.py            # run all six checks
    python scripts/probe_minimax.py --dry-run  # show what would run, no network

Exit codes follow the repository convention:
``0`` acceptance met (checks 1, 2 and 4 pass) · ``1`` ran but acceptance failed
· ``2`` could not run at all (no key, or the endpoint is unreachable).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MODEL = "MiniMax-M3"
REQUEST_TIMEOUT_SECONDS = 60
EVIDENCE_CHARS = 200

# Documented cap on the OpenAI-compatible route. Set explicitly on every call
# rather than relying on the endpoint default -- BUILD-PLAN.md T-003 step 4.
MAX_COMPLETION_TOKENS = 2048

# Verbatim from BUILD-PLAN.md T-002. Do not reword: checks 3, 4 and 5 are only
# comparable to each other, and to a later re-run, if the prompt is identical.
TYPED_DECISION_PROMPT = (
    "Return only this JSON object and nothing else. No prose, no markdown fences.\n"
    '{"decision":"narrow","target":{"type":"bgp_neighbor","id":"10.255.0.12"}}'
)

THINK_TAG = re.compile(r"<think>", re.IGNORECASE)

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------


class Redactor:
    """Replaces the API key with a placeholder anywhere it appears in output.

    Belt and braces: the key should never reach a printed string in the first
    place, but a probe whose whole job is dumping raw API responses is exactly
    where an echoed request header would slip through unnoticed.
    """

    PLACEHOLDER = "***REDACTED***"

    def __init__(self, secret: str | None) -> None:
        self._secret = secret or ""

    def __call__(self, text: str) -> str:
        if self._secret and self._secret in text:
            return text.replace(self._secret, self.PLACEHOLDER)
        return text


def load_api_key() -> tuple[str | None, str]:
    """Return ``(key, source)``. Environment wins over ``.env``, like the repo."""
    env_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if env_key:
        return env_key, "environment"

    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "MINIMAX_API_KEY":
                value = value.strip().strip("\"'")
                if value:
                    return value, str(env_file)
    return None, "not found"


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


class ProbeError(RuntimeError):
    """The endpoint could not be reached at all, as distinct from answering badly."""


def call(
    payload: dict[str, Any], *, api_key: str, base_url: str, redact: Redactor
) -> tuple[int, dict[str, Any] | None, str]:
    """POST to ``chat/completions``. Returns ``(http_status, parsed_json, raw)``.

    Never raises for an API-level error -- an error envelope is a result to be
    recorded, not an exception. Only genuine unreachability raises.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed https endpoint
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        # An HTTP error still carries a body worth reading -- that is where the
        # OpenAI-compatible route puts its error envelope.
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(redact(f"{type(exc).__name__}: {exc}")) from None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, raw


def api_error(status: int, parsed: dict[str, Any] | None) -> str | None:
    """Return a human-readable API error, or ``None`` if the response looks sound.

    Checks three places, because this vendor uses all three:
    a non-2xx HTTP status, an OpenAI-shaped ``error`` object, and MiniMax's own
    ``base_resp.status_code`` -- which is the one that arrives alongside HTTP 200.
    """
    if parsed is None:
        return f"HTTP {status}, response body was not JSON"

    base_resp = parsed.get("base_resp")
    if isinstance(base_resp, dict) and base_resp.get("status_code", 0) not in (0, None):
        return (
            f"base_resp.status_code={base_resp.get('status_code')} "
            f"{base_resp.get('status_msg', '')}".strip()
        )

    error = parsed.get("error")
    if isinstance(error, dict):
        return f"{error.get('type', 'error')}: {error.get('message', '')}".strip()
    if isinstance(error, str) and error:
        return error

    if status >= 400:
        return f"HTTP {status}"
    return None


def message_of(parsed: dict[str, Any] | None) -> dict[str, Any]:
    choices = (parsed or {}).get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {}


def content_of(parsed: dict[str, Any] | None) -> str:
    content = message_of(parsed).get("content")
    return content if isinstance(content, str) else ""


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


class Result:
    def __init__(self, number: int, name: str, verdict: str, evidence: str) -> None:
        self.number = number
        self.name = name
        self.verdict = verdict
        self.evidence = evidence


def base_payload(model: str, prompt: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
    }
    payload.update(extra)
    return payload


def run_checks(*, api_key: str, base_url: str, model: str, redact: Redactor) -> list[Result]:
    results: list[Result] = []

    def record(number: int, name: str, verdict: str, evidence: str) -> None:
        results.append(Result(number, name, verdict, redact(evidence)))

    # -- 1 & 2: one call answers both. Auth/reachability and model acceptance
    # fail differently (1004 vs a model-not-found error), so they are reported
    # separately even though a single request settles them.
    payload = base_payload(model, "Reply with the single word: ok", max_completion_tokens=16)
    status, parsed, raw = call(payload, api_key=api_key, base_url=base_url, redact=redact)
    error = api_error(status, parsed)
    content = content_of(parsed)

    if error is None and content.strip():
        record(1, "Auth and reachability", PASS, f"HTTP {status}, content={content.strip()!r}")
    else:
        record(
            1,
            "Auth and reachability",
            FAIL,
            f"HTTP {status}, error={error!r}, raw={raw[:EVIDENCE_CHARS]}",
        )

    model_rejected = bool(error) and re.search(
        r"model.*(not.?found|invalid|unknown|not.?exist)", error or "", re.IGNORECASE
    )
    if model_rejected:
        record(2, f"Model ID accepted ({model})", FAIL, f"error={error!r}")
    elif error is None:
        returned = (parsed or {}).get("model", model)
        record(2, f"Model ID accepted ({model})", PASS, f"model={returned!r}")
    else:
        # An unrelated failure (auth, quota) tells us nothing about the model ID.
        record(2, f"Model ID accepted ({model})", INFO, f"undetermined; call failed: {error!r}")

    # -- 3: does reasoning leak into content by default? Either answer is
    # informative; this is recorded, never a gate.
    payload = base_payload(model, TYPED_DECISION_PROMPT)
    status, parsed, raw = call(payload, api_key=api_key, base_url=base_url, redact=redact)
    error = api_error(status, parsed)
    content = content_of(parsed)
    if error:
        record(3, "<think> leakage, default", INFO, f"call failed: {error!r}")
    else:
        leaked = bool(THINK_TAG.search(content))
        record(
            3,
            "<think> leakage, default",
            INFO,
            f"<think> {'PRESENT' if leaked else 'absent'}; "
            f"parses_as_json={_parses(content)}; content={content[:EVIDENCE_CHARS]!r}",
        )

    # -- 4: the acceptance-critical one. The gate parses this as JSON.
    payload = base_payload(model, TYPED_DECISION_PROMPT, reasoning_split=True)
    status, parsed, raw = call(payload, api_key=api_key, base_url=base_url, redact=redact)
    error = api_error(status, parsed)
    content = content_of(parsed)
    message = message_of(parsed)
    has_reasoning_field = any(
        key in message for key in ("reasoning_details", "reasoning_content", "reasoning")
    )
    if error:
        record(4, "reasoning_split suppresses <think>", FAIL, f"call failed: {error!r}")
    elif THINK_TAG.search(content):
        record(
            4,
            "reasoning_split suppresses <think>",
            FAIL,
            f"<think> STILL PRESENT; content={content[:EVIDENCE_CHARS]!r}",
        )
    else:
        record(
            4,
            "reasoning_split suppresses <think>",
            PASS,
            f"no <think>; parses_as_json={_parses(content)}; "
            f"reasoning_field={has_reasoning_field}; content={content[:EVIDENCE_CHARS]!r}",
        )

    # -- 5: determinism. A number to set expectations, not a gate.
    payload = base_payload(model, TYPED_DECISION_PROMPT, temperature=0, reasoning_split=True)
    samples: list[str] = []
    failures = 0
    for _ in range(5):
        status, parsed, _raw = call(payload, api_key=api_key, base_url=base_url, redact=redact)
        if api_error(status, parsed):
            failures += 1
            continue
        samples.append(content_of(parsed))
    if not samples:
        record(5, "Determinism at temperature 0", INFO, f"no samples; {failures} calls failed")
    else:
        identical = max(samples.count(value) for value in set(samples))
        record(
            5,
            "Determinism at temperature 0",
            INFO,
            f"{identical}/5 byte-identical ({len(set(samples))} distinct, "
            f"{failures} failed); first={samples[0][:EVIDENCE_CHARS]!r}",
        )

    # -- 6: tool calling. Informational for MVP-0; constrains MVP-1.
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_bgp_neighbor_state",
                "description": "Return the BGP session state for one peer address.",
                "parameters": {
                    "type": "object",
                    "properties": {"peer": {"type": "string", "enum": ["10.255.0.12"]}},
                    "required": ["peer"],
                },
            },
        }
    ]
    payload = base_payload(
        model,
        "What is the BGP session state for peer 10.255.0.12? Use the provided tool.",
        tools=tools,
        reasoning_split=True,
    )
    status, parsed, raw = call(payload, api_key=api_key, base_url=base_url, redact=redact)
    error = api_error(status, parsed)
    tool_calls = message_of(parsed).get("tool_calls")
    if error:
        record(6, "Tool calling", INFO, f"call failed: {error!r}")
    elif tool_calls:
        names = [
            (c.get("function") or {}).get("name") for c in tool_calls if isinstance(c, dict)
        ]
        record(6, "Tool calling", INFO, f"tool_calls returned: {names}")
    else:
        record(
            6,
            "Tool calling",
            INFO,
            f"NO tool_calls; content={content_of(parsed)[:EVIDENCE_CHARS]!r}",
        )

    return results


def _parses(content: str) -> bool:
    try:
        json.loads(content.strip())
    except (json.JSONDecodeError, AttributeError):
        return False
    return True


# --------------------------------------------------------------------------
# presentation
# --------------------------------------------------------------------------


def print_table(results: list[Result]) -> None:
    width = max((len(r.name) for r in results), default=10)
    print()
    print(f"{'#':<3} {'CHECK':<{width}}  {'VERDICT':<7}  EVIDENCE")
    print("-" * (3 + width + 22))
    for r in results:
        print(f"{r.number:<3} {r.name:<{width}}  {r.verdict:<7}  {r.evidence}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report configuration and exit without making any network call",
    )
    args = parser.parse_args()

    base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL)
    api_key, source = load_api_key()
    redact = Redactor(api_key)

    print(f"endpoint : {base_url}/chat/completions")
    print(f"model    : {model}")
    print(f"key      : {'present (%d chars)' % len(api_key) if api_key else 'NOT FOUND'}"
          f" [source: {source}]")

    if args.dry_run:
        print("\n--dry-run: no network calls made.")
        return 0

    if not api_key:
        print(
            "\nCannot run: MINIMAX_API_KEY is not set in the environment or .env.\n"
            "Set it and re-run. The key is never echoed, written to a file, or committed.",
            file=sys.stderr,
        )
        return 2

    try:
        results = run_checks(api_key=api_key, base_url=base_url, model=model, redact=redact)
    except ProbeError as exc:
        print(f"\nCannot run: endpoint unreachable -- {exc}", file=sys.stderr)
        return 2

    print_table(results)

    verdicts = {r.number: r.verdict for r in results}
    required = {1: "Auth and reachability", 2: "Model ID accepted", 4: "reasoning_split"}
    failed = [f"check {n} ({label})" for n, label in required.items() if verdicts.get(n) != PASS]

    if failed:
        print(f"ACCEPTANCE: FAILED -- {', '.join(failed)} must pass.")
        print("Per BUILD-PLAN.md T-002, a check-4 failure is a blocker, not something")
        print("to work around silently: a <think>-stripping step is a decision, not an")
        print("implementation detail.")
        return 1

    print("ACCEPTANCE: met -- checks 1, 2 and 4 pass. Checks 3, 5, 6 recorded above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
