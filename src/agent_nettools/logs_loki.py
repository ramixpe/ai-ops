"""A read-only Loki log adapter: Stage-2 M5's first evidence source that is
not a device.

Grounding
---------
`docs/design/stage-2-architecture.md` §2.4/§2.4a: metrics and logs are
evidence, read as a **context** input for the wide flow step, never as a
descent rung -- "the narrow rung still compares parsed fields, and the
history is context around it, never the thing a verdict turns on." This
module is the read side only: it fetches and shapes; it is not wired into
`flows.py`/`checks.py`/`investigation.py` (out of scope by the build's own
instruction -- "expose the adapter, do not consume it in a descent").

`docs/build/discovery-loki.md` (T-004) is the measured groundwork this module
implements against: Loki 2.9.8 at the lab's management address, four labels
(`host`/`job`/`severity`/`source_ip`), `source_ip` as the join key (not
`host`'s `.sota-xrd`-suffixed convention, which is a syslog-ng rewrite rule
this repository does not own), two independent timestamps (Loki's own is
ingest time; the device's own clock is inside the message body), and heavy
duplication (one event stored 1,346 times in the T-004 sample) that
`log_window.dedupe` exists to remove -- "load-bearing for B-206, dead-looking
until then" per that module's own docstring. B-206 (`BACKLOG.md`) is this
adapter; it is filed `BLOCKED` on B-206a/B-206b, two *platform* facts (the
severity floor and the collector's own SSH noise) that are properties of the
syslog-ng pipeline this repository does not own, not defects in this module.
Building the adapter now, ahead of B-206's unblock, is consistent with
`evidence-reduction.md` §7's discipline either way: `coverage_from_loki`
below encodes the severity gap structurally, so an absence claim made over
this source is refused *by the same mechanism* regardless of when the
platform fix lands.

The three hard requirements this module exists to satisfy
-----------------------------------------------------------
1. **LogQL has no allowlist analogue, so this module is one.** :data:`LOKI_QUERIES`
   is an exact-match table of named queries with typed, validated slots --
   the same discipline `templates.py` uses for parameterized `show` commands
   (canonicalize by reconstruction, never pass-through). There is no
   function anywhere in this module that accepts a caller-supplied LogQL
   string; the *only* caller-influenced value that ends up inside a LogQL
   selector is a device's inventory-resolved `mgmt_ip`, re-parsed through
   `ipaddress.IPv4Address` so its only possible output alphabet is digits
   and dots (see `_DeviceSlot.parse` and `_build_logs_for_device_selector`).
2. **The envelope shape is `network_tools._base_result`'s shape.**
   `{tool, device, status, timestamp, source, data, errors}`, `data` carrying
   `parsed`/`parse_status`, `status` in `success`/`error` (see `_base_envelope`
   for why this is a local, structurally-identical rebuild rather than an
   import of that module's private helper). `source="loki"` is the literal
   string `network_tools.py`'s own docstring names as the intended spelling
   for a new, non-SSH source (`_source_for`'s docstring, `network_tools.py`).
3. **A log line is device-authored free text and crosses the projector.**
   `model_egress.FREE_TEXT_FIELDS` is keyed `(context, field)` and
   `_envelope_context` reads `data["intent"]` or `data["template"]` --
   nothing else. Every envelope this module returns sets
   `data["intent"] = <query name>`, which is both the query's identity *and*
   the context string the projector needs to find this module's free-text
   fields at all. See `_base_envelope`'s docstring for the trap this closes.

Field-name choice for the free-text entries: reuse, not a new name
---------------------------------------------------------------------
`mcp_server.boundary.sanitize` matches free text by **field name alone**,
across the whole MCP surface, with no context scoping (`_FREE_TEXT_FIELD_NAMES`
is a flat set built from every `(context, field)` pair in
`model_egress.FREE_TEXT_FIELDS`). A new, generic name -- `line`, `message` --
would add a *new* member to that flat set and start wrapping any field with
that name anywhere on the surface, including tools this module has nothing
to do with. This module's records instead reuse the exact field names the
`logging` template's free-text entries already use -- `text` (the syslog
line's message body) and `code` (the mnemonic's trailing segment) -- because
they name the *same concept* Loki is carrying (a syslog record's body and
its mnemonic code, not a coincidence of spelling) and because reusing an
already-declared name adds **zero** new members to `_FREE_TEXT_FIELD_NAMES`:
`"text"` and `"code"` are already in that set from `("logging", "text")` /
`("logging", "code")`, so this module's entries change nothing about what
`boundary.sanitize` matches anywhere else. That is the deliberate opposite of
what the warning above is about -- see `FREE_TEXT_FIELDS` below.

Absence is not zero
--------------------
A window with no matching lines is indistinguishable, *by record count
alone*, from a window Loki never answered. Two structural devices prevent
that from reading as "nothing happened":

* `data.status` / `data.parsed.meta.query_complete` distinguish "the HTTP
  call to Loki failed" from "the call succeeded and found nothing" -- the
  former is `status="error"`, `query_complete=False`; the latter is
  `status="success"`, `query_complete=True`, `records=[]`.
* :func:`coverage_from_loki` builds a `coverage.Coverage` (the same
  `unevaluated`-style discipline `coverage_from_logging` already applies to
  `show logging`) whose `severity_available` is the *measured* ceiling this
  pipeline carries today -- `(3, 4)`, per discovery-loki.md §6.1/B-206a --
  so `Coverage.gaps()` refuses an absence claim over this source
  structurally, the same way `test_a_source_that_drops_severities_can_never_
  support_a_negative` already pins for the identical Loki case.

Testing seam
------------
`fetcher=` on :func:`run_named_query`, the same injection idiom as
`network_tools`'s `sender=`: the default (`_http_fetcher`) performs a real
HTTP GET against Loki with the stdlib's `urllib` (no new dependency, the
same choice `notifier.py` already made); a test supplies its own
`fetcher(base_url, params) -> dict` returning a canned, Loki-shaped response
(or raising `LokiTransportError` to simulate a failure), so the whole suite
runs with no network.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from . import log_window
from ._env import _float_env
from .coverage import Coverage
from .inventory_model import find_device
from .parsers import PARSE_FAILED, PARSE_OK

__all__ = [
    "DEFAULT_LOKI_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LOKI_QUERIES",
    "LOKI_TIMEOUT_ENV",
    "LOKI_URL_ENV",
    "MEASURED_SEVERITY_AVAILABLE",
    "SOURCE_LOKI",
    "STATUS_ERROR",
    "STATUS_SUCCESS",
    "LokiQuery",
    "LokiQueryError",
    "LokiTransportError",
    "coverage_from_loki",
    "known_loki_queries",
    "run_named_query",
]

# --------------------------------------------------------------------------- #
# Envelope vocabulary
# --------------------------------------------------------------------------- #

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

#: The literal `network_tools.py`'s own `_source_for` docstring names as the
#: intended spelling for a brand-new, non-SSH evidence source: "it would
#: build its own envelope with `_base_result(..., source="loki")` directly,
#: since `source` is a plain string with no enum behind it." Also the exact
#: string `coverage.Coverage.source` already uses for this case, pinned by
#: `test_a_source_that_drops_severities_can_never_support_a_negative`.
SOURCE_LOKI = "loki"

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

LOKI_URL_ENV = "NETTOOLS_LOKI_URL"
#: discovery-loki.md §1: the management-network address of the live stack,
#: measured 2026-08-15/18. Its own caveat applies -- "this is a container IP,
#: not a stable service address... the URL belongs in an environment
#: variable, never a literal" -- and that is honoured by *never referencing
#: this constant directly*; every call site reads `_loki_url()`, which is
#: env-then-this-default, the same resolution order every other setting in
#: `settings.py` uses. Treat this value as a documented, dated fallback for a
#: single-operator lab, not a promise it will still resolve tomorrow.
DEFAULT_LOKI_URL = "http://172.20.250.103:3100"

LOKI_TIMEOUT_ENV = "NETTOOLS_LOKI_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Measured 2026-08-15 (discovery-loki.md §6.1) and reconfirmed 2026-08-18
#: (B-206a): only IOS-XR severities 3 (`err`) and 4 (`warning`) reach Loki.
#: Confirmed NOT to be the devices' own trap level (OBS-041) -- every device
#: reports `Trap logging: level informational` (severities 0-6), so the drop
#: happens further down the syslog-ng pipeline, which this repository does
#: not own and cannot fix from here. Loki's HTTP API has no way to report
#: this about itself the way `show logging`'s header states its own buffer
#: level, so it cannot be *measured* per query the way
#: `log_window.coverage_from_logging` reads one -- it is *declared*, the
#: same "declared, not derived" discipline `coverage.IOSXR_SEVERITY_LEVELS`
#: itself already uses for a table nothing in that module measures live
#: either. If B-206a/B-206b are ever fixed upstream, this constant must be
#: updated by a human after a fresh measurement -- never widened
#: automatically, and never narrowed without one either.
MEASURED_SEVERITY_AVAILABLE: tuple[int, ...] = (3, 4)


def _loki_url() -> str:
    return os.getenv(LOKI_URL_ENV, "").strip() or DEFAULT_LOKI_URL


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class LokiQueryError(ValueError):
    """A named query is unknown, or one of its slots failed validation.

    Never raised out of :func:`run_named_query` -- caught there and turned
    into a `status="error"` envelope, the same discipline every function in
    `network_tools.py` uses (never raise past the tool boundary). A distinct
    type from `templates.TemplateValidationError`: that module is frozen
    (§0.5) and governs a different command surface (device commands, not a
    log query), and this module must not couple to it.
    """


class LokiTransportError(Exception):
    """The HTTP call to Loki failed, or its response was not usable.

    Raised by the default fetcher (`_http_fetcher`) and by any test fetcher
    simulating a failure. Caught by :func:`run_named_query` and turned into
    the same `status="error"` envelope shape a validation failure produces --
    a caller should not have to tell "Loki is unreachable" and "the query was
    malformed" apart by exception type.
    """


# --------------------------------------------------------------------------- #
# Named-query table: the LogQL allowlist analogue
# --------------------------------------------------------------------------- #


class _DeviceSlot:
    """A bare device name (`"PE2"`), validated against the inventory --
    never against Loki's own `.sota-xrd`-suffixed `host` label, which is a
    syslog-ng rewrite rule this repository does not own (discovery-loki.md
    §2). Resolves to the device's `mgmt_ip`, re-parsed through
    `ipaddress.IPv4Address` for the same canonicalize-by-reconstruction
    reason `templates.py` re-parses every value it accepts rather than
    trusting the inventory's own prior pydantic validation: the value that
    ends up inside a LogQL string must be provably reconstructed from a
    typed parser's own output, never trusted pass-through, however many
    layers already checked it upstream.
    """

    def parse(self, name: str, value: object) -> str:
        # Messages below are deliberately worded to match existing
        # `model_egress.ERROR_KINDS`/`mcp_server.boundary.ERROR_KINDS`
        # tokens ("expected a string", "must not be empty", "is not in the
        # lab inventory") so a validation refusal here classifies through
        # the same safe, already-reviewed phrases those tables use for
        # `templates.py` and `event_routing.py` -- no new entry needed for
        # any of these three cases.
        if not isinstance(value, str):
            raise LokiQueryError(f"{name}: expected a string, got {type(value).__name__}")
        if not value:
            raise LokiQueryError(f"{name}: must not be empty")
        device = find_device(value)
        if device is None:
            raise LokiQueryError(f"{name}: {value!r} is not in the lab inventory")
        try:
            return str(ipaddress.IPv4Address(device.mgmt_ip))
        except ValueError as exc:  # pragma: no cover - inventory_model already validates this
            raise LokiQueryError(
                f"{name}: inventory mgmt_ip for {value!r} is not a valid IPv4 address"
            ) from exc


class _BoundedIntSlot:
    """A plain Python `int` (never a string to parse) restricted to an
    inclusive `[minimum, maximum]`. This module's callers are programmatic
    (an orchestrator or a model's tool call), not `argparse`, so unlike
    `templates.BoundedIntParam` (which validates CLI-supplied strings) this
    slot takes a native int and rejects anything else outright -- a
    deliberate, narrower choice for a narrower calling surface, not a
    relaxation of the `templates.py` discipline it is modelled on."""

    def __init__(self, *, minimum: int, maximum: int) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def parse(self, name: str, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LokiQueryError(f"{name}: expected an integer, got {type(value).__name__}")
        if not (self.minimum <= value <= self.maximum):
            raise LokiQueryError(
                f"{name}: must be between {self.minimum} and {self.maximum}, got {value}"
            )
        return value


#: `{source_ip="<ip>"}` -- a Loki stream selector over exactly one label.
#: `resolved["device"]` is `_DeviceSlot.parse`'s return value: a string that
#: can only ever be `str(ipaddress.IPv4Address(...))`'s own output, i.e. only
#: digits and dots. There is no character in that alphabet that can close the
#: selector's quote or brace early, so this is reconstruction exactly like
#: `templates.render_command`'s `str.format` step -- the caller's own text
#: (the device *name*) never reaches this string at all; only a value a
#: typed parser produced from looking that name up does.
_SELECTOR_SHAPE = re.compile(r'^\{source_ip="(?:[0-9]{1,3}\.){3}[0-9]{1,3}"\}$')


def _build_logs_for_device_selector(resolved: Mapping[str, Any]) -> str:
    return '{{source_ip="{0}"}}'.format(resolved["device"])


@dataclass(frozen=True)
class LokiQuery:
    """One named, validated LogQL query. The `templates.Template` pattern,
    carried over to a query language with no allowlist of its own.

    `params` maps a slot name to an object with a `.parse(name, value) ->
    canonical value` method (`_DeviceSlot`/`_BoundedIntSlot` above) -- the
    same shape `templates.ParamType` uses. `build_selector` receives only
    the *resolved* (already-validated, already-canonical) values and returns
    the LogQL stream selector; it is never handed a raw caller value.
    """

    name: str
    params: Mapping[str, Any]
    build_selector: Callable[[Mapping[str, Any]], str]
    description: str


#: The exact-match table. Adding a query means adding an entry here with its
#: own declared, typed slots -- never a function that accepts a LogQL string.
#: One entry today, deliberately: see the module's own report / FINDINGS-style
#: reasoning in the PR description for why a mnemonic- or regex-filtered
#: variant is not added yet (a caller-supplied LogQL line filter is a much
#: larger validation surface -- arbitrary regex from a caller is its own
#: denial-of-service vector -- and mnemonic filtering is already possible
#: client-side over this query's own structured `records`, with no new LogQL
#: surface at all).
LOKI_QUERIES: dict[str, LokiQuery] = {
    "logs_for_device": LokiQuery(
        name="logs_for_device",
        params={
            "device": _DeviceSlot(),
            # Loki's own retention_period is 168h (discovery-loki.md §4) --
            # asking further back than the source retains is a caller error
            # worth refusing explicitly rather than silently returning less
            # than asked.
            "since_seconds": _BoundedIntSlot(minimum=1, maximum=7 * 24 * 3600),
            # Conservative ceiling, matching this project's evidence-budget
            # ethos (evidence-reduction.md) rather than Loki's own default
            # query limit: a model-facing tool should ask for a second page
            # before it is handed thousands of lines in one call.
            "limit": _BoundedIntSlot(minimum=1, maximum=1000),
        },
        build_selector=_build_logs_for_device_selector,
        description=(
            "Every log line Loki holds for one device's management IP, most "
            "recent first, within the last `since_seconds`."
        ),
    ),
}


def known_loki_queries() -> tuple[str, ...]:
    return tuple(sorted(LOKI_QUERIES))


# --------------------------------------------------------------------------- #
# Envelope construction
# --------------------------------------------------------------------------- #


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_envelope(query_name: str, device_name: str | None) -> dict[str, Any]:
    """`network_tools._base_result`'s exact shape, rebuilt locally rather
    than imported.

    Not imported because `_base_result` is module-private (leading
    underscore) by this codebase's own convention -- every other
    cross-module `network_tools` import in this package (`checks.py`,
    `fixtures.py`, `investigation.py`, `agent_loop.py`, `cli.py`) takes only
    public names (`STATUS_ERROR`, `collect_evidence`, `run_template`, ...),
    never a private helper, and `network_tools.py` is explicitly out of
    scope for edits here (it cannot be given an `__all__`/export to make the
    coupling official). The shape itself is five lines and worth stating
    once per module rather than coupling to an unexported symbol of a file
    this change must not touch.

    **The trap this closes**: `model_egress._envelope_context` reads
    `data["intent"]` or `data["template"]` and nothing else. A successful
    `network_tools` base-intent envelope sets one of those inside
    `_attach_parsed`, one call after `_base_result` returns an empty `data:
    {}` -- so `_base_result` alone never satisfies the projector's context
    lookup. Every caller of *this* function must likewise set
    `data["intent"]` before returning; `run_named_query` does so
    immediately (`data["intent"] = query_name`), and every return path in
    this module goes through this function first, so there is exactly one
    place that could forget it.
    """

    return {
        "tool": "run_named_query",
        "device": device_name,
        "status": STATUS_SUCCESS,
        "timestamp": _timestamp(),
        "source": SOURCE_LOKI,
        "data": {},
        "errors": [],
    }


def _error_envelope(query_name: str, device_name: str | None, message: str) -> dict[str, Any]:
    envelope = _base_envelope(query_name, device_name)
    envelope["status"] = STATUS_ERROR
    envelope["errors"].append(message)
    # `data.intent` is set even on a refusal/failure -- see `_base_envelope`'s
    # docstring. An error envelope still has an identity (which query was
    # asked for), and setting it here means an error envelope is exactly as
    # projector-safe as a success one, not a second, unguarded shape.
    envelope["data"] = {
        "intent": query_name,
        "query_name": query_name,
        "parse_status": PARSE_FAILED,
        "parsed": {
            "records": [],
            "meta": {
                "lines": "0",
                "query_complete": False,
            },
        },
    }
    return envelope


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def _http_fetcher(base_url: str, params: dict[str, str]) -> dict[str, Any]:
    """The real transport. stdlib only (`urllib`), the same choice
    `notifier.py` already made rather than adding a `requests` dependency
    for one caller. Never used by a test -- see `run_named_query`'s
    `fetcher=` seam."""

    url = f"{base_url.rstrip('/')}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}"
    timeout = _float_env(LOKI_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LokiTransportError(f"loki request failed: {exc}") from exc

    if not (200 <= int(status) < 300):
        raise LokiTransportError(f"loki returned http status {status}")

    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise LokiTransportError("loki response was not valid json") from exc

    if not isinstance(parsed, dict) or parsed.get("status") != "success":
        raise LokiTransportError("loki query did not return a success status")

    return parsed


# --------------------------------------------------------------------------- #
# Record extraction
# --------------------------------------------------------------------------- #

# Confirmed against real 2026-08-18 Loki output (T-004 groundwork,
# re-verified live): the Loki copy of one line is prefixed with syslog-ng's
# rewritten `HOST` field (`P2.sota-xrd `) *before* the device's own
# `RP/0/RP0/CPU0:` node token -- the on-device `show logging` buffer carries
# no such prefix. This is therefore intentionally NOT
# `template_parsers._LOG_ENTRY` with a shared import: the wire shape genuinely
# differs by one leading token, template_parsers.py is out of scope for edits
# here, and `event_routing.py`'s own precedent (importing that private regex
# for its unprefixed syslog-receiver case) does not apply to a prefixed line.
_LOKI_LOG_LINE = re.compile(
    r"^(?P<host>\S+)\s+RP/0/RP0/CPU0:(?P<timestamp>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+\s+\w+): "
    r"(?P<process>[A-Za-z0-9_]+)\[(?P<pid>\d+)\]: %(?P<mnemonic>[A-Za-z0-9_-]+) : (?P<text>.*)$"
)


def _record_from_line(labels: Mapping[str, Any], ingest_ns: str, line: str) -> dict[str, Any]:
    """One Loki value -> one structured record, in the same field shape
    `template_parsers.py`'s `logging` template already uses
    (`mnemonic`/`facility`/`severity`/`code`/`process`/`pid`/`text`) plus the
    Loki-specific provenance fields the device buffer has no equivalent for.

    A line that does not match the expected shape is **not** dropped and
    **not** raised as a parse failure for the whole query -- it becomes a
    record with every structured field `None` and `text` holding the whole
    raw line, so it is still visible (and still free-text-protected) rather
    than silently vanishing. `meta.unparsed_lines` counts how many of these
    there were.
    """

    base: dict[str, Any] = {
        "host": labels.get("host"),
        "source_ip": labels.get("source_ip"),
        # Loki's own PRI-derived label ("err"/"warning") -- NOT the IOS-XR
        # 0-7 mnemonic severity extracted below. Kept under a distinct name
        # so the two are never confused.
        "loki_severity_label": labels.get("severity"),
        # syslog-ng stamps this destination with `timestamp("current")` --
        # ingest time, never event time (discovery-loki.md §3). Kept
        # separate from `timestamp` (the device's own clock, inside the
        # message body) for exactly that reason; a correlation step must use
        # `timestamp`, never this field, as the event time.
        "ingest_timestamp_ns": ingest_ns,
    }

    match = _LOKI_LOG_LINE.match(line)
    if not match:
        return {
            **base,
            "timestamp": "",
            "process": None,
            "pid": None,
            "mnemonic": "",
            "facility": None,
            "severity": None,
            "code": None,
            "text": line,
        }

    mnemonic = match["mnemonic"]
    parts = mnemonic.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit():
        facility, severity, code = parts
    else:
        facility = severity = code = None

    return {
        **base,
        "timestamp": match["timestamp"],
        "process": match["process"],
        "pid": match["pid"],
        "mnemonic": mnemonic,
        "facility": facility,
        "severity": severity,
        "code": code,
        "text": match["text"],
    }


def _extract_records(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    data = raw.get("data") if isinstance(raw, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise LokiTransportError("loki response was not the expected streams shape")

    records: list[dict[str, Any]] = []
    unparsed = 0
    for stream in result:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        values = stream.get("values") if isinstance(stream.get("values"), list) else []
        for entry in values:
            if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                continue
            ts_ns, line = entry
            if not isinstance(line, str):
                continue
            record = _record_from_line(labels, str(ts_ns), line)
            if not record["mnemonic"]:
                unparsed += 1
            records.append(record)
    return records, unparsed


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #


def _to_ns(dt: datetime) -> int:
    """Integer nanoseconds since the epoch, without `float` precision loss.

    `dt.timestamp() * 1e9` loses precision at this magnitude (~1.8e18, well
    past float64's ~9e15 exact-integer range); building the value from two
    integer components (whole seconds, then microseconds) keeps it exact.
    """

    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000


def run_named_query(
    query_name: str,
    *,
    fetcher: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    **params: Any,
) -> dict[str, Any]:
    """The one entry point. Never raises -- every failure (an unknown query
    name, an invalid slot, an unreachable Loki, a malformed response) comes
    back as a `status="error"` envelope in the same shape a success uses, so
    a caller -- including a model choosing a query by name -- handles both
    uniformly and never sees a traceback.

    `query_name` is looked up in :data:`LOKI_QUERIES` by exact string match.
    There is no parameter anywhere on this function, or on anything it
    calls, that accepts a LogQL string -- see the module docstring's
    "hard requirements" section for the full argument.
    """

    device_name = params.get("device") if isinstance(params.get("device"), str) else None

    query = LOKI_QUERIES.get(query_name)
    if query is None:
        return _error_envelope(
            query_name,
            device_name,
            f"unknown loki query {query_name!r}; valid: {', '.join(known_loki_queries())}",
        )

    declared = set(query.params)
    supplied = set(params)
    if declared != supplied:
        missing = sorted(declared - supplied)
        extra = sorted(supplied - declared)
        return _error_envelope(
            query_name,
            device_name,
            f"{query_name}: parameter mismatch (missing={missing}, unexpected={extra})",
        )

    try:
        # Every slot is validated before any is used to build the selector --
        # the same "validate everything, then render" order
        # `templates.render_command` uses, so a rejection always means
        # nothing was built at all.
        resolved = {name: query.params[name].parse(name, value) for name, value in params.items()}
    except LokiQueryError as exc:
        return _error_envelope(query_name, device_name, str(exc))

    selector = query.build_selector(resolved)
    if not _SELECTOR_SHAPE.fullmatch(selector):
        # Layer-4-style post-build check, mirroring
        # `templates._validate_rendered_command`'s shape check: this can only
        # fire if `LOKI_QUERIES` itself is written wrong (a future query
        # whose `build_selector` does not match its own declared shape) --
        # every caller-supplied value was already validated above, so this
        # is not reachable from any input a caller controls.
        return _error_envelope(
            query_name,
            device_name,
            f"{query_name}: built selector failed the post-build shape check: {selector!r}",
        )

    since_seconds = resolved["since_seconds"]
    limit = resolved["limit"]
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(seconds=since_seconds)

    request_params = {
        "query": selector,
        "start": str(_to_ns(start_dt)),
        "end": str(_to_ns(end_dt)),
        "limit": str(limit),
        "direction": "backward",
    }

    active_fetcher = fetcher or _http_fetcher

    try:
        raw = active_fetcher(_loki_url(), request_params)
        records, unparsed_count = _extract_records(raw)
    except LokiTransportError as exc:
        envelope = _error_envelope(query_name, device_name, str(exc))
        envelope["data"]["parsed"]["meta"]["query_window_start"] = start_dt.isoformat()
        envelope["data"]["parsed"]["meta"]["query_window_end"] = end_dt.isoformat()
        envelope["data"]["parsed"]["meta"]["since_seconds"] = since_seconds
        envelope["data"]["parsed"]["meta"]["limit"] = limit
        return envelope

    total_before_dedup = len(records)
    # B-206b / discovery-loki.md §6.2: one event stored 1,346 times in the
    # T-004 sample. `log_window.dedupe` keys on (device timestamp, mnemonic,
    # text), never ingest time -- exactly the shape this module's records
    # use, by construction (see `_record_from_line`).
    deduped = log_window.dedupe(records)
    duplicates_removed = total_before_dedup - len(deduped)

    parsed = {
        "records": deduped,
        "meta": {
            "lines": str(len(deduped)),
            "query_window_start": start_dt.isoformat(),
            "query_window_end": end_dt.isoformat(),
            "since_seconds": since_seconds,
            "limit": limit,
            "records_before_dedup": total_before_dedup,
            "duplicates_removed": duplicates_removed,
            "unparsed_lines": unparsed_count,
            "query_complete": True,
        },
    }

    envelope = _base_envelope(query_name, device_name)
    envelope["data"] = {
        "intent": query_name,
        "query_name": query_name,
        # Safe to expose verbatim: reconstructed entirely from validated,
        # canonical values (see `_build_logs_for_device_selector`), never
        # from caller text. Useful for an operator reading the envelope by
        # eye; not itself device-authored or free text.
        "logql": selector,
        "parse_status": PARSE_OK,
        "parsed": parsed,
    }
    return envelope


# --------------------------------------------------------------------------- #
# Coverage: absence-is-not-zero, exposed for the wide step to consume
# --------------------------------------------------------------------------- #


def coverage_from_loki(parsed: dict[str, Any], device: str) -> Coverage:
    """Build a `coverage.Coverage` from one `run_named_query()` result's
    `data.parsed`. Mirrors `log_window.coverage_from_logging`'s layering
    exactly: this module's own functions never build or consume a
    `Coverage` themselves (`run_named_query` does not call this), the same
    way `network_tools.run_template` never calls `coverage_from_logging` --
    a downstream, wide-step consumer (`investigation.py`'s own call to
    `coverage_from_logging` is the existing precedent) calls this once it
    has decided the window is relevant. **Not wired into that consumer by
    this change** -- exposing the adapter, not consuming it, per this
    build's own instruction.

    `severity_available` is `MEASURED_SEVERITY_AVAILABLE`, not something read
    from `parsed` -- see that constant's own docstring for why Loki's API
    cannot self-report this the way `show logging`'s header can.
    """

    meta = parsed.get("meta") or {}
    limit = meta.get("limit")
    before_dedup = meta.get("records_before_dedup")

    notes: list[str] = []
    if isinstance(limit, int) and isinstance(before_dedup, int) and limit > 0 and before_dedup >= limit:
        notes.append(
            f"the query returned exactly its requested limit ({limit}); more "
            "matching records may exist in this window and were not retrieved"
        )

    return Coverage(
        device=device,
        source=SOURCE_LOKI,
        query_complete=bool(meta.get("query_complete", False)),
        window_start=meta.get("query_window_start"),
        window_end=meta.get("query_window_end"),
        severity_available=MEASURED_SEVERITY_AVAILABLE,
        records_available=None,
        records_returned=len(parsed.get("records") or ()),
        records_dropped_at_source=0,
        notes=tuple(notes),
    )
