"""
Schwyz Verwaltungsgericht Scraper (SZ VG)
==========================================
Scrapes court decisions from the Tribuna VTPlus platform at gerichte.sz.ch/vg/.

Platform: Tribuna GWT-RPC
Coverage: Verwaltungsgericht Schwyz
Volume: ~1,800 decisions
Language: de

Source: https://gerichte.sz.ch/vg/
"""
from __future__ import annotations

from pathlib import Path

from scrapers.cantonal.sz_gerichte import SZGerichteScraper


class SZVerwaltungsgerichtScraper(SZGerichteScraper):
    """Scraper for Schwyz Verwaltungsgericht (VG) via Tribuna GWT-RPC."""

    COURT_CODE_STR = "sz_verwaltungsgericht"
    # Verified doc_id -> decision_id map (2026-11-25 classification).
    DOCID_SEED = Path(__file__).resolve().parent / "seeds" / "sz_verwaltungsgericht.docids.tsv"
    TRIBUNA_URL = "https://gerichte.sz.ch/vg/tribunavtplus/loadTable"
    GWT_PERMUTATION = "6AC682AB32A2550405E5C0C850B33E15"
    GWT_MODULE_BASE = "https://gerichte.sz.ch/vg/tribunavtplus/"
    DOWNLOAD_URL = "https://gerichte.sz.ch/vg/tribunavtplus/ServletDownload/"

    # Search template with VG credential
    SEARCH_TPL = (
        r"7|0|55|https://gerichte.sz.ch/vg/tribunavtplus/|"
        r"CAC80118FB77794F1FDFC1B51371CC63|"
        r"tribunavtplus.client.zugriff.LoadTableService|search|"
        r"java.lang.String/2004016611|java.util.ArrayList/4159755760|Z|I|"
        r"java.lang.Integer/3438268394|java.util.Map||0|TRI|0;false|5;true|"
        r"eb2a27c3b340f474c53a16cb29c55f4b90de38e26a9c875f739e151a447265a6"
        r"09dac39df8d75eb9eb8c245eb211962b|"
        r"1|java.util.HashMap/1797211028|"
        r"decisionDate|Urteilsdatum|dossierNumber|Dossier|classification|Klassierung|"
        r"indexCode|Quelle|dossierObject|Betreff|law|Rechtsgebiet|shortText|Vorschautext|"
        r"department|Abteilung|createDate|Erstelldatum|creater|Ersteller|judge|Richter|"
        r"executiontype|Erledigungsart|legalDate|Rechtskraftdatum|objecttype|Objekttyp|"
        r"typist|Schreiber|description|Beschreibung|reference|Referenz|relevance|Relevanz|de|"
        r"1|2|3|4|46|5|5|6|7|6|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|"
        r"8|8|8|5|5|9|9|9|5|5|5|5|7|10|5|5|5|5|5|5|5|"
        r"11|12|6|0|0|6|1|5|13|"
        r"11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|"
        r"1|{page_nr}|-1|11|11|0|9|0|9|-1|"
        r"14|15|16|17|0|18|18|"
        r"5|19|5|20|5|21|5|22|5|23|5|24|5|25|5|26|5|27|5|28|5|29|5|30|"
        r"5|31|5|32|5|33|5|34|5|35|5|36|5|37|5|38|5|39|5|40|5|41|5|42|"
        r"5|43|5|44|5|45|5|46|5|47|5|48|5|49|5|50|5|51|5|52|5|53|5|54|"
        r"11|55|11|11|12|12|0|"
    )

    # HTML retrieval template with VG credential (ASCII codes)
    HTML_TPL = (
        "7|0|15|https://gerichte.sz.ch/vg/tribunavtplus/|"
        "CAC80118FB77794F1FDFC1B51371CC63|"
        "tribunavtplus.client.zugriff.LoadTableService|getDocumentDetails|"
        "java.lang.String/2004016611|java.util.List|[B/3308590456|"
        "java.lang.Boolean/476441737|Z|TRI|{}||"
        "java.util.ArrayList/4159755760|0|de|"
        "1|2|3|4|10|5|5|5|6|5|7|8|9|5|5|10|11|12|13|0|14|"
        "7|96|101|98|50|97|50|55|99|51|98|51|52|48|102|52|55|52|99|53|"
        "51|97|49|54|99|98|50|57|99|53|53|102|52|98|98|54|56|53|100|51|"
        "48|57|57|97|53|49|53|51|57|97|49|55|54|100|55|49|54|102|50|100|"
        "51|100|50|97|55|99|102|54|57|98|51|101|49|56|99|53|97|53|97|98|"
        "51|98|51|55|100|98|53|52|49|55|101|56|99|55|57|53|100|48|"
        "8|0|0|12|15|"
    )

    # Decrypt templates for VG paths
    DECRYPT_START = (
        "7|0|11|https://gerichte.sz.ch/vg/tribunavtplus/|"
        "CAC80118FB77794F1FDFC1B51371CC63|"
        "tribunavtplus.client.zugriff.LoadTableService|urlEncodingTribuna|"
        "java.util.Map|java.util.HashMap/1797211028|java.lang.String/2004016611|partURL|"
    )
    DECRYPT_END = "|1|2|3|4|1|5|6|2|7|8|7|9|7|10|7|11|"
