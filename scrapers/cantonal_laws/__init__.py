"""Cantonal law scrapers — direct scraping from official cantonal portals."""
from __future__ import annotations

# Registry: canton code → (module, class_name)
CANTONAL_LAW_SCRAPERS: dict[str, tuple[str, str]] = {
    # TI — PHP portal at www3.ti.ch/CAN/RLeggi
    "TI": ("scrapers.cantonal_laws.ti", "TIScraper"),
    # ZH — custom AEM CMS + PDF from notes.zh.ch
    "ZH": ("scrapers.cantonal_laws.zh", "ZHScraper"),
    # SIL cantons — server-rendered HTML (Word-generated)
    "GE": ("scrapers.cantonal_laws.sil", "SILScraper"),
    "NE": ("scrapers.cantonal_laws.sil", "SILScraper"),
    # LexWork cantons (18) — all use the same API
    "AG": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "AI": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "AR": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "BE": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "BL": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "BS": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "FR": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "GL": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "GR": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "LU": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "NW": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "OW": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "SG": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "SH": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "SO": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "TG": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    # UR — discovered 2026-05-02: rechtsbuch.ur.ch is a LexWork SPA with the
    # same /api/{lang}/texts_of_law/* endpoints as the other 18 cantons.
    # Replaces the lossy LexFind PDF fallback (489 laws → direct extraction).
    "UR": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "VS": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    "ZG": ("scrapers.cantonal_laws.lexwork", "LexWorkScraper"),
    # ──────────────────────────────────────────────────────────
    # Still on lexfind_pdf fallback (lossy text extraction):
    #   JU 1,164 laws — rsju.jura.ch (custom IceCube CMS)
    #   VD 1,305 laws — Base législative vaudoise (BLV) under www.vd.ch
    #   SZ   587 laws — www.sz.ch/kanton/gesetze/systematische-gesetzsammlung
    # Each needs a custom scraper (no shared LexWork API). See
    # docs/cantonal_laws_missing.md for portal URLs and structural notes.
    # ──────────────────────────────────────────────────────────
}

# LexWork portal hostnames per canton
LEXWORK_HOSTS: dict[str, str] = {
    "AG": "gesetzessammlungen.ag.ch",
    "AI": "ai.clex.ch",
    "AR": "ar.clex.ch",
    "BE": "www.belex.sites.be.ch",
    "BL": "bl.clex.ch",
    "BS": "www.gesetzessammlung.bs.ch",
    "FR": "fr.clex.ch",
    "GL": "gl.clex.ch",
    "GR": "www.gr-lex.gr.ch",
    "LU": "srl.lu.ch",
    "NW": "nw.clex.ch",
    "OW": "ow.clex.ch",
    "SG": "www.gesetzessammlung.sg.ch",
    "SH": "sh.clex.ch",
    "SO": "bgs.so.ch",
    "TG": "www.rechtsbuch.tg.ch",
    "UR": "rechtsbuch.ur.ch",
    "VS": "vs.clex.ch",
    "ZG": "zg.clex.ch",
}

# Primary legislation language per canton
CANTON_LANG: dict[str, str] = {
    "AG": "de", "AI": "de", "AR": "de", "BE": "de", "BL": "de",
    "BS": "de", "FR": "fr", "GE": "fr", "GL": "de", "GR": "de",
    "JU": "fr", "LU": "de", "NE": "fr", "NW": "de", "OW": "de",
    "SG": "de", "SH": "de", "SO": "de", "SZ": "de", "TG": "de",
    "TI": "it", "UR": "de", "VD": "fr", "VS": "de", "ZG": "de",
    "ZH": "de",
}


def mount_retries(session, total: int = 3, backoff_factor: float = 5.0) -> None:
    """Retry transient upstream failures before a law counts as an error.

    5xx responses, connection resets and read timeouts are retried up to
    `total` times with exponential backoff (5 s, 10 s, 20 s at the default
    factor; a Retry-After header is honoured). 4xx is not retried, so the
    callers' 404 handling is unchanged, and a 5xx that survives every retry
    still comes back as a normal response for raise_for_status().

    Why: on 2026-09-02 zh.ch answered four detail pages with 500 inside two
    minutes. Each was a one-shot error, the canton was marked FAIL, the
    monthly unit exited 1 and the DB rebuild was skipped for all 26 cantons.
    All four pages served normally minutes later.
    """
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry

    retry = Retry(
        total=total, connect=total, read=total, status=total,
        backoff_factor=backoff_factor,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
