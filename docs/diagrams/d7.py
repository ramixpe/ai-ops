import facts
from svgkit import *

_flows = facts.flows_summary()
_lab_devices = facts.lab_devices()
_neo4j = facts.neo4j_graph_counts()
_netbox = facts.netbox_fabric_counts()
_prom_metrics = facts.prometheus_metric_count()
_evq = facts.evidence_source_query_counts()
_tool_classes = facts.classic_mcp_tool_class_counts()

W, H = 1720, 1130
s = Svg(W, H)
header(s, "Stage 2 — the SOTA application",
       "As decided 2026-08-18, corrected against what shipped: no separate hub — a third MCP registration class — but the observability, NetBox and neo4j reads are now real.",
       "decided 2026-08-18 · partially built since")

# ---- legend ----
lx = 48
for label, col, bg in [("BUILT — shipped, tested", GREEN, GREENBG),
                       ("LIVE — exists on host, access verified today", TEAL, TEALBG),
                       ("BUILD NOW — Stage 2 work", BLUE, BLUEBG),
                       ("ROADMAP — later", FAINT, "#ffffff")]:
    s.rect(lx, 116, 13, 13, fill=bg, stroke=col, rx=3)
    s.text(lx + 20, 127, label, size=11.2, fill=MUTED)
    lx += w_sans(label, 11.2) + 44

# =============== ROW 1: the human, the model, the hub ===============
y1 = 152
s.rect(48, y1, 250, 118, fill="#ffffff", stroke=LINE, rx=12)
s.text(68, y1 + 28, "OPERATOR", size=13, weight="700", ls="0.8")
s.text(68, y1 + 50, "Questions in plain language.", size = 11.2, fill=MUTED)
s.text(68, y1 + 66, "Verdicts on the accuracy ledger.", size=11.2, fill=MUTED)
s.text(68, y1 + 82, "The only writer of outcomes.", size=11.2, fill=MUTED)

s.line(298, y1 + 59, 336, y1 + 59, stroke="#a8a8a2", sw=1.6, marker="arw")

s.rect(340, y1, 292, 118, fill="#ffffff", stroke=LINE, rx=12)
s.text(360, y1 + 28, "LLM — LM Studio / hosted", size=13, weight="700")
s.text(360, y1 + 50, "Selects and fills. Never authors a", size=11.2, fill=MUTED)
s.text(360, y1 + 66, "flow, a command, or a conclusion.", size=11.2, fill=MUTED)
s.text(360, y1 + 88, "Scored per-dimension by the eval", size=11.2, fill=PURPLE)
s.text(360, y1 + 103, "harness — capability is not monotonic.", size=11.2, fill=PURPLE)

s.line(632, y1 + 59, 670, y1 + 59, stroke="#a8a8a2", sw=1.6, marker="arw")

s.rect(674, y1, 300, 118, fill=GREENBG, stroke="#b9dcc5", rx=12)
s.text(694, y1 + 28, "THE MCP SERVER — not a separate hub", size=13, weight="700", fill=GREEN, ls="0.6")
s.pill(694, y1 + 40, "BUILT", None, fill="#fff", stroke=GREEN, tcol=GREEN, h=20, size=10.5)
s.text(694, y1 + 82, f"One surface, {_tool_classes['total']} tools, 3 registration classes.", size=11.2, fill=MUTED)
s.text(694, y1 + 100, "The gradient is diagram 9's MAY/MAY NOT, not a menu here.", size=10.6, fill=GREEN, weight="600")

# other doors, under the operator
s.rect(48, y1 + 132, 584, 58, fill=GREENBG, stroke="#b9dcc5", rx=10)
s.text(68, y1 + 155, "THE OTHER DOORS — no model in them", size=11.5, weight="700", fill=GREEN, ls="0.6")
s.text(68, y1 + 173, "CLI (human, direct)  ·  route-event: syslog line / Alertmanager webhook → flow, deterministically (B-480)", size=11, fill=MUTED)
s.line(632, y1 + 161, 674, y1 + 130, stroke="#b9dcc5", sw=1.4, marker="arwg")

# =============== the gradient bar ===============
y2 = y1 + 214
s.text(48, y2, "THE MENU — a gradient the model walks, wide to narrow", size=13.5, weight="700", ls="0.8")
s.text(48 + w_sans("THE MENU — a gradient the model walks, wide to narrow", 13.5) * 1.16 + 40, y2,
       "decided: Option C, split by phase — and every side-effect lives behind a flow, never composed by the model", size=11.3, fill=MUTED)
gy = y2 + 14
GW = W - 96
for i, (title, sub, examples, col, bg, frac) in enumerate([
    ("FLOWS — widest", "frame the problem, gather broadly, all side-effects",
     "audit · analyze --fabric · health --all · n8n: notify/ticket/schedule", PURPLE, PURPBG, 0.36),
    # OSPF/RSVP/CDP are not "still to add" -- the 2026-08-19 protocol sweep
    # (OBS-183) measured no observable state for any of the three on this
    # fabric and refused them permanently, not provisionally. ISIS/LDP/MP-BGP
    # VPNv4 are the ones that are real here, and all three now have coverage.
    ("FOCUSED / CONTEXT TOOLS", "one domain, both readings: context AND one specific check",
     "per-protocol adjacency: ISIS · LDP · MP-BGP VPNv4 done · OSPF/RSVP/CDP surveyed, refused (OBS-183)", BLUE, BLUEBG, 0.34),
    ("DESCENT TOOLS — narrowest", "one subject, one rung, one verdict from parsed fields",
     "investigate · get_bgp_neighbor · a single rung check", GREEN, GREENBG, 0.30),
]):
    x = 48 + sum([0.36, 0.34, 0.30][:i]) * GW + i * 0  # contiguous
    w = frac * GW - (8 if i < 2 else 0)
    s.rect(x, gy, w, 86, fill=bg, stroke=col, rx=10)
    s.text(x + 16, gy + 24, title, size=12.5, weight="700", fill=col)
    s.text(x + 16, gy + 44, sub, size=10.8, fill=INK)
    s.text(x + 16, gy + 66, examples, size=10.2, fill=MUTED, family=MONO)
    if i < 2:
        s.line(x + w + 1, gy + 43, x + w + 7, gy + 43, stroke="#a8a8a2", sw=1.5, marker="arw")
s.text(48, gy + 104, "Fractal with the core: the same wide-then-narrow shape the descent already runs inside one investigate. History (below) feeds the WIDE step only — a trend is context, never a rung's verdict.",
       size=11, fill=MUTED)

# =============== ROW 3: backends ===============
y3 = gy + 130
s.text(48, y3, "THE BACKENDS BEHIND THE HUB", size=13.5, weight="700", ls="0.8")
y3 += 12
BK = [
 ("nettools", "BUILT", GREEN, GREENBG,
  [f"{len(facts.cli_subcommands())} commands · {len(facts.classic_mcp_tools())} MCP tools · {len(_flows['implemented'])} flows",
   "descent, grounding, epoch, ledger", "the deterministic diagnostic core"]),
 ("event loop", "BUILT", GREEN, GREENBG,
  # Correction against what actually shipped: no n8n deployment exists in
  # this repo (checked -- no docker-compose, no n8n code). The wide-step +
  # side-effect logic n8n was meant to hold was built directly instead
  # (diagram 8). n8n stays A valid choice for the operator's own scheduler,
  # never a dependency this repo introduces.
  ["event_routing.py + event_watch.py: wide step", "admission.py + ticket.py: gate + recorder",
   "n8n/cron/systemd all still fine — diagram 8"]),
 ("neo4j", "BUILT", GREEN, GREENBG,
  [f"{_neo4j['nodes']} nodes, {_neo4j['relationships']} relationships — live",
   "DERIVED from parsed LLDP+IS-IS evidence", f"{_evq['neo4j']} read query: get_lab_graph_topology"]),
 ("NetBox inventory", "BUILT", GREEN, GREENBG,
  [f"{_netbox['devices']} devices, {_netbox['interfaces']} interfaces, {_netbox['cables']} cables — live",
   "derived, not authored — like neo4j", f"{_evq['netbox']} read queries, gated (B-512)"]),
 ("documentation", "BUILT", GREEN, GREENBG,
  ["grep/normal DB now — decided, shipped", "search_lab_knowledge, no vector DB", "citations survive retrieval: path:line"]),
]
bw = (W - 96 - 4 * 14) / 5
for i, (name, chip, col, bg, lines) in enumerate(BK):
    x = 48 + i * (bw + 14)
    s.rect(x, y3, bw, 108, fill=bg, stroke=col if bg != "#ffffff" else LINE, rx=11)
    s.text(x + 14, y3 + 24, name, size=13, weight="700", family=MONO)
    cw = w_sans(chip, 9.5) + 16
    s.rect(x + bw - cw - 12, y3 + 11, cw, 17, fill=col if chip != "ROADMAP" else "#fff",
           stroke=col, rx=8)
    s.text(x + bw - cw / 2 - 12, y3 + 23, chip, size=9.5,
           fill="#fff" if chip != "ROADMAP" else FAINT, weight="700", anchor="middle")
    for j, ln in enumerate(lines):
        s.text(x + 14, y3 + 46 + j * 16, ln, size=10.3, fill=MUTED)
# hub -> backends arrow
s.line(824, y1 + 118, 824, y3 - 18, stroke="#b9d0f4", sw=1.6, marker="arwb")

# =============== ROW 4: cache + observability ===============
y4 = y3 + 132
# cache
s.rect(48, y4, 560, 172, fill=AMBERBG, stroke="#eed7ae", rx=12)
s.text(68, y4 + 26, "THE CACHE LAYER — epoch-aware, decided", size=12.5, weight="700", fill=AMBER, ls="0.6")
s.pillrow(68, y4 + 38, [("Redis", "hot state / KPIs"), ("Postgres 16", "config — already on host", {"stroke": TEAL, "tcol": TEAL}), ("neo4j", "topology")],
          fill="#fff", stroke="#eed7ae", h=24, size=11.5)
for j, ln in enumerate([
  "Config is cached; a syslog config-change event invalidates it. Status is always collected.",
  "THE RULE: a cached value's AGE counts against the coherence skew bound — the epoch",
  "guarantee survives the cache or the cache does not ship. Later: fields may be qualified",
  "always-cached, one reviewed decision per field. Expected-vs-observed diff falls out free (B-106).",
]):
    s.text(68, y4 + 86 + j * 16, ln, size=10.6, fill=INK if j == 1 else MUTED, weight="600" if j == 1 else "400")

# observability — LIVE
ox = 632
s.rect(ox, y4, W - ox - 48, 172, fill=TEALBG, stroke="#a7d6d0", rx=12)
s.text(ox + 20, y4 + 26, "OBSERVABILITY — DISCOVERED LIVE, NOW READ THROUGH 3 MCP TOOLS", size=12.5, weight="700", fill=TEAL, ls="0.6")
rows = [
 ("syslog-ng 4.5", ".101:514", f"all {len(_lab_devices)} routers logging", "→ Loki 2.9.8", ".103:3100", "host/severity labels"),
 ("gNMI → telegraf", "gnmic:7890", "streaming telemetry", "→ Prometheus", ".102:9090", f"{facts.fmt(_prom_metrics)} metrics live"),
 ("Grafana 10.4", ".104:3000", "dashboards over both", "Alertmanager", ".105:9093", "route-event's live peer"),
]
ry = y4 + 50
for a, ai, an, b, bi, bn in rows:
    s.text(ox + 20, ry, a, size=11.3, family=MONO, weight="600")
    s.text(ox + 168, ry, ai, size=10, family=MONO, fill=FAINT)
    s.text(ox + 258, ry, an, size=10.3, fill=MUTED)
    s.text(ox + 470, ry, b, size=11.3, family=MONO, weight="600")
    s.text(ox + 610, ry, bi, size=10, family=MONO, fill=FAINT)
    s.text(ox + 700, ry, bn, size=10.3, fill=MUTED)
    ry += 22
s.text(ox + 20, ry + 8, "No longer just flowing — now READ: get_lab_logs, get_lab_interface_rate_history,", size=10.6, fill=TEAL, weight="600")
s.text(ox + 20, ry + 24, "get_lab_isis_adjacency_history, get_lab_ldp_session_history, get_lab_device_uptime_history (B-512).", size=10.6, fill=TEAL, weight="600")
s.text(ox + 20, ry + 44, "History is evidence about a WINDOW; a verdict is about an instant. Absence of a sample is never zero.", size=10.3, fill=MUTED)

# =============== ROW 5: the fabric ===============
y5 = y4 + 196
s.rect(48, y5, W - 96, 64, fill="#ffffff", stroke=LINE, rx=12)
s.text(68, y5 + 26, "THE FABRIC", size=12.5, weight="700", ls="0.8")
s.text(68 + w_sans("THE FABRIC", 12.5) * 1.16 + 24, y5 + 26,
       f"{len(_lab_devices)} × IOS-XR (XRd) on 172.20.250.0/24 · syslog → .101 · gNMI → telegraf · SSH reads only through nettools' frozen allowlist — no config mode, no shell, four invariants unchanged",
       size=11, fill=MUTED)
dx = 68
for d, _role in _lab_devices:
    dx += s.pill(dx, y5 + 36, d, None, fill=GREENBG, stroke="#b9dcc5", tcol=GREEN, h=20, size=10.5) + 6
s.text(dx + 16, y5 + 50, "PE3↔P2 IS-IS break preserved as the isis-broken fixture — the config axis's first live demand", size=10.3, fill=AMBER)

# arrows fabric->observability, cache->fabric
s.line(1180, y5 - 2, 1180, y4 + 174, stroke="#a7d6d0", sw=1.5, marker="arw")
s.line(330, y5 - 2, 330, y4 + 174, stroke="#eed7ae", sw=1.5, marker="arw")

# =============== ROW 6: decisions + pending ===============
y6 = y5 + 88
s.rect(48, y6, 1000, 158, fill="#1b1b1f", rx=12)
s.text(68, y6 + 26, "DECIDED 2026-08-18", size=12.5, weight="700", fill="#e9e9e6", ls="0.8")
for j, ln in enumerate([
  "Option C, wide→narrow gradient · side-effects only behind flows · shipped as a 3rd MCP registration class, not a new hub",
  "Cache is epoch-aware, still to build · neo4j + NetBox are derived and now BUILT (LLDP/IS-IS collector) · doc store: grep, shipped",
  "GRACE is the prompt discipline (RACE vs P.E.N.E closed) · measure-first: seal a prediction, score the menu with the eval harness",
  "Juniper out of scope · push to GitHub at every milestone · retire stale docs at milestone 0 (evidence-class documents stay)",
]):
    s.text(68, y6 + 52 + j * 22, ln, size=11.2, fill="#c9c9c4")

s.rect(1072, y6, W - 1072 - 48, 158, fill="#ffffff", stroke=LINE, rx=12)
s.text(1092, y6 + 26, "OPEN — ON THE PLAN", size=12.5, weight="700", ls="0.8")
for j, ln in enumerate([
  "memory (session/agent — D14)",
  "measure the context window (the 7-backend manifest cost)",
  # Resolved by the 2026-08-19 protocol sweep (OBS-183), not still open:
  # LDP (B-109) and MP-BGP VPNv4 shipped; OSPF/RSVP/CDP were surveyed live
  # against all nine devices and refused -- no observable state on this
  # fabric, not merely "not yet built".
  "protocol coverage: settled (OBS-183) — LDP/MP-BGP VPNv4 added, OSPF/RSVP/CDP refused",
  "RR-aware TS agent flow · neo4j ontology (v2)",
  "round 6 + Q1 — still the owed measurement",
]):
    s.text(1092, y6 + 50 + j * 21, "· " + ln, size=11, fill=MUTED)

s.text(48, H - 26,
       "The guarantee that survives every box on this page: no verdict is reached from anything but parsed, point-in-time device fields — "
       "the model selects and fills, history frames, flows act, and only a human scores the ledger.",
       size=12, fill=MUTED)
s.save("07-stage2-architecture.svg")
print("ok")
