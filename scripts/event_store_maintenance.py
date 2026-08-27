#!/usr/bin/env python3
"""Report durable lifecycle retention candidates; deletion is intentionally unavailable."""

from __future__ import annotations

import json
import sys

from agent_nettools.event_store import EventStore


def main() -> int:
    store = EventStore()
    if sys.argv[1:] == ["--apply"]:
        print(json.dumps(store.compact(), sort_keys=True))
        return 0
    if sys.argv[1:]:
        print("usage: event_store_maintenance.py [--apply]", file=sys.stderr)
        return 2
    print(json.dumps(store.compaction_report(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())