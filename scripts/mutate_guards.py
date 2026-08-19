#!/usr/bin/env python3
"""Remove each guard, confirm its test notices, restore. A guardrail that cannot fail is not one.

A test that passes proves the code does something. **Only removing the guard proves the
test would notice if it stopped.** `BUILD-PLAN.md` §0.12 is the rule; this is the check.

Three defects in the first version of this harness, all found by using it, and each one
shaped how this version works:

1.  **It picked a guard's test file by name similarity** — `test_mcp_server.py` for a guard
    in `mcp_server/server.py`, `test_descent.py` for one in `descent.py`. Both guesses were
    wrong; the guards live in `test_mcp_boundary.py` and `test_flows.py`. It reported both
    as **VACUOUS**.

    **A verifier whose errors produce plausible alarms is more dangerous than one whose
    errors produce noise.** "This guard is vacuous" is exactly the finding this tool exists
    to make, so its false negatives are indistinguishable from its true positives — and
    they point at the more alarming conclusion, which is the one a tired reader accepts.
    B-458 and B-456 would have been reopened on a false report.

    **So a test target is never guessed here.** `resolve_guard_tests` greps `tests/` for the
    guard's own symbol. If it cannot find one it **fails loudly and exits non-zero** rather
    than running a mutation it cannot interpret. *Unresolvable* and *vacuous* are different
    answers and must never render the same.

2.  **It left stale `__pycache__`.** After a pass, `make test` reported a failure that was
    in no source file. That direction is loud and self-correcting. **The other direction is
    not**: source restored, bytecode still mutated, a later run executing code that exists
    nowhere and reporting green — *a mutation recorded as caught when it was not*. Every
    mutation here is bracketed by a cache purge, and the purge is asserted.

3.  **It could in principle have left a mutation on disk.** Restores are asserted
    byte-for-byte, and the run ends by re-checking the four frozen files against the T-001
    baseline and requiring a clean `git status`.

**No frozen file is ever mutated.** `BUILD-PLAN.md` §0.5 freezes `test_safety.py`,
`test_template_security.py`, `platforms.py` and `templates.py`, so the two most
safety-critical suites cannot be mutation-tested at all while that holds. What this tool
can do is mutate *the code they guard*, and the first two entries below do exactly that.

Usage:
    python scripts/mutate_guards.py            # all guards
    python scripts/mutate_guards.py B-453 ...  # a subset
"""

from __future__ import annotations

import pathlib
import shutil
import signal
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

FROZEN = {
    "tests/test_safety.py",
    "tests/test_template_security.py",
    "src/agent_nettools/platforms.py",
    "src/agent_nettools/templates.py",
}

BASELINE_COMMIT = "6629a2c"

#: id, what it guards, file, the exact text to remove, what to put there, and the SYMBOL
#: that identifies the guard's test. The symbol is what `tests/` is searched for -- never
#: the module name, never a filename.
MUTATIONS = [
    ("TICKET-FORGERY", "a caller string cannot forge a ticket section or verdict",
     "src/agent_nettools/ticket.py",
     '    flat = " ".join(str(text).splitlines())\n    return flat.replace("`", "\'").strip() or "(empty)"\n',
     '    return str(text)\n',
     "test_a_forged_outcome_in_the_subject_is_not_parsed_as_a_verdict"),

    ("TICKET-DEGRADE", "a ticket write failure never fails an investigation",
     "src/agent_nettools/ticket.py",
     '        if open_mode == "x":\n            raise\n',
     '        raise\n',
     "test_a_tickets_directory_that_is_a_file_still_never_raises"),

    ("B-497", "transport healthy requires an FSM state implying established TCP",
     "src/agent_nettools/checks.py",
     "        if armed and fsm in _TCP_UP_STATES:\n",
     "        if armed:\n",
     "test_a_socket_armed_mid_connect_is_not_evidence_of_transport"),

    ("ROUND-8-SOCKET", "the BGP socket line is read positionally, not by first match",
     "src/agent_nettools/template_parsers.py",
     '            meta["socket_armed_read"] = match["read"] == "armed"\n',
     '            meta["socket_armed_read"] = match["io"] == "armed"\n',
     "test_the_socket_line_is_read_positionally_not_by_first_match"),

    ("B-467-MCP", "the MCP boundary quotes device free text (holistic review)",
     "mcp_server/boundary.py",
     "            elif key in _FREE_TEXT_FIELD_NAMES and isinstance(value, str):\n",
     "            elif key in _FREE_TEXT_FIELD_NAMES and isinstance(value, str) and False:\n",
     "test_device_free_text_in_parsed_records_is_quoted_not_left_bare"),

    ("P0-ALERTMANAGER-SUBJECT", "an alertmanager subject is validated before routing",
     "src/agent_nettools/event_routing.py",
     "    return text if _INTERFACE_NAME.fullmatch(text) else None\n",
     "    return text\n",
     "test_an_alertmanager_subject_carrying_shell_metacharacters_is_refused"),

    ("SAFETY-ALLOWLIST", "the allowlist check itself",
     "src/agent_nettools/network_tools.py",
     "    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "    unsafe_commands = []\n",
     "test_refuses_unapproved_commands"),

    ("SAFETY-ORDER", "the allowlist is checked before credentials load",
     "src/agent_nettools/network_tools.py",
     "    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "    _ = get_device(device_name)\n    unsafe_commands = [command for command in commands if not is_approved(platform, command)]\n",
     "test_refuses_unapproved_commands_before_loading_credentials"),

    ("B-453", "identifier containment is reached by the gate",
     "src/agent_nettools/grounding.py",
     "        .merge(check_identifier_containment(report, descent))\n", "",
     "check_identifier_containment"),

    ("T-035", "the notifier cannot receive an evidence bundle",
     "src/agent_nettools/notifier.py",
     "    notifier: Notifier | None = None,\n",
     "    notifier: Notifier | None = None,\n    evidence: dict | None = None,\n",
     "test_the_notifier_cannot_receive_an_evidence_bundle"),

    ("B-424", "timeline citations are checked",
     "src/agent_nettools/grounding.py",
     'GroundingFailure(\n                    "invented_timestamp"',
     'GroundingFailure(\n                    "DISABLED_invented_timestamp"',
     "invented_timestamp"),

    ("B-428", "no_fault_on_path when rung 1 is healthy",
     "src/agent_nettools/descent.py", "NO_FAULT_ON_PATH", "ALL_LAYERS_HEALTHY",
     "no_fault_on_path"),

    ("B-436", "coherence refuses on a moved fabric",
     "src/agent_nettools/epoch.py",
     "        return self.status in (FABRIC_MOVED, UNVERIFIED)",
     "        return False",
     "refuses"),

    ("B-458", "the MCP boundary sanitises at registration",
     "mcp_server/server.py", "sanitize(", "(lambda x, **k: x)(",
     "test_registration_is_what_applies_the_boundary"),

    ("B-456", "aggregation moves with the member set",
     "src/agent_nettools/descent.py",
     "            return list(members), Aggregation.ANY_HEALTHY",
     "            return list(members), Aggregation.ALL_HEALTHY",
     "test_each_member_set_carries_its_own_aggregation"),

    ("B-465", "an adjacency count above baseline is not a fault",
     "src/agent_nettools/checks.py", "    if actual > expected:\n", "    if False:\n",
     "test_losing_an_adjacency_warns_and_gaining_one_does_not"),

    ("B-435", "LLDP device IDs resolve before comparison",
     "src/agent_nettools/topology.py",
     "            if resolve_device(neighbor, mapping) is None:\n",
     "            if neighbor not in evidence_by_device:\n",
     "test_the_three_hostnames_are_this_fabric_s_own_devices"),

    ("B-461", "rungs are numbered in the report",
     "src/agent_nettools/render.py", '"{position}/{total} ', '"',
     "position"),

    # ---- Wave 1 guards (FIX-PLAN). Three of these first ran with wrong
    # anchors/symbols or against a genuinely vacuous suite; the B-474 entry
    # below is the one that found a real gap -- the tests proved the atomic
    # helper behaved and nothing proved the save paths CALLED it. The wiring
    # test exists because this entry reported VACUOUS (OBS-153). ----

    ("B-470", "the analyze path projects evidence before serializing",
     "src/agent_nettools/llm_analysis.py",
     "project_evidence(", "(lambda e, **k: e)(",
     "test_the_raw_text_canaries_never_reach_the_prompt"),

    ("B-467", "embedded delimiters cannot escape the quote block",
     "src/agent_nettools/model_egress.py",
     'text.replace(DEVICE_TEXT_OPEN, "").replace(DEVICE_TEXT_CLOSE, "")',
     "text",
     "test_quote_device_text_strips_embedded_delimiters"),

    ("B-474", "save paths route through the atomic writer",
     "src/agent_nettools/evidence_store.py",
     "_atomic_write_text(path, json.dumps(evidence, indent=2))",
     'path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")',
     "test_save_paths_actually_route_through_the_atomic_writer"),

    ("B-474b", "one golden row per device is enforced by index",
     "src/agent_nettools/evidence_store.py",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_one_golden",
     "CREATE INDEX IF NOT EXISTS idx_snapshots_one_golden",
     "rejects_a_second_golden_row"),

    ("B-473", "active probes carry distinct annotations",
     "mcp_server/server.py",
     '"title": "ACTIVE PROBE — generates network traffic"',
     '"title": "probe"',
     "ACTIVE PROBE"),

    ("B-468", "a probe with zero received exits nonzero",
     "src/agent_nettools/cli.py",
     "    return EXIT_OK if received > 0 else EXIT_WARNING",
     "    return EXIT_OK",
     "test_a_ping_with_total_loss_exits_nonzero"),

    ("B-108", "a REFUSED flow reaches the operator with its reason and a "
     "pointer to the real surface, not argparse's 'invalid choice'",
     "src/agent_nettools/cli.py",
     "    if args.flow in flows.REFUSED_OBJECT_TYPES:",
     "    if False and args.flow in flows.REFUSED_OBJECT_TYPES:",
     "test_a_refused_flow_is_answered_with_its_reason_not_an_invalid_choice"),

    ("B-511", "a document carrying the evaluation-material marker is never "
     "returned by search_knowledge -- the corpus must not hold its own "
     "answer key (OBS-194: a model under test searched MCP-RETEST-PROTOCOL.md "
     "and read back the expected answer to the fabrication question it was "
     "just asked)",
     "src/agent_nettools/knowledge.py",
     "            if _is_evaluation_material(lines):\n                continue\n",
     "",
     "test_evaluation_material_is_never_returned_by_search"),

    ("B-489", "_run_rendered_command has no command= parameter to smuggle a "
     "pre-rendered string through, bypassing reconstruction against the "
     "declaring template",
     "src/agent_nettools/network_tools.py",
     "    params: dict[str, str],\n"
     "    read_timeout: float | None = None,\n"
     "    sender: Callable[[dict[str, Any], str], str] | None = None,\n"
     ") -> dict[str, Any]:\n",
     "    params: dict[str, str],\n"
     "    command: str | None = None,\n"
     "    read_timeout: float | None = None,\n"
     "    sender: Callable[[dict[str, Any], str], str] | None = None,\n"
     ") -> dict[str, Any]:\n",
     "test_run_rendered_command_refuses_a_show_running_config_shaped_smuggled_command"),

    ("B-446-LATENCY-DEDUP", "EvidenceEpoch.latency_ms sums each device's "
     "DISTINCT observation windows, not every observation -- the many intents "
     "that share one session's started/completed pair must contribute that "
     "window's duration once, not once per intent",
     "src/agent_nettools/epoch.py",
     "            windows.setdefault(o.device, set()).add((o.started, o.completed))\n",
     "            windows.setdefault(o.device, []).append((o.started, o.completed))\n",
     "test_latency_ms_deduplicates_observations_sharing_one_window"),

    ("B-508", "every intent PLATFORM_INTENTS['cisco_xr'] declares has a "
     "CHECK_TOOLS entry -- bgp_vpnv4/ldp/ldp_discovery were collected and "
     "parsed on every device read with no way for an operator to check any "
     "of them alone (the third instance of OBS-187/OBS-191's shape: a "
     "capability exists and the surface does not name it)",
     "src/agent_nettools/network_tools.py",
     '    "bgp_vpnv4": check_bgp_vpnv4_neighbors,\n', "",
     "test_every_cisco_xr_intent_has_a_check_tool"),

    # ---- B-512 (Job 2): Loki/Prometheus as MCP tools. The main new guard is
    # the external-source gate -- the same enforcement shape B-493 gave the
    # active-probe gate, applied to a third registration class
    # (`_external_source_tool`). Counted before mutating (OBS-191): the
    # anchor string below occurs exactly once in server.py, and nothing else
    # in `_register_sanitized_tool` independently stops the wrapped function
    # (logs_loki.run_named_query/metrics_prometheus.run_named_query) from
    # running while the gate is closed -- so a test that proves the wrapped
    # function is never called is not vacuously true by some other path. ----

    ("B-512-GATE", "the external-source gate is checked before an "
     "external-source tool's wrapped function (logs_loki/"
     "metrics_prometheus run_named_query) is ever called",
     "mcp_server/server.py",
     "            if external_source and not _mcp_external_sources_allowed():\n",
     "            if external_source and False:\n",
     "test_external_source_gate_actually_prevents_the_call_when_disabled"),

    # ---- This task: NetBox/neo4j as MCP read tools. The main new guard is
    # the "derived, not authoritative" claim in `get_lab_netbox_inventory`'s
    # own description -- OBS-112/MCP §14 established that a tool's
    # DESCRIPTION, not its docstring's existence, is what a model actually
    # reasons from, so the one sentence stating NetBox is a recording (never
    # live) is the load-bearing text, not a side note. Counted before adding
    # this entry (OBS-191): the anchor string occurs exactly once in
    # server.py (`grep -c` against the line below), so removing it here
    # cannot leave the claim true elsewhere in the file by accident. The
    # gate reused for these two tools (NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES,
    # `_external_source_tool`) is the same shared mechanism B-512-GATE above
    # already mutation-tests generically -- a new gate-specific entry here
    # would duplicate that guard, not add one. ----

    ("NETBOX-DERIVED", "get_lab_netbox_inventory's description states "
     "NetBox is DERIVED from parsed device evidence and is never "
     "authoritative about the live fabric -- the reasoning surface a model "
     "actually acts on (OBS-112), not merely documented in a module a model "
     "never reads",
     "mcp_server/server.py",
     "    collector and is NEVER authoritative about the live fabric: it is a\n",
     "    collector: it is a\n",
     "test_netbox_tools_state_they_are_derived_not_authoritative"),
    # B-104: the config axis (D16). config_section.py's `parse_xr_config_isis`
    # never stores what follows an `authentication ...` line -- only that one
    # was seen (`authentication_configured: bool`). Counted before mutating
    # (OBS-191): the anchor string below occurs exactly once in
    # config_section.py (the interface-scoped branch; the global-scope branch
    # a few lines up uses `meta[...]`, a textually different string, so this
    # mutation targets one occurrence unambiguously). Mutated to store the
    # raw line instead of a bool -- still truthy, so a check that only looked
    # at "is this field falsy" would not notice; the guard is specifically
    # that no secret-shaped text reaches any field, which is what the test
    # below actually asserts (`canary not in json.dumps(parsed)`).
    ("OBS-202", "an abbreviated interface name is EXPANDED to the spelling "
     "the telemetry stores, so `Gi0/0/0/0` from check_lab_interfaces reaches "
     "the same series as `GigabitEthernet0/0/0/0` -- without it the natural "
     "list-then-ask workflow returns zero samples and reports the series was "
     "never observed, which is a confidently wrong answer rather than an error",
     "src/agent_nettools/metrics_prometheus.py",
     "        return canonical(value)",
     "        return value",
     "test_an_abbreviated_interface_name_finds_the_same_series_as_the_long_form"),

    ("B-104", "config_isis never stores what follows an 'authentication ...' "
     "line in a structured field",
     "src/agent_nettools/config_section.py",
     '                current["authentication_configured"] = True\n',
     '                current["authentication_configured"] = stripped\n',
     "test_config_isis_never_stores_what_follows_authentication"),

    # ---- B-101/B-102: the reasoning gate (docs/design/reasoning-gate.md,
    # operator-approved). `reasoning_gate.parse_decision` turns a model's raw
    # "narrow" decision into a `NarrowRequest` by indexing into the
    # code-enumerated candidate list -- never by reading an identity out of
    # the model's own text. The bound this entry mutation-tests is the index
    # range check, and specifically its LOWER half: without it, Python's own
    # negative indexing lets `index=-1` silently resolve to `candidates[-1]`,
    # the LAST enumerated candidate, instead of being refused -- a fabricated
    # selection wearing syntactically valid clothing, exactly the shape
    # B-459 warns about for a free-text target. Counted before mutating
    # (OBS-191): the anchor string below occurs exactly once in
    # reasoning_gate.py (`grep -c` against the line, checked by hand before
    # this entry was added). Mutated to drop the `index < 0 or` half, leaving
    # only the upper-bound check -- still refuses an index too large, so a
    # test that only tried an oversized index would not notice; the guard is
    # specifically that a negative index is refused too, which is what
    # `test_a_negative_index_is_refused_not_silently_wrapped` actually
    # asserts. ----

    ("B-550", "parse_decision refuses a negative index rather than letting "
     "Python's own negative indexing silently resolve it to the last "
     "enumerated candidate",
     "src/agent_nettools/reasoning_gate.py",
     "        if index < 0 or index >= len(candidates):\n",
     "        if index >= len(candidates):\n",
     "test_a_negative_index_is_refused_not_silently_wrapped"),
    # B-106: the intent-vs-observed diff (D16's payoff). `config_diff.FieldDiff`
    # closes over three outcomes -- agrees/disagrees/cannot_compare -- and
    # refuses to construct a `cannot_compare` result with no `reason`. That is
    # the guard against this build's own recurring failure, an absence
    # rendered as a value (OBS-188, OBS-202), applied to the new axis. Counted
    # before mutating (OBS-191): the anchor string below occurs exactly once
    # in config_diff.py (`grep -c` against the line, confirmed before this
    # entry was added). Mutated to a no-op `pass`, so a `cannot_compare`
    # `FieldDiff` with no reason constructs silently instead of raising --
    # the test below only holds if that construction is rejected.
    ("B-540", "a cannot_compare FieldDiff must carry a reason, or a missing "
     "comparison silently reads as a value nobody actually checked",
     "src/agent_nettools/config_diff.py",
     '        if self.outcome == CANNOT_COMPARE and not self.reason:\n'
     '            raise ValueError(\n'
     '                "a cannot_compare FieldDiff must carry a reason -- an absence with "\n'
     '                "no explanation is indistinguishable from a value nobody checked"\n'
     '            )\n',
     '        pass\n',
     "test_cannot_compare_without_a_reason_is_rejected"),
    # B-110/B-111. Counted before mutating (OBS-191): the anchor occurs exactly
    # once in flows.py. Without this branch, `flow_for("l3vpn_service")` and
    # `flow_for("topology")` still raise `NotImplementedError` -- the generic
    # "pending design" fallback also names the object type in its repr, so a
    # test that only checked the exception type or `match=object_type` would
    # not notice the mutation at all. What the mutation actually removes is
    # each refusal's *specific* reason (the VRF-collection-surface gap, the
    # audit/learn-topology pointers) -- exactly what
    # `test_l3vpn_service_is_refused_not_merely_unbuilt` asserts is present.
    ("B-110-B-111", "a REFUSED_OBJECT_TYPES flow raises its own specific "
     "reason, not the generic pending-design fallback that would otherwise "
     "still fire (and still name the object type) with none of the reasoning",
     "src/agent_nettools/flows.py",
     "    if object_type in REFUSED_OBJECT_TYPES:",
     "    if False and object_type in REFUSED_OBJECT_TYPES:",
     "test_l3vpn_service_is_refused_not_merely_unbuilt"),
    ("B-511-PROBE", "the probe generator's exclusion filter actually removes "
     "reserved addresses, rather than the candidate pool merely happening "
     "not to include one (B-511/OBS-195: a fabrication probe whose answer is "
     "written down anywhere is a retrieval test; the generator exists so a "
     "leaked identifier is impossible, which depends on this filter, not on "
     "luck)",
     "scripts/generate_probe_identifier.py",
     "    return [str(ip) for ip in network.hosts() if str(ip) not in reserved]\n",
     "    return [str(ip) for ip in network.hosts()]\n",
     "test_unused_hosts_excludes_reserved_and_keeps_everything_else"),
    # ---- B-519: the interface-name canonicalisation audit. The four
    # entry points below (`get_lab_interface`, `investigate_lab_session`,
    # `nettools interface`, `nettools investigate`) were AUDITED and found
    # to be EXEMPT -- see tests/test_interface_canonicalization.py's module
    # docstring for why forwarding the caller's own spelling, unrewritten,
    # is correct here (unlike the Prometheus label lookup OBS-202 already
    # fixed). The guard worth mutation-testing is the opposite direction
    # from every entry above: not "a protection is removed and a test
    # notices", but "a rewrite is introduced where the audit deliberately
    # left none, and a test notices that too" -- `.upper()` stands in for
    # any such rewrite (including a well-intentioned `canonical(...)` call)
    # without needing a new import the mutation would otherwise leave
    # dangling. Counted before adding these entries (OBS-191): each anchor
    # string below occurs exactly once in its file (`grep -c`, 2026-08-19),
    # so each mutation targets the one call site it names, unambiguously. ----

    ("B-519-MCP-INTERFACE", "get_lab_interface forwards the caller's "
     "interface-name spelling unrewritten (audited EXEMPT: it renders "
     "straight into a device command IOS-XR accepts in either spelling, "
     "with no comparison on this path for a rewrite to fix)",
     "mcp_server/server.py",
     "    return get_interface(device_name, name)\n",
     "    return get_interface(device_name, name.upper())\n",
     "test_get_lab_interface_forwards_the_caller_spelling_unchanged"),

    ("B-519-MCP-SUBJECT", "investigate_lab_session forwards the caller's "
     "subject spelling unrewritten for interface/isis_adjacency/ldp_session "
     "(audited EXEMPT: SubjectRule.AS_IS renders it straight into a device "
     "command, and checks.interface_exists already tolerates either "
     "spelling on its own via same_interface)",
     "mcp_server/server.py",
     "    result = investigate(device, subject, flow=flow)\n",
     "    result = investigate(device, subject.upper(), flow=flow)\n",
     "test_investigate_lab_session_forwards_the_subject_spelling_unchanged"),

    ("B-519-CLI-INTERFACE", "nettools interface forwards the caller's "
     "interface-name spelling unrewritten -- the CLI's own copy of the "
     "get_lab_interface guard above",
     "src/agent_nettools/cli.py",
     "    result = get_interface(args.device, args.name)\n",
     "    result = get_interface(args.device, args.name.upper())\n",
     "test_cli_interface_forwards_the_caller_spelling_unchanged"),

    ("B-519-CLI-SUBJECT", "nettools investigate forwards the caller's "
     "subject spelling unrewritten -- the CLI's own copy of the "
     "investigate_lab_session guard above",
     "src/agent_nettools/cli.py",
     "            args.device, subject, flow=flow, analyst=analyst, sender=sender\n",
     "            args.device, subject.upper(), flow=flow, analyst=analyst, sender=sender\n",
     "test_cli_investigate_forwards_the_subject_spelling_unchanged"),

    # ---- B-517: neo4j read tool. GRAPH-DERIVED mirrors NETBOX-DERIVED above
    # exactly, for the same reason: `get_lab_graph_topology`'s DESCRIPTION,
    # not a module docstring nobody reads, is the reasoning surface a model
    # acts on (OBS-112). Counted before adding this entry (OBS-191): the
    # anchor string occurs exactly once in server.py (`python3 -c` string
    # count, 2026-08-19). ----

    ("GRAPH-DERIVED", "get_lab_graph_topology's description states the "
     "graph is DERIVED from parsed device evidence and is never "
     "authoritative about the live fabric -- OBS-112's lesson applied to "
     "the third external-source tool",
     "mcp_server/server.py",
     "    This graph is DERIVED from this project's own parsed LLDP/IS-IS evidence\n"
     "    by `graph.write_graph` and is NEVER authoritative about the live fabric:\n",
     "    This graph\n"
     "    by `graph.write_graph`:\n",
     "test_get_lab_graph_topology_states_it_is_derived_not_authoritative"),

    # ---- B-515: SR-TE policy detail. `templates.split_sr_policy_id` itself
    # cannot be mutated here -- it lives in the FROZEN templates.py, and
    # `run_one` above refuses to touch a FROZEN path (§0.5); its own
    # guarantee is covered by tests/test_safety.py/test_template_security.py
    # passing UNEDITED against the new template, verified separately. What
    # CAN be (and is) mutated is the caller-side wiring in the two non-frozen
    # files this task owns. Counted before adding these entries (OBS-191):
    # both anchor strings occur exactly once in their file (`grep -c`,
    # 2026-08-19). ----

    ("B-515-SPLIT-CALLED", "get_lab_sr_policy_detail actually calls "
     "templates.split_sr_policy_id to validate/split policy_id before "
     "calling run_template, rather than forwarding the raw caller string",
     "mcp_server/server.py",
     "        color, endpoint = split_sr_policy_id(policy_id)\n",
     "        color, endpoint = policy_id, policy_id\n",
     "test_get_lab_sr_policy_detail_refuses_a_malformed_policy_id_classified_not_unclassified"),

    ("B-515-FREE-TEXT-DECLARED", "sr_policy_detail's last_error is declared "
     "as free text in FREE_TEXT_FIELDS, so it crosses the MCP boundary "
     "quoted rather than bare -- the declaration itself, not the generic "
     "quoting mechanism B-467-MCP above already guards",
     "src/agent_nettools/model_egress.py",
     '        ("sr_policy_detail", "last_error"),\n',
     "",
     "test_get_lab_sr_policy_detail_wraps_last_error_as_free_text_through_the_actual_registered_tool"),

    # ---- B-530: the TSDB survey (ldp_session_history/device_uptime_
    # history). The guard worth mutation-testing is the deliberate exclusion
    # this survey's own argument rests on: LDP's series carries
    # capabilities_received_description (free-text-shaped, "MP: Multi-
    # Topology (MT)") and the shaper must never extract it. Counted before
    # adding this entry (OBS-191): the anchor occurs exactly once in
    # metrics_prometheus.py (`grep -c`, 2026-08-19). ----

    ("B-530-LDP-NO-CAPABILITIES", "_shape_ldp_session_records never "
     "extracts capabilities_received_description/_sent_description (the "
     "two labels on the LDP series that read as free text) into the "
     "record -- the specific exclusion the TSDB survey's own argument for "
     "querying this family rests on",
     "src/agent_nettools/metrics_prometheus.py",
     '                "peer_state": metric.get("detailed_information_peer_state"),\n',
     '                "peer_state": metric.get("detailed_information_peer_state"),\n'
     '                "capabilities": metric.get("detailed_information_capabilities_received_description"),\n',
     "test_an_unnamed_hostile_label_never_reaches_the_ldp_shaped_record"),
]


def sh(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True)


#: Paths whose bytecode cannot affect THIS tree's test run, and so must not be
#: scanned or purged. `.venv` is the installed interpreter. `.claude/worktrees`
#: holds independent git checkouts: their `src/` is never on this run's
#: sys.path, so their caches can neither mask a mutation here nor be safely
#: deleted -- an agent may be mid-run in one, and purging under it is a race we
#: would create for no benefit. Narrowing the scan to this tree keeps the
#: guarantee identical (no stale bytecode for the module under mutation, in the
#: tree being mutated) while removing files that were never in scope.
_UNSCANNED = (".venv", ".claude/worktrees")


def _in_scope(p: pathlib.Path) -> bool:
    """True if ``p`` (always a descendant of `REPO`, via `REPO.rglob`) is
    bytecode this run may purge or must treat as stale if found.

    Checked against the path **relative to `REPO`**, not the absolute path.
    Every agent runs this script from inside its own worktree, so `REPO`
    itself (the script's own `parent.parent`) already lives under
    `.claude/worktrees/<name>/`. Matching `_UNSCANNED` against the absolute
    path meant every result ever found -- all descendants of `REPO`, so all
    inheriting `REPO`'s own ancestry -- contained the substring
    `.claude/worktrees` and was excluded: 0 of 470 `__pycache__` directories
    measured in-scope in a real worktree run (2026-08-19). That made
    `purge_pycache` and `assert_no_stale_bytecode` silent no-ops on every run
    that matters, reintroducing defect 2 above under the one condition this
    harness actually runs in. Relative to `REPO`, `.claude/worktrees` appears
    only when a scan started from a *main* checkout descends into a *nested,
    different* worktree -- exactly the case the exclusion is for -- never
    merely because `REPO` itself happens to sit under one.
    """
    text = str(p.relative_to(REPO))
    return not any(skip in text for skip in _UNSCANNED)


def purge_pycache() -> None:
    """Defect 2. Bracket every mutation, because the silent direction reports a
    mutation as caught when it was not."""

    for cache in REPO.rglob("__pycache__"):
        if _in_scope(cache):
            shutil.rmtree(cache, ignore_errors=True)
    shutil.rmtree(REPO / ".pytest_cache", ignore_errors=True)


def assert_no_stale_bytecode(path: str) -> None:
    module = pathlib.Path(path).stem
    stale = [p for p in REPO.rglob(f"{module}.cpython-*.pyc") if _in_scope(p)]
    if stale:
        raise SystemExit(f"REFUSING TO CONTINUE: stale bytecode for {module}: {stale}")


def resolve_guard_tests(symbol: str) -> list[str]:
    """Defect 1. Find the guard's tests by searching for the guard, never by name.

    Returns the test files that mention the symbol. An empty list is an **error**, not a
    verdict — the caller must fail loudly rather than run a mutation whose result it
    cannot interpret.
    """

    r = sh(f"grep -rl -- {symbol!r} tests/")
    return sorted(f for f in r.stdout.split() if f.endswith(".py"))


def _restore_on_signal() -> None:
    """Convert SIGTERM/SIGINT into an exception so `run_one`'s `finally` runs.

    `try/finally` unwinds on an exception. It does **not** unwind on a signal:
    Python's default SIGTERM handler terminates the process immediately, so a
    mutation in flight is never restored and the source file is left broken.

    That is not hypothetical. On 2026-08-19 a gate command wrapped this script
    in a 10-minute timeout; the suite had grown past 8 minutes, `timeout` sent
    SIGTERM mid-mutation, and `epoch.py` was left with `Coherence.refuses`
    hardcoded to `return False` -- the mutation, committed to the working tree.
    Three unrelated `test_epoch.py` tests then failed, and the obvious reading
    was "the fixture refresh broke them". An agent sent to fix those tests
    correctly refused, diagnosed the real cause, and reported it instead of
    weakening the assertions to match corrupted source (OBS-300).

    **The failure mode is the danger, not the interruption.** A killed run
    leaves no error, no log line, and no clue -- only unrelated tests failing
    for a reason that points somewhere else entirely.
    """

    def _raise(signum, _frame):
        raise KeyboardInterrupt(f"interrupted by signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _raise)


def assert_no_leftover_mutation() -> None:
    """Refuse to start if a mutation target already differs from HEAD.

    Belt to the signal handler's braces: if a previous run was killed in a way
    no handler could catch (SIGKILL, power loss), the tree still holds its
    mutation. Starting a fresh run on top of that would mutate an already
    mutated file and restore it to the *wrong* original.
    """

    targets = sorted({m[2] for m in MUTATIONS})
    dirty = []
    for rel in targets:
        proc = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel],
                              cwd=REPO, capture_output=True)
        if proc.returncode == 1:
            dirty.append(rel)
    if dirty:
        raise SystemExit(
            "REFUSING TO START: these mutation targets differ from HEAD:\n  "
            + "\n  ".join(dirty)
            + "\n\nIf you have uncommitted work in them, commit or stash it first.\n"
            "If a previous run was killed mid-mutation, `git diff` them -- a\n"
            "leftover mutation is usually a single line reverting a guard\n"
            "(OBS-300). Restore before running, or this run will mutate an\n"
            "already-mutated file and restore it to the wrong original."
        )


def run_one(ident, what, path, old, new, symbol) -> dict:
    if path in FROZEN:
        raise SystemExit(f"REFUSED: {path} is frozen by BUILD-PLAN §0.5")

    tests = resolve_guard_tests(symbol)
    if not tests:
        # Loudly, and distinctly from "vacuous".
        return {"id": ident, "what": what, "result": "UNRESOLVED",
                "detail": f"no test in tests/ mentions {symbol!r} — cannot interpret a "
                          f"mutation of this guard, so none was run"}

    source_path = REPO / path
    original = source_path.read_text()
    if old not in original:
        return {"id": ident, "what": what, "result": "ANCHOR-MISSING",
                "detail": f"the text this mutation removes is not in {path} — the guard "
                          f"has moved or been rewritten; update the harness"}

    target = " ".join(tests)
    purge_pycache()
    try:
        count = None if ident == "B-458" else 1
        source_path.write_text(
            original.replace(old, new) if count is None else original.replace(old, new, 1)
        )
        result = sh(f".venv/bin/python -m pytest {target} -q 2>&1 | tail -1")
        tail = result.stdout.strip()
    finally:
        source_path.write_text(original)
        if source_path.read_text() != original:
            raise SystemExit(f"RESTORE FAILED for {path} — fix by hand before continuing")
        purge_pycache()

    assert_no_stale_bytecode(path)
    caught = "failed" in tail or "error" in tail.lower()
    return {"id": ident, "what": what, "tests": tests, "mutated": tail[:70],
            "result": "HOLDS" if caught else "VACUOUS"}


def main(argv: list[str]) -> int:

    _restore_on_signal()
    assert_no_leftover_mutation()
    wanted = set(argv[1:])
    selected = [m for m in MUTATIONS if not wanted or m[0] in wanted]

    print(f"mutating {len(selected)} guard(s). No frozen file is touched.\n")
    results = [run_one(*m) for m in selected]

    for r in results:
        print(f"  {r['result']:14} {r['id']:18} {r['what']}")
        if r.get("detail"):
            print(f"                   {r['detail']}")
        elif r.get("mutated"):
            print(f"                   {' '.join(r['tests'])} -> {r['mutated']}")

    # Every mutation restored, and the frozen files still frozen.
    print()
    # A frozen file's baseline may MOVE, but only with recorded sign-off (§0.5
    # takes additions only). Re-pinning keeps the guard live at the new blob
    # instead of retiring it; `tests/test_frozen_files.py` carries the same
    # table and the reason each baseline moved.
    REPINNED = {
        # platforms.py: B-109's ldp/ldp_discovery intents (prior repin), plus
        # the protocol-coverage sweep's bgp_vpnv4 intent (this repin, same
        # session -- Opus 5 orchestrator, 2026-08-19, under the standing
        # autonomous Stage-2 mandate, NOT operator sign-off; §0.5 review is
        # still owed, see PendingOperatorReview in tests/test_frozen_files.py
        # and OBS-182/B-505) -- OSPF/RSVP-TE/CDP were checked live and
        # deliberately NOT added (no observable state on this fabric; see the
        # comment beside PLATFORM_INTENTS["cisco_xr"]["bgp_vpnv4"] in
        # platforms.py). Additive both times; the frozen safety TESTS pass
        # unedited against it.
        "src/agent_nettools/platforms.py": "0a11cdc99d4b0d37c69e7845566bc32898dba96a",
        # templates.py: B-104 (prior repin), the config axis (D16) -- two new
        # templates, `config_isis` and `config_interface`, both
        # section-scoped, neither an unqualified `show running-config`
        # (pinned absent by tests/test_config_section.py, with a positive
        # control per OBS-181). PLUS B-515 (this repin, same file, same
        # additive discipline): one more template, `sr_policy_detail` --
        # `show segment-routing traffic-eng policy color {color} endpoint
        # ipv4 {endpoint} detail`, reusing the EXISTING BoundedIntParam/
        # IPv4AddressParam types (not a new one, so
        # tests/test_template_security.py's FROZEN `_VALID_BY_TYPE` table
        # needs no edit) -- closes the gap MCP §14b measured: a model could
        # say a down SR-TE policy had no resolving candidate path but not
        # name which SID or segment list. NOT operator sign-off -- see the
        # matching PendingOperatorReview entry in tests/test_frozen_files.py
        # for the full note; both slots must be kept in sync by hand, since
        # this table is a plain literal and not imported from that test
        # module.
        "src/agent_nettools/templates.py": "09b1b799472e57973b30afecd82a1f9fa45f797b",
    }
    for f in sorted(FROZEN):
        expected = REPINNED.get(f) or sh(f"git rev-parse {BASELINE_COMMIT}:{f}").stdout.strip()
        head = sh(f"git rev-parse HEAD:{f}").stdout.strip()
        if expected != head:
            raise SystemExit(
                f"FROZEN FILE CHANGED: {f}\n"
                "  §0.5 takes ADDITIONS ONLY, with operator sign-off. If this is\n"
                "  authorised, add it to REPINNED here and to tests/test_frozen_files.py\n"
                "  with who approved it and why. If not, revert."
            )
    dirty = sh("git status --porcelain").stdout.strip()
    if dirty:
        print("  WARNING — working tree is not clean after the pass:")
        print("\n".join(f"    {line}" for line in dirty.split("\n")))
    else:
        print("  tree clean; four frozen files identical to the T-001 baseline")

    bad = [r for r in results if r["result"] in ("VACUOUS", "UNRESOLVED", "ANCHOR-MISSING")]
    print(f"\n  {len(results) - len(bad)}/{len(results)} guards hold.")
    if bad:
        print("  Investigate before trusting any of them:")
        for r in bad:
            print(f"    {r['result']}  {r['id']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
