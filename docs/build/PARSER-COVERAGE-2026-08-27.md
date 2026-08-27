# Parser coverage and model-visible evidence — 2026-08-27

## Result

Measured against every IOS-XR text fixture currently committed:

| Measure | Structured | Deferred useful data | Presentation only | Unaccounted |
|---|---:|---:|---:|---:|
| Non-blank lines | 63.01% (8,403) | 8.47% (1,129) | 28.52% (3,803) | 0.00% (0) |
| Raw bytes | 81.31% (905,392) | 4.35% (48,474) | 14.33% (159,574) | 0.00% (0) |

Corpus: 640 files, 602 parser invocations, 13,335 non-blank lines, and
1,113,440 bytes. All parsers returned `PARSE_OK`; `unparsed_rows` was also
zero.

There are therefore three different answers to “how much is parsed?”:

1. **63.01% of all non-blank command-output lines are structurally parsed.**
2. **88.16% of information-bearing lines are structurally parsed.** This
   excludes the 3,803 lines already classified as presentation-only, leaving
   8,403 parsed and 1,129 deferred lines.
3. **100% of fixture lines are accounted for**, but “accounted for” includes
   declared ignores and must not be reported as 100% parsed.

The line percentage is the better measure for comparing commands. The byte
percentage is dominated by `show logging last 200`: logging supplies 690,674
bytes (62.03% of the corpus) and parses 99.92% of them. Without logging, only
48.76% of non-blank lines are structured, 11.77% are deferred, and 39.46% are
presentation. Among the remaining information-bearing non-log lines, 80.55%
are parsed and 19.45% are deferred.

## Per-parser line coverage

| Parser | Structured | Deferred | Presentation | Unknown |
|---|---:|---:|---:|---:|
| `intent:bgp` | 18.59% | 5.50% | 75.92% | 0.00% |
| `intent:bgp_vpnv4` | 18.68% | 5.49% | 75.82% | 0.00% |
| `intent:facts` | 25.00% | 0.00% | 75.00% | 0.00% |
| `intent:interfaces` | 66.96% | 0.00% | 33.04% | 0.00% |
| `intent:isis` | 71.64% | 0.00% | 28.36% | 0.00% |
| `intent:ldp` | 97.86% | 0.00% | 2.14% | 0.00% |
| `intent:ldp_discovery` | 94.94% | 0.00% | 5.06% | 0.00% |
| `intent:lldp` | 35.04% | 10.83% | 54.13% | 0.00% |
| `intent:sr` | 16.22% | 12.61% | 71.17% | 0.00% |
| `template:bgp_neighbor` | 27.22% | 22.23% | 50.55% | 0.00% |
| `template:config_interface` | 63.64% | 0.00% | 36.36% | 0.00% |
| `template:config_isis` | 30.00% | 45.71% | 24.29% | 0.00% |
| `template:interface` | 52.22% | 17.38% | 30.41% | 0.00% |
| `template:logging` | 99.52% | 0.00% | 0.48% | 0.00% |
| `template:ping` | 60.00% | 0.00% | 40.00% | 0.00% |
| `template:route` | 71.12% | 0.00% | 28.88% | 0.00% |
| `template:traceroute` | 58.02% | 0.00% | 41.98% | 0.00% |

Low structured coverage is not automatically a defect. BGP summary, facts,
and SR output contain many repeated headings and separators. The actionable
gap is the **deferred** column: those rules are explicitly marked
`NOT_NEEDED_YET`, meaning their lines carry real values but no parser schema
currently retains them.

## What the model actually receives

The normal model boundaries replace `data.commands` with
`commands_withheld`; the model gets command names and character/line counts,
not raw device output. `unaccounted_lines` is likewise replaced by a count.
Structured `data.parsed` fields pass through, with device-authored free-text
fields quoted as untrusted content.

Consequently:

* the model receives the structured 63.01% line-equivalent, subject to later
  evidence budgets;
* it receives **none of the 1,129 deferred lines**, and currently receives no
  count telling it that those useful fields were omitted;
* it does not receive presentation-only lines, which is desirable;
* it receives no raw fallback on parse failure, by design;
* agent/fabric analysis can additionally truncate the serialized structured
  result at the default 4,000 characters per intent and 40,000 characters per
  fabric; free-text projection has a separate 8,000-character budget.

This means there is no honest single “percentage of raw bytes visible to the
AI.” Parsed structures compress and reorganize the source, and downstream
budgets depend on the exact investigation. The figures above measure parser
coverage; model-visible prompt coverage needs a separate run against each
actual investigation and its truncation report.

## Highest-value missing structured evidence

The 1,129 deferred lines are concentrated in a small surface:

| Parser | Deferred lines | Deferred bytes | Examples |
|---|---:|---:|---|
| `bgp_neighbor` | 544 | 24,008 | denied/advertised prefixes, slow-peer state, connection attempts, BFD/read activity |
| `interface` | 392 | 19,117 | load, speed/duplex, last input/output, five-minute input/output rates |
| `config_isis` | 96 | 2,603 | BFD timers, FRR/TI-LFA, flex-algo and SR configuration |
| `lldp` | 38 | 1,026 | device-reported neighbor count, currently not cross-checked |
| `sr` | 28 | 976 | table/process state and policy detail currently outside the base schema |
| `bgp` + `bgp_vpnv4` | 31 | 744 | table-level state flags |

The most frequent individual omissions are prefix-denial counters (160
lines), slow-peer details (240 lines including state headers), interface
load/activity snapshots (180), advertised-prefix counters (80), five-minute
rates (144), and speed/duplex summaries (68).

## Recommended target

Do not make “100% raw CLI text in the prompt” the target. That would reverse
invariant 4, restore a prompt-injection surface, spend tokens on tables and
separators, and make judgment depend on formatting quirks the typed parsers
already eliminate.

Use these targets instead:

1. **100% accounting**: keep unknown and malformed rows at zero on the real
   corpus. Already achieved for current fixtures.
2. **100% information-bearing parsing**: convert every exercised
   `NOT_NEEDED_YET` line into typed `meta`, `records`, or a typed
   `supplemental` object. Current result: 88.16% by line, 94.92% by byte;
   excluding logging: 80.55% by line and 81.62% by byte.
3. **100% omission disclosure**: put structured counts for deferred,
   presentation, unknown, malformed, and budget-truncated evidence into every
   model-facing envelope. The model should always know its evidence margin.
4. **Explicit expansion for exceptional raw context**: when a parser cannot
   represent a needed detail, expose it only through a bounded, pre-approved
   expansion tool and quote it as untrusted device text. Do not silently add
   all command output to every prompt.

Implementation priority should be `bgp_neighbor`, `interface`, then
`config_isis`; together they contain 91.41% of all deferred lines. After those
schemas are extended, promote LLDP/SR/BGP summary fields and leave only true
presentation rules in the ignore sets.

## Reproducing the measurement

Run:

```bash
.venv/bin/python tools/measure_parser_coverage.py
.venv/bin/python tools/measure_parser_coverage.py --json
```

The auditor instruments the same `finalize()` calls used by production
parsers. It assigns every byte and every non-blank line to exactly one bucket,
fails if any fixture has no parser mapping or any parser does not return
`PARSE_OK`, and reports `unparsed_rows` separately. It does not modify parser
results or model behavior.
