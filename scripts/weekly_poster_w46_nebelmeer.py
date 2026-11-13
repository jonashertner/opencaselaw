"""Week 46 (6 - 12 November 2026), German: Nebelmeer.

Switzerland's relief from swisstopo's height model, seen from straight above, with a sea of fog
whose upper limit stands at exactly 1,132 metres above sea level: one metre for each of the week's
new decisions. Everything lower lies under the fog (52 % of the country: the Mittelland, the lake
basins, the floors of the Alpine valleys); the Jura ridges and the Alps stand out of it. Below the
map the same fog in a section from Basel to Monte Rosa.

Real: the heights (DHM25/200 m, © swisstopo) and the border. Drawn: the light (from the north-west,
as on Swiss maps), the fog's thinning towards its shore, and the shadows the mountains throw on it.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w46_assets")
BG = (8, 11, 24)
ROCK_LO, ROCK_HI = np.array([14, 20, 44.0]), np.array([150, 168, 205.0])   # shadow side, lit side
RED = "#ff5a36"
FOG, FOG_SHADE = np.array([246, 243, 236.0]), np.array([176, 188, 214.0])
MAP_W = 1012                     # css px
MAP_X, MAP_Y = 34, 318
PROFILE = ((611.5, 267.5), (633.9, 87.3))        # Basel, Dufourspitze (km)


def heights() -> tuple[np.ndarray, dict]:
    meta = json.loads((ASSETS / "dhm200_ch.json").read_text())
    z = np.asarray(Image.open(ASSETS / "dhm200_ch.png")).astype(np.float32)
    return z, meta


def shade(z: np.ndarray, inside: np.ndarray, cell: float) -> np.ndarray:
    """Relief shading 0..1, light from the north-west, with a little light from the west and north
    so that no slope goes flat."""
    zz = np.where(inside, z, np.nan)
    fill = np.where(inside, z, np.nanmean(zz))
    gy, gx = np.gradient(fill, cell)
    out = np.zeros_like(fill)
    for az, wgt in ((315, .6), (270, .2), (0, .2)):
        a, alt = np.radians(az), np.radians(38)
        lx, ly, lz = np.sin(a) * np.cos(alt), np.cos(a) * np.cos(alt), np.sin(alt)
        out += wgt * np.clip((-gx * lx + gy * ly + lz) / np.sqrt(gx ** 2 + gy ** 2 + 1), 0, 1)
    return np.clip((out - .25) / .62, 0, 1)


def cast(top: np.ndarray, cell: float, alt_deg: float = 16.0, reach_m: float = 26000.0) -> np.ndarray:
    """Shadow (0..1) that the surface `top` throws on itself with the light from the north-west."""
    rise = np.tan(np.radians(alt_deg)) * cell * np.sqrt(2)       # metres of light ray per diagonal cell
    lit = np.ones_like(top)
    for k in range(1, int(reach_m / (cell * np.sqrt(2))) + 1):
        up = np.full_like(top, -1e9)
        up[k:, k:] = top[:-k, :-k]                               # the cell k steps to the north-west
        lit = np.minimum(lit, np.clip(1 - (up - k * rise - top) / 60.0, 0, 1))
    return 1 - lit


def render(level: float, width: int) -> Image.Image:
    """The map as an image of the given pixel width, on the page's ground."""
    z1, meta = heights()
    inside = z1 > 0
    z = z1 - 1
    cell = meta["cell"] * 1000
    s = shade(z, inside, cell)
    rock = ROCK_LO + (ROCK_HI - ROCK_LO) * (s ** 1.25)[..., None]
    depth = np.where(inside, level - z, 0)
    fog = np.clip(1 - np.exp(-np.clip(depth, 0, None) / 55.0), 0, 1)        # thin at the shore
    top = np.where(inside, np.maximum(z, level), level)
    shadow = cast(top, cell) * (depth > 0)
    fog_rgb = FOG + (FOG_SHADE - FOG) * (0.85 * shadow)[..., None]
    rgb = rock * (1 - fog[..., None]) + fog_rgb * fog[..., None]
    rgb = np.where(inside[..., None], rgb, np.array(BG, dtype=float))
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    h = round(width * z.shape[0] / z.shape[1])
    img = img.resize((width, h), Image.LANCZOS)
    # a soft glow of the fog on the ground round the country
    glow = Image.fromarray((np.where(inside, fog, 0) * 255).astype(np.uint8)).resize((width, h), Image.BILINEAR)
    halo = np.asarray(glow.filter(ImageFilter.GaussianBlur(width / 60)), dtype=float)[..., None] / 255
    edge = np.asarray(Image.fromarray((inside * 255).astype(np.uint8)).resize((width, h), Image.LANCZOS), dtype=float)[..., None] / 255
    out = np.asarray(img, dtype=float) * edge + (np.array(BG) + (FOG_SHADE - np.array(BG)) * 0.16 * halo) * (1 - edge)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def profile(n: int = 900) -> tuple[np.ndarray, np.ndarray]:
    """Heights along the straight line Basel - Dufourspitze: (km along the line, metres)."""
    z1, meta = heights()
    (e0, n0), (e1, n1) = PROFILE
    t = np.linspace(0, 1, n)
    e, nn = e0 + (e1 - e0) * t, n0 + (n1 - n0) * t
    c, r = (e - meta["e0"]) / meta["cell"] - .5, (meta["n1"] - nn) / meta["cell"] - .5
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    g = lambda rr, cc: z1[np.clip(rr, 0, z1.shape[0] - 1), np.clip(cc, 0, z1.shape[1] - 1)] - 1  # noqa: E731
    z = (g(r0, c0) * (1 - fc) + g(r0, c0 + 1) * fc) * (1 - fr) + (g(r0 + 1, c0) * (1 - fc) + g(r0 + 1, c0 + 1) * fc) * fr
    return t * float(np.hypot(e1 - e0, n1 - n0)), z


def uri(img: Image.Image) -> str:
    data = io.BytesIO()
    img.save(data, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()


def design(week: dict, lang: str) -> str:
    level = week["added"]
    n = wp.fmt(level, lang)
    z1, meta = heights()
    under = float(((z1 > 0) & (z1 - 1 < level)).sum() / (z1 > 0).sum())
    img = render(level, MAP_W * 2)
    map_h = img.height / 2
    to_px = lambda e, nn: (MAP_X + (e - meta["e0"]) / (z1.shape[1] * meta["cell"]) * MAP_W,  # noqa: E731
                           MAP_Y + (meta["n1"] - nn) / (z1.shape[0] * meta["cell"]) * map_h)

    # the section
    km, z = profile()
    last = int(np.nonzero(z > 0)[0][-1])                         # the line ends on the border
    km, z = km[:last + 1], z[:last + 1]
    PX0, PX1, PY0 = 62.0, 1018.0, 1180.0                         # the section's base line
    zmax = 4634.0
    PH = 8 * (PX1 - PX0) / km[-1] * zmax / 1000                  # heights drawn eight times too tall
    x = PX0 + km / km[-1] * (PX1 - PX0)
    y = PY0 - z / zmax * PH
    yf = PY0 - level / zmax * PH
    land = f"M{PX0},{PY0}L" + "L".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y)) + f"L{PX1},{PY0}Z"
    (ax, ay), (bx, by) = to_px(*PROFILE[0]), to_px(*PROFILE[1])

    css = f"""
body{{background:rgb{BG};color:#f4f1ea;font-family:'Inter Tight',sans-serif}}
.n{{position:absolute;left:50px;top:8px;font:400 286px/1 'Instrument Serif',serif;letter-spacing:-.035em}}
.t{{position:absolute;left:640px;top:92px;font:400 54px/1 'Instrument Serif',serif}}
.t i{{display:block;font:500 17px/1.4 'Inter Tight',sans-serif;font-style:normal;letter-spacing:.09em;
     text-transform:uppercase;color:#93a0c4;margin-top:14px}}
.map{{position:absolute;left:{MAP_X}px;top:{MAP_Y}px;width:{MAP_W}px;height:{map_h:.1f}px}}
svg{{position:absolute;left:0;top:0}}
svg text{{font:500 14px 'Inter Tight',sans-serif;fill:#93a0c4;letter-spacing:.05em}}
svg text.w{{fill:#f4f1ea;font-size:16px;letter-spacing:.02em}}
.k{{position:absolute;left:62px;top:1222px;width:720px;font:400 31px/1.12 'Instrument Serif',serif}}
.c{{position:absolute;left:62px;top:1312px;font:400 13px/1 'Inter Tight',sans-serif;letter-spacing:.05em;color:#7480a6}}
.u{{position:absolute;right:58px;top:1304px;font:600 20px/1 'Inter Tight',sans-serif}}
"""
    body = f"""<img class="map" src="{uri(img)}" alt="">
<div class="n">{n}</div>
<div class="t">neue Entscheide<i>Woche {week['week_no']} · {week['from'].day}.–{week['to'].day}. November {week['to'].year}</i></div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{RED}" stroke-width="1.4"/>
<circle cx="{ax:.1f}" cy="{ay:.1f}" r="3.2" fill="{RED}"/><circle cx="{bx:.1f}" cy="{by:.1f}" r="3.2" fill="{RED}"/>
<rect x="{PX0}" y="{yf:.1f}" width="{PX1 - PX0}" height="{PY0 - yf:.1f}" fill="#f4f1ea"/>
<path d="{land}" fill="#3a4b7a"/>
<line x1="{PX0}" y1="{PY0}" x2="{PX1}" y2="{PY0}" stroke="{RED}" stroke-width="1.4"/>
<text x="{PX0}" y="{yf - 10:.1f}" class="w">{n} m</text>
<text x="{PX0}" y="{PY0 + 22}">Basel</text>
<text x="{PX1}" y="{PY0 + 22}" text-anchor="end">Monte Rosa</text>
</svg>
<div class="k">Nebelobergrenze {n} Meter: ein Meter für jeden neuen Entscheid. Darunter liegen {round(under * 100)} Prozent der Schweiz.</div>
<div class="c">Höhenmodell DHM25 © swisstopo · Schnitt Basel–Monte Rosa, achtfach überhöht</div>
<div class="u">opencaselaw.ch</div>"""
    return page("family=Instrument+Serif:ital@0;1&family=Inter+Tight:wght@400;500;600", css, body, lang)
