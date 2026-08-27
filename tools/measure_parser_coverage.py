#!/usr/bin/env python3
"""Measure how much captured command output becomes structured evidence.

The parsers already prove that every non-blank line is *accounted for*.  That
is deliberately weaker than proving that every line is parsed.  This audit
splits the fixture corpus into four mutually exclusive buckets:

* ``structured`` -- a parser claims the line while building meta/records;
* ``deferred`` -- a NOT_NEEDED_YET ignore rule claims useful information;
* ``presentation`` -- blank lines, banners, headers, and other lines declared
  to carry no extractable field;
* ``unaccounted`` -- neither the parser nor a declared rule claims the line.

The byte totals include line endings, so the four buckets sum to the exact
fixture byte count.  Line totals exclude blank lines, matching the parser's
section-0.10 accounting contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_nettools import config_section, parsers, template_parsers  # noqa: E402


@dataclass
class Counts:
    files: int = 0
    cases: int = 0
    raw_bytes: int = 0
    meaningful_lines: int = 0
    structured_bytes: int = 0
    structured_lines: int = 0
    deferred_bytes: int = 0
    deferred_lines: int = 0
    presentation_bytes: int = 0
    presentation_lines: int = 0
    unaccounted_bytes: int = 0
    unaccounted_lines: int = 0
    unparsed_rows: int = 0

    def add(self, other: "Counts") -> None:
        for field in self.__dataclass_fields__:
            setattr(self, field, getattr(self, field) + getattr(other, field))


class Auditor:
    def __init__(self) -> None:
        self.current_name = ""
        self.current_files = 0
        self.by_parser: dict[str, Counts] = defaultdict(Counts)
        self.deferred_reasons: Counter[str] = Counter()
        self.unaccounted_samples: Counter[str] = Counter()
        self.finalize_calls = 0

    def finalize(self, original: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        raw = kwargs["raw"]
        consumed = {line.strip() for line in kwargs.get("consumed", ())}
        custom_rules = tuple(kwargs.get("ignores", ()))
        common_rules = template_parsers.XR_COMMON_IGNORES
        counts = Counts(files=self.current_files, cases=1, raw_bytes=len(raw.encode()))

        for segment in raw.splitlines(keepends=True):
            byte_count = len(segment.encode())
            line = segment.rstrip("\r\n")
            stripped = line.strip()
            if not stripped:
                counts.presentation_bytes += byte_count
                continue

            counts.meaningful_lines += 1
            if stripped in consumed:
                counts.structured_lines += 1
                counts.structured_bytes += byte_count
                continue

            if any(rule.matches(line) or rule.matches(stripped) for rule in common_rules):
                counts.presentation_lines += 1
                counts.presentation_bytes += byte_count
                continue

            matched = next(
                (rule for rule in custom_rules if rule.matches(line) or rule.matches(stripped)),
                None,
            )
            if matched is None:
                counts.unaccounted_lines += 1
                counts.unaccounted_bytes += byte_count
                self.unaccounted_samples[stripped] += 1
            elif matched.kind is template_parsers.IgnoreKind.NOT_NEEDED_YET:
                counts.deferred_lines += 1
                counts.deferred_bytes += byte_count
                self.deferred_reasons[matched.reason] += 1
            else:
                counts.presentation_lines += 1
                counts.presentation_bytes += byte_count

        # ``splitlines`` omits a final zero-length segment but not its bytes;
        # assign any discrepancy (normally none) to presentation overhead.
        allocated = (
            counts.structured_bytes
            + counts.deferred_bytes
            + counts.presentation_bytes
            + counts.unaccounted_bytes
        )
        counts.presentation_bytes += counts.raw_bytes - allocated
        counts.unparsed_rows = int(kwargs.get("unparsed_rows", 0))
        self.by_parser[self.current_name].add(counts)
        self.finalize_calls += 1
        return original(**kwargs)


@contextmanager
def traced_finalizers(auditor: Auditor) -> Iterable[None]:
    modules = (parsers, template_parsers, config_section)
    originals = {module: module.finalize for module in modules}
    for module, original in originals.items():
        module.finalize = lambda _original=original, **kwargs: auditor.finalize(  # type: ignore[attr-defined]
            _original, **kwargs
        )
    try:
        yield
    finally:
        for module, original in originals.items():
            module.finalize = original  # type: ignore[attr-defined]


def _template_for(path: Path) -> str | None:
    name = path.name
    if name.startswith("show-bgp-neighbor-"):
        return "bgp_neighbor"
    if name.startswith("show-route-"):
        return "route"
    if name.startswith("show-interfaces-") and name != "show-interfaces-brief.txt":
        return "interface"
    if name.startswith("show-logging-last-"):
        return "logging"
    if name.startswith("ping-"):
        return "ping"
    if name.startswith("traceroute-"):
        return "traceroute"
    if name == "show-running-config-router-isis.txt":
        return "config_isis"
    if name.startswith("show-running-config-interface-"):
        return "config_interface"
    return None


STATIC_FILES = {
    "show-interfaces-brief.txt": "interfaces",
    "show-bgp-summary.txt": "bgp",
    "show-bgp-vpnv4-unicast-summary.txt": "bgp_vpnv4",
    "show-lldp-neighbors.txt": "lldp",
    "show-isis-neighbors.txt": "isis",
    "show-mpls-ldp-neighbor.txt": "ldp",
    "show-mpls-ldp-discovery.txt": "ldp_discovery",
    "show-segment-routing-traffic-eng-policy.txt": "sr",
}
FACT_FILES = ("show-running-config-hostname.txt", "show-version.txt")


def audit(corpus: Path) -> Auditor:
    auditor = Auditor()
    seen: set[Path] = set()
    failures: list[str] = []

    with traced_finalizers(auditor):
        for label_dir in sorted({path.parent for path in corpus.rglob("*.txt")}):
            fact_paths = tuple(label_dir / name for name in FACT_FILES)
            if all(path.is_file() for path in fact_paths):
                auditor.current_name = "intent:facts"
                auditor.current_files = len(fact_paths)
                outputs = {path.name: path.read_text() for path in fact_paths}
                _parsed, status = parsers.parse_intent("cisco_xr", "facts", outputs)
                if status != parsers.PARSE_OK:
                    failures.append(f"{label_dir}: facts={status}")
                seen.update(fact_paths)

            for path in sorted(label_dir.glob("*.txt")):
                if path in seen:
                    continue
                intent = STATIC_FILES.get(path.name)
                template = _template_for(path)
                auditor.current_files = 1
                if intent is not None:
                    auditor.current_name = f"intent:{intent}"
                    _parsed, status = parsers.parse_intent(
                        "cisco_xr", intent, {path.name: path.read_text()}
                    )
                elif template is not None:
                    auditor.current_name = f"template:{template}"
                    _parsed, status = template_parsers.parse_template_output(
                        "cisco_xr", template, path.read_text()
                    )
                else:
                    failures.append(f"{path}: no parser mapping")
                    continue
                if status != parsers.PARSE_OK:
                    failures.append(f"{path}: {auditor.current_name}={status}")
                seen.add(path)

    all_files = set(corpus.rglob("*.txt"))
    missing = sorted(all_files - seen)
    if missing:
        failures.extend(f"{path}: not audited" for path in missing)
    if failures:
        raise RuntimeError("parser coverage audit failed:\n" + "\n".join(failures))
    return auditor


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def report(auditor: Auditor) -> dict[str, Any]:
    total = Counts()
    for counts in auditor.by_parser.values():
        total.add(counts)

    def enrich(counts: Counts) -> dict[str, Any]:
        data = asdict(counts)
        data["structured_line_pct"] = _pct(counts.structured_lines, counts.meaningful_lines)
        data["deferred_line_pct"] = _pct(counts.deferred_lines, counts.meaningful_lines)
        data["presentation_line_pct"] = _pct(counts.presentation_lines, counts.meaningful_lines)
        data["unaccounted_line_pct"] = _pct(counts.unaccounted_lines, counts.meaningful_lines)
        data["structured_byte_pct"] = _pct(counts.structured_bytes, counts.raw_bytes)
        data["deferred_byte_pct"] = _pct(counts.deferred_bytes, counts.raw_bytes)
        data["presentation_byte_pct"] = _pct(counts.presentation_bytes, counts.raw_bytes)
        data["unaccounted_byte_pct"] = _pct(counts.unaccounted_bytes, counts.raw_bytes)
        return data

    return {
        "definition": {
            "structured": "claimed while building parser meta/records",
            "deferred": "matched by a NOT_NEEDED_YET ignore rule",
            "presentation": "blank or declared NO_EXTRACTABLE_FIELD/common output",
            "unaccounted": "claimed by neither parser nor ignore rule",
        },
        "total": enrich(total),
        "by_parser": {name: enrich(auditor.by_parser[name]) for name in sorted(auditor.by_parser)},
        "top_deferred_reasons": auditor.deferred_reasons.most_common(20),
        "unaccounted_samples": auditor.unaccounted_samples.most_common(20),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "cisco_xr",
        help="fixture corpus root (default: tests/fixtures/cisco_xr)",
    )
    parser.add_argument("--json", action="store_true", help="emit complete JSON")
    args = parser.parse_args()
    result = report(audit(args.corpus))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        total = result["total"]
        print(
            f"{total['files']} files / {total['cases']} parser cases / "
            f"{total['raw_bytes']} bytes / {total['meaningful_lines']} non-blank lines"
        )
        print("parser                         structured   deferred  presentation  unaccounted")
        for name, row in result["by_parser"].items():
            print(
                f"{name:30} {row['structured_line_pct']:9.2f}% "
                f"{row['deferred_line_pct']:9.2f}% {row['presentation_line_pct']:12.2f}% "
                f"{row['unaccounted_line_pct']:11.2f}%"
            )
        print(
            f"TOTAL (lines)                 {total['structured_line_pct']:9.2f}% "
            f"{total['deferred_line_pct']:9.2f}% {total['presentation_line_pct']:12.2f}% "
            f"{total['unaccounted_line_pct']:11.2f}%"
        )
        print(
            f"TOTAL (bytes)                 {total['structured_byte_pct']:9.2f}% "
            f"{total['deferred_byte_pct']:9.2f}% {total['presentation_byte_pct']:12.2f}% "
            f"{total['unaccounted_byte_pct']:11.2f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
