import facts
from svgkit import *

# ---------------- measured facts (see facts.py) ----------------
SL = lambda name: facts.module_lines(f"src/agent_nettools/{name}")  # noqa: E731
ML = lambda name: facts.module_lines(f"mcp_server/{name}")  # noqa: E731

cli_subcommand_count = len(facts.cli_subcommands())
classic_tool_count = len(facts.classic_mcp_tools())
staged_tool_count = len(facts.staged_mcp_tools())
audit_rule_count = facts.audit_rule_count()
flows = facts.flows_summary()
declared_flow_count = len(flows["declared"])
implemented_flow_count = len(flows["implemented"])
cmd_counts = facts.approved_command_counts()
verb_allowlist = facts.verb_allowlist_display()
guard_n = facts.guard_count()
lab_devices = facts.lab_devices()
facts.assert_contains("src/agent_nettools/network_tools.py", r"def _netmiko_send_commands",
                       "d1.py's TRANSPORT box code pointer")
config_axis = facts.config_axis_summary()
config_diff_consumers = facts.config_diff_consumers()
# Was a one-time SystemExit tripwire ("has a consumer now — go update the
# pill label"); retired at release-1.0 (W5, `--reconcile-config` gave
# config_diff.py its first consumer). The label below now reads
# `config_diff_consumers` itself, so it can never go stale silently again —
# a permanent fix, not a permanent guard.
config_diff_label = (
    f"opt-in via --reconcile-config ({', '.join(config_diff_consumers)})"
    if config_diff_consumers else "unwired"
)
ROLE_LABEL = {"core": "core", "edge": "edge", "route-reflector": "reflector"}

W, H = 1580, 1210
s = Svg(W, H)
header(s, "ios-xr-nettools — repo anatomy",
       f"{facts.fmt(facts.src_lines())} lines of source + {facts.fmt(facts.mcp_lines())} in the MCP server · "
       f"{facts.fmt(facts.tests_lines())} lines of tests · "
       f"read-only, single lab, {len(lab_devices)} Cisco XRd nodes",
       facts.header_tag())

SPINE_X, SPINE_W = 48, 1010
RAIL_X, RAIL_W = 1090, W - 1090 - 48

def layout(pills, maxw, pad=16, gap=8, size=12.5):
    """Wrap pills into rows that fit maxw. pills: (label, sub, over)."""
    rows, cur, curw = [], [], 0
    for p in pills:
        label, sub = p[0], p[1]
        wl = w_mono(label, size)
        ws = (w_sans(sub, size - 2) + 8) if sub else 0
        pw = wl + ws + 22
        if cur and curw + pw + gap > maxw:
            rows.append(cur); cur, curw = [], 0
        cur.append(p); curw += pw + gap
    if cur: rows.append(cur)
    return rows

LAYERS = [
    dict(t="ENTRY POINTS", n="1", c=SLATEBG, sc=LINE, tc=INK,
         d="how a question arrives — five front doors, every one read-only",
         p=[("cli.py", f"{facts.fmt(SL('cli.py'))} · {cli_subcommand_count} subcommands"),
            ("mcp_server/server.py", f"{facts.fmt(ML('server.py'))} · {classic_tool_count} tools (classic)"),
            ("staged_surface.py", f"{facts.fmt(ML('staged_surface.py'))} · {staged_tool_count} tools (opt-in)"),
            ("event_routing.py", f"{facts.fmt(SL('event_routing.py'))} · syslog/Alertmanager → flow"),
            ("notifier.py", f"{facts.fmt(SL('notifier.py'))} · Telegram, egress-bounded")]),
    dict(t="ANSWER LAYER", n="2", c="#ffffff", sc=LINE, tc=INK,
         d="composes a diagnosis out of already-parsed facts — never out of device text",
         p=[("investigation.py", f"{facts.fmt(SL('investigation.py'))} · drives the descent"),
            ("checks.py", f"{facts.fmt(SL('checks.py'))} · per-role health rules"),
            ("health.py", f"{facts.fmt(SL('health.py'))} · fabric verdicts, silences"),
            ("audit.py", f"{facts.fmt(SL('audit.py'))} · {audit_rule_count} fabric-vs-itself rules"),
            ("fabric_analysis.py", facts.fmt(SL('fabric_analysis.py'))),
            ("knowledge.py", f"{facts.fmt(SL('knowledge.py'))} · grep, not RAG"),
            ("output.py", f"{facts.fmt(SL('output.py'))} · json | table | summary")]),
    dict(t="OPERATIONS", n="3", c=TEALBG, sc="#a7d6d0", tc=TEAL,
         d="closes the loop around an investigation — none of it a dependency descent (diagram 8)",
         p=[("event_watch.py", f"{facts.fmt(SL('event_watch.py'))} · polls Loki, groups by root cause"),
            ("admission.py", f"{facts.fmt(SL('admission.py'))} · per-device/fabric concurrency gate"),
            ("ticket.py", f"{facts.fmt(SL('ticket.py'))} · append-only flight recorder"),
            ("ownership.py", f"{facts.fmt(SL('ownership.py'))} · declared who-gets-told table"),
            ("incident_correlation.py", f"{facts.fmt(SL('incident_correlation.py'))} · 3 bases, strongest first")]),
    dict(t="DETERMINISTIC CORE", n="4", c=BLUEBG, sc="#b9d0f4", tc=BLUE,
         d="NO MODEL CALL, EVER — if this layer ever needs one, something above it was designed wrong",
         p=[("descent.py", f"{facts.fmt(SL('descent.py'))} · lowest broken rung wins"),
            ("flows.py", f"{facts.fmt(SL('flows.py'))} · {declared_flow_count} declared / {implemented_flow_count} implemented"),
            ("epoch.py", f"{facts.fmt(SL('epoch.py'))} · one observation window"),
            ("grounding.py", f"{facts.fmt(SL('grounding.py'))} · citation + chain + containment"),
            ("reasoning_gate.py", f"{facts.fmt(SL('reasoning_gate.py'))} · built, not yet wired (diagram 9)"),
            ("config_section.py", f"{facts.fmt(SL('config_section.py'))} · {len(config_axis['templates'])} templates, configured intent"),
            ("config_diff.py", f"{facts.fmt(SL('config_diff.py'))} · {len(config_axis['diff_functions'])} fields, {len(config_axis['outcomes'])} outcomes, {config_diff_label}"),
            ("topology.py", facts.fmt(SL('topology.py'))), ("interface_kind.py", facts.fmt(SL('interface_kind.py')))]),
    dict(t="EVIDENCE & PARSING", n="5", c="#ffffff", sc=LINE, tc=INK,
         d="the only code in the repository that reads raw device text",
         p=[("network_tools.py", f"{facts.fmt(SL('network_tools.py'))} · the one transport chokepoint"),
            ("parsers.py", facts.fmt(SL('parsers.py'))), ("template_parsers.py", facts.fmt(SL('template_parsers.py'))),
            ("evidence_store.py", f"{facts.fmt(SL('evidence_store.py'))} · json | sqlite"),
            ("log_window.py", facts.fmt(SL('log_window.py'))),
            ("fixtures.py", f"{facts.fmt(SL('fixtures.py'))} · {facts.fmt(facts.fixture_stats()['captures'])} captures, replay offline")]),
    dict(t="SAFETY BOUNDARY", n="6", c=REDBG, sc="#f0bfb8", tc=RED,
         d="checked BEFORE credentials load and BEFORE a socket opens — both files frozen since the pre-build baseline",
         p=[("platforms.py", f"{facts.fmt(SL('platforms.py'))} · FROZEN · exact-match frozenset", dict(stroke="#e0a79e")),
            ("templates.py", f"{facts.fmt(SL('templates.py'))} · FROZEN · canonicalise by reconstruction", dict(stroke="#e0a79e"))],
         foot=f"cisco_xr {cmd_counts['cisco_xr']} · cisco_iosxe {cmd_counts['cisco_iosxe']} · "
              f"juniper_junos {cmd_counts['juniper_junos']} approved commands.  VERB_ALLOWLIST = {verb_allowlist}."
              "  There is no run_command(), no config mode, no shell."),
]

y = 126
_layer_mid_y = {}  # layer title -> vertical centre, for the model-branch arrows below
for L in LAYERS:
    rows = layout(L["p"], SPINE_W - 34)
    bh = 46 + len(rows) * 34 + (22 if L.get("foot") else 0)
    s.rect(SPINE_X, y, SPINE_W, bh, fill=L["c"], stroke=L["sc"], rx=12)
    s.badge(SPINE_X + 26, y + 25, L["n"], col=L["tc"] if L["tc"] != INK else "#55555a")
    s.text(SPINE_X + 46, y + 24, L["t"], size=13.5, weight="700", fill=L["tc"], ls="0.9")
    s.text(SPINE_X + 46 + w_sans(L["t"], 13.5) * 1.16 + len(L["t"]) * 0.9 + 20, y + 24, L["d"], size=11.8, fill=MUTED)
    ry = y + 38
    for r in rows:
        s.pillrow(SPINE_X + 17, ry, r, fill="#fff", stroke=L["sc"] if L["c"] != "#ffffff" else LINE)
        ry += 34
    if L.get("foot"):
        s.text(SPINE_X + 20, ry + 8, L["foot"], size=11.5, fill=RED)
    nexty = y + bh
    _layer_mid_y[L["t"]] = (y + nexty) / 2
    if L is not LAYERS[-1]:
        s.line(SPINE_X + SPINE_W / 2, nexty + 2, SPINE_X + SPINE_W / 2, nexty + 16,
               stroke="#bcbcb6", sw=1.6, marker="arw")
    y = nexty + 20

# transport + devices
s.rect(SPINE_X, y, SPINE_W, 44, fill="#ffffff", stroke=LINE, rx=12)
s.badge(SPINE_X + 26, y + 22, "7", col="#55555a")
s.text(SPINE_X + 46, y + 21, "TRANSPORT", size=13.5, weight="700", ls="0.9")
s.text(SPINE_X + 46 + w_sans("TRANSPORT", 13.5) * 1.16 + 9 * 0.9 + 20, y + 21,
       "netmiko over SSH · one login per device per observation window, not one per question", size=11.8, fill=MUTED)
# `_netmiko_send_commands` is a code pointer, not a count -- left literal.
s.text(SPINE_X + SPINE_W - 18, y + 21, "_netmiko_send_commands()  ← the single exit", size=11.5,
       fill=MUTED, anchor="end", family=MONO)
y += 44
s.line(SPINE_X + SPINE_W / 2, y + 2, SPINE_X + SPINE_W / 2, y + 16, stroke="#bcbcb6", sw=1.6, marker="arw")
y += 20

s.rect(SPINE_X, y, SPINE_W, 62, fill=GREENBG, stroke="#b9dcc5", rx=12)
s.text(SPINE_X + 20, y + 24, "THE LAB", size=13.5, weight="700", fill=GREEN, ls="0.9")
# "containerlab", the /24, and "IS-IS + SR + iBGP over a route reflector" are
# architecture description, not tree-derived counts -- the device count is.
s.text(SPINE_X + 20 + w_sans("THE LAB", 13.5) * 1.16 + 7 * 0.9 + 20, y + 24,
       f"containerlab · {len(lab_devices)} Cisco IOS-XR (XRd) nodes on 172.20.250.0/24 · IS-IS + SR + iBGP over a route reflector",
       size=11.8, fill=MUTED)
dx = SPINE_X + 20
for d, role in lab_devices:
    dx += s.pill(dx, y + 32, d, ROLE_LABEL.get(role, role), fill="#fff", stroke="#b9dcc5", h=22, size=11.5,
                 tcol=GREEN, subcol=MUTED) + 7

# ---------------- right rail ----------------
ry = 126
s.rect(RAIL_X, ry, RAIL_W, 300, fill=PURPBG, stroke="#d5c6f5", rx=12)
s.text(RAIL_X + 20, ry + 26, "THE MODEL SIDE", size=13.5, weight="700", fill=PURPLE, ls="0.9")
s.text(RAIL_X + 20, ry + 46, "A branch off layers 2 and 5 — never in the answer path.",
       size=11.5, fill=MUTED)
s.text(RAIL_X + 20, ry + 63, "No model call happens unless you ask for one.", size=11.5,
       fill=PURPLE, weight="600")
s.rect(RAIL_X + 18, ry + 76, RAIL_W - 36, 48, fill="#fff", stroke="#d5c6f5", rx=8)
s.text(RAIL_X + 30, ry + 96, "model_egress.py", size=12.5, family=MONO, weight="600", fill=PURPLE)
s.text(RAIL_X + 30 + w_mono("model_egress.py", 12.5) + 10, ry + 96, f"{facts.fmt(SL('model_egress.py'))} · the projector", size=11, fill=MUTED)
s.text(RAIL_X + 30, ry + 114, "raw text withheld-with-count · free text quoted", size=10.8, fill=MUTED)
ly = ry + 136
for lbl, name, sub in [("prompt_library.py", "prompt_library.py", "builds every prompt"),
                 ("evidence_budget.py", "evidence_budget.py", "fits the window"),
                 ("llm_analysis.py", "llm_analysis.py", "analyze / correlate"),
                 ("agent_loop.py", "agent_loop.py", "bounded tool loop"),
                 ("mcp_server/boundary.py", "boundary.py", "sanitise-on-register")]:
    s.text(RAIL_X + 30, ly, "•", size=12, fill=PURPLE)
    s.text(RAIL_X + 42, ly, lbl, size=11.5, family=MONO, fill=INK)
    line_n = SL(name) if lbl != "mcp_server/boundary.py" else ML(name)
    s.text(RAIL_X + 42 + w_mono(lbl, 11.5) + 8, ly, f"{facts.fmt(line_n)} · {sub}", size=10.8, fill=MUTED)
    ly += 19
s.text(RAIL_X + 20, ry + 250, "Providers: Anthropic · OpenAI · MiniMax · any", size=11.2, fill=MUTED)
s.text(RAIL_X + 20, ry + 266, "OpenAI-compatible local server (LM Studio).", size=11.2, fill=MUTED)
s.text(RAIL_X + 20, ry + 228, "Triggered only by:  --paraphrase · analyze · agent · an MCP client", size=10.9, fill=PURPLE)
s.text(RAIL_X + 20, ry + 286, "→ full detail in diagram 3", size=11.2, fill=PURPLE, weight="600")

ry += 320
s.rect(RAIL_X, ry, RAIL_W, 366, fill="#ffffff", stroke=LINE, rx=12)
s.text(RAIL_X + 20, ry + 26, "THE FOUR INVARIANTS", size=13.5, weight="700", ls="0.9")
s.text(RAIL_X + 20, ry + 45, "Everything else is a feature. These are the build.", size=11.3, fill=MUTED)
iy = ry + 66
INV = [("1", "Platform resolves credential-free.",
        "lab.platform_for() reads static data only, so the\nallowlist check can happen before any secret loads.", "6"),
       ("2", "The allowlist is checked before credentials.",
        "Ordering is load-bearing: a bad command never\nreaches the network, and never costs a login.", "6"),
       ("3", "No command is built by interpolation.",
        "A prefix/address/name is parsed into a typed object\nand the command re-rendered from its canonical form.", "6"),
       ("4", "No unparsed device text reaches a model.",
        "Structural, not filtered: prompt_library never holds\nthe text. Five egress paths, all projected.", "2/5")]
for n, title, body, layer in INV:
    s.badge(RAIL_X + 32, iy + 4, n, col=RED if n != "4" else PURPLE, r=10)
    s.text(RAIL_X + 50, iy + 8, title, size=11.8, weight="650")
    for i, ln in enumerate(body.split("\n")):
        s.text(RAIL_X + 50, iy + 25 + i * 15, ln, size=10.9, fill=MUTED)
    s.text(RAIL_X + RAIL_W - 20, iy + 8, f"layer {layer}", size=10, fill=FAINT, anchor="end", family=MONO)
    iy += 68
s.text(RAIL_X + 20, ry + 348,
       f"Each is pinned by a test that fails when the guard is removed ({guard_n}/{guard_n} verified).",
       size=10.8, fill=GREEN)

# branch arrows from the spine to the model rail — from the actual vertical
# centre of ANSWER LAYER and EVIDENCE & PARSING, wherever the spine's own
# layer count put them this run, not a pixel offset frozen from an older
# layer count.
s.path(f"M {SPINE_X + SPINE_W + 2} {_layer_mid_y['ANSWER LAYER']:.1f} H {RAIL_X - 8}",
       stroke=PURPLE, sw=1.5, dash="4 4", marker="arwp")
s.path(f"M {SPINE_X + SPINE_W + 2} {_layer_mid_y['EVIDENCE & PARSING']:.1f} H {RAIL_X - 8}",
       stroke=PURPLE, sw=1.5, dash="4 4", marker="arwp")

s.text(48, H - 26,
       "Read down the spine: a question enters at the top, becomes an answer in layer 4, and only layers 5–7 ever touch a device. "
       "The model branch hangs off the side — it restates conclusions, it never reaches them.",
       size=12, fill=MUTED)
s.save("01-repo-anatomy.svg")
print("ok")
