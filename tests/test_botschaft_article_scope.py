"""A Botschaft's article comments must reach the right article of the right act.

Found 2026-11-02 on BBl 2023 679, the BEKJ Botschaft: 592 paragraphs, no anchor,
no section, no link. Fedlex marks its comments up as plain paragraphs under a
numbered <level>, which the parser did not read, and footnotes were spliced into
the sentences. Meanwhile an <article> anchored everything after it to the end of
the document. With anchors in place, link enrichment crossed every anchor with
every act of the bill, so the comment on Art. 26 VwVG in the annex section would
have linked to Art. 26 BEKJ.

Offline: fixture XML shaped like the real files, tmp SQLite.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import enrich_botschaft_links as ebl  # noqa: E402
import reparse_xml_materialien as rx  # noqa: E402
import mcp_server as m  # noqa: E402
from search_stack import article_scope as scope  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402
from search_stack.build_botschaft_corpus import parse_akoma_ntoso_xml as parse  # noqa: E402

AKN = b'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'
BEKJ_NAMES = ({"BEKJ", "LPCJ", "LCEG"},
              {"Bundesgesetz über die Plattformen für die elektronische Kommunikation in der Justiz"})


def doc(body: str) -> bytes:
    return (b"<?xml version='1.0' encoding='UTF-8'?><akomaNtoso " + AKN + b"><act><body>"
            + body.encode() + b"</body></act></akomaNtoso>")


BEKJ_BOTSCHAFT = doc("""
<level eId="lvl_4"><num>4</num><heading>Grundzüge der Vorlage</heading>
  <content><p>Die Vorlage regelt die elektronische Kommunikation in der Justiz.</p></content>
</level>
<level eId="lvl_5"><num>5</num><heading>Erläuterungen zu einzelnen Artikeln</heading>
 <level eId="lvl_5/lvl_5.1"><num>5.1</num>
  <heading>Bundesgesetz über die Plattformen für die elektronische Kommunikation in der Justiz</heading>
  <content>
   <p>Art. 26 Nichterreichbarkeit einer Plattform</p>
   <p>Abs. 1 und 2</p>
   <p>Ist die Plattform am Tag des Fristablaufs nicht erreichbar<authorialNote marker="9"><p>Vgl. Art. 44 ZPO und die Ausführungen dazu.</p></authorialNote>, verlängert sich die Frist.</p>
   <p>Art. 27 Datenschutz</p>
   <p>Die Bestimmung verweist auf das Datenschutzgesetz des Bundes.</p>
  </content>
 </level>
 <level eId="lvl_5/lvl_5.2"><num>5.2</num><heading>Änderung anderer Bundesgesetze</heading>
  <level eId="x"><num>5.2.2</num>
   <heading>Verwaltungsverfahrensgesetz vom 20. Dezember 1968<authorialNote marker="10"><p>SR 172.021</p></authorialNote></heading>
   <content>
    <p>Art. 26 Abs. 1 Einleitungssatz und 1<sup>bis</sup></p>
    <p>Die Akten im Verwaltungsverfahren werden elektronisch geführt und weitergegeben.</p>
   </content>
  </level>
 </level>
</level>
<level eId="lvl_6"><num>6</num><heading>Auswirkungen</heading>
 <content><p>Die Auswirkungen auf den Bund sind in Ziffer 4.2 beschrieben worden.</p></content>
</level>
""")


# ── parser ────────────────────────────────────────────────────────────────


def test_plain_article_headings_anchor_their_comment():
    rows = list(parse(BEKJ_BOTSCHAFT))
    run = [r["text"] for r in rows if r["article_anchor"] == "26"
           and r["section_path"].endswith("in der Justiz")]
    assert run[0] == "Art. 26 Nichterreichbarkeit einer Plattform"
    assert run[1] == "Abs. 1 und 2"  # a short subheading is part of the comment
    assert run[2].startswith("Ist die Plattform")


def test_anchor_stops_at_the_next_outline_level():
    rows = list(parse(BEKJ_BOTSCHAFT))
    tail = next(r for r in rows if r["text"].startswith("Die Auswirkungen"))
    assert tail["article_anchor"] is None
    assert tail["section_path"] == "6 Auswirkungen"


def test_section_path_is_the_whole_heading_path_with_the_sr_of_the_footnote():
    rows = list(parse(BEKJ_BOTSCHAFT))
    row = next(r for r in rows if r["text"].startswith("Die Akten"))
    assert row["section_path"] == (
        "5 Erläuterungen zu einzelnen Artikeln > 5.2 Änderung anderer Bundesgesetze > "
        "5.2.2 Verwaltungsverfahrensgesetz vom 20. Dezember 1968 [SR 172.021]")


def test_footnote_leaves_the_sentence_and_stays_searchable_on_its_own():
    rows = list(parse(BEKJ_BOTSCHAFT))
    sentence = next(r for r in rows if r["text"].startswith("Ist die Plattform"))
    assert sentence["text"] == ("Ist die Plattform am Tag des Fristablaufs nicht erreichbar, "
                                "verlängert sich die Frist.")
    note = next(r for r in rows if r["text"].startswith("Vgl. Art. 44"))
    assert note["article_anchor"] is None


def test_short_note_and_note_on_an_article_number_are_kept():
    rows = list(parse(doc(
        '<article eId="art_8"><num>Art. 8<authorialNote marker="1"><p>Die endgültige Nummerierung '
        'legt die Bundeskanzlei fest.</p></authorialNote></num><heading>Gleichstellung</heading>'
        '<paragraph eId="p"><num>1</num><content><p>Das Gesetz sorgt für die Gleichstellung'
        '<authorialNote marker="2"><p>BBl 2018 6949</p></authorialNote>.</p></content></paragraph>'
        "</article>")))
    texts = [r["text"] for r in rows]
    assert "Art. 8 Gleichstellung" in texts
    assert "1 Das Gesetz sorgt für die Gleichstellung." in texts
    assert "BBl 2018 6949" in texts
    assert any(t.startswith("Die endgültige Nummerierung") for t in texts)


def test_text_keeps_reading_order_around_inline_elements():
    """The old extractor put a tail before its element's children:
    'BBl , 2018 6949 7321' for 'BBl 2018 6949, 7321'."""
    rows = list(parse(doc(
        "<level><num>1</num><heading>Grundlagen</heading><content>"
        '<p>Vgl. die Botschaft, BBl <ref href="x"><i>2018</i> 6949</ref>, 7321 und weitere Stellen.</p>'
        "</content></level>")))
    assert rows[0]["text"] == "Vgl. die Botschaft, BBl 2018 6949, 7321 und weitere Stellen."


def test_superscript_stays_part_of_the_word():
    rows = list(parse(BEKJ_BOTSCHAFT))
    assert any(r["text"] == "Art. 26 Abs. 1 Einleitungssatz und 1bis" for r in rows)


def test_article_element_anchors_only_itself():
    """BBl 2026 1177: one <article> anchored the other 242 paragraphs."""
    rows = list(parse(doc("""
      <level><num>5</num><heading>Erläuterungen</heading><content>
        <p>Vorbemerkung zu den einzelnen Bestimmungen der Vorlage.</p></content>
        <article eId="art_9"><num>Art. 9</num><heading>Abs. 2 Bst. a</heading>
          <content><p>Seit 2011 definiert das Gesetz die systemrelevanten Banken.</p></content>
        </article>
        <content><p>Weitere Bemerkungen ohne Bezug zu einem bestimmten Artikel.</p></content>
      </level>""")))
    by = {r["text"][:20]: r["article_anchor"] for r in rows}
    assert by["Art. 9 Abs. 2 Bst. a"] == "9"
    assert by["Seit 2011 definiert "] == "9"
    assert by["Weitere Bemerkungen "] is None


def test_numbered_paragraph_is_one_row_not_two():
    rows = list(parse(doc("""
      <article eId="art_26"><num>Art. 26</num><heading>Nichterreichbarkeit</heading>
       <paragraph eId="p1"><num>1</num><content><p>Ist eine Plattform nicht erreichbar, so verlängert sich die Frist.</p></content></paragraph>
      </article>""")))
    assert [r["text"] for r in rows] == [
        "Art. 26 Nichterreichbarkeit",
        "1 Ist eine Plattform nicht erreichbar, so verlängert sich die Frist."]


@pytest.mark.parametrize("eid,number", [
    ("art_5", "5"), ("art_26_a", "26a"), ("art_12_quater", "12quater"), ("art_41a", "41a"),
])
def test_article_numbers_from_eid(eid, number):
    rows = list(parse(doc(f'<article eId="{eid}"><p>Text der Bestimmung, lang genug zum Testen.</p></article>')))
    assert rows[0]["article_anchor"] == number


def test_range_eid_anchors_nothing():
    rows = list(parse(doc('<article eId="art_18_24"><p>Text der Bestimmung, lang genug zum Testen.</p></article>')))
    assert rows[0]["article_anchor"] is None


@pytest.mark.parametrize("text,number", [
    ("Art. 26 Nichterreichbarkeit einer Plattform", "26"),
    ("Art. 5 Abs. 2 Bst. a", "5"),
    ("Art. 3 Abs. 2 zweiter Satz", "3"),
    ("Art. 57a Politische Partizipation", "57a"),
    ("Art. 3 Kantonsgebiet [Marginalie]", "3"),
    ("Art. 93 Marginalie und Abs. 4 und 5", "93"),
    ("Artikel 5 Absatz 6", "5"),
    ("Art. 12", "12"),
    ("Zu Art. 7", "7"),
    ("Art. 26 Inaccessibilité d'une plateforme", "26"),
    ("Art. 26, al. 1", "26"),
    ("Art. 72, par. 1ter (nouveau)", "72"),
    ("Art. 9, 9a, 9b, 10 et 37b", "9"),
    ("Art. 13 à 20", "13"),
    ("Art. 32–34", "32"),
    ("Art. 26 cpv. 1", "26"),
    ("Artikel 7 Absatz 2: Der Bundesrat kann für die Nutzung Mindestanforderungen festlegen.", "7"),
    ("Artikel 30: Der Bundesrat bestimmt die Aufsichtsbehörde.", "30"),
    ("Artikel 86 des Bundesgesetzes vom 4. Oktober 1991 über das bäuerliche Bodenrecht;", None),
    ("Artikel 65 Absatz 1 BV gibt dem Bund die Kompetenz, die notwendigen statistischen "
     "Daten zu erheben, und der Bund nimmt diese Aufgabe seit langem wahr und entwickelt sie weiter.", None),
    ("Artikel 1 umschreibt den Gegenstand des Gesetzesentwurfs.", None),
    ("Der Artikel 5 bleibt unverändert und wird hier nicht weiter erläutert.", None),
])
def test_article_heading_shapes(text, number):
    assert bbc._akn_article_heading(text) == number


def test_emphasised_lead_opens_a_comment():
    rows = list(parse(doc(
        "<level><num>3</num><heading>Abkommen</heading><content>"
        "<p><i>Artikel 4</i> über die Dokumentenkontrolle sieht vor, dass die Behörde prüft.</p>"
        "<p><i>Das Abkommen</i> wurde nach langen Verhandlungen im Jahr 2024 unterzeichnet.</p>"
        "</content></level>")))
    assert [r["article_anchor"] for r in rows] == ["4", "4"]


def test_outline_level_can_be_the_article():
    rows = list(parse(doc(
        "<level><num>5.1</num><heading>Art. 3 Zweck</heading><content>"
        "<p>Die Bestimmung umschreibt den Zweck des Gesetzes ausführlich.</p>"
        "</content></level>")))
    assert rows[0]["article_anchor"] == "3"


def test_footnote_paragraph_is_never_a_heading():
    rows = list(parse(doc(
        "<level><num>1</num><heading>Grundlagen</heading><content>"
        '<p>Die Vorlage stützt sich auf das Gesetz<authorialNote marker="1"><p>Art. 8 Space Act.</p>'
        "</authorialNote> und auf weitere Grundlagen im Verordnungsrecht.</p>"
        "<p>Der nächste Absatz gehört zu keinem Artikel der Vorlage.</p>"
        "</content></level>")))
    assert {r["article_anchor"] for r in rows} == {None}


# ── scope ─────────────────────────────────────────────────────────────────


def _about(head, section, several=False, sr="172.023", names=BEKJ_NAMES):
    return scope._heading_is_about_act(head, section, sr, names[0], names[1], several)


def test_section_decides_between_acts():
    annex = "5 Erläuterungen > 5.2 Änderung > 5.2.2 Verwaltungsverfahrensgesetz vom 20. Dezember 1968 [SR 172.021]"
    main = ("5 Erläuterungen > 5.1 Bundesgesetz über die Plattformen für die elektronische "
            "Kommunikation in der Justiz")
    assert _about("Art. 26 Nichterreichbarkeit", main)
    assert not _about("Art. 26 Abs. 1 Einleitungssatz", annex)


def test_sr_tag_for_this_act_counts():
    assert _about("Art. 1", "5 > 5.3 Gesetz [SR 172.023]", several=True)


def test_heading_line_naming_another_act_loses():
    assert not _about("Art. 5 Abs. 1 VwVG", "5 Erläuterungen zu einzelnen Artikeln")


def test_heading_line_naming_this_act_wins_with_a_draft_prefix():
    assert _about("Art. 5 Abs. 1 E-BEKJ", "5.2 Zivilprozessordnung")


def test_no_verdict_counts_only_for_a_document_on_one_act():
    assert _about("Art. 3 Zweck", "5 Erläuterungen zu einzelnen Artikeln")
    assert not _about("Art. 3 Zweck", "5 Erläuterungen zu einzelnen Artikeln", several=True)


def _run(number, title="", section="5 Erläuterungen zu einzelnen Artikeln"):
    return {"article_anchor": number, "section_path": section,
            "text": f"Art. {number} {title}".strip()}


def _place(rows, enacted, several=False):
    return scope.place_runs(rows, "172.023", BEKJ_NAMES[0], BEKJ_NAMES[1], several, enacted)


def test_same_title_keeps_the_number():
    assert _place([_run("5", "Zweck")], {"5": "Zweck"}) == {"5": "5"}


def test_renumbered_article_goes_to_the_enacted_number():
    """TJPG: Art. 40 of the Botschaft is enacted Art. 33."""
    enacted = {"33": "Prüfung der Meldungen", "40": "Eintragung im Grundbuch"}
    rows = [_run("40", "Prüfung der Meldungen"), _run("47", "Eintragung im Grundbuch")]
    assert _place(rows, enacted) == {"33": "40", "40": "47"}


def test_inserted_article_shifts_only_what_follows():
    """BEKJ: Parliament added Art. 37, draft Art. 37 became Art. 38. Art. 26 is
    a placeholder in the consolidation, so nothing can be compared; its
    neighbours kept their numbers, so it keeps its own."""
    enacted = {"25": "Kosten", "27": "Datenschutz", "37": "Übergangsbestimmungen",
               "38": "Referendum und Inkrafttreten"}
    rows = [_run("25", "Kosten"), _run("26", "Nichterreichbarkeit einer Plattform"),
            _run("27", "Datenschutz"), _run("37", "Referendum und Inkrafttreten")]
    assert _place(rows, enacted) == {"25": "25", "26": "26", "27": "27", "38": "37"}


def test_unplaced_comment_in_a_shifted_stretch_gets_no_link():
    enacted = {"33": "Prüfung", "34": "Anderes", "36": "Dritte Sache", "49": "Übergang"}
    rows = [_run("40", "Prüfung"), _run("41", "Amtshilfe"), _run("43", "Dritte Sache")]
    assert _place(rows, enacted) == {"33": "40", "36": "43"}


def test_untitled_comment_with_no_shift_keeps_its_number():
    assert _place([_run("5", "Abs. 2")], {"5": "Zweck"}) == {"5": "5"}


def test_reworded_title_counts_when_close():
    assert _place([_run("5", "Regulierungskostenschätzungen")],
                  {"5": "Regulierungskostenschätzung"}) == {"5": "5"}


def test_title_shared_by_several_articles_moves_nothing():
    enacted = {"7": "Begriffe", "9": "Begriffe"}
    assert _place([_run("3", "Begriffe")], enacted) == {"3": "3"}


def test_title_decides_where_no_heading_does():
    """SIAG in BBl 2024 2359: a treaty and an act, one "Erläuterungen" section."""
    assert _place([_run("10", "Beschaffung von Informationen"), _run("11", "Unbekannt")],
                  {"10": "Beschaffung von Informationen"}, several=True) == {"10": "10"}


def test_title_never_overrides_a_section_about_another_act():
    section = "5.2 Zivilprozessordnung [SR 272]"
    assert _place([_run("2", "Begriffe", section)], {"2": "Begriffe"}) == {}


def test_shortened_title_in_a_heading_names_the_act():
    names = {"Bundesgesetz über die Ziele im Klimaschutz, die Innovation und die Stärkung der "
             "Energiesicherheit"}
    assert scope._passage_names_act("1 Bundesgesetz über die Ziele im Klimaschutz", None, {"KlG"}, names)


def test_runs_split_on_section_and_number():
    rows = [{"article_anchor": a, "section_path": s}
            for a, s in [("26", "x"), ("26", "x"), ("27", "x"), ("26", "y")]]
    assert [len(r) for r in scope.anchor_runs(rows)] == [2, 1, 1]


# ── links + read side ─────────────────────────────────────────────────────


def _db(path, xml_bytes=BEKJ_BOTSCHAFT, doc_type="botschaft"):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE amendment_refs (sr_number TEXT, article TEXT, ref_type TEXT,
            year INTEGER, page INTEGER, fedlex_url TEXT, context TEXT);
    """)
    bbc.ensure_schema(conn)
    bbc.migrate_additive(conn)
    bid = conn.execute(
        "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, source_url, "
        "format, language, ingested_at, doc_type, title) VALUES (2023, 679, 'BBl 2023 679', ?, ?, "
        "'akoma-ntoso-xml', 'de', '2026-01-01', ?, 'Botschaft')",
        ("https://fedlex.data.admin.ch/eli/fga/2023/679",
         "https://fedlex.data.admin.ch/filestore/x.xml", doc_type)).lastrowid
    rx.replace_paragraphs(conn, bid, list(parse(xml_bytes)), xml_bytes)
    conn.commit()
    return conn, bid


def _statutes(path, enacted=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE laws (sr_number TEXT, title_de TEXT, title_fr TEXT, title_it TEXT, "
                 "abbr_de TEXT, abbr_fr TEXT, abbr_it TEXT, consolidation_date TEXT, work_uri TEXT)")
    conn.executemany("INSERT INTO laws VALUES (?,?,?,?,?,?,?,?,?)", [
        ("172.023", "Bundesgesetz vom 20. Dezember 2024 über die Plattformen für die elektronische "
         "Kommunikation in der Justiz (BEKJ)", None, None, "BEKJ", "LPCJ", "LCEG", None,
         "https://fedlex.data.admin.ch/eli/cc/2025/583"),
        ("172.021", "Bundesgesetz über das Verwaltungsverfahren", None, None, "VwVG", "PA", "PA", None,
         "https://fedlex.data.admin.ch/eli/cc/1969/737")])
    conn.execute("CREATE TABLE articles (sr_number TEXT, lang TEXT, article_num TEXT, heading TEXT, "
                 "section TEXT DEFAULT '')")
    conn.executemany("INSERT INTO articles (sr_number, lang, article_num, heading) VALUES (?,?,?,?)",
                     [("172.023", "de", n, h) for n, h in enacted])
    conn.commit(); conn.close()


def _enrich(monkeypatch, db, statutes, pairs):
    monkeypatch.setattr(ebl, "fetch_all_botschaft_cc_pairs", lambda types: pairs)
    monkeypatch.setattr(sys, "argv", ["enrich", "--db", str(db), "--statutes", str(statutes),
                                      "--no-historical"])
    assert ebl.main() == 0


CHAIN = [("https://fedlex.data.admin.ch/eli/fga/2023/679", "https://fedlex.data.admin.ch/eli/cc/2025/583")]


@pytest.fixture
def linked(tmp_path, monkeypatch):
    db, st = tmp_path / "materialien.db", tmp_path / "statutes.db"
    conn, bid = _db(db)
    conn.close()
    _statutes(st)
    _enrich(monkeypatch, db, st, CHAIN)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))
    monkeypatch.setattr(m, "MATERIALIEN_DB_PATH", db)
    monkeypatch.setattr(m, "STATUTES_DB_PATH", st)
    return db, st, bid


def test_links_exist_for_the_act_of_the_section(linked):
    db, _, bid = linked
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT sr_number, article, evidence FROM article_botschaft_links").fetchall()
    assert sorted(rows) == [("172.023", "26", "sparql_parliament_chain"),
                            ("172.023", "27", "sparql_parliament_chain")]


def test_run_for_another_act_is_not_a_link(tmp_path, monkeypatch):
    db, st = tmp_path / "materialien.db", tmp_path / "statutes.db"
    conn, _ = _db(db, doc("""
      <level><num>5.1</num><heading>Bundesgesetz über die Plattformen für die elektronische Kommunikation in der Justiz</heading>
        <content><p>Art. 1 Zweck und Gegenstand</p></content></level>
      <level><num>5.2</num><heading>Zivilprozessordnung<authorialNote><p>SR 272</p></authorialNote></heading>
        <content><p>Art. 2 Ausnahmen von der Zuständigkeit</p></content></level>"""))
    conn.close()
    _statutes(st)
    _enrich(monkeypatch, db, st, CHAIN)
    conn = sqlite3.connect(db)
    assert [r[0] for r in conn.execute("SELECT article FROM article_botschaft_links")] == ["1"]


def test_stale_chain_link_goes_other_evidence_stays(linked, monkeypatch):
    db, st, bid = linked
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO article_botschaft_links VALUES (?,?,?,?,?)", [
        ("172.023", "99", bid, "considered", "sparql_parliament_chain"),
        ("172.021", "26", bid, "considered", "amendment_refs:1")])
    conn.commit(); conn.close()
    _enrich(monkeypatch, db, st, CHAIN)
    conn = sqlite3.connect(db)
    kept = {r[:2] for r in conn.execute("SELECT sr_number, article FROM article_botschaft_links")}
    assert ("172.023", "99") not in kept and ("172.021", "26") in kept


def test_a_document_missing_from_the_chain_answer_keeps_its_links(linked, monkeypatch):
    db, st, _ = linked
    _enrich(monkeypatch, db, st, [])
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM article_botschaft_links").fetchone()[0] == 2


RENUMBERED = doc("""
  <level><num>5</num><heading>Erläuterungen zu einzelnen Artikeln</heading><content>
   <p>Art. 37 Referendum und Inkrafttreten</p>
   <p>Die Bestimmung regelt das Referendum und das Inkrafttreten des Gesetzes.</p>
  </content></level>""")


def test_renumbered_comment_is_served_under_the_enacted_number(tmp_path, monkeypatch):
    db, st = tmp_path / "m.db", tmp_path / "s.db"
    conn, _ = _db(db, RENUMBERED)
    conn.close()
    _statutes(st, enacted=[("37", "Übergangsbestimmungen"), ("38", "Referendum und Inkrafttreten")])
    _enrich(monkeypatch, db, st, CHAIN)
    rows = sqlite3.connect(db).execute("SELECT article, evidence FROM article_botschaft_links").fetchall()
    assert rows == [("38", "sparql_parliament_chain:draft_art=37")]
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))
    monkeypatch.setattr(m, "STATUTES_DB_PATH", st)
    out = m._handle_get_article_purpose(sr_number="172.023", article="38")
    [src] = out["sources"]
    assert src["draft_article"] == "37"
    assert src["paragraphs"][0]["text"] == "Art. 37 Referendum und Inkrafttreten"
    assert "renumbered" in out["_hint"]
    # the enacted Art. 37 is another provision: this comment must not answer it
    assert m._handle_get_article_purpose(sr_number="172.023", article="37")["sources"] == []


def test_link_with_a_draft_number_is_replaced_on_the_next_run(tmp_path, monkeypatch):
    db, st = tmp_path / "m.db", tmp_path / "s.db"
    conn, _ = _db(db, RENUMBERED)
    conn.close()
    _statutes(st, enacted=[("38", "Referendum und Inkrafttreten")])
    _enrich(monkeypatch, db, st, CHAIN)
    _enrich(monkeypatch, db, st, CHAIN)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM article_botschaft_links").fetchone()[0] == 1


def test_read_side_returns_only_the_requested_acts_comment(linked):
    db, st, bid = linked
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO article_botschaft_links VALUES ('172.021','26',?, 'considered','amendment_refs:1')", (bid,))
    conn.commit(); conn.close()
    out = m._handle_get_article_purpose(sr_number="172.023", article="26")
    texts = [p["text"] for p in out["sources"][0]["paragraphs"]]
    assert texts[0] == "Art. 26 Nichterreichbarkeit einer Plattform"
    assert not any("Verwaltungsverfahren" in t for t in texts)
    out = m._handle_get_article_purpose(sr_number="172.021", article="26")
    texts = [p["text"] for p in out["sources"][0]["paragraphs"]]
    assert texts[0].startswith("Art. 26 Abs. 1") and not any("Plattform" in t for t in texts)


def _pdf_doc(db, page=7001, section="2 Staatsverträge sind vorbehalten."):
    conn = sqlite3.connect(db)
    bid = conn.execute(
        "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, source_url, "
        "format, language, ingested_at, doc_type) VALUES (2006, ?, ?, ?, 'https://x', 'pdf', 'de', "
        "'2026-01-01', 'botschaft')",
        (page, f"BBl 2006 {page}", f"https://fedlex.data.admin.ch/eli/fga/2006/{page}")).lastrowid
    conn.execute(
        "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, section_path, "
        "article_anchor, text, text_length) VALUES (?, 1, 77, ?, '26', ?, 40)",
        (bid, section, "Art. 26 Der Kommentar im PDF zur Bestimmung."))
    conn.commit(); conn.close()
    return bid


def test_pdf_passage_is_never_filtered_on_its_section(linked):
    """A PDF section is any capitalised line. Filtering on it would have
    dropped ~3,500 links that return the right text (2026-11-02)."""
    db, _, _ = linked
    bid = _pdf_doc(db, section="4.1 Änderung des Zivilgesetzbuches vom 10. Dezember 1907")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO article_botschaft_links VALUES ('172.023','26',?, 'considered','x')", (bid,))
    conn.commit(); conn.close()
    out = m._handle_get_article_purpose(sr_number="172.023", article="26")
    pdf = next(s for s in out["sources"] if s["format"] == "pdf")
    assert pdf["paragraphs"][0]["printed_page"] == 7077


def test_no_verdict_keeps_a_footnote_link_on_the_read_side(tmp_path, monkeypatch):
    db, st = tmp_path / "m.db", tmp_path / "s.db"
    conn, bid = _db(db, doc("<level><num>5</num><heading>Erläuterungen zu einzelnen Artikeln</heading>"
                            "<content><p>Art. 3 Zweck und Gegenstand der Regelung</p></content></level>"))
    conn.execute("INSERT INTO article_botschaft_links VALUES ('172.023','3',?, 'considered','statute_footnote:x')", (bid,))
    conn.execute("INSERT INTO article_botschaft_links VALUES ('172.021','3',?, 'considered','statute_footnote:x')", (bid,))
    conn.commit(); conn.close()
    _statutes(st)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))
    monkeypatch.setattr(m, "STATUTES_DB_PATH", st)
    out = m._handle_get_article_purpose(sr_number="172.023", article="3")
    assert out["sources"][0]["paragraphs"]


def test_pdf_anchors_still_reach_every_act_of_the_bill(tmp_path, monkeypatch):
    db, st = tmp_path / "m.db", tmp_path / "s.db"
    conn, _ = _db(db)
    conn.close()
    _statutes(st)
    _pdf_doc(db)
    uri = "https://fedlex.data.admin.ch/eli/fga/2006/7001"
    _enrich(monkeypatch, db, st, [(uri, "https://fedlex.data.admin.ch/eli/cc/2025/583"),
                                  (uri, "https://fedlex.data.admin.ch/eli/cc/1969/737")])
    conn = sqlite3.connect(db)
    assert sorted(r[0] for r in conn.execute(
        "SELECT sr_number FROM article_botschaft_links WHERE article='26'")) == ["172.021", "172.023"]


# ── re-parse ──────────────────────────────────────────────────────────────


def test_reparse_replaces_rows_and_search_follows(tmp_path):
    conn, bid = _db(tmp_path / "m.db", doc("<p>Alter Text, der nach dem Lauf nicht mehr gefunden wird.</p>"))
    cache = tmp_path / "xml"
    cache.mkdir()
    (cache / f"{bid}.xml").write_bytes(BEKJ_BOTSCHAFT)
    stats = rx.reparse(conn, cache_dir=cache)
    assert stats["reparsed"] == 1 and stats["failed"] == 0
    hit = lambda q: conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs_fts "
                                 "WHERE botschaft_paragraphs_fts MATCH ?", (q,)).fetchone()[0]
    assert hit("Nichterreichbarkeit") >= 1 and hit("Alter") == 0


def test_reparse_dry_run_changes_nothing(tmp_path):
    conn, bid = _db(tmp_path / "m.db", doc("<p>Alter Text, der stehen bleiben muss im Trockenlauf.</p>"))
    cache = tmp_path / "xml"
    cache.mkdir()
    (cache / f"{bid}.xml").write_bytes(BEKJ_BOTSCHAFT)
    rx.reparse(conn, cache_dir=cache, dry_run=True)
    assert conn.execute("SELECT text FROM botschaft_paragraphs").fetchone()[0].startswith("Alter")


def test_reparse_keeps_a_document_whose_file_yields_nothing(tmp_path):
    conn, bid = _db(tmp_path / "m.db")
    before = conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0]
    stats = rx.reparse(conn, fetch=lambda url: b"<akomaNtoso><act><meta/></act></akomaNtoso>",
                       sleep=lambda s: None)
    assert stats["empty"] == 1
    assert conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] == before


def test_html_shell_is_refused(monkeypatch):
    class Shell:
        def read(self):
            return b"<html data-beasties-container><head><title>Casemates</title></head></html>"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(rx, "urlopen", lambda req, timeout=60: Shell())
    with pytest.raises(rx.NotXml):
        rx.fetch_xml("https://fedlex.data.admin.ch/filestore/x.xml")


def test_reparse_stops_after_repeated_failures(tmp_path):
    conn, _ = _db(tmp_path / "m.db")
    def boom(url):
        raise OSError("refused")
    for page in range(700, 708):
        conn.execute(
            "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, source_url, "
            "format, language, ingested_at) VALUES (2023, ?, ?, ?, 'https://x', 'akoma-ntoso-xml', 'de', 'x')",
            (page, f"BBl 2023 {page}", f"e{page}"))
    assert rx.reparse(conn, fetch=boom, sleep=lambda s: None)["stopped"] is True
