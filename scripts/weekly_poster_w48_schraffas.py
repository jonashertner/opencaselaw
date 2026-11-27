"""Week 48 (20 - 26 November 2026), Romansh: Schraffas -- the hachures of the Dufour map, Blatt XV.

v3 (art-directed twice; see weekly_poster_w48_schraffas_v1.py/_v2.py and the matching
rm-schraffas-v1/v2.png for the earlier states and why each was revised). Sheet XV "Davos -
Martinsbruck" of the Dufour Map (1853, the first official topographic survey of Switzerland)
covers the Lower Engadine -- Romansh heartland: Schuls (Scuol), Tarasp, S-charl, the peaks above
them. Its relief is drawn entirely in copper-engraved hachures, short strokes laid along the line
of steepest descent -- no contour lines, the whole mountain built from thousands of individual
engraved marks.

v1 enlarged its crop only 1.2x (hachures stayed map-sized, the figure needed a scrim). v2 cropped
600x750 px of the source and enlarged it 3.6x, so each stroke reads as a brush-like mark -- but
chose its 861 red strokes as the 861 nearest a single point, which read as a disc with a ragged
fringe, unrelated to the landform, and put the figure where it collided with both the red strokes
and the map's own lettering.

v3 keeps v2's crop (the dark hachured spur between Piz Ayrutz and Piz Lischana, falling into the
Vadret Lischanna glacier; full-resolution box 9650,3625-10250,4375 of the 11786x8600 source -- see
SOURCES.json for the other candidates tried) but changes the selection rule and the colour: the 861
are now chosen as the strokes nearest the ridge's own crest line (RIDGE_POLYLINE in
weekly_poster_w48_schraffas_build.py, traced by eye along the engraving's own rock-edge line), not
a point -- real alpenglow lights the top of the relief, so the red now runs along the crest and
falls away down both flanks, tapering to nothing well before the glacier. The carmine plate's
colour floor is raised (a gamma curve on the ink density, chosen-strokes only) so a thin or faint
stroke still prints solid, not pale. Every number here is asserted again below and in the build
script against week["added"]; strokes_manifest.json (version 3) records the ridge polyline and the
exact rule.

The figure and the footer were both repositioned off the recoloured strokes and off the map's own
lettering entirely: "861" sits in the calm, almost bare patch of glacier to the right of the ridge,
clear of both the red flank and the "Vadret Lischanna" script (which runs through the *centre* of
the glacier, not its right edge); the three footer lines sit in the narrow clean band of paper
between that script and the P. Cornet massif at the very bottom. Neither needs a scrim or a
knockout -- both patches are the scan's own bare paper, with at most a few faint, unlabelled
contour-wisp lines under them.

Romansh edition: almost no words, per standing instruction. "Emna 48", the dates as digits (the
Rumantsch Grischun month name is not used here for lack of a proofreader to check it), the figure,
and the source credit. No other word is added.

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w48_assets") / "schraffas"
BG_PATH = ASSETS / "schraffas_2160x2700.jpg"
MANIFEST = ASSETS / "strokes_manifest.json"

CARMINE = "#c8102e"
INK = "#241f1b"   # the graphite/footer ink -- matches the recolour's own warm-dark tone


def design(week: dict, lang: str = "rm") -> str:
    assert lang == "rm", "the Schraffas poster is Romansh-only"
    n = week["added"]
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["added"] == manifest["chosen"] == n == 861, (manifest, n)
    assert manifest["version"] == 3

    bg_b64 = base64.b64encode(BG_PATH.read_bytes()).decode()

    a, b = week["from"], week["to"]
    date_line = f"20.–26.11.{b.year}" if (a.day, b.day) == (20, 26) else f"{a.day}.–{b.day}.{b.month}.{b.year}"
    week_line = f"Emna {week['week_no']}"
    n_txt = f"{n:,}".replace(",", "’")  # Swiss thousands separator

    body = f"""
<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="num">{esc(n_txt)}</div>
<div class="key">Mintga schraffa cotschna è ina decisiun.</div>
<div class="foot">
  <div class="week">{esc(week_line)} &middot; {esc(date_line)}</div>
  <div class="credit">Dufour, Blatt XV, 1853 &middot; &copy;swisstopo</div>
  <div class="url">opencaselaw.ch</div>
</div>"""

    css = f"""
body{{background:#f7f2e4}}
.bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}}
.num{{position:absolute;left:560px;right:34px;top:775px;text-align:center;
  font:900 300px/.82 'Cinzel',serif;letter-spacing:-.01em;color:{CARMINE};
  font-variant-numeric:lining-nums tabular-nums}}
.foot{{position:absolute;left:0;right:0;bottom:92px;text-align:center}}
.week{{font:600 27px/1.2 'Cormorant Garamond',serif;color:{INK};letter-spacing:.02em}}
.key{{position:absolute;left:560px;right:34px;top:1036px;text-align:center;font:italic 700 26px/1.25 'Cormorant Garamond',serif;color:{CARMINE};letter-spacing:.01em;text-shadow:0 0 6px #f7f2e4,0 0 12px #f7f2e4,0 0 18px #f7f2e4}}
.credit{{margin-top:8px;font:italic 400 16px/1.35 'Cormorant Garamond',serif;color:{INK};
  opacity:.82;letter-spacing:.005em}}
.url{{margin-top:6px;font:700 23px/1 'Cormorant Garamond',serif;color:{INK};letter-spacing:.02em}}
"""
    return page("family=Cinzel:wght@600;900&family=Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400",
                 css, body, lang)
