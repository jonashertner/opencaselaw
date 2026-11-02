"""The answer must say which document a passage comes from.

Found 2026-11-02 on BEKJ Art. 26 (SR 172.023): get_article_purpose returned the
draft act BBl 2023 680 with page null under a hint calling all text "verbatim
from the Federal Council Botschaft", while the Botschaft BBl 2023 679 has no
link row at all (its XML carries no article anchors). get_law repeated the
claim, and get_materialien listed the draft alone. Measured on production the
same day: 246 articles of 32 acts answered with a draft only, 93 of 786 acts
lost documents to a row cap, every PDF before 2021 gave a `page` that is not
the printed page.

These tests pin the read side: draft text never appears as an explanation, the
hint names the types actually returned, a page is only offered as citable when
it is one, a law-level list is not eaten by one long message, consultations of
a new act are found through the act's name and labelled as such, and a
not-yet-in-force provision does not read as a missing one.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mcp_server as m  # noqa: E402
from _statutes_fixture import make_statutes_conn  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402
from search_stack import build_consultation_corpus as bcc  # noqa: E402

SR = "172.023"
FEDLEX = "https://fedlex.data.admin.ch"
DRAFT_TITLE = ("Bundesgesetz über die Plattformen für die elektronische Kommunikation "
               "in der Justiz (BEKJ) (Entwurf)")
BOT_TITLE = ("Botschaft zum Bundesgesetz über die Plattformen für die elektronische "
             "Kommunikation in der Justiz")
DRAFT_ART_26 = "1 Ist eine Plattform am Tag, an dem eine Frist abläuft, nicht erreichbar."


class Db:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE materialien (id INTEGER PRIMARY KEY, law_code TEXT, sr_number TEXT,
                article TEXT, bbl_ref TEXT, bbl_page_refs TEXT, legislative_intent TEXT,
                key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
                general_context TEXT);
            CREATE TABLE amendment_refs (sr_number TEXT, article TEXT, ref_type TEXT,
                year INTEGER, page INTEGER, fedlex_url TEXT, context TEXT);
            CREATE TABLE parliamentary_modifications (law_code TEXT, council TEXT,
                date TEXT, text TEXT);
        """)
        bbc.ensure_schema(self.conn)
        bbc.migrate_additive(self.conn)

    def doc(self, year, page, doc_type="botschaft", title=None, lang="de", fmt="pdf",
            paras=(), link=(), sr=SR, collection=None):
        prefix = "BBl" if lang == "de" else "FF"
        bid = self.conn.execute(
            "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
            "source_url, format, language, ingested_at, doc_type, title, source_collection) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (year, page, f"{prefix} {year} {page}", f"{FEDLEX}/eli/fga/{year}/{page}",
             f"{FEDLEX}/filestore/{year}-{page}-{lang}.pdf", fmt, lang,
             "2026-11-02T00:00:00+00:00", doc_type, title, collection)).lastrowid
        for order, (pg, anchor, text) in enumerate(paras, 1):
            self.conn.execute(
                "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, "
                "section_path, article_anchor, text, text_length) VALUES (?,?,?,?,?,?,?)",
                (bid, order, pg, None, anchor, text, len(text)))
        for art in link:
            self.conn.execute(
                "INSERT INTO article_botschaft_links VALUES (?,?,?,'considered','fixture')",
                (sr, art, bid))
        self.conn.commit()
        return bid


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "materialien.db"
    out = Db(path)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(path))
    monkeypatch.setattr(m, "MATERIALIEN_DB_PATH", path)
    monkeypatch.setattr(m, "_resolve_materialien_sr", lambda conn, law: SR)
    monkeypatch.setattr(m, "_get_consultations_conn", lambda: None)
    return out


def _bekj(db):
    """The production shape: Botschaft in the corpus without a link, draft linked."""
    db.doc(2023, 679, title=BOT_TITLE, fmt="akoma-ntoso-xml",
           paras=[(None, None, "Art. 26 Nichterreichbarkeit einer Plattform")])
    db.doc(2023, 680, "erlassentwurf", DRAFT_TITLE, fmt="akoma-ntoso-xml",
           paras=[(None, "26", DRAFT_ART_26)], link=["26"])


# ── get_article_purpose ───────────────────────────────────────────────────


def test_draft_text_is_never_a_source_and_never_called_botschaft(db):
    _bekj(db)
    out = m._handle_get_article_purpose(sr_number=SR, article="26")
    assert out["sources"] == []
    [draft] = out["draft_texts"]
    assert draft["bbl_citation"] == "BBl 2023 680"
    assert draft["doc_type_label"] == "Erlassentwurf"
    assert draft["paragraphs"][0]["text"] == DRAFT_ART_26
    assert "verbatim from the Federal Council Botschaft" not in out["_hint"]
    assert "DRAFT" in out["_hint"]


def test_botschaft_beside_the_draft_is_a_pointer_without_text(db):
    _bekj(db)
    out = m._handle_get_article_purpose(sr_number=SR, article="26")
    [c] = out["botschaft_candidates"]
    assert c["bbl_citation"] == "BBl 2023 679" and c["doc_type"] == "botschaft"
    assert c["basis"] == "printed_before_the_linked_draft_same_title"
    assert "paragraphs" not in c


def test_candidate_needs_the_same_title_and_a_near_page(db):
    db.doc(2023, 679, title="Botschaft zur Änderung des Zollgesetzes")
    db.doc(2023, 600, title=BOT_TITLE)  # right title, printed elsewhere
    db.doc(2023, 680, "erlassentwurf", DRAFT_TITLE,
           paras=[(None, "26", DRAFT_ART_26)], link=["26"])
    out = m._handle_get_article_purpose(sr_number=SR, article="26")
    assert "botschaft_candidates" not in out


def test_linked_botschaft_wins_and_the_draft_stays_apart(db):
    db.doc(2023, 679, title=BOT_TITLE, link=["26"],
           paras=[(26, "26", "Die Nichterreichbarkeit muss nicht mit einer Störung zusammenhängen.")])
    db.doc(2023, 680, "erlassentwurf", DRAFT_TITLE, link=["26"],
           paras=[(None, "26", DRAFT_ART_26)])
    out = m._handle_get_article_purpose(sr_number=SR, article="26")
    assert [s["doc_type"] for s in out["sources"]] == ["botschaft"]
    assert [d["doc_type"] for d in out["draft_texts"]] == ["erlassentwurf"]
    assert "botschaft_candidates" not in out
    assert "Botschaft des Bundesrates" in out["_hint"] and "BBl 2023 679" in out["_hint"]


def test_hint_names_the_types_returned(db):
    db.doc(2024, 50, "stellungnahme_bundesrat", "Stellungnahme zum Kommissionsbericht",
           link=["5"], paras=[(3, "5", "Der Bundesrat beantragt, auf die Vorlage einzutreten.")])
    out = m._handle_get_article_purpose(sr_number=SR, article="5")
    assert "Stellungnahme des Bundesrates" in out["_hint"]
    assert "Botschaft des Bundesrates" not in out["_hint"]


def test_full_text_fallback_never_returns_a_draft(db):
    db.doc(2023, 680, "erlassentwurf", DRAFT_TITLE,
           paras=[(1, None, "Änderung (SR 172.023) und Art. 26 im Entwurf des Gesetzes.")])
    db.doc(2023, 679, title=BOT_TITLE,
           paras=[(40, None, "Die Botschaft zu (SR 172.023) erläutert Art. 26 des Gesetzes.")])
    out = m._handle_get_article_purpose(sr_number=SR, article="26")
    assert [s["doc_type"] for s in out["sources"]] == ["botschaft"]
    assert out["sources"][0]["paragraphs"][0]["printed_page"] == 40


# ── pages ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("year,start,page,collection,printed", [
    (2006, 7001, 77, None, 7077),      # the BBl ran through the year
    (2000, 1, 5, None, 5),
    (2020, 8000, 1, None, 8000),
    (2021, 44, 5, None, 5),            # own pagination since 2021
    (2023, 679, 26, None, 26),
    (1999, 4000, 5, None, None),       # mixed, not mapped
    (1975, 609, 5, m._BAR_COLLECTION, None),  # scan, no reliable running head
    (2023, 679, None, None, None),     # Akoma Ntoso: no pages
])
def test_printed_page(year, start, page, collection, printed):
    row = {"bbl_year": year, "bbl_page": start, "source_collection": collection}
    assert m._bbl_printed_page(row, page) == printed


def test_answer_offers_the_printed_page_and_says_null_means_no_pinpoint(db):
    db.doc(2006, 7001, title="Botschaft zum Bundesgerichtsgesetz", link=["72"],
           paras=[(77, "72", "Artikel 72 regelt die Beschwerde in Zivilsachen.")])
    out = m._handle_get_article_purpose(sr_number=SR, article="72")
    [para] = out["sources"][0]["paragraphs"]
    assert (para["page"], para["printed_page"]) == (77, 7077)
    assert "S. {printed_page}" in out["_hint"] and "S. {page}" not in out["_hint"]
    assert "printed_page null" in out["_hint"]


def test_search_botschaft_carries_the_printed_page(db):
    db.doc(2006, 7001, title="Botschaft zum Bundesgerichtsgesetz", link=["72"],
           paras=[(77, "72", "Artikel 72 regelt die Beschwerde in Zivilsachen.")])
    [hit] = m._handle_search_botschaft(query="Beschwerde")["results"]
    assert hit["page"] == 77 and hit["printed_page"] == 7077


# ── pointers ──────────────────────────────────────────────────────────────


def test_pointer_leaves_out_drafts(db):
    _bekj(db)
    conn = m._get_materialien_conn()
    assert m._verbatim_botschaft_pointer(conn, SR, "26") is None
    db.doc(2023, 800, title="Botschaft zur Revision", link=["26"],
           paras=[(1, "26", "Die Revision betrifft die Nichterreichbarkeit der Plattform.")])
    conn = m._get_materialien_conn()
    ptr = m._verbatim_botschaft_pointer(conn, SR, "26")
    assert ptr["bbl_citations"] == ["BBl 2023 800"]
    assert "Botschaft paragraphs" in ptr["note"]


def test_pointer_to_a_report_does_not_say_botschaft(db):
    db.doc(2024, 50, "bericht_kommission", "Bericht der Kommission", link=["5"],
           paras=[(3, "5", "Die Kommission beantragt eine neue Bestimmung.")])
    ptr = m._verbatim_botschaft_pointer(m._get_materialien_conn(), SR, "5")
    assert "Botschaft paragraphs" not in ptr["note"]
    assert "Bericht" in ptr["note"]


def test_history_counts_only_botschaften_as_botschaften(db, monkeypatch):
    _bekj(db)
    monkeypatch.setattr(m, "_get_legislation_local", lambda **k: None)
    out = m._handle_get_article_history(sr_number=SR, article="26")
    assert out["summary"]["botschaft_count"] == 0
    assert out["summary"]["bundesblatt_document_count"] == 1


# ── get_materialien ───────────────────────────────────────────────────────


def test_note_does_not_call_a_draft_a_federal_council_message(db):
    _bekj(db)
    out = m.get_materialien("BEKJ")
    assert [d["doc_type"] for d in out["botschaft_documents"]] == ["erlassentwurf"]
    assert "Federal Council messages" not in out["note"]
    assert "No Botschaft is linked" in out["note"]
    assert out["botschaft_candidates"][0]["bbl_citation"] == "BBl 2023 679"


def test_one_long_message_does_not_hide_the_older_ones(db):
    db.doc(2020, 100, title="Ältere Botschaft", link=["1"], paras=[(1, "1", "Text zu Artikel 1 des Gesetzes.")])
    db.doc(2024, 500, title="Lange Botschaft", link=[str(i) for i in range(1, 400)],
           paras=[(1, "1", "Text zu Artikel 1 des Gesetzes.")])
    docs = m._linked_botschaft_documents(m._get_materialien_conn(), SR, None)
    assert [d["bbl_citation"] for d in docs] == ["BBl 2024 500", "BBl 2020 100"]
    assert len(docs[0]["articles"]) == 399


def test_law_level_list_says_when_it_is_cut(db):
    for page in range(1, 26):
        db.doc(2010, page * 10, title=f"Botschaft {page}", link=["1"],
               paras=[(1, "1", "Text zu Artikel 1 des Gesetzes.")])
    out = m.get_materialien("BEKJ")
    assert len(out["botschaft_documents"]) == 20
    assert out["botschaft_documents_total"] == 25
    assert "20 newest of 25" in out["note"]


def test_language_editions_stay_one_message(db):
    db.doc(2022, 2743, title="Botschaft", link=["1"], paras=[(1, "1", "Text zu Artikel 1.")])
    db.doc(2022, 2743, title="Message", lang="fr", link=["1"], paras=[(1, "1", "Texte de l'article 1.")])
    [doc] = m._linked_botschaft_documents(m._get_materialien_conn(), SR, None)
    assert doc["language"] in ("de", "fr") and len(doc["other_languages"]) == 1


# ── consultations named by the act ────────────────────────────────────────

NEW_ACT = f"{FEDLEX}/eli/dl/proj/6020/67/cons_1"
ORDINANCE = f"{FEDLEX}/eli/dl/proj/2024/79/cons_1"
LINKED = f"{FEDLEX}/eli/dl/proj/2022/1/cons_1"


def _procedure(conn, uri, title, description=None, start="2020-11-11", sr=None):
    conn.execute(
        "INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
        "description_de, start_date, discovered_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (uri, uri.rsplit("proj/", 1)[1], int(start[:4]), title, description, start,
         "2026-10-19", "2026-10-19"))
    if sr:
        conn.execute("INSERT INTO consultation_laws VALUES (?,?,?)", (uri, uri + "/work", sr))


def _report(conn, uri, paragraphs):
    doc = uri + "/doc_1"
    conn.execute(
        "INSERT INTO consultation_documents (document_uri, procedure_uri, doc_type_code, "
        "doc_type) VALUES (?,?,12,'erlaeuternder_bericht')", (doc, uri))
    fid = conn.execute(
        "INSERT INTO consultation_files (document_uri, language, file_url, text_policy, status) "
        "VALUES (?,'de','https://x/f.pdf','text','done')", (doc,)).lastrowid
    for order, (section, anchor, text) in enumerate(paragraphs, 1):
        conn.execute(
            "INSERT INTO consultation_paragraphs (file_id, para_order, page_number, section_path, "
            "article_anchor, text, text_length) VALUES (?,?,?,?,?,?,?)",
            (fid, order, 37, section, anchor, text, len(text)))


@pytest.fixture
def cons(tmp_path, monkeypatch):
    laws = [{"sr_number": SR, "title_de": "Bundesgesetz vom 20. Dezember 2024 über die Plattformen",
             "abbr_de": "BEKJ", "abbr_fr": "LPCJ", "abbr_it": "LCEG"},
            {"sr_number": "700.1", "abbr_de": "RPV"}, {"sr_number": "1.1", "abbr_de": "XG"},
            {"sr_number": "1.2", "abbr_de": "ZWEI"}, {"sr_number": "1.3", "abbr_de": "ZWEI"}]
    path = tmp_path / "statutes.db"
    st = sqlite3.connect(path)
    make_statutes_conn([], laws=laws).backup(st)
    st.close()
    monkeypatch.setattr(m, "STATUTES_DB_PATH", path)
    conn = bcc.connect_work_db(tmp_path / "work.db")
    yield conn
    conn.close()


def test_new_act_and_its_ordinance_are_found_by_name(cons):
    _procedure(cons, NEW_ACT, "Bundesgesetz über die Plattform in der Justiz (BEKJ)")
    _procedure(cons, ORDINANCE, "Verordnung über die elektronische Kommunikation (VEKJ)",
               "Ersterlass der Verordnung zum Bundesgesetz (BEKJ; SR 173.023).", start="2026-03-13")
    _procedure(cons, LINKED, "Änderung des Gesetzes", start="2022-01-01", sr=SR)
    out = {v["procedure_uri"]: v for v in m._consultations_for_sr(cons, SR, None, law_code="BEKJ")}
    assert set(out) == {NEW_ACT, ORDINANCE, LINKED}
    assert out[NEW_ACT]["link_basis"] == "abbreviation_in_title"
    assert out[ORDINANCE]["link_basis"] == "abbreviation_in_description"
    assert "not a Fedlex link" in out[ORDINANCE]["link_note"]
    assert "link_basis" not in out[LINKED]


def test_procedure_with_an_sr_link_is_not_matched_by_name(cons):
    _procedure(cons, NEW_ACT, "Anpassung an das Gesetz (BEKJ)", sr="700.1")
    assert m._consultations_for_sr(cons, SR, None, law_code="BEKJ") == []


@pytest.mark.parametrize("title,sr", [
    ("Bundesgesetz (BEKJA)", SR),            # no substring match
    ("BEKJ ohne Klammern", SR),
    ("Loi fédérale (LPCJ)", SR),             # French abbreviations collide with other acts
    ("Gesetz (XG)", "1.1"),                  # too short
    ("Gesetz (ZWEI)", "1.2"),                # two acts share it
])
def test_weak_names_are_not_used(cons, title, sr):
    _procedure(cons, NEW_ACT, title)
    assert m._consultations_for_sr(cons, sr, None) == []


def test_passage_of_a_named_procedure_needs_its_own_heading_to_name_the_act(cons):
    _procedure(cons, ORDINANCE, "Verordnung (VEKJ)", "Verordnung zum Gesetz (BEKJ; SR 173.023).")
    _report(cons, ORDINANCE, [
        ("3 Erläuterungen zu den einzelnen Artikeln", "26", "Art. 26 Aufbewahrung der Logdaten"),
        ("3 Erläuterungen zu den einzelnen Artikeln", "27", "Art. 27 Inkrafttreten"),
        ("5 Erläuterungen zu Artikel 26 Absatz 5 BEKJ", "26",
         "Artikel 26 Absatz 5 BEKJ wurde während der parlamentarischen Beratungen eingefügt."),
    ])
    [entry] = m._consultations_for_sr(cons, SR, "26", law_code="BEKJ")
    [passage] = entry["erlaeuternder_bericht_passages"]
    assert passage["text"].startswith("Artikel 26 Absatz 5 BEKJ")


def test_same_number_in_another_act_of_a_named_procedure_is_dropped(cons):
    _procedure(cons, NEW_ACT, "Bundesgesetz (BEKJ)")
    _report(cons, NEW_ACT, [
        ("3.2.1 Verwaltungsverfahrensgesetz (VwVG)", "26", "Artikel 26 Absatz 1 VwVG"),
    ])
    [entry] = m._consultations_for_sr(cons, SR, "26", law_code="BEKJ")
    assert "erlaeuternder_bericht_passages" not in entry


# ── not yet in force ──────────────────────────────────────────────────────


@pytest.mark.parametrize("note", [
    "Wird zu einem späteren Zeitpunkt in Kraft gesetzt.",
    "Tritt zu einem späteren Zeitpunkt in Kraft (AS 2022 600).",
    "Noch nicht in Kraft.",
    "Nicht in Kraft gesetzt.",
    "Entre en vigueur ultérieurement.",
    "Pas encore en vigueur.",
    "Entra in vigore in un secondo tempo.",
    "Non ancora in vigore.",
])
def test_not_in_force_notes_are_recognised(note):
    empty = {"text": "", "footnote": note}
    live = {"text": "1 Der Bundesrat regelt die Einzelheiten.", "footnote": note}
    m._label_statute_stubs([empty, live])
    assert empty["in_force_note"].startswith("Not in force")
    assert "part or all" in live["in_force_note"]


def test_other_notes_are_not_labelled():
    art = {"text": "", "footnote": "Aufgehoben durch Ziff. I des BG vom 7. Okt. 1994, "
                                   "in Kraft seit 1. Jan. 1996."}
    m._label_statute_stubs([art])
    assert "in_force_note" not in art


@pytest.fixture
def law(monkeypatch, tmp_path):
    rows = [
        {"sr_number": SR, "article_num": "17", "text": "Der Austritt ist möglich."},
        {"sr_number": SR, "article_num": "28", "text": "",
         "footnote": "Wird zu einem späteren Zeitpunkt in Kraft gesetzt."},
        {"sr_number": "210", "article_num": "1", "text": "1 Das Gesetz findet Anwendung."},
    ]

    def conn():
        c = make_statutes_conn(rows)
        c.row_factory = sqlite3.Row
        return c

    fake = tmp_path / "statutes.db"
    fake.touch()
    monkeypatch.setattr(m, "STATUTES_DB_PATH", fake)
    monkeypatch.setattr(m, "_get_statutes_conn", conn)
    monkeypatch.setattr(m, "_statute_text_cache", {})
    monkeypatch.setattr(m, "_FEDLEX_WORK_URI_MAP", None)
    monkeypatch.setattr(m, "_fetch_pending_changes", lambda sr: [])
    monkeypatch.setattr(m, "_get_materialien_for_doctrine", lambda *a, **k: None, raising=False)


def test_missing_article_of_a_partly_enacted_act_is_explained(law):
    res = m.get_law(sr_number=SR, article="26")
    assert res["articles"] == []
    assert "not yet in force" in res["article_note"]
    assert "Art. 26" in m._format_get_law_response(res)
    assert "not yet in force" in m._format_get_law_response(res)


def test_missing_article_of_another_act_gets_no_such_note(law):
    assert "article_note" not in m.get_law(sr_number="210", article="999")


def test_placeholder_row_is_marked_not_in_force(law):
    [art] = m.get_law(sr_number=SR, article="28")["articles"]
    assert art["text"] == "" and art["in_force_note"].startswith("Not in force")
