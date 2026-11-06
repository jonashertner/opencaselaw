"""Record the W45 map as a LinkedIn video: 1440x1800, 30 fps, virtual clock (rAF, timers, Web Animations
advanced by hand, so every frame is exact whatever the headless GPU does)."""
import math, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

page_path, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
frames = out.parent / "frames45"
frames.mkdir(parents=True, exist_ok=True)
for f in frames.glob("*.png"):
    f.unlink()
FPS, SECONDS = 30, 14
DT = 1000 / FPS

CLOCK = """
(()=>{let now=0;const q=[];let id=0;const timers=[];
 window.requestAnimationFrame=cb=>{q.push([++id,cb]);return id};
 window.cancelAnimationFrame=i=>{const k=q.findIndex(e=>e[0]===i);if(k>=0)q.splice(k,1)};
 const pn=performance.now.bind(performance);performance.now=()=>now;
 window.setTimeout=(cb,ms)=>{timers.push([++id,now+(ms||0),cb]);return id};
 window.clearTimeout=i=>{const k=timers.findIndex(e=>e[0]===i);if(k>=0)timers.splice(k,1)};
 window.__tick=dt=>{now+=dt;
   for(const a of document.getAnimations()){a.pause();a.currentTime=(a.currentTime||0)+dt}
   timers.sort((a,b)=>a[1]-b[1]);while(timers.length&&timers[0][1]<=now){const t=timers.shift();t[2]()}
   const run=q.splice(0);for(const[,cb]of run)cb(now)};
})();
"""

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(args=["--use-gl=angle", "--ignore-gpu-blocklist"])
    except Exception:
        browser = p.chromium.launch(channel="chrome", args=["--ignore-gpu-blocklist"])
    pg = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=4 / 3)
    pg.add_init_script(CLOCK)
    pg.goto(page_path.as_uri(), wait_until="networkidle")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(500)
    # where cantons sit on screen
    at = pg.evaluate("""(()=>{const mid={};D.codes.forEach((c,i)=>{let xs=[],ys=[];for(let r=0;r<D.rows;r++)for(let k=0;k<D.cols;k++)if(pickA[r*D.cols+k]===i+1){xs.push(k);ys.push(r)}
      if(xs.length){xs.sort((a,b)=>a-b);ys.sort((a,b)=>a-b);mid[c]=[(xs[xs.length>>1]+.5)*D.pick,(ys[ys.length>>1]+.5)*D.pick]}});
      const r=document.getElementById('map').getBoundingClientRect(),s=Math.min(r.width/D.w,r.height/D.h),ox=(r.width-D.w*s)/2,oy=(r.height-D.h*s)/2+(r.width>980?Math.min(26,r.height*.03):0);
      const o={};for(const k in mid)o[k]=[r.left+ox+mid[k][0]*s,r.top+oy+mid[k][1]*s];return o})()""")
    ge, vd, zh, be = at["GE"], at["VD"], at["ZH"], at["BE"]
    off = (1060, 1300)  # rest point, off the map

    def ease(t):
        return t * t * (3 - 2 * t)

    def path(t):
        """pointer position at time t (s), or None to stay put"""
        legs = [(2.6, 3.6, off, ge), (3.6, 4.9, ge, vd), (4.9, 5.6, vd, off),
                (8.6, 9.6, off, zh), (9.6, 10.9, zh, be), (10.9, 11.6, be, off)]
        for a, b, p0, p1 in legs:
            if a <= t <= b:
                u = ease((t - a) / (b - a))
                return (p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u)
        return None

    pg.mouse.move(*off)
    for i in range(FPS * SECONDS):
        t = i / FPS
        xy = path(t)
        if xy:
            pg.mouse.move(*xy)
        if abs(t - 6.4) < 1e-6:
            pg.evaluate("document.getElementById('m-year').click()")
        if abs(t - 12.2) < 1e-6:
            pg.evaluate("document.getElementById('m-week').click()")
        pg.evaluate(f"window.__tick({DT})")
        pg.screenshot(path=str(frames / f"{i:04d}.png"))
    browser.close()

subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "%04d.png"),
                "-vf", "scale=1440:1800:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-movflags", "+faststart", str(out)], check=True)
print(out, out.stat().st_size)
