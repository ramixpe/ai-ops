"""A read-only Prometheus metrics adapter: Stage-2 M5b, the temporal evidence
axis's second source (`logs_loki.py` is the first).

Grounding
---------
History is a first-class input: utilisation trend and CPU/packet-drop history
are read as *context* for the wide
flow step, never as a descent rung. The load-bearing sentence: **"absence of
a sample is not a value of zero, and the parse/trust layer must distinguish
them the same way `checks.py` already distinguishes `unevaluated` from
`broken`."** This module is the read side only -- it fetches and shapes; it
is not wired into `flows.py`/`checks.py`/`investigation.py`/`cli.py`/the MCP
surface (out of scope by the build's own instruction, and `logs_loki.py`'s
precedent: "expose the adapter, do not consume it in a descent").

The original alerting survey measured the groundwork this module implements
against, reconfirmed live 2026-08-19: Prometheus v2.51.2 at
the lab's management address, 572 metric names, the `source` label carrying
bare device names (`P1`...`RR1`) exactly as `inventory/lab.yaml` spells them
-- **no translation needed**, unlike Loki's IP-based join. Retention is `1w or
500MiB` (`/api/v1/status/runtimeinfo`), which is why
`since_seconds`'s ceiling below and the existence-check lookback are both one
week: asking further back than the source retains is a caller error worth
refusing/bounding explicitly, the same reasoning `logs_loki.LOKI_QUERIES`
gives for its own `since_seconds` ceiling.

Two live findings shape what this module builds and what it deliberately does
not (§5 below):

* **The device join key is exact.** `source="PE1"` needs no reconstruction
  the way Loki's `mgmt_ip` does -- but `_DeviceSlot` still re-validates by
  inventory lookup *and* a closed-charset reconstruction of `device.name`,
  never trusting the caller's or the inventory's own prior validation, for
  the same "canonicalize by reconstruction, never pass-through" reason
  `logs_loki._DeviceSlot` gives.
* **A single BGP neighbor series carries ~150 labels**, including
    `peer_reset_reason`/`reset_reason`, a cardinality smell. This module does not
    query BGP metrics -- still
  true after the B-530 TSDB survey added LDP and device-uptime history --
  specifically *because* every metric family it does query (IS-IS adjacency
  uptime, `infra_statsd_oper` interface counters, LDP session uptime,
  device uptime) was measured to carry **no free-text label at all**, and
  this module is built to keep that true by construction rather than by
  filtering after the fact (see "Why this module needs zero new
  `FREE_TEXT_FIELDS` entries" below). **The BGP argument was re-verified
  live, not merely reasserted, for this survey -- see "Why BGP is still
  not queried" further down**, which corrects the reasoning this bullet
  used to give: querying live `/api/v1/label/reset_reason/values` found 8
  distinct values (and 2 for `peer_reset_reason`) recorded over the
  retention window, all short protocol-code tokens (`admin-shutdown`,
  `peer-closed`, ...) -- **not obviously prose a model could be talked into
  treating as an instruction**, so the disqualifying property is not
  "free text" after all. It is that those values changing at all means
  Prometheus is storing MULTIPLE historical series per neighbor for what
  every other query in this module treats as one stable, lifelong series
  per identity -- a real, different, and unsolved problem this survey does
  not attempt to fix.

The three hard requirements this module exists to satisfy (identical to
`logs_loki.py`'s, restated because the "how" differs enough to be worth
spelling out again rather than pointing at a sibling and hoping the mapping
is obvious)
--------------------------------------------------------------------------
1. **PromQL needs an allowlist analogue, so this module is one.**
   :data:`PROMETHEUS_QUERIES` is an exact-match table of named queries with
   typed, validated slots -- `templates.py`'s discipline, carried to a query
   language with no allowlist of its own, the same move `logs_loki.py` already
   made for LogQL. There is no function anywhere in this module that accepts
   a caller-supplied PromQL string. Every value that ends up inside a PromQL
   selector is either (a) a device name, re-looked-up through
   `inventory_model.find_device` and re-validated against a closed charset
   (`_DeviceSlot`), (b) an interface name, validated against the same
   anchored-charset regex `templates._parse_interface_name` uses -- rebuilt
   locally rather than imported, for the same "must not couple to a frozen,
   differently-scoped validator" reasoning `logs_loki._BoundedIntSlot`'s own
   docstring gives for not importing `templates.BoundedIntParam` -- or (c) a
   counter's Prometheus metric name, drawn from a fixed, module-internal
   dict (`_COUNTER_METRICS`) the caller can only select from by an
   allowlisted short name, never author.
2. **The envelope shape is `network_tools._base_result`'s shape.**
   `{tool, device, status, timestamp, source, data, errors}`, rebuilt locally
   for the exact reason `logs_loki._base_envelope`'s docstring gives (that
   function is module-private and `network_tools.py` is out of scope for
   edits here). `source="prometheus"` is the literal spelling
   `network_tools._source_for`'s own docstring names for "a brand-new,
   non-SSH source... it would build its own envelope with
   `_base_result(..., source="loki")` directly" -- the same sentence, read
   for the sibling case it was always meant to cover.
3. **Every return path sets `data["intent"]`.** `model_egress._envelope_context`
   reads `data["intent"]` or `data["template"]` and nothing else -- the exact
   trap `logs_loki._base_envelope`'s docstring names, and this module closes
   it the identical way: `_base_envelope` never sets it, every caller
   (`_error_envelope`, `run_named_query`'s success path) does, and there is
   exactly one place in this module that could forget.

Why this module needs zero new `FREE_TEXT_FIELDS` entries
-----------------------------------------------------------
`logs_loki.py` reuses `("logging", "text")`/`("logging", "code")` because a
Loki record's free-text fields are unavoidable -- correlation needs the
syslog line's own prose. None of this module's four named queries construct
a record that way: :func:`_shape_interface_rate_records`,
:func:`_shape_isis_adjacency_records`, :func:`_shape_ldp_session_records`
and :func:`_shape_device_uptime_records` each pull a **fixed, named, closed
set** of label keys out of Prometheus's `metric` dict via `.get(...)` --
never `**metric`, never "everything this series happened to carry." A
Prometheus series for each of this module's four metric families was
measured live (2026-08-19, the LDP/uptime two as part of the B-530 TSDB
survey) to carry only structured labels -- device/interface identifiers,
YANG enum states (`neighbor_state`, `neighbor_circuit_type`, LDP's own
`peer_state`), numeric values -- never a description-shaped free-text leaf.
LDP's own series carries two labels that DO look free-text-shaped at first
read (`capabilities_received_description`/`capabilities_sent_description`,
e.g. `"MP: Multi-Topology (MT)"`) -- deliberately not extracted by
:func:`_shape_ldp_session_records` even though they were considered, the
same "extract only what was reviewed" discipline applied rather than
assumed safe by pattern-matching against the isis/interface precedent. So
the free-text risk the build brief warns about ("a metric label value is
device-authored... an interface description can appear in one") is real *in
general* -- the original alerting survey measured it on the BGP series this
module still does not touch, see "Why BGP is still not queried" below -- and
is closed here **structurally**, by never extracting a label this module has
not named, rather than by wrapping a value after the fact.
`test_an_unnamed_hostile_label_never_reaches_the_shaped_record` in the test
suite is the canary: it injects an injection-shaped, prose-bearing label
under a key neither shaper reads and asserts it is absent from the output,
not merely wrapped.

If a future query needs a metric whose labels *do* carry free text, it must
add `(query_name, field)` to `model_egress.FREE_TEXT_FIELDS` and
`mcp_server/boundary.py`'s copy in the same commit, per the project's
instruction -- and per that same instruction, should prefer reusing an
existing field name (`"text"`) over inventing a new one, for the reason
`logs_loki.py`'s own "Field-name choice" section gives (`boundary.sanitize`
matches by name alone, with no context scoping).

Why BGP is still not queried (re-verified, B-530 TSDB survey)
----------------------------------------------------------------
Re-examined rather than reasserted, because "the module already says no to
BGP" is not, by itself, still a reason once two more families have been
added past the original two. Queried live 2026-08-19:
`{__name__=~"Cisco_IOS_XR_ipv4_bgp_oper.*"}` returns series across all 257
BGP metric names, and **every single one** carries `reset_reason` and
`peer_reset_reason` as labels -- gNMI/telemetry export attaches every
sibling leaf of a YANG table row to every metric derived from that row, so
there is no metric NAME that is "the safe one"; the two fields are not a
column a caller could ask to leave out of the request.

That alone would not disqualify BGP -- this module's shape functions
already extract only named labels, so *not calling* `.get("reset_reason")`
in a hypothetical `bgp_session_history` shaper would keep those two labels
out of the response, the same way `_shape_ldp_session_records` leaves
`capabilities_*_description` out despite it being present on the series.
Sampling the actual label VALUES is what disqualifies it:
`/api/v1/label/reset_reason/values` returns 8 distinct values recorded over
the retention window (`admin-shutdown`, `af-activated`, `af-deactivated`,
`bgp-none`, `not-received`, `not-sent`, `peer-closed`,
`rr-client-changed`); `peer_reset_reason` returns 2. Short protocol-code
tokens, not prose -- so the earlier characterisation of these specifically
as a *prompt-injection* risk does not hold up to this sampling, and is
corrected here rather than repeated.

What the 8-and-2 measurement DOES prove: these labels are not constant for
a neighbor's whole lifetime the way `lsr_id`/`system_id`/`source` are for
every family this module DOES query. A label that changes value means
Prometheus opens a NEW series each time it changes and leaves the old one
to go stale, un-scraped, still indexed. Every existence/coverage mechanism
this module relies on (`_series_known`, `records_available`,
`coverage_from_prometheus_history`'s four absence cases) is built on "one
selector matches one stable series (or one stable series per adjacency),
and a gap in it means a real gap" -- a BGP selector matching one neighbor
would instead match however many of these churned, mostly-dead series still
fall inside the query window, with no field in this module's existing
vocabulary for "which of these several results is the CURRENT one" versus
"an abandoned series from three resets ago." That is a different, harder
problem than absence-vs-zero, this module has no mechanism for it today,
and building one was out of scope for this survey -- so BGP metrics remain
unqueried, for a corrected and re-verified reason rather than the original
one.

Absence is not zero -- the axis this module exists to get right
-------------------------------------------------------------------
A time series and a log stream fail differently. Loki's failure mode is a
*severity floor* (some records never arrive at all, at any time, structurally
-- `MEASURED_SEVERITY_AVAILABLE`). Prometheus's is a *scrape gap*: a counter
that stops being scraped for part of a window is, from the returned matrix
alone, indistinguishable from a counter that reported real zeros for that
same span. Two structural devices close this, mirroring `coverage_from_loki`'s
"declared, not derived" discipline but adapted to what a time-series API can
actually self-report:

* **Within a window: expected-vs-returned sample counts.** For
  `interface_rate_history` (an exact-match, single-series selector), the
  number of evaluation points Prometheus *should* have produced is computable
  exactly from `since_seconds`/`step_seconds` (the same grid formula
  `/api/v1/query_range` itself uses: `floor((end-start)/step) + 1`) --
  `coverage.Coverage.records_available` vs `.records_returned`, and
  `.truncated`/`.gaps()` fire automatically when they disagree, exactly the
  mechanism `coverage_from_loki` already reuses rather than a parallel
  concept of completeness. `isis_adjacency_history` matches an unknown number
  of series (one per adjacency, not knowable ahead of the call), so it
  reports `records_available=None` -- the same honest "unknown total" choice
  `coverage_from_loki` makes for Loki's own record count.
* **Across the whole retention window: has this series ever existed at
  all?** A window that returns zero points is not one fact -- it is *at
  least* two: "this device/interface/metric combination has never been
  scraped" (a wrong slot, not a gap) and "it has been scraped before, just
  not in this window" (a real collection interruption -- the device stopped
  reporting, or interface went away). `/api/v1/series`, queried with a
  **separate, wider** time range (:data:`DEFAULT_EXISTENCE_LOOKBACK_SECONDS`,
  matching Prometheus's own measured one-week retention) tells the two apart,
  because it reports every label-set Prometheus has ever indexed in that
  range independent of whether the narrow window has samples. This is a
  *second* HTTP call, made **only** when the primary range query returned
  zero points -- see :func:`_series_known` -- and its own failure never
  flips the overall envelope to `status="error"`: the primary read already
  succeeded, so `meta.series_known` becomes `None` with an honest note rather
  than the whole result being discounted for an auxiliary check that
  couldn't complete.

Together these are the four cases the build brief asks to be tested, and
:func:`coverage_from_prometheus_history` is what a wide-step consumer reads
to tell them apart -- see that function's docstring for the mapping.

Rate, not raw counters
-----------------------
`infra_statsd_oper`'s counters are cumulative (bytes/packets/drops/errors
since the last clear). "Utilisation... sudden drop, sudden increase" (§2.4a)
is a question about *rate of change*, not the raw cumulative value -- and a
raw counter's own resets (a device reboot, `clear counters`) would otherwise
look exactly like a nonsensical negative delta. `interface_rate_history`
therefore queries `rate(<metric>{...}[<window>s])`, Prometheus's own
reset-aware rate function, evaluated every `step_seconds` -- not a
client-side delta this module would have to get reset-detection right for
itself. This is a `str.format`-built PromQL fragment from only validated
slot values, the exact same reconstruction discipline as the selector
itself; see `_INTERFACE_RATE_SHAPE` for the post-build check that would
catch a `build_selector` bug before any HTTP call is made.

The range-vector `[<window>s]` is `_RATE_WINDOW_SECONDS` (a fixed 60s), **not**
`step_seconds` -- a first version tied it to `step_seconds` and, measured
live 2026-08-19, that returned **empty** even for a genuinely nonzero counter
(`bytes_sent` on a live interface): `rate()` needs at least two raw samples
inside its lookback, and a window equal to the ~15s scrape interval can
legitimately contain zero or one sample depending on evaluation-timestamp
alignment. `_RATE_WINDOW_SECONDS`'s own docstring has the measurement and the
~4x-scrape-interval reasoning.

Testing seam
------------
`fetcher=` on :func:`run_named_query`, the same injection idiom
`logs_loki.run_named_query`'s `fetcher=` and `network_tools`'s `sender=` use.
Unlike Loki's fetcher (one endpoint, `query_range`), this module's fetcher
takes a `path` argument (`_http_fetcher(base_url, path, params) -> dict`)
because a single query can make up to two calls -- `/api/v1/query_range` for
the data, `/api/v1/series` for the existence check -- and a test fetcher
needs to answer both from one canned-response table, keyed on `path`. The
default `_http_fetcher` is never used by a test; the real transport is
stdlib-only `urllib`, the same choice `notifier.py`/`logs_loki.py` already
made.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from ._env import _float_env, _int_env
from .coverage import Coverage
from .interface_kind import canonical
from .inventory_model import find_device
from .parsers import PARSE_FAILED, PARSE_OK

__all__ = [
    "DEFAULT_EXISTENCE_LOOKBACK_SECONDS",
    "DEFAULT_PROMETHEUS_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXISTENCE_LOOKBACK_ENV",
    "MAX_SAMPLES_PER_QUERY",
    "PROMETHEUS_QUERIES",
    "PROMETHEUS_TIMEOUT_ENV",
    "PROMETHEUS_URL_ENV",
    "SOURCE_PROMETHEUS",
    "STATUS_ERROR",
    "STATUS_SUCCESS",
    "PrometheusQuery",
    "PrometheusQueryError",
    "PrometheusTransportError",
    "coverage_from_prometheus_history",
    "known_prometheus_queries",
    "run_named_query",
]

# --------------------------------------------------------------------------- #
# Envelope vocabulary
# --------------------------------------------------------------------------- #

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

#: See `network_tools._source_for`'s own docstring: "a brand-new, non-SSH
#: source (Loki, NetBox)... would build its own envelope with
#: `_base_result(..., source="loki")` directly, since `source` is a plain
#: string with no enum behind it." This is that same sentence's Prometheus
#: case.
SOURCE_PROMETHEUS = "prometheus"

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

PROMETHEUS_URL_ENV = "NETTOOLS_PROMETHEUS_URL"
#: Measured 2026-08-15 and reconfirmed live 2026-08-19 (`/-/ready` -> 200).
#: A container IP, not a guaranteed-stable
#: service address -- `_prometheus_url()` is env-then-this-default, and every
#: call site reads that function rather than this constant directly, the same
#: convention `logs_loki.DEFAULT_LOKI_URL`'s own docstring explains.
DEFAULT_PROMETHEUS_URL = "http://172.20.250.102:9090"

PROMETHEUS_TIMEOUT_ENV = "NETTOOLS_PROMETHEUS_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 10.0

EXISTENCE_LOOKBACK_ENV = "NETTOOLS_PROMETHEUS_EXISTENCE_LOOKBACK_SECONDS"
#: Measured 2026-08-19: `/api/v1/status/runtimeinfo` reports
#: `"storageRetention": "1w or 500MiB"`. Asking `/api/v1/series` to look back
#: further than the source retains cannot distinguish "never observed" from
#: "observed, then aged out" -- so this matches the measured retention rather
#: than being picked arbitrarily. Same "declared, dated, human-updates-it"
#: discipline as `logs_loki.MEASURED_SEVERITY_AVAILABLE`.
DEFAULT_EXISTENCE_LOOKBACK_SECONDS = 7 * 24 * 3600

#: Ceiling on the number of evaluation points one `interface_rate_history`
#: call may request (`since_seconds // step_seconds + 1`), enforced before
#: any HTTP call -- the same evidence-budget ethos as `logs_loki`'s `limit`
#: slot ceiling (1000), not a value Prometheus itself imposes.
MAX_SAMPLES_PER_QUERY = 1000


def _prometheus_url() -> str:
    return os.getenv(PROMETHEUS_URL_ENV, "").strip() or DEFAULT_PROMETHEUS_URL


def _existence_lookback_seconds() -> int:
    return _int_env(EXISTENCE_LOOKBACK_ENV, DEFAULT_EXISTENCE_LOOKBACK_SECONDS)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class PrometheusQueryError(ValueError):
    """A named query is unknown, or one of its slots failed validation.

    Never raised out of :func:`run_named_query` -- caught there and turned
    into a `status="error"` envelope, the same discipline
    `logs_loki.LokiQueryError` documents for the identical reason.
    """


class PrometheusTransportError(Exception):
    """An HTTP call to Prometheus failed, or its response was not usable.

    Raised by the default fetcher (`_http_fetcher`) and by any test fetcher
    simulating a failure, for *either* endpoint this module calls
    (`/api/v1/query_range` or `/api/v1/series`). Caught by
    :func:`run_named_query` for the primary call (turned into a
    `status="error"` envelope) and by :func:`_series_known` for the
    existence-check call (turned into `series_known=None` plus a note,
    never a failed envelope -- see that function's docstring).
    """


# --------------------------------------------------------------------------- #
# Named-query table: the PromQL allowlist analogue
# --------------------------------------------------------------------------- #

_DEVICE_NAME_SHAPE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class _DeviceSlot:
    """A bare device name (`"PE1"`), validated against the inventory.

    Unlike `logs_loki._DeviceSlot` (which must translate a device name to an
    IP because Loki's `host` label carries a syslog-ng rewrite this repo does
    not own), Prometheus's `source` label already carries the bare inventory
    name ("no mapping, no suffix"). But "the join
    key needs no translation" is not the same claim as "the caller's string
    is safe to embed" -- so this slot still re-looks-up the name through
    `inventory_model.find_device` and re-validates the *returned* record's
    own `.name` against a closed charset before using it, never trusting
    either the caller's text or the inventory's own prior pydantic
    validation. Same "canonicalize by reconstruction" reasoning as
    `logs_loki._DeviceSlot.parse`'s `ipaddress.IPv4Address` re-parse -- see
    `test_a_corrupted_inventory_name_is_refused_not_forwarded` for the
    defence-in-depth case this exists to catch.
    """

    def parse(self, name: str, value: object) -> str:
        if not isinstance(value, str):
            raise PrometheusQueryError(f"{name}: expected a string, got {type(value).__name__}")
        if not value:
            raise PrometheusQueryError(f"{name}: must not be empty")
        device = find_device(value)
        if device is None:
            raise PrometheusQueryError(f"{name}: {value!r} is not in the lab inventory")
        canonical = device.name
        if not isinstance(canonical, str) or not _DEVICE_NAME_SHAPE.fullmatch(canonical):
            # Unreachable from any caller input `inventory_model` itself would
            # accept -- pydantic already constrains `Device.name` -- but
            # reached directly by this module's own adversarial-hunt test via
            # monkeypatching `find_device`, the same way
            # `logs_loki`'s corrupted-mgmt_ip test does. Phrased to reuse the
            # existing "contains a forbidden character" classifier entry
            # (model_egress.ERROR_KINDS / mcp_server.boundary.ERROR_KINDS)
            # rather than adding a new one for an unreachable branch.
            raise PrometheusQueryError(
                f"{name}: inventory device name for {value!r} contains a forbidden character"
            )
        return canonical


_INTERFACE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./-]{0,62}$")


class _InterfaceSlot:
    """An interface name, validated by the identical anchored-charset regex
    `templates._parse_interface_name` uses -- rebuilt locally, not imported,
    for the reason `logs_loki._BoundedIntSlot`'s own docstring gives for not
    reusing `templates.BoundedIntParam`: this module's callers are
    programmatic, `templates.py` is frozen and governs a different command
    surface (device commands, not a metrics query), and this module must not
    couple to it. Deliberately worded to match `templates.py`'s own "not a
    valid interface name" phrase so a refusal classifies through the
    *existing* `ERROR_KINDS` entry both `model_egress.py` and
    `mcp_server/boundary.py` already carry -- no new entry needed.
    """

    def parse(self, name: str, value: object) -> str:
        if not isinstance(value, str):
            raise PrometheusQueryError(f"{name}: expected a string, got {type(value).__name__}")
        if not value:
            raise PrometheusQueryError(f"{name}: must not be empty")
        if not _INTERFACE_NAME_RE.fullmatch(value) or ".." in value:
            raise PrometheusQueryError(f"{name}: not a valid interface name: {value!r}")
        # Expand an abbreviated name to the spelling the telemetry actually
        # stores. gNMI writes `interface_name="GigabitEthernet0/0/0/0"`, while
        # `show interfaces brief` -- and therefore `check_lab_interfaces`, and
        # therefore any model that lists interfaces before asking for their
        # history -- says `Gi0/0/0/0`. Matching the label literally meant that
        # workflow returned zero samples with `series_known=False`, whose note
        # reads "no series matching this device/interface/metric selector has
        # been observed": a confidently wrong answer that a model has no way to
        # doubt (measured 2026-08-19 over 68 consecutive calls, OBS-202).
        #
        # `canonical` expands rather than merely comparing, and is valid here
        # for the reason its own docstring sets: the expansion is scoped to one
        # device, because the device is a separate validated slot in the same
        # selector.
        return canonical(value)


#: Short, caller-facing counter name -> the real Prometheus metric name.
#: Measured live 2026-08-19 against `/api/v1/label/__name__/values`: all ten
#: exist under the `Cisco_IOS_XR_infra_statsd_oper:infra_statistics_
#: interfaces_interface_latest_generic_counters_*` family discovery-
#: alerting.md §3 names ("bytes/packets sent+received... All currently 0
#: across all nine devices" for the error/drop ones). A caller can only ever
#: select a *key* of this dict (`_CounterSlot.parse` checks membership); the
#: metric-name *value* is module-internal and never caller-authored, so it
#: needs no separate reconstruction step the way a caller-supplied string
#: would.
_COUNTER_METRICS: dict[str, str] = {
    "bytes_received": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_bytes_received",
    "bytes_sent": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_bytes_sent",
    "packets_received": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_packets_received",
    "packets_sent": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_packets_sent",
    "input_errors": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_input_errors",
    "output_errors": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_output_errors",
    "input_drops": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_input_drops",
    "output_drops": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_output_drops",
    "crc_errors": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_crc_errors",
    "carrier_transitions": "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_latest_generic_counters_carrier_transitions",
}


class _CounterSlot:
    """A caller-facing short name, validated by exact membership in
    :data:`_COUNTER_METRICS`. Returns the short name unchanged (not the
    metric name) so `meta["counter"]` stays human-readable; `build_selector`
    does the dict lookup itself at render time."""

    def parse(self, name: str, value: object) -> str:
        if not isinstance(value, str):
            raise PrometheusQueryError(f"{name}: expected a string, got {type(value).__name__}")
        if value not in _COUNTER_METRICS:
            raise PrometheusQueryError(
                f"{name}: {value!r} is not an allowlisted counter; valid: "
                f"{', '.join(sorted(_COUNTER_METRICS))}"
            )
        return value


class _BoundedIntSlot:
    """A plain Python `int`, restricted to an inclusive `[minimum, maximum]`.
    Identical shape to `logs_loki._BoundedIntSlot` -- a third copy of that
    idiom, deliberately not imported for the same "must not couple to a
    sibling's private helper" reasoning `logs_loki.py` itself gives for not
    reusing `templates.BoundedIntParam`."""

    def __init__(self, *, minimum: int, maximum: int) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def parse(self, name: str, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PrometheusQueryError(f"{name}: expected an integer, got {type(value).__name__}")
        if not (self.minimum <= value <= self.maximum):
            raise PrometheusQueryError(
                f"{name}: must be between {self.minimum} and {self.maximum}, got {value}"
            )
        return value


#: `rate()`'s own range-vector lookback -- deliberately **independent** of
#: the caller's `step_seconds` (which only controls how often the range
#: query *evaluates* `rate(...)`, not how many raw samples feed each
#: evaluation). Measured live 2026-08-19: consecutive raw samples on this
#: pipeline land ~15s apart. Standard PromQL practice (and what Grafana's own
#: `$__rate_interval` computes to) is a rate window of at least ~4x the
#: scrape interval, so at least two raw samples reliably fall inside it
#: regardless of evaluation-timestamp alignment -- a window equal to the
#: scrape interval itself was tried first and measured to return **empty**
#: even for a genuinely nonzero counter (`bytes_sent` on a live interface),
#: because a 15s lookback ending at an arbitrary evaluation instant can
#: legitimately contain zero or one raw sample, and `rate()` needs two.
_RATE_WINDOW_SECONDS = 60

#: `rate(<metric>{source="<device>",interface_name="<interface>"}[60s])`.
#: Structural, not tied to one metric name: the metric-name alphabet
#: (`[A-Za-z0-9_:]+`) is checked for *shape* here because `_COUNTER_METRICS`
#: already guarantees the *value* is one of ten fixed, safe strings -- the
#: regex's job is proving the surrounding selector syntax cannot be broken
#: out of by anything reconstructed from a validated slot, the same
#: division of labour `logs_loki._SELECTOR_SHAPE` uses.
_INTERFACE_RATE_SHAPE = re.compile(
    r'^rate\([A-Za-z0-9_:]+\{source="[A-Za-z0-9_-]{1,32}",'
    r'interface_name="[A-Za-z][A-Za-z0-9_./-]{0,62}"\}\[[0-9]{1,4}s\]\)$'
)

#: The SAME selector without the `rate(...)[window]` wrapper -- what
#: `/api/v1/series` (the existence check, `_series_known`) needs. Measured
#: live 2026-08-19: `/api/v1/series?match[]=rate(...)` is refused outright
#: (`400 Bad Request`, `"unexpected \"(\""`) -- that endpoint's `match[]`
#: accepts only a plain vector selector, never a PromQL function expression.
#: `PrometheusQuery.existence_selector` exists specifically so a query whose
#: *data* selector is wrapped in a function (this one) can still supply a
#: bare selector for the existence check, while a query whose data selector
#: is already bare (`isis_adjacency_history`) can reuse it unchanged.
_INTERFACE_BARE_SHAPE = re.compile(
    r'^[A-Za-z0-9_:]+\{source="[A-Za-z0-9_-]{1,32}",'
    r'interface_name="[A-Za-z][A-Za-z0-9_./-]{0,62}"\}$'
)


def _build_interface_rate_selector(resolved: Mapping[str, Any]) -> str:
    metric = _COUNTER_METRICS[resolved["counter"]]
    return 'rate({0}{{source="{1}",interface_name="{2}"}}[{3}s])'.format(
        metric, resolved["device"], resolved["interface"], _RATE_WINDOW_SECONDS
    )


def _build_interface_bare_selector(resolved: Mapping[str, Any]) -> str:
    metric = _COUNTER_METRICS[resolved["counter"]]
    return '{0}{{source="{1}",interface_name="{2}"}}'.format(
        metric, resolved["device"], resolved["interface"]
    )


def _shape_interface_rate_records(
    result: list, resolved: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`interface_rate_history`'s response shaper: an exact-match selector
    (one device, one interface, one counter) matches **at most one** series,
    so the expected sample count is computable exactly from the request's
    own `since_seconds`/`step_seconds` -- mirroring the grid
    `/api/v1/query_range` itself evaluates on. This is what lets
    `coverage_from_prometheus_history` report a precise `records_available`
    for this query, unlike `isis_adjacency_history`'s unknown series count.
    """

    since_seconds = resolved["since_seconds"]
    step_seconds = resolved["step_seconds"]
    expected = since_seconds // step_seconds + 1

    records: list[dict[str, Any]] = []
    if result:
        series = result[0] if isinstance(result[0], dict) else {}
        values = series.get("values") if isinstance(series.get("values"), list) else []
        for entry in values:
            if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                continue
            ts, val = entry
            try:
                timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                rate = round(float(val), 4)
            except (TypeError, ValueError):
                continue
            records.append({"timestamp": timestamp, "rate_per_second": rate})

    return records, {
        "records_returned": len(records),
        "records_available": expected,
        "counter": resolved["counter"],
        "metric_name": _COUNTER_METRICS[resolved["counter"]],
        "interface": resolved["interface"],
    }


#: `Cisco_IOS_XR_clns_isis_oper:...neighbor_uptime{source="<device>"}` --
#: device-wide, matching every adjacency at once (one series per neighbor).
_ISIS_UPTIME_METRIC = (
    "Cisco_IOS_XR_clns_isis_oper:isis_instances_instance_neighbors_neighbor_neighbor_uptime"
)

_ISIS_ADJACENCY_SHAPE = re.compile(
    r'^' + re.escape(_ISIS_UPTIME_METRIC) + r'\{source="[A-Za-z0-9_-]{1,32}"\}$'
)


def _build_isis_adjacency_selector(resolved: Mapping[str, Any]) -> str:
    return '{0}{{source="{1}"}}'.format(_ISIS_UPTIME_METRIC, resolved["device"])


def _samples_and_resets(values: list) -> tuple[list[dict[str, Any]], list[str]]:
    """One Prometheus series' raw ``[[timestamp, value], ...]`` -> a typed
    sample list plus the timestamps at which the value *dropped* between
    consecutive samples.

    Shared by every "seconds since this thing last came up" gauge this
    module reads -- IS-IS `neighbor_uptime`, LDP `ta_up_time_seconds`
    (B-530), and the device's own `system_time_uptime_uptime` (B-530): a
    drop means exactly the same thing in each, it went down (or rebooted)
    and came back, and it is otherwise indistinguishable from ordinary
    counter growth. Extracted from `_shape_isis_adjacency_records` (B-530)
    rather than reimplemented for the two new callers -- see that function's
    own docstring and the two new ones below for what each calls a "reset"
    (an adjacency flap; an LDP session flap; a reboot).
    """

    samples: list[dict[str, Any]] = []
    reset_timestamps: list[str] = []
    previous: float | None = None
    for entry in values:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        ts, val = entry
        try:
            timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            value = float(val)
        except (TypeError, ValueError):
            continue
        samples.append({"timestamp": timestamp, "uptime_seconds": value})
        if previous is not None and value < previous:
            reset_timestamps.append(timestamp)
        previous = value
    return samples, reset_timestamps


def _shape_isis_adjacency_records(
    result: list, resolved: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`isis_adjacency_history`'s response shaper: one record per adjacency
    (one Prometheus series), each carrying its own sample history plus a
    derived flap signal -- `neighbor_uptime` is seconds-since-last-came-up,
    so a value that *drops* between consecutive samples means the adjacency
    reset (went down and came back), the "sudden drop" §2.4a asks history to
    surface (`_samples_and_resets`). Only a fixed, named set of label keys is
    ever read from `series["metric"]` (never the whole dict) -- see the
    module docstring's "why this module needs zero new FREE_TEXT_FIELDS
    entries" section.
    """

    records: list[dict[str, Any]] = []
    total_samples = 0

    for series in result:
        if not isinstance(series, dict):
            continue
        metric = series.get("metric") if isinstance(series.get("metric"), dict) else {}
        values = series.get("values") if isinstance(series.get("values"), list) else []

        samples, reset_timestamps = _samples_and_resets(values)

        total_samples += len(samples)
        records.append(
            {
                "interface_name": metric.get("interface_name"),
                "neighbor_system_id": metric.get("system_id"),
                "neighbor_state": metric.get("neighbor_state"),
                "neighbor_circuit_type": metric.get("neighbor_circuit_type"),
                "samples": samples,
                "reset_count": len(reset_timestamps),
                "reset_timestamps": reset_timestamps,
            }
        )

    return records, {
        "records_returned": total_samples,
        "records_available": None,
        "adjacency_count": len(records),
    }


#: `Cisco_IOS_XR_mpls_ldp_oper:...ta_up_time_seconds{source="<device>"}` --
#: device-wide, one series per LDP session (matches `_ISIS_UPTIME_METRIC`'s
#: own "one selector, many adjacencies" shape). Chosen over the two metrics
#: named in this survey's brief (`peer_holdtime`, session-protection
#: `spht_remaining`/`sp_duration`) after measuring all three live 2026-08-19:
#: `peer_holdtime` is the CONFIGURED hold timer (180s on every one of this
#: fabric's 28 sessions, never varying -- static configuration, not a trend);
#: `spht_remaining`/`sp_duration` are session-protection fields and this
#: fabric configures session protection on zero sessions
#: (`detailed_information_has_sp="false"` on all 28), so both read a
#: constant 0 everywhere -- exposing either would look diagnostic and never
#: actually vary. `ta_up_time_seconds` is LDP's own direct counterpart to
#: `neighbor_uptime` -- seconds since this session last came up, genuinely
#: live (measured: 2,885s to 511,698s across this fabric's real sessions) --
#: and reuses the identical flap-detection shape `_shape_isis_adjacency_records`
#: already established, via `_samples_and_resets`.
_LDP_UPTIME_METRIC = (
    "Cisco_IOS_XR_mpls_ldp_oper:mpls_ldp_global_active_default_vrf_neighbors_"
    "neighbor_protocol_information_ta_up_time_seconds"
)

_LDP_SESSION_SHAPE = re.compile(
    r'^' + re.escape(_LDP_UPTIME_METRIC) + r'\{source="[A-Za-z0-9_-]{1,32}"\}$'
)


def _build_ldp_session_selector(resolved: Mapping[str, Any]) -> str:
    return '{0}{{source="{1}"}}'.format(_LDP_UPTIME_METRIC, resolved["device"])


def _shape_ldp_session_records(
    result: list, resolved: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`ldp_session_history`'s response shaper -- `_shape_isis_adjacency_
    records`'s exact shape, one record per LDP session. `interface_name` is
    read from the slash-spelled label (`..._data_interface`, e.g.
    "GigabitEthernet0/0/0/0"), never the underscore-spelled sibling
    (`..._data_interface_name`, "GigabitEthernet0_0_0_0") gNMI also exports
    for the same field -- the slash form is the one every other tool in this
    project treats as canonical (`interface_kind.canonical`'s own output
    shape), and extracting the underscore one here would have been a FIFTH
    interface-spelling variant, exactly what B-519's audit exists to catch.
    `capabilities_received_description`/`capabilities_sent_description` (the
    two labels on this series that read as free text, e.g. "MP: Multi-
    Topology (MT)") are deliberately NOT extracted -- see the module
    docstring's "why this module needs zero new FREE_TEXT_FIELDS entries".
    """

    records: list[dict[str, Any]] = []
    total_samples = 0

    for series in result:
        if not isinstance(series, dict):
            continue
        metric = series.get("metric") if isinstance(series.get("metric"), dict) else {}
        values = series.get("values") if isinstance(series.get("values"), list) else []

        samples, reset_timestamps = _samples_and_resets(values)

        total_samples += len(samples)
        records.append(
            {
                "lsr_id": metric.get("lsr_id"),
                "interface_name": metric.get(
                    "ldp_nbr_ipv4_adj_info_adjacency_group_link_hello_data_interface"
                ),
                "peer_state": metric.get("detailed_information_peer_state"),
                "samples": samples,
                "reset_count": len(reset_timestamps),
                "reset_timestamps": reset_timestamps,
            }
        )

    return records, {
        "records_returned": total_samples,
        "records_available": None,
        "session_count": len(records),
    }


#: `Cisco_IOS_XR_shellutil_oper:system_time_uptime_uptime{source="<device>"}`
#: -- exactly one series per device (measured live 2026-08-19: nine series,
#: one per fabric device, no other labels of interest), unlike
#: isis/ldp's "one selector, many adjacencies" shape -- the exact-match,
#: single-series shape `interface_rate_history` already has, so
#: `records_available` is computed the same exact way that query's shaper
#: does rather than left `None`.
_DEVICE_UPTIME_METRIC = "Cisco_IOS_XR_shellutil_oper:system_time_uptime_uptime"

_DEVICE_UPTIME_SHAPE = re.compile(
    r'^' + re.escape(_DEVICE_UPTIME_METRIC) + r'\{source="[A-Za-z0-9_-]{1,32}"\}$'
)


def _build_device_uptime_selector(resolved: Mapping[str, Any]) -> str:
    return '{0}{{source="{1}"}}'.format(_DEVICE_UPTIME_METRIC, resolved["device"])


def _shape_device_uptime_records(
    result: list, resolved: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`device_uptime_history`'s response shaper: exactly one series
    (this device's own uptime counter), so records are the flat sample list
    directly -- `interface_rate_history`'s single-series shape, not
    `isis_adjacency_history`'s per-adjacency one. A drop between consecutive
    samples means the device rebooted (`_samples_and_resets`, the identical
    "seconds since this last came up" logic B-530 reuses from
    `_shape_isis_adjacency_records`) -- "did this device reboot, and when",
    which nothing in this build could answer before this query.
    """

    since_seconds = resolved["since_seconds"]
    step_seconds = resolved["step_seconds"]
    expected = since_seconds // step_seconds + 1

    samples: list[dict[str, Any]] = []
    reset_timestamps: list[str] = []
    if result:
        series = result[0] if isinstance(result[0], dict) else {}
        values = series.get("values") if isinstance(series.get("values"), list) else []
        samples, reset_timestamps = _samples_and_resets(values)

    return samples, {
        "records_returned": len(samples),
        "records_available": expected,
        "reboot_count": len(reset_timestamps),
        "reboot_timestamps": reset_timestamps,
    }


@dataclass(frozen=True)
class PrometheusQuery:
    """One named, validated PromQL query. `logs_loki.LokiQuery`'s pattern,
    extended with a per-query response shaper because (unlike Loki, where
    every response is a syslog line) this module's two queries have
    genuinely different result shapes -- one exact-match single series, one
    device-wide multi-series match.

    `params` maps a slot name to an object with a `.parse(name, value) ->
    canonical value` method. `build_selector` and `shape_response` both
    receive only the *resolved* (already-validated, already-canonical)
    parameter mapping -- neither is ever handed a raw caller value.

    `existence_selector`/`existence_selector_shape` are a **second**
    selector pair, separate from `build_selector`/`selector_shape`, because
    `/api/v1/series` (the existence check `_series_known` calls) only
    accepts a plain vector selector -- never a PromQL function expression
    like `interface_rate_history`'s `rate(...)[...]` data selector. A query
    whose data selector is already bare (`isis_adjacency_history`) reuses
    its own `build_selector`/`selector_shape` for both roles; one that
    wraps its selector in a function must supply a bare equivalent (see
    `_build_interface_bare_selector`). Discovered live 2026-08-19: passing
    the wrapped selector to `/api/v1/series` was refused outright
    (`400 Bad Request`) -- not a hypothetical concern.
    """

    name: str
    params: Mapping[str, Any]
    build_selector: Callable[[Mapping[str, Any]], str]
    selector_shape: re.Pattern
    existence_selector: Callable[[Mapping[str, Any]], str]
    existence_selector_shape: re.Pattern
    shape_response: Callable[[list, Mapping[str, Any]], tuple[list[dict[str, Any]], dict[str, Any]]]
    description: str


#: The exact-match table. Four entries -- the two live metric families the
#: build brief originally named (`Cisco_IOS_XR_clns_isis_oper` adjacency
#: state, `infra_statsd_oper` interface counters), plus two more from the
#: B-530 TSDB survey (`Cisco_IOS_XR_mpls_ldp_oper` session uptime,
#: `Cisco_IOS_XR_shellutil_oper` device uptime). BGP session-state history
#: is still not here: see the module docstring's "Why BGP is still not
#: queried" section for the re-verified reason (a re-verified reason, not
#: the original one -- series churn from a label that takes multiple values
#: over the retention window, not "free text").
PROMETHEUS_QUERIES: dict[str, PrometheusQuery] = {
    "interface_rate_history": PrometheusQuery(
        name="interface_rate_history",
        params={
            "device": _DeviceSlot(),
            "interface": _InterfaceSlot(),
            "counter": _CounterSlot(),
            "since_seconds": _BoundedIntSlot(minimum=60, maximum=7 * 24 * 3600),
            "step_seconds": _BoundedIntSlot(minimum=15, maximum=3600),
        },
        build_selector=_build_interface_rate_selector,
        selector_shape=_INTERFACE_RATE_SHAPE,
        existence_selector=_build_interface_bare_selector,
        existence_selector_shape=_INTERFACE_BARE_SHAPE,
        shape_response=_shape_interface_rate_records,
        description=(
            "Per-second rate of one allowlisted interface counter (bytes/"
            "packets sent+received, or an error/drop counter) on one "
            "device's interface, sampled every `step_seconds` over the last "
            "`since_seconds` -- the utilisation/error-rate trend history."
        ),
    ),
    "isis_adjacency_history": PrometheusQuery(
        name="isis_adjacency_history",
        params={
            "device": _DeviceSlot(),
            "since_seconds": _BoundedIntSlot(minimum=60, maximum=7 * 24 * 3600),
            "step_seconds": _BoundedIntSlot(minimum=15, maximum=3600),
        },
        build_selector=_build_isis_adjacency_selector,
        selector_shape=_ISIS_ADJACENCY_SHAPE,
        # Already a bare selector -- reused for the existence check unchanged.
        existence_selector=_build_isis_adjacency_selector,
        existence_selector_shape=_ISIS_ADJACENCY_SHAPE,
        shape_response=_shape_isis_adjacency_records,
        description=(
            "Every IS-IS adjacency's `neighbor_uptime` history on one "
            "device, sampled every `step_seconds` over the last "
            "`since_seconds`; a value that drops between consecutive "
            "samples is a flap (the adjacency reset), counted per adjacency."
        ),
    ),
    "ldp_session_history": PrometheusQuery(
        name="ldp_session_history",
        params={
            "device": _DeviceSlot(),
            "since_seconds": _BoundedIntSlot(minimum=60, maximum=7 * 24 * 3600),
            "step_seconds": _BoundedIntSlot(minimum=15, maximum=3600),
        },
        build_selector=_build_ldp_session_selector,
        selector_shape=_LDP_SESSION_SHAPE,
        # Already a bare selector -- reused for the existence check unchanged,
        # the same choice isis_adjacency_history makes for the same reason.
        existence_selector=_build_ldp_session_selector,
        existence_selector_shape=_LDP_SESSION_SHAPE,
        shape_response=_shape_ldp_session_records,
        description=(
            "Every LDP session's `ta_up_time_seconds` (seconds since it "
            "last came up) history on one device, sampled every "
            "`step_seconds` over the last `since_seconds`; a value that "
            "drops between consecutive samples is a flap (the session "
            "reset), counted per session."
        ),
    ),
    "device_uptime_history": PrometheusQuery(
        name="device_uptime_history",
        params={
            "device": _DeviceSlot(),
            "since_seconds": _BoundedIntSlot(minimum=60, maximum=7 * 24 * 3600),
            "step_seconds": _BoundedIntSlot(minimum=15, maximum=3600),
        },
        build_selector=_build_device_uptime_selector,
        selector_shape=_DEVICE_UPTIME_SHAPE,
        existence_selector=_build_device_uptime_selector,
        existence_selector_shape=_DEVICE_UPTIME_SHAPE,
        shape_response=_shape_device_uptime_records,
        description=(
            "One device's own `system_time_uptime_uptime` (seconds since "
            "boot) history, sampled every `step_seconds` over the last "
            "`since_seconds`; a value that drops between consecutive "
            "samples means the device rebooted."
        ),
    ),
}


def known_prometheus_queries() -> tuple[str, ...]:
    return tuple(sorted(PROMETHEUS_QUERIES))


# --------------------------------------------------------------------------- #
# Envelope construction
# --------------------------------------------------------------------------- #


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_envelope(query_name: str, device_name: str | None) -> dict[str, Any]:
    """`network_tools._base_result`'s exact shape, rebuilt locally -- see the
    module docstring's "hard requirement 2" section for why (identical
    reasoning to `logs_loki._base_envelope`, which this mirrors verbatim).
    """

    return {
        "tool": "run_named_query",
        "device": device_name,
        "status": STATUS_SUCCESS,
        "timestamp": _timestamp(),
        "source": SOURCE_PROMETHEUS,
        "data": {},
        "errors": [],
    }


def _error_envelope(query_name: str, device_name: str | None, message: str) -> dict[str, Any]:
    envelope = _base_envelope(query_name, device_name)
    envelope["status"] = STATUS_ERROR
    envelope["errors"].append(message)
    envelope["data"] = {
        "intent": query_name,
        "query_name": query_name,
        "parse_status": PARSE_FAILED,
        "parsed": {
            "records": [],
            "meta": {
                "records_returned": 0,
                "records_available": None,
                "query_complete": False,
            },
        },
    }
    return envelope


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def _http_fetcher(base_url: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    """The real transport. stdlib only (`urllib`), matching `logs_loki.py`'s
    and `notifier.py`'s choice. Never used by a test -- see
    `run_named_query`'s `fetcher=` seam."""

    url = f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    timeout = _float_env(PROMETHEUS_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PrometheusTransportError(f"prometheus request failed: {exc}") from exc

    if not (200 <= int(status) < 300):
        raise PrometheusTransportError(f"prometheus returned http status {status}")

    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise PrometheusTransportError("prometheus response was not valid json") from exc

    if not isinstance(parsed, dict) or parsed.get("status") != "success":
        raise PrometheusTransportError("prometheus query did not return a success status")

    return parsed


def _extract_matrix(raw: dict[str, Any]) -> list:
    data = raw.get("data") if isinstance(raw, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise PrometheusTransportError("prometheus response was not the expected matrix shape")
    return result


def _extract_series_list(raw: dict[str, Any]) -> list:
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        raise PrometheusTransportError("prometheus response was not the expected series-list shape")
    return data


def _series_known(
    base_url: str,
    selector: str,
    end_dt: datetime,
    fetcher: Callable[[str, str, dict[str, str]], dict[str, Any]],
) -> tuple[bool | None, str | None]:
    """Has *any* sample matching `selector` been observed within
    :func:`_existence_lookback_seconds` of `end_dt`, independent of whether
    the narrow query window has one?

    Called by :func:`run_named_query` **only** when the primary range query
    returned zero points -- this is a second HTTP call, and the common case
    (data present) never pays for it. A failure here is never raised past
    this function and never turns a successful primary read into a failed
    envelope: the caller already has a real answer from Prometheus about the
    requested window, and an auxiliary check that could not complete is
    reported as `None` with an explanatory note, not manufactured into a
    claim either way.
    """

    lookback = _existence_lookback_seconds()
    start_dt = end_dt - timedelta(seconds=lookback)
    params = {
        "match[]": selector,
        "start": str(int(start_dt.timestamp())),
        "end": str(int(end_dt.timestamp())),
    }
    try:
        raw = fetcher(base_url, "/api/v1/series", params)
        series = _extract_series_list(raw)
    except PrometheusTransportError as exc:
        return None, f"the existence check itself failed: {exc}"
    return (len(series) > 0), None


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #


def run_named_query(
    query_name: str,
    *,
    fetcher: Callable[[str, str, dict[str, str]], dict[str, Any]] | None = None,
    **params: Any,
) -> dict[str, Any]:
    """The one entry point. Never raises -- every failure (an unknown query
    name, an invalid slot, a sample-budget refusal, an unreachable
    Prometheus, a malformed response) comes back as a `status="error"`
    envelope in the same shape a success uses.

    `query_name` is looked up in :data:`PROMETHEUS_QUERIES` by exact string
    match. There is no parameter anywhere on this function, or on anything it
    calls, that accepts a PromQL string -- see the module docstring's "hard
    requirements" section.
    """

    device_name = params.get("device") if isinstance(params.get("device"), str) else None

    query = PROMETHEUS_QUERIES.get(query_name)
    if query is None:
        return _error_envelope(
            query_name,
            device_name,
            f"unknown prometheus query {query_name!r}; valid: {', '.join(known_prometheus_queries())}",
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
        resolved = {name: query.params[name].parse(name, value) for name, value in params.items()}
    except PrometheusQueryError as exc:
        return _error_envelope(query_name, device_name, str(exc))

    since_seconds = resolved["since_seconds"]
    step_seconds = resolved["step_seconds"]
    expected_points = since_seconds // step_seconds + 1
    if expected_points > MAX_SAMPLES_PER_QUERY:
        return _error_envelope(
            query_name,
            device_name,
            f"{query_name}: since_seconds/step_seconds would return approximately "
            f"{expected_points} samples, over the {MAX_SAMPLES_PER_QUERY} ceiling; "
            "request a shorter window or a coarser step",
        )

    selector = query.build_selector(resolved)
    if not query.selector_shape.fullmatch(selector):
        # Can only fire if PROMETHEUS_QUERIES itself is written wrong -- every
        # caller-supplied slot was already validated above -- mirroring
        # `logs_loki`'s identical defense-in-depth post-build check.
        return _error_envelope(
            query_name,
            device_name,
            f"{query_name}: built selector failed the post-build shape check: {selector!r}",
        )

    # The existence-check selector (see PrometheusQuery's docstring) is built
    # and validated here too, eagerly -- even on a run where it may never be
    # used -- so that "the primary read succeeded" always implies "the
    # existence check, if needed, is at least well-formed": nothing about
    # PROMETHEUS_QUERIES being written wrong surfaces only after an HTTP call
    # has already been made.
    existence_selector = query.existence_selector(resolved)
    if not query.existence_selector_shape.fullmatch(existence_selector):
        return _error_envelope(
            query_name,
            device_name,
            f"{query_name}: built selector failed the post-build shape check: {existence_selector!r}",
        )

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(seconds=since_seconds)
    request_params = {
        "query": selector,
        "start": str(int(start_dt.timestamp())),
        "end": str(int(end_dt.timestamp())),
        "step": str(step_seconds),
    }

    active_fetcher = fetcher or _http_fetcher

    try:
        raw = active_fetcher(_prometheus_url(), "/api/v1/query_range", request_params)
        result = _extract_matrix(raw)
    except PrometheusTransportError as exc:
        envelope = _error_envelope(query_name, device_name, str(exc))
        envelope["data"]["parsed"]["meta"]["query_window_start"] = start_dt.isoformat()
        envelope["data"]["parsed"]["meta"]["query_window_end"] = end_dt.isoformat()
        envelope["data"]["parsed"]["meta"]["since_seconds"] = since_seconds
        envelope["data"]["parsed"]["meta"]["step_seconds"] = step_seconds
        return envelope

    records, meta_extra = query.shape_response(result, resolved)

    series_known: bool | None = None
    existence_note: str | None = None
    if meta_extra.get("records_returned", 0) == 0:
        series_known, existence_note = _series_known(
            _prometheus_url(), existence_selector, end_dt, active_fetcher
        )

    meta: dict[str, Any] = {
        "query_window_start": start_dt.isoformat(),
        "query_window_end": end_dt.isoformat(),
        "since_seconds": since_seconds,
        "step_seconds": step_seconds,
        "query_complete": True,
        "series_known": series_known,
    }
    if existence_note:
        meta["series_existence_check_note"] = existence_note
    meta.update(meta_extra)

    envelope = _base_envelope(query_name, device_name)
    envelope["data"] = {
        "intent": query_name,
        "query_name": query_name,
        # Safe to expose verbatim: reconstructed entirely from validated,
        # canonical slot values (see `_build_interface_rate_selector`/
        # `_build_isis_adjacency_selector`), never from caller text.
        "promql": selector,
        "parse_status": PARSE_OK,
        "parsed": {"records": records, "meta": meta},
    }
    return envelope


# --------------------------------------------------------------------------- #
# Coverage: absence-is-not-zero, exposed for the wide step to consume
# --------------------------------------------------------------------------- #


def coverage_from_prometheus_history(parsed: dict[str, Any], device: str) -> Coverage:
    """Build a `coverage.Coverage` from one `run_named_query()` result's
    `data.parsed`. Not called by `run_named_query` itself -- a downstream,
    wide-step consumer calls this once it has decided the window is
    relevant, mirroring `coverage_from_loki`'s own layering exactly (and,
    like that function, **not wired into any consumer by this change**).

    The four cases the build brief asks to be distinguished, and how this
    function reports each:

    1. **Samples present.** `records_returned` > 0; `query_complete=True`.
       `Coverage.complete` is `True` unless the count still falls short of
       `records_available` (a partial-window gap even though *some* data
       came back -- case 2, below, can co-occur with this one).
    2. **Series exists, window (or part of it) empty.**
       `meta["series_known"] is True` and `records_returned <
       records_available` (only computable for `interface_rate_history`'s
       exact-match series; `isis_adjacency_history` reports
       `records_available=None` and relies on `series_known` alone here).
       `Coverage.truncated` fires from the existing `records_available` vs
       `records_returned` mechanism -- reused, not reinvented -- and this
       function adds an explanatory note so the *reason* for the gap reads
       as a collection interruption, not a measured zero.
    3. **Series never observed at all.** `meta["series_known"] is False` --
       the `/api/v1/series` existence check, over a week-wide lookback,
       found nothing. Reported as its own note; distinct wording from case 2
       so a reader does not conflate "this never existed" with "this
       stopped."
    4. **Query failed.** `meta.get("query_complete")` is `False` (the
       primary HTTP call raised) -- `Coverage.gaps()`'s existing
       `query_complete` check already covers this without anything added
       here.
    """

    meta = parsed.get("meta") or {}
    records_available = meta.get("records_available")
    records_returned = meta.get("records_returned", 0)
    series_known = meta.get("series_known")

    notes: list[str] = []
    if series_known is False:
        notes.append(
            "no series matching this device/interface/metric selector has been "
            f"observed in the existence-check lookback ({_existence_lookback_seconds()}s); "
            "this points at a wrong device, interface, or metric combination, "
            "not a scrape gap"
        )
    elif series_known is None and meta.get("series_existence_check_note"):
        notes.append(str(meta["series_existence_check_note"]))
    if (
        series_known is True
        and isinstance(records_available, int)
        and isinstance(records_returned, int)
        and records_returned < records_available
    ):
        notes.append(
            "the series is known to exist, but this window returned fewer "
            "samples than the requested step/window implies -- a collection "
            "gap, not a measured zero; absence of a sample is never a value "
            "of zero"
        )

    return Coverage(
        device=device,
        source=SOURCE_PROMETHEUS,
        query_complete=bool(meta.get("query_complete", False)),
        window_start=meta.get("query_window_start"),
        window_end=meta.get("query_window_end"),
        records_available=records_available if isinstance(records_available, int) else None,
        records_returned=records_returned if isinstance(records_returned, int) else 0,
        records_dropped_at_source=0,
        notes=tuple(notes),
    )
