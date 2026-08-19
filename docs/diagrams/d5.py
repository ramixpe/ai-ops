import facts
from svgkit import *

guard_n = facts.guard_count()
frozen_n = facts.frozen_file_count()
tests_n = facts.tests_passed()
skipped_n = facts.tests_skipped()
findings_n = facts.findings_count()
fixtures_stat = facts.fixture_stats()
src_n = facts.src_lines()
tests_lines_n = facts.tests_lines()
backlog_n = facts.backlog_total()
_ratio = tests_lines_n / src_n if src_n else 0
_ratio_label = "near 1:1" if 0.8 <= _ratio <= 1.25 else f"{_ratio:.1f}:1"

W, H = 1580, 940
s = Svg(W, H)
header(s, "Where we are",
       "Part 1 (MVP-0) is built, reviewed and green. What remains before publication needs a lab window and a human — not more code.",
       f"{facts.last_commit_date()} · {facts.commit_count()} commits")

# ---------- journey ----------
jy = 132
STAGES = [("Part 0", "setup, frozen\nbaseline", "done"),
          ("Part 1 · MVP-0", "the descent, evidence,\ngrounding, MCP", "done"),
          ("Reviews", "3 external + a 5-lens\nholistic review", "done"),
          ("Lab window", "round 8b · round 6 + Q1\nMCP re-test", "now"),
          ("Publication", "the ai-ops repo", "next"),
          ("Part 2 · MVP-1", "config axis, more flows,\nmulti-turn", "later")]
seg = (W - 96) / len(STAGES)
for i, (name, sub, st) in enumerate(STAGES):
    x = 48 + i * seg
    col = {"done": GREEN, "now": AMBER, "next": BLUE, "later": FAINT}[st]
    bg = {"done": GREENBG, "now": AMBERBG, "next": BLUEBG, "later": "#ffffff"}[st]
    s.rect(x, jy, seg - 12, 66, fill=bg, stroke=col, rx=9,
           dash="4 3" if st == "later" else None)
    s.text(x + 16, jy + 24, name, size=13, weight="700", fill=col)
    for j, ln in enumerate(sub.split("\n")):
        s.text(x + 16, jy + 42 + j * 14, ln, size=10.5, fill=MUTED)
    mark = {"done": "✓ complete", "now": "◆ you are here", "next": "→ next", "later": "planned"}[st]
    s.text(x + seg - 26, jy + 24, mark, size=10.5, fill=col, weight="700", anchor="end")
    if i < len(STAGES) - 1:
        s.line(x + seg - 11, jy + 33, x + seg - 2, jy + 33, stroke="#c6c6c0", sw=1.5)

# ---------- KPI tiles ----------
ky = jy + 88
# Wall-clock suite duration is deliberately not shown here: on a machine
# shared with other concurrent work (this repo's own agent harness runs many
# worktrees at once) it swings widely enough between otherwise-identical
# runs -- 23s to 45s, observed back to back on an unchanged tree -- that no
# fixed rounding bucket keeps two fresh regenerations byte-identical. Every
# other number on this tile is a property of the tree; that one would only
# ever be a property of how busy the box happened to be.
KPI = [(facts.fmt(tests_n), "tests passing", f"{skipped_n} skipped · zero network", GREEN),
       (f"{guard_n} / {guard_n}", "mutation guards hold", "each proven to fail when its guard goes", GREEN),
       (f"{frozen_n} / {frozen_n}", "frozen files intact", "byte-identical to the pre-build baseline", GREEN),
       (facts.fmt(findings_n), "findings recorded", "append-only; corrections are appended", BLUE),
       (facts.fmt(fixtures_stat["captures"]), "committed captures",
        f"{fixtures_stat['devices']} devices × {fixtures_stat['labels']} labels — the offline lab", BLUE),
       (facts.fmt(src_n), "lines of source", f"+{facts.fmt(tests_lines_n)} lines of tests — {_ratio_label}", MUTED)]
tw = (W - 96 - 5 * 12) / 6
for i, (big, label, sub, col) in enumerate(KPI):
    x = 48 + i * (tw + 12)
    s.rect(x, ky, tw, 92, fill="#ffffff", stroke=LINE, rx=10)
    s.rect(x, ky, tw, 3, fill=col, rx=1.5)
    s.text(x + 18, ky + 42, big, size=27, weight="700", fill=col)
    s.text(x + 18, ky + 62, label, size=12, weight="600")
    s.text(x + 18, ky + 79, sub, size=9.8, fill=MUTED)

# ---------- backlog ----------
by = ky + 114
BW = 690
s.rect(48, by, BW, 336, fill="#ffffff", stroke=LINE, rx=11)
s.text(68, by + 28, "THE BACKLOG, RECONCILED", size=13, weight="700", ls="0.8")
s.text(68, by + 47, f"{backlog_n} items. The number that matters is not how few are open —", size=11, fill=MUTED)
s.text(68, by + 62, "it is that every state is one somebody can defend.", size=11, fill=MUTED)

# BACKLOG.md's own vocabulary documents 5 states; two more (CLOSED-AS-REFUSED,
# CLOSED-AS-MEASURED) appear once each in practice. facts.backlog_buckets()
# folds anything past the original 4 into one "OTHER" bucket, so this bar
# keeps its original shape and still accounts for every row.
_STATE_STYLE = {
    "DONE": (GREEN, "shipped and verified"),
    "DEFERRED": (BLUE, "examined, and each carries its unblocking condition"),
    "OPEN": (AMBER, "real work, not yet started"),
    "BLOCKED": (RED, "cannot proceed — hardware, or outside this repo"),
    "OTHER": (TEAL, "out-of-scope by decision, or closed some way other than DONE"),
}
STATES = [(name, n, *_STATE_STYLE[name]) for name, n in facts.backlog_buckets()]
total = sum(n for _, n, _, _ in STATES)
bx, bary = 68, by + 82
barw = BW - 40
for name, n, col, _ in STATES:
    w = barw * n / total
    s.rect(bx, bary, w, 26, fill=col, rx=0)
    s.text(bx + w / 2, bary + 17, str(n), size=12, fill="#fff", weight="700", anchor="middle")
    bx += w
s.rect(68, bary, barw, 26, fill="none", stroke="#ffffff", rx=0, sw=0)
ly = bary + 48
for name, n, col, desc in STATES:
    s.rect(68, ly - 10, 11, 11, fill=col, rx=2)
    s.text(86, ly, name, size=11.5, weight="650", fill=col)
    s.text(86 + 74, ly, f"{n}", size=11.5, weight="700", family=MONO)
    s.text(86 + 104, ly, desc, size=10.8, fill=MUTED)
    ly += 21
s.line(68, ly + 2, 68 + barw, ly + 2, stroke=LINE)
s.text(68, ly + 24, "DEFERRED exists because the vocabulary was one state short:", size=11, weight="600")
s.text(68, ly + 40, "BLOCKED means cannot. DEFERRED means chose not to — and here is", size=10.8, fill=MUTED)
s.text(68, ly + 55, "what would change that. Eight well-reviewed items had been labelled", size=10.8, fill=MUTED)
s.text(68, ly + 70, "'unverified', which meant the opposite of what was true.", size=10.8, fill=MUTED)

# ---------- operator gate ----------
ox = 48 + BW + 20
OW = W - ox - 48
s.rect(ox, by, OW, 336, fill=AMBERBG, stroke="#eed7ae", rx=11)
s.text(ox + 20, by + 28, "WHAT IS WAITING ON YOU", size=13, weight="700", fill=AMBER, ls="0.8")
s.text(ox + 20, by + 47, "In runbook order. None of it is code — it is measurement that", size=11, fill=MUTED)
s.text(ox + 20, by + 62, "needs a live fabric, and one thing that needs your judgement.", size=11, fill=MUTED)
GATES = [("1", "MCP re-test Q5 / Q6", "no lab needed — ask the two questions,\nsend me the transcript"),
         ("2", "./scripts/preflight.sh", "seals the tree before the window opens"),
         ("3", "Round 8b", "the re-run with the fixed sampler; closes\nor bounds B-463"),
         ("4", "Round 6 (fault option 8, --max-hold 45)", "and ask Q1 during ITS window, not 8b's —\ntool selection needs a stable fabric"),
         ("5", "Then: B-113 default flip", "only after the A/B lands. Doing it first\nwould make the measurement impossible")]
gy = by + 84
for n, title, sub in GATES:
    s.badge(ox + 32, gy + 2, n, col=AMBER, r=10)
    s.text(ox + 50, gy + 6, title, size=11.8, weight="650")
    for i, ln in enumerate(sub.split("\n")):
        s.text(ox + 50, gy + 22 + i * 14, ln, size=10.5, fill=MUTED)
    gy += 22 + 14 * len(sub.split("\n")) + 8

# ---------- recent history ----------
hy = by + 356
s.rect(48, hy, W - 96, 190, fill="#ffffff", stroke=LINE, rx=11)
s.text(68, hy + 28, "THE LAST FIVE WAVES — WHAT GOT BUILT, AND WHAT EACH ONE TAUGHT", size=13, weight="700", ls="0.8")
# The first four waves are historical snapshots of past commits -- not
# properties of the current tree, so re-measuring them from HEAD would just
# be a different kind of wrong. Only "Now" describes the tree this generator
# is looking at, so only its metric is measured.
WAVES = [("FIX-PLAN", "6 agents", "1,820 → 1,916 tests",
          "The merge gate caught what reading did not:\na test that proved the helper, not the wiring."),
         ("OPS wave", "solo", "→ 1,954 tests",
          "Four agents lost to a spend limit; built solo\nto the same specs. Where both defects landed."),
         ("Docs lens", "1 agent", "20 drifts",
          "Two contradictory totals six lines apart, and a\nread-first handover 35 commits stale."),
         ("Holistic", "4 lenses", "2 security fixes",
          "Two reviewers, working blind to each other,\nfound the same two defects. That is a measurement."),
         ("Now", "—", f"{facts.fmt(tests_n)} tests · {guard_n} guards",
          "Everything green, everything pushed. The next\nmove is a lab window, not a commit.")]
cw = (W - 96 - 40 - 4 * 12) / 5
for i, (name, who, metric, lesson) in enumerate(WAVES):
    x = 68 + i * (cw + 12)
    last = i == len(WAVES) - 1
    s.rect(x, hy + 46, cw, 124, fill=GREENBG if last else "#fdfdfc",
           stroke="#b9dcc5" if last else LINE, rx=9)
    s.text(x + 14, hy + 68, name, size=12.5, weight="700", fill=GREEN if last else INK)
    s.text(x + cw - 14, hy + 68, who, size=10, fill=FAINT, anchor="end", family=MONO)
    s.text(x + 14, hy + 86, metric, size=11, family=MONO, fill=BLUE if not last else GREEN)
    for j, ln in enumerate(lesson.split("\n")):
        s.text(x + 14, hy + 108 + j * 14, ln, size=10.2, fill=MUTED)

s.text(48, H - 26,
       "The honest summary: the code is done and defended; the claims that still need evidence are the ones only a live fault can produce.",
       size=12, fill=MUTED)
s.save("05-current-state.svg")
print("ok")
