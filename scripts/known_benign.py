#!/usr/bin/env python3
"""Which health findings are this fabric's baseline noise, derived not listed.

`preflight.sh` needs to tell an operator which findings are worth reading. The
first version carried a hand-written list of two rule names, and the first live
run flagged three more as "read these" — all three documented known state, and
one of them (`suspicious_baseline`) was the rule **succeeding**.

**A list of exceptions maintained by hand goes stale in the direction that
matters**: it cries wolf about the rules that are working, which trains the
reader to skip the section where a real finding will eventually appear. That is
§0.13's rules face — a generalisation written from the instances in front of me.

So the set is derived instead. The `healthy` fixture label is, by its own
definition in `tests/fixtures/README.md`, *"the rebuilt fabric with nothing wrong
with it"*. **Whatever fires there is this fabric's floor.**

Compared by `(rule, device, subject)`, not by rule name alone. A second device
developing the same rule is new, and suppressing it by rule name would be the
same mistake one level down.

Prints one `rule|device|subject` triple per line. Exits 0 always -- it describes,
it does not judge.
"""

from __future__ import annotations

import os
import sys

from agent_nettools.checks import evaluate_device
from agent_nettools.fixtures import load_fixture_evidence
from agent_nettools.inventory_model import load_inventory_file


def main() -> int:
    path = os.environ.get("NETTOOLS_INVENTORY", "inventory/lab.yaml")
    inventory = load_inventory_file(path)
    for device in inventory.devices:
        try:
            evidence = load_fixture_evidence(device.name, label="healthy")
        except Exception:
            continue
        for finding in evaluate_device(evidence, device)["findings"]:
            print(f"{finding['rule']}|{device.name}|{finding.get('subject') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
