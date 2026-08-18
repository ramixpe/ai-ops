from svgkit import *

W, H = 1580, 1020
s = Svg(W, H)
header(s, "One call, end to end",
       "nettools investigate RR1 10.255.0.12 — what actually happens between the command and the answer. Every value below is from a real run.",
       "flow: bgp_session · 5 rungs")

# command bar
s.rect(48, 120, W - 96, 52, fill="#1b1b1f", stroke="none", rx=10)
s.text(68, 152, "$ nettools investigate RR1 10.255.0.12 --from-fixtures --format table",
       size=15, family=MONO, fill="#e9e9e6")
s.text(W - 68, 152, "exit 1 (a fault was found)  ·  0.25 s  ·  no network, no credentials, no API key",
       size=12, family=MONO, fill="#8f8f88", anchor="end")

LX, LW = 48, 862
RX, RW = 944, W - 944 - 48

STAGES = [
 ("1", "Resolve and validate the request", BLUE,
  ["Device looked up in the inventory (never taken from message text).",
   "Subject validated by reconstruction: IPv4Address('10.255.0.12') → re-rendered."],
  "cli.py → flows.flow_for('bgp_session')"),
 ("2", "Pre-walk collection check", BLUE,
  ["Every collect step in every rung must be resolvable from (device, subject) alone,",
   "BEFORE the walk starts. A rung may not collect something an earlier rung concluded."],
  "epoch.validate_prewalk_collection()"),
 ("3", "Collect the evidence epoch — once", TEAL,
  ["One SSH login per device for the whole walk, not one per rung or per question.",
   "6 intents (facts · interfaces · bgp · lldp · isis · sr). Here: replayed from fixtures."],
  "network_tools.collect_evidence()"),
 ("4", "Parse — the only place device text is read", TEAL,
  ["Raw output → typed records. Parse failure is a state, not an exception:",
   "a rung whose parse did not reach PARSE_OK is 'unevaluated', never 'healthy'."],
  "parsers.py · template_parsers.py"),
 ("5", "Walk the ladder", GREEN,
  ["Five rungs, top to bottom. Every verdict is code comparing parsed fields —",
   "no model, no heuristic, no scoring. broken → keep going. unevaluated → stop."],
  "descent.py"),
 ("6", "Pick the cause: the LOWEST broken rung", GREEN,
  ["Not the first. The first broken rung is the symptom you already knew about;",
   "the lowest one is what to go and fix. Here: interface on PE2."],
  "descent.DescentResult.cause"),
 ("7", "Coherence + grounding gates", AMBER,
  ["Epoch skew measured 0.013 s against a 30 s bound → coherent.",
   "Grounding checks citation, chain and identifier containment before anything is emitted."],
  "epoch.py · grounding.py"),
 ("8", "Render and exit", MUTED,
  ["json | table | summary — the exit code is computed from the full result BEFORE",
   "rendering, so a summary view can never soften what json would have reported."],
  "output.py → exit 0 | 1 | 2"),
]

y = 196
for n, title, col, body, code in STAGES:
    h = 78
    s.rect(LX, y, LW, h, fill="#ffffff", stroke=LINE, rx=10)
    s.rect(LX, y, 4, h, fill=col, rx=2)
    s.badge(LX + 30, y + 26, n, col=col, r=11)
    s.text(LX + 50, y + 31, title, size=14, weight="650")
    for i, ln in enumerate(body):
        s.text(LX + 50, y + 52 + i * 16, ln, size=11.6, fill=MUTED)
    s.text(LX + LW - 18, y + 22, code, size=11, family=MONO, fill=FAINT, anchor="end")
    if n != "8":
        s.line(LX + LW / 2, y + h + 1, LX + LW / 2, y + h + 12, stroke="#c6c6c0", sw=1.5, marker="arw")
    y += h + 14

# ---------- right: what came back ----------
ry = 196
s.rect(RX, ry, RW, 254, fill="#ffffff", stroke=LINE, rx=10)
s.text(RX + 20, ry + 26, "WHAT CAME BACK", size=12.5, weight="700", ls="0.9")
s.text(RX + 20, ry + 45, "the whole ladder, not just the broken part", size=11, fill=MUTED)
cols = [RX + 20, RX + 148, RX + 214]
s.text(cols[0], ry + 70, "RUNG", size=10, family=MONO, fill=FAINT)
s.text(cols[1], ry + 70, "DEV", size=10, family=MONO, fill=FAINT)
s.text(cols[2], ry + 70, "VERDICT", size=10, family=MONO, fill=FAINT)
s.line(RX + 20, ry + 78, RX + RW - 20, ry + 78, stroke=LINE)
rows = [("bgp_session", "RR1", "BROKEN", "state: Idle"),
        ("transport", "RR1", "BROKEN", "socket not armed for read"),
        ("route_to_peer", "RR1", "BROKEN", "% Network not in table"),
        ("igp_adjacency", "PE2", "BROKEN", "0 IS-IS adjacencies"),
        ("interface", "PE2", "BROKEN", "1 of 3 members healthy")]
yy = ry + 98
for i, (r, d, v, why) in enumerate(rows):
    last = i == len(rows) - 1
    if last:
        s.rect(RX + 14, yy - 15, RW - 28, 38, fill=REDBG, stroke="#f0bfb8", rx=7)
    s.text(cols[0], yy, r, size=11.5, family=MONO, fill=RED if last else INK, weight="600" if last else "400")
    s.text(cols[1], yy, d, size=11.5, family=MONO, fill=RED if last else INK)
    s.text(cols[2], yy, v, size=11.5, family=MONO, fill=RED if last else MUTED, weight="600" if last else "400")
    s.text(cols[0], yy + 15 if last else yy, "", size=10)
    if last:
        s.text(cols[2] + 62, yy, "←  CAUSE", size=11, family=MONO, fill=RED, weight="700")
        s.text(cols[0], yy + 16, why + "  ·  the lowest broken rung", size=10.5, fill=RED)
        yy += 16
    else:
        s.text(cols[2] + 62, yy, why, size=10.5, fill=FAINT)
    yy += 26

ry += 272
s.rect(RX, ry, RW, 132, fill=GREENBG, stroke="#b9dcc5", rx=10)
s.text(RX + 20, ry + 26, "THE ANSWER", size=12.5, weight="700", fill=GREEN, ls="0.9")
s.text(RX + 20, ry + 52, "finding:", size=11.5, fill=MUTED, family=MONO)
s.text(RX + 100, ry + 52, "interface_line_down on PE2", size=12.5, family=MONO, weight="600", fill=GREEN)
s.text(RX + 20, ry + 74, "trustworthy:", size=11.5, fill=MUTED, family=MONO)
s.text(RX + 120, ry + 74, "true", size=12.5, family=MONO, weight="600", fill=GREEN)
s.text(RX + 20, ry + 96, "report:", size=11.5, fill=MUTED, family=MONO)
s.text(RX + 100, ry + 96, "emitted — rendered from the descent", size=12, family=MONO, fill=GREEN)
s.text(RX + 20, ry + 116, "No model spoke here. None was asked to.", size=11, fill=MUTED)

ry += 150
s.rect(RX, ry, RW, 300, fill="#ffffff", stroke=LINE, rx=10)
s.text(RX + 20, ry + 26, "FOUR RULES THIS WALK OBEYS", size=12.5, weight="700", ls="0.9")
items = [
 ("Lowest broken rung, not first", "bgp_session was broken too — reporting it\nwould have named the symptom as the cause."),
 ("unevaluated stops the walk", "A rung that could not be judged is never read\nas healthy; the descent halts and says so."),
 ("One epoch for the whole walk", "Symptom and cause are re-read in the same\nwindow, so a fix mid-walk cannot fake a pass."),
 ("No fault on the path", "If nothing on the dependency path is broken, that\nis the answer — off-path faults are observations."),
]
iy = ry + 52
for t_, b in items:
    s.text(RX + 20, iy, "▸", size=11, fill=BLUE)
    s.text(RX + 38, iy, t_, size=11.8, weight="650")
    for i, ln in enumerate(b.split("\n")):
        s.text(RX + 38, iy + 17 + i * 14, ln, size=10.8, fill=MUTED)
    iy += 62

s.text(48, H - 26,
       "The point of the ladder: five things were broken, and only one of them is worth a truck roll. "
       "The descent is what turns 'the session is down' into 'go look at PE2's bundle members'.",
       size=12, fill=MUTED)
s.save("02-call-path.svg")
print("ok")
