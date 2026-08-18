from svgkit import *

W, H = 1580, 990
s = Svg(W, H)
header(s, "The trust boundary — how device text reaches a model",
       "An unauthenticated attacker can write into device output: a syslog line, a BGP reset reason, an interface description. "
       "Everything here exists so that text can never become an instruction.",
       "invariant 4 · B-467 / B-470 / B-481")

# ---------------- pipeline ----------------
py_ = 126
def stage(x, w, title, col, bg, lines, foot=None, h=232):
    s.rect(x, py_, w, h, fill=bg, stroke=col if bg != "#ffffff" else LINE, rx=11)
    s.text(x + 18, py_ + 26, title, size=12.5, weight="700", fill=col, ls="0.8")
    yy = py_ + 50
    for kind, txt in lines:
        if kind == "h":
            s.text(x + 18, yy, txt, size=11.4, weight="650"); yy += 18
        elif kind == "m":
            s.text(x + 18, yy, txt, size=11, family=MONO, fill=INK); yy += 17
        elif kind == "b":
            s.text(x + 18, yy, "•", size=11, fill=col)
            s.text(x + 32, yy, txt, size=10.9, fill=MUTED); yy += 16
        elif kind == "i":
            s.text(x + 32, yy, txt, size=10.7, fill=MUTED); yy += 15
        else:
            s.text(x + 18, yy, txt, size=10.9, fill=MUTED); yy += 15
    if foot:
        s.text(x + 18, py_ + h - 14, foot, size=10.5, fill=col, weight="600")

stage(48, 320, "1 · WHAT THE DEVICE SAYS", RED, REDBG, [
    ("t", "Raw command output. Four fields are"),
    ("t", "attacker-influenceable in normal operation:"),
    ("m", "logging.text"), ("m", "logging.code"),
    ("m", "bgp_neighbor.last_reset_reason"),
    ("m", "interface.description"),
    ("t", ""),
    ("t", "Anyone who can make a device log a line"),
    ("t", "can choose that line's contents."),
], "measured: 17,916 chars of prose in one run")

s.line(378, py_ + 116, 404, py_ + 116, stroke="#a8a8a2", sw=1.6, marker="arw")

stage(412, 300, "2 · PARSED", INK, "#ffffff", [
    ("h", "Parsing bounds structure, not content."),
    ("t", ""),
    ("t", "A parser guarantees the SHAPE of a record:"),
    ("t", "which fields exist, of what type. It makes no"),
    ("t", "promise at all about what the strings say."),
    ("t", ""),
    ("t", "So a parsed record is not a safe record —"),
    ("t", "that is the whole reason step 3 exists."),
], "563 committed captures parse this way")

s.line(742, py_ + 116, 768, py_ + 116, stroke="#a8a8a2", sw=1.6, marker="arw")

stage(776, 402, "3 · THE PROJECTOR — model_egress.py", PURPLE, PURPBG, [
    ("h", "Three rules, applied to every projection:"),
    ("b", "RAW_TEXT_KEYS → withheld with a count."),
    ("i", "the raw buffer never crosses: \"748 chars withheld\""),
    ("b", "FREE_TEXT_FIELDS → quoted, never bare."),
    ("i", "wrapped in <<<DEVICE-TEXT untrusted>>> … <<<END>>>,"),
    ("i", "and any embedded delimiter is stripped first."),
    ("b", "ERROR_KINDS → classified and REBUILT."),
    ("i", "an error is matched to a known-safe phrase and"),
    ("i", "re-emitted; the original string is never forwarded."),
], "plus a per-projection character budget")

s.line(1208, py_ + 116, 1234, py_ + 116, stroke="#a8a8a2", sw=1.6, marker="arwp")

stage(1242, W - 1242 - 48, "4 · WHAT A MODEL SEES", PURPLE, "#ffffff", [
    ("t", "Typed fields, plus device prose that is"),
    ("t", "explicitly framed as untrusted data:"),
    ("t", ""),
    ("m", "<<<DEVICE-TEXT untrusted>>>"),
    ("m", "%BGP-5-ADJCHANGE: neighbor"),
    ("m", "10.255.0.12 Down BGP Notification"),
    ("m", "<<<END-DEVICE-TEXT>>>"),
    ("t", ""),
    ("t", "Content preserved. Authority removed."),
])

# ---------------- five paths ----------------
ay = py_ + 258
s.text(48, ay, "THE FIVE PATHS FROM DEVICE TEXT TO A MODEL", size=13.5, weight="700", ls="0.9")
s.text(48 + w_sans("THE FIVE PATHS FROM DEVICE TEXT TO A MODEL", 13.5) * 1.16 + 58, ay,
       "an invariant that holds for every internal caller is not an invariant — it is a convention that has not yet met a new consumer",
       size=11.5, fill=MUTED)
ay += 16

PATHS = [
 ("prompt_library.py", "build_correlate_prompt()", "the log-correlation prompt", "PROJECTED", GREEN, GREENBG,
  "Structurally cannot leak: the module never holds raw text in the first place."),
 ("llm_analysis.py", "build_analysis_prompt()", "nettools analyze / --fabric", "PROJECTED", GREEN, GREENBG,
  "Projected before the prompt is assembled, both single-device and fabric."),
 ("evidence_budget.py", "budget_device_evidence()", "fitting evidence to a window", "PROJECTED", GREEN, GREENBG,
  "Trimming happens after projection, so trimming can never re-expose raw text."),
 ("agent_loop.py", "tool results → messages", "nettools agent", "PROJECTED", GREEN, GREENBG,
  "Every tool result is projected before it is appended to the conversation."),
 ("mcp_server/boundary.py", "sanitize() on registration", "every MCP tool, both surfaces", "WAS THE GAP", RED, REDBG,
  "Withheld raw `commands` — but passed free text UNMARKED. Found 2026-08-18 by two independent reviewers. Fixed: B-481."),
]
cy = ay + 12
for mod, fn, what, verdict, col, bg, note in PATHS:
    s.rect(48, cy, W - 96, 52, fill=bg if col == RED else "#ffffff", stroke="#f0bfb8" if col == RED else LINE, rx=9)
    s.rect(48, cy, 4, 52, fill=col, rx=2)
    s.text(72, cy + 22, mod, size=12.5, family=MONO, weight="600")
    s.text(72, cy + 40, fn, size=10.8, family=MONO, fill=FAINT)
    s.text(330, cy + 22, what, size=11.8, fill=INK)
    s.text(330, cy + 40, note, size=10.8, fill=MUTED)
    bw = w_sans(verdict, 11) + 26
    s.rect(W - 48 - bw - 12, cy + 15, bw, 22, fill=col, rx=11)
    s.text(W - 48 - bw / 2 - 12, cy + 30, verdict, size=11, fill="#fff", weight="700", anchor="middle")
    cy += 58

# ---------------- the honest residual ----------------
cy += 6
s.rect(48, cy, 760, 190, fill=AMBERBG, stroke="#eed7ae", rx=11)
s.text(68, cy + 28, "WHAT THIS DOES NOT CLAIM", size=12.5, weight="700", fill=AMBER, ls="0.8")
for i, ln in enumerate([
  "Delimiters mitigate steering. They do not eliminate it. A sufficiently persuasive",
  "line inside the quoted block may still influence how a model words its paraphrase.",
  "",
  "The real bound is on the OUTPUT side, and it is structural rather than textual:",
  "the diagnosis was already computed by code before any model was invoked, and the",
  "model's reply cannot become an action — it cannot run a command, reach a device,",
  "or change an exit code. So steering the prose does not steer the outcome.",
]):
    s.text(68, cy + 54 + i * 18, ln, size=11.5, fill=INK if i in (3,) else MUTED,
           weight="600" if i == 3 else "400")

s.rect(828, cy, W - 828 - 48, 190, fill="#ffffff", stroke=LINE, rx=11)
s.text(848, cy + 28, "HOW THE GAP WAS FOUND — AND WHY IT MATTERS", size=12.5, weight="700", ls="0.8")
for i, (b, ln) in enumerate([
  (True,  "B-467 shipped the projector for four paths. The MCP boundary was a fifth,"),
  (False, "and its scope never listed it — so it inherited nothing."),
  (True,  "Two reviewers found it independently on the same day: one by canary through"),
  (False, "the registered tools, one by a sweep over every egress path. Both surfaces."),
  (True,  "The convergence is the signal. One reviewer finding it is a report;"),
  (False, "two finding it separately is a measurement."),
  (True,  "Fixed the same day, regression-tested, and mutation-verified — the test is"),
  (False, "proven to fail when the guard is removed, so it cannot rot into a false pass."),
]):
    s.text(848, cy + 54 + i * 17, ln, size=11.3, fill=INK if b else MUTED)

s.text(48, H - 26,
       "The design rule underneath all of this: bound what the model can DO, not what it can be told. "
       "Quoting is defence in depth; the answer being computed before the model runs is the actual guarantee.",
       size=12, fill=MUTED)
s.save("03-trust-boundary.svg")
print("ok")
