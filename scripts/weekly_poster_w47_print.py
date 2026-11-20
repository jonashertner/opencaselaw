"""Week 47 (13 - 19 November 2026), English: The Week's Fingerprint, Literally -- as a risograph.

A real whorl from E. R. Henry's "Classification and Uses of Finger Prints" (London, 1900) -- print
no. 139 on the book's own "Tracing the Ridges" teaching plate (Henry died 1931; public domain, see
weekly_poster_w47_assets/print/SOURCES.json) -- redrawn as exactly 974 traced ridge strokes, one per
new decision, printed as a screenprint/risograph: three vivid ink plates (one per language) and a
fourth, the huge "974" itself in fluorescent pink -- not black, which would just stay black under a
multiply overprint -- so the ridges that cross it genuinely mix: blue over pink reads violet,
vermilion over pink a hot red, yellow over pink orange. Every plate carries real, visible riso grain
(coarse blotches of uneven ink coverage, fine speckle, and displaced/ragged stroke edges, tuned to
read at full view, not only at 100%) plus slight per-plate misregistration and a faint paper haze.

Nothing here is procedurally drawn. The scan was binarised and skeletonised with a from-scratch
Zhang-Suen thinning (numpy only, no scipy); the skeleton was traced into real ridge polylines, and a
distance transform (iterative erosion, numpy only) gave each point along them the scan's own local
ink thickness, smoothed with a moving average and clamped to a narrow range so a ridge tapers rather
than beads. A handful of scan artefacts -- the printed plate's own table-rule line and a few other
long, almost perfectly straight runs no real whorl ridge would make -- were identified by straightness
and position and dropped before the count was fixed. A short, near-straight bridge directly joining
two otherwise-unconnected junctions ("ladder rung": scan noise bridging two adjacent ridges) was
pruned the same way. What's left was merged or split -- never invented -- until there are exactly 974
(asserted below), each carrying its own real, varying width. They are ordered by distance from the
whorl's own core and assigned to three ink plates, core outward: the innermost 40 (Italian), the next
381 (French), the outer 553 (German) -- matching this week's language split exactly. The extraction
is a one-off (not run at render time); its numeric output ships as scripts/weekly_poster_w47_assets/
print/segments_974.json, which this module only draws.

The page is scaled to CONTAIN the whole traced print (never to crop it): asserted below, every one of
the 974 segments keeps at least 90% of its own length on the page. The print's own aspect leaves a
little room below it, filled edge to edge by the huge "974" itself -- its own ink plate, in solid
deep ink with its own grain, printed first; the three ridge plates overprint it with a multiply blend,
so ridges crossing the numeral darken into it rather than being masked away.

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w47_assets") / "print"

SEGMENTS = json.loads((ASSETS / "segments_974.json").read_text(encoding="utf-8"))
EXPECTED_COUNTS = {"de": 553, "fr": 381, "it": 40}
assert len(SEGMENTS) == 974, f"expected 974 ridge segments, found {len(SEGMENTS)}"
for _lang, _n in EXPECTED_COUNTS.items():
    _have = sum(1 for s in SEGMENTS if s["lang"] == _lang)
    assert _have == _n, f"{_lang}: expected {_n} ridge segments, found {_have}"

# Fit the WHOLE traced print inside the page -- a "contain" fit on the segments' own real bounding
# box (padded a little for safety), never a crop. Height is the slack dimension: content is pushed
# to touch the top edge, and the leftover at the bottom is filled by the huge "974" plate itself.
_xs = [p[0] for s in SEGMENTS for p in s["pts"]]
_ys = [p[1] for s in SEGMENTS for p in s["pts"]]
_PAD = 4.0
_X0, _X1 = min(_xs) - _PAD, max(_xs) + _PAD
_Y0, _Y1 = min(_ys) - _PAD, max(_ys) + _PAD

SCALE = min(W / (_X1 - _X0), H / (_Y1 - _Y0))
OFFSET_X = -_X0 * SCALE
SHIFT_Y = -_Y0 * SCALE + 2.0  # +2px: a hair of paper so no point sits exactly on the page edge
CORE_SKEL = (523.5, 454.5)  # (x, y): the whorl's own core, in that skeleton-pixel space
WIDTH_MULT = 1.8  # local ridge half-width (skeleton px) -> stroke width, tuned for ~40% coverage

# risograph-style plate inks: ultramarine / vermilion / fluorescent yellow, overprinted on a
# fourth plate -- the huge "974" -- in fluorescent pink, chosen because it MIXES on overprint
# (black would just stay black): blue over pink -> violet, vermilion over pink -> hot red,
# yellow over pink -> orange (all verified by multiplying the actual hex values).
INK = {"de": "#1026e0", "fr": "#ff3414", "it": "#f2ff1e"}
NAME = {"de": "German", "fr": "French", "it": "Italian"}
NUM_INK = "#ff48b0"
TEXT_INK = "#120f0b"
PAPER = "#f7f6f0"
# slight per-plate misregistration (px, design space) -- a real screenprint never lands perfectly
REGISTER = {"de": (2.5, -2.0), "fr": (-3.0, 2.0), "it": (2.0, 3.0)}


def _tx(x: float, y: float) -> tuple[float, float]:
    return x * SCALE + OFFSET_X, y * SCALE + SHIFT_Y


def _assert_all_segments_on_page(min_fraction: float = 0.90) -> None:
    """Every one of the 974 segments must be on the page -- not just mostly survive a crop. Checks
    each segment's own bounding box intersects the page, and that at least `min_fraction` of its
    own arc length lies within it."""
    for s in SEGMENTS:
        pts = [_tx(x, y) for x, y in s["pts"]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert not (max(xs) < 0 or min(xs) > W or max(ys) < 0 or min(ys) > H), \
            f"segment entirely off the page: {s['lang']} r={s.get('r')}"
        total = inside = 0.0
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            seg_len = math.hypot(x1 - x0, y1 - y0)
            total += seg_len
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            if 0 <= mx <= W and 0 <= my <= H:
                inside += seg_len
        frac = 1.0 if total == 0 else inside / total
        assert frac >= min_fraction, \
            f"segment only {frac:.0%} on the page (need >={min_fraction:.0%}): {s['lang']} r={s.get('r')}"


_assert_all_segments_on_page()


def _micro_strokes(pts: list[tuple[float, float]], widths: list[float]) -> list[str]:
    """A variable-width ink stroke as a chain of short round-capped/round-joined SVG strokes, one
    per point-to-point step (stroke-width = that step's real local ridge thickness). SVG's own
    round joins and caps do the rounding -- not a hand-built offset polygon -- so every join is
    geometrically exact: no self-intersecting zigzag at a sharp turn, and no bead, since a cap's
    radius is just that point's real local half-width, never an independent bulge."""
    out = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        w = widths[i] + widths[i + 1]
        out.append(f'<path d="M{x0:.2f} {y0:.2f} L{x1:.2f} {y1:.2f}" stroke-width="{w:.2f}"/>')
    return out


def _grain_filter(fid: str, seed: int, strength: float = 0.55, blotch_freq: float = 0.045,
                   fine_freq: float = 0.9, edge_scale: float = 3.0) -> str:
    """Real riso/screenprint texture for one plate, tuned to be visible at full view, not just
    100%: a COARSE turbulence makes big blotches of lighter/heavier ink coverage (uneven squeegee
    pressure); a FINE turbulence punches small flecks/holes into the ink (speckle); a third,
    independent turbulence displaces the stroke geometry itself (feDisplacementMap), so edges come
    out ragged rather than a clean vector outline. All three are composited 'in' the plate's own
    shape, so texture never spills outside the ink."""
    seed2, seed3 = seed + 17, seed + 31
    return (f'<filter id="{fid}" x="-30%" y="-30%" width="160%" height="160%" '
            f'color-interpolation-filters="sRGB">'
            f'<feTurbulence type="fractalNoise" baseFrequency="0.12" numOctaves="2" seed="{seed3}" result="edgeN"/>'
            f'<feDisplacementMap in="SourceGraphic" in2="edgeN" scale="{edge_scale:.1f}" '
            f'xChannelSelector="R" yChannelSelector="G" result="ragged"/>'
            f'<feTurbulence type="fractalNoise" baseFrequency="{blotch_freq}" numOctaves="2" seed="{seed}" result="blotch"/>'
            f'<feColorMatrix in="blotch" type="matrix" '
            f'values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.4 0.4 0.4 0 0.05" result="blotchA"/>'
            f'<feTurbulence type="fractalNoise" baseFrequency="{fine_freq}" numOctaves="2" seed="{seed2}" result="fine"/>'
            f'<feColorMatrix in="fine" type="matrix" '
            f'values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.33 0.33 0.33 0 {1 - strength:.2f}" result="fineA"/>'
            f'<feComposite in="fineA" in2="blotchA" operator="in" result="speckle"/>'
            f'<feComposite in="ragged" in2="speckle" operator="in"/></filter>')


def _haze_filter() -> str:
    """A faint ink haze/dust over the whole paper, independent of any one plate -- the kind of
    mottling real uncoated stock picks up under a riso drum."""
    return ('<filter id="g-haze" x="0%" y="0%" width="100%" height="100%">'
            '<feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="5" result="n"/>'
            '<feColorMatrix in="n" type="matrix" '
            'values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.5 0.5 0.5 0 -0.72"/></filter>')


def _ridge_plate(lang: str) -> str:
    """One ink plate: every point of every one of this language's segments -- no cropping, no
    dropped points, so the full per-language count stays intact -- offset a few px for
    misregistration, textured with its own grain, and overprinted with a multiply blend."""
    strokes = []
    for s in SEGMENTS:
        if s["lang"] != lang:
            continue
        pts = [_tx(x, y) for x, y in s["pts"]]
        widths = [w * WIDTH_MULT for w in s["w"]]
        strokes.extend(_micro_strokes(pts, widths))
    dx, dy = REGISTER[lang]
    return (f'<g transform="translate({dx},{dy})" style="mix-blend-mode:multiply" '
            f'filter="url(#g-{lang})"><g stroke="{INK[lang]}" fill="none" stroke-linecap="round" '
            f'stroke-linejoin="round">{"".join(strokes)}</g></g>')


def design(week: dict, lang: str) -> str:
    assert lang == "en", "the print poster is English-only"
    n = week["added"]
    assert n == 974, f"this poster's ridge count is fixed at 974 new decisions; got {n}"
    assert week["languages"] == EXPECTED_COUNTS, \
        f"ridge colouring was baked for {EXPECTED_COUNTS}; week['languages'] is {week['languages']}"

    a, b = week["from"], week["to"]
    n_fmt = esc(wp.fmt(n, lang))

    filters = "".join([
        _grain_filter("g-de", 11),
        _grain_filter("g-fr", 61, blotch_freq=0.05, fine_freq=0.95),
        _grain_filter("g-it", 113, blotch_freq=0.04, fine_freq=0.85, strength=0.5),
        _grain_filter("g-num", 7, blotch_freq=0.035, fine_freq=0.6, strength=0.45, edge_scale=2.2),
    ]) + _haze_filter()
    haze_layer = (f'<rect width="{W}" height="{H}" fill="#2a1f14" filter="url(#g-haze)" '
                  f'opacity="0.5" style="mix-blend-mode:multiply"/>')

    number = (f'<g filter="url(#g-num)" style="mix-blend-mode:multiply">'
              f'<text x="{W / 2}" y="1080" text-anchor="middle" font-family="Playfair Display" '
              f'font-size="600" font-weight="900" letter-spacing="-0.03em" fill="{NUM_INK}">'
              f'{n_fmt}</text></g>')

    tagline = (f'<text x="{W / 2}" y="1165" text-anchor="middle" font-family="Playfair Display" '
               f'font-size="38" font-style="italic" font-weight="500" fill="{TEXT_INK}">'
               f'new decisions in Swiss case law</text>')

    # key: dark, legible text with a solid swatch of each plate's own ink before the word (not
    # the ink itself as the text colour -- fluorescent yellow text on pale paper is illegible)
    key_y, key_size, sw_w, sw_h, gap = 1205, 23, 30, 10, 8
    col_centers = [W / 2 - 290, W / 2, W / 2 + 290]
    key_parts = []
    for k, cx in zip(("de", "fr", "it"), col_centers):
        label = f"{NAME[k]} {wp.fmt(EXPECTED_COUNTS[k], lang)}"
        text_w = len(label) * key_size * 0.56  # rough estimate, centres the swatch+label block
        block_w = sw_w + gap + text_w
        x0 = cx - block_w / 2
        sw_y = key_y - sw_h * 0.72
        key_parts.append(f'<rect x="{x0:.1f}" y="{sw_y:.1f}" width="{sw_w}" height="{sw_h}" '
                          f'rx="2" fill="{INK[k]}"/>')
        key_parts.append(f'<text x="{x0 + sw_w + gap:.1f}" y="{key_y}" text-anchor="start" '
                          f'font-family="EB Garamond" font-size="{key_size}" font-weight="700" '
                          f'fill="{TEXT_INK}">{esc(label)}</text>')
    key_line = "".join(key_parts)

    week_text = f"WEEK {week['week_no']} · {a.day}–{b.day} {b:%B %Y}".upper()
    weekurl = (f'<text x="{W / 2}" y="1245" text-anchor="middle" font-family="EB Garamond" '
               f'font-size="20" font-weight="600" letter-spacing="0.04em" fill="{TEXT_INK}">'
               f'{esc(week_text)} &#183; OPENCASELAW.CH</text>')

    credit = (f'<text x="{W / 2}" y="1277" text-anchor="middle" font-family="EB Garamond" '
              f'font-size="15" font-style="italic" fill="{TEXT_INK}" opacity="0.78">'
              f'Whorl: E. R. Henry, Classification and Uses of Finger Prints, London 1900 '
              f'&#183; public domain</text>')

    plates = "".join(_ridge_plate(k) for k in ("de", "fr", "it"))
    body = f"""
<svg viewBox="0 0 {W} {H}">
<defs>{filters}</defs>
<rect width="{W}" height="{H}" fill="{PAPER}"/>
{number}
{plates}
{haze_layer}
{tagline}{key_line}{weekurl}{credit}
</svg>"""

    css = f"body{{background:{PAPER}}}\ntext{{font-variant-numeric:lining-nums tabular-nums}}"
    return page("family=Playfair+Display:ital,opsz,wght@0,5..1200,400..900;1,5..1200,400..900"
                "&family=EB+Garamond:ital,wght@0,400..800;1,400..800", css, body, lang)
