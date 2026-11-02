# Materialien source defects found on the BEKJ (SR 172.023)

Date: 2026-11-02. Status: tool-layer fixes, G1 and G2 on branch `claude/zealous-kirch-8889ed`, rehearsed on a copy, not pushed or deployed. G3-G7 wait for Jonas's OK.

Measured on copies of production `materialien.db` (built 2026-11-02 04:31 UTC) and `statutes.db` (06:26 UTC), on the `consultations.db` metadata tables, and on Fedlex SPARQL. `decisions.db` was not touched. German rows unless stated.

## Corrections to the brief

- `proj/2020/67/cons_1` is in the corpus, stored as `eli/dl/proj/6020/67/cons_1` (its `event_id` is `proj/2020/67/cons_1`).
- BBl 2025 19 is not in the corpus. It is Fedlex type 84, Schlussabstimmungstext, which the ingest never requests.
- The 9 KB "stub" is not a bad file. `…-de-xml-1.xml` does not exist, and Fedlex answers 200 with its HTML app shell. SPARQL lists the real file as `…-de-xml.xml` (167 KB, 38 `<article>` elements).
- Art. 26 Abs. 5 is explained in a document we hold: the VEKJ Erläuternder Bericht, section "5 Erläuterungen zu Artikel 26 Absatz 5 BEKJ", p. 37.

## 1. `get_article_purpose`: draft text labelled as Botschaft

**Root cause.** `scripts/enrich_botschaft_links.py` makes one link per `article_anchor` found in a document. The Fedlex chain does tie both BBl 2023 679 and 680 to `cc/2025/583`. But 679 has 592 paragraphs and no anchor, so it gets no link, while the draft has 37 anchors and gets 37 links. The handler then wrote one fixed hint ("All text is verbatim from the Federal Council Botschaft") whatever it returned.

Behind that sit four parser faults in `search_stack/build_botschaft_corpus.py`:

| Fault | Effect | Measured |
|---|---|---|
| The XML parser reads `<article>`, `<chapter>` and similar, but Botschaften use `<level>` and plain `<p>Art. 26 …</p>` | No anchor, no `section_path` | 224 of 298 XML Botschaften have no anchor; 93 of those have 5 or more `Art.` headings |
| `current_anchor` never resets in XML | A stray `<article>` anchors the rest of the document (BBl 2026 1177: 242 paragraphs on "Art. 9") | All 95 anchored explanatory XML documents run to the end |
| PDF text has no blank lines, so `re.split(r"\n\s*\n")` keeps each page as one paragraph and only a page-top heading is seen | Most article comments get no anchor | All 1,771 typed PDF Botschaften; 422 have no anchor despite 5 or more headings; 491 of the 492 anchored ones with 5 or more headings have under half of them |
| `_akn_text` folds `authorialNote` into the paragraph | Footnote text sits inside "verbatim" paragraphs | BBl 2023 679 has 97 notes; not counted corpus-wide |

**Does it affect every act whose draft is ingested separately?** No. It needs a draft with anchors and a Botschaft without them. Drafts are linked for 34 acts. On 32 of them (12 national, 20 treaties) some articles answer with a draft only: 246 articles.

**Reach of the wrong answer.** Of 13,474 linked articles across 746 acts:

| Class | Articles | Acts |
|---|---|---|
| Draft only | 246 | 32 |
| No Botschaft, other Bundesblatt types | 802 | 156 |
| Botschaft plus another type | 503 | 92 |
| Any non-Botschaft row (hint was false) | 1,551 | 217 |
| Every source without page numbers | 816 | 123 |

**Botschaften lost to the link step.** Fedlex's chain gives 717 (Botschaft, act) pairs with the Botschaft in the corpus; 378 have no link. 104 of those pairs concern 102 national acts, including BEKJ, UEG, FPG, GüTG, EMBAG and BVVG. The rest are treaties, many with no article commentary to anchor.

**Pages were wrong too, beyond null.** `page` counts pages of the stored file, and the hint said to cite it. Tested against printed running heads on every German PDF:

| Period | Rule | Evidence |
|---|---|---|
| 2000-2020 | printed = `bbl_page + page - 1` | 2,490 of 2,491 testable documents |
| 2021- | printed = `page` | 208 of 208 |
| 1999 | mixed, not mapped | 67 / 21 / 23 unknown |
| BAR scans | no reliable head, not mapped | 21 of 300 testable |

FR and IT samples follow the same rule. Example: file page 77 of BBl 2006 7001 is printed page 7077, so "BBl 2006 7001, S. 77" was a wrong pinpoint. This affected every pre-2021 PDF quotation.

## 2. `get_materialien(law_code="BEKJ")`

Four separate causes:

1. **BBl 2023 679:** same missing links as above.
2. **A row cap (new finding).** The law-level query capped link rows at 60, newest first. 93 of 786 acts lost documents this way: OR showed 3 of 88, AIG 1 of 49. BEKJ's 111 draft rows alone would have pushed the Botschaft out even with its links fixed.
3. **BBl 2025 19:** type 84 is not in `FGA_DOC_TYPES`. 362 such documents exist since 2021; none is ingested.
4. **Vernehmlassungen:** `consultation_laws` comes only from `jolux:foreseenImpactToLegalResource`, and a new act has no SR number at consultation time. Fedlex has no edge from `proj/6020/67` to the parliamentary business `proj/7022/0206`, so SPARQL cannot supply it. 1,209 of 2,694 procedures have no SR link, 949 of them with documents.

## 3. `get_law`: adopted, not in force

**Root cause.** The consolidated XML keeps placeholders (`art_18_24`, `art_25`, `art_26`, `art_29_30`, `art_31`, `art_32_34`) carrying only the number and the note "Wird zu einem späteren Zeitpunkt in Kraft gesetzt." The mirror stores one of the six (Art. 28, empty) and drops the rest, so `get_law` returned `articles: []` and said nothing.

**Reach.**
- At least 54 of 5,546 acts have the marker in their German Fedlex XML; only 19 acts (31 rows) keep it in `statutes.db`.
- Some marked articles have text and were served as current law without warning (MWSTG Art. 34, LFG Art. 40a, DZV Art. 115h).
- Pending amendments, from the 362 type-84 documents: 15 have an AS act with a future entry date, 13 have an AS act with no date, 66 have entered into force, 268 have no `isFollowingAct` edge. That last group cannot be sorted without more work, so 28 is a floor.

**Source check.** AS `oc/2025/583` and BBl 2025 19 are identical for 37 of 38 articles, ignoring whitespace; only the final clause Art. 38 differs. The AS XML is available, also with 38 articles.

## Done on the branch (tool layer, `mcp_server.py`)

- `get_article_purpose`: draft text moves to `draft_texts` and never appears in `sources`. **Behaviour change:** an existing test that expected the draft in `sources` was updated on purpose.
- The hint is built from the types actually returned.
- `printed_page` added to `get_article_purpose` and `search_botschaft`; null where unknown, and the hint says to cite without a pinpoint then.
- `botschaft_candidates`: pointer without text to the Botschaft printed within 6 numbers before a linked draft under the same title. BEKJ gets BBl 2023 679.
- `get_law` / `get_doctrine` pointer: no longer offers drafts as "Botschaft paragraphs".
- `get_materialien`: caps documents, not rows; reports `botschaft_documents_total`; the note states the real types.
- Vernehmlassungen: unlinked procedures matched by the act's unique German abbreviation in parentheses, labelled `link_basis`. 274 procedures, 245 acts. Sampled precision 34 of 36 (RPV and BPV had other meanings), which is why it is labelled and never presented as a Fedlex link. Passages need their own heading to name the act.
- `get_law`: `in_force_note` on marked articles, `article_note` on an empty answer in a partly enacted act.

**Considered and rejected:** returning Botschaft text at read time by matching headings. Of 246 draft-only articles, 2 matched exactly and 180 loosely, and loose hits include the same number in another act of the bill (`Art. 26 Abs. 1` of the VwVG in BBl 2023 679). A quotation tool should not guess. Until G1 + G2 are deployed, BEKJ users get the pointer and `search_botschaft`.

**Live effect on BEKJ.** `get_materialien("BEKJ", article="26")` now returns both consultations and the VEKJ report's passage on Art. 26 Abs. 5, p. 37.

## G1 + G2: built and rehearsed (Jonas's OK, 2026-11-02)

Commits on the branch, not pushed. Run through `materialien_swap` on a copy with the production commands: 4,394 XML documents re-parsed, no failures, every source file matched its stored hash, `quick_check` and FTS `integrity-check` pass, 95 seconds offline.

**What changed beyond the plan.** Building this found three things the proposal did not cover:

1. **Renumbering produces wrong quotations.** A Botschaft numbers its comments by the draft, and Parliament inserts and drops articles. TJPG Art. 33 is Art. 40 of the Botschaft; BEKJ Art. 38 is its Art. 37. Before this, `get_article_purpose(TJPG, 40)` could quote the comment on a different provision. Only headings reveal it. G2 therefore places each comment by comparing its title with the enacted heading: same number, a different unique article, or, in a shifted stretch with nothing to compare, no link. The link keeps the document's number in `evidence` (`sparql_parliament_chain:draft_art=40`, no schema change), and the answer carries `draft_article`.
2. **The old text was often scrambled.** `_akn_text` put an element's tail before its children: `BBl , 2018 6949 7321` for `BBl 2018 6949, 7321`. The new reader keeps reading order, fixes spaces around `<sup>` and `<i>`, and stores each paragraph once.
3. **Ingest never re-parses an unchanged file**, and enrichment never deleted a link. `scripts/reparse_xml_materialien.py` is new, and each run now replaces the chain links of the documents it covers.

**Scope is XML only.** Applied to PDFs, the act filter would have dropped about 3,500 links that return the right text, because any capitalised line reads as a section there. PDF behaviour is unchanged.

**Results (German rows, production copy):**

| Measure | Before | After |
|---|---|---|
| XML Botschaften with anchors | 74 of 298 | 239 of 298 |
| XML paragraphs with a section path | 37,962 | 399,262 |
| Articles answered by a draft only | 246 (32 acts) | 29 (12 acts) |
| Articles with a Botschaft linked | 12,426 | 12,841 |
| National acts missing a Botschaft that Fedlex ties to them | 102 | 93 (all but one PDF) |
| Chain links, German | 2,200 | 2,589 (+460, -71) |
| Article links, all languages | 27,026 | 28,180 |
| Links corrected to the enacted number | 0 | 246 (7 acts, TJPG 210) |

- First Botschaft link: BEKJ, BVVG, FPG, GüTG, SIAG, UEG and three more acts.
- BEKJ Art. 26 and Art. 38 answer in DE, FR and IT. The new Art. 37 correctly has no comment.
- Language parity: 0 gaps across 75 document-act pairs. Placement is decided on the German edition and applied to all three, because the mirror lacks some rows in other languages (no Italian BEKJ Art. 38).

**Regression sweep** through the real handler, all 13,474 linked articles: 22 had text before and have none now. All were reviewed and are correct removals:
- 10 TJPG numbers that do not exist in the act or belong to annex acts (Art. 57-62, 88, 718, 747, 814).
- 9 TJPG articles in the shifted stretch that could not be placed (6 of them because the mirror has no heading to compare).
- SR 312.53 Art. 7 and 8, renumbered.
- GwG Art. 25, which was returning TJPG Art. 25.

**Remaining limits:**
- `page` stays null for XML documents until G3.
- PDF anchors still follow draft numbers and still cross all acts of a bill until G4. The renumbering fix does not reach them, and how many PDF-era acts were renumbered is unknown. This is the largest remaining risk of a wrong quotation.
- Three note exchanges (SR 0.362.381.019, .020, .031) lost all article links. Those links pointed at EU regulation articles, but the acts now list no document until G5.
- A range heading (`Art. 32–34`) anchors its first number only.
- BBl 2024 1201 keeps no link to its Bundesgesetz, because no heading separates it from the treaty.
- `docs/canonical_numbers.md` needs the new counts after the swap.

**Deploy, in this order. Not done.** See the section in `runbooks/materialien_completeness.md`. Code first: the old server cannot read `draft_art`.

## Gated changes (need Jonas's OK)

G1 and G2 are built, see above; deploying them still needs his OK.

| # | Change | Touches | Fixes |
|---|---|---|---|
| G1 | XML parser: read `<level>` headings into `section_path`, take `Art. N` paragraphs as anchors, reset at each level, skip `authorialNote` | `build_botschaft_corpus.py`, re-parse 1,466 DE XML docs (×3 languages) | Article text for the BEKJ class |
| G2 | Act scoping in link enrichment: link an anchor only inside that act's section. Today every anchor is crossed with every act of the bill: 14 bills with more than one national act already carry 1,622 links exposed to this | `enrich_botschaft_links.py` | Prevents VwVG Art. 26 linking to BEKJ Art. 26 |
| G3 | XML page alignment: match XML paragraphs to pdf-a pages | new pass, about 4,400 PDFs | Citable pages for XML documents |
| G4 | Split PDF page blobs at `^Art. N` lines; can run on stored text, no refetch | parser + one re-chunk pass | Anchors for pre-2022 Botschaften |
| G5 | Additive `law_document_links` table from the chain | schema | Lists documents with no article anchors (treaties, EMBAG-type gaps) |
| G6 | Add type 84, slug `schlussabstimmungstext`, to `FGA_DOC_TYPES` | ingest, systemd `--types`, `search_botschaft` enum | BBl 2025 19 in the corpus |
| G7 | Store placeholders and adopted text, see below | `build_statutes_db.py`, schema | Defect 3 |

**G1 + G3 prototype** (scratchpad, not in the repo): on BBl 2023 679 it gives Art. 26 on pp. 33-34, scoped to section 5.1, with VwVG Art. 26 kept apart in 5.2.2. Alignment: 565 of 572 paragraphs. Five more Botschaften: 346/348, 925/925, 726/735, 151/152, 382/385. PDF page equals printed page there ("82 / 82").

**Run on a copy.** All of G1-G6 go through `search_stack/materialien_swap.py`. Expect link counts to rise and some wrong links to drop; a baseline diff of article links per act should be reviewed before the swap.

### G7: serving adopted text

Proposed response when an article is not in force:

```json
{
  "articles": [],
  "adopted_text": {
    "status": "adopted_not_in_force",
    "source": "<citation from the source record>",
    "eli_uri": "https://fedlex.data.admin.ch/eli/oc/2025/583",
    "adopted": "2024-12-20",
    "entry_into_force": null,
    "articles": [{"article_num": "26", "heading": "…", "text": "…"}],
    "note": "Adopted, not in force. Not current law."
  }
}
```

Rules:
- Never merge into `articles`. `articles` stays what is in force, so the quote rail, `as_of` and the search index keep their meaning.
- Source order: the AS act if Fedlex has one, otherwise the Schlussabstimmungstext.
- Parse with the statute parser, so `article_num` follows `get_law`.
- Not-in-force status comes from the placeholders only, never from "article missing in the consolidation", which would catch repealed articles too.
- Store placeholder rows with their range and note in a new additive table, built by the monthly Fedlex run.
- First version covers new acts. Pending amendments to existing acts need a separate shape, since their text is an amending instruction; list them next to `pending_changes`.
- Fetch through SPARQL manifestations. Never guess a filename, and reject a response that is not XML.

## M10: annexes under `<components>`, built and rehearsed

Reported by the BEKJ commentary session, confirmed on all 4,394 cached files, built with Jonas's OK of 2026-11-02. `parse_akoma_ntoso_xml` read the first `body` and never `<components>`. The old parser did the same, so G1 neither caused nor fixed it.

German XML documents with components, as they stood:

| Type | With components | In body | In components |
|---|---|---|---|
| Botschaft | 51 of 298 | 689,138 | 340,346 |
| Erlassentwurf | 68 of 482 | 154,504 | 95,388 |
| Bericht des Bundesrates | 16 of 31 | 118,351 | 274,112 |
| Bericht einer Kommission | 39 of 146 | 970,430 | 85,657 |
| Stellungnahme des Bundesrates | 1 of 94 | 1,065 | 759 |

In 39 of 175 such documents components held most of the text; BBl 2026 1528 kept 933 of about 42,000 words. For a draft or final text they carry the changes to other acts.

**Fix.** The parser reads `<components>` after the body, under the attachment's title (`Anhang > Änderung anderer Erlasse > 6. Zivilprozessordnung [SR 272]`).

**Attached rows are never anchored.** An attachment is another act's articles, or a treaty or draft attached to a Botschaft. Neither explains the article asked about. Anchored, its Art. 26 could come back in `sources` as Botschaft commentary, the defect this work started from. So annex text is searchable through `search_botschaft`, but `get_article_purpose` does not answer from it.

**Rehearsed on a copy.**
- BBl 2023 680 de: 5,147 to 9,089 words, with the Art. 128c ZPO sentence.
- BBl 2025 19 de, parsed from the file: 10,124 words. It reaches the corpus only with G6.
- XML paragraphs 451,530 to 513,554; 11,030 rows under an `Anhang` path.
- Article links identical to the G1 + G2 rehearsal: 28,180, none added or removed. Anchored rows are byte-identical, so the G1 + G2 regression sweep holds unchanged.
- No attached row carries an anchor, in any document type.

The rows lost to the old parser cannot be searched until the re-parse has run.

Also reported, not verified here: S9 (`-an` means anonymised on Fedlex, while `_FORMAT_PRIORITY` treats it as Akoma Ntoso and prefers it), S10 (wrong date and title on PKG rows), S11 (`cite` misses). See `docs/claude/bekj-fixes-handover-2026-11-02.md`, in the primary checkout.

## Not verified

- Precision of the abbreviation match rests on a 36-row sample.
- The 54-act marker count is a text search on German XML with two phrases, so it is a floor. Some hits may refer to treaties.
- `printed_page` was tested by page number in running text, not by opening PDFs, except for a few documents.
- G1 + G2 ran on copies from 2026-11-02 04:31 UTC. Live data will differ slightly by deploy time.
- Anchor detection was reviewed on samples and by FR/DE parity (4,053 against 4,065 anchors, before the last two heading forms were added), not by reading all 4,394 documents.
- Why the mirror kept Art. 28 and not the other placeholders was not traced.
