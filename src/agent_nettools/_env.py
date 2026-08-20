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

This module's `_float_env`/`_int_env` are the LOOSEST union of all of that:
whatever any one of the old copies used to accept, this still accepts, so
routing every call site through this module changes no currently-passing
behaviour (F2 commit 1). A later, deliberate commit narrows `_float_env` to
reject inf/nan for every caller, not just the two that already did (F2
commit 2) -- a semantics change, not a mechanical merge, so it is not folded
into this first version.

Absence is never coerced to a number: unset, blank, or unparseable all fall
back to the caller's own `default`, exactly like a malformed string --
`default` is the caller's explicit "I don't know" value, never silently 0.
"""

from __future__ import annotations

import math
import os


def _float_env(name: str, default: float) -> float:
    """Read a float-valued env var. Unset, blank, or unparseable -> default.

    Rejects only NaN and `-inf` -- values none of the six pre-existing
    copies ever accepted. `+inf` and zero/negative finite values pass
    through, matching whichever of the old copies already allowed them (see
    the module docstring's inf/nan and sign axes).
    """

    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if math.isnan(value) or value == float("-inf"):
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
