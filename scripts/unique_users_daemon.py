"""Privacy-preserving unique-consumer counter (ocl-uniq).

Answers "how many individual direct consumers does the platform have per
day / per month" without identifying anyone and without ever writing an IP
address to disk:

  nginx --syslog/UDP(127.0.0.1:5141)--> this daemon --> aggregate JSONL

  - nginx sends "<ip>|<user-agent>" per API/MCP request (loopback only).
  - The daemon computes sha256(window_salt + ip) IN MEMORY and feeds the
    64-bit prefix into a HyperLogLog sketch per (window, client class).
  - Only sketches (no membership queries possible) and aggregate counts
    are persisted. Window salts live in the systemd StateDirectory with
    mode 0600 and are deleted when their window closes, so even the
    sketch inputs become uncomputable after the fact.
  - Windows: UTC day and UTC calendar month. Rotating per-window salts
    make cross-window linkage impossible by construction — deliberately:
    we buy "no tracking" at the price of "no returning-user analysis".

Interpretation note: platform-NAT cohorts (claude.ai / ChatGPT connector
egress) appear here as a handful of infrastructure IPs — their end users
are structurally invisible to us and stay estimate-only. The honest
"direct individual consumers" figure is the browser+script+claude-code
share; the total is reported alongside per-class splits so the two are
never conflated.

Behavioural classification (2026-08-03). The user-agent alone cannot
separate a reader from a scraper wearing a Chrome string — measured that
day: 98k addresses claimed "browser" while declared bots outnumbered
Chrome requests 3:2 in the same hour. So each address is additionally
judged by what it *did* — pace, rhythm, volume and endpoint mix; see
verdict() for the rules and for why the usual asset-loading test does
not apply to this site.

Verdicts are 'mensch', 'maschine' or 'unklar' — the third is used rather
than guessing, e.g. for an address with too few requests to judge. The
per-address feature records live in RAM only, are capped, and are
destroyed when the window closes; only the resulting sketches persist.

Daily records append to output/research_logs/unique_users.jsonl.
State (sketch registers + current window ids) checkpoints every
CHECKPOINT_S so a restart resumes mid-window with only HLL-level error.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import socket
import threading
import time
from pathlib import Path

LISTEN_ADDR = ("127.0.0.1", int(os.environ.get("OCL_UNIQ_PORT", "5141")))
STATE_DIR = Path(os.environ.get("STATE_DIRECTORY",
                                os.environ.get("OCL_UNIQ_STATE", "/var/lib/ocl-uniq")))
OUT_PATH = Path(os.environ.get(
    "OCL_UNIQ_OUT", "/opt/caselaw/repo/output/research_logs/unique_users.jsonl"))
CHECKPOINT_S = int(os.environ.get("OCL_UNIQ_CHECKPOINT_S", "600"))

HLL_P = 14                              # 2^14 registers -> ~0.8% std error
HLL_M = 1 << HLL_P

CLASSES = ("browser", "script", "claude-code", "anthropic-egress",
           "openai-egress", "crawler", "other")
VERDICTS = ("mensch", "maschine", "unklar")
# per-address behaviour records held in RAM; beyond the cap new addresses
# are still counted in the sketches but not behaviourally judged
FEATURE_CAP = int(os.environ.get("OCL_UNIQ_FEATURE_CAP", "500000"))
# drip rule (see verdict): active over >= 6 h at <= 3 requests per active
# UTC hour; below 8 requests the verdict is 'unklar' rather than 'maschine'
DRIP_SPAN_S = 6 * 3600
DRIP_PER_HOUR = 3
DRIP_MIN_N = 8


def verdict(f: dict) -> str:
    """Reader or robot, from behaviour alone. Deliberately conservative:
    anything the evidence does not settle stays 'unklar'.

    Note on assets (measured 2026-08-03): decision pages here are
    self-contained HTML with inline styling and no external stylesheets,
    scripts or images, so a real browser fetches nothing besides the
    document. The classic "did it load the assets" test is therefore
    inapplicable on this site, and using it would brand every genuine
    reader a machine. The signals that do work:

      pace    a 90 KB judgment cannot be read in a second; sustained
              sub-2s spacing is mechanical
      rhythm  metronomic gaps (CV < 0.15) never come from a person
      volume  a practitioner reads tens of decisions a day, not hundreds
      mix     traffic that never touches a document page and only calls
              the API at volume is programmatic by definition
      drip    a few requests every hour, spread over most of the day: a
              crawler pacing itself under the rules above. Readers come in
              sessions: several pages close together, then nothing.

    Why drip exists (measured 2026-11-21..23): from 2026-11-03 "mensch" rose
    from 200-430 a day to 3,000-10,000. 88-95% of those addresses were
    active for 6 h or more, a median ~13 min between requests, with a flat
    24-hour volume. 1,497 of them were the same Tencent Cloud addresses on
    all three days. Replayed on those days, the drip rule removed every
    Tencent address and left 190-240 a day. On a weekday 82% of those were
    first seen during office hours.

    The user-agent is consulted for one thing only: a client that declares
    itself a crawler is believed. Without that rule a backtest over one
    hour of real traffic put 260 of 266 "mensch" verdicts on Googlebot and
    friends — a well-behaved crawler reading judgments at a civilised pace
    is behaviourally indistinguishable from a reader.
    """
    if f.get("ua_cls") == "crawler":
        return "maschine"                # self-declared: take it at its word
    n = f["n"]
    mean_gap = f["dt_sum"] / f["gaps"] if f["gaps"] else None
    cv = None
    if f["gaps"] >= 5 and mean_gap and mean_gap > 0:
        var = max(0.0, f["dt2_sum"] / f["gaps"] - mean_gap * mean_gap)
        cv = math.sqrt(var) / mean_gap

    if n <= 3:
        return "unklar"                  # too little evidence to judge
    if cv is not None and cv < 0.15:
        return "maschine"                # metronome
    if f["gaps"] >= 4 and mean_gap is not None and mean_gap < 2.0:
        return "maschine"                # faster than anyone can read
    if n > 120:
        return "maschine"                # volume beyond human reading
    if f["docs"] == 0 and f["apis"] + f["mcps"] >= 10:
        return "maschine"                # API-only consumer
    last = f.get("last") or 0.0
    span = last - (f.get("first") or last)
    hours = bin(f.get("hrs", 0)).count("1")
    if span >= DRIP_SPAN_S and hours and n / hours <= DRIP_PER_HOUR:
        # spread thin across the day; with little evidence, don't guess
        return "maschine" if n >= DRIP_MIN_N else "unklar"
    if f["assets"] > 0 or f["docs"] > 0:
        return "mensch"
    return "unklar"


def classify_ua(ua: str) -> str:
    """Coarse client class from the User-Agent. Data-driven refinements
    welcome; unknowns land in 'other' rather than polluting a real class.

    Crawler detection runs FIRST: GPTBot ("openai.com/gptbot") and
    ClaudeBot ("@anthropic.com") carry their vendor's name and would
    otherwise inflate the egress classes; Googlebot presents as Mozilla
    and was 97% of the first 20 minutes of 'browser' before this class
    existed (observed 2026-08-02)."""
    u = (ua or "").lower()
    for tok in ("bot", "crawler", "spider", "slurp", "bytespider",
                "facebookexternalhit", "meta-externalagent", "headless"):
        if tok in u:
            return "crawler"
    if "claude-code" in u or "claude code" in u:
        return "claude-code"
    if "claude-user" in u or "anthropic" in u or "claude.ai" in u:
        return "anthropic-egress"
    if "chatgpt" in u or "openai" in u or u.startswith("oai"):
        return "openai-egress"
    if u.startswith("mozilla"):
        return "browser"
    for tok in ("python", "curl", "httpx", "aiohttp", "requests", "node",
                "go-http", "okhttp", "java", "wget", "libwww", "axios"):
        if tok in u:
            return "script"
    return "other"


def parse_line(payload: str) -> tuple[str, str, str] | None:
    """Extract (ip, ua, request-class) from a syslog datagram. nginx
    prepends '<PRI>Mmm dd hh:mm:ss host tag: ' — split on the tag marker.
    The third field is optional so the daemon keeps working against the
    older two-field log_format during a rollout."""
    msg = payload.split("ocluniq: ", 1)[-1].strip()
    if "|" not in msg:
        return None
    parts = msg.split("|")
    ip = parts[0].strip()
    if not ip or " " in ip:
        return None
    ua = parts[1].strip() if len(parts) > 1 else ""
    cls = parts[2].strip() if len(parts) > 2 else "other"
    return ip, ua, cls


class HLL:
    """Minimal HyperLogLog (p=14) over a 64-bit hash prefix."""

    __slots__ = ("reg",)

    def __init__(self, reg: bytearray | None = None):
        self.reg = reg if reg is not None else bytearray(HLL_M)

    def add_hash(self, h64: int) -> None:
        bucket = h64 >> (64 - HLL_P)
        w = h64 & ((1 << (64 - HLL_P)) - 1)
        # rho: leading zeros within the (64-p)-bit remainder, +1
        rho = (64 - HLL_P) - w.bit_length() + 1
        if rho > self.reg[bucket]:
            self.reg[bucket] = rho

    def estimate(self) -> int:
        alpha = 0.7213 / (1 + 1.079 / HLL_M)
        s = 0.0
        zeros = 0
        for r in self.reg:
            s += 2.0 ** -r
            if r == 0:
                zeros += 1
        e = alpha * HLL_M * HLL_M / s
        if e <= 2.5 * HLL_M and zeros:
            e = HLL_M * math.log(HLL_M / zeros)   # linear counting, small range
        return int(round(e))


def salted_h64(salt: bytes, ip: str) -> int:
    d = hashlib.sha256(salt + ip.encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(d[:8], "big")


class Windows:
    """Day + month sketch windows with rotating salts. Clock injectable."""

    def __init__(self, now=None):
        self.now = now or (lambda: time.time())
        self.flags: list[str] = []
        self._roll(force=True)

    def _ids(self):
        t = time.gmtime(self.now())
        return (time.strftime("%Y-%m-%d", t), time.strftime("%Y-%m", t))

    def _salt(self, name: str) -> bytes:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        p = STATE_DIR / f"salt-{name}"
        if p.exists():
            return p.read_bytes()
        s = secrets.token_bytes(32)
        p.touch(mode=0o600, exist_ok=True)
        p.write_bytes(s)
        p.chmod(0o600)
        return s

    def _roll(self, force: bool = False) -> dict | None:
        """Rotate windows if the UTC day/month changed. Returns the
        finalized daily record (to append) or None."""
        day, month = self._ids()
        record = None
        if force:
            self.day, self.month = day, month
            self.day_salt = self._salt(f"day-{day}")
            self.month_salt = self._salt(f"month-{month}")
            self.day_hll = {c: HLL() for c in (*CLASSES, *VERDICTS, "total")}
            self.month_hll = {c: HLL() for c in (*CLASSES, *VERDICTS, "total")}
            self.feat: dict[int, dict] = {}
            return None
        if day != self.day:
            self._seal_verdicts()
            record = self.snapshot(final=True)
            (STATE_DIR / f"salt-day-{self.day}").unlink(missing_ok=True)
            self.day = day
            self.day_salt = self._salt(f"day-{day}")
            self.day_hll = {c: HLL() for c in (*CLASSES, *VERDICTS, "total")}
            self.feat = {}                      # behaviour records die with the window
            self.flags = []
        if month != self.month:
            (STATE_DIR / f"salt-month-{self.month}").unlink(missing_ok=True)
            self.month = month
            self.month_salt = self._salt(f"month-{month}")
            self.month_hll = {c: HLL() for c in (*CLASSES, *VERDICTS, "total")}
        return record

    def _seal_verdicts(self) -> None:
        """Judge every tracked address once, at window close, and fold the
        verdicts into the sketches. Done here rather than per request so a
        visitor is judged on the whole day, not on their first click."""
        for h64, f in self.feat.items():
            v = verdict(f)
            self.day_hll[v].add_hash(h64)
            if f.get("hm") is not None:
                self.month_hll[v].add_hash(f["hm"])

    def observe(self, ip: str, ua: str, req_cls: str = "other") -> dict | None:
        record = self._roll()
        cls = classify_ua(ua)
        hd = salted_h64(self.day_salt, ip)
        hm = salted_h64(self.month_salt, ip)
        for key in (cls, "total"):
            self.day_hll[key].add_hash(hd)
            self.month_hll[key].add_hash(hm)
        # behaviour record, RAM only, capped
        f = self.feat.get(hd)
        if f is None:
            if len(self.feat) >= FEATURE_CAP:
                return record
            f = self.feat[hd] = {"n": 0, "assets": 0, "docs": 0, "apis": 0,
                                 "mcps": 0, "gaps": 0, "dt_sum": 0.0,
                                 "dt2_sum": 0.0, "prev": None, "hm": hm,
                                 "ua_cls": cls, "first": None, "last": 0.0,
                                 "hrs": 0}
        t = self.now()
        f["n"] += 1
        if f["first"] is None:
            f["first"] = t
        f["last"] = t
        f["hrs"] |= 1 << (int(t // 3600) % 24)   # active UTC hours, bitmask
        if req_cls == "asset":
            f["assets"] += 1
        elif req_cls == "doc":
            f["docs"] += 1
        elif req_cls == "api":
            f["apis"] += 1
        elif req_cls == "mcp":
            f["mcps"] += 1
        if f["prev"] is not None:
            dt = t - f["prev"]
            if 0 < dt < 1800:               # ignore idle gaps: a new visit
                f["gaps"] += 1
                f["dt_sum"] += dt
                f["dt2_sum"] += dt * dt
        f["prev"] = t
        return record

    def snapshot(self, final: bool = False) -> dict:
        live = {}
        if not final:                        # interim view: judge on the fly
            tmp = {v: HLL() for v in VERDICTS}
            for h64, f in self.feat.items():
                tmp[verdict(f)].add_hash(h64)
            live = {v: h.estimate() for v, h in tmp.items()}
        return {
            "date": self.day,
            "final": final,
            "uniques": {k: h.estimate() for k, h in self.day_hll.items()},
            "verhalten": live or {v: self.day_hll[v].estimate() for v in VERDICTS},
            "beobachtete_adressen": len(self.feat),
            "month": self.month,
            "month_to_date": {k: h.estimate() for k, h in self.month_hll.items()},
            "flags": list(self.flags),
        }

    # ── checkpoint / resume ────────────────────────────────────────────
    def save_state(self) -> None:
        # NB: self.feat (per-address behaviour) is deliberately NOT saved —
        # it exists only for the lifetime of the window, in memory.
        st = {"day": self.day, "month": self.month,
              "day_reg": {k: h.reg.hex() for k, h in self.day_hll.items()},
              "month_reg": {k: h.reg.hex() for k, h in self.month_hll.items()}}
        tmp = STATE_DIR / "state.json.tmp"
        tmp.write_text(json.dumps(st))
        os.replace(tmp, STATE_DIR / "state.json")

    def load_state(self) -> bool:
        p = STATE_DIR / "state.json"
        if not p.exists():
            return False
        try:
            st = json.loads(p.read_text())
        except Exception:
            return False
        if st.get("day") != self.day or st.get("month") != self.month:
            return False                # stale checkpoint: start fresh
        for k, hexreg in st["day_reg"].items():
            self.day_hll[k] = HLL(bytearray.fromhex(hexreg))
        for k, hexreg in st["month_reg"].items():
            self.month_hll[k] = HLL(bytearray.fromhex(hexreg))
        self.flags.append("resumed-from-checkpoint")
        return True


def append_record(rec: dict) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def main() -> int:
    win = Windows()
    win.load_state()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.bind(LISTEN_ADDR)
    print(f"ocl-uniq listening on {LISTEN_ADDR[0]}:{LISTEN_ADDR[1]}, "
          f"state={STATE_DIR}, out={OUT_PATH}")

    lock = threading.Lock()

    def checkpoint_loop():
        while True:
            time.sleep(CHECKPOINT_S)
            with lock:
                rec = win._roll()          # also finalizes across quiet midnights
                if rec:
                    append_record(rec)
                win.save_state()

    threading.Thread(target=checkpoint_loop, daemon=True).start()

    while True:
        data, _ = sock.recvfrom(8192)
        parsed = parse_line(data.decode("utf-8", "replace"))
        if not parsed:
            continue
        with lock:
            rec = win.observe(*parsed)
        if rec:
            append_record(rec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
