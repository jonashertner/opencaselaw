#!/usr/bin/env python3
"""Ban networks that mass-harvest the export renderers, for a limited time.

Why (2026-10-23): a fleet of 50 addresses in one cloud provider's range pulled
~12,900 /api/decisions/<id>/export.docx renders per 15 minutes for six hours.
Every limit in nginx was per address, so the fleet got 50 budgets and none of
them fired. Each render reads a full decision and builds a Word file inside an
MCP worker: the workers grew to ~30 GB, the page cache shrank, the data volume
sat at its IOPS ceiling and the nightly build's QC gate timed out. The range
was blocked by hand only after the fleet had already stopped.

What it does, every run (timer: every 5 minutes):
  - reads the last WINDOW minutes of nginx's tier1.log (and the most recent
    rotated, still uncompressed file, so a window across midnight is whole);
  - counts "harvest" requests: export renders and fetches of /bulk-access.txt
    (the notice a throttled client is sent; reading it in a loop is a tell);
  - bans a network (IPv4 /24, IPv6 /48) with NET_MAX or more in the window
    from at least NET_MIN_IPS addresses (one address alone is already held to
    10 exports a minute by the per-address budget in ocl-bulk-export.conf;
    its excess costs nginx a 429, not a render);
  - bans the member /24s of a wider fleet: an IPv4 /16 with WIDE_MAX or more
    from at least WIDE_MIN_IPS addresses, each member with WIDE_MEMBER_MIN;
  - writes the active bans to a host-local nginx geo file, checks the config
    and reloads nginx; a ban expires TTL hours after its network last crossed
    the line (banned requests are still logged as 429s, so a fleet that keeps
    going keeps its ban);
  - sends one ntfy message per run that adds a ban.

A ban only closes the export renderers (deploy/nginx/mcp-server); search, the
JSON API, /entscheid/ pages and MCP keep working for that network, so a wrong
ban costs little. The geo file, the state file and the allowlist hold client
networks and live on the host only: never commit them.

Calibration (tier1.log 2026-10-20..23): export + notice requests per /24 per
15 min have median 1, 99th percentile 6, 99.9th 24. The heaviest single cloud
addresses reached 700-1,175, all from one address each (and mostly 429s);
the 10-23 fleet ~1,600 per /24 from 6-11 addresses each (12,900 over 8).
Replayed over those four days, these defaults ban the 10-23 fleet at 11:25,
six minutes after it started, and one crawler range that ignores robots.txt.

Settings (environment):
  OCL_AUTOBAN_LOGS        comma-separated live logs (/var/log/nginx/tier1.log)
  OCL_AUTOBAN_GEO         generated nginx geo file (/etc/nginx/snippets/ocl-autoban.geo)
  OCL_AUTOBAN_STATE       ban state (/var/lib/opencaselaw/autoban.json)
  OCL_AUTOBAN_ALLOW       networks never banned, one CIDR per line
                          (/etc/opencaselaw/autoban-allow.txt; may be absent)
  OCL_AUTOBAN_WINDOW_MIN  window in minutes (15)
  OCL_AUTOBAN_NET_MAX     per-network threshold in the window (600)
  OCL_AUTOBAN_NET_MIN_IPS distinct addresses a network needs (3)
  OCL_AUTOBAN_WIDE_MAX    per-/16 threshold in the window (2000)
  OCL_AUTOBAN_WIDE_MIN_IPS     distinct addresses a /16 needs (10)
  OCL_AUTOBAN_WIDE_MEMBER_MIN  requests a member /24 needs (50)
  OCL_AUTOBAN_TTL_H       ban length in hours after the last offence (24)
  OCL_AUTOBAN_MAX_NEW     at most this many new bans per run (64)
  OCL_AUTOBAN_DRY         "1": report what would be banned, change nothing
"""
from __future__ import annotations

import datetime as dt
import glob
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator

LOGS = [p for p in os.environ.get("OCL_AUTOBAN_LOGS", "/var/log/nginx/tier1.log").split(",") if p]
GEO_FILE = Path(os.environ.get("OCL_AUTOBAN_GEO", "/etc/nginx/snippets/ocl-autoban.geo"))
STATE_FILE = Path(os.environ.get("OCL_AUTOBAN_STATE", "/var/lib/opencaselaw/autoban.json"))
ALLOW_FILE = Path(os.environ.get("OCL_AUTOBAN_ALLOW", "/etc/opencaselaw/autoban-allow.txt"))
WINDOW_MIN = int(os.environ.get("OCL_AUTOBAN_WINDOW_MIN", "15"))
NET_MAX = int(os.environ.get("OCL_AUTOBAN_NET_MAX", "600"))
NET_MIN_IPS = int(os.environ.get("OCL_AUTOBAN_NET_MIN_IPS", "3"))
WIDE_MAX = int(os.environ.get("OCL_AUTOBAN_WIDE_MAX", "2000"))
WIDE_MIN_IPS = int(os.environ.get("OCL_AUTOBAN_WIDE_MIN_IPS", "10"))
WIDE_MEMBER_MIN = int(os.environ.get("OCL_AUTOBAN_WIDE_MEMBER_MIN", "50"))
TTL_H = float(os.environ.get("OCL_AUTOBAN_TTL_H", "24"))
MAX_NEW = int(os.environ.get("OCL_AUTOBAN_MAX_NEW", "64"))
DRY_RUN = os.environ.get("OCL_AUTOBAN_DRY") == "1"
NTFY_URL = (os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/") + "/"
            + os.environ.get("NTFY_TOPIC", "opencaselaw-publish"))

# tier1: $remote_addr $time_iso8601 "$request_method $uri" $status $request_time "$http_user_agent"
LINE = re.compile(r'^(\S+) (\S+) "\S+ (\S*)" (\d{3}) ')
HARVEST_PATH = re.compile(r"^/api/decisions/[^/]+/export|^/bulk-access\.txt$")
TS_LEN = 25  # 2026-10-23T11:19:56+00:00


def log(msg: str) -> None:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} autoban {msg}", flush=True)


def _parse_ts(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _line_ts(line: bytes) -> dt.datetime | None:
    parts = line.split(b" ", 2)
    if len(parts) < 2:
        return None
    return _parse_ts(parts[1].decode("ascii", "replace"))


def _seek_since(fh, size: int, since: dt.datetime) -> int:
    """Byte offset of the first line at or after `since` (the log is in time
    order). Binary search on line starts, so a 230 MB log costs ~30 reads."""
    lo, hi = 0, size
    while hi - lo > 4096:
        mid = (lo + hi) // 2
        fh.seek(mid)
        fh.readline()  # finish the partial line
        pos = fh.tell()
        line = fh.readline()
        ts = _line_ts(line) if line else None
        if ts is None or ts >= since:
            hi = mid
        else:
            lo = pos
    return lo


def recent_lines(paths: Iterable[str], since: dt.datetime) -> Iterator[str]:
    for path in paths:
        try:
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(_seek_since(fh, size, since))
            if fh.tell():
                fh.readline()  # land on a line start
            for raw in fh:
                line = raw.decode("utf-8", "replace")
                m = LINE.match(line)
                if not m:
                    continue
                ts = _parse_ts(m.group(2))
                if ts is not None and ts >= since:
                    yield line


def log_paths(live: list[str], since: dt.datetime) -> list[str]:
    """The live logs plus each one's newest rotated, uncompressed sibling
    when it was written to inside the window (the window spans a rotation)."""
    out: list[str] = []
    for path in live:
        rotated = [p for p in glob.glob(path + "-*") + glob.glob(path + ".1")
                   if not p.endswith(".gz")]
        rotated.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        if rotated and dt.datetime.fromtimestamp(
                os.path.getmtime(rotated[0]), dt.timezone.utc) >= since:
            out.append(rotated[0])
        out.append(path)
    return out


def bannable(addr) -> bool:
    """Only public addresses: never loopback, the LAN, or the host itself."""
    return addr.is_global


def network_of(ip: str) -> ipaddress._BaseNetwork | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if not bannable(addr):
        return None
    prefix = 24 if addr.version == 4 else 48
    return ipaddress.ip_network(f"{addr}/{prefix}", strict=False)


def count_harvest(lines: Iterable[str]):
    """Per network: request count and distinct addresses."""
    hits: Counter = Counter()
    ips: dict = defaultdict(set)
    for line in lines:
        m = LINE.match(line)
        if not m or not HARVEST_PATH.match(m.group(3)):
            continue
        net = network_of(m.group(1))
        if net is None:
            continue
        hits[net] += 1
        ips[net].add(m.group(1))
    return hits, ips


def offenders(hits: Counter, ips: dict) -> dict:
    """{network: reason} for this window."""
    found: dict = {}
    for net, n in hits.items():
        if n >= NET_MAX and len(ips[net]) >= NET_MIN_IPS:
            found[net] = f"{n} export requests in {WINDOW_MIN} min from {len(ips[net])} address(es)"
    wide_hits: Counter = Counter()
    wide_ips: dict = defaultdict(set)
    members: dict = defaultdict(list)
    for net, n in hits.items():
        if net.version != 4:
            continue
        wide = net.supernet(new_prefix=16)
        wide_hits[wide] += n
        wide_ips[wide] |= ips[net]
        members[wide].append(net)
    for wide, n in wide_hits.items():
        if n < WIDE_MAX or len(wide_ips[wide]) < WIDE_MIN_IPS:
            continue
        for net in members[wide]:
            if net not in found and hits[net] >= WIDE_MEMBER_MIN:
                found[net] = (f"fleet in {wide}: {n} export requests in {WINDOW_MIN} min "
                              f"from {len(wide_ips[wide])} addresses, {hits[net]} from this /24")
    return found


def load_allow(path: Path) -> list:
    nets = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return nets
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            nets.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            log(f"allowlist: ignoring unparsable line {line!r}")
    return nets


def allowed(net, allow: list) -> bool:
    return any(net.version == a.version and (net.subnet_of(a) or a.subnet_of(net))
               for a in allow)


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("bans", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_bans(bans: dict, found: dict, allow: list, now: dt.datetime) -> tuple[dict, list, list]:
    """Renew and add bans, drop expired ones. Returns (bans, new, expired)."""
    until = (now + dt.timedelta(hours=TTL_H)).isoformat(timespec="seconds")
    bans = dict(bans)
    expired = [cidr for cidr, b in bans.items()
               if (_parse_ts(b.get("until", "")) or now) <= now]
    for cidr in expired:
        del bans[cidr]
    new: list = []
    for net, reason in sorted(found.items(), key=lambda kv: str(kv[0])):
        cidr = str(net)
        if allowed(net, allow):
            log(f"skip {cidr} (allowlisted): {reason}")
            continue
        if cidr in bans:
            bans[cidr].update(until=until, reason=reason)
            continue
        if len(new) >= MAX_NEW:
            log(f"skip {cidr}: already {MAX_NEW} new bans this run")
            continue
        bans[cidr] = {"since": now.isoformat(timespec="seconds"), "until": until, "reason": reason}
        new.append(cidr)
    return bans, new, expired


def render_geo(bans: dict) -> str:
    lines = ["# Generated by scripts/autoban_harvesters.py: host-local, never commit.",
             "# Networks banned from the export renderers; see runbooks/harvest_autoban.md."]
    for cidr in sorted(bans, key=lambda c: (ipaddress.ip_network(c).version, ipaddress.ip_network(c))):
        lines.append(f"{cidr} 1;  # until {bans[cidr]['until']}")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def apply_geo(text: str) -> bool:
    """Write the geo file, test nginx, reload. On a failed test the previous
    file is put back and nothing is reloaded."""
    try:
        old = GEO_FILE.read_text(encoding="utf-8")
    except OSError:
        old = None
    if old == text:
        return True
    _write(GEO_FILE, text)
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if test.returncode != 0:
        log("nginx -t failed, restoring the previous ban list: " + test.stderr.strip()[-400:])
        if old is None:
            GEO_FILE.unlink(missing_ok=True)
        else:
            _write(GEO_FILE, old)
        return False
    reload = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True)
    if reload.returncode != 0:
        log("nginx reload failed: " + reload.stderr.strip()[-400:])
        return False
    return True


def notify(new: list, bans: dict) -> None:
    body = "\n".join(f"{c}: {bans[c]['reason']}" for c in new)
    try:
        req = urllib.request.Request(
            NTFY_URL, data=body.encode(),
            headers={"Title": f"OpenCaseLaw: {len(new)} network(s) banned from exports for {TTL_H:g} h"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001 — the ban stands without the alert
        log(f"ntfy failed (non-fatal): {e}")


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(minutes=WINDOW_MIN)
    hits, ips = count_harvest(recent_lines(log_paths(LOGS, since), since))
    found = offenders(hits, ips)
    top = hits.most_common(1)
    bans, new, expired = update_bans(load_state(STATE_FILE), found, load_allow(ALLOW_FILE), now)
    log(f"{sum(hits.values())} export requests in {WINDOW_MIN} min from {len(hits)} networks"
        + (f", top {top[0][0]} ({top[0][1]})" if top else "")
        + f"; {len(bans)} active ban(s), {len(new)} new, {len(expired)} expired"
        + (" [dry run]" if DRY_RUN else ""))
    for cidr in new:
        log(f"{'would ban' if DRY_RUN else 'ban'} {cidr}: {bans[cidr]['reason']}")
    for cidr in expired:
        log(f"{'would lift' if DRY_RUN else 'lift'} {cidr}")
    if DRY_RUN:
        return 0
    if not apply_geo(render_geo(bans)):
        return 1
    _write(STATE_FILE, json.dumps({"bans": bans}, indent=1, sort_keys=True) + "\n")
    if new:
        notify(new, bans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
