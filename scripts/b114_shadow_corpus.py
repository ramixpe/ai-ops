#!/usr/bin/env python3
"""Measure B-114's residual narrowing demand against the decisive fixture.

This is a fixture replay, not a model evaluation and not a network operation.
It tests whether the observed case that reaches ``cause_not_localised`` remains
unexplained after deterministic configuration reconciliation.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_nettools import config_diff
from agent_nettools.fixtures import fixture_sender
from agent_nettools.investigation import investigate


def measure() -> dict[str, Any]:
    """Return a contained receipt for the known real B-114 candidate case."""

    device = "PE3"
    interface = "Gi0/0/0/0"
    sender = fixture_sender(label="isis-broken")
    result = investigate(device, interface, flow="isis_adjacency", sender=sender)
    reconciliation = config_diff.reconcile_interface(
        config_diff.gather_reconciliation_evidence(device, [interface], sender=sender),
        device,
        interface,
    )
    residual = result.finding == "cause_not_localised" and not reconciliation.has_disagreement
    return {
        "measurement": "b114_shadow_residual_demand",
        "source": "fixture:isis-broken",
        "investigations": 1,
        "cause_not_localised": int(result.finding == "cause_not_localised"),
        "config_reconciled": int(reconciliation.has_disagreement),
        "residual_unexplained": int(residual),
        "narrowing_mode": "shadow_only",
    }


def main() -> int:
    print(json.dumps(measure(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())