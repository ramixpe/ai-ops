import facts
from svgkit import *

flows = facts.flows_summary()
rung_counts = facts.flow_rung_counts()
intents = facts.cisco_xr_intents()
templates = facts.cisco_xr_templates()
cmd_counts = facts.approved_command_counts()
verb_allowlist = facts.verb_allowlist_display()

def _rung_word(name):
    n = rung_counts[name]
    return f"{name} — {n} rung" + ("" if n == 1 else "s")

W, H = 1580, 1010
s = Svg(W, H)
header(s, "How a diagnosis is reached",
       "Three tiers of composition, and one deterministic walk. The model picks from menus and fills in blanks — it never writes a rung, a command, or a conclusion.",
       "descent.py · flows.py")

# ---------------- left: three tiers ----------------
LX, LW = 48, 620
s.text(LX, 132, "THE THREE TIERS", size=13.5, weight="700", ls="0.9")
s.text(LX + w_sans("THE THREE TIERS", 13.5) * 1.16 + 26, 132,
       "each built only out of the one below it", size=11.3, fill=MUTED)

TIERS = [
 ("III", "FLOWS", PURPLE, PURPBG,
  "A named dependency ladder for one kind of object.",
  [" · ".join(_rung_word(n) for n in flows["implemented"]),
   # `device_health` is investigated-and-refused (B-108, OBS-186), which is a
   # different claim from "not yet built" -- l3vpn_service/topology are the
   # latter. Keeping them on separate lines is what keeps that distinction
   # visible rather than flattening both into one "not built" list.
   f"refused, not merely unbuilt: {' · '.join(flows['refused'])}",
   f"declared, not yet built: {' · '.join(flows['unbuilt'])}"],
  "A flow is data, not code. Adding one adds no new rule."),
 ("II", "TOOLS / INTENTS", BLUE, BLUEBG,
  "A named question with a fixed answer shape.",
  [f"{len(intents)} collection intents: {' · '.join(intents)}",
   f"{len(templates['lookups'])} object lookups: {' · '.join(templates['lookups'])}",
   f"{len(templates['probes'])} active probes: {' · '.join(templates['probes'])}"],
  "Each maps to approved commands plus a parser. Nothing else."),
 ("I", "COMMANDS", RED, REDBG,
  "Exact strings, frozen, matched by identity.",
  [f"cisco_xr: {cmd_counts['cisco_xr']}    cisco_iosxe: {cmd_counts['cisco_iosxe']}    "
   f"juniper_junos: {cmd_counts['juniper_junos']}",
   f"VERB_ALLOWLIST = {verb_allowlist}",
   "templates re-render every argument from a typed object"],
  "There is no run_command(). This is the floor everything stands on."),
]
ty = 148
for num, name, col, bg, lead, lines, foot in TIERS:
    h = 112 + len(lines) * 18
    s.rect(LX, ty, LW, h, fill=bg, stroke=col, rx=11)
    s.text(LX + 22, ty + 32, num, size=21, weight="700", fill=col, family=MONO)
    s.text(LX + 66, ty + 30, name, size=14.5, weight="700", fill=col, ls="0.6")
    s.text(LX + 66, ty + 50, lead, size=11.5, fill=INK)
    for i, ln in enumerate(lines):
        s.text(LX + 66, ty + 74 + i * 17, ln, size=11, family=MONO, fill=MUTED)
    s.line(LX + 22, ty + h - 30, LX + LW - 22, ty + h - 30, stroke=col, sw=0.6, dash="3 3")
    s.text(LX + 22, ty + h - 12, foot, size=10.9, fill=col, weight="600")
    if num != "I":
        s.text(LX + LW / 2, ty + h + 15, "▲  composed only from", size=10, fill=FAINT, anchor="middle")
    ty += h + 26

s.rect(LX, ty, LW, 96, fill="#1b1b1f", rx=11)
s.text(LX + 22, ty + 28, "WHAT THE MODEL IS ALLOWED TO DO HERE", size=12, weight="700",
       fill="#e9e9e6", ls="0.7")
s.text(LX + 22, ty + 52, "Select a flow. Fill in a device and a subject. That is the whole surface.",
       size=11.8, fill="#c9c9c4")
s.text(LX + 22, ty + 72, "It cannot author a rung, invent a command, or decide what 'broken' means —",
       size=11.3, fill="#8f8f88")
s.text(LX + 22, ty + 87, "so growth is new items in a list, never new rules to reason about.",
       size=11.3, fill="#8f8f88")

# ---------------- right: the ladder ----------------
RX, RW = 712, W - 712 - 48
s.text(RX, 132, "THE LADDER — flow: bgp_session", size=13.5, weight="700", ls="0.9")
s.text(RX + w_sans("THE LADDER — flow: bgp_session", 13.5) * 1.16 + 58, 132,
       "walked top to bottom, always", size=11.3, fill=MUTED)

# name/scope/collects come straight from FLOWS["bgp_session"].descent
# (facts.bgp_session_ladder()); the question and its rationale are
# hand-written prose the code has no field for, keyed by rung name so a
# ladder change that adds, removes or renames a rung fails this lookup
# instead of silently rendering a stale question beside a real rung.
_RUNG_PROSE = {
    "bgp_session": ("Is the session Established?", "the symptom you were paged about"),
    "transport": ("Is there a TCP session under it?",
                  "separates 'BGP is unhappy' from 'nothing is connected'"),
    "route_to_peer": ("Is there a route to the peer address?",
                       "no route → the session cannot form, whatever BGP says"),
    "igp_adjacency": ("Does the far device have IGP adjacencies?",
                       "the walk crosses to the OTHER device here"),
    "interface": ("Are the path's member links up?",
                  "ANY_HEALTHY over the member set — one live member is enough"),
}
RUNGS = []
for rung in facts.bgp_session_ladder():
    if rung["name"] not in _RUNG_PROSE:
        raise SystemExit(f"d6.py: rung {rung['name']!r} has no entry in _RUNG_PROSE — add one.")
    q, why = _RUNG_PROSE[rung["name"]]
    RUNGS.append((rung["name"], rung["scope"], rung["collects"], q, why))
ry = 152
for i, (name, scope, collects, q, why) in enumerate(RUNGS):
    last = i == len(RUNGS) - 1
    h = 88
    s.rect(RX, ry, RW, h, fill=REDBG if last else "#ffffff",
           stroke="#f0bfb8" if last else LINE, rx=10)
    s.rect(RX, ry, 4, h, fill=RED if last else "#c6c6c0", rx=2)
    s.text(RX + 24, ry + 26, f"{i+1}.", size=12.5, family=MONO, fill=FAINT)
    s.text(RX + 50, ry + 26, name, size=13.5, weight="700", family=MONO,
           fill=RED if last else INK)
    sw_ = w_sans(scope, 9.5) + 18
    s.rect(RX + 50 + w_mono(name, 13.5) + 14, ry + 14, sw_, 17,
           fill=SLATEBG if scope == "LOCAL" else BLUEBG, rx=8)
    s.text(RX + 50 + w_mono(name, 13.5) + 14 + sw_ / 2, ry + 26, scope, size=9.5,
           fill=MUTED if scope == "LOCAL" else BLUE, anchor="middle", weight="700")
    s.text(RX + RW - 20, ry + 26, "BROKEN", size=11.5, family=MONO, weight="700",
           fill=RED, anchor="end")
    s.text(RX + 50, ry + 48, q, size=12, fill=INK)
    s.text(RX + 50, ry + 66, why, size=10.7, fill=MUTED)
    s.text(RX + RW - 20, ry + 48, "collects: " + collects, size=10.2, family=MONO,
           fill=FAINT, anchor="end")
    if last:
        s.text(RX + RW - 20, ry + 68, "←  THE CAUSE — lowest broken rung", size=11,
               weight="700", fill=RED, anchor="end")
    if not last:
        s.text(RX + 26, ry + h + 11, "▼ broken → keep going (the cause may be deeper)",
               size=10, fill=MUTED)
    ry += h + 22

# rules card
s.rect(RX, ry + 6, RW, 216, fill="#ffffff", stroke=LINE, rx=11)
s.text(RX + 20, ry + 32, "THE FOUR RULES OF THE WALK", size=12.5, weight="700", ls="0.8")
RULES = [
 (GREEN, "healthy", "stop descending this branch — nothing below it is the cause"),
 (RED, "broken", "keep going. The FIRST broken rung is the symptom; the LOWEST is the fault"),
 (AMBER, "unevaluated", "STOP and say so. A rung that could not be judged is never read as healthy"),
 (BLUE, "no fault on path", "if nothing on the dependency path is broken, that IS the answer — faults\nfound elsewhere are recorded as observations, not promoted to a cause"),
]
yy = ry + 58
for col, word, meaning in RULES:
    bw = w_mono(word, 11.5) + 20
    s.rect(RX + 20, yy - 12, bw, 20, fill=col, rx=10)
    s.text(RX + 20 + bw / 2, yy + 2, word, size=10.5, fill="#fff", weight="700",
           anchor="middle", family=MONO)
    for j, ln in enumerate(meaning.split("\n")):
        s.text(RX + 34 + bw, yy + 2 + j * 14, ln, size=11, fill=MUTED)
    yy += 34 + (14 if "\n" in meaning else 0)
s.line(RX + 20, yy - 6, RX + RW - 20, yy - 6, stroke=LINE)
s.text(RX + 20, yy + 14, "Every verdict above is code comparing parsed fields. No scoring, no",
       size=11, fill=INK)
s.text(RX + 20, yy + 30, "thresholds a model can nudge, no place for a plausible answer to enter.",
       size=11, fill=INK)

s.text(48, H - 26,
       "This is the whole thesis: make the answer a walk over declared data, and the model's job shrinks to navigation — "
       "which is why a 4-billion-parameter model on a laptop can drive it.",
       size=12, fill=MUTED)
s.save("06-descent.svg")
print("ok")
