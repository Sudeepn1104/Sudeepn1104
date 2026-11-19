#!/usr/bin/env python3
"""Render the profile README cards.

Reads data/contributions.json (the raw GraphQL response for the contribution
calendar and public repositories) and writes animated SVG cards to cards/,
one dark and one light version of each.

    python scripts/render_cards.py            # animated cards -> cards/
    STATIC=1 python scripts/render_cards.py   # final-frame cards + index.html -> preview/

The output depends only on the JSON file, the fonts and assets/: "today" is
the last day in the contribution calendar, never the wall clock.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import html
import io
import json
import os
import statistics
import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools import subset
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contributions.json"
FONTS = ROOT / "fonts"
PORTRAIT = ROOT / "assets" / "portrait.jpg"
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("preview" if STATIC else "cards")

# --------------------------------------------------------------------------
# Profile content
# --------------------------------------------------------------------------

LOGIN = "Sudeepn1104"
NAME = "Sudeep U Nayak"
INITIALS = "SN"
ONE_LINE = "I build ML systems that notice when they stop working."
ROLE = "AI & ML engineering student · MITE Moodbidri · MLOps and NLP"

TILES = [
    # (key, icon, label, url, link text)
    ("portfolio", "globe", "Portfolio", "https://sudeepn1104.github.io/", "sudeepn1104.github.io"),
    ("linkedin", "person", "LinkedIn", "https://www.linkedin.com/in/sudeep-u-nayak-b69313374/",
     "linkedin.com/in/sudeep-u-nayak-b69313374"),
    ("email", "mail", "Email", "mailto:sudeepnayak1104@gmail.com", "sudeepnayak1104@gmail.com"),
]

# Featured repos in order. "stack" overrides the Language row.
FEATURED = [
    {"name": "MLOPs-Observatory", "title": "MLOps Observatory",
     "stack": "Python · scikit-learn · Streamlit · FastAPI · Docker · GitHub Actions"},
    {"name": "AdharaAI", "title": "AdharaAI",
     "stack": "Python · Transformers · InLegalBERT · ONNX Runtime · FastAPI · Docker"},
    {"name": "jalsetu-lite", "title": "JalSetu Lite",
     "stack": "FastAPI · SQLite · JavaScript · Next.js · Docker"},
]
NEVER_SHOW: set[str] = set()

# --------------------------------------------------------------------------
# Design tokens (from sudeepn1104.github.io)
# --------------------------------------------------------------------------

THEMES = {
    "dark": {"bg": "#0c0e12", "surface": "#14171d", "text": "#e9eaee", "muted": "#9aa1ae",
             "line": "#252a33", "accent": "#7c9cff", "page": "#0d1117"},
    "light": {"bg": "#f7f7f2", "surface": "#ffffff", "text": "#14161b", "muted": "#5a606c",
              "line": "#e2e2da", "accent": "#2f5bea", "page": "#ffffff"},
}

FONT_FILES = {
    "display": "space-grotesk-latin-600-normal.woff",
    "sans": "inter-latin-400-normal.woff",
    "sans-m": "inter-latin-500-normal.woff",
    "mono": "jetbrains-mono-latin-400-normal.woff",
    "mono-m": "jetbrains-mono-latin-500-normal.woff",
}
SHAPE_FEATURES = {"kern": True, "liga": True, "calt": True}
TNUM_FEATURES = {**SHAPE_FEATURES, "tnum": True}

W = 840              # full-width card width (README column)
TILE_W = 273         # 32.5% of W, so tile text renders at the same size
PAD = 32


def mix(a: str, b: str, t: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def n(v: float) -> str:
    """Compact, deterministic number formatting for SVG attributes."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# --------------------------------------------------------------------------
# Fonts: shaping, measuring, subsetting, outlines
# --------------------------------------------------------------------------

class Font:
    def __init__(self, key: str, path: Path):
        self.key = key
        self.family = f"c-{key}"
        self.raw = path.read_bytes()
        tt = TTFont(io.BytesIO(self.raw), recalcTimestamp=False)
        tt.flavor = None
        buf = io.BytesIO()
        tt.save(buf)
        self.sfnt = buf.getvalue()
        self.tt = TTFont(io.BytesIO(self.sfnt))
        self.upem = self.tt["head"].unitsPerEm
        self.hb_font = hb.Font(hb.Face(hb.Blob(self.sfnt)))
        self._cache: dict[tuple[str, bool], float] = {}

    def width(self, s: str, size: float, tnum: bool = False) -> float:
        key = (s, tnum)
        if key not in self._cache:
            buf = hb.Buffer()
            buf.add_str(s)
            buf.guess_segment_properties()
            hb.shape(self.hb_font, buf, TNUM_FEATURES if tnum else SHAPE_FEATURES)
            self._cache[key] = sum(p.x_advance for p in buf.glyph_positions) / self.upem
        return self._cache[key] * size

    def subset_data_uri(self, chars: str) -> str:
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = ["*"]
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
        opts.notdef_outline = True
        opts.recalc_timestamp = False
        font = TTFont(io.BytesIO(self.sfnt), recalcTimestamp=False)
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(set(chars))))
        sub.subset(font)
        font.flavor = "woff"
        out = io.BytesIO()
        font.save(out)
        return "data:font/woff;base64," + base64.b64encode(out.getvalue()).decode("ascii")

    def outline(self, s: str, x: float, baseline: float, size: float) -> str:
        """SVG path data for the glyph outlines of s, placed at (x, baseline)."""
        buf = hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, SHAPE_FEATURES)
        order = self.tt.getGlyphOrder()
        gs = self.tt.getGlyphSet()
        scale = size / self.upem
        pen_x = 0
        parts = []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            name = order[info.codepoint]
            pen = SVGPathPen(gs, ntos=lambda v: n(v))
            tpen = TransformPen(pen, (scale, 0, 0, -scale, x + (pen_x + pos.x_offset) * scale,
                                      baseline - pos.y_offset * scale))
            gs[name].draw(tpen)
            parts.append(pen.getCommands())
            pen_x += pos.x_advance
        return " ".join(p for p in parts if p)


FONTS_BY_KEY = {k: Font(k, FONTS / f) for k, f in FONT_FILES.items()}


def fit(text: str, font: str, size: float, max_w: float) -> str:
    """Return text, truncated with an ellipsis if it is wider than max_w."""
    f = FONTS_BY_KEY[font]
    if f.width(text, size) <= max_w:
        return text
    while text and f.width(text.rstrip() + "…", size) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text: str, font: str, size: float, max_w: float, max_lines: int) -> list[str]:
    f = FONTS_BY_KEY[font]
    words = text.split()
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        trial = (cur + " " + words[i]).strip()
        if f.width(trial, size) <= max_w or not cur:
            cur = trial
            i += 1
        else:
            lines.append(cur)
            cur = ""
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
        cur = ""
    if i < len(words) or cur:
        lines[-1] = fit(lines[-1] + " " + " ".join(words[i:]), font, size, max_w)
        if not lines[-1].endswith("…"):
            lines[-1] = fit(lines[-1] + "…", font, size, max_w)
    return lines


# --------------------------------------------------------------------------
# SVG card builder
# --------------------------------------------------------------------------

CSS_ANIM = """
.rise{animation:rise .6s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .6s ease-out both}
.draw{transform-box:fill-box;transform-origin:0 50%;animation:draw .7s cubic-bezier(.6,0,.2,1) both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .55s cubic-bezier(.2,.7,.2,1) both}
.pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .35s cubic-bezier(.2,.8,.3,1.2) both}
.stroke{animation:stroke 1.4s cubic-bezier(.6,0,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(6px)}}
@keyframes fade{from{opacity:0}}
@keyframes draw{from{transform:scaleX(0)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes pop{from{opacity:0;transform:scale(.4)}}
@keyframes stroke{from{stroke-dashoffset:1}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}.smil{display:none}}
"""


class Card:
    def __init__(self, name: str, width: float, height: float, theme: str, title: str, label: str):
        self.name, self.w, self.h = name, width, height
        self.t = THEMES[theme]
        self.theme = theme
        self.title, self.label = title, label
        self.body: list[str] = []
        self.defs: list[str] = []
        self.chars: dict[str, set[str]] = {}
        self.ids = 0

    def uid(self, prefix: str) -> str:
        self.ids += 1
        return f"{prefix}{self.ids}"

    def add(self, s: str) -> None:
        self.body.append(s)

    @staticmethod
    def anim(cls: str, delay: float, extra_cls: str = "") -> str:
        if STATIC or not cls:
            return f' class="{extra_cls}"' if extra_cls else ""
        return f' class="{(cls + " " + extra_cls).strip()}" style="animation-delay:{delay:.2f}s"'

    def text(self, x, y, s, font="sans", size=14, fill="text", anchor="start", cls="", delay=0.0,
             extra="", tnum=False) -> None:
        self.chars.setdefault(font, set()).update(s)
        colour = self.t.get(fill, fill)
        a = "" if anchor == "start" else f' text-anchor="{anchor}"'
        self.add(f'<text x="{n(x)}" y="{n(y)}" font-family="{FONTS_BY_KEY[font].family}" '
                 f'font-size="{n(size)}" fill="{colour}"{a}{extra}{self.anim(cls, delay, "tn" if tnum else "")}>{esc(s)}</text>')

    def rule(self, x1, y, x2, colour="line", width=1.0, cls="draw", delay=0.0) -> None:
        # Hairlines sit on half pixels so they render crisp.
        yy = y + (0.5 if width % 2 else 0)
        self.add(f'<line x1="{n(x1)}" y1="{n(yy)}" x2="{n(x2)}" y2="{n(yy)}" '
                 f'stroke="{self.t.get(colour, colour)}" stroke-width="{n(width)}"{self.anim(cls, delay)}/>')

    def frame(self) -> None:
        self.add(f'<rect x=".5" y=".5" width="{n(self.w - 1)}" height="{n(self.h - 1)}" rx="2" '
                 f'fill="{self.t["bg"]}" stroke="{self.t["line"]}"/>')

    def render(self) -> str:
        faces = []
        for key in sorted(self.chars):
            f = FONTS_BY_KEY[key]
            faces.append(f'@font-face{{font-family:"{f.family}";src:url({f.subset_data_uri("".join(self.chars[key]))}) '
                         f'format("woff")}}')
        css = "".join(faces) + ".tn{font-variant-numeric:tabular-nums}"
        if not STATIC:
            css += CSS_ANIM.replace("\n", "")
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(self.w)}" height="{n(self.h)}" '
                f'viewBox="0 0 {n(self.w)} {n(self.h)}" role="img" aria-label="{esc(self.label)}">'
                f"<title>{esc(self.title)}</title><style>{css}</style>{defs}"
                + "".join(self.body) + "</svg>\n")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load():
    raw = json.loads(DATA.read_text())
    user = raw["data"]["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [(dt.date.fromisoformat(d["date"]), int(d["contributionCount"]))
            for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort()
    repos = {r["name"]: r for r in user["repositories"]["nodes"]}
    return days, repos


def fmt_date(d: dt.date) -> str:
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


def plural(v: int, word: str) -> str:
    return f"{v:,} {word}{'' if v == 1 else 's'}"


def stats(days):
    today = days[-1][0]
    counts = dict(days)
    # Current streak: ends today, or yesterday if nothing has landed today yet.
    d = today if counts.get(today, 0) else today - dt.timedelta(days=1)
    cur = 0
    while counts.get(d, 0) > 0:
        cur += 1
        d -= dt.timedelta(days=1)
    longest = run = 0
    for _, c in days:
        run = run + 1 if c else 0
        longest = max(longest, run)
    active = sum(1 for _, c in days if c)
    best = max(days, key=lambda dc: (dc[1], dc[0]))
    return {"today": today, "current": cur, "longest": longest, "active": active,
            "total_days": len(days), "busiest": best, "total": sum(c for _, c in days)}


def months(days, k=12):
    today = days[-1][0]
    keys = []
    y, m = today.year, today.month
    for _ in range(k):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    totals = {key: 0 for key in keys}
    for d, c in days:
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += c
    return [(key, totals[key]) for key in keys]


def nice_step(maxv: float, target=4) -> int:
    if maxv <= 0:
        return 1
    raw = maxv / target
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag and float(m * mag).is_integer():
            return int(m * mag)
    return int(10 * mag)


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------

def card_hero(theme):
    H = 236
    c = Card("hero", W, H, theme, f"{NAME} — profile",
             f"Profile header for {NAME}: {ONE_LINE} {ROLE}.")
    t = c.t
    c.frame()
    # Faint grid, clipped to the card.
    clip = c.uid("clip")
    c.defs.append(f'<clipPath id="{clip}"><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="2"/></clipPath>')
    grid = [f"M{x} 1V{H - 1}" for x in range(24, W, 24)] + [f"M1 {y}H{W - 1}" for y in range(20, H, 24)]
    c.add(f'<g clip-path="url(#{clip})"><path d="{" ".join(grid)}" stroke="{t["line"]}" stroke-width="1" '
          f'opacity=".45" fill="none"/>')
    if not STATIC:
        c.add(f'<line class="smil" x1="0" y1="1" x2="0" y2="{H - 1}" stroke="{t["accent"]}" stroke-width="1.5" opacity="0">'
              f'<animate attributeName="x1" from="0" to="{W}" begin="0.15s" dur="1.8s" fill="freeze" '
              f'calcMode="spline" keySplines=".5 0 .3 1"/>'
              f'<animate attributeName="x2" from="0" to="{W}" begin="0.15s" dur="1.8s" fill="freeze" '
              f'calcMode="spline" keySplines=".5 0 .3 1"/>'
              f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.1;.8;1" begin="0.15s" '
              f'dur="1.8s" fill="freeze"/></line>')
    c.add("</g>")

    fs = 172
    fx, fy = W - PAD - fs, (H - fs) / 2
    left_w = fx - PAD - 40
    size = 56.0
    while FONTS_BY_KEY["display"].width(NAME, size) > left_w and size > 28:
        size -= 1
    c.text(PAD, 104, NAME, "display", size, cls="rise", delay=0.05)
    c.rule(PAD, 128, fx - 40, colour="text", cls="draw", delay=0.3)
    c.text(PAD, 166, fit(ONE_LINE, "sans-m", 18, left_w), "sans-m", 18, cls="rise", delay=0.45)
    c.text(PAD, 194, fit(ROLE, "sans", 15, left_w), "sans", 15, fill="muted", cls="rise", delay=0.6)

    # Portrait frame or stroked monogram.
    c.add(f'<rect x="{n(fx + .5)}" y="{n(fy + .5)}" width="{fs - 1}" height="{fs - 1}" rx="2" '
          f'fill="{t["surface"]}" stroke="{t["line"]}"{c.anim("fade", 0.2)}/>')
    if PORTRAIT.exists():
        pc = c.uid("pc")
        c.defs.append(f'<clipPath id="{pc}"><rect x="{n(fx + 1)}" y="{n(fy + 1)}" width="{fs - 2}" '
                      f'height="{fs - 2}" rx="2"/></clipPath>')
        data = base64.b64encode(PORTRAIT.read_bytes()).decode("ascii")
        c.add(f'<image x="{n(fx + 1)}" y="{n(fy + 1)}" width="{fs - 2}" height="{fs - 2}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#{pc})" '
              f'href="data:image/jpeg;base64,{data}"{c.anim("fade", 0.3)}/>')
    else:
        f = FONTS_BY_KEY["display"]
        ms = 96.0
        mw = f.width(INITIALS, ms)
        cap = f.tt["OS/2"].sCapHeight / f.upem * ms
        mx, my = fx + (fs - mw) / 2, fy + (fs + cap) / 2
        d = f.outline(INITIALS, mx, my, ms)
        dash = "" if STATIC else ' pathLength="1" stroke-dasharray="1"'
        c.add(f'<path d="{d}" fill="none" stroke="{t["accent"]}" stroke-width="1.5" '
              f'stroke-linejoin="round"{dash}{c.anim("stroke", 0.35)}/>')
        # Corner ticks, datasheet style.
        c.text(fx + 12, fy + fs - 12, f"@{LOGIN.lower()}", "mono", 11, fill="muted", cls="fade", delay=0.9)
    return c


ICONS = {
    "globe": '<circle cx="10" cy="10" r="8.25"/><path d="M1.75 10h16.5M10 1.75c2.4 2.3 3.6 5 3.6 8.25S12.4 15.95 10 18.25C7.6 15.95 6.4 13.25 6.4 10S7.6 4.05 10 1.75z"/>',
    "person": '<rect x="1.75" y="1.75" width="16.5" height="16.5" rx="2"/><circle cx="10" cy="8" r="3"/><path d="M4.5 18.25c.7-3 2.9-4.75 5.5-4.75s4.8 1.75 5.5 4.75"/>',
    "mail": '<rect x="1.75" y="3.75" width="16.5" height="12.5" rx="2"/><path d="M2.5 5l7.5 6 7.5-6"/>',
}


def card_tile(theme, idx, tile):
    key, icon, label, url, link = tile
    H = 92
    c = Card(f"tile-{key}", TILE_W, H, theme, f"{label}: {link}", f"Link tile: {label}, {link}")
    t = c.t
    base = 0.8 + idx * 0.1
    c.frame()
    c.add(f'<g transform="translate(20 18)" fill="none" stroke="{t["text"]}" stroke-width="1.5" '
          f'stroke-linecap="round" stroke-linejoin="round"><g{c.anim("rise", base)}>{ICONS[icon]}</g></g>')
    ax, ay = TILE_W - 20, 20
    c.add(f'<path d="M{ax - 9} {ay + 9}L{ax} {ay}M{ax - 7} {ay}H{ax}V{ay + 7}" fill="none" stroke="{t["muted"]}" '
          f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"{c.anim("fade", base + 0.25)}/>')
    c.text(20, 60, label, "sans-m", 15, cls="rise", delay=base + 0.05)
    c.text(20, 78, fit(link, "mono", 12, TILE_W - 40), "mono", 12, fill="muted", cls="rise", delay=base + 0.12)
    return c


def card_monthly(theme, days):
    H = 292
    ms = months(days)
    st = stats(days)
    vals = [v for _, v in ms]
    busiest_i = max(range(len(ms)), key=lambda i: (vals[i], i))
    cur_i = len(ms) - 1
    year_total = sum(vals)
    first, last = ms[0][0], ms[-1][0]
    span = f"{calendar.month_abbr[first[1]]} {first[0]} – {calendar.month_abbr[last[1]]} {last[0]}"
    c = Card("monthly", W, H, theme, "Contributions per month",
             f"Bar chart of contributions per month, {span}: {year_total} in total, busiest "
             f"{calendar.month_name[ms[busiest_i][0][1]]} {ms[busiest_i][0][0]} with {vals[busiest_i]}. "
             f"Current streak {st['current']} days, longest streak {st['longest']} days, "
             f"{st['active']} of {st['total_days']} days active.")
    t = c.t
    B = 1.2
    c.frame()
    c.text(PAD, 42, "// contributions per month", "mono", 12, fill="muted", cls="fade", delay=B)
    c.text(W - PAD, 42, f"{year_total:,} · {span}", "mono", 12, fill="muted", anchor="end", cls="fade", delay=B)
    c.rule(PAD, 58, W - PAD, delay=B + 0.05)

    top, bottom = 96, 236
    x0, x1 = 64, 520
    step = nice_step(max(vals))
    gmax = max(step, -(-max(vals) // step) * step)
    def py(v): return bottom - (bottom - top) * v / gmax
    for g in range(0, gmax + 1, step):
        y = round(py(g))
        c.rule(x0, y, x1, colour="line" if g else "muted", cls="fade", delay=B + 0.1)
        c.text(x0 - 10, y + 4, f"{g:,}", "mono", 11, fill="muted", anchor="end", cls="fade", delay=B + 0.1)
    slot = (x1 - x0) / len(ms)
    bw = 22
    labels = []
    for i, ((yy, mm), v) in enumerate(ms):
        cx = x0 + slot * (i + 0.5)
        bx, by = cx - bw / 2, py(v)
        h = bottom - by
        delay = B + 0.15 + i * 0.05
        if i == cur_i:
            if h > 1:
                c.add(f'<rect x="{n(bx + .75)}" y="{n(by + .75)}" width="{n(bw - 1.5)}" height="{n(h - .75)}" '
                      f'fill="{mix(t["bg"], t["text"], .06)}" stroke="{t["text"]}" stroke-width="1.5" '
                      f'stroke-dasharray="3 3"{c.anim("grow", delay)}/>')
        elif h > 0:
            fill = t["accent"] if i == busiest_i else mix(t["bg"], t["text"], .32)
            c.add(f'<rect x="{n(bx)}" y="{n(by)}" width="{bw}" height="{n(h)}" fill="{fill}"{c.anim("grow", delay)}/>')
        c.text(cx, bottom + 20, calendar.month_abbr[mm], "mono", 11,
               fill="text" if i in (busiest_i, cur_i) else "muted", anchor="middle", cls="fade", delay=delay)
        if i == cur_i:
            labels.append((i, cx, by, f"Month in progress · {v:,}", "end"))
        elif i == busiest_i:
            labels.append((i, cx, by, f"Busiest · {v:,}", "middle"))
    # Place callouts above their bars; lift one if they would collide.
    boxes = []
    bar_tops = [(x0 + slot * (i + 0.5) - bw / 2, x0 + slot * (i + 0.5) + bw / 2, py(v)) for i, v in enumerate(vals)]
    for i, cx, by, s, anchor in sorted(labels, key=lambda l: l[2]):
        wdt = FONTS_BY_KEY["sans-m"].width(s, 12, tnum=True)
        lx = cx + bw / 2 - wdt if anchor == "end" else cx - wdt / 2
        lx = max(x0, min(lx, x1 - wdt))
        # Baseline sits 8px above the tallest bar the label spans.
        y = min([by] + [bt for b0, b1, bt in bar_tops if b1 > lx - 4 and b0 < lx + wdt + 4]) - 8
        for (bx0, bx1, byy) in boxes:
            if lx < bx1 + 6 and lx + wdt > bx0 - 6 and abs(y - byy) < 16:
                y = byy - 16
        boxes.append((lx, lx + wdt, y))
        if by - y > 14:  # lifted well clear of its bar: draw a leader down to it
            c.add(f'<line x1="{n(cx + .5)}" y1="{n(y + 5)}" x2="{n(cx + .5)}" y2="{n(by - 3)}" '
                  f'stroke="{t["muted"]}" stroke-width="1"{c.anim("fade", B + 0.8)}/>')
        c.text(lx, y, s, "sans-m", 12, fill="accent" if i == busiest_i and i != cur_i else "text",
               cls="rise", delay=B + 0.75, tnum=True)

    # Key/value rows.
    rx0, rx1 = 568, W - PAD
    bd, bc = st["busiest"]
    rows = [
        ("Current streak", plural(st["current"], "day")),
        ("Longest streak", plural(st["longest"], "day")),
        ("Active days", f"{st['active']:,} of {st['total_days']:,}"),
        ("Busiest day", f"{fmt_date(bd)} · {bc:,}"),
    ]
    ry = 80
    rh = 39
    for k, (key, val) in enumerate(rows):
        d = B + 0.3 + k * 0.08
        c.rule(rx0, ry + k * rh, rx1, delay=d)
        c.text(rx0, ry + k * rh + 25, key, "sans", 14, fill="muted", cls="rise", delay=d + 0.05)
        c.text(rx1, ry + k * rh + 25, val, "sans-m", 14, anchor="end", cls="rise", delay=d + 0.08, tnum=True)
    c.rule(rx0, ry + len(rows) * rh, rx1, delay=B + 0.3 + len(rows) * 0.08)
    return c


def card_repo(theme, idx, spec, repo):
    t0 = 2.0 + idx * 0.3
    desc = (repo.get("description") or "").strip() or "No description yet."
    lines = wrap(desc, "sans", 15, W - 2 * PAD, 4)
    rows = []
    if spec.get("stack"):
        rows.append(("Stack", spec["stack"]))
    elif repo.get("primaryLanguage"):
        rows.append(("Language", repo["primaryLanguage"]["name"]))
    if repo.get("stargazerCount", 0) > 0:
        rows.append(("Stars", f"{repo['stargazerCount']:,}"))
    pushed = dt.datetime.fromisoformat(repo["pushedAt"].replace("Z", "+00:00")).date()
    rows.append(("Last push", fmt_date(pushed)))
    desc_top = 128
    rows_top = desc_top + (len(lines) - 1) * 23 + 22
    rh = 36
    H = rows_top + len(rows) * rh + 28
    title = spec.get("title") or repo["name"]
    owner_repo = repo["url"].replace("https://github.com/", "")
    c = Card(f"repo-{repo['name'].lower()}", W, H, theme, f"{title} ({owner_repo})",
             f"Featured repository {title}, {owner_repo}: {desc} "
             + ". ".join(f"{k}: {v}" for k, v in rows) + ".")
    c.frame()
    c.rule(PAD, 28, W - PAD, colour="text", width=2, delay=t0)
    c.text(PAD, 70, title, "display", 26, cls="rise", delay=t0 + 0.1)
    c.text(W - PAD, 70, f"P/{idx + 1:02d}", "mono", 12, fill="muted", anchor="end", cls="fade", delay=t0 + 0.15)
    c.text(PAD, 94, owner_repo, "mono", 13, fill="accent", cls="rise", delay=t0 + 0.15)
    for k, line in enumerate(lines):
        c.text(PAD, desc_top + k * 23, line, "sans", 15, cls="rise", delay=t0 + 0.2 + k * 0.03)
    for k, (key, val) in enumerate(rows):
        y = rows_top + k * rh
        d = t0 + 0.25 + k * 0.06
        c.rule(PAD, y, W - PAD, delay=d)
        c.text(PAD, y + 23, key, "sans", 14, fill="muted", cls="rise", delay=d + 0.04)
        c.text(W - PAD, y + 23, fit(val, "mono", 13, W - 2 * PAD - 140), "mono", 13, anchor="end",
               cls="rise", delay=d + 0.06)
    c.rule(PAD, rows_top + len(rows) * rh, W - PAD, delay=t0 + 0.25 + len(rows) * 0.06)
    return c


def card_graph(theme, days):
    t = THEMES[theme]
    B = 2.9
    st = stats(days)
    nz = sorted(cn for _, cn in days if cn)
    if len(nz) >= 4:
        q1, q2, q3 = statistics.quantiles(nz, n=4, method="inclusive")
    else:
        q1 = q2 = q3 = (nz[-1] if nz else 1)
    levels = [t["line"]] + [mix(t["bg"], t["accent"], f) for f in (.3, .55, .8, 1)]
    def level(cn):
        if cn == 0: return 0
        if cn <= q1: return 1
        if cn <= q2: return 2
        if cn <= q3: return 3
        return 4
    first, last = days[0][0], days[-1][0]
    H = 248
    c = Card("graph", W, H, theme, "Contribution graph",
             f"Contribution graph from {fmt_date(first)} to {fmt_date(last)}: {st['total']} contributions on "
             f"{st['active']} days, shaded in four levels by quartile of active days.")
    c.frame()
    c.text(PAD, 42, "// contribution graph", "mono", 12, fill="muted", cls="fade", delay=B)
    c.text(W - PAD, 42, f"{plural(st['total'], 'contribution')} · {fmt_date(first)} – {fmt_date(last)}",
           "mono", 12, fill="muted", anchor="end", cls="fade", delay=B)
    c.rule(PAD, 58, W - PAD, delay=B + 0.05)
    cell, pitch = 11, 14
    gx, gy = 64, 100
    # Columns are weeks starting Sunday.
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    ncols = (last - start).days // 7 + 1
    counts = dict(days)
    # Month labels where a month's first day falls; drop any that would collide.
    prev_end = -1e9
    for col in range(ncols):
        wk0 = start + dt.timedelta(days=7 * col)
        for k in range(7):
            d = wk0 + dt.timedelta(days=k)
            if d.day == 1 and first <= d <= last or (col == 0 and k == 0 and d.day <= 7):
                s = calendar.month_abbr[d.month]
                x = gx + col * pitch
                wdt = FONTS_BY_KEY["mono"].width(s, 11)
                if x >= prev_end + 6 and x + wdt <= W - PAD:
                    c.text(x, gy - 10, s, "mono", 11, fill="muted", cls="fade", delay=B + 0.1)
                    prev_end = x + wdt
                break
    for row, s in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, gy + row * pitch + 9, s, "mono", 11, fill="muted", cls="fade", delay=B + 0.1)
    for col in range(ncols):
        wk0 = start + dt.timedelta(days=7 * col)
        rects = []
        for k in range(7):
            d = wk0 + dt.timedelta(days=k)
            if d < first or d > last:
                continue
            cn = counts.get(d, 0)
            rects.append(f'<rect x="{gx + col * pitch}" y="{gy + k * pitch}" width="{cell}" height="{cell}" rx="2" '
                         f'fill="{levels[level(cn)]}"><title>{fmt_date(d)}: {plural(cn, "contribution")}</title></rect>')
        c.add(f'<g{c.anim("pop", B + 0.15 + col * 0.0135)}>{"".join(rects)}</g>')
    # Legend.
    ly = gy + 7 * pitch + 22
    more_w = FONTS_BY_KEY["mono"].width("More", 11)
    lx = W - PAD - more_w
    c.text(lx, ly + 9, "More", "mono", 11, fill="muted", cls="fade", delay=B + 0.9)
    sx = lx - 8 - 5 * pitch + 3
    for k in range(5):
        c.add(f'<rect x="{sx + k * pitch}" y="{ly}" width="{cell}" height="{cell}" rx="2" fill="{levels[k]}"'
              f'{c.anim("fade", B + 0.9)}/>')
    c.text(sx - 8, ly + 9, "Less", "mono", 11, fill="muted", anchor="end", cls="fade", delay=B + 0.9)
    c.text(PAD, ly + 9, f"Shaded by quartile of active days · cut-offs {n(q1)}, {n(q2)}, {n(q3)}",
           "mono", 11, fill="muted", cls="fade", delay=B + 0.9)
    return c


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build():
    days, repos = load()
    featured = []
    for spec in FEATURED:
        r = repos.get(spec["name"])
        if r is None or r["name"] in NEVER_SHOW or r.get("isPrivate"):
            print(f"skipping {spec['name']}: not in the public repository list", file=sys.stderr)
            continue
        featured.append((spec, r))
    out: dict[str, list[Card]] = {}
    for theme in THEMES:
        cards = [card_hero(theme)]
        cards += [card_tile(theme, i, tile) for i, tile in enumerate(TILES)]
        cards.append(card_monthly(theme, days))
        cards += [card_repo(theme, i, spec, r) for i, (spec, r) in enumerate(featured)]
        cards.append(card_graph(theme, days))
        out[theme] = cards
    return out


def preview_html(out) -> str:
    cols = []
    for theme, cards in out.items():
        t = THEMES[theme]
        parts = []
        for cd in cards:
            img = f'<img src="{cd.name}-{theme}.svg" alt="{esc(cd.label)}"'
            if cd.name.startswith("tile-"):
                parts.append(f'{img} style="width:32.5%">')
            else:
                parts.append(f'{img} style="width:100%;display:block;margin:0 0 12px">')
        body = []
        tiles = [p for p, cd in zip(parts, cards) if cd.name.startswith("tile-")]
        for p, cd in zip(parts, cards):
            if cd.name.startswith("tile-"):
                if cd.name == cards[1].name:
                    body.append(f'<p class="tiles">{" ".join(tiles)}</p>')
                continue
            body.append(p)
        cols.append(f'<section style="background:{t["page"]}"><h2 style="color:{t["muted"]}">{theme}</h2>'
                    + "".join(body) + "</section>")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Card preview</title><style>"
            "body{margin:0;display:flex;gap:0;font-family:system-ui,sans-serif}"
            "section{flex:1;padding:24px 32px;box-sizing:border-box;max-width:900px}"
            "h2{font:500 12px ui-monospace,monospace;margin:0 0 12px}"
            ".tiles{margin:0 0 12px;font-size:16px;line-height:0}.tiles img{vertical-align:top}"
            "</style></head><body>" + "".join(cols) + "</body></html>\n")


def main():
    out = build()
    OUT.mkdir(parents=True, exist_ok=True)
    written = set()
    for theme, cards in out.items():
        for cd in cards:
            p = OUT / f"{cd.name}-{theme}.svg"
            p.write_text(cd.render())
            written.add(p.name)
    for old in OUT.glob("*.svg"):
        if old.name not in written:
            old.unlink()
    if STATIC:
        (OUT / "index.html").write_text(preview_html(out))
    print(f"wrote {len(written)} cards to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
