r"""publicationtc.fr.ch sends base64 document paths since ~2026-08 (offline regression).

Golden GWT-RPC page 0 of the FR listing, captured 2026-09-14: every row's encrypted path
is ~140 chars of base64 with "=" escaped as \\x3D. The hex-only recogniser produced
enc_path "" → "No download URL" → fetch None for every new FR decision (34 by
2026-09-14, zero intake since ~2026-08-10). GR / ZG / BE still send hex, which keeps the
old URL shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.base_tribuna import _gwt_unescape  # noqa: E402
from scrapers.cantonal.fr_gerichte import FRGerichteScraper  # noqa: E402

PAGE0 = (REPO / "tests" / "fixtures" / "tribuna_fr_page0_base64path_20260914.txt").read_text(encoding="utf-8")


def test_gwt_unescape():
    assert _gwt_unescape("abc\\x3D") == "abc="
    assert _gwt_unescape("a\\\\b\\\"c") == 'a\\b"c'
    assert _gwt_unescape("plain") == "plain"


def test_fr_rows_get_a_base64_enc_path_and_a_download_url():
    s = FRGerichteScraper()
    total, rows = s._parse_search_response(PAGE0)
    assert total == 14708
    by = {r["docket_number"]: r for r in rows}
    assert "601 2025 145" in by                                # an August-2026 arrêt on page 0
    row = by["601 2025 145"]
    assert row["decision_date"] == "2026-08-13"
    assert row["title"] and not row["title"].startswith("AAAA")   # a real string, not the token
    assert max(r["decision_date"] for r in rows) >= "2026-08-01"
    assert len(row["enc_path"]) >= 110 and "\\x" not in row["enc_path"]
    assert row["enc_path"].endswith("=")                       # \x3D decoded
    assert not row["title"].startswith("AAAA")                 # the token is not the title
    assert all(r["enc_path"] for r in rows), "every row on the page carries a path"
    url = s._build_download_url(row)
    assert url.startswith(f"{s._gwt_base}/ServletDownload/601_2025_145?path=")
    token = url.split("path=", 1)[1].split("&")[0]
    assert "%3D" in token and "+" not in token and "/" not in token
    assert url.endswith("&pathIsEncrypted=1&dossiernummer=601_2025_145")


def test_hex_path_keeps_the_legacy_url_shape():
    s = FRGerichteScraper()
    hexpath = "ab" * 40
    url = s._build_download_url({"docket_number": "602 2025 79", "enc_path": hexpath})
    assert url == (f"{s._gwt_base}/ServletDownload/602_2025_79_{hexpath}"
                   f"?path={hexpath}&pathIsEncrypted=1&dossiernummer=602_2025_79")
