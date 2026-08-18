from svgkit import *

W, H = 1580, 1000
s = Svg(W, H)
header(s, "ios-xr-nettools — repo anatomy",
       "21,116 lines of source + 1,326 in the MCP server · 20,440 lines of tests · 235 commits · read-only, single lab, 9 Cisco XRd nodes",
       "2026-08-18 · feat/investigation-layer")

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
         p=[("cli.py", "1,396 · 31 subcommands"),
            ("mcp_server/server.py", "818 · 23 tools (classic)"),
            ("staged_surface.py", "247 · 6 tools (opt-in)"),
            ("event_routing.py", "350 · syslog/Alertmanager → flow"),
            ("notifier.py", "370 · Telegram, egress-bounded")]),
    dict(t="ANSWER LAYER", n="2", c="#ffffff", sc=LINE, tc=INK,
         d="composes a diagnosis out of already-parsed facts — never out of device text",
         p=[("investigation.py", "787 · drives the descent"),
            ("checks.py", "1,535 · per-role health rules"),
            ("audit.py", "288 · 5 fabric-vs-itself rules"),
            ("fabric_analysis.py", "189"),
            ("knowledge.py", "176 · grep, not RAG"),
            ("output.py", "466 · json | table | summary")]),
    dict(t="DETERMINISTIC CORE", n="3", c=BLUEBG, sc="#b9d0f4", tc=BLUE,
         d="NO MODEL CALL, EVER — if this layer ever needs one, something above it was designed wrong",
         p=[("descent.py", "546 · lowest broken rung wins"),
            ("flows.py", "626 · 7 declared / 2 implemented"),
            ("epoch.py", "736 · one observation window"),
            ("grounding.py", "973 · citation + chain + containment"),
            ("topology.py", "369"), ("interface_kind.py", "197")]),
    dict(t="EVIDENCE & PARSING", n="4", c="#ffffff", sc=LINE, tc=INK,
         d="the only code in the repository that reads raw device text",
         p=[("network_tools.py", "2,032 · the one transport chokepoint"),
            ("parsers.py", "844"), ("template_parsers.py", "1,742"),
            ("evidence_store.py", "548 · json | sqlite"),
            ("log_window.py", "423"),
            ("fixtures.py", "339 · 563 captures, replay offline")]),
    dict(t="SAFETY BOUNDARY", n="5", c=REDBG, sc="#f0bfb8", tc=RED,
         d="checked BEFORE credentials load and BEFORE a socket opens — both files frozen since the pre-build baseline",
         p=[("platforms.py", "204 · FROZEN · exact-match frozenset", dict(stroke="#e0a79e")),
            ("templates.py", "476 · FROZEN · canonicalise by reconstruction", dict(stroke="#e0a79e"))],
         foot="cisco_xr 7 · cisco_iosxe 5 · juniper_junos 5 approved commands.  VERB_ALLOWLIST = {show, ping, traceroute}."
              "  There is no run_command(), no config mode, no shell."),
]

y = 126
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
    if L is not LAYERS[-1]:
        s.line(SPINE_X + SPINE_W / 2, nexty + 2, SPINE_X + SPINE_W / 2, nexty + 16,
               stroke="#bcbcb6", sw=1.6, marker="arw")
    y = nexty + 20

# transport + devices
s.rect(SPINE_X, y, SPINE_W, 44, fill="#ffffff", stroke=LINE, rx=12)
s.badge(SPINE_X + 26, y + 22, "6", col="#55555a")
s.text(SPINE_X + 46, y + 21, "TRANSPORT", size=13.5, weight="700", ls="0.9")
s.text(SPINE_X + 46 + w_sans("TRANSPORT", 13.5) * 1.16 + 9 * 0.9 + 20, y + 21,
       "netmiko over SSH · one login per device per observation window, not one per question", size=11.8, fill=MUTED)
s.text(SPINE_X + SPINE_W - 18, y + 21, "_netmiko_send_commands()  ← the single exit", size=11.5,
       fill=MUTED, anchor="end", family=MONO)
y += 44
s.line(SPINE_X + SPINE_W / 2, y + 2, SPINE_X + SPINE_W / 2, y + 16, stroke="#bcbcb6", sw=1.6, marker="arw")
y += 20

s.rect(SPINE_X, y, SPINE_W, 62, fill=GREENBG, stroke="#b9dcc5", rx=12)
s.text(SPINE_X + 20, y + 24, "THE LAB", size=13.5, weight="700", fill=GREEN, ls="0.9")
s.text(SPINE_X + 20 + w_sans("THE LAB", 13.5) * 1.16 + 7 * 0.9 + 20, y + 24,
       "containerlab · 9 Cisco IOS-XR (XRd) nodes on 172.20.250.0/24 · IS-IS + SR + iBGP over a route reflector",
       size=11.8, fill=MUTED)
dx = SPINE_X + 20
for d, role in [("P1", "core"), ("P2", "core"), ("P3", "core"), ("P4", "core"),
                ("PE1", "edge"), ("PE2", "edge"), ("PE3", "edge"), ("PE4", "edge"), ("RR1", "reflector")]:
    dx += s.pill(dx, y + 32, d, role, fill="#fff", stroke="#b9dcc5", h=22, size=11.5,
                 tcol=GREEN, subcol=MUTED) + 7

# ---------------- right rail ----------------
ry = 126
s.rect(RAIL_X, ry, RAIL_W, 300, fill=PURPBG, stroke="#d5c6f5", rx=12)
s.text(RAIL_X + 20, ry + 26, "THE MODEL SIDE", size=13.5, weight="700", fill=PURPLE, ls="0.9")
s.text(RAIL_X + 20, ry + 46, "A branch off layers 2 and 4 — never in the answer path.",
       size=11.5, fill=MUTED)
s.text(RAIL_X + 20, ry + 63, "No model call happens unless you ask for one.", size=11.5,
       fill=PURPLE, weight="600")
s.rect(RAIL_X + 18, ry + 76, RAIL_W - 36, 48, fill="#fff", stroke="#d5c6f5", rx=8)
s.text(RAIL_X + 30, ry + 96, "model_egress.py", size=12.5, family=MONO, weight="600", fill=PURPLE)
s.text(RAIL_X + 30 + w_mono("model_egress.py", 12.5) + 10, ry + 96, "441 · the projector", size=11, fill=MUTED)
s.text(RAIL_X + 30, ry + 114, "raw text withheld-with-count · free text quoted", size=10.8, fill=MUTED)
ly = ry + 136
for lbl, sub in [("prompt_library.py", "363 · builds every prompt"),
                 ("evidence_budget.py", "250 · fits the window"),
                 ("llm_analysis.py", "785 · analyze / correlate"),
                 ("agent_loop.py", "708 · bounded tool loop"),
                 ("mcp_server/boundary.py", "260 · sanitise-on-register")]:
    s.text(RAIL_X + 30, ly, "•", size=12, fill=PURPLE)
    s.text(RAIL_X + 42, ly, lbl, size=11.5, family=MONO, fill=INK)
    s.text(RAIL_X + 42 + w_mono(lbl, 11.5) + 8, ly, sub, size=10.8, fill=MUTED)
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
        "lab.platform_for() reads static data only, so the\nallowlist check can happen before any secret loads.", "5"),
       ("2", "The allowlist is checked before credentials.",
        "Ordering is load-bearing: a bad command never\nreaches the network, and never costs a login.", "5"),
       ("3", "No command is built by interpolation.",
        "A prefix/address/name is parsed into a typed object\nand the command re-rendered from its canonical form.", "5"),
       ("4", "No unparsed device text reaches a model.",
        "Structural, not filtered: prompt_library never holds\nthe text. Five egress paths, all projected.", "2/4")]
for n, title, body, layer in INV:
    s.badge(RAIL_X + 32, iy + 4, n, col=RED if n != "4" else PURPLE, r=10)
    s.text(RAIL_X + 50, iy + 8, title, size=11.8, weight="650")
    for i, ln in enumerate(body.split("\n")):
        s.text(RAIL_X + 50, iy + 25 + i * 15, ln, size=10.9, fill=MUTED)
    s.text(RAIL_X + RAIL_W - 20, iy + 8, f"layer {layer}", size=10, fill=FAINT, anchor="end", family=MONO)
    iy += 68
s.text(RAIL_X + 20, ry + 348,
       "Each is pinned by a test that fails when the guard is removed (20/20 verified).",
       size=10.8, fill=GREEN)

# branch arrow from spine layer 2 to the model rail
s.path(f"M {SPINE_X + SPINE_W + 2} 250 H {RAIL_X - 8}", stroke=PURPLE, sw=1.5, dash="4 4", marker="arwp")
s.path(f"M {SPINE_X + SPINE_W + 2} 600 H {RAIL_X - 8}", stroke=PURPLE, sw=1.5, dash="4 4", marker="arwp")

s.text(48, H - 26,
       "Read down the spine: a question enters at the top, becomes an answer in layer 3, and only layers 4–6 ever touch a device. "
       "The model branch hangs off the side — it restates conclusions, it never reaches them.",
       size=12, fill=MUTED)
s.save("01-repo-anatomy.svg")
print("ok")
