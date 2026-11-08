"""
Swiss Cantonal Courts Registry
================================

Maps all 26 cantons to their court decision platforms.

Platform types discovered:
1. WEBLAW — Weblaw-hosted search portals (query ticket + HTML parsing)
2. TRIBUNA — Tribuna GWT-RPC portals (encrypted, paginated)
3. WEBLAW_VAADIN — Weblaw Vaadin-based portals (Playwright)
4. CUSTOM — Canton-specific implementations (each unique)
5. FINDINFO — FindInfo / Omnis platform
6. ICMS — ICMS document management
7. TYPO3 — TYPO3/DIAM CMS

Each entry contains:
- canton: Two-letter code
- name: Full name
- platform: Platform type
- courts: List of court instances with URLs
- notes: Implementation notes
- status: 'ready' | 'blocked' | 'partial'

Scraper keys (run_scraper.py) are listed in notes.
All 26 cantons have at least one working scraper.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CourtInfo:
    """A single court within a canton."""
    name: str          # e.g., "Obergericht"
    url: str           # Base URL
    has_pdf: bool = True
    has_html: bool = True
    notes: str = ""


@dataclass
class CantonRegistry:
    """Registry entry for a canton's courts."""
    canton: str
    name_de: str
    name_fr: str
    platform: str
    courts: list[CourtInfo] = field(default_factory=list)
    scraper_keys: str = ""
    notes: str = ""
    status: str = "ready"  # 'ready', 'blocked', 'partial'


# ============================================================
# Full registry
# ============================================================

CANTON_REGISTRY: dict[str, CantonRegistry] = {

    # --- AARGAU ---
    "AG": CantonRegistry(
        canton="AG",
        name_de="Aargau",
        name_fr="Argovie",
        platform="WEBLAW",
        courts=[
            CourtInfo("Gerichte", "https://agve.weblaw.ch"),
        ],
        scraper_keys="ag_gerichte",
        notes="Weblaw LEv4 portal.",
        status="ready",
    ),

    # --- APPENZELL INNERRHODEN ---
    "AI": CantonRegistry(
        canton="AI",
        name_de="Appenzell Innerrhoden",
        name_fr="Appenzell Rhodes-Intérieures",
        platform="CUSTOM",
        courts=[
            CourtInfo("Kantonsgericht", "https://www.ai.ch"),
        ],
        scraper_keys="ai_gerichte",
        notes="Custom scraper. Very small canton.",
        status="ready",
    ),

    # --- APPENZELL AUSSERRHODEN ---
    "AR": CantonRegistry(
        canton="AR",
        name_de="Appenzell Ausserrhoden",
        name_fr="Appenzell Rhodes-Extérieures",
        platform="WEBLAW",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://ar-gerichte.weblaw.ch"),
        ],
        scraper_keys="ar_gerichte",
        notes="Weblaw LEv4 portal.",
        status="ready",
    ),

    # --- BERN ---
    "BE": CantonRegistry(
        canton="BE",
        name_de="Bern",
        name_fr="Berne",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Zivil- und Strafgerichte", "https://www.zsg-entscheide.apps.be.ch/tribunapublikation", notes="Tribuna old, nf=20"),
            CourtInfo("Verwaltungsgericht", "https://www.vg-urteile.apps.be.ch/tribunapublikation", notes="Tribuna new, nf=21"),
            CourtInfo("Anwaltsaufsichtsbehörde", "https://www.aa-entscheide.apps.be.ch/tribunapublikation", notes="Tribuna old, nf=20, 65 decisions"),
            CourtInfo("BVD", "https://www.bvd-entscheide.apps.be.ch/tribunavtplus", notes="Tribuna old, nf=20, court filter TRI"),
            CourtInfo("Steuerrekurskommission", "https://www.strk-entscheide.apps.be.ch/tribunapublikation", notes="Tribuna new, nf=21, court filter StRK"),
        ],
        scraper_keys="be_zivilstraf, be_verwaltungsgericht, be_anwaltsaufsicht, be_steuerrekurs, be_bvd",
        notes="Tribuna for main courts. AA (65 decisions, old Tribuna). BVD and STRK answer only their own court filter (TRI, StRK); with the DLAConfig name (BVD) or STRK they return zero rows.",
        status="ready",
    ),

    # --- BASEL-LANDSCHAFT ---
    "BL": CantonRegistry(
        canton="BL",
        name_de="Basel-Landschaft",
        name_fr="Bâle-Campagne",
        platform="CUSTOM",
        courts=[
            CourtInfo("Entscheide-Kantonsgericht", "https://www.baselland.ch"),
        ],
        scraper_keys="bl_gerichte",
        notes="Custom scraper for baselland.ch Entscheide.",
        status="ready",
    ),

    # --- BASEL-STADT ---
    "BS": CantonRegistry(
        canton="BS",
        name_de="Basel-Stadt",
        name_fr="Bâle-Ville",
        platform="CUSTOM",
        courts=[
            CourtInfo("Appellationsgericht", "https://www.appellationsgericht.bs.ch"),
        ],
        scraper_keys="bs_gerichte",
        notes="Custom scraper.",
        status="ready",
    ),

    # --- FRIBOURG ---
    "FR": CantonRegistry(
        canton="FR",
        name_de="Freiburg",
        name_fr="Fribourg",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Kantonsgericht / Tribunal cantonal", "https://bdlf.fr.ch"),
        ],
        scraper_keys="fr_gerichte",
        notes="Tribuna GWT-RPC. Bilingual (de/fr).",
        status="ready",
    ),

    # --- GENÈVE ---
    "GE": CantonRegistry(
        canton="GE",
        name_de="Genf",
        name_fr="Genève",
        platform="CUSTOM",
        courts=[
            CourtInfo("Cour de justice", "https://justice.ge.ch"),
        ],
        scraper_keys="ge_gerichte",
        notes="Custom API scraper. French-language.",
        status="ready",
    ),

    # --- GLARUS ---
    "GL": CantonRegistry(
        canton="GL",
        name_de="Glarus",
        name_fr="Glaris",
        platform="FINDINFO",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://gl.entscheidsuche.ch"),
        ],
        scraper_keys="gl_gerichte",
        notes="FindInfo/Omnis platform. Very small canton.",
        status="ready",
    ),

    # --- GRAUBÜNDEN ---
    "GR": CantonRegistry(
        canton="GR",
        name_de="Graubünden",
        name_fr="Grisons",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Kantonsgericht / Verwaltungsgericht", "https://entscheide.gr.ch"),
        ],
        scraper_keys="gr_gerichte",
        notes="Tribuna GWT-RPC. Trilingual (de/rm/it). ~14k decisions.",
        status="ready",
    ),

    # --- JURA ---
    "JU": CantonRegistry(
        canton="JU",
        name_de="Jura",
        name_fr="Jura",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Tribunal cantonal", "https://jurisprudence.jura.ch"),
        ],
        scraper_keys="ju_gerichte",
        notes="Tribuna GWT-RPC. French-language. Blocks Hetzner IPs — scraped locally (1,052 decisions).",
        status="ready",
    ),

    # --- LUZERN ---
    "LU": CantonRegistry(
        canton="LU",
        name_de="Luzern",
        name_fr="Lucerne",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Kantonsgericht / Verwaltungsgericht", "https://gerichte.lu.ch"),
        ],
        scraper_keys="lu_gerichte",
        notes="Tribuna GWT-RPC.",
        status="ready",
    ),

    # --- NEUCHÂTEL ---
    "NE": CantonRegistry(
        canton="NE",
        name_de="Neuenburg",
        name_fr="Neuchâtel",
        platform="FINDINFO",
        courts=[
            CourtInfo("Tribunal cantonal", "https://jurisprudence.ne.ch"),
        ],
        scraper_keys="ne_gerichte",
        notes="FindInfo/Omnis (JurisWeb). French-language. Blocks Hetzner IPs — set NE_PROXY.",
        status="blocked",
    ),

    # --- NIDWALDEN ---
    "NW": CantonRegistry(
        canton="NW",
        name_de="Nidwalden",
        name_fr="Nidwald",
        platform="ICMS",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://www.nw.ch"),
        ],
        scraper_keys="nw_gerichte",
        notes="ICMS document management portal. ~498 decisions.",
        status="ready",
    ),

    # --- OBWALDEN ---
    "OW": CantonRegistry(
        canton="OW",
        name_de="Obwalden",
        name_fr="Obwald",
        platform="WEBLAW_VAADIN",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://ow-gerichte.weblaw.ch"),
        ],
        scraper_keys="ow_gerichte",
        notes="Weblaw Vaadin (Playwright). ~470 decisions.",
        status="ready",
    ),

    # --- ST. GALLEN ---
    "SG": CantonRegistry(
        canton="SG",
        name_de="St. Gallen",
        name_fr="Saint-Gall",
        platform="TYPO3",
        courts=[
            CourtInfo("Gerichte", "https://www.publikationen.sg.ch"),
        ],
        scraper_keys="sg_publikationen",
        notes="TYPO3/DIAM CMS. Weblaw portal (sg-entscheide.weblaw.ch) is DNS dead.",
        status="ready",
    ),

    # --- SCHAFFHAUSEN ---
    "SH": CantonRegistry(
        canton="SH",
        name_de="Schaffhausen",
        name_fr="Schaffhouse",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht", "https://obergerichtsentscheide.sh.ch"),
        ],
        scraper_keys="sh_gerichte",
        notes="KSD Backend CMS API + PDF. ~709 decisions.",
        status="ready",
    ),

    # --- SOLOTHURN ---
    "SO": CantonRegistry(
        canton="SO",
        name_de="Solothurn",
        name_fr="Soleure",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://so-gerichte.weblaw.ch"),
        ],
        scraper_keys="so_gerichte",
        notes="Custom scraper. ~8.9k decisions.",
        status="ready",
    ),

    # --- SCHWYZ ---
    "SZ": CantonRegistry(
        canton="SZ",
        name_de="Schwyz",
        name_fr="Schwyz",
        platform="TRIBUNA",
        courts=[
            CourtInfo("Kantonsgericht", "https://gerichte.sz.ch"),
            CourtInfo("Verwaltungsgericht", "https://gerichte.sz.ch"),
        ],
        scraper_keys="sz_gerichte, sz_verwaltungsgericht",
        notes="Tribuna VTPlus (GWT-RPC). ~3.2k decisions. 1 result per page (slow).",
        status="ready",
    ),

    # --- THURGAU ---
    "TG": CantonRegistry(
        canton="TG",
        name_de="Thurgau",
        name_fr="Thurgovie",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht", "https://rechtsprechung.tg.ch"),
        ],
        scraper_keys="tg_gerichte",
        notes="Confluence/Scroll Viewport HTML. RBOG series (~1.2k decisions).",
        status="ready",
    ),

    # --- TICINO ---
    "TI": CantonRegistry(
        canton="TI",
        name_de="Tessin",
        name_fr="Tessin",
        platform="FINDINFO",
        courts=[
            CourtInfo("Tribunale d'appello / Tribunale amministrativo", "https://www3.ti.ch/CAN/giurisprudenza"),
        ],
        scraper_keys="ti_gerichte",
        notes="FindInfo/Omnis. Italian-language. ~58k decisions on entscheidsuche.",
        status="ready",
    ),

    # --- URI ---
    "UR": CantonRegistry(
        canton="UR",
        name_de="Uri",
        name_fr="Uri",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht / Verwaltungsgericht", "https://www.ur.ch"),
        ],
        scraper_keys="ur_gerichte",
        notes="Custom scraper. Very small canton.",
        status="ready",
    ),

    # --- VAUD ---
    "VD": CantonRegistry(
        canton="VD",
        name_de="Waadt",
        name_fr="Vaud",
        platform="CUSTOM",
        courts=[
            CourtInfo("Tribunal cantonal", "https://prestations.vd.ch/pub/101623/"),
        ],
        scraper_keys="vd_gerichte",
        notes="Spring Boot REST API + PDF download. ~3.5-4k decisions/year since 2007.",
        status="ready",
    ),

    # --- VALAIS ---
    "VS": CantonRegistry(
        canton="VS",
        name_de="Wallis",
        name_fr="Valais",
        platform="CUSTOM",
        courts=[
            CourtInfo("Kantonsgericht / Tribunal cantonal", "https://www.vs.ch"),
        ],
        scraper_keys="vs_gerichte",
        notes="Custom scraper. Bilingual (de/fr). ~4.2k decisions.",
        status="ready",
    ),

    # --- ZUG ---
    "ZG": CantonRegistry(
        canton="ZG",
        name_de="Zug",
        name_fr="Zoug",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht", "https://www.zg.ch"),
            CourtInfo("Verwaltungsgericht", "https://www.zg.ch"),
        ],
        scraper_keys="zg_verwaltungsgericht",
        notes="Custom scrapers. Two courts.",
        status="ready",
    ),

    # --- ZÜRICH ---
    "ZH": CantonRegistry(
        canton="ZH",
        name_de="Zürich",
        name_fr="Zurich",
        platform="CUSTOM",
        courts=[
            CourtInfo("Obergericht", "https://www.gerichte-zh.ch", notes="livesearch.php API"),
            CourtInfo("Verwaltungsgericht", "https://vger.zh.ch"),
            CourtInfo("Sozialversicherungsgericht", "https://www.sozialversicherungsgericht.zh.ch"),
            CourtInfo("Steuerrekursgericht", "https://www.steuerrekurs.zh.ch"),
            CourtInfo("Baurekursgericht", "https://www.brg.zh.ch"),
        ],
        scraper_keys="zh_gerichte, zh_obergericht, zh_verwaltungsgericht, zh_sozialversicherungsgericht, zh_baurekursgericht, zh_steuerrekursgericht",
        notes="5 separate scrapers. ZH_Obergericht uses livesearch.php with date-range windows.",
        status="ready",
    ),
}


# ============================================================
# Summary statistics
# ============================================================


def print_registry_summary():
    """Print a summary of the cantonal court registry."""
    print(f"{'Canton':<6} {'Platform':<18} {'Scrapers':<50} {'Status'}")
    print("-" * 130)
    for code, entry in sorted(CANTON_REGISTRY.items()):
        print(
            f"{code:<6} {entry.platform:<18} "
            f"{entry.scraper_keys[:48]:<50} {entry.status}"
        )

    platforms = {}
    for entry in CANTON_REGISTRY.values():
        platforms[entry.platform] = platforms.get(entry.platform, 0) + 1

    print("\nPlatform distribution:")
    for p, count in sorted(platforms.items(), key=lambda x: -x[1]):
        print(f"  {p}: {count} cantons")

    statuses = {}
    for entry in CANTON_REGISTRY.values():
        statuses[entry.status] = statuses.get(entry.status, 0) + 1
    print("\nStatus distribution:")
    for s, count in sorted(statuses.items()):
        print(f"  {s}: {count} cantons")


if __name__ == "__main__":
    print_registry_summary()
