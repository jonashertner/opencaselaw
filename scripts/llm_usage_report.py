#!/usr/bin/env python3
"""
Daily / per-feature roll-up of Anthropic API spend logged by the MCP
server.

Reads the JSONL receipts at $OCL_LLM_USAGE_LOG (default
`logs/llm_usage.jsonl`) and prints a summary by day, model, and feature.

Usage:
    python3 scripts/llm_usage_report.py                    # all-time summary
    python3 scripts/llm_usage_report.py --days 7           # last 7 days
    python3 scripts/llm_usage_report.py --since 2026-04-01
    python3 scripts/llm_usage_report.py --by feature       # group by feature
    python3 scripts/llm_usage_report.py --json             # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LOG_PATH = Path(os.environ.get(
    "OCL_LLM_USAGE_LOG",
    str(Path(__file__).resolve().parent.parent / "logs" / "llm_usage.jsonl"),
))


def _load_records(path: Path, since: date | None) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = r.get("ts") or ""
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if since and dt.date() < since:
                continue
            r["_dt"] = dt
            records.append(r)
    return records


def _aggregate(records: list[dict], group_by: str) -> dict:
    """group_by ∈ {'day', 'feature', 'model', 'day_model', 'day_feature'}"""
    out: dict = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0,
                                      "cache_r": 0, "cache_w": 0,
                                      "cost_usd": 0.0, "errors": 0})

    def _key(r: dict) -> str:
        d = r["_dt"].date().isoformat()
        if group_by == "day":         return d
        if group_by == "feature":     return r.get("feature", "?")
        if group_by == "model":       return r.get("model", "?")
        if group_by == "day_model":   return f"{d}  {r.get('model', '?')}"
        if group_by == "day_feature": return f"{d}  {r.get('feature', '?')}"
        return d

    for r in records:
        k = _key(r)
        out[k]["calls"] += 1
        out[k]["in"] += int(r.get("in") or 0)
        out[k]["out"] += int(r.get("out") or 0)
        out[k]["cache_r"] += int(r.get("cache_r") or 0)
        out[k]["cache_w"] += int(r.get("cache_w") or 0)
        out[k]["cost_usd"] += float(r.get("cost_usd") or 0.0)
        if not r.get("ok", True):
            out[k]["errors"] += 1
    return out


def _print_table(rows: dict, key_label: str) -> None:
    keys = sorted(rows.keys())
    print(f"{key_label:38s} {'calls':>7} {'in_tok':>9} {'out_tok':>8} "
          f"{'errors':>7} {'cost_usd':>10}")
    print("─" * 84)
    total_calls = total_in = total_out = total_err = 0
    total_cost = 0.0
    for k in keys:
        v = rows[k]
        print(f"{k[:38]:38s} {v['calls']:>7} {v['in']:>9} {v['out']:>8} "
              f"{v['errors']:>7} ${v['cost_usd']:>9.4f}")
        total_calls += v["calls"]; total_in += v["in"]
        total_out += v["out"]; total_err += v["errors"]
        total_cost += v["cost_usd"]
    print("─" * 84)
    print(f"{'TOTAL':38s} {total_calls:>7} {total_in:>9} {total_out:>8} "
          f"{total_err:>7} ${total_cost:>9.4f}")


def _render_html(records: list[dict]) -> str:
    """Build a self-contained Chart.js HTML dashboard from `records`.

    Two charts:
      1. Daily cost ($) stacked by feature — bar chart.
      2. Cumulative cost ($) over time — line chart.

    Data is inlined as a JSON literal; the page has no external network
    calls except the Chart.js CDN (~50 KB)."""
    by_day_feature: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    by_day_calls: dict[str, int] = defaultdict(int)
    by_day_tokens_in: dict[str, int] = defaultdict(int)
    by_day_tokens_out: dict[str, int] = defaultdict(int)
    features: set[str] = set()
    for r in records:
        d = r["_dt"].date().isoformat()
        f = r.get("feature", "?")
        by_day_feature[d][f] += float(r.get("cost_usd") or 0.0)
        by_day_calls[d] += 1
        by_day_tokens_in[d] += int(r.get("in") or 0)
        by_day_tokens_out[d] += int(r.get("out") or 0)
        features.add(f)
    days = sorted(by_day_feature.keys())
    features_ordered = sorted(features)

    # Per-feature daily series
    series = []
    palette = ["#0ea5e9", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444",
                "#14b8a6", "#f97316", "#6366f1"]
    for i, feat in enumerate(features_ordered):
        series.append({
            "label": feat,
            "data":  [round(by_day_feature[d].get(feat, 0.0), 6)
                      for d in days],
            "backgroundColor": palette[i % len(palette)],
            "stack": "cost",
        })

    # Cumulative spend
    cumulative = []
    running = 0.0
    for d in days:
        running += sum(by_day_feature[d].values())
        cumulative.append(round(running, 6))

    total_cost = round(sum(cumulative[-1:]) if cumulative else 0.0, 4)
    total_calls = sum(by_day_calls.values())
    total_in = sum(by_day_tokens_in.values())
    total_out = sum(by_day_tokens_out.values())

    payload = {
        "days": days,
        "features": features_ordered,
        "series": series,
        "cumulative": cumulative,
        "totals": {
            "cost_usd":  total_cost,
            "calls":     total_calls,
            "tokens_in": total_in,
            "tokens_out": total_out,
            "first_day": days[0] if days else None,
            "last_day":  days[-1] if days else None,
        },
    }

    return _HTML_TEMPLATE.replace(
        "__PAYLOAD__", json.dumps(payload, ensure_ascii=False)
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <title>OpenCaseLaw — LLM spend</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preconnect" href="https://cdn.jsdelivr.net">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg:#0b0f14; --surf:#121821; --tx:#e6edf3; --tx2:#9ca6b1;
      --brd:#1f2733; --ac:#7dd3fc;
    }
    @media (prefers-color-scheme: light) {
      :root { --bg:#f7f8fa; --surf:#fff; --tx:#1a1f26; --tx2:#5b6573;
              --brd:#e4e7eb; --ac:#0369a1; }
    }
    html,body { margin:0; padding:0; background:var(--bg); color:var(--tx);
                font-family:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,sans-serif;
                font-weight:300; letter-spacing:-0.005em; }
    .wrap { max-width:1100px; margin:0 auto; padding:32px 20px; }
    h1 { font-weight:200; font-size:clamp(28px,4vw,40px); margin:0 0 4px;
         letter-spacing:-0.02em; }
    .sub { color:var(--tx2); font-size:14px; margin-bottom:32px; }
    .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
             gap:12px; margin-bottom:32px; }
    .stat { background:var(--surf); border:1px solid var(--brd); border-radius:8px;
            padding:18px 20px; }
    .stat .k { color:var(--tx2); font-size:11px; text-transform:uppercase;
               letter-spacing:0.05em; margin-bottom:6px; }
    .stat .v { font-weight:300; font-size:28px; letter-spacing:-0.02em; }
    .card { background:var(--surf); border:1px solid var(--brd); border-radius:8px;
            padding:20px; margin-bottom:20px; }
    .card h2 { font-weight:300; font-size:18px; margin:0 0 16px;
               letter-spacing:-0.01em; }
    .chart-wrap { position:relative; height:340px; }
    footer { color:var(--tx2); font-size:12px; margin-top:24px;
             text-align:center; }
    code { background:var(--bg); padding:2px 6px; border-radius:4px;
           font-size:12px; color:var(--ac); }
  </style>
</head><body>
<div class="wrap">
  <h1>LLM spend</h1>
  <p class="sub">OpenCaseLaw MCP server — Anthropic API receipts.
     Self-contained snapshot generated by
     <code>scripts/llm_usage_report.py --html</code>.</p>

  <div class="stats" id="headline"></div>

  <div class="card">
    <h2>Daily cost (USD), stacked by feature</h2>
    <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
  </div>

  <div class="card">
    <h2>Cumulative cost (USD)</h2>
    <div class="chart-wrap"><canvas id="cumChart"></canvas></div>
  </div>

  <footer>
    Generated <span id="when"></span>. Data source:
    <code>logs/llm_usage.jsonl</code>. Pricing: claude-sonnet-4-6 $3/$15 per M,
    claude-haiku-4-5 $1/$5 per M, claude-haiku-5-5 $0.10/$0.50 per M.
  </footer>
</div>

<script>
const PAYLOAD = __PAYLOAD__;

document.getElementById('when').textContent = new Date().toISOString().slice(0,16).replace('T',' ') + ' UTC';

(function renderHeadline() {
  const t = PAYLOAD.totals;
  const stats = [
    ['total cost',     '$' + Number(t.cost_usd).toFixed(4)],
    ['calls',          Number(t.calls).toLocaleString('en-US')],
    ['input tokens',   Number(t.tokens_in).toLocaleString('en-US')],
    ['output tokens',  Number(t.tokens_out).toLocaleString('en-US')],
    ['range',          (t.first_day || '?') + ' \u2192 ' + (t.last_day || '?')],
  ];
  const root = document.getElementById('headline');
  for (const [k, v] of stats) {
    const card = document.createElement('div');
    card.className = 'stat';
    const kEl = document.createElement('div');
    kEl.className = 'k';
    kEl.textContent = k;
    const vEl = document.createElement('div');
    vEl.className = 'v';
    vEl.textContent = v;
    card.appendChild(kEl);
    card.appendChild(vEl);
    root.appendChild(card);
  }
})();

const tickColor = getComputedStyle(document.documentElement).getPropertyValue('--tx2').trim();
const gridColor = getComputedStyle(document.documentElement).getPropertyValue('--brd').trim();
const baseOpts = {
  responsive: true, maintainAspectRatio: false,
  plugins: {
    legend: { labels: { color: tickColor } },
    tooltip: {
      callbacks: {
        label: (ctx) => {
          const v = ctx.parsed.y ?? ctx.parsed;
          return ctx.dataset.label + ': $' + v.toFixed(4);
        }
      }
    }
  },
  scales: {
    x: { stacked: true, ticks: { color: tickColor }, grid: { color: gridColor } },
    y: { stacked: true, ticks: { color: tickColor,
                                   callback: (v) => '$' + v.toFixed(3) },
         grid: { color: gridColor } },
  },
};

new Chart(document.getElementById('dailyChart'), {
  type: 'bar',
  data: { labels: PAYLOAD.days, datasets: PAYLOAD.series },
  options: baseOpts,
});

new Chart(document.getElementById('cumChart'), {
  type: 'line',
  data: {
    labels: PAYLOAD.days,
    datasets: [{
      label: 'Cumulative spend',
      data: PAYLOAD.cumulative,
      borderColor: getComputedStyle(document.documentElement).getPropertyValue('--ac').trim(),
      backgroundColor: 'rgba(125,211,252,0.10)',
      fill: true, tension: 0.2, pointRadius: 3,
    }],
  },
  options: {
    ...baseOpts,
    scales: {
      x: { ticks: { color: tickColor }, grid: { color: gridColor } },
      y: { ticks: { color: tickColor,
                     callback: (v) => '$' + v.toFixed(3) },
           grid: { color: gridColor },
           beginAtZero: true },
    },
  },
});
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="LLM usage / cost roll-up")
    ap.add_argument("--log", default=str(DEFAULT_LOG_PATH),
                    help=f"Path to llm_usage.jsonl (default: {DEFAULT_LOG_PATH})")
    ap.add_argument("--days", type=int, default=None,
                    help="Limit to last N days")
    ap.add_argument("--since", default=None,
                    help="Limit to records on/after YYYY-MM-DD")
    ap.add_argument("--by", choices=("day", "feature", "model",
                                      "day_model", "day_feature"),
                    default="day",
                    help="Group by (default: day)")
    ap.add_argument("--json", action="store_true",
                    help="Output JSON instead of a table")
    ap.add_argument("--html", metavar="PATH", default=None,
                    help="Write a self-contained Chart.js HTML dashboard "
                         "(daily stacked-bar by feature + cumulative-spend "
                         "line). All data inlined; open in any browser.")
    args = ap.parse_args()

    log_path = Path(args.log)
    since: date | None = None
    if args.since:
        since = date.fromisoformat(args.since)
    elif args.days:
        since = (datetime.now(timezone.utc).date()
                 - timedelta(days=args.days - 1))

    records = _load_records(log_path, since)
    if not records:
        print(f"No records in {log_path}"
              + (f" since {since}" if since else ""), file=sys.stderr)
        sys.exit(0 if log_path.exists() else 1)

    agg = _aggregate(records, args.by)

    if args.html:
        out_path = Path(args.html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_render_html(records), encoding="utf-8")
        print(f"Wrote {len(records)} records to {out_path}", file=sys.stderr)
        return

    if args.json:
        print(json.dumps({
            "log": str(log_path),
            "since": since.isoformat() if since else None,
            "n_records": len(records),
            "group_by": args.by,
            "rows": agg,
        }, indent=2, ensure_ascii=False))
        return

    label = {"day": "date", "feature": "feature", "model": "model",
             "day_model": "date · model", "day_feature": "date · feature"}[args.by]
    print(f"\nLLM usage from {log_path} "
          f"({len(records)} records"
          + (f", since {since}" if since else "") + ")\n")
    _print_table(agg, label)


if __name__ == "__main__":
    main()
