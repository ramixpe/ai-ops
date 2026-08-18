from svgkit import *

W, H = 1580, 940
s = Svg(W, H)
header(s, "What it can actually do",
       "31 CLI subcommands · 23 MCP tools on the classic surface, 6 on the staged one · 6 collection intents · 2 investigation flows of 7 declared",
       "every one of them read-only")

# legend
lx = 48
for label, col, bg in [("works offline (fixtures or local state)", GREEN, GREENBG),
                       ("needs a reachable device", "#55555a", "#ffffff"),
                       ("calls a model — opt-in, never default", PURPLE, PURPBG),
                       ("generates traffic (gated)", AMBER, AMBERBG)]:
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

y = 148
y += group(LX, y, LW, "ASK ONE DEVICE A QUESTION", "the six collection intents — one login answers all six", [
    ("facts", None, "live"), ("interfaces", None, "live"), ("bgp", None, "live"),
    ("lldp", None, "live"), ("isis", None, "live"), ("sr", None, "live"),
    ("fabric", "one check, all 9 devices", "live"), ("inventory", "credential-free", "off"),
], LW - 34) + 14

y += group(LX, y, LW, "LOOK UP ONE OBJECT", "validated parameterised templates — the argument is re-rendered, never interpolated", [
    ("route", "DEVICE PREFIX", "live"), ("bgp-neighbor", "DEVICE ADDRESS", "live"),
    ("interface", "DEVICE NAME", "live"), ("logging", "DEVICE --count N", "live"),
    ("ping", "sends ICMP", "probe"), ("traceroute", "sends UDP", "probe"),
], LW - 34) + 14

y += group(LX, y, LW, "DIAGNOSE", "the part that answers 'why', not just 'what'", [
    ("investigate", "the deterministic descent", "off"), ("health", "role-aware verdicts", "off"),
    ("audit", "5 fabric-vs-itself rules", "off"), ("learn-topology", "derive expected state", "off"),
    ("diff", "vs latest or golden", "live"), ("flaps", "oscillation across history", "off"),
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
    ("config show | check", "38 declared settings", "off"), ("inspect", "smoke-test the MCP surface", "off"),
    ("version", None, "off"),
], LW - 34)

# ---------------- right rail: MCP ----------------
ry = 148
s.rect(RX, ry, RW, 470, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 18, ry + 26, "THE MCP SURFACES", size=12.5, weight="700", ls="0.8")
s.text(RX + 18, ry + 45, "Two, deliberately — selected by NETTOOLS_MCP_SURFACE,", size=10.9, fill=MUTED)
s.text(RX + 18, ry + 60, "because collapsing to one would destroy the A/B that", size=10.9, fill=MUTED)
s.text(RX + 18, ry + 75, "measures whether a small model selects better from it.", size=10.9, fill=MUTED)

s.rect(RX + 16, ry + 88, RW - 32, 22, fill=SLATEBG, rx=6)
s.text(RX + 26, ry + 103, "classic — 23 tools  (the default)", size=11.5, weight="650", family=MONO)
TOOLS = ["list_lab_devices", "get_lab_device_facts", "check_lab_interfaces",
         "check_lab_bgp_neighbors", "check_lab_lldp_neighbors", "check_lab_isis_neighbors",
         "check_lab_sr_policies", "check_lab_fabric", "collect_lab_evidence", "get_lab_route",
         "get_lab_bgp_neighbor", "get_lab_interface", "get_lab_logging",
         "search_lab_knowledge", "explain_lab_mnemonic", "diff_lab_device_against_latest",
         "diff_lab_device_against_golden", "assess_lab_device_health", "assess_lab_fabric_health",
         "detect_lab_flaps", "investigate_lab_session", "get_lab_ping", "get_lab_traceroute"]
ty = ry + 126
for i, t_ in enumerate(TOOLS):
    col = AMBER if t_ in ("get_lab_ping", "get_lab_traceroute") else MUTED
    s.text(RX + 26 + (0 if i % 2 == 0 else 216), ty + (i // 2) * 15, "· " + t_,
           size=10.2, family=MONO, fill=col)
ty += 12 * 15 + 14

s.rect(RX + 16, ty, RW - 32, 22, fill=BLUEBG, rx=6)
s.text(RX + 26, ty + 15, "staged — 6 tools  (NETTOOLS_MCP_SURFACE=staged)", size=11.5, weight="650",
       family=MONO, fill=BLUE)
ty += 32
for name, q in [("explore_lab", "what is here, is it broadly OK?"),
                ("check_lab", "what is the state of X right now?"),
                ("lookup_lab", "what does this device say about X?"),
                ("investigate_lab", "why is this broken?"),
                ("history_lab", "what changed on this device?"),
                ("probe_lab", "can it reach that, right now?")]:
    s.text(RX + 26, ty, name, size=11, family=MONO, fill=BLUE, weight="600")
    s.text(RX + 26 + w_mono(name, 11) + 10, ty, q, size=10.3, fill=MUTED)
    ty += 17
s.text(RX + 26, ty + 8, "One tool per stage of an investigation, not one per", size=10.3, fill=MUTED)
s.text(RX + 26, ty + 22, "function — mirroring how an engineer actually moves.", size=10.3, fill=MUTED)

ry += 490
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
