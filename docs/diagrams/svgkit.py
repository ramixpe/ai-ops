"""Tiny SVG layout helper — enough for boxes, pills, text and arrows."""

SANS = "ui-sans-serif,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"

# palette
BG      = "#fbfbf9"
INK     = "#17171a"
MUTED   = "#6f6f6a"
FAINT   = "#9a9a94"
LINE    = "#d8d8d2"
BLUE    = "#1f5fd0"
BLUEBG  = "#eaf1fd"
GREEN   = "#15803d"
GREENBG = "#e8f5ec"
RED     = "#c0392b"
REDBG   = "#fdecea"
AMBER   = "#b45309"
AMBERBG = "#fdf3e3"
PURPLE  = "#6d28d9"
PURPBG  = "#f2ecfd"
SLATEBG = "#eef0f3"
TEAL    = "#0f766e"
TEALBG  = "#e6f4f2"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def w_sans(s, size):
    return len(str(s)) * size * 0.545


def w_mono(s, size):
    return len(str(s)) * size * 0.605


class Svg:
    def __init__(self, w, h, bg=BG):
        self.w, self.h = w, h
        self.parts = []
        self.defs = []
        self.parts.append(f'<rect width="{w}" height="{h}" fill="{bg}"/>')

    def raw(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, fill="none", stroke="none", rx=8, sw=1, dash=None, op=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}{o}/>')

    def text(self, x, y, s, size=13, fill=INK, weight="400", anchor="start",
             family=SANS, ls=None, op=None):
        l = f' letter-spacing="{ls}"' if ls else ""
        o = f' opacity="{op}"' if op else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{l}{o}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke=LINE, sw=1, dash=None, marker=None, op=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{marker})"' if marker else ""
        o = f' opacity="{op}"' if op else ""
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}{m}{o}/>')

    def path(self, d, stroke=LINE, sw=1, fill="none", dash=None, marker=None):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{marker})"' if marker else ""
        self.parts.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{da}{m}/>')

    def pill(self, x, y, label, sub=None, fill="#fff", stroke=LINE, tcol=INK,
             size=12.5, subcol=MUTED, pad=11, h=26, family=MONO, weight="500"):
        """A rounded pill sized to its text. Returns its width."""
        wl = (w_mono(label, size) if family == MONO else w_sans(label, size))
        ws = w_sans(sub, size - 2) + 8 if sub else 0
        w = wl + ws + pad * 2
        self.rect(x, y, w, h, fill=fill, stroke=stroke, rx=h / 2, sw=1)
        self.text(x + pad, y + h / 2 + size * 0.36, label, size=size, fill=tcol,
                  family=family, weight=weight)
        if sub:
            self.text(x + pad + wl + 8, y + h / 2 + (size - 2) * 0.36, sub,
                      size=size - 2, fill=subcol, family=SANS)
        return w

    def pillrow(self, x, y, items, gap=8, **kw):
        """items: list of (label, sub) or (label, sub, overrides-dict)."""
        cx = x
        for it in items:
            label, sub = it[0], it[1]
            over = dict(kw)
            if len(it) > 2 and it[2]:
                over.update(it[2])
            cx += self.pill(cx, y, label, sub, **over) + gap
        return cx - gap - x

    def badge(self, x, y, n, col=BLUE, r=11):
        self.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{col}"/>')
        self.text(x, y + 4, n, size=12, fill="#fff", weight="700", anchor="middle")

    def save(self, path):
        defs = ('<defs>'
                '<marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#6f6f6a"/></marker>'
                '<marker id="arwb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#1f5fd0"/></marker>'
                '<marker id="arwr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#c0392b"/></marker>'
                '<marker id="arwp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#6d28d9"/></marker>'
                '<marker id="arwg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto-start-reverse">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#15803d"/></marker>'
                + "".join(self.defs) + '</defs>')
        body = "\n".join(self.parts)
        out = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}" font-family="{SANS}">{defs}\n{body}\n</svg>')
        open(path, "w").write(out)
        return path


def header(s, title, subtitle, tag=None):
    s.text(48, 56, title, size=27, weight="700")
    s.text(48, 82, subtitle, size=14, fill=MUTED)
    if tag:
        tw = w_sans(tag, 11.5) + 22
        s.rect(s.w - 48 - tw, 38, tw, 24, fill="#fff", stroke=LINE, rx=12)
        s.text(s.w - 48 - tw / 2, 54, tag, size=11.5, fill=MUTED, anchor="middle",
               family=MONO)
    s.line(48, 100, s.w - 48, 100, stroke=LINE)
