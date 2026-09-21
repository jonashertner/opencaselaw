"""Registry of Swiss OA legal scholarship sources.

Each entry describes how to harvest one source via OAI-PMH (or, occasionally,
a custom scraper module). Add new sources here; the orchestrator
`scrapers/scholarship/harvest_all.py` walks this list.

`active=False` entries are scaffolded but not yet wired (e.g. require set-spec
discovery or a custom adapter).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ScholarshipSource:
    key: str                       # short slug; JSONL filename
    name: str                      # human-readable
    kind: str                      # 'oai_pmh' | 'custom'
    base_url: Optional[str] = None
    set_spec: Optional[str] = None
    metadata_prefix: str = "oai_dc"
    rate_limit: float = 1.0
    # Post-hoc subject-keyword filter applied during OAI ingest. When set,
    # only records whose dc:subject (or DDC classification, types, title,
    # language metadata) contains at least one of these keywords (case-
    # insensitive) are kept. Used to filter big multi-faculty IRs to law.
    # Example: ["340", "law", "Recht", "droit", "diritto", "Rechtswissenschaft"]
    subject_filter: tuple[str, ...] = ()
    # Default-license metadata applied when individual records don't carry
    # license info. The CC license URL we display in attribution.
    license_default: Optional[str] = None
    license_url_default: Optional[str] = None
    # If True, treat the source-default license as authoritative and apply
    # it to every record whose dc:rights doesn't surface a CC license
    # (e.g. ex-ante.ch which only emits a per-author copyright line via
    # OAI but publishes its CC-BY-NC-ND-4.0 license on the homepage).
    license_authoritative: bool = False
    # Free-form attribution text shown alongside every result served from
    # this source — required by CC-BY / CC-BY-SA / CC-BY-NC-SA / "free to
    # read" upstream terms.
    attribution: str = ""
    # User-facing homepage (linked from /docs/scholarship-licenses.html)
    homepage: Optional[str] = None
    notes: str = ""
    # Adaptive datestamp-windowed harvesting (oai_pmh.harvest windowed=True).
    # Required for repositories that silently truncate long resumption
    # chains — UNIGE ends its chain ~21k records into ~124k with a clean
    # "no token", indistinguishable from completion (found 2026-08-22, the
    # source had been frozen at datestamp<=2012 for months). Enable for any
    # IR with >20k records.
    windowed: bool = False
    active: bool = True
    # For 'custom' kind: module path (e.g. "scrapers.scholarship.leges")
    custom_module: Optional[str] = None


SOURCES: list[ScholarshipSource] = [
    # ── Historical Swiss legal periodicals via e-periodica (ETH Library) ─
    # Strategy: harvest the primary law set (ddc:340) AND adjacent DDC sets
    # that often contain law-relevant content (political science 320, public
    # admin 350) with a law-keyword subject filter to catch border-line
    # records that ETH didn't classify primarily as Law. Each set returns
    # ~3,000-30,000 records on full walk.
    ScholarshipSource(
        key="e_periodica_polsci",
        name="e-periodica — political science (law-filtered)",
        kind="oai_pmh",
        base_url="https://www.e-periodica.ch/oai",
        set_spec="ddc:320",
        subject_filter=(
            "Recht", "Verfassung", "Bundesgericht", "Bundesrecht",
            "Strafrecht", "Staatsrecht", "Verwaltungsrecht", "Völkerrecht",
            "droit", "constitution", "Gesetz", "law", "diritto",
            "jurisprudence", "Rechts",
        ),
        rate_limit=1.0,
        license_default="rightsstatements-in-copyright",
        license_url_default="https://rightsstatements.org/vocab/InC/1.0/",
        attribution=(
            "© respective rightsholders. Digitized by ETH Library "
            "(e-periodica.ch). Set ddc:320 (Political science) filtered "
            "post-hoc to law-relevant subjects."
        ),
        homepage="https://www.e-periodica.ch/",
        notes="Catches law-adjacent records ETH classified under DDC 320 "
              "(constitutional law, public-law journals).",
        active=True,
    ),
    ScholarshipSource(
        key="e_periodica_pubadmin",
        name="e-periodica — public administration (law-filtered)",
        kind="oai_pmh",
        base_url="https://www.e-periodica.ch/oai",
        set_spec="ddc:350",
        subject_filter=(
            "Recht", "Verfassung", "Bundesrecht", "Verwaltungsrecht",
            "Verwaltung", "Gesetz", "droit", "law", "diritto",
            "amtlich", "Behörde", "Polizei", "police", "Justiz",
        ),
        rate_limit=1.0,
        license_default="rightsstatements-in-copyright",
        license_url_default="https://rightsstatements.org/vocab/InC/1.0/",
        attribution=(
            "© respective rightsholders. Digitized by ETH Library "
            "(e-periodica.ch). Set ddc:350 (Public administration) "
            "filtered post-hoc to law-relevant subjects."
        ),
        homepage="https://www.e-periodica.ch/",
        notes="Catches admin-law journals and police/justice ministry "
              "publications under DDC 350.",
        active=True,
    ),
    ScholarshipSource(
        key="e_periodica_law",
        name="e-periodica — Swiss historical law journals (ETH Library)",
        kind="oai_pmh",
        base_url="https://www.e-periodica.ch/oai",
        set_spec="ddc:340",
        rate_limit=1.0,
        license_default="rightsstatements-in-copyright",
        license_url_default="https://rightsstatements.org/vocab/InC/1.0/",
        attribution=(
            "© respective rightsholders. Digitized and made freely available "
            "by ETH Library (e-periodica.ch). Each record links to its "
            "ETH-hosted PDF/HTML view. Re-use beyond fair-use private/non-"
            "commercial reading requires checking the per-issue rights "
            "statement on e-periodica.ch."
        ),
        homepage="https://www.e-periodica.ch/",
        notes="ETH Library's digitization of Swiss periodicals. Set 'ddc:340' "
              "filters all 400+ journals to law (Dewey 340). Records include "
              "legal texts from 1708 onwards (e.g. Berner Sammlung der "
              "Kantonsgesetze, ZSR/ZBJV/SJZ/RDS historical issues). OAI "
              "identifier format: 'oai:agora.ch:<journal>:YYYY:<issue>::<page>'.",
        active=True,
    ),

    # ── Repositorium.ch — Swiss-law disciplinary repository ──────────────
    ScholarshipSource(
        key="repositorium_ch",
        name="Repositorium.ch — Swiss-law disciplinary repository",
        kind="custom",
        custom_module="scrapers.scholarship.repositorium_ch",
        license_default="OA-author-deposited",
        license_url_default="https://repositorium.ch/",
        attribution=(
            "© respective authors. Self-deposited on Repositorium.ch — a "
            "Swiss-law-focused disciplinary repository run by a non-profit "
            "association, supported by the UZH Faculty of Law. License per "
            "record."
        ),
        homepage="https://repositorium.ch/",
        notes="Supabase-backed PostgREST API at api.repositorium.ch with the "
              "public anonymous JWT key. Currently small (~31 publications) "
              "but high-quality Swiss law content with rich metadata.",
        active=True,
    ),

    # ── UniNE LIBRA — discovered as working DSpace 7 OAI 2026-05-27 ──────
    ScholarshipSource(
        key="libra_unine",
        name="UniNE LIBRA — law content",
        kind="oai_pmh",
        base_url="https://libra.unine.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in LIBRA, the institutional "
            "repository of the University of Neuchâtel (DSpace 7). License "
            "per record."
        ),
        homepage="https://libra.unine.ch/",
        notes="UniNE repository (DSpace 7). ~31k records (earliest 2025-09 — "
              "newly launched repo). Filtered to law-keyword subjects.",
        active=True,
    ),

    # ── Custom-scraper OA Swiss law journals (Tier 2) ────────────────────
    ScholarshipSource(
        key="medialex",
        name="medialex — Zeitschrift für Medienrecht",
        kind="custom",
        custom_module="scrapers.scholarship.medialex",
        license_default="CC-BY-SA-4.0",
        license_url_default="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution=(
            "© respective authors. Published in medialex — Zeitschrift "
            "für Medienrecht (medialex.ch), CC-BY-SA-4.0."
        ),
        homepage="https://medialex.ch/",
        notes="WordPress site. REST API at /wp-json/wp/v2/posts. ~268 posts.",
        active=True,
    ),
    ScholarshipSource(
        key="eizpublishing",
        name="EuZ — Zeitschrift für Europarecht (eizpublishing.ch)",
        kind="custom",
        custom_module="scrapers.scholarship.eizpublishing",
        license_default="CC-BY-NC-ND-4.0",
        license_url_default="https://creativecommons.org/licenses/by-nc-nd/4.0/",
        attribution=(
            "© respective authors. Published by EIZ Publishing "
            "(eizpublishing.ch); EuZ — Zeitschrift für Europarecht. "
            "CC-BY-NC-ND-4.0."
        ),
        homepage="https://eizpublishing.ch/",
        notes="WordPress site with custom post types 'publikationen' (161) "
              "+ 'artikel' (87). Standard /posts is empty.",
        active=True,
    ),
    ScholarshipSource(
        key="anci_ch",
        name="Ancilla Iuris — Lagen des Rechts",
        kind="custom",
        custom_module="scrapers.scholarship.anci_ch",
        license_default="CC-BY-4.0",
        license_url_default="https://creativecommons.org/licenses/by/4.0/",
        attribution=(
            "© respective authors. Published in Ancilla Iuris — Lagen des "
            "Rechts (anci.ch), CC-BY-4.0, OA legal-theory journal since 2006."
        ),
        homepage="https://www.anci.ch/",
        notes="Static PDFs + per-article landing pages. ~100 articles total.",
        active=True,
    ),

    # ── Authored OA reference works ─────────────────────────────────────
    ScholarshipSource(
        key="thegoodboard",
        name="The Good Board — Swiss Corporate Governance Reference",
        kind="custom",
        custom_module="scrapers.scholarship.thegoodboard",
        license_default="OA-author-permitted-reuse",
        license_url_default="https://thegoodboard.ch/llms.txt",
        attribution=(
            "© Jonas Hertner. Published at thegoodboard.ch. Author "
            "explicitly permits ingestion by training corpora and "
            "retrieval systems; substantial reproduction must attribute "
            "the author and link to the original."
        ),
        homepage="https://thegoodboard.ch/",
        notes="Authored reference work on Swiss corporate governance — "
              "reference articles + commentary + agenda briefings + glossary. "
              "Sitemap-driven scrape (no OAI-PMH).",
        active=True,
    ),

    # Attribution-only entry: the shard is built once from the publisher's
    # ePub by scripts/build_shk_kommentar_shard.py (a static 2021 edition), so
    # there is nothing to harvest weekly — hence active=False.
    ScholarshipSource(
        key="shk_kommentar",
        name="Kommentar zur Schaffhauser Verwaltungsrechtspflege "
             "(Meyer/Herrmann/Bilger, Hrsg.; EIZ Publishing 2021)",
        kind="custom",
        license_default="CC-BY-SA",
        license_url_default=None,
        attribution=(
            "© 2021 the respective authors. Kilian Meyer / Oliver Herrmann / "
            "Stefan Bilger (Hrsg.), Kommentar zur Schaffhauser "
            "Verwaltungsrechtspflege, EIZ Publishing 2021, "
            "https://doi.org/10.36862/eiz-411. Imprint: \"CC BY-NC-ND (Werk), "
            "CC BY-SA (Text)\" — the text served here is CC BY-SA. Cite as: "
            "BearbeiterIn, in: Meyer/Herrmann/Bilger (Hrsg.), Kommentar zur "
            "Schaffhauser Verwaltungsrechtspflege, 2021, Art. X VRG/JG N. X."
        ),
        homepage="https://eizpublishing.ch/publikationen/"
                 "kommentar-zur-schaffhauser-verwaltungsrechtspflege/",
        notes="One record per Kommentierung (98 articles of VRG + JG), two "
              "essays, three checklists. Parsed from the ePub; margin numbers "
              "and text verified against the PDF edition. Editors and "
              "authors confirmed their agreement to the ingest (J. Hertner, "
              "2026-09-22).",
        active=False,
    ),

    # ── Standalone OA law journals (small but high-quality) ─────────────
    ScholarshipSource(
        key="sui_generis",
        name="sui generis (OJS, hosted on hope.uzh.ch)",
        kind="oai_pmh",
        base_url="https://sui-generis.ch/oai",
        set_spec="suigeneris",
        license_default="CC-BY-SA-4.0",
        license_url_default="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution=(
            "© respective authors. Published in sui generis "
            "(sui-generis.ch), CC-BY-SA-4.0. A small minority of articles "
            "carry CC-BY-NC-SA-4.0 — check the per-record license field."
        ),
        homepage="https://sui-generis.ch/",
        rate_limit=1.0,
        notes="Peer-reviewed OA Swiss law journal since 2014. Set 'suigeneris' "
              "groups all sub-sets (ART, INFO, MIGRAT, OEFF, PRIVAT, STRAF).",
        active=True,
    ),
    ScholarshipSource(
        key="leoh",
        name="LEOH — Journal of Animal Law, Ethics and One Health",
        kind="oai_pmh",
        base_url="https://leoh.ch/oai",
        set_spec="leoh",
        license_default="CC-BY-ND-4.0",
        license_url_default="https://creativecommons.org/licenses/by-nd/4.0/",
        attribution=(
            "© respective authors. Published in LEOH — Journal of Animal Law, "
            "Ethics and One Health (leoh.ch), peer-reviewed OA. Per-record "
            "license is typically CC-BY-ND-4.0 (no derivative works); some "
            "articles may carry CC-BY-4.0 — always check the per-record "
            "license field before deriving or remixing."
        ),
        homepage="https://leoh.ch/",
        notes="OJS, hosted on hope.uzh.ch like sui-generis. Sub-sets cover "
              "articles, legal education, jurisprudence, legislation, book reviews.",
        active=True,
    ),
    ScholarshipSource(
        key="leges",
        name="LeGes — Gesetzgebung & Evaluation (Bundeskanzlei)",
        kind="custom",
        custom_module="scrapers.scholarship.leges",
        license_default="OA-Swiss-federal",
        license_url_default="https://www.bk.admin.ch/bk/de/home/dokumentation/zeitschrift--leges-.html",
        attribution=(
            "Published by the Federal Chancellery of Switzerland (Bundeskanzlei). "
            "Federal publication: free of copyright (Art. 5 al. 1 lit. a URG) — "
            "may be reproduced without permission."
        ),
        homepage="https://www.bk.admin.ch/bk/de/home/dokumentation/zeitschrift--leges-.html",
        notes="Federal Chancellery's quarterly journal on legislation and "
              "evaluation. Hosted on leges.weblaw.ch; URL enumeration of "
              "/legesissues/YEAR/N.html + per-article HTML scrape.",
        active=True,
    ),
    ScholarshipSource(
        key="justice",
        name="Justice-Justiz-Giustizia (judges' association)",
        kind="custom",
        custom_module="scrapers.scholarship.justice",
        license_default="OA-no-redistribution",
        attribution=(
            "© respective authors and Schweizerische Vereinigung der "
            "Richterinnen und Richter (SVR-ASM). Reproduction beyond fair use "
            "requires permission from the publisher."
        ),
        homepage="https://richterzeitung.weblaw.ch/",
        notes="OA portion of the Swiss judges' association journal. Custom "
              "HTML scrape from justice-justiz-giustizia.ch / richterzeitung.weblaw.ch.",
        active=False,
    ),

    # ── University institutional repositories filtered to law ───────────
    # All Swiss IRs expose OAI-PMH. Set specs vary; "law"-filter discovery is
    # per-IR (some use faculty codes, some Dewey 340, some custom). Listed
    # here as scaffolding; activate as set-spec is verified.
    # Law-keyword subject filter applied to multi-faculty IRs. Catches:
    #   - Dewey "340" code in dc:subject or DDC URI
    #   - German "Recht" / "Rechtswissenschaft" / "Jura"
    #   - French "droit" / "jurisprudence"
    #   - Italian "diritto" / "giurisprudenza"
    #   - English "law" (LEGAL_KEYWORDS reused below)
    # _LAW_KEYWORDS exposed as a module-level tuple in case downstream
    # tooling needs to query the same filter.

    # --- multi-faculty IRs (broad harvest + subject filter to law) ---
    ScholarshipSource(
        key="unige_law",
        name="UNIGE Archive ouverte — law content",
        kind="oai_pmh",
        base_url="https://archive-ouverte.unige.ch/oai",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "ddc/340", "ddc:340",
        ),
        rate_limit=1.0,
        license_default=None,
        attribution=(
            "© respective authors. Deposited at Archive ouverte UNIGE "
            "(University of Geneva). License per record — typically CC-BY or "
            "'free to read'; check the per-record license field."
        ),
        homepage="https://archive-ouverte.unige.ch/",
        notes="UNIGE full-text + metadata. Filtered post-hoc to law-keyword "
              "subjects (~124k total records). Their OAI silently truncates "
              "chains at ~21k records (clean no-token end, no error) — the "
              "source served only datestamp<=2012 content until windowed "
              "harvesting (2026-08-22).",
        windowed=True,
        active=True,
    ),
    ScholarshipSource(
        key="alexandria_law",
        name="UniSG Alexandria — law + business law content",
        kind="oai_pmh",
        base_url="https://www.alexandria.unisg.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in Alexandria, University of "
            "St Gallen's research portal. License per record."
        ),
        homepage="https://www.alexandria.unisg.ch/",
        notes="UniSG repository (DSpace). Filtered post-hoc to law-keyword "
              "subjects (~61k total records).",
        active=True,
    ),
    ScholarshipSource(
        key="eth_research_collection",
        name="ETH Research Collection — law content",
        kind="oai_pmh",
        base_url="https://www.research-collection.ethz.ch/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Rechtsökonomie", "Rechts",
            "Regulierung", "regulation",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in ETH Research Collection, "
            "ETH Zurich. License per record."
        ),
        homepage="https://www.research-collection.ethz.ch/",
        notes="ETH RC (DSpace). Mostly STEM; filtered to law-relevant subjects "
              "to catch legal-tech, regulation, ethics, governance research "
              "(~113k total records, expect ~1-3% match rate).",
        active=True,
    ),

    # --- Big multi-faculty IRs (DSpace 7 at /server/oai/request) ---
    ScholarshipSource(
        key="edoc_unibas_law",
        name="UniBas edoc — law content",
        kind="oai_pmh",
        base_url="https://edoc.unibas.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Jura",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in edoc, the Open Access "
            "Repository of the University of Basel (DSpace 7). License "
            "per record. Persistent IDs: hdl.handle.net/20.500.14716/<id>."
        ),
        homepage="https://edoc.unibas.ch/",
        notes="UniBas repository (DSpace 7, handle prefix 20.500.14716). "
              "~162k total records; filtered to law-keyword subjects.",
        active=True,
    ),
    ScholarshipSource(
        key="zhaw_digitalcollection",
        name="ZHAW digitalcollection — law / regulatory content",
        kind="oai_pmh",
        base_url="https://digitalcollection.zhaw.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Regulierung", "regulation", "compliance",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in ZHAW digitalcollection "
            "(Zurich University of Applied Sciences, DSpace 7). License "
            "per record."
        ),
        homepage="https://digitalcollection.zhaw.ch/",
        notes="ZHAW UAS repository (DSpace 7). ~36k records; mostly applied "
              "sciences; expect ~200-500 law-relevant records (legal-tech, "
              "regulation, compliance research).",
        active=True,
    ),
    ScholarshipSource(
        key="fhnw_irf",
        name="FHNW IRF — law / business law content",
        kind="oai_pmh",
        base_url="https://irf.fhnw.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Wirtschaftsrecht", "Compliance", "Regulierung",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in the Institutional Repository "
            "FHNW (Fachhochschule Nordwestschweiz, DSpace 7). License per record."
        ),
        homepage="https://irf.fhnw.ch/",
        notes="FHNW UAS repository (DSpace 7). ~34k records; mostly applied / "
              "business; expect ~300-800 law/business-law records.",
        active=True,
    ),

    # --- Pure-law OJS journals discovered via Zenodo DACHLI catalog ---
    ScholarshipSource(
        key="cognitio",
        name="cognitio — Studentisches Forum für Recht und Gesellschaft",
        kind="oai_pmh",
        base_url="https://www.cognitio-zeitschrift.ch/index.php/index/oai",
        set_spec="cognitio",
        license_default="CC-BY-NC-SA-4.0",
        license_url_default="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        attribution=(
            "© respective authors. Published in cognitio — Studentisches "
            "Forum für Recht und Gesellschaft (cognitio-zeitschrift.ch), "
            "CC-BY-NC-SA-4.0 (non-commercial, share-alike)."
        ),
        homepage="https://www.cognitio-zeitschrift.ch/",
        notes="Swiss student law journal, hosted on SOAP2 (Shared Open Access "
              "Publishing Platform); set=cognitio.",
        active=True,
    ),
    ScholarshipSource(
        key="cfs",
        name="CFS — Criminologie, Forensique et Sécurité",
        kind="oai_pmh",
        base_url="https://www.cognitio-zeitschrift.ch/index.php/index/oai",
        set_spec="cfs",
        license_default="CC-BY-4.0",
        license_url_default="https://creativecommons.org/licenses/by/4.0/",
        attribution=(
            "© respective authors. Published in CFS — Criminologie, "
            "Forensique et Sécurité (Swiss French-language criminology + "
            "forensic-science + security journal, peer-reviewed OA)."
        ),
        homepage="https://cfs-journal.ch/",
        notes="Swiss criminology / forensic-science journal on SOAP2 OJS; "
              "set=cfs.",
        active=True,
    ),
    ScholarshipSource(
        key="ex_ante",
        name="ex/ante — Zeitschrift der juristischen Nachwuchsforschung",
        kind="oai_pmh",
        base_url="https://ex-ante.ch/index.php/index/oai",
        set_spec="exante",
        license_default="CC-BY-NC-ND-4.0",
        license_url_default="https://creativecommons.org/licenses/by-nc-nd/4.0/",
        license_authoritative=True,  # OAI dc:rights only has copyright line
        attribution=(
            "© respective authors. Published in ex/ante — Zeitschrift der "
            "juristischen Nachwuchsforschung (ex-ante.ch), peer-reviewed OA, "
            "CC-BY-NC-ND-4.0."
        ),
        homepage="https://ex-ante.ch/",
        notes="Swiss junior legal research journal, OJS; set=exante; "
              "sub-sets: PRE/ART/IND/THESE/THESELISTE.",
        active=True,
    ),

    # --- Still-problematic big IRs ---
    ScholarshipSource(
        key="zora_law",
        name="UZH ZORA — law content",
        kind="oai_pmh",
        base_url="https://www.zora.uzh.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Jura",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in the Zurich Open Repository "
            "and Archive (ZORA), University of Zurich. License per record."
        ),
        homepage="https://www.zora.uzh.ch/",
        notes="UZH repository (DSpace 7). completeListSize=217,930 "
              "(2026-08-22); filtered to law-keyword subjects. UZH Faculty "
              "of Law is the largest in Switzerland — expected highest "
              "single-IR yield. The 2026-05-27 '504 server-side timeout' is "
              "gone: Identify AND ListIdentifiers verified working "
              "2026-08-22. Windowed as a precaution at this chain length.",
        windowed=True,
        active=True,
    ),
    ScholarshipSource(
        key="boris_law",
        name="UniBE BORIS Portal — law content",
        kind="oai_pmh",
        base_url="https://boris-portal.unibe.ch/server/oai/request",
        set_spec=None,
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in BORIS Portal, the Bern "
            "Open Repository and Information System, University of Bern. "
            "License per record."
        ),
        homepage="https://boris-portal.unibe.ch/",
        notes="UniBE repository (DSpace 7, migrated from EPrints). ~167k "
              "total records; filtered to law-keyword subjects.",
        active=True,
    ),
    ScholarshipSource(
        key="serval_law",
        name="UNIL SERVAL — migrated to IRIS, OAI URL TBD",
        kind="oai_pmh",
        base_url="https://iris.unil.ch/server/oai/request",
        notes="UNIL migrated SERVAL to IRIS (iris.unil.ch); old /oaiprovider "
              "returns 403, new /server/oai/request returns SPA HTML. Needs "
              "outreach to UNIL library for current OAI URL.",
        active=False,
    ),
    ScholarshipSource(
        key="folia_law",
        name="UniFR FOLIA — superseded by unifr_law (SONAR)",
        kind="oai_pmh",
        base_url="https://folia.unifr.ch/oai2",
        notes="RESOLVED 2026-08-22: folia.unifr.ch is a SONAR instance — "
              "folia.unifr.ch/oai2d answers as 'Swiss Open Access "
              "Repository' with SONAR's global sets. Fribourg content is "
              "harvested as unifr_law below (sonar.rero.ch, set=unifr). "
              "This placeholder stays inactive.",
        active=False,
    ),

    # ── SONAR (sonar.rero.ch) — one Invenio endpoint, many institutions ──
    # ListSets verified 2026-08-22: unifr, usi, hesso, fernuni, hepbejune…
    # Records carry per-record rights; treated like the other multi-faculty
    # IRs (law-keyword filter post-hoc).
    ScholarshipSource(
        key="unifr_law",
        name="UniFR via SONAR — law content",
        kind="oai_pmh",
        base_url="https://sonar.rero.ch/oai2d",
        set_spec="unifr",
        subject_filter=(
            "340", "law", "Recht", "Rechtswiss", "droit", "diritto",
            "jurisprudence", "Jura",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in SONAR, the Swiss Open "
            "Access Repository (set: Université de Fribourg). License per "
            "record."
        ),
        homepage="https://sonar.rero.ch/",
        notes="Fribourg is the bilingual (de/fr) law faculty — direct "
              "target for the French private-law gap behind GitHub #89. "
              "Endpoint + set verified live 2026-08-22.",
        windowed=True,
        active=True,
    ),
    ScholarshipSource(
        key="usi_law",
        name="USI via SONAR — law content",
        kind="oai_pmh",
        base_url="https://sonar.rero.ch/oai2d",
        set_spec="usi",
        subject_filter=(
            "340", "law", "Recht", "droit", "diritto", "giurisprudenza",
            "jurisprudence",
        ),
        rate_limit=1.0,
        attribution=(
            "© respective authors. Deposited in SONAR, the Swiss Open "
            "Access Repository (set: Università della Svizzera italiana). "
            "License per record."
        ),
        homepage="https://sonar.rero.ch/",
        notes="First Italian-language academic source — the corpus held "
              "~140 it-language records total before this. Endpoint + set "
              "verified live 2026-08-22.",
        windowed=True,
        active=True,
    ),
    ScholarshipSource(
        key="libra_law",
        name="UniNE LIBRA — law faculty (endpoint TBD)",
        kind="oai_pmh",
        base_url="https://libra.unine.ch/oai2",
        notes="University of Neuchâtel. Probe returned 404; needs research.",
        active=False,
    ),
    ScholarshipSource(
        key="unilu_law",
        name="UniLU — law faculty (endpoint TBD)",
        kind="oai_pmh",
        base_url="https://zenodo.org/oai2d",
        notes="UniLU may deposit to Zenodo. Endpoint TBD.",
        active=False,
    ),
    ScholarshipSource(
        key="infoscience_epfl",
        name="EPFL Infoscience — law content (deferred)",
        kind="oai_pmh",
        base_url="https://infoscience.epfl.ch/oai2d",
        subject_filter=(
            "340", "law", "Recht", "droit", "diritto", "ethics", "policy",
        ),
        notes="EPFL is STEM-focused; law content is minimal (regulatory "
              "tech, ethics policy). Active only if first three IRs settle.",
        active=False,
    ),

    # ── National library + federal repositories ────────────────────────
    ScholarshipSource(
        key="e_helvetica_law",
        name="e-Helvetica (Swiss National Library)",
        kind="oai_pmh",
        base_url="https://www.e-helvetica.nb.admin.ch/oai/edoc",
        notes="Federal deposits (theses, government reports). Filter to law.",
        active=False,
    ),
    ScholarshipSource(
        key="bj_studien",
        name="BJ (Bundesamt für Justiz) — Berichte & Studien",
        kind="custom",
        custom_module="scrapers.scholarship.bj",
        notes="Federal Office of Justice publishes its commissioned legal "
              "research as PDFs on bj.admin.ch. No OAI-PMH; custom scrape.",
        active=False,
    ),

    # (e_periodica_law is now ACTIVE via OAI-PMH near the top — set ddc:340)
]


def active_sources() -> list[ScholarshipSource]:
    return [s for s in SOURCES if s.active]


def by_key(key: str) -> ScholarshipSource | None:
    for s in SOURCES:
        if s.key == key:
            return s
    return None


# ── Re-exported corpora (not in SOURCES, but served as scholarship) ──────
# These come from ok_commentaries.db via the build_legal_scholarship
# re-export step. They aren't OAI-PMH-harvested, but they ARE served from
# the scholarship corpus, so the licensing layer needs to know about them.
_REEXPORTED = {
    "onlinekommentar": {
        "name": "OnlineKommentar.ch",
        "license_default": "CC-BY-4.0",
        "license_url_default": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": (
            "© respective authors. Published on OnlineKommentar.ch under "
            "CC-BY-4.0. Re-use must credit author + journal + license."
        ),
        "homepage": "https://onlinekommentar.ch/",
        "notes": "Scholarly commentary on Swiss federal law. CC-BY-4.0.",
    },
    "openlegalcommentary": {
        "name": "OpenLegalCommentary.ch",
        "license_default": "CC-BY-SA-4.0",
        "license_url_default": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": (
            "© respective authors. Published on OpenLegalCommentary.ch under "
            "CC-BY-SA-4.0. Re-use must credit author + journal + license; "
            "derivatives must be licensed CC-BY-SA-4.0."
        ),
        "homepage": "https://openlegalcommentary.ch/",
        "notes": "OA commentaries on the Swiss Federal Constitution (BV).",
    },
}


def attribution_for_source(source_key: str) -> dict:
    """Return a dict with attribution / license / homepage for a source key.

    Falls back to a generic attribution string for sources without explicit
    metadata so we never serve an unattributed publication. The returned
    dict is safe to dump in MCP responses.
    """
    s = by_key(source_key)
    if s is not None:
        return {
            "source": s.key,
            "name": s.name,
            "license": s.license_default,
            "license_url": s.license_url_default,
            "attribution": s.attribution or (
                f"© respective authors. Indexed from {s.name}. "
                "License per record — check each item."
            ),
            "homepage": s.homepage,
        }
    if source_key in _REEXPORTED:
        r = _REEXPORTED[source_key]
        return {
            "source": source_key,
            "name": r["name"],
            "license": r["license_default"],
            "license_url": r["license_url_default"],
            "attribution": r["attribution"],
            "homepage": r["homepage"],
        }
    return {
        "source": source_key,
        "name": source_key,
        "license": None,
        "license_url": None,
        "attribution": (
            f"© respective authors. Source: {source_key}. License per record."
        ),
        "homepage": None,
    }


def license_usage_hint(license_code: str | None) -> dict:
    """Return machine-readable downstream-use guidance for a license code.

    Surfaced in MCP responses so LLMs / consumers know what they can do
    with the content. Keyed by upstream CC code + our internal labels.
    """
    if not license_code:
        return {
            "license": None,
            "may_attribute": True,
            "may_quote_verbatim": True,
            "may_summarize_or_paraphrase": "unknown",
            "may_redistribute": "unknown",
            "may_use_commercially": "unknown",
            "share_alike_required": False,
            "note": (
                "License not declared by the source. Default to fair-use "
                "quotation with attribution; do not redistribute without "
                "verifying the upstream rights statement."
            ),
        }
    code = license_code.upper()
    base = {
        "license": code,
        "may_attribute": True,
        "may_quote_verbatim": True,
        "may_summarize_or_paraphrase": True,
        "may_redistribute": True,
        "may_use_commercially": True,
        "share_alike_required": False,
        "note": "",
    }
    if code == "CC-BY-4.0":
        base["note"] = (
            "CC-BY-4.0: attribution required (author + license + link). "
            "Derivatives, commercial use, and redistribution are permitted."
        )
        return base
    if code == "CC-BY-SA-4.0":
        base["share_alike_required"] = True
        base["note"] = (
            "CC-BY-SA-4.0: attribution required. Derivatives MUST be "
            "released under CC-BY-SA-4.0 (Share-Alike). Indexing/search "
            "is a §3(b) collection — not a derivative — so the index "
            "itself need not be CC-BY-SA. Quotation downstream is fine."
        )
        return base
    if code == "CC-BY-ND-4.0":
        base["may_summarize_or_paraphrase"] = False
        base["note"] = (
            "CC-BY-ND-4.0: attribution required, NoDerivatives. You may "
            "quote verbatim with attribution; you may NOT publish a "
            "modified, paraphrased, abridged, or transformed version. "
            "LLM-generated summaries that re-publish modified content "
            "violate the ND clause."
        )
        return base
    if code == "CC-BY-NC-4.0":
        base["may_use_commercially"] = False
        base["note"] = (
            "CC-BY-NC-4.0: attribution required; non-commercial use only. "
            "Re-use in a commercial product or service requires separate "
            "permission from the rightsholder."
        )
        return base
    if code == "CC-BY-NC-SA-4.0":
        base["may_use_commercially"] = False
        base["share_alike_required"] = True
        base["note"] = (
            "CC-BY-NC-SA-4.0: attribution required, non-commercial only, "
            "Share-Alike. Derivatives must be CC-BY-NC-SA-4.0. Commercial "
            "use requires separate permission from the rightsholder."
        )
        return base
    if code == "CC-BY-NC-ND-4.0":
        base["may_use_commercially"] = False
        base["may_summarize_or_paraphrase"] = False
        base["note"] = (
            "CC-BY-NC-ND-4.0: attribution required, non-commercial, "
            "NoDerivatives. Verbatim quotation with attribution only; "
            "no modification; no commercial use."
        )
        return base
    if code == "OA-AUTHOR-PERMITTED-REUSE":
        base["note"] = (
            "OA-author-permitted-reuse: author has explicitly invited "
            "training corpora and retrieval systems to ingest the work. "
            "Substantial reproduction must attribute the author and link "
            "to the original."
        )
        return base
    if code == "OA-SWISS-FEDERAL":
        base["note"] = (
            "Federal Swiss publication: no copyright under Art. 5(1)(a) URG. "
            "Reproduction permitted without permission; attribution courteous."
        )
        return base
    if code == "ETH-LIBRARY-FREE-TO-READ":
        base["may_redistribute"] = "see-source"
        base["note"] = (
            "ETH Library 'free to read' grant: full text accessible; "
            "redistribution rights vary by upstream publisher. Check the "
            "original record before redistributing."
        )
        return base
    if code == "OA-NO-REDISTRIBUTION":
        base["may_redistribute"] = False
        base["note"] = (
            "Open access for reading; redistribution beyond fair use "
            "requires permission from the publisher."
        )
        return base
    # Unknown / vendor-specific
    base["note"] = (
        f"License '{license_code}' is not a recognized CC variant. Treat "
        "as 'all rights reserved' for derivative work and redistribution; "
        "fair-use quotation with attribution should still be safe."
    )
    base["may_summarize_or_paraphrase"] = "unknown"
    base["may_redistribute"] = "unknown"
    base["may_use_commercially"] = "unknown"
    return base


def licenses_catalog() -> list[dict]:
    """Full source/license catalog including re-exported corpora.

    Used by:
      - `list_scholarship_sources` MCP tool (license summary block)
      - `/api/scholarship/licenses` REST endpoint
      - `/scholarship-licenses.html` static dashboard page
    """
    rows = []
    for s in SOURCES:
        rows.append({
            "source": s.key,
            "name": s.name,
            "kind": s.kind,
            "license": s.license_default,
            "license_url": s.license_url_default,
            "attribution": s.attribution,
            "homepage": s.homepage,
            "active": s.active,
        })
    for k, r in _REEXPORTED.items():
        rows.append({
            "source": k,
            "name": r["name"],
            "kind": "re-export",
            "license": r["license_default"],
            "license_url": r["license_url_default"],
            "attribution": r["attribution"],
            "homepage": r["homepage"],
            "active": True,
        })
    return rows
