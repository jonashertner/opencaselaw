"""
Jura Courts Scraper (JU Gerichte)
=================================
Scrapes court decisions from the Tribuna VTPlus platform at jurisprudence.jura.ch.

Platform: Tribuna GWT-RPC (same as SZ scraper)
Coverage: Tribunal cantonal (TC) + Tribunal de première instance (TPI)
Volume: ~1,079 decisions
Language: fr

Inherits from SZGerichteScraper — only overrides configuration constants.
HTML retrieval is not available for JU; all content is fetched via PDF fallback.
"""
from __future__ import annotations

import os
from pathlib import Path

from scrapers.cantonal.sz_gerichte import SZGerichteScraper


class JUGerichteScraper(SZGerichteScraper):
    """Scraper for Jura courts (TC + TPI) via Tribuna GWT-RPC.

    Note: jurisprudence.jura.ch blocks Hetzner IPs at the TCP level.
    Set JU_PROXY or SCRAPER_PROXY to a SOCKS5 tunnel if needed.
    Example: ssh -D 1080 -fNq relay-host and export JU_PROXY=socks5h://127.0.0.1:1080
    """

    CANTON = "JU"
    COURT_CODE_STR = "ju_gerichte"
    TRIBUNA_URL = "https://jurisprudence.jura.ch/tribunavtplus/loadTable"
    GWT_PERMUTATION = "C8CE51A1CBF8D3F8785E0231E597C2B4"
    GWT_MODULE_BASE = "https://jurisprudence.jura.ch/tribunavtplus/"
    DOWNLOAD_URL = "https://jurisprudence.jura.ch/tribunavtplus/ServletDownload/"

    # Optional JU-specific proxy override.
    # If empty, BaseScraper falls back to env SCRAPER_PROXY.
    PROXY = os.environ.get("JU_PROXY", "")

    # GWT-RPC search template — court filter has both TC and TPI (ArrayList size 2)
    SEARCH_TPL = (
        r"7|0|56|https://jurisprudence.jura.ch/tribunavtplus/|"
        r"CAC80118FB77794F1FDFC1B51371CC63|"
        r"tribunavtplus.client.zugriff.LoadTableService|search|"
        r"java.lang.String/2004016611|java.util.ArrayList/4159755760|Z|I|"
        r"java.lang.Integer/3438268394|java.util.Map||0|TC|TPI|0;false|5;true|"
        r"3613730c5bff07159093e019f4866a1fcd955672703a8598b0fb641a55bef20e"
        r"f2dc5c9ce5d7233d2335c2d83d7dcf5a1158da448ea78beeb249fe7a2a740656|"
        r"1|java.util.HashMap/1797211028|"
        r"decisionDate|Date de l'arrêt|dossierNumber|Dossier|classification|Classification|"
        r"indexCode|Source|dossierObject|Objet|law|Matière|shortText|Texte d'aperçu|"
        r"department|Département|createDate|Date de création|creater|Créateur|judge|Juge|"
        r"executiontype|Manière de liquidation|legalDate|Date exécutoire|objecttype|Type d'objet|"
        r"typist|Auteur|description|Description|reference|Référence|relevance|Pertinence|fr|"
        r"1|2|3|4|46|5|5|6|7|6|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|"
        r"8|8|8|5|5|9|9|9|5|5|5|5|7|10|5|5|5|5|5|5|5|"
        r"11|12|6|0|0|6|2|5|13|5|14|"
        r"11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|"
        r"1|{page_nr}|-1|11|11|0|9|0|9|-1|"
        r"15|16|17|18|0|19|18|"
        r"5|20|5|21|5|22|5|23|5|24|5|25|5|26|5|27|5|28|5|29|5|30|5|31|"
        r"5|32|5|33|5|34|5|35|5|36|5|37|5|38|5|39|5|40|5|41|5|42|5|43|"
        r"5|44|5|45|5|46|5|47|5|48|5|49|5|50|5|51|5|52|5|53|5|54|5|55|"
        r"11|56|11|11|12|12|0|"
    )

    # HTML retrieval is NOT available for JU — all content via PDF fallback
    HTML_TPL = ""

    # Verified doc_id -> decision_id map (2026-09-26 classification, see
    # SZGerichteScraper "Document-aware identity"); seeds state/ju_gerichte.docids.txt.
    DOCID_SEED = Path(__file__).resolve().parent / "seeds" / "ju_gerichte.docids.tsv"

    # Path decrypt templates
    DECRYPT_START = (
        "7|0|11|https://jurisprudence.jura.ch/tribunavtplus/|"
        "CAC80118FB77794F1FDFC1B51371CC63|"
        "tribunavtplus.client.zugriff.LoadTableService|urlEncodingTribuna|"
        "java.util.Map|java.util.HashMap/1797211028|java.lang.String/2004016611|partURL|"
    )
    DECRYPT_END = "|1|2|3|4|1|5|6|2|7|8|7|9|7|10|7|11|"

    def _fetch_html(self, doc_id: str) -> str:
        """HTML retrieval is not available for JU — always return empty."""
        return ""
