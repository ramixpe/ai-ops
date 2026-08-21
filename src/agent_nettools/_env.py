"""The one `_float_env`/`_int_env` implementation, shared by every module that
used to carry its own copy (F2, release-1.0 cleanup, OBS-510..519/B-810..819).

Before this module existed, six files each defined their own `_float_env`
(`notifier.py`, `netbox.py`, `admission.py`, `network_tools.py`,
`logs_loki.py`, `metrics_prometheus.py`) and four defined their own
`_int_env` (`admission.py`, `notifier.py`, `network_tools.py`,
`metrics_prometheus.py`) -- `settings.py`'s own module docstring already
named the duplication, without consolidating it. The ten copies were not
identical: two independent axes had drifted --

- **inf/nan**: `admission.py`/`network_tools.py`'s `_float_env` rejected
  non-finite values (`math.isfinite`, added in a 2026-08-18 review after a
  `NETTOOLS_*_SECONDS=nan` typo was found to sail through every range check
  silently and become a timeout that never fires); the other four accepted
  `+inf` (rejecting it only incidentally, via a `value > 0` comparison that
  is false for `-inf`/`nan` but true for `+inf`).
- **sign**: `admission.py`/`network_tools.py`'s `_int_env`, and both of
  their `_float_env`, accepted zero/negative values with no range check at
  all; `notifier.py`/`logs_loki.py`/`netbox.py`/`metrics_prometheus.py`
  rejected non-positive values (`value > 0 else default`).

F2 commit 1 made this module's `_float_env`/`_int_env` the LOOSEST union of
all of that: whatever any one of the old copies used to accept, it still
accepted, so routing every call site through this module changed no
currently-passing behaviour. F2 commit 2 (this version) narrows `_float_env`
one axis: inf/nan now fall back to `default` for every caller, not just the
two (`admission.py`/`network_tools.py`) that already guarded against it --
a deliberate safety tightening, not a mechanical merge. A `NETTOOLS_*_
SECONDS=nan` or `=inf` typo silently sailing through every range check and
becoming a timeout that never fires is exactly the failure the 2026-08-18
review added `math.isfinite` to catch in those two copies; this commit gives
the other four callers (`notifier.py`, `netbox.py`, `logs_loki.py`,
`metrics_prometheus.py`) the same guarantee they never had. The sign axis
(zero/negative accepted) is untouched -- it is a separate concern this task
does not ask this module to resolve; see the F2 commit 1 report for the
full difference table.

Absence is never coerced to a number: unset, blank, or unparseable all fall
back to the caller's own `default`, exactly like a malformed string --
`default` is the caller's explicit "I don't know" value, never silently 0.

`_BOOL_TRUE`/`_BOOL_FALSE` (EER-015)
-------------------------------------
The recognized boolean spellings, case-insensitive: `{"1", "true", "yes",
"on"}` and `{"0", "false", "no", "off"}`. `settings.py` declared its own copy
of both; `mcp_server/server.py` declared the truthy half twice more, once per
MCP-only gate (`_MCP_ACTIVE_PROBES_TRUTHY`, `_MCP_EXTERNAL_SOURCES_TRUTHY`) --
four definitions of two literal tuples, collapsed here. Each gate keeps its
own semantically-named constant (`_mcp_active_probes_allowed`/
`_mcp_external_sources_allowed` both still read `_MCP_*_TRUTHY` by that exact
name, unchanged -- one of them is a `scripts/mutate_guards.py` anchor,
EER-008B-EXTERNAL, and rewriting that return line would silently void the
guard); only the *value* they are assigned now comes from here instead of a
second literal. `settings.py`'s own `_BOOL_SPELLINGS = _BOOL_FALSE |
_BOOL_TRUE` stays exactly as it was, now built from the imported pair.

This is a values-only merge -- unlike `_float_env`/`_int_env` above, there
was no cross-copy behavioral drift to reconcile: every copy was the same
four-string set, case for case, before this change.
"""

from __future__ import annotations

import math
import os

# See "`_BOOL_TRUE`/`_BOOL_FALSE` (EER-015)" above.
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def _float_env(name: str, default: float) -> float:
    """Read a float-valued env var. Unset, blank, or unparseable -> default.

    inf/nan fall back to `default` exactly like a malformed string does
    (F2 commit 2) -- `math.isnan`/`math.isinf` both false is the same test
    as `math.isfinite`, spelled out so the "same as malformed" framing is
    explicit at the call site. Zero/negative finite values still pass
    through unchanged (the sign axis is not this commit's concern).
    """

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return value


def _int_env(name: str, default: int) -> int:
    """Read an int-valued env var. Unset, blank, or unparseable -> default.

    No range check -- the loosest of the four pre-existing copies (matches
    `admission.py`/`network_tools.py`'s originals exactly; `int()` itself
    already refuses anything inf/nan-shaped with a `ValueError`).
    """

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
