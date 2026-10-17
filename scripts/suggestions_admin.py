#!/usr/bin/env python3
"""Read and answer the suggestions sent from opencaselaw.ch/vorschlag/.

Run on the VPS with the workers' environment, so it opens the live file:

    SWISS_CASELAW_DIR=/opt/caselaw/repo/output python3 scripts/suggestions_admin.py list
    ... list --status received          only the unread ones (default: everything not shipped/declined)
    ... show 7
    ... set 7 read
    ... set 7 planned --note "Kommt mit dem nächsten Scraper-Lauf."
    ... set 7 shipped --note "Seit 24.10. in der Suche."

Statuses: received → read → planned | declined → shipped. The person sees the status
and the note on their status link. When they left an e-mail address, `set` prints a
ready mailto: line; the page promises one personal message per status change, and
nothing sends it automatically.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import suggestions as sg  # noqa: E402

_STATUS_DE = {"received": "eingegangen", "read": "gelesen", "planned": "geplant",
              "declined": "abgelehnt", "shipped": "umgesetzt"}


def _list(args) -> int:
    if not sg.db_path().exists():
        print(f"no suggestions yet ({sg.db_path()})")
        return 0
    conn = sg.connect()
    try:
        if args.status:
            rows = conn.execute("SELECT * FROM suggestions WHERE status=? ORDER BY id DESC LIMIT ?",
                                (args.status, args.limit)).fetchall()
        elif args.all:
            rows = conn.execute("SELECT * FROM suggestions ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM suggestions WHERE status NOT IN ('shipped','declined') "
                                "ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    finally:
        conn.close()
    for r in rows:
        first = " ".join(r["text"].split())
        print(f"{sg.number(r['id'])}  {r['created_at'][:16].replace('T', ' ')}  {r['status']:<9} {r['kind']:<8} "
              f"{r['lang']}  {'@' if r['email'] else ' '}  {first[:90]}")
    print(f"-- {len(rows)} shown · {sg.db_path()}")
    return 0


def _show(args) -> int:
    conn = sg.connect()
    try:
        r = conn.execute("SELECT * FROM suggestions WHERE id=?", (args.id,)).fetchone()
    finally:
        conn.close()
    if r is None:
        print(f"no suggestion {args.id}", file=sys.stderr)
        return 1
    print(f"Vorschlag {sg.number(r['id'])}  ({r['status']}, {r['kind']}, {r['lang']})")
    print(f"eingegangen {r['created_at']} · geändert {r['updated_at']}")
    if r["context_type"]:
        print(f"Bezug: {r['context_type']} {r['context_value'] or ''}")
    if r["page"]:
        print(f"von: {r['page']}")
    if r["redactions"]:
        print(f"auf dem Server ersetzt: {r['redactions']} Angabe(n)")
    print(f"E-Mail: {r['email'] or '—'}")
    print(f"Status-Link: https://opencaselaw.ch/vorschlag/#{r['token']}")
    print()
    print(textwrap.indent(textwrap.fill(r["text"], 96, replace_whitespace=False), "  "))
    if r["note"]:
        print(f"\nNotiz: {r['note']}")
    return 0


def _set(args) -> int:
    try:
        r = sg.set_status(args.id, args.status, args.note)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{sg.number(r['id'])} → {r['status']}" + (f" · {r['note']}" if r["note"] else ""))
    if r["email"]:
        subject = f"Ihr Vorschlag {sg.number(r['id'])} an OpenCaseLaw: {_STATUS_DE[r['status']]}"
        body = (f"Status: {_STATUS_DE[r['status']]}\n" + (f"\n{r['note']}\n" if r["note"] else "")
                + f"\nhttps://opencaselaw.ch/vorschlag/#{r['token']}\n")
        print("mailto:" + r["email"] + "?" + urllib.parse.urlencode(
            {"subject": subject, "body": body}, quote_via=urllib.parse.quote))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=sg.STATUSES)
    pl.add_argument("--all", action="store_true")
    pl.add_argument("--limit", type=int, default=50)
    ps = sub.add_parser("show")
    ps.add_argument("id", type=int)
    pt = sub.add_parser("set")
    pt.add_argument("id", type=int)
    pt.add_argument("status", choices=sg.STATUSES)
    pt.add_argument("--note")
    args = p.parse_args(argv)
    return {"list": _list, "show": _show, "set": _set}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
