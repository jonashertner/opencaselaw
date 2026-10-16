#!/usr/bin/env python3
"""Download swissALTI3D 2 m tiles for a WGS84 bbox into dem/ (data dir; nothing executes from there)."""
import json, sys, urllib.request, concurrent.futures as cf
from pathlib import Path
HERE = Path(__file__).resolve().parent
OUT = HERE / "dem"; OUT.mkdir(exist_ok=True)
bbox = sys.argv[1] if len(sys.argv) > 1 else "9.28,47.19,9.44,47.28"
url = f"https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissalti3d/items?bbox={bbox}&limit=100"
hrefs = []
while url:
    d = json.load(urllib.request.urlopen(url, timeout=60))
    for f in d["features"]:
        for k, a in f["assets"].items():
            if k.endswith("_2_2056_5728.tif"):
                hrefs.append(a["href"])
    url = next((l["href"] for l in d.get("links", []) if l.get("rel") == "next"), None)
print(len(hrefs), "tiles")
def get(h):
    p = OUT / h.rsplit("/", 1)[1]
    if not p.exists():
        urllib.request.urlretrieve(h, p)
    return p.stat().st_size
with cf.ThreadPoolExecutor(12) as ex:
    sizes = list(ex.map(get, hrefs))
print(sum(sizes) / 1e6, "MB")
