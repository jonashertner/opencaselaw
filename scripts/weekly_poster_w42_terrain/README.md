# Week-42 piste-map terrain (Weisshorn–Bishorn)

`terrain.png` (2160×1880, sky transparent) and `terrain_runs.npz` (skyline, screen-space
fall-line field, projected summits) are what `scripts/weekly_poster_w42_piste.py` draws on.

They were rendered from open elevation data: swissALTI3D 2 m tiles (© swisstopo, 216 tiles,
fetched with `fetch_dem.py` via the STAC API) mosaicked by `mosaic.py`, with Copernicus GLO-30
(© ESA) for the terrain beyond the fine mosaic (`glo_to_lv95.py`); camera from `solve_cam.py`,
view and light in `cfg_weiss.json`; `render_dem.py` does the ray march, shading, snow/rock/forest
and haze. The DEM tiles themselves (~320 MB) are not in the repo; rerun `fetch_dem.py` to get them.
Scratch venv for the pipeline: numpy, scipy, tifffile, imagecodecs, pillow.
