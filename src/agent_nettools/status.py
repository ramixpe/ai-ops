"""EER-015: the status/source vocabulary every envelope in this project uses,
pulled out of `network_tools.py` so a pure predicate module can name a status
without importing a transport module to get it.

Why this moved
------------------
`checks.py` is explicit in its own module docstring that it touches "no I/O,
no device access, no inventory reads, no clock, no environment" -- it is the
deterministic half of the investigation layer, and its own tests run with no
lab. Before this change it still did ``from .network_tools import
STATUS_ERROR, STATUS_UNSUPPORTED`` -- importing the SSH transport module
purely for two string constants that happen to live there. That import cost
nothing at runtime (Python caches modules), but it was a cohesion smell in
exactly the shape the independent review named: a module that promises zero
device-layer coupling reaching into the device-layer module anyway, for a
value that was never actually about a device. `STATUS_SUCCESS`,
`STATUS_ERROR`, `STATUS_UNSUPPORTED`, `SOURCE_LIVE` and `SOURCE_FIXTURE` are
about the shape of a result envelope, not about how one was collected --
`checks.py`, `network_tools.py`, `agent_loop.py` and (indirectly, through
`agent_nettools/__init__.py`'s lazy re-exports) anyone treating this as a
library all agree on that shape without any of them needing to own it.

`network_tools.py` re-exports every one of these names (same pattern as
`DEFAULT_SNAPSHOT_DIR = evidence_store.DEFAULT_SNAPSHOT_DIR` a little further
down that file) so every existing ``from .network_tools import STATUS_ERROR``
and ``network_tools.SOURCE_LIVE`` call site keeps working unchanged --
breaking those was never the point.

What did **not** move, on purpose
--------------------------------------
- `_source_for`, `_base_result`, `_safe_error`, `_unsupported_result` and
  `_mark_admission_refused` stay in `network_tools.py`. They are transport
  logic that happens to *read* this vocabulary, not part of the vocabulary
  itself, and moving them would be the module split the independent review
  explicitly said not to do.
- `ledger.py`'s own `SOURCE_LIVE`/`SOURCE_FIXTURE` (same two spellings, a
  different field -- whether a *diagnosis* came from a live device or a
  fixture replay, not where an *evidence read* came from) stay exactly where
  they are. `network_tools.py`'s own comment already explains why it never
  imported ledger's copy even though the strings agree: `ticket.py`'s
  "Ledger sibling, not ledger duplicate" reasoning applies here as much as it
  does there, and this move does not change that -- `status.py` is a new leaf
  module, not ledger.py, so importing it does not reopen that question.
- `metrics_prometheus.py` and `logs_loki.py` each declare their *own*
  independent `STATUS_SUCCESS`/`STATUS_ERROR` (not imported from
  `network_tools.py` even before this change). Out of scope for this pass --
  flagged, not silently left inconsistent, in case a future EER-015 step
  wants to fold them in too.
"""

from __future__ import annotations

# Statuses a result envelope may carry. "unsupported" means the device's platform
# has no command for the requested intent -- a Junos box has no SR-TE policy
# output. That is a property of the fabric, not a failure, so it is neither an
# error nor a success and must not make a fabric check go red.
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"

# Where an envelope's data actually came from -- real SSH against the device,
# or replayed/injected output over the `sender=` seam (a committed fixture via
# `fixtures.fixture_sender`, or any other test double; from this layer's own
# point of view both are simply "not a live read of the device at the moment
# this ran", which is exactly `ledger.py`'s own definition of `SOURCE_FIXTURE`
# -- see its module docstring's "What 'source' is for" section). Everything
# in this file was implicitly SSH before Stage 2's Loki/Prometheus/NetBox
# sources made that stop being true; this is the minimum discriminator the
# build report asks for.
#
# Deliberately **not** imported from `ledger.py`, even though the spellings
# are the same on purpose: `ticket.py`'s own docstring ("Ledger sibling, not
# ledger duplicate") explains why two modules built in the same session avoid
# a hard import between them, and the same reasoning applies here -- a
# documented convention, not a runtime coupling.
SOURCE_LIVE = "live"
SOURCE_FIXTURE = "fixture"
