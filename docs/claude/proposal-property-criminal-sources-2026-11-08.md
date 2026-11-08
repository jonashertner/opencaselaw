# Property law, land register, tenancy and criminal law sources — proposal (2026-11-08)

Same approach as the SchKG and corporate builds: take in the public official texts (Art. 5 URG)
that practitioners in these fields cite. The research came from two discovery passes plus my own
live checks. Every count below was fetched live on 2026-11-08 unless it is marked otherwise.

## Where the gap is

**Not in the rulings.** A search for land-register supervision terms (`Grundbuchbeschwerde`,
`Aufsichtsbehörde über das Grundbuch`, `Grundbuchverwalter`) returns 697+ decisions. Apart from BGE
and BGer, they come from 20+ cantonal courts and authorities (LU, OW, AR, ZH OG, FR, BE, SG, AG,
ZG RR and others). Criminal rulings come in through every cantonal portal, plus BStGer and MKG.
No canton publishes individual Strafbefehle or Einstellungsverfügungen online, so there is no
prosecutor-decision source to scrape. The ZH Bezirksgerichte, AG Strafgericht and the ZH
Mietgericht are already in.

**The gap is the guidance layer.** `search_practice` holds nothing from the land registers,
notaries or prosecutors. A Grundbuch query returns only SchKG VZG forms, BSV WEF notes and a SIF
report. There are no prosecution Weisungen, no sentencing recommendations and no
Strafvollzug-Richtlinien. These texts are cited all the time in briefs and judgments (WOSTA, the
SSK Strafmassempfehlungen, the EGBA Wegleitung zur Lex Koller).

**Commentary is upstream-bound.** OnlineKommentar has no commentary on ZGB 641–977 (Sachenrecht).
On StGB/StPO it covers only scattered articles (IRSG, the CCC and a few StGB/StPO articles are
well covered). We cannot fix that ourselves.

## Tier 1 — official texts, crawlable, build now

### Criminal law

| # | Source | What | Volume (verified) | Shape / reuse |
|---|---|---|---|---|
| 1 | **ZH Oberstaatsanwaltschaft — WOSTA** | Weisungen für das Vorverfahren, the most-cited cantonal prosecution guidance | `zh.ch/content/dam/zhweb/bilder-dokumente/themen/sicherheit-justiz/strafverfahren/WOSTA_Stand_09.09.2026_barrierefrei_V02.pdf`, **289 pages**, current edition 09.09.2026. Linked from `/de/sicherheit-justiz/strafverfahren/informationszugang-interessenbindungen.html`. The non-"barrierefrei" filename that search engines index returns 404 | One long document → chunk per chapter/Ziffer. Version per "Stand" date; the filename changes on every edition, so discover it from the page and do not hard-code it |
| 2 | **ZH Strafmassempfehlungen** | sentencing recommendations of 23.11.2023 (SVG, BetmG, …) + a separate one for Umweltkriminalität | same page; 2 PDFs (11 pp. + 1) | same module as #1 |
| 3 | **SSK/CPS Empfehlungen** (Schweizerische Staatsanwälte-Konferenz) | national recommendations: Strafzumessung SVG and BetmG, Landesverweisung, Rechtshilfe, VOSTRA, häusliche Gewalt, Menschenhandel, Bankeneditionen, Gerichtsstand, … | `ssk-cmp.ch/de/dienstleistungen/empfehlungen`, **19 PDFs**, FR page parallel. Domain moved from ssk-cps.ch; old links 404 | static HTML list |
| 4 | **VS Ministère public — directives** | Strafzumessung SVG, Fahrfähigkeit, Landesverweisung, DNA, Rechtshilfe, Cannabis-Ordnungsbussen, … | `vs.ch/de/web/mp/directives-et-reglements`, **21 PDFs**, DE + FR | Liferay static |
| 5 | **Concordat latin (CLDJP)** | concordat règlements, directives (CLAE sorties/congés), décisions, recommandations (e.g. 2026-09 CLAE/CLP on Art. 67 CP) for the Strafvollzug and the juvenile and police concordats | `cldjp.ch` WordPress: `wp-json/wp/v2/media?mime_type=application/pdf` → **666 PDFs** (`X-WP-Total`), FR + some DE | WP REST, as in `scrapers/scholarship/wordpress_rest.py`. Filter out member lists and presentations (only règlement/directive/décision/recommandation) |
| 6 | **NWI + OSK concordats** (justizvollzug.ch) | Vorlagen/Arbeitsdokumente, incl. the Richtlinien annexes (Ripol, Opferinformationsrecht Art. 92a StGB), Vollzugsauftrag, Vollzugsplan-Richtziele | `/konkordate/vorlagen-arbeitsdokumente` **23 PDFs**; `/konkordate/jahresberichte` 44 PDFs | Webflow static. The Richtlinien themselves are a Tier 2 item (#T2-1) |
| 7 | **NKVF** (Nationale Kommission zur Verhütung von Folter) | facility visit reports + the cantonal authorities' Stellungnahmen, ~2010– | `nkvf.admin.ch/de/berichte-nach-jahr`: **356 PDF links** on the DE page; the discovery pass counted 178 documents (report and Stellungnahme pairs and language copies, to dedupe at build) | static list. Cited in detention and Art. 3 ECHR cases |
| 8 | **BJ Internationale Rechtshilfe in Strafsachen** | Jahresberichte IRH 2016–2024 + 11 documents in the sd-web widget, plus the Muster-Ersuchen | `bj.admin.ch/de/internationale-rechtshilfe-in-strafsachen`: 9 `jb-irh-*` + 11 `backend.bj.admin.ch/fileservice/...` PDFs (titles not yet read) | `scrapers/practice/sdweb.py`, as in bj_schkg. **The Wegleitung (10th ed. 2025) is still to locate**: the indexed `rhf.admin.ch/dam/rhf/.../wegleitung-strafsachen-d.pdf` returns 404, so it may be among the 11 fileservice PDFs |

### Property, land register, tenancy

| # | Source | What | Volume (verified) | Shape / reuse |
|---|---|---|---|---|
| 9 | **BWO Mitteilungen zum Mietrecht** | official federal digest of tenancy-court decisions ("Auszüge aus Gerichtsentscheiden"), many of them never published by the courts themselves | `bwo.admin.ch/de/mitteilungen-zum-mietrecht`, **64 PDFs**, each trilingual | Nuxt SSR payload → PDFs. One practice row per digest entry, `doc_type rechtsprechung` (as for the Bankenombudsman cases). Highest value per unit of effort in this proposal |
| 10 | **EGBA (BJ) Dokumentation und Weisungen** | Erläuterungen GBV (incl. Anhänge), the 2024 cross-cutting inspection report, the cantonal inspection reports (UR, OW, FR, VS, VD), Langzeitsicherung | `bj.admin.ch/de/dokumentation-und-weisungen`, 14+ PDFs, FR mirror | `sdweb.py` |
| 11 | **EGBA Lex Koller** | Wegleitung für die Grundbuchämter (BewG), Brexit Infoschreiben 2019/2020 | `bj.admin.ch/de/behoerdeninformationen`, 3 PDFs, DE/FR/IT | `sdweb.py`; same module as #10 |
| 12 | **ZH Notariatsinspektorat** (notariate-zh.ch) | a full Grundbuch practice handbook as HTML (Stockwerkeigentum, Schuldbrief, Grundpfand, Dienstbarkeiten, Lex Koller) + Merkblätter + the Dienstanweisung zur NotGebV | `/de/downloads/grundbuch` **12 PDFs**; ~100 handbook pages (discovery count, not recounted) | Drupal static. Largest canton; the first cantonal land-register practice in the corpus |
| 13 | **BS Grundbuch-Newsletter für Notariate** | Nr. 1–17, 2013–2025 | `bs.ch/bvd/grundbuch-und-vermessungsamt/grundbuch/informationen-zum-grundbuch-fuer-notariate`, 18 PDFs | static |
| 14 | **BE Grundbuchamt Handbuch** + **AG Weisung betreffend die Grundbuchführung** | one consolidated practice handbook each (BE 1.9 MB, AG 2.4 MB, consolidating earlier Kreisschreiben) | 1 + 1 PDF (URLs in scratchpad notes) | registry rows, `grundbuch_kantone` with the same shape as `schkg_kantone` (#12–14 go in one module, `authority = canton`) |

## Tier 2 — needs your call or another pass

1. **NWI/OSK Richtlinien proper** sit in `erlasse.konkordate.ch`. It is an ExtJS app (tenant
   `svk`) on the same vendor platform as the SKOS and URI law sites, and has no static listing.
   Mapping its JSON API is about a day's work. The Richtlinien are binding inter-cantonal texts
   (Ausgang/Urlaub, Arbeitsexternat, Kostgeld, …), so the material is worth it.
2. **BE Generalstaatsanwaltschaft Weisungen/Richtlinien.** BE's own count is 25 Weisungen + 8
   Richtlinien. Individual PDFs resolve under `staw.justice.be.ch/content/dam/.../weisungen/`, but
   no index page has been found yet.
3. **Other Ministères publics** (GE, TI, SG, LU, BS, AG beyond its single "Weisung Führung von
   Strafverfahren"): not found by guessable paths. Each needs a manual navigation pass.
4. **Cantonal land-register Weisungen outside ZH/BS/BE/AG.** GR (GIHA Kreisschreiben) exists but
   was not enumerated. FR, GE and VD are required by law to issue instructions, but none was found
   online, which would mean asking the cantons. SG, LU, TG, VS, TI, NE and BL were not checked.
5. **Notariatsaufsicht disciplinary decisions** (Notariatskommission/Chambre des notaires): not
   surveyed. They would fit the existing `*_anwaltsaufsicht` scraper pattern if any canton
   publishes them.
6. **BGBB / bäuerliches Bodenrecht.** No published practice collection was found; only the legal
   basis.
7. **Bundesanwaltschaft and AB-BA Tätigkeitsberichte.** Annual PDFs, official, 2011–. They are
   reports rather than guidance, but the AB-BA reports include its supervisory findings. Cheap to
   add, low priority.

## Checked and rejected
Cantonal Strafbefehle and Einstellungen (not published anywhere) · BAZG/EZV VStrR Strafbescheide
(not published) · Swissmedic criminal rulings (press releases only) · KSBS (old name of the SSK,
dead domain) · ASTRA Administrativmassnahmen (cantonal measures; there is no federal Weisung text) ·
cadastre.ch / V+D Kreisschreiben (peripheral; could be added later with `sdweb.py`).

## Build plan (if approved)
- New practice modules: `strafverfolgung` (#1–4, authority = ZH/SSK/VS, doc_types `weisung` /
  `empfehlung`), `strafvollzug_konkordate` (#5–6), `nkvf` (#7), extending the BJ sd-web pattern to
  `bj_rechtshilfe` (#8) and `egba` (#10–11), `bwo_mietrecht` (#9), `grundbuch_kantone` (#12–14).
- Golden-HTML/JSON fixtures, offline tests, then the `search_practice` source, authority and
  doc_type enums and the tool description in mcp_server.py (adds a new doc_type `empfehlung`).
- All of this lands in practice.db with the weekly practice run. No decisions.db court, no
  `publish.py` or schema change, so nothing here touches the nightly pipeline gate.
- Suggested order: #9 BWO → #1–3 WOSTA/ZH/SSK → #10–11 EGBA → #12–14 → #5–7 → #8.

## Build status (2026-11-08, uncommitted)
Built all of Tier 1 as seven practice sources, registered in `scrapers/practice/runner.py` and
advertised in the `search_practice` schema. These are the live dry-run counts. The NWI/OSK
Richtlinien are still missing: erlasse.konkordate.ch is a Scala.js app with a WebSocket / `/ajax`
protocol and no REST API, so it was not built.

| source | rows | notes |
|---|---|---|
| strafverfolgung | 103 | ZH WOSTA 17 chapters (Stand 09.09.2026) + Strafmassempfehlungen; SSK-CPS 38 (DE/FR); VS 42; AG 4 |
| bj_rechtshilfe | 56 | Wegleitung 2025 found at rhf.admin.ch `/dam/de/sd-web/hqB7Y2qO6Cv9/wegleitung-strafsachen.pdf`; ~30 further rhf checklists/Rundschreiben left for later |
| strafvollzug | 350 | CLDJP 287 (465 distinct media of 666 claimed; minutes, agendas, member lists dropped); NWI-OSK 63; language taken from the text where the file name gives none |
| nkvf | 258 | 180 visit reports (authority response usually in the same PDF), 50 reports, 24 NKVF Stellungnahmen, 4 factsheets |
| bwo_mietrecht | 419 | 407 decision extracts + 12 whole-issue rows; pdfplumber for the scanned Bände 1-52, PyMuPDF for 53-64 |
| egba | 38 | GBV Erläuterungen, inspections, Lex Koller Wegleitung, BGBB, ship register |
| grundbuch_kantone | 160 | ZH 117, BS 17, BE 18 (Handbuch by chapter, DE/FR), AG 8 |

Scans fall back to a title placeholder where tesseract lacks deu/fra/ita (dev machine). The
practice service runs `Sat 21:30 UTC` once the code is deployed.

## Tier 2 build status (2026-11-08, branch `claude/beautiful-williamson-b9d638`, not pushed)
Live dry-runs (2 s between requests, generic UA; the BE prosecutor host at its 10 s crawl delay), 0 failed:

| Tier 2 item | source | rows |
|---|---|---:|
| 1. NWI/OSK Richtlinien (erlasse.konkordate.ch) | strafvollzug | 72 (all Erlasse: Vereinbarungen, Reglemente, Tarife, 26 Richtlinien, Merkblätter, Standards) |
| 2. BE Generalstaatsanwaltschaft | strafverfolgung | 52 (26 DE + 26 FR) |
| 3. Other Ministères publics | strafverfolgung | VD 29, FR 58 (FR + DE), GE 49 |
| 4. Cantonal land-register Weisungen | grundbuch_kantone | 36 (GE 11, VS 19, AR 3, GR 1, LU 2) |
| 7. BA + AB-BA reports | strafverfolgung | BA 60 (2006-2025, DE/FR/IT), AB-BA 65 (annual + inspection) |
| rhf.admin.ch checklists etc. | bj_rechtshilfe | 78 (DE/FR/IT pages; 75 new) |
| 5. Notariatsaufsicht decisions, 6. BGBB | — | nothing official published; see the runbook |

erlasse.konkordate.ch turned out to be readable without the WebSocket: the app's `/ajax` "home" call returns
the whole tree as gzipped JSON with a direct PDF link per Erlass (see `strafvollzug.py`). Findings, skipped
items and the deploy steps: `runbooks/practice_tier2_and_tpf_link_2026-11.md`. Open question for Jonas:
vs.ch's robots.txt disallows the PDFs the Tier 1 VS prosecutor part downloads.
