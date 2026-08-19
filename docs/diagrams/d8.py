import facts
from svgkit import *

# ---------------- measured facts (see facts.py) ----------------
lab_devices = facts.lab_devices()
routing = facts.event_routing_table_sizes()
adm = facts.admission_defaults()
corr = facts.incident_correlation_summary()
ladder = facts.interaction_ladder()
write_path = facts.write_path_backlog()

for _relpath, _pattern, _ctx in [
    ("src/agent_nettools/event_routing.py", r"def route_event\(", "d8.py stage 1"),
    ("src/agent_nettools/event_watch.py", r"def watch_device\(", "d8.py stage 1"),
    ("src/agent_nettools/admission.py", r"def admit\(", "d8.py stage 3"),
    ("src/agent_nettools/ticket.py", r"def open_ticket\(", "d8.py stage 4"),
    ("src/agent_nettools/ticket.py", r"def record_model_exchange\(", "d8.py stage 5"),
    ("src/agent_nettools/notifier.py", r"def render_report_text\(", "d8.py stage 6"),
    ("mcp_server/server.py", r"def read_lab_ticket\(", "d8.py stage 7"),
    ("src/agent_nettools/ticket.py", r"def record_ticket_outcome\(", "d8.py stage 7"),
    ("src/agent_nettools/health.py", r"def apply_silences\(", "d8.py dedup panel"),
    ("src/agent_nettools/ownership.py", r"def resolve_ownership\(", "d8.py dedup panel"),
]:
    facts.assert_contains(_relpath, _pattern, _ctx)

W, H = 1580, 1060
s = Svg(W, H)
header(s, "The event-driven loop",
       "event → notification → investigation → RCA → notification → the operator closes it. "
       "The operator's own scenario, built stage by stage — and honest about the one stage that is not.",
       facts.header_tag())

# ---------------- the interaction ladder strip ----------------
ly = 116
_LADDER_LABEL = "THE INTERACTION LADDER"
s.text(48, ly, _LADDER_LABEL, size=12, weight="700", ls="0.8", fill=MUTED)
s.text(48 + w_sans(_LADDER_LABEL, 12) * 1.16 + len(_LADDER_LABEL) * 0.8 + 20, ly,
       "docs/design/interfaces.md — this loop lives at Stage 2", size=10.8, fill=MUTED)
lseg = (W - 96) / len(ladder)
_LADDER_STATE = {
    "MVP-0": ("done", GREEN, GREENBG),
    "MVP-0 fast-follow": ("done", GREEN, GREENBG),
    "Stage 2": ("here", AMBER, AMBERBG),
    "MVP-1": ("partial", BLUE, BLUEBG),
    "Stage 3": ("blocked", RED, REDBG),
}
_LADDER_NOTE = {
    "MVP-0": "nettools CLI — built",
    "MVP-0 fast-follow": "Telegram relay — built",
    "Stage 2": "this diagram — you are here",
    "MVP-1": "not on the channel; MCP is the side-door in — read-only",
    "Stage 3": f"blocked on identity — {write_path[0][0]}",
}
for i, (stage, iface, direction) in enumerate(ladder):
    x = 48 + i * lseg
    _kind, col, bg = _LADDER_STATE.get(stage, ("", MUTED, "#ffffff"))
    s.rect(x, ly + 12, lseg - 10, 54, fill=bg, stroke=col, rx=8)
    s.text(x + 12, ly + 32, stage, size=11.8, weight="700", fill=col)
    s.text(x + 12, ly + 48, iface, size=9.6, fill=MUTED)
    s.text(x + 12, ly + 61, _LADDER_NOTE.get(stage, direction), size=9, fill=col, weight="600")

# ---------------- left: the pipeline ----------------
LX, LW = 48, 900
RX, RW = 972, W - 972 - 48

STAGES = [
 ("1", "Trigger intake — device never comes from message text", BLUE,
  ["event_routing.route_event() maps a syslog line or an Alertmanager webhook to a flow by table",
   f"lookup ({routing['mnemonic']} mnemonic + {routing['alertname']} alertname entries) — never a model's judgement.",
   "event_watch.watch_device() does the same for a polled Loki window, and collapses repeated lines",
   "from one root cause first. An event naming no device is unroutable, never guessed."],
  "event_routing.py · event_watch.py"),
 ("2", "Nothing here runs anything", MUTED,
  ["Routing returns an argv list, not a call. “It listens on nothing, calls nothing, retries",
   "nothing.” Whether, and how fast, that list becomes a running `nettools investigate` process",
   "is the operator's own infrastructure choice — cron, systemd, n8n — not this repo's."],
  "RoutingDecision.suggested_command()"),
 ("3", "Admission gate", TEAL,
  [f"At most {adm['per_device']} concurrent investigation(s) per device, {adm['fabric']} across the fabric — filesystem",
   "locks, cross-process. One root cause producing forty syslog lines does not open forty SSH",
   "sessions against one device. Refusal is a recorded, structured `unevaluated`, never a fake pass."],
  "admission.admit()"),
 ("4", "A ticket opens; the descent runs", GREEN,
  ["An append-only Markdown flight recorder, one per run — opened before the walk, not after.",
   "The descent itself is diagram 2's walk, unchanged: no model, code comparing parsed fields."],
  "ticket.open_ticket() · descent.py"),
 ("5", "If a model is asked to paraphrase", PURPLE,
  ["grounding.py grades the paraphrase (diagram 9); the ticket records the grade beside the prose,",
   "never instead of it. Large, static fields are content-addressed — hashed and written once to a",
   "sidecar file; the ticket keeps only the hash. The model's own account of what it did is never",
   "treated as evidence of what happened."],
  "ticket.Ticket.record_model_exchange()"),
 ("6", "Two notifications, one ticket", AMBER,
  ["Opened, then — once RCA lands — the finding plus the ticket's own run_id, so picking it up",
   "starts from the answer, not from zero. The notifier receives the report object only, never the",
   "evidence bundle, and refuses to relay a report marked non-authoritative."],
  "notifier.render_report_text()"),
 ("7", "The operator closes it", GREEN,
  ["Reads the full flight recording — code's answer and any model's prior prose, kept distinct —",
   "back through an MCP client (LM Studio, or any other) by the ticket's run_id. The outcome",
   "(confirmed_correct / incorrect / unknown) is recorded by a human. Nothing upstream sets it."],
  "read_lab_ticket() · ticket.record_ticket_outcome()"),
]

y = ly + 84
for n, title, col, body, code in STAGES:
    h = 34 + len(body) * 16 + 20
    s.rect(LX, y, LW, h, fill="#ffffff", stroke=LINE, rx=10)
    s.rect(LX, y, 4, h, fill=col, rx=2)
    s.badge(LX + 28, y + 24, n, col=col, r=11)
    s.text(LX + 48, y + 29, title, size=13.3, weight="650")
    for i, ln in enumerate(body):
        s.text(LX + 48, y + 50 + i * 16, ln, size=11, fill=MUTED)
    s.text(LX + LW - 18, y + 20, code, size=10.3, family=MONO, fill=FAINT, anchor="end")
    if n != "7":
        s.line(LX + LW / 2, y + h + 1, LX + LW / 2, y + h + 11, stroke="#c6c6c0", sw=1.5, marker="arw")
    y += h + 13

# ---------------- right: dedup, the ticket shape, where it stops ----------------
ry = ly + 84
s.rect(RX, ry, RW, 300, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 18, ry + 26, "DEDUP BEFORE IT PAGES YOU", size=12, weight="700", ls="0.7")
s.text(RX + 18, ry + 44,
       "Every rule below is a table a human can read end to end — not logic threaded through delivery.",
       size=10.3, fill=MUTED)
DEDUP = [
 ("Silence", [
    "A maintenance window tags a finding silenced: true rather than",
    "deleting it — what WOULD have paged is kept, not erased.",
    "expires_at is mandatory: nothing silences forever by accident.",
 ]),
 ("Ownership", [
    "Who gets told is a declared table, not code inside the notifier.",
    "An unmatched finding still reaches the default owner — “no rule",
    "matched” never means “nobody is told.”",
 ]),
 ("Correlation", [
    f"{corr['bases'][0].replace('_', ' ')} > {corr['bases'][1].replace('_', ' ')} > "
    f"{corr['bases'][2].replace('_', ' ')}, strongest claim first.",
    f"{corr['excluded_count']} co-occurrence heuristics — arrival-time proximity",
    "chief among them — are refused by name, not merely left undone.",
 ]),
]
dy = ry + 66
for title, lines in DEDUP:
    s.text(RX + 18, dy, "▸", size=11, fill=TEAL)
    s.text(RX + 34, dy, title, size=11.6, weight="650")
    for i, ln in enumerate(lines):
        s.text(RX + 34, dy + 16 + i * 14, ln, size=10.2, fill=MUTED)
    dy += 20 + len(lines) * 14 + 8

ry += 320
s.rect(RX, ry, RW, 168, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 18, ry + 26, "WHAT'S ON THE TICKET", size=12, weight="700", ls="0.7")
for i, ln in enumerate([
  "code_observed — the question, the tool/device timeline, evidence provenance,",
  "and the descent's own answer. Always present, always code's.",
  "model_claimed — what a model said last time, if anything did. Its own",
  "warning field says first: not verified evidence just because it is recorded.",
  "system_prompt / tools_manifest / user_payload are content-addressed:",
  "scrubbed, sha256'd, written once to a _prompts/<hash>.txt sidecar.",
]):
    s.text(RX + 18, ry + 48 + i * 16, ln, size=10.4,
           fill=INK if i in (0, 2, 4) else MUTED, weight="600" if i in (0, 2, 4) else "400")

ry += 188
STOP_H = 280
s.rect(RX, ry, RW, STOP_H, fill=REDBG, stroke="#f0bfb8", rx=11)
s.text(RX + 18, ry + 26, "WHERE IT STOPS", size=12.5, weight="700", fill=RED, ls="0.8")
s.text(RX + 18, ry + 44, "“The write path. Everything here is gated on identity,", size=10.6, fill=INK, weight="600")
s.text(RX + 18, ry + 58, "and identity does not exist yet.” — BACKLOG.md, Stage 3", size=10.6, fill=INK, weight="600")
wy = ry + 80
for wid, item in write_path:
    s.text(RX + 18, wy, wid, size=10.2, family=MONO, fill=RED, weight="700")
    s.text(RX + 78, wy, item, size=10.2, fill=INK)
    s.text(RX + RW - 18, wy, "DEFERRED", size=9, family=MONO, fill=RED, anchor="end")
    wy += 17
wy += 8
s.line(RX + 18, wy, RX + RW - 18, wy, stroke="#f0bfb8")
wy += 18
s.text(RX + 18, wy, "This build is read-only by invariant (no run_command, no config mode, no", size=10.2, fill=RED)
wy += 15
s.text(RX + 18, wy, "shell) — Stage 3 is not waiting on more code here. It is waiting on B-301,", size=10.2, fill=RED)
wy += 15
s.text(RX + 18, wy, "an identity provider nothing in this repo builds. “There is no Stage 4.”", size=10.2, fill=RED, weight="600")

s.text(48, H - 26,
       "A diagram that showed this loop closing would be lying: the fix is a device write, this build "
       "never makes one, and the seven items that would let a human authorize one are all still DEFERRED.",
       size=12, fill=MUTED)
s.save("08-event-loop.svg")
print("ok")
