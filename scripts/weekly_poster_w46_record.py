"""Record the W46 map as a LinkedIn video: 1440x1800, 30 fps, on a virtual clock (requestAnimationFrame
and performance.now advanced by hand, so every frame is exact whatever the headless GPU does).

    python3 scripts/weekly_poster_w46_record.py output/posters/w46/map.html output/posters/w46/map.mp4
"""
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

page_path, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
frames = out.parent / "frames46"
frames.mkdir(parents=True, exist_ok=True)
for f in frames.glob("*.png"):
    f.unlink()
FPS, SECONDS = 30, 17.5
DT = 1000 / FPS

CLOCK = """
(()=>{let now=0;const q=[];let id=0;
 window.requestAnimationFrame=cb=>{q.push([++id,cb]);return id};
 window.cancelAnimationFrame=i=>{const k=q.findIndex(e=>e[0]===i);if(k>=0)q.splice(k,1)};
 performance.now=()=>now;
 window.__tick=dt=>{now+=dt;
   for(const a of document.getAnimations()){a.pause();a.currentTime=(a.currentTime||0)+dt}
   const run=q.splice(0);for(const[,cb]of run)cb(now)};
})();
"""

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(args=["--use-gl=angle", "--ignore-gpu-blocklist"])
    except Exception:
        browser = p.chromium.launch(channel="chrome", args=["--ignore-gpu-blocklist"])
    pg = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=4 / 3)
    pg.on("console", lambda m: print("console:", m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: print("pageerror:", e))
    pg.add_init_script(CLOCK)
    pg.goto(page_path.as_uri(), wait_until="networkidle")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(600)

    def at(e, n):   # a km point on screen
        return tuple(pg.evaluate(f"(()=>{{const r=document.getElementById('map').getBoundingClientRect();"
                                 f"return [r.left+({e}-D.box[0])/D.box[2]*r.width, r.top+(D.box[1]-{n})/D.box[3]*r.height]}})()"))

    bern, jungfrau, off = at(600.0, 199.7), at(641.2, 155.4), (1040, 1000)

    def ease(t):
        return t * t * (3 - 2 * t)

    def path(t):
        legs = [(13.0, 13.9, off, bern), (14.9, 15.9, bern, jungfrau), (16.9, 17.4, jungfrau, off)]
        for a, b, p0, p1 in legs:
            if a <= t <= b:
                u = ease((t - a) / (b - a))
                return (p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u)
        return None

    pg.mouse.move(*off)
    for i in range(int(FPS * SECONDS)):
        xy = path(i / FPS)
        if xy:
            pg.mouse.move(*xy)
        pg.evaluate(f"window.__tick({DT})")
        pg.screenshot(path=str(frames / f"{i:04d}.png"))
    browser.close()

subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "%04d.png"),
                "-vf", "scale=1440:1800:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-movflags", "+faststart", str(out)], check=True)
print(out, out.stat().st_size)
