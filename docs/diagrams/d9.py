import facts
from svgkit import *

# ---------------- measured facts (see facts.py) ----------------
tool_classes = facts.classic_mcp_tool_class_counts()
kinds = facts.reasoning_gate_candidate_kinds()
checks = facts.grounding_check_names()
egress = facts.model_egress_table_sizes()
gate_consumers = facts.reasoning_gate_consumers()
if gate_consumers:
    raise SystemExit(
        f"d9.py: reasoning_gate.py now has live, non-test consumers {gate_consumers} — "
        "the 'built, not yet wired' framing below is stale; update the diagram before regenerating."
    )

for _relpath, _pattern, _ctx in [
    ("src/agent_nettools/reasoning_gate.py", r"class NarrowRequest", "d9.py MAY card 2"),
    ("src/agent_nettools/reasoning_gate.py", r"class Stop", "d9.py MAY card 2"),
    ("src/agent_nettools/reasoning_gate.py", r"def parse_decision\(", "d9.py MAY card 2"),
    ("src/agent_nettools/grounding.py", r"def ground_report\(", "d9.py MAY card 3"),
    ("src/agent_nettools/agent_loop.py", r"def _execute_tool\(", "d9.py MAY card 1"),
    ("src/agent_nettools/platforms.py", r"APPROVED_COMMANDS", "d9.py MAY NOT card 1"),
    ("src/agent_nettools/reasoning_gate.py", r"def __post_init__", "d9.py MAY NOT card 2"),
    ("src/agent_nettools/grounding.py", r"def check_identifier_containment\(", "d9.py MAY NOT card 2"),
    ("src/agent_nettools/grounding.py", r"class GroundingFailure", "d9.py MAY NOT card 4"),
    ("src/agent_nettools/investigation.py", r"paraphrase = decoded if paraphrase_grounding\.ok else None",
     "d9.py MAY NOT card 4"),
]:
    facts.assert_contains(_relpath, _pattern, _ctx)

W, H = 1580, 1170
s = Svg(W, H)
header(s, "The model boundary",
       "This build's central claim, drawn: every place a model can influence what happens next, and the "
       "code — not the prose — that stops it going further.",
       "reasoning_gate.py · grounding.py · model_egress.py")

COLW = (W - 96 - 24) / 2
LX, RXc = 48, 48 + COLW + 24

# ---------------- MAY (left) ----------------
s.rect(LX, 116, COLW, 8, fill=GREEN, rx=4)
s.text(LX, 146, "A MODEL MAY", size=15, weight="700", fill=GREEN, ls="0.6")

MAY = [
 ("Select a tool, by name", [
    f"Pick one of {tool_classes['total']} registered MCP tools, or one of the 6 names",
    "agent_loop.py's own tool loop recognises. Every device-touching",
    "argument re-enters the same validated function a CLI caller would.",
 ], "agent_loop._execute_tool() · mcp.tool()"),
 ("Ask the gate to look closer, by index", [
    f"Pick a position in a list CODE already built ({', '.join(kinds)}) —",
    "never name a target. The index is the model's entire lever.",
 ], "reasoning_gate.parse_decision()"),
 ("Paraphrase a report code already reached", [
    "Restate a conclusion in prose. The conclusion itself was computed",
    "by descent.py before any model was invoked, and stays unchanged",
    "whether the paraphrase is graded pass or fail.",
 ], "grounding.ground_report()"),
]
cy = 168
for title, body, code in MAY:
    h = 30 + len(body) * 15 + 18
    s.rect(LX, cy, COLW, h, fill=GREENBG, stroke="#b9dcc5", rx=10)
    s.text(LX + 18, cy + 24, title, size=12.6, weight="650", fill=GREEN)
    for i, ln in enumerate(body):
        s.text(LX + 18, cy + 44 + i * 15, ln, size=10.6, fill=INK)
    s.text(LX + 18, cy + h - 10, code, size=9.8, family=MONO, fill="#3f9e63")
    cy += h + 12

# ---------------- MAY NOT (right) ----------------
s.rect(RXc, 116, COLW, 8, fill=RED, rx=4)
s.text(RXc, 146, "A MODEL MAY NOT", size=15, weight="700", fill=RED, ls="0.6")

MAYNOT = [
 ("Author a command", [
    "Every device string is built from platforms.APPROVED_COMMANDS or a",
    "templates.py template, re-rendered from a typed object. There is no",
    "run_command(device, text) anywhere in this package to call.",
 ], "platforms.py · templates.py"),
 ("Name a target that was not observed", [
    "NarrowRequest.target accepts only a Candidate; a bare string raises",
    "TypeError before the object exists. check_identifier_containment()",
    "refuses any IPv4/interface/device token in prose that does not",
    "resolve to what the descent actually observed.",
 ], "NarrowRequest.__post_init__ · check_identifier_containment()"),
 ("Return a verdict", [
    "NarrowRequest and Stop have no field a cause or finding could",
    "occupy — not filtered out, unrepresentable. descent.py has already",
    "reached the verdict before a model runs at all.",
 ], "reasoning_gate.GateDecision · descent.py"),
 ("Have its prose emitted if grounding refuses", [
    "paraphrase = decoded if paraphrase_grounding.ok else None. A",
    "GroundingFailure carries a fixed template message and a capped",
    "locus string — no field it owns can hold the refused sentence.",
 ], "investigation.py · GroundingFailure"),
]
cy = 168
for title, body, code in MAYNOT:
    h = 30 + len(body) * 15 + 18
    s.rect(RXc, cy, COLW, h, fill=REDBG, stroke="#f0bfb8", rx=10)
    s.text(RXc + 18, cy + 24, title, size=12.6, weight="650", fill=RED)
    for i, ln in enumerate(body):
        s.text(RXc + 18, cy + 44 + i * 15, ln, size=10.6, fill=INK)
    s.text(RXc + 18, cy + h - 10, code, size=9.8, family=MONO, fill="#c0392b")
    cy += h + 12

# ---------------- detail band: THE GATE / GROUNDING ----------------
dy = 168 + 3 * 0  # computed below once both columns are laid out; use max
# left column ended at cy for MAY (3 cards), right at cy for MAY NOT (4 cards) —
# recompute both explicitly so the detail band starts below the taller one.
may_bottom = 168 + sum(30 + len(b) * 15 + 18 + 12 for _, b, _ in MAY)
maynot_bottom = 168 + sum(30 + len(b) * 15 + 18 + 12 for _, b, _ in MAYNOT)
dy = max(may_bottom, maynot_bottom) + 6

s.line(48, dy, W - 48, dy, stroke=LINE)
dy += 22

DETW = (W - 96 - 24) / 2
GX, GX2 = 48, 48 + DETW + 24

s.rect(GX, dy, DETW, 400, fill=PURPBG, stroke="#d5c6f5", rx=11)
s.text(GX + 18, dy + 26, "THE GATE — two shapes, nothing else parses", size=12.3, weight="700", fill=PURPLE, ls="0.6")
s.pill(GX + 18, dy + 38, "NarrowRequest(target: Candidate)", None, fill="#fff", stroke="#d5c6f5",
       tcol=PURPLE, h=22, size=10.3)
s.pill(GX + 18 + 250, dy + 38, "Stop(reason: str)", None, fill="#fff", stroke="#d5c6f5",
       tcol=PURPLE, h=22, size=10.3)
for i, ln in enumerate([
  "\"look closer at this\"                    \"nothing more to ask\" — reason is the",
  "                                            ONE field either shape lets a model's",
  "                                            own prose occupy. It changes no code.",
]):
    s.text(GX + 18, dy + 78 + i * 14, ln, size=9.6, family=MONO, fill=MUTED)
gy = dy + 128
s.text(GX + 18, gy, "Candidates are code-enumerated (B-102), never model text:", size=11, weight="600")
s.text(GX + 18, gy + 16,
       f"{len(kinds)} kinds today — " + " · ".join(kinds) + " — read from exactly the",
       size=10.3, fill=MUTED)
s.text(GX + 18, gy + 31, "evidence sections a descent's own checks already read, device-listed order.",
       size=10.3, fill=MUTED)
gy += 54
s.text(GX + 18, gy, "parse_decision(raw, candidates) refuses, never repairs:", size=11, weight="600")
for i, ln in enumerate([
  "an index outside range(len(candidates)) — both ends checked explicitly",
  "(no Python-style candidates[-1] silently substituting the last one);",
  "a bool masquerading as int (bool is an int subclass in Python);",
  "any shape besides {\"decision\": \"stop\"|\"narrow\", ...} — GateRefusal.",
]):
    s.text(GX + 18, gy + 17 + i * 14, "•  " + ln, size=10.2, fill=MUTED)
gy += 17 + 4 * 14 + 12
s.rect(GX + 18, gy, DETW - 36, 30, fill=AMBERBG, stroke="#eed7ae", rx=7)
s.text(GX + 30, gy + 19,
       "BUILT + TESTED, NOT YET WIRED — zero non-test callers; B-103 (the narrowing pass) is unbuilt.",
       size=10, fill=AMBER, weight="600")

s.rect(GX2, dy, DETW, 400, fill="#ffffff", stroke=LINE, rx=11)
s.text(GX2 + 18, dy + 26, "GROUNDING — ground_report() composes 7 checks", size=12.3, weight="700", ls="0.6")
_CHECK_MEANING = {
    "check_grounding": "citation — every observation's evidence_key must be real",
    "check_chain_coverage": "chain — every rung read, and the causal chain, must be cited",
    "check_identifier_containment": "containment — every named IPv4/interface/device must be observed",
    "check_recommendation_closed": "next_check must be verbatim from render.next_check_for(), never invented",
    "check_no_invented_cause": "scans for invented-cause language whenever descent.cause is None",
    "check_absence_coverage": "correlation only — an absence claim needs coverage behind it",
    "check_timeline_citations": "correlation only — every timeline entry must cite a real record",
}
gy = dy + 46
for name in checks:
    meaning = _CHECK_MEANING.get(name)
    if meaning is None:
        raise SystemExit(f"d9.py: grounding check {name!r} has no entry in _CHECK_MEANING — add one.")
    s.text(GX2 + 18, gy, name + "()", size=10.6, family=MONO, weight="600", fill=INK)
    gy += 14
    s.text(GX2 + 30, gy, meaning, size=10, fill=MUTED)
    gy += 22
gy += 4
s.line(GX2 + 18, gy, GX2 + DETW - 18, gy, stroke=LINE)
gy += 18
s.text(GX2 + 18, gy, "GroundingFailure(kind, locus, detail) — detail is always this module's own",
       size=10.3, weight="600")
gy += 15
s.text(GX2 + 18, gy,
       "fixed template text; locus is a length-capped identifier only. No field on the",
       size=10.3, fill=MUTED)
gy += 15
s.text(GX2 + 18, gy, "object can hold the model's own sentence — a failure cannot leak the refusal.",
       size=10.3, fill=MUTED)

# ---------------- footer band: the projector's tables ----------------
fy = dy + 420
s.rect(48, fy, W - 96, 66, fill=SLATEBG, stroke=LINE, rx=10)
s.text(68, fy + 24, "EVERYTHING ABOVE STILL CROSSES THE PROJECTOR FIRST", size=11.5, weight="700", ls="0.5")
s.text(68, fy + 44,
       f"model_egress.py — RAW_TEXT_KEYS: {egress['raw_text_keys']}  ·  FREE_TEXT_FIELDS: {egress['free_text_fields']}  ·  "
       f"ERROR_KINDS: {egress['error_kinds']}  —  grown, not shrunk, as evidence sources and gates were added; "
       "diagram 3 has the full mechanism.",
       size=10.6, fill=MUTED)

s.text(48, H - 26,
       "The pattern repeats at every boundary above: not a rule asking to be followed, but a shape a violation "
       "cannot be expressed in — a TypeError, a refused index, a field that does not exist.",
       size=12, fill=MUTED)
s.save("09-model-boundary.svg")
print("ok")
