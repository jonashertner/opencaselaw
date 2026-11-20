"""Week 47 (13 - 19 November 2026), German: the week's courts re-cut into Paul Klee's own roads.

Klee's "Hauptweg und Nebenwege" (Highway and Byways, January 1929, oil on canvas, Museum Ludwig
Koeln, 83.7 x 67.5 cm) is a field of horizontal colour strips with a pale main road climbing the
centre, narrowing toward a horizon band, and two broad orange side roads branching off it, each
itself tapering away as it recedes. That is also the shape of a week in the corpus: one broad
federal stream up the middle, cantonal courts branching off either side by size.

The three roads are re-cut from the painting's own pixels. For each road we take its real content
(a crop straight out of the scan, nothing invented), slice it into small fixed-height chunks so no
chunk is ever stretched or squashed (the paint grain never changes scale), shuffle the chunks --
a genuine re-stack, not a redraw -- and re-slice the shuffled strip at new cut points set by this
week's court totals. Every pixel in every road is an untouched, repositioned source pixel.

  main road   = the federal courts (by_canton["CH"], split by seat from by_court):
                bger@VD, bvger, bger@LU, bstger, edoeb -- stacked widest (bottom) to narrowest
                (near the horizon), descending by count.
  side roads  = every other court in by_court (40 of them, cantonal + regulators), split between
                the painting's own two flanking roads by a greedy running-sum balance (so the two
                roads carry 359 decisions each, to the decision) and, within each road, stacked
                busiest nearest the main road (bottom) to smallest near where that road recedes.

Total: 256 + 359 + 359 = 974, asserted at render time against week["added"] and week["by_court"].
A thin, hand-wavering ink line (the colour sampled from the painting's own contour work) is drawn
along each re-cut road's edge and at every court boundary -- Klee's own line technique, not a
chart gridline -- so the recut reads as drawing. No court is named on the poster.

The horizon band at the top is left untouched and carries the week's figure, huge, blended into
the paint with a multiply so it sits in the canvas rather than on top of it.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
MW, MH = 2160, 2700  # the master collage, built at render scale (2x)
ASSETS = Path(__file__).with_name("weekly_poster_w47_assets") / "hauptweg"
SRC_PATH = ASSETS / "klee_2160x2700.jpg"  # the Commons file resampled to MW x MH (LANCZOS), JPEG q95
BG_CACHE = ASSETS / "bg_2160.jpg"

INK = (64, 61, 54)  # sampled: median colour of the painting's own darkest (contour-line) pixels

MONTH_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
            "Oktober", "November", "Dezember"]

FED_KEYS = ["bger@VD", "bvger", "bger@LU", "bstger", "edoeb"]

# ── the three roads, traced by eye against a fraction grid laid over the painting ──────────
# control points: (y_fraction_of_canvas, x_left_fraction, x_right_fraction), y descending from
# the canvas foot (1.00) toward the horizon.
MAIN_PTS = [
    (1.00, 0.385, 0.685),
    (0.85, 0.398, 0.655),
    (0.70, 0.405, 0.630),
    (0.55, 0.412, 0.605),
    (0.40, 0.418, 0.588),
    (0.25, 0.423, 0.578),
    (0.19, 0.427, 0.573),
]
LEFT_PTS = [
    (1.00, 0.185, 0.385),
    (0.85, 0.215, 0.372),
    (0.70, 0.245, 0.360),
    (0.55, 0.275, 0.345),
]
RIGHT_PTS = [
    (1.00, 0.685, 0.885),
    (0.85, 0.698, 0.855),
    (0.70, 0.710, 0.825),
    (0.55, 0.725, 0.800),
]


class _Corridor:
    def __init__(self, pts):
        pts = sorted(pts, key=lambda p: p[0])
        self.yf = np.array([p[0] for p in pts])
        self.xl = np.array([p[1] for p in pts])
        self.xr = np.array([p[2] for p in pts])
        self.y_top = float(self.yf.min() * MH)
        self.y_bot = float(self.yf.max() * MH)
        self.h = int(round(self.y_bot - self.y_top))

    def edges(self, y_px: np.ndarray):
        yf = y_px / MH
        left = np.interp(yf, self.yf, self.xl) * MW
        right = np.interp(yf, self.yf, self.xr) * MW
        return left, right

    def bbox(self):
        ys = np.linspace(self.y_top, self.y_bot, 400)
        left, right = self.edges(ys)
        return int(np.floor(left.min())), int(np.ceil(right.max()))


def _federal_bands(week: dict) -> list[tuple[str, int]]:
    bc = week["by_court"]
    bands = sorted(((k, bc[k]) for k in FED_KEYS if k in bc), key=lambda kv: -kv[1])
    assert sum(n for _, n in bands) == week["by_canton"]["CH"]
    return bands


def _side_bands(week: dict) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    bc = week["by_court"]
    side = {k: n for k, n in bc.items() if k not in FED_KEYS}
    items = sorted(side.items(), key=lambda kv: -kv[1])
    left: list[tuple[str, int]] = []
    right: list[tuple[str, int]] = []
    sl = sr = 0
    for k, n in items:  # greedy balance: the next-largest court goes to whichever road is behind
        if sl <= sr:
            left.append((k, n)); sl += n
        else:
            right.append((k, n)); sr += n
    return left, right


def _cut_and_restack(src_arr: np.ndarray, corridor: _Corridor, bands: list, seed: int,
                      chunk_h: int = 50) -> tuple[np.ndarray, int]:
    """The road's own real pixel rows, cut into fixed-height chunks (never stretched), shuffled,
    then re-sliced at this week's court boundaries. Returns (content, x0)."""
    x0, x1 = corridor.bbox()
    w = x1 - x0
    total_units = sum(n for _, n in bands)
    raw = src_arr[int(corridor.y_top):int(corridor.y_top) + corridor.h, x0:x1]
    h = raw.shape[0]
    chunks = [raw[y:y + chunk_h] for y in range(0, h, chunk_h)]
    rng = random.Random(seed)
    rng.shuffle(chunks)
    shuffled = np.concatenate(chunks, axis=0)
    px_per_unit = h / total_units
    out, cursor, cum = [], 0, 0.0
    for _key, n in bands:
        cum += n * px_per_unit
        end = min(int(round(cum)), h)
        if end <= cursor:
            end = min(cursor + 1, h)
        out.append(shuffled[cursor:end])
        cursor = end
    content = np.concatenate(out, axis=0)
    if content.shape[0] != h:
        pad = h - content.shape[0]
        content = np.concatenate([content, shuffled[-pad:]], axis=0) if pad > 0 else content[:h]
    return content, x0


def _paste(canvas: np.ndarray, corridor: _Corridor, content: np.ndarray, x0: int) -> None:
    h, w, _ = content.shape
    y_top = int(corridor.y_top)
    ys = np.arange(y_top, y_top + h)
    left, right = corridor.edges(ys.astype(float))
    for i, y in enumerate(ys):
        if not (0 <= y < MH):
            continue
        lx = max(int(round(left[i])) - x0, 0)
        rx = min(int(round(right[i])) - x0, w)
        if rx > lx:
            canvas[y, x0 + lx:x0 + rx] = content[i, lx:rx]


def _draw_contours(img: Image.Image, corridor: _Corridor, bands: list, seed: int, main: bool = False) -> None:
    """Klee's own wavering ink line, along each re-cut road's edge and at every court boundary.
    Drawn stronger on the main road than on the side roads -- the one deliberate, legible device
    that lets the main road read as the main road at a glance, without a single label."""
    outer_w, outer_a = (6, 205) if main else (3, 100)
    inner_w, inner_a = (3, 175) if main else (1, 58)

    d = ImageDraw.Draw(img, "RGBA")
    rng = random.Random(seed)
    ys = np.linspace(corridor.y_top, corridor.y_bot, 220)
    left, right = corridor.edges(ys)
    lj = [rng.uniform(-0.8, 0.8) for _ in ys]
    rj = [rng.uniform(-0.8, 0.8) for _ in ys]
    d.line([(float(left[i] + lj[i]), float(ys[i])) for i in range(len(ys))], fill=INK + (outer_a,), width=outer_w, joint="curve")
    d.line([(float(right[i] + rj[i]), float(ys[i])) for i in range(len(ys))], fill=INK + (outer_a,), width=outer_w, joint="curve")

    total_units = sum(n for _, n in bands)
    px_per_unit = corridor.h / total_units
    cum = 0.0
    for _key, n in bands[:-1]:
        cum += n * px_per_unit
        y = corridor.y_top + cum
        lx, rx = corridor.edges(np.array([y]))
        x0, x1 = float(lx[0]), float(rx[0])
        pts = [(x0 + (x1 - x0) * s / 10, y + rng.uniform(-0.6, 0.6)) for s in range(11)]
        d.line(pts, fill=INK + (inner_a,), width=inner_w, joint="curve")


def _veil(img: Image.Image, corridor: _Corridor, alpha: int = 32,
          color: tuple[int, int, int] = (252, 248, 236)) -> Image.Image:
    """A very slight translucent wash over a side road's real pixels -- not a repaint, just a
    patina that lets it recede a touch behind the unveiled main road. Returns the composited RGB."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    ys = np.linspace(corridor.y_top, corridor.y_bot, 240)
    left, right = corridor.edges(ys)
    poly = [(float(left[i]), float(ys[i])) for i in range(len(ys))]
    poly += [(float(right[i]), float(ys[i])) for i in range(len(ys) - 1, -1, -1)]
    d.polygon(poly, fill=color + (alpha,))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _build_master(week: dict) -> bytes:
    """The recut collage, 2160x2700, as JPEG bytes. Built fresh from this week's by_court every
    call (cheap: a few seconds), and also written to bg_2160.jpg for inspection."""
    src = Image.open(SRC_PATH).convert("RGB").resize((MW, MH), Image.LANCZOS)
    src_arr = np.asarray(src)
    canvas = src_arr.copy()

    main_c, left_c, right_c = _Corridor(MAIN_PTS), _Corridor(LEFT_PTS), _Corridor(RIGHT_PTS)
    fed = _federal_bands(week)
    left_bands, right_bands = _side_bands(week)

    assert sum(n for _, n in fed) + sum(n for _, n in left_bands) + sum(n for _, n in right_bands) == week["added"]

    for corridor, bands, seed in ((main_c, fed, 11), (left_c, left_bands, 12), (right_c, right_bands, 13)):
        content, x0 = _cut_and_restack(src_arr, corridor, bands, seed=seed + 100)
        _paste(canvas, corridor, content, x0)

    out = Image.fromarray(canvas).convert("RGB")
    out = _veil(out, left_c)
    out = _veil(out, right_c)
    _draw_contours(out, main_c, fed, 11, main=True)
    _draw_contours(out, left_c, left_bands, 12, main=False)
    _draw_contours(out, right_c, right_bands, 13, main=False)

    BG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.save(BG_CACHE, quality=92)
    from io import BytesIO
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def design(week: dict, lang: str = "de") -> str:
    n = week["added"]
    assert n == 974, n
    assert sum(week["by_court"].values()) == n == week["by_canton"]["CH"] + sum(
        v for k, v in week["by_court"].items() if k not in FED_KEYS)

    fed = _federal_bands(week)
    left_bands, right_bands = _side_bands(week)
    fed_n = sum(x for _, x in fed)
    side_n = sum(x for _, x in left_bands) + sum(x for _, x in right_bands)
    side_courts = len(left_bands) + len(right_bands)
    assert fed_n == 256 and side_n == 718 and side_courts == 40 and fed_n + side_n == n

    bg_bytes = _build_master(week)
    bg_b64 = base64.b64encode(bg_bytes).decode()

    a, b = week["from"], week["to"]
    date_line = f"Woche {week['week_no']} · {a.day}.–{b.day}. {MONTH_DE[b.month - 1]} {b.year}"
    # fed covers bger, bvger, bstger (courts) and edoeb (a federal authority, not a court); the side
    # roads cover cantonal courts plus a handful of non-court cantonal bodies (e.g. a Regierungsrat
    # acting on appeal) -- "Gerichte und Behörden" is accurate on both sides, "Gerichte" alone is not.
    key_line = (f"Hauptweg: {esc(wp.fmt(fed_n, 'de'))} Entscheide der Bundesgerichte und -behörden · "
                f"Nebenwege: {esc(wp.fmt(side_n, 'de'))} aus {esc(wp.fmt(side_courts, 'de'))} weiteren "
                f"Gerichten und Behörden")

    body = f"""
<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="scrim"></div>
<div class="num">{esc(wp.fmt(n, 'de'))}</div>
<div class="tag">neue Entscheide</div>
<div class="foot">
  <div class="week">{esc(date_line)}</div>
  <div class="key">{key_line}</div>
  <div class="url">opencaselaw.ch</div>
  <div class="credit">Paul Klee, <i>Hauptweg und Nebenwege</i>, 1929, Museum Ludwig Köln</div>
</div>"""

    css = f"""
body{{background:#9fcfe0}}
.bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}}
.scrim{{position:absolute;left:0;right:0;bottom:0;height:360px;
  background:linear-gradient(to top,rgba(10,8,5,.70) 0%,rgba(10,8,5,.52) 32%,rgba(10,8,5,.18) 68%,rgba(10,8,5,0) 100%)}}
.num{{position:absolute;left:0;right:0;top:14px;text-align:center;
  font:900 258px/1 'Jost',sans-serif;letter-spacing:-.035em;color:#211c16;
  mix-blend-mode:multiply;font-variant-numeric:lining-nums tabular-nums}}
.tag{{position:absolute;left:0;right:0;top:244px;text-align:center;
  font:600 46px/1 'Jost',sans-serif;letter-spacing:.015em;color:#1c1712;
  mix-blend-mode:multiply}}
.foot{{position:absolute;left:54px;right:54px;bottom:46px}}
.week{{font:500 27px/1.2 'Jost',sans-serif;color:#fffaf0;letter-spacing:.01em;
  text-shadow:0 1px 10px rgba(0,0,0,.6),0 1px 2px rgba(0,0,0,.55)}}
.key{{margin-top:8px;font:400 16.5px/1.3 'Jost',sans-serif;color:#e9e1cd;letter-spacing:.003em;
  text-shadow:0 1px 8px rgba(0,0,0,.55),0 1px 2px rgba(0,0,0,.5);white-space:nowrap}}
.url{{margin-top:12px;font:700 34px/1 'Jost',sans-serif;color:#fffaf0;letter-spacing:.005em;
  text-shadow:0 1px 10px rgba(0,0,0,.6),0 1px 2px rgba(0,0,0,.55)}}
.credit{{margin-top:9px;font:400 18px/1.35 'Jost',sans-serif;color:#d9cfb9;letter-spacing:.005em;
  text-shadow:0 1px 8px rgba(0,0,0,.6),0 1px 2px rgba(0,0,0,.5)}}
.credit i{{font-style:italic}}
"""
    return page("family=Jost:wght@400;500;600;700;800;900", css, body, lang)
