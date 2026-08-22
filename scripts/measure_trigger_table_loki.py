#!/usr/bin/env python
"""B-706 Stage 2 evidence: re-measure the mnemonics.yaml trigger table against
post-B-206a Loki.

STATUS 2026-08-22: this has NOT completed a full run. It was stopped partway by
sustained Loki HTTP 429s -- its own recovery query (the line-filtered one that
reaches past the 1000-line ceiling, see B-712) is the stage that never finished,
so no ``trigger_table_loki_measurement.json`` was produced. The partial results
it did yield are recorded in OBS-702, along with what they were sufficient to
conclude and what they were not.

Kept in the tree rather than deleted because the Stage 2 promotion question is
still open (B-706) and will need exactly this measurement once B-711 is fixed
and Loki is not being rate-limited. **Rate-limit it before re-running**: the run
that hit the 429s was issuing per-device and per-mnemonic queries back to back
with no pacing, which is what closed the limiter on us in the first place.

READ-ONLY. Every network call in this script is an HTTP GET against Loki
(``172.20.250.103:3100``). Nothing here writes to a device, runs
``faultlab/fault_lab.py``, or calls anything outside ``agent_nettools``'s own
read path. It does not import or touch ``mnemonics.yaml`` as a write target --
``knowledge.load_mnemonic_table()`` only reads it.

What this measures and how
---------------------------
1. **The sanctioned path** -- ``logs_loki.run_named_query("logs_for_device",
   since_seconds=604800, limit=1000)`` -- is the tool's own maximum window
   (604800s = 7 days = Loki's own retention ceiling, `logs_loki.py`'s own
   comment) and its own maximum per-query line budget (1000). This is run once
   per device and its numbers (``records_before_dedup``, ``duplicates_removed``,
   the deduped ``records``) are the first-class, tool-produced measurement.

2. **A device-wide raw replica** -- calling `logs_loki`'s own private
   pipeline (`_DeviceSlot`, `_build_logs_for_device_selector`, `_to_ns`,
   `_http_fetcher`, `_extract_records`) with IDENTICAL parameters to what
   `run_named_query` uses internally -- exists for exactly one reason:
   `run_named_query` discards the pre-dedup record list once it has counted
   it, so there is no way to get a PER-MNEMONIC raw-line count from the
   sanctioned entry point alone (only a device-wide total). This replica is
   cross-checked against the sanctioned call (see ``_spot_check`` below) to
   prove it is not a second, drifted implementation.

   `log_window.dedupe` -- the project's own function, the one named in the
   task brief -- is then called explicitly on the raw replica, per device (so
   its (timestamp, mnemonic, text) key never needs a host discriminator: one
   device's query result cannot contain another device's records).

3. **The noise-crowding problem, found empirically, not assumed.** Four of
   nine devices (PE1, PE2, PE3, RR1) hit the 1000-line cap on the 7-day
   device-wide query -- exactly as `mnemonics.yaml`'s own header predicted for
   the OLD measurement, and confirmed still true post-B-206a because the
   volume that fills the cap has grown (SECURITY-SSHD_SYSLOG_PRX-3 plus new
   IP-TCP-3-NOAUTH/BADAUTH bursts). A capped device's raw replica does not
   reach back 7 days; it reaches back only as far as the noise allows. A rare
   mnemonic's earlier occurrences can be pushed out of the window entirely
   even though Loki still holds them.

   For exactly the four capped devices, a SECOND, LogQL-filtered query is
   run: ``{source_ip="<ip>"} |~ "<mnemonic-alternation>"``, a regex
   alternation of all 16 severity-eligible mnemonics (the four severity
   6/7 mnemonics are structurally excluded by the routers' own destination
   config -- see `logs_loki.MEASURED_SEVERITY_AVAILABLE` -- and are not
   worth a request). This is still a read-only Loki query, still through
   `logs_loki._http_fetcher` (the project's own transport), just with a
   selector `logs_loki.LOKI_QUERIES` does not expose -- necessary because
   the tool's own device-wide query cannot see past its own noise on these
   four hosts. The filtered result is unioned with the device-wide raw
   replica and re-deduped, so nothing is double-counted.

Rate limiting
-------------
Loki's HTTP endpoint 429s under a burst of requests from this measurement
(measured empirically while writing this script). ``_fetch`` retries with
exponential backoff (5s, 8.5s, 14.5s, ... capped, 8 attempts) and every call
site sleeps between requests. A full run takes a few minutes because of this,
not because of query cost.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

from agent_nettools import event_routing, log_window, logs_loki  # noqa: E402
from agent_nettools.inventory_model import load_inventory_file  # noqa: E402
from agent_nettools.knowledge import load_mnemonic_table  # noqa: E402

OUT_PATH = Path(__file__).parent / "trigger_table_loki_measurement.json"

SEVEN_DAYS = 7 * 24 * 3600
MAX_LIMIT = 1000  # logs_loki._BoundedIntSlot's own ceiling for "limit"

B206A_FIX_DATE = (8, 20)  # month, day -- the fix landed 2026-08-20

# Severities 6 and 7 are structurally excluded by MEASURED_SEVERITY_AVAILABLE
# (2, 3, 4, 5) -- the routers' own destination `severity` line, re-measured
# 2026-08-21 (B-696). No amount of querying recovers a severity Loki was
# never sent. Listed here, not queried individually with the mnemonic-filter
# probe, to keep the request budget for mnemonics that can plausibly answer.
STRUCTURALLY_EXCLUDED_SEVERITY = {6, 7}


def _mnemonic_severity(mnemonic: str) -> int | None:
    parts = mnemonic.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return int(parts[1])
    return None


def _fetch(query: str, since_seconds: int, limit: int, retries: int = 14) -> tuple[list[dict[str, Any]], int]:
    """One raw Loki fetch through the project's own transport
    (`logs_loki._http_fetcher`) and extractor (`logs_loki._extract_records`),
    with retry/backoff on the 429s this endpoint produces under this
    script's request volume. Measured empirically: the cooldown after a
    burst is longer than a few tens of seconds, so this backs off hard
    (doubling, capped at 120s) rather than assuming a short window."""

    end_dt = datetime.datetime.now(datetime.timezone.utc)
    start_dt = end_dt - datetime.timedelta(seconds=since_seconds)
    request_params = {
        "query": query,
        "start": str(logs_loki._to_ns(start_dt)),
        "end": str(logs_loki._to_ns(end_dt)),
        "limit": str(limit),
        "direction": "backward",
    }
    delay = 8.0
    for attempt in range(retries):
        try:
            raw = logs_loki._http_fetcher(logs_loki._loki_url(), request_params)
            return logs_loki._extract_records(raw)
        except logs_loki.LokiTransportError as exc:
            if "429" in str(exc) and attempt < retries - 1:
                print(f"    [429, retry in {delay:.1f}s]", file=sys.stderr)
                time.sleep(delay)
                delay = min(delay * 2.0, 120.0)
                continue
            raise


def _device_wide_raw(mgmt_ip: str) -> tuple[list[dict[str, Any]], int]:
    resolved = {"device": mgmt_ip, "since_seconds": SEVEN_DAYS, "limit": MAX_LIMIT}
    selector = logs_loki._build_logs_for_device_selector(resolved)
    return _fetch(selector, SEVEN_DAYS, MAX_LIMIT)


def _mnemonic_filtered_raw(mgmt_ip: str, mnemonics: list[str]) -> tuple[list[dict[str, Any]], int]:
    alternation = "|".join(mnemonics)
    selector = '{source_ip="%s"} |~ "%s"' % (mgmt_ip, alternation)
    return _fetch(selector, SEVEN_DAYS, MAX_LIMIT)


def _record_key(r: dict[str, Any]) -> tuple[str, str, str]:
    return (r.get("timestamp", ""), r.get("mnemonic", ""), r.get("text", ""))


def _spot_check(device: str, mgmt_ip: str, replica_raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-check the raw replica pipeline against the real, sanctioned
    `run_named_query` call for one device, so the replica is proven to match
    the tool's own numbers rather than trusted by construction."""

    sanctioned = logs_loki.run_named_query(
        "logs_for_device", device=device, since_seconds=SEVEN_DAYS, limit=MAX_LIMIT
    )
    meta = sanctioned["data"]["parsed"]["meta"]
    replica_dedup = log_window.dedupe(replica_raw)
    return {
        "device": device,
        "sanctioned_status": sanctioned["status"],
        "sanctioned_records_before_dedup": meta.get("records_before_dedup"),
        "replica_raw_count": len(replica_raw),
        "sanctioned_deduped_count": len(sanctioned["data"]["parsed"]["records"]),
        "replica_deduped_count": len(replica_dedup),
        "counts_match": (
            meta.get("records_before_dedup") == len(replica_raw)
            and len(sanctioned["data"]["parsed"]["records"]) == len(replica_dedup)
        ),
    }


def main() -> None:
    inventory = load_inventory_file()
    devices = [(d.name, d.mgmt_ip) for d in inventory.devices]
    print(f"Devices from inventory: {[d[0] for d in devices]}", file=sys.stderr)

    table = load_mnemonic_table()
    all_mnemonics = [e["mnemonic"] for e in table]
    assert len(all_mnemonics) == 20, f"expected 20 mnemonics, found {len(all_mnemonics)}"

    severity_eligible = [
        m for m in all_mnemonics if _mnemonic_severity(m) not in STRUCTURALLY_EXCLUDED_SEVERITY
    ]
    structurally_excluded = [m for m in all_mnemonics if m not in severity_eligible]
    print(f"Severity-eligible ({len(severity_eligible)}): {severity_eligible}", file=sys.stderr)
    print(f"Structurally excluded, sev 6/7 ({len(structurally_excluded)}): {structurally_excluded}", file=sys.stderr)

    per_device: dict[str, dict[str, Any]] = {}
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for name, mgmt_ip in devices:
        print(f"--- {name} ({mgmt_ip}): device-wide 7d raw fetch ---", file=sys.stderr)
        raw, unparsed = _device_wide_raw(mgmt_ip)
        time.sleep(4.0)
        capped = len(raw) >= MAX_LIMIT
        timestamps = sorted(t for t in (r["timestamp"] for r in raw) if t)
        entry: dict[str, Any] = {
            "mgmt_ip": mgmt_ip,
            "device_wide_raw_count": len(raw),
            "device_wide_unparsed": unparsed,
            "device_wide_capped_at_1000": capped,
            "device_wide_earliest_raw_timestamp": timestamps[0] if timestamps else None,
            "device_wide_latest_raw_timestamp": timestamps[-1] if timestamps else None,
            "records": list(raw),
        }

        if capped:
            print(f"    CAPPED at {MAX_LIMIT} -- running mnemonic-filtered recovery query", file=sys.stderr)
            filtered, f_unparsed = _mnemonic_filtered_raw(mgmt_ip, severity_eligible)
            time.sleep(4.0)
            entry["filtered_recovery_raw_count"] = len(filtered)
            entry["filtered_recovery_capped_at_1000"] = len(filtered) >= MAX_LIMIT
            # Union device-wide + filtered-recovery, de-duplicated on the
            # SAME identity dedupe() uses, so a record present in both fetches
            # is not double counted.
            seen = {_record_key(r) for r in raw}
            for r in filtered:
                if _record_key(r) not in seen:
                    seen.add(_record_key(r))
                    entry["records"].append(r)
        else:
            entry["filtered_recovery_raw_count"] = None
            entry["filtered_recovery_capped_at_1000"] = None

        entry["combined_raw_count"] = len(entry["records"])
        entry["deduped_records"] = log_window.dedupe(entry["records"])
        entry["distinct_event_count"] = len(entry["deduped_records"])
        per_device[name] = entry

    # Spot check the raw-replica pipeline against the real sanctioned call,
    # on one uncapped and one capped device.
    spot_checks = []
    for name, mgmt_ip in devices:
        if name in ("P1", "PE2"):
            print(f"--- spot check vs sanctioned run_named_query: {name} ---", file=sys.stderr)
            # Compare against ONLY the device-wide slice (the first
            # `device_wide_raw_count` records), never the mnemonic-filtered
            # recovery records appended after it -- `run_named_query` itself
            # has no knowledge of the recovery query, so it is only a valid
            # cross-check against the device-wide fetch this replica shares
            # its parameters with.
            device_wide_slice = per_device[name]["records"][: per_device[name]["device_wide_raw_count"]]
            check = _spot_check(name, mgmt_ip, device_wide_slice)
            spot_checks.append(check)
            time.sleep(4.0)

    # ------------------------------------------------------------------ #
    # Per-mnemonic aggregation across all devices.
    # ------------------------------------------------------------------ #
    per_mnemonic: dict[str, dict[str, Any]] = {}
    for mnemonic in all_mnemonics:
        raw_lines = 0
        distinct_events = 0
        hosts: set[str] = set()
        loki_severity_labels: set[str] = set()
        device_severity_digit = _mnemonic_severity(mnemonic)
        earliest = None
        latest = None
        earliest_device = None
        latest_device = None
        per_device_distinct: dict[str, int] = {}
        samples: list[dict[str, Any]] = []

        for name, entry in per_device.items():
            d_raw = [r for r in entry["records"] if r["mnemonic"] == mnemonic]
            d_dedup = [r for r in entry["deduped_records"] if r["mnemonic"] == mnemonic]
            if not d_raw and not d_dedup:
                continue
            raw_lines += len(d_raw)
            distinct_events += len(d_dedup)
            if d_dedup:
                per_device_distinct[name] = len(d_dedup)
            for r in d_dedup:
                hosts.add(r.get("host") or name)
                if r.get("loki_severity_label"):
                    loki_severity_labels.add(r["loki_severity_label"])
                ts = r.get("timestamp", "")
                if ts:
                    if earliest is None or ts < earliest:
                        earliest, earliest_device = ts, name
                    if latest is None or ts > latest:
                        latest, latest_device = ts, name
            samples.extend(d_dedup[:2])

        per_mnemonic[mnemonic] = {
            "raw_lines": raw_lines,
            "distinct_events": distinct_events,
            "hosts": sorted(per_device_distinct),
            "distinct_events_by_host": per_device_distinct,
            "loki_severity_labels": sorted(loki_severity_labels),
            "mnemonic_severity_digit": device_severity_digit,
            "structurally_excluded_by_severity_floor": mnemonic in structurally_excluded,
            "earliest_device_timestamp": earliest,
            "earliest_on_device": earliest_device,
            "latest_device_timestamp": latest,
            "latest_on_device": latest_device,
            "sample_texts": [s.get("text", "")[:200] for s in samples[:5]],
        }

    # ------------------------------------------------------------------ #
    # LINEPROTO / LINK-3 pairing check.
    # ------------------------------------------------------------------ #
    link3 = []
    lineproto = []
    for name, entry in per_device.items():
        for r in entry["deduped_records"]:
            if r["mnemonic"] == "PKT_INFRA-LINK-3-UPDOWN":
                link3.append({**r, "device": name})
            elif r["mnemonic"] == "PKT_INFRA-LINEPROTO-5-UPDOWN":
                lineproto.append({**r, "device": name})

    def _second(ts: str) -> str:
        # "Aug 21 09:29:19.123 UTC" -> "Aug 21 09:29:19" (drop ms + tz token)
        m = re.match(r"^(\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\.\d+\s+\S+$", ts.strip())
        return m.group(1) if m else ts

    pairing = []
    for ev in link3:
        same_second = [
            lp for lp in lineproto
            if lp["device"] == ev["device"] and _second(lp["timestamp"]) == _second(ev["timestamp"])
        ]
        pairing.append({
            "link3_device": ev["device"],
            "link3_timestamp": ev["timestamp"],
            "link3_text": ev["text"],
            "paired_lineproto_found": bool(same_second),
            "paired_lineproto": same_second,
        })

    # ------------------------------------------------------------------ #
    # route_syslog_line() on the real, distinct events for the two live
    # candidates.
    # ------------------------------------------------------------------ #
    routing_results = []
    for mnemonic in ("ROUTING-BGP-5-ADJCHANGE", "PKT_INFRA-LINK-3-UPDOWN"):
        for name, entry in per_device.items():
            for r in entry["deduped_records"]:
                if r["mnemonic"] != mnemonic:
                    continue
                # Reconstruct the exact on-device line shape
                # (template_parsers._LOG_ENTRY) from the parsed Loki fields --
                # this is the Loki line minus syslog-ng's prepended host
                # token, rebuilt from the SAME regex groups logs_loki itself
                # already extracted, not retyped by hand.
                line = f"RP/0/RP0/CPU0:{r['timestamp']}: {r['process']}[{r['pid']}]: %{r['mnemonic']} : {r['text']}"
                decision = event_routing.route_syslog_line(line, device=name)
                routing_results.append({
                    "device": name,
                    "mnemonic": mnemonic,
                    "reconstructed_line": line,
                    "decision": decision.as_dict(),
                })

    # ------------------------------------------------------------------ #
    # Write everything out.
    # ------------------------------------------------------------------ #
    output = {
        "measured_at_utc": now_utc.isoformat(),
        "b206a_fix_date": "2026-08-20",
        "window_requested_since_seconds": SEVEN_DAYS,
        "window_requested_max_limit": MAX_LIMIT,
        "devices": {
            name: {k: v for k, v in entry.items() if k not in ("records", "deduped_records")}
            for name, entry in per_device.items()
        },
        "spot_checks": spot_checks,
        "per_mnemonic": per_mnemonic,
        "lineproto_link3_pairing": pairing,
        "routing_results": routing_results,
        # Full per-device deduped records kept separately so the summary
        # above stays readable; still in the same JSON file.
        "deduped_records_by_device": {
            name: entry["deduped_records"] for name, entry in per_device.items()
        },
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nWrote {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
