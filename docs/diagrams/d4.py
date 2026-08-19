import facts
from svgkit import *

cli_subcommands = facts.cli_subcommands()
classic_tools = facts.classic_mcp_tools()
staged_tools = facts.staged_mcp_tools()
probe_tools = set(facts.classic_mcp_probe_tools())
external_tools = set(facts.classic_mcp_external_source_tools())
intents = facts.cisco_xr_intents()
lab_devices = facts.lab_devices()
templates = facts.cisco_xr_templates()
flows = facts.flows_summary()
settings_n = facts.settings_count()

W, H = 1580, 1000
s = Svg(W, H)
header(s, "What it can actually do",
       f"{len(cli_subcommands)} CLI subcommands · {len(classic_tools)} MCP tools on the classic surface, "
       f"{len(staged_tools)} on the staged one · {len(intents)} collection intents · "
       f"{len(flows['implemented'])} investigation flows of {len(flows['declared'])} declared",
       "every one of them read-only")

# legend
lx = 48
for label, col, bg in [("works offline (fixtures or local state)", GREEN, GREENBG),
                       ("needs a reachable device", "#55555a", "#ffffff"),
                       ("calls a model — opt-in, never default", PURPLE, PURPBG),
                       ("generates traffic (gated)", AMBER, AMBERBG),
                       ("reaches Loki/Prometheus/NetBox/neo4j, not a device (gated)", TEAL, TEALBG)]:
    s.rect(lx, 116, 13, 13, fill=bg, stroke=col, rx=3)
    s.text(lx + 20, 127, label, size=11.2, fill=MUTED)
    lx += w_sans(label, 11.2) + 46

LX, LW = 48, 1000
RX, RW = 1072, W - 1072 - 48

def group(x, y, w, title, note, rows, maxw):
    """rows: list of (label, sub, kind)"""
    KIND = {"off": (GREEN, GREENBG), "live": ("#9a9a94", "#ffffff"),
            "model": (PURPLE, PURPBG), "probe": (AMBER, AMBERBG)}
    laid, cur, curw = [], [], 0
    for r in rows:
        pw = w_mono(r[0], 12) + (w_sans(r[1], 10) + 8 if r[1] else 0) + 22
        if cur and curw + pw + 8 > maxw:
            laid.append(cur); cur, curw = [], 0
        cur.append(r); curw += pw + 8
    if cur: laid.append(cur)
    h = 44 + len(laid) * 32
    s.rect(x, y, w, h, fill="#fdfdfc", stroke=LINE, rx=11)
    s.text(x + 18, y + 26, title, size=12.5, weight="700", ls="0.7")
    s.text(x + 18 + w_sans(title, 12.5) * 1.16 + len(title) * 0.95 + 26, y + 26, note, size=11, fill=MUTED)
    yy = y + 38
    for row in laid:
        cx = x + 16
        for label, sub, kind in row:
            col, bg = KIND[kind]
            cx += s.pill(cx, yy, label, sub, fill=bg, stroke=col, size=12,
                         tcol=INK if kind == "live" else col, h=24) + 8
        yy += 32
    return h

# The 6 base intents keep their plain "live" pill; the 3 added since (B-109's
# ldp/ldp_discovery, the bgp_vpnv4 context intent) render identically — the
# row wraps automatically as the intent count grows, per `group()` above.
y = 148
intent_rows = [(name, None, "live") for name in intents]
y += group(LX, y, LW, "ASK ONE DEVICE A QUESTION", f"the {len(intents)} collection intents — one login answers all {len(intents)}",
    intent_rows + [
    ("fabric", f"one check, all {len(lab_devices)} devices", "live"), ("inventory", "credential-free", "off"),
], LW - 34) + 14

_TEMPLATE_DISPLAY = {"route": "route", "bgp_neighbor": "bgp-neighbor", "interface": "interface",
                     "logging": "logging", "ping": "ping", "traceroute": "traceroute",
                     "config_isis": "config_isis", "config_interface": "config_interface",
                     "sr_policy_detail": "sr-policy"}
_TEMPLATE_ARGS = {"route": "DEVICE PREFIX", "bgp_neighbor": "DEVICE ADDRESS", "interface": "DEVICE NAME",
                  "logging": "DEVICE --count N", "ping": "sends ICMP", "traceroute": "sends UDP",
                  "sr_policy_detail": "DEVICE COLOR:ENDPOINT — B-515"}
# config_isis/config_interface (B-104) are real templates -- they parse, and
# tests exercise them -- but neither has its own CLI subcommand or MCP tool
# (checked against cli.build_parser() and mcp_server.server directly; see
# facts.config_diff_consumers()'s sibling check for config_diff.py's own,
# stricter unwired claim). The only registered caller that can select a
# template BY NAME at all is run_lab_template's enum, inside `nettools
# agent` -- so today these are reachable through a model's tool choice, or
# not at all, never through a human typing a dedicated subcommand.
_CLI_ONLY_TEMPLATES = {n for n in templates["lookups"] if _TEMPLATE_DISPLAY[n] in cli_subcommands}
_AGENT_ONLY_TEMPLATES = [n for n in templates["lookups"] if n not in _CLI_ONLY_TEMPLATES]
lookup_rows = [(_TEMPLATE_DISPLAY[n], _TEMPLATE_ARGS[n], "live") for n in templates["lookups"] if n in _CLI_ONLY_TEMPLATES]
probe_rows = [(_TEMPLATE_DISPLAY[n], _TEMPLATE_ARGS[n], "probe") for n in templates["probes"]]
agent_only_rows = [(_TEMPLATE_DISPLAY[n], "via nettools agent's tool loop only — no subcommand", "model") for n in _AGENT_ONLY_TEMPLATES]
y += group(LX, y, LW, "LOOK UP ONE OBJECT", "validated parameterised templates — the argument is re-rendered, never interpolated",
    lookup_rows + probe_rows + agent_only_rows, LW - 34) + 14

y += group(LX, y, LW, "DIAGNOSE", "the part that answers 'why', not just 'what'", [
    ("investigate", "the deterministic descent", "off"), ("health", "role-aware verdicts", "off"),
    ("audit", f"{facts.audit_rule_count()} fabric-vs-itself rules", "off"), ("learn-topology", "derive expected state", "off"),
    ("diff", "vs latest or golden", "live"), ("flaps", "oscillation across history", "off"),
    ("ledger", "diagnosis accuracy, human-recorded", "off"),
], LW - 34) + 14

y += group(LX, y, LW, "EVIDENCE OVER TIME", "snapshots are the memory this tool reasons against", [
    ("capture", "--all --label t0", "live"), ("baseline pin | show", "the golden snapshot", "off"),
    ("evidence prune | history", "retention", "off"), ("metrics", "json | prometheus", "off"),
], LW - 34) + 14

y += group(LX, y, LW, "MODEL-ASSISTED — OPT-IN, NEVER THE DEFAULT", "these are the only four ways a model gets invoked at all", [
    ("analyze", "one device or --fabric", "model"), ("agent", "bounded tool-calling loop", "model"),
    ("demo", "narrated walkthrough", "model"), ("investigate --paraphrase", "restates the finding", "model"),
], LW - 34) + 14

y += group(LX, y, LW, "OPS PLUMBING", "how it plugs into the rest of a NOC", [
    ("route-event", "syslog/Alertmanager → flow", "off"), ("--notify", "Telegram, egress-bounded", "off"),
    ("config show | check", f"{settings_n} declared settings", "off"), ("inspect", "smoke-test the MCP surface", "off"),
    ("version", None, "off"),
], LW - 34)

# ---------------- right rail: MCP ----------------
ry = 148
n_rows = -(-len(classic_tools) // 2)  # ceil div — the list wraps at 2 columns regardless of count
_mcp_box_h = 88 + n_rows * 15 + 14 + 22 + 10 + len(staged_tools) * 17 + 30 + 16
s.rect(RX, ry, RW, _mcp_box_h, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 18, ry + 26, "THE MCP SURFACES", size=12.5, weight="700", ls="0.8")
s.text(RX + 18, ry + 45, "Two, deliberately — selected by NETTOOLS_MCP_SURFACE,", size=10.9, fill=MUTED)
s.text(RX + 18, ry + 60, "because collapsing to one would destroy the A/B that", size=10.9, fill=MUTED)
s.text(RX + 18, ry + 75, "measures whether a small model selects better from it.", size=10.9, fill=MUTED)

s.rect(RX + 16, ry + 88, RW - 32, 22, fill=SLATEBG, rx=6)
s.text(RX + 26, ry + 103, f"classic — {len(classic_tools)} tools  (the default)", size=11.5, weight="650", family=MONO)
ty = ry + 126
for i, t_ in enumerate(classic_tools):
    col = AMBER if t_ in probe_tools else (TEAL if t_ in external_tools else MUTED)
    s.text(RX + 26 + (0 if i % 2 == 0 else 216), ty + (i // 2) * 15, "· " + t_,
           size=10.2, family=MONO, fill=col)
ty += n_rows * 15 + 14

s.rect(RX + 16, ty, RW - 32, 22, fill=BLUEBG, rx=6)
s.text(RX + 26, ty + 15, f"staged — {len(staged_tools)} tools  (NETTOOLS_MCP_SURFACE=staged)", size=11.5, weight="650",
       family=MONO, fill=BLUE)
ty += 32
# One question per staged tool — hand-written (the point of a staged tool IS
# its question, per staged_surface.py's own docstring), keyed by name so a
# tool added or renamed there fails this lookup instead of silently
# rendering blank.
_STAGED_QUESTIONS = {
    "explore_lab": "what is here, is it broadly OK?",
    "check_lab": "what is the state of X right now?",
    "lookup_lab": "what does this device say about X?",
    "investigate_lab": "why is this broken?",
    "history_lab": "what changed on this device?",
    "probe_lab": "can it reach that, right now?",
}
for name in staged_tools:
    q = _STAGED_QUESTIONS.get(name)
    if q is None:
        raise SystemExit(f"d4.py: staged tool {name!r} has no entry in _STAGED_QUESTIONS — add one.")
    s.text(RX + 26, ty, name, size=11, family=MONO, fill=BLUE, weight="600")
    s.text(RX + 26 + w_mono(name, 11) + 10, ty, q, size=10.3, fill=MUTED)
    ty += 17
s.text(RX + 26, ty + 8, "One tool per stage of an investigation, not one per", size=10.3, fill=MUTED)
s.text(RX + 26, ty + 22, "function — mirroring how an engineer actually moves.", size=10.3, fill=MUTED)

ry += _mcp_box_h + 20
s.rect(RX, ry, RW, 214, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 18, ry + 26, "EXIT CODES — ONE SCHEME", size=12.5, weight="700", ls="0.8")
s.text(RX + 18, ry + 45, "computed from the full result before rendering, so", size=10.9, fill=MUTED)
s.text(RX + 18, ry + 60, "--format summary can never soften what json says", size=10.9, fill=MUTED)
for i, (code, col, what) in enumerate([
    ("0", GREEN, "ok / info — nothing actionable"),
    ("1", AMBER, "ran, and reports a problem: a warning\nverdict, a failed command, real drift"),
    ("2", RED, "could not run at all, or the worst outcome:\nbad inventory, critical verdict, an answer\nthat is not trustworthy")]):
    yy = ry + 84 + i * 44
    s.rect(RX + 20, yy - 13, 24, 22, fill=col, rx=6)
    s.text(RX + 32, yy + 3, code, size=12.5, fill="#fff", weight="700", anchor="middle", family=MONO)
    for j, ln in enumerate(what.split("\n")):
        s.text(RX + 54, yy + 3 + j * 13, ln, size=10.6, fill=MUTED)

s.text(48, H - 26,
       "Growth is meant to look like adding items to these lists — one more intent, one more rung, one more audit rule — "
       "never like adding a new kind of thing that needs new rules to govern it.",
       size=12, fill=MUTED)
s.save("04-capabilities.svg")
print("ok")
