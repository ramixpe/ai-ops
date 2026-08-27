#!/usr/bin/env python3
"""Run durable event and Telegram recovery sweeps on a bounded cadence."""

from __future__ import annotations

import logging
import signal
import threading

from agent_nettools.event_store import EventStore
from agent_nettools.recovery_service import (
    RecoveryService,
    configured_lease_seconds,
    configured_sweep_seconds,
)

STOP = threading.Event()


def _stop(_signum, _frame) -> None:
    STOP.set()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    service = RecoveryService(EventStore(), lease_seconds=configured_lease_seconds())
    interval = configured_sweep_seconds()
    while not STOP.is_set():
        result = service.sweep_once()
        logging.getLogger(__name__).info(
            "recovery sweep complete claimed_events=%s notification_outcomes=%s",
            len(result.claimed_events),
            result.notification_outcomes,
        )
        STOP.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())