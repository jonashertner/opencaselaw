"""Week 47 (13 - 19 November 2026), italiano: Fossili.

A slab of fine-grained, near-black bituminous shale, full-bleed, photographed as if under one low
raking light from the side -- the way a museum plate is lit to bring up every scale. 974 fossil
fishes are pressed into it, one per decision, and their SIZE is the week's own timeline: the 973
older decisions shrink toward the foot of a steep curve (most of them 16-30px, barely more than a
fleck of ink), while the single most recent decision becomes one monumental fish, 600-900px long,
sweeping diagonally across the whole slab and running off its edge -- re-cut from the same source
plate at several times the working resolution the small fish use, so its scales hold up at that
size. Depth by age is otherwise carried only as tone, sharpness and z-order (older = duller,
softer, drawn first/underneath; newer = brighter, crisper, drawn on top), never as screen position
-- the 973 sit on one even grid over the whole canvas, so there is no empty region and no edge
clump, only the one dramatic exception.

The fish are 7 real lithographs (plus the one giant, an 8th, higher-resolution cut of the same
plate as the smallest-numbered of the 7) from Louis Agassiz's *Recherches sur les poissons
fossiles* (Neuchatel, 1833-1845): a Swiss work, drawn and printed in Neuchatel (lithography by H.
Nicolet, the plates by J. Dinkel and others), scanned by BHL/Internet Archive from the Missouri
Botanical Garden copy. Public domain -- Agassiz died in 1873. Genera Lepidotus, Pholidophorus and
Tetragonolepis.

Each lithograph was cut out in two passes, not one: an ink/saturation threshold first, then --
this is what actually removes the rock matrix and the plate's own frame and guide lines -- a
connected-component pass that keeps only the single largest connected blob of ink (the fish
itself), fills any enclosed holes, and feathers the edge by 1-2px. Two plates whose matrix shading
is literally touching the fish's own ink at every threshold tried could not be separated cleanly
and were dropped rather than shipped with a visible rectangle; see SOURCES.json.

Colour is stripped deliberately: each cut-out becomes a single pale warm-grey impression, but the
recolouring is not a flat fill. A soft-blurred copy of the lithograph's own luminance gives the
overall shading; the *difference* between that and the sharp original (scale rows, fin rays, head
bones) is boosted back in, gently, as local contrast; and a low directional emboss -- one light
raking in from the side, the same direction over every sprite and over the ground itself via a
single page-wide gradient -- is built from that same high-frequency layer. Every one of those
three terms is combined through a smooth tanh soft-clip, never a hard cutoff, and the warm-grey
ink colour is itself capped well short of white -- so the brightest scale edge is still a pale
mineral grey, not a chrome highlight, and there is no posterised banding anywhere. No fish keeps
its original ochre/brown print colour, and none is a flat silhouette: at any size large enough to
resolve, the scale relief -- matte, like a limestone cast, not glossy -- is the point.

The "974" is not printed over the fish -- it is cut through them: the numeral's own glyphs are
filled with the *same ground photograph*, aligned pixel-for-pixel with the full-bleed background
and slightly polished (darkened, flattened) so it reads as bare shale exposed where the fish layer
has been sheared away, with a single thin incised bevel (dark upper-left inner edge, pale
lower-right inner edge) and a uniform dark interior otherwise -- no outline, no fill colour, no
stray highlight leaking into a counter. "nuove decisioni" sits on a short, soft, dark strip below
it -- legible pale stone-colour text, not another cut. The footer carries its own quiet darkened
band. Fish are never excluded from underneath any of this; the overlays simply sit on top. The one
giant fish keeps clear of the numeral by sitting in the lower half of the page, between the title
and the footer, so its head, eye, body and fins all stay fully legible -- only its tail grazes the
right edge.

The ground is a second real photograph: a CC0 bituminous-shale hand specimen (GeoDIL, via
Wikimedia Commons), a single large clean crop scaled up with no mirroring or repeat, darkened
toward near-black with the photograph's own row-to-row brightness kept as the only lamination.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import random
from datetime import date
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w47_assets") / "fossili"

INK = "#dccbA4"            # pale grey-ochre: the impression colour, fixed at cut-out time
INK_DIM = "#9a8f7c"
GROUND_B64 = base64.b64encode((ASSETS / "ground.jpg").read_bytes()).decode()

# the 7 distinct cleaned, recoloured fish for the 973 ordinary marks, each (filename, w, h)
FISH = [
    ("f1_pholidophorus_latus.png", 560, 222),
    ("f2_lepidotus_notopterus.png", 768, 395),
    ("f3_tab39_fig1.png", 244, 548),
    ("f4_tab39_fig3.png", 190, 556),
    ("f5_pholidophorus_micronyx.png", 116, 364),
    ("f6_pholidophorus_tenuiserratus.png", 159, 556),
    ("f7_tetragonolepis.png", 669, 392),
]
# the single most recent decision: the same Pholidophorus latus plate (f1) re-cut from a ~3.75x
# higher-resolution scan of the source page, so its scale rows hold up at 600-900px
GIANT = ("f0_giant_pholidophorus_latus.png", 2097, 729)
GIANT_LONG_PX = 650          # within the requested 600-900px range
# the lower half, between "nuove decisioni" and the footer band, nearly clear of the numeral --
# head at the left stays fully on the page; the tail just grazes the right edge
GIANT_CENTER = (750, 976)
GIANT_ROTATE = 6.0

# one even grid over the WHOLE canvas for the 973 ordinary fish -- 28 x 35 = 980 cells, trimmed.
# Depth (age) never determines a cell's position, only its tone/size/sharpness -- so there is no
# empty "old" region and no edge clumping; the one giant fish is placed separately, outside this grid.
GRID_COLS, GRID_ROWS = 28, 35
CELL_W, CELL_H = W / GRID_COLS, H / GRID_ROWS
assert GRID_COLS * GRID_ROWS >= 973

# extreme scale-by-recency curve for the 973 ordinary marks: most stay small (the oldest ~16-20px);
# only the last few percent, closest in date to the giant, grow toward a couple hundred px.
SIZE_LO, SIZE_HI, SIZE_POWER = 16.0, 190.0, 5.5
OPACITY_LO, OPACITY_HI = 0.50, 1.00
BRIGHT_LO, BRIGHT_HI = 0.82, 1.16
BLUR_LEVELS = (1.1, 0.75, 0.5, 0.3, 0.12, 0.0)   # oldest sixth -> newest sixth

# a few preferred "current" directions fish mostly align to, not tumbled at random
CURRENTS = (-11.0, 4.0, 17.0)


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _fish_defs() -> str:
    out = []
    for i, (fn, w, h) in enumerate(FISH):
        b64 = _b64(ASSETS / fn)
        out.append(f'<image id="f{i}" x="{-w/2}" y="{-h/2}" width="{w}" height="{h}" '
                   f'href="data:image/png;base64,{b64}"/>')
    gw, gh = GIANT[1], GIANT[2]
    out.append(f'<image id="giant" x="{-gw/2}" y="{-gh/2}" width="{gw}" height="{gh}" '
               f'href="data:image/png;base64,{_b64(ASSETS / GIANT[0])}"/>')
    return "".join(out)


def _blur_filters() -> str:
    out = []
    for lv in sorted({round(b * 10) for b in BLUR_LEVELS if b > 0.01}):
        out.append(f'<filter id="blur{lv}" x="-80%" y="-80%" width="260%" height="260%">'
                   f'<feGaussianBlur stdDeviation="{lv/10:.2f}"/></filter>')
    return "".join(out)


def _dated_ranks(week: dict) -> list[date]:
    """Every one of the week's 974 decisions, by its own decision date, oldest first. week["dates"]
    is {"CH"|"KT": {day: n}}; both are real per-day counts straight from the verified snapshot."""
    out: list[date] = []
    for cat in ("CH", "KT"):
        for day, n in week["dates"].get(cat, {}).items():
            d = date.fromisoformat(day)
            out.extend([d] * n)
    out.sort()
    assert len(out) == week["added"], (len(out), week["added"])
    return out


def _lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


def _place(rng: random.Random, fid: str, native_long: float, x: float, y: float, size_px: float,
           blur: float, opacity: float, bri: float, dest: list, deg: float | None = None,
           flip: int | None = None) -> None:
    if deg is None:
        deg = rng.choice(CURRENTS) + rng.uniform(-8, 8) if rng.random() < 0.85 else rng.uniform(-65, 65)
    if flip is None:
        flip = -1 if rng.random() < 0.5 else 1
    scale = size_px / native_long
    filt = f' filter="url(#blur{round(blur*10)})"' if blur > 0.01 else ""
    style = f' style="filter:brightness({bri:.2f})"' if abs(bri - 1) > 0.005 else ""
    dest.append(f'<use href="#{fid}" transform="translate({x:.1f},{y:.1f}) rotate({deg:.1f}) '
               f'scale({scale*flip:.4f},{scale:.4f})" opacity="{opacity:.2f}"{filt}{style}/>')


def _instances(rng: random.Random, dated: list[date]) -> str:
    """974 <use> placements, one per decision, in date order (so later -- newer, bigger, sharper
    -- fish paint on top where they overlap): 973 ordinary marks on an evenly shuffled grid (no
    correlation between position and age), plus the single newest decision as the one giant fish,
    placed on its own diagonal sweep rather than in the grid."""
    n = len(dated)
    regular_n = n - 1   # the newest decision becomes the giant, handled separately below
    cells = [((c + 0.5) * CELL_W, (r + 0.5) * CELL_H) for r in range(GRID_ROWS) for c in range(GRID_COLS)]
    rng.shuffle(cells)
    positions = cells[:regular_n]

    out: list[str] = []
    for i in range(regular_n):
        depth = i / (regular_n - 1)
        cx, cy = positions[i]
        x = cx + rng.uniform(-0.30, 0.30) * CELL_W
        y = cy + rng.uniform(-0.30, 0.30) * CELL_H
        size = (SIZE_LO + (SIZE_HI - SIZE_LO) * depth ** SIZE_POWER) * rng.uniform(0.78, 1.30)
        opacity = _lerp(OPACITY_LO, OPACITY_HI, depth ** 0.7)
        bri = _lerp(BRIGHT_LO, BRIGHT_HI, depth)
        blur = BLUR_LEVELS[min(len(BLUR_LEVELS) - 1, int(depth * len(BLUR_LEVELS)))]
        fid_idx = rng.randrange(len(FISH))
        native_long = max(FISH[fid_idx][1], FISH[fid_idx][2])
        _place(rng, f"f{fid_idx}", native_long, x, y, size, blur, opacity, bri, out)

    # the giant: the single newest decision, sharpest and brightest of all, drawn last (on top).
    # flip is fixed (never mirrored) -- GIANT_CENTER/_ROTATE were chosen for this exact orientation
    # (head at the sprite's native left, fully on the page; tail at the right, grazing the edge)
    gx, gy = GIANT_CENTER
    _place(rng, "giant", max(GIANT[1], GIANT[2]), gx, gy, GIANT_LONG_PX, 0.0, 1.0, 1.18, out,
           deg=GIANT_ROTATE, flip=1)
    return "".join(out)


TITLE = {"it": "nuove decisioni"}
MONTH_IT = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
            "settembre", "ottobre", "novembre", "dicembre"]


def design(week: dict, lang: str = "it") -> str:
    n = week["added"]
    assert n == 974, n
    dated = _dated_ranks(week)

    rng = random.Random(f"fossili-{n}-v5")

    defs_imgs = _fish_defs()
    blur_defs = _blur_filters()
    fish_svg = _instances(rng, dated)

    numeral_text = wp.fmt(n, "it")
    title_text = TITLE.get(lang, TITLE["it"])

    a, b = week["from"], week["to"]
    week_line = f"Settimana {week['week_no']} · {a.day}–{b.day} {MONTH_IT[b.month]} {b.year}"

    body = f"""
<img class="bg" src="data:image/jpeg;base64,{GROUND_B64}" alt="">
<svg class="fish" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>{blur_defs}{defs_imgs}</defs>
  {fish_svg}
</svg>
<div class="raking"></div>
<div class="title-strip"></div>
<div class="title">{esc(title_text)}</div>
<div class="numeral shadow">{esc(numeral_text)}</div>
<div class="numeral hi">{esc(numeral_text)}</div>
<div class="numeral cut">{esc(numeral_text)}</div>
<div class="foot-strip"></div>
<div class="foot">
  <div class="week">{esc(week_line)}</div>
  <div class="url">opencaselaw.ch</div>
  <div class="credit">Pesci fossili: Louis Agassiz, <i>Recherches sur les poissons fossiles</i>,
    Neuch&acirc;tel 1833&ndash;43 &middot; roccia: scisto bituminoso (GeoDIL, CC0)</div>
</div>"""

    NUM_FONT = "font:600 536px/.80 'Fraunces',serif;font-variation-settings:'opsz' 144,'SOFT' 4,'WONK' 0;" \
               "font-variant-numeric:lining-nums tabular-nums;letter-spacing:-.025em;"

    css = f"""
body{{background:#050302;color:{INK};font-family:'Fraunces',serif;overflow:hidden}}
.bg{{position:absolute;left:0;top:0;width:{W}px;height:{H}px;object-fit:cover;z-index:0}}
.fish{{position:absolute;left:0;top:0;z-index:1;pointer-events:none}}

/* one low, hard light raking in from the side across the whole slab -- the same direction the
   per-sprite emboss uses, so the fish and the stone read as lit by a single source */
.raking{{position:absolute;left:0;top:0;width:{W}px;height:{H}px;z-index:2;pointer-events:none;
  background:linear-gradient(100deg,rgba(226,214,188,.10) 0%,rgba(226,214,188,.03) 16%,
  rgba(0,0,0,0) 38%,rgba(0,0,0,.18) 66%,rgba(0,0,0,.34) 100%);mix-blend-mode:soft-light}}

/* "nuove decisioni": pale stone-colour text on a short, soft, dark strip -- not a cut */
.title-strip{{position:absolute;left:0;top:742px;width:{W}px;height:90px;z-index:3;
  background:radial-gradient(ellipse 480px 62px at 50% 50%,rgba(4,3,2,.62) 0%,rgba(4,3,2,.34) 55%,
  rgba(4,3,2,0) 85%)}}
.title{{position:absolute;left:0;top:770px;width:{W}px;text-align:center;z-index:3;
  font:400 54px/1 'Fraunces',serif;font-variation-settings:'opsz' 68;font-style:italic;
  letter-spacing:.01em;color:{INK};text-shadow:1px 2px 0 rgba(3,2,1,.6)}}

/* "974": cut through the fish layer (and the giant fish) down to the ground photo itself, not
   printed over it -- three stacked copies: a dark stroke peeking from the upper-left, a pale
   stroke peeking from the lower-right (together a single 2-3px incised bevel), and on top the
   actual cut window. Huge -- bigger than the page is tall at its own cap-height. */
.numeral{{position:absolute;left:0;top:232px;width:{W}px;text-align:center;{NUM_FONT}}}
.numeral.shadow{{z-index:4;color:transparent;-webkit-text-stroke:2px rgba(0,0,0,.65);
  text-stroke:2px rgba(0,0,0,.65);transform:translate(-1.5px,-1.5px)}}
.numeral.hi{{z-index:5;color:transparent;-webkit-text-stroke:2px rgba(196,182,152,.42);
  text-stroke:2px rgba(196,182,152,.42);transform:translate(1.5px,1.5px)}}
.numeral.cut{{z-index:6;background-image:url(data:image/jpeg;base64,{GROUND_B64});
  background-size:{W}px {H}px;background-position:0 -232px;background-repeat:no-repeat;
  -webkit-background-clip:text;background-clip:text;color:transparent;
  filter:brightness(.56) contrast(1.05) saturate(.75)}}

.foot-strip{{position:absolute;left:0;right:0;bottom:0;height:230px;z-index:3;
  background:linear-gradient(0deg,rgba(3,2,1,.74) 0%,rgba(3,2,1,.48) 48%,rgba(3,2,1,0) 100%)}}
.foot{{position:absolute;left:0;right:0;bottom:36px;text-align:center;z-index:7}}
.week{{font:600 24px/1 'Fraunces',serif;font-variation-settings:'opsz' 30;letter-spacing:.05em;
  color:{INK};text-shadow:0 1px 8px rgba(0,0,0,.85)}}
.url{{margin-top:11px;font:600 27px/1 'Archivo',sans-serif;letter-spacing:.05em;color:{INK};
  text-shadow:0 1px 8px rgba(0,0,0,.85)}}
.credit{{margin:12px auto 0;max-width:860px;font:400 14px/1.45 'Fraunces',serif;
  font-variation-settings:'opsz' 18;font-style:italic;color:{INK_DIM};
  text-shadow:0 1px 6px rgba(0,0,0,.9)}}
"""
    return page("family=Fraunces:ital,opsz,wght,SOFT,WONK@0,9..144,300..900,0..100,0..1;"
               "1,9..144,300..900,0..100,0..1&family=Archivo:wght@500;600", css, body, lang)
