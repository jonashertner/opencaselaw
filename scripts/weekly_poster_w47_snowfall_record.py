#!/usr/bin/env python3
"""Record the W47 film as a LinkedIn video: 1440 x 1800, 30 fps, on a virtual clock
(requestAnimationFrame and performance.now advanced by hand, so every frame is exact whatever the
headless GPU does).

    python3 scripts/weekly_poster_w47_snowfall_record.py output/posters/w47/snowfall.html output/posters/w47/snowfall.mp4
    python3 scripts/weekly_poster_w47_snowfall_record.py <html> <dir> --stills 0.4 3 8 12 17   # PNG stills only
"""
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

page_path, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
stills = [float(v) for v in sys.argv[sys.argv.index("--stills") + 1:]] if "--stills" in sys.argv else None
FPS, SECONDS = 30, 20.0
DT = 1000 / FPS

CLOCK = """
(()=>{let now=0;const q=[];let id=0;
 window.requestAnimationFrame=cb=>{q.push([++id,cb]);return id};
 window.cancelAnimationFrame=i=>{const k=q.findIndex(e=>e[0]===i);if(k>=0)q.splice(k,1)};
 performance.now=()=>now;
 window.__tick=dt=>{now+=dt;const run=q.splice(0);for(const[,cb]of run)cb(now)};
})();
"""

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu"])
    except Exception:
        browser = p.chromium.launch(channel="chrome", args=["--ignore-gpu-blocklist"])
    pg = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=4 / 3)
    pg.on("console", lambda m: print("console:", m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: print("pageerror:", e))
    pg.add_init_script(CLOCK)
    pg.goto(page_path.as_uri(), wait_until="networkidle")
    pg.wait_for_function("window.__ready === true", timeout=60000)
    pg.evaluate("window.__tick(0)")            # the first frame starts the film's clock at 0
    if stills is not None:
        out.mkdir(parents=True, exist_ok=True)
        t = 0.0
        for s in sorted(stills):
            pg.evaluate(f"window.__tick({(s - t) * 1000})")
            t = s
            pg.screenshot(path=str(out / f"still_{s:05.2f}.png"))
            print(out / f"still_{s:05.2f}.png")
        browser.close()
        sys.exit(0)
    frames = out.parent / "frames47"
    frames.mkdir(parents=True, exist_ok=True)
    for f in frames.glob("*.png"):
        f.unlink()
    for i in range(int(FPS * SECONDS)):
        if i:
            pg.evaluate(f"window.__tick({DT})")
        pg.screenshot(path=str(frames / f"{i:04d}.png"))
    browser.close()

subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "%04d.png"),
                "-vf", "scale=1440:1800:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                "-movflags", "+faststart", str(out)], check=True)
print(out, out.stat().st_size)
