"""Deterministic health verdicts over collected evidence.

Why this module exists
-----------------------
``status: "success"`` in every other tool in this package reflects only
*transport* success: the SSH session opened and the commands ran. A device
whose every BGP peer is down still reports ``success`` -- there is nothing
today that answers "is this device healthy?". At fleet scale that question
must be answered by cheap, deterministic rules *before* any LLM ever looks at
the evidence, so the reasoning layer only ever sees the anomalies, not all
nine (or nine thousand) devices' worth of noise.

Two independent rule classes -- read this before adding a rule
----------------------------------------------------------------
``inventory/lab.yaml``'s ``expected:`` blocks are *derived* from this fabric's
own current state (see ``topology.py``), and this fabric is partly broken:
the file literally records ``PE2: isis_adjacencies: 0`` and
``PE4: isis_adjacencies: 0``. A rule that only compares observed counts
against that baseline would therefore call both devices healthy -- the
baseline blesses the very brokenness it was measured from. So health rules
come in two kinds, kept in separate tables below, and a change should know
which one it is:

1. **Role invariants** (``ROLE_INVARIANT_RULES``) -- what must be true of a
   router in this fabric *given its role*, independent of anything recorded
   in the inventory. These are what catch baked-in brokenness that the
   baseline would otherwise bless: "every router must have at least one IS-IS
   adjacency" does not care that the baseline says zero is expected.
2. **Baseline rules** (``BASELINE_RULES``) -- compare an observed count
   against the inventory's recorded ``expected:`` value. These catch *drift*
   from the last known-derived state, which role invariants cannot see (a
   device dropping from 4 BGP peers to 3 is not a role violation, but it is a
   change worth flagging).

A third, single ``META_RULES`` entry (``suspicious_baseline``) makes the tool
honest about its own baseline: when the recorded expectation is itself a
value a role invariant would call unhealthy (``isis_adjacencies == 0``), that
is flagged directly, so nobody mistakes "matches the baseline" for "is
healthy" on a device whose baseline was learned from a broken fabric.

Unevaluated, not silently "ok"
-------------------------------
A rule must never read a missing or failed intent as healthy. If an intent's
parse did not succeed (``parsers.PARSE_OK``) -- because the platform command
errored, is ``unsupported`` on this platform, or the parser itself failed --
every rule reading that intent is skipped for that device and the intent name
is listed in the verdict's ``unevaluated`` list instead. Silence from a failed
collection must never look like health.

**Merged into `checks.py` at B-403.** This module is now a re-export shim so
the nine importers below keep working; the rules themselves live beside the
descent's predicates. See the divider comment there for what the merge cost.
"""

from __future__ import annotations

from .checks import (  # noqa: F401 - re-exported for the pre-merge importers
    ALL_RULES,
    BASELINE_RULES,
    META_RULES,
    ROLE_INVARIANT_RULES,
    SEVERITY_ORDER,
    Rule,
    RuleContext,
    evaluate_device,
    evaluate_fabric,
    exit_code_for_severity,
    severity_rank,
)
