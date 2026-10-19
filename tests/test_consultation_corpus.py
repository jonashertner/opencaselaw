"""Vernehmlassung corpus from Fedlex (2026-10-19).

Pins search_stack/build_consultation_corpus.py and its server surface:

  * discovery from the Fedlex consultation graph (procedures, SR links,
    documents per language, Fedlex vocabulary labels), idempotent, with the
    text policy per document type and a replaced upload re-queued,
  * paragraph segmentation: running heads, page numbers, table-of-contents
    pages and footnote citations never become text or headings; numbered
    headings set section_path, "Art. N" headings set article_anchor; line
    breaks are joined with the conservative de-hyphenation,
  * the fetch policy: identical language copies stored once after a 256 KB
    prefix check, 404 → not_found, a corrupt file is final, 429/403 stop at
    once, N consecutive failures stop, time budget, the download cache,
  * publish: a checked snapshot replaced atomically through a symlink,
  * search_botschaft(doc_type=<Vernehmlassung type>) and the
    `vernehmlassungen` block of get_materialien.

Offline: fake SPARQL, a fake HTTP client and PDFs generated with PyMuPDF.
"""
from __future__ import annotations

import io
import sqlite3
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_consultation_corpus as bcc  # noqa: E402

fitz = pytest.importorskip("fitz")

V = "https://fedlex.data.admin.ch/vocabulary/"
P1 = "https://fedlex.data.admin.ch/eli/dl/proj/2023/45/cons_1"
P2 = "https://fedlex.data.admin.ch/eli/dl/proj/2023/46/cons_1"
FS = "https://fedlex.data.admin.ch/filestore/fedlex.data.admin.ch/eli/dl/proj/2023/45/cons_1"
LANG = {"de": "http://publications.europa.eu/resource/authority/language/DEU",
        "fr": "http://publications.europa.eu/resource/authority/language/FRA",
        "it": "http://publications.europa.eu/resource/authority/language/ITA"}


def b(**kw):
    """SPARQL binding row from keyword values (None = unbound)."""
    return {k: {"value": v} for k, v in kw.items() if v is not None}


def _doc_rows(file_suffix=""):
    rows = []
    # Erläuternder Bericht, three languages, pdf-a (+ a docx alternative in DE)
    for lg in ("de", "fr", "it"):
        rows.append(b(c=P1, doc=P1 + "/doc_10", dt=V + "draft-document-type/12",
                      dtitle={"de": "Erläuternder Bericht", "fr": "Rapport explicatif",
                              "it": "Rapporto esplicativo"}[lg], dtl=lg, lang=LANG[lg],
                      fname=f"EB_{lg}.pdf", fmt=V + "user-format/pdf-a",
                      file=f"{FS}/doc_10/{lg}/pdf-a/eb-{lg}{file_suffix}.pdf",
                      pd="2023-06-15T14:02:03Z"))
    rows.append(b(c=P1, doc=P1 + "/doc_10", dt=V + "draft-document-type/12",
                  lang=LANG["de"], fmt=V + "user-format/docx",
                  file=f"{FS}/doc_10/de/docx/eb-de.docx"))
    # Stellungnahmen: one file filed under DE and FR
    for lg in ("de", "fr"):
        rows.append(b(c=P1, doc=P1 + "/doc_13", dt=V + "draft-document-type/21",
                      lang=LANG[lg], fmt=V + "user-format/pdf-a",
                      file=f"{FS}/doc_13/{lg}/pdf-a/st-{lg}.pdf"))
    # Begleitschreiben: link only
    rows.append(b(c=P1, doc=P1 + "/doc_6", dt=V + "draft-document-type/13",
                  lang=LANG["de"], fmt=V + "user-format/pdf-a",
                  file=f"{FS}/doc_6/de/pdf-a/bs-de.pdf"))
    # Diverses only as .doc: unsupported format, kept with its link
    rows.append(b(c=P1, doc=P1 + "/doc_5", dt=V + "draft-document-type/15",
                  lang=LANG["de"], fmt=V + "user-format/doc",
                  file=f"{FS}/doc_5/de/doc/div-de.doc"))
    # a row for a procedure outside this year's set is ignored
    rows.append(b(c="https://fedlex.data.admin.ch/eli/dl/proj/1999/1/cons_1",
                  doc="x", dt=V + "draft-document-type/12"))
    return rows


class FakeSparql:
    def __init__(self, file_suffix=""):
        self.file_suffix = file_suffix
        self.queries: list[str] = []

    def __call__(self, q: str):
        self.queries.append(q)
        if "skos:prefLabel ?l BIND" in q:
            return [b(s=V + "draft-document-type/12", l="Erläuternder Bericht", lg="de"),
                    b(s=V + "draft-document-type/12", l="Rapport explicatif", lg="fr"),
                    b(s=V + "consultation-status/5", l="Abgeschlossen", lg="de"),
                    b(s=V + "consultation-status/5", l="Clôturée", lg="fr"),
                    b(s=V + "legal-institution/262", l="Bundesamt für Umwelt", lg="de")]
        if "GROUP BY ?y" in q:
            return [b(y="2023", n="2"), b(y="1980", n="1")]
        if "eventTitle" in q:
            return [b(c=P1, id="proj/2023/45/cons_1", status=V + "consultation-status/5",
                      title="Verordnungspaket Umwelt Frühling 2024", tl="de",
                      desc="Anpassung von Verordnungen"),
                    b(c=P1, id="proj/2023/45/cons_1", status=V + "consultation-status/5",
                      title="Paquet d'ordonnances environnementales", tl="fr"),
                    b(c=P2, id="proj/2023/46/cons_1", title="Ohne Dokumente", tl="de")]
        if "ConsultationPhase" in q:
            return [b(c=P1, start="2023-06-15", end="2023-10-06",
                      i1=V + "legal-institution/S1", i2=V + "legal-institution/262")]
        if "foreseenImpactToLegalResource" in q:
            return [b(c=P1, w="https://fedlex.data.admin.ch/eli/cc/1998/2261_2261_2261", sr="814.680"),
                    b(c=P1, w="https://fedlex.data.admin.ch/eli/oc/2024/1")]
        if "draftProcessDocumentType" in q:
            return _doc_rows(self.file_suffix)
        raise AssertionError("unexpected query: " + q[:120])


@pytest.fixture
def work(tmp_path):
    conn = bcc.connect_work_db(tmp_path / "work.db")
    yield conn
    conn.close()


def _discover(conn, sparql=None):
    return bcc.discover(conn, sparql=sparql or FakeSparql(), sleep=lambda s: None)


# ── discovery ─────────────────────────────────────────────────────────────


def test_discover_stores_procedures_laws_documents_and_policy(work):
    st = _discover(work)
    assert st["years"] == 1  # 1980 is below FIRST_YEAR
    p = work.execute("SELECT * FROM consultation_procedures WHERE procedure_uri=?", (P1,)).fetchone()
    assert p["event_id"] == "proj/2023/45/cons_1" and p["year"] == 2023
    assert p["title_de"].startswith("Verordnungspaket") and p["title_fr"].startswith("Paquet")
    assert (p["start_date"], p["end_date"]) == ("2023-06-15", "2023-10-06")
    # the most specific institution level wins
    assert p["institution_uri"] == V + "legal-institution/262"
    laws = dict(work.execute("SELECT work_uri, sr_number FROM consultation_laws").fetchall())
    assert laws["https://fedlex.data.admin.ch/eli/cc/1998/2261_2261_2261"] == "814.680"
    assert laws["https://fedlex.data.admin.ch/eli/oc/2024/1"] is None
    files = {(r["document_uri"].rsplit("/", 1)[1], r["language"]): r for r in
             work.execute("SELECT * FROM consultation_files")}
    eb = files[("doc_10", "de")]
    assert eb["format"] == "pdf-a" and eb["status"] == "pending" and eb["text_policy"] == "text"
    assert "docx" in eb["alt_files_json"]  # the alternative manifestation is kept
    assert files[("doc_6", "de")]["status"] == "link_only"
    assert files[("doc_5", "de")]["status"] == "unsupported_format"
    assert files[("doc_13", "fr")]["status"] == "pending"
    docs = dict(work.execute("SELECT document_uri, doc_type FROM consultation_documents").fetchall())
    assert docs[P1 + "/doc_10"] == "erlaeuternder_bericht"
    assert docs[P1 + "/doc_13"] == "stellungnahmen"
    assert "x" not in docs
    lab = work.execute("SELECT * FROM consultation_vocab WHERE uri=?",
                       (V + "draft-document-type/12",)).fetchone()
    assert (lab["label_de"], lab["label_fr"]) == ("Erläuternder Bericht", "Rapport explicatif")


def test_discover_is_idempotent_and_requeues_a_replaced_upload(work):
    _discover(work)
    n = work.execute("SELECT COUNT(*) FROM consultation_files").fetchone()[0]
    work.execute("UPDATE consultation_files SET status='done', sha256='x' WHERE language='fr'")
    work.commit()
    st = _discover(work)
    assert st["files_new"] == 0
    assert work.execute("SELECT COUNT(*) FROM consultation_files").fetchone()[0] == n
    assert work.execute("SELECT COUNT(*) FROM consultation_files WHERE status='done'").fetchone()[0] == 2
    st = _discover(work, FakeSparql(file_suffix="-v2"))
    assert st["files_requeued"] == 3  # the three Erläuternder-Bericht files changed URL
    fr = work.execute("SELECT * FROM consultation_files WHERE document_uri=? AND language='fr'",
                      (P1 + "/doc_10",)).fetchone()
    assert fr["status"] == "pending" and fr["sha256"] is None and fr["file_url"].endswith("-v2.pdf")


# ── segmentation ──────────────────────────────────────────────────────────


def test_segment_drops_running_heads_page_numbers_and_toc():
    head = "Verordnungspaket Umwelt\nErläuternder Bericht"
    pages = [
        [head, "1/4", "Inhaltsverzeichnis", "1 Ausgangslage 3", "2 Grundzüge der Vorlage 3",
         "3 Erläuterungen 4", "4 Auswirkungen 4", "5 Rechtliche Aspekte 4"],
        [head, "2/4", "Erläuternder Bericht\n1\nAusgangslage",
         "Die geltenden Regelungen gewähren den Kan-\ntonen keinen Spielraum, deshalb ist eine "
         "neue Bestimmung nötig."],
        [head, "3/4", "2.1 Inhalt des Verordnungstextes",
         "Der neue Artikel 18 Absatz 3 AltlV beinhaltet folgende Regelung für die Behörden."],
        [head, "4/4", "Art. 18 Abs. 3", "Die Behörde kann ausnahmsweise den Wiedereinbau erlauben.",
         "4 BBl 1990 II 38 5 BBl 2000 1865"],
    ]
    paras = list(bcc.segment_blocks(pages, "de"))
    texts = [p["text"] for p in paras]
    assert not any("Verordnungspaket" in t for t in texts)
    assert not any(t.strip() in ("1/4", "2/4") for t in texts)
    body = next(p for p in paras if p["text"].startswith("Die geltenden"))
    assert "Kantonen" in body["text"]  # de-hyphenated
    assert body["section_path"] == "1 Ausgangslage" and body["page_number"] == 2
    # the TOC page set no section
    assert all(p["section_path"] is None for p in paras if p["page_number"] == 1)
    regel = next(p for p in paras if p["text"].startswith("Der neue Artikel"))
    assert regel["section_path"] == "2.1 Inhalt des Verordnungstextes"
    beh = next(p for p in paras if p["text"].startswith("Die Behörde"))
    assert beh["article_anchor"] == "18"
    foot = next(p for p in paras if "BBl 1990" in p["text"])
    assert foot["section_path"] == "2.1 Inhalt des Verordnungstextes"  # not a heading


def test_segment_joins_table_cells_and_keeps_words():
    pages = [["Alter", "Männer", "Frauen", "Total",
              "Ein längerer Absatz, der nach der Tabelle steht und ganz bleibt."]]
    paras = list(bcc.segment_blocks(pages, "de", paginated=False))
    assert paras[0]["text"] == "Alter Männer Frauen Total"
    assert paras[0]["page_number"] is None
    assert paras[1]["text"].startswith("Ein längerer Absatz")


# ── fetch ─────────────────────────────────────────────────────────────────


def _pdf(paragraphs: list[str], pages: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for t in paragraphs:
            page.insert_textbox(fitz.Rect(72, y, 520, y + 90), t, fontsize=10)
            y += 100
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


LONG = ("Mit dieser Vorlage sollen die Hinterlassenenleistungen in erster Linie "
        "während der Erziehungszeit ausgerichtet werden, wie der Bundesrat es beantragt. ")


class FakeHttp:
    def __init__(self, files: dict[str, object]):
        self.files = files
        self.gets: list[str] = []
        self.heads: list[str] = []
        self.prefixes: list[str] = []
        self.last_from_cache = False

    def _data(self, url):
        v = self.files[url]
        if isinstance(v, Exception):
            raise v
        return v

    def head_length(self, url):
        self.heads.append(url)
        return len(self._data(url))

    def get_prefix(self, url, n):
        self.prefixes.append(url)
        return self._data(url)[:n]

    def get(self, url, max_bytes):
        self.gets.append(url)
        data = self._data(url)
        if len(data) > max_bytes:
            raise bcc._TooLarge(len(data))
        return data


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, None)


def _urls(conn, doc):
    return {r["language"]: r["file_url"] for r in conn.execute(
        "SELECT language, file_url FROM consultation_files WHERE document_uri=?", (P1 + doc,))}


def test_fetch_stores_text_and_dedupes_identical_language_copies(work):
    _discover(work)
    eb, st = _urls(work, "/doc_10"), _urls(work, "/doc_13")
    shared = _pdf([LONG * 3], pages=2)
    http = FakeHttp({eb["de"]: _pdf([LONG * 3]), eb["fr"]: _pdf([LONG * 2 + "fr"]),
                     eb["it"]: b"%PDF-1.7 garbage",
                     st["de"]: shared, st["fr"]: shared})
    stats = bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None)
    assert stats["stopped"] is None
    rows = {(r["document_uri"].rsplit("/", 1)[1], r["language"]): r for r in
            work.execute("SELECT * FROM consultation_files WHERE text_policy='text'")}
    assert rows[("doc_10", "de")]["status"] == "done"
    assert rows[("doc_10", "fr")]["status"] == "done"
    assert rows[("doc_10", "it")]["status"] == "parse_error"
    assert rows[("doc_13", "de")]["status"] == "done"
    assert rows[("doc_13", "fr")]["status"] == "same_as"
    assert rows[("doc_13", "fr")]["same_as_file_id"] == rows[("doc_13", "de")]["file_id"]
    assert st["fr"] not in http.gets and st["fr"] in http.prefixes
    n_fr = work.execute("SELECT COUNT(*) FROM consultation_paragraphs WHERE file_id=?",
                        (rows[("doc_13", "fr")]["file_id"],)).fetchone()[0]
    assert n_fr == 0
    hit = work.execute("SELECT rowid FROM consultation_paragraphs_fts WHERE "
                       "consultation_paragraphs_fts MATCH 'Erziehungszeit'").fetchall()
    assert hit
    # Erläuternde Berichte first (priority), Stellungnahmen last
    assert http.gets.index(eb["de"]) < http.gets.index(st["de"])
    # nothing left to fetch; a second run is a no-op
    assert bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None)["queued"] == 0


def test_fetch_404_is_final_and_429_stops_at_once(work):
    _discover(work)
    eb, st = _urls(work, "/doc_10"), _urls(work, "/doc_13")
    http = FakeHttp({eb["de"]: _http_error(eb["de"], 404),
                     eb["fr"]: _http_error(eb["fr"], 429),
                     eb["it"]: _pdf([LONG]), st["de"]: _pdf([LONG]), st["fr"]: _pdf([LONG])})
    stats = bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None)
    assert stats["stopped"] == "http_429"
    assert stats["processed"] == 2
    de = work.execute("SELECT status, http_status FROM consultation_files WHERE file_url=?",
                      (eb["de"],)).fetchone()
    assert tuple(de) == ("not_found", 404)
    fr = work.execute("SELECT status FROM consultation_files WHERE file_url=?", (eb["fr"],)).fetchone()
    assert fr[0] == "pending"  # untouched: the next run starts there


def test_fetch_stops_after_consecutive_failures_with_backoff(work):
    _discover(work)
    urls = [r[0] for r in work.execute(
        "SELECT file_url FROM consultation_files WHERE status='pending'")]
    http = FakeHttp({u: urllib.error.URLError("timed out") for u in urls})
    pauses = []
    stats = bcc.fetch(work, rate_limit=0.5, http=http, sleep=pauses.append,
                      max_consecutive_failures=3)
    assert stats["stopped"] == "3_consecutive_failures"
    assert pauses == [bcc.BACKOFF_BASE_SECONDS, bcc.BACKOFF_BASE_SECONDS * 2]
    errs = work.execute("SELECT COUNT(*) FROM consultation_files WHERE status='error' "
                        "AND attempts=1").fetchone()[0]
    assert errs == 3


def test_fetch_respects_time_budget(work):
    _discover(work)
    ticks = iter([0, 0, 5, 100, 100, 100, 100])
    http = FakeHttp({u: _pdf([LONG]) for (u,) in work.execute(
        "SELECT file_url FROM consultation_files WHERE status='pending'")})
    stats = bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None,
                      max_seconds=10, clock=lambda: next(ticks))
    assert stats["stopped"] == "max_seconds" and stats["processed"] == 2


def test_http_cache_serves_second_get_without_network(tmp_path, monkeypatch):
    h = bcc._Http(cache_dir=tmp_path / "cache")
    url = "https://fedlex.data.admin.ch/filestore/x.pdf"
    (tmp_path / "cache").mkdir(exist_ok=True)
    h._cache_path(url).write_bytes(b"cached")

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert h.get(url, 100) == b"cached" and h.last_from_cache


def test_off_fedlex_host_is_never_fetched(work):
    _discover(work)
    work.execute("UPDATE consultation_files SET file_url='https://evil.example/x.pdf' "
                 "WHERE language='de' AND document_uri=?", (P1 + "/doc_10",))
    work.commit()
    row = bcc._queue(work)[0]
    http = FakeHttp({})
    assert bcc.fetch_one(work, row, http) == "unsupported_format"
    assert http.gets == []


# ── publish ───────────────────────────────────────────────────────────────


def test_publish_replaces_through_a_symlink(work, tmp_path):
    _discover(work)
    vol = tmp_path / "volume"
    vol.mkdir()
    real = vol / "consultations.db"
    real.write_bytes(b"old")
    link = tmp_path / "consultations.db"
    link.symlink_to(real)
    out = bcc.publish(work, link)
    assert out == Path(real).resolve()
    assert link.is_symlink()
    c = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    assert c.execute("SELECT COUNT(*) FROM consultation_procedures").fetchone()[0] == 2
    c.close()
    assert not (vol / "consultations.db.tmp").exists()


def test_publish_refuses_an_empty_corpus(work, tmp_path):
    with pytest.raises(RuntimeError):
        bcc.publish(work, tmp_path / "consultations.db")
    assert not (tmp_path / "consultations.db").exists()


# ── server ────────────────────────────────────────────────────────────────


@pytest.fixture
def served(work, tmp_path, monkeypatch):
    _discover(work)
    eb, st = _urls(work, "/doc_10"), _urls(work, "/doc_13")
    art = _pdf(["Art. 18 Abs. 3", "Die Behörde kann den Wiedereinbau von belastetem "
                "Aushubmaterial ausnahmsweise erlauben, wenn die Umwelt gesamthaft weniger "
                "belastet wird und keine schädlichen Einwirkungen zu erwarten sind.", LONG])
    http = FakeHttp({eb["de"]: art, eb["fr"]: _pdf(["Rapport " + LONG * 2]),
                     eb["it"]: _pdf(["Rapporto " + LONG * 2]),
                     st["de"]: _pdf([LONG * 2]), st["fr"]: _pdf([LONG * 2])})
    bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None)
    out = bcc.publish(work, tmp_path / "consultations.db")
    monkeypatch.setenv("SWISS_CASELAW_CONSULTATIONS_DB", str(out))
    import mcp_server
    return mcp_server


def test_search_botschaft_vernehmlassung_type(served):
    out = served._handle_search_botschaft(query="Aushubmaterial",
                                          doc_type="erlaeuternder_bericht")
    assert out["doc_type_filter"] == "erlaeuternder_bericht"
    assert out["total"] == 1
    hit = out["results"][0]
    assert hit["source"] == "fedlex_vernehmlassung"
    assert hit["doc_type_label"] == "Erläuternder Bericht"
    assert hit["procedure_title"].startswith("Verordnungspaket")
    assert hit["procedure_uri"] == P1 and hit["procedure_id"] == "proj/2023/45/cons_1"
    assert hit["consultation_deadline"] == "2023-10-06"
    assert hit["institution"] == "Bundesamt für Umwelt" and hit["status"] == "Abgeschlossen"
    assert hit["article_anchor"] == "18" and hit["page"] == 1
    assert "Aushubmaterial" in hit["text"] and "<<<" not in hit["text"]
    assert hit["file_url"].startswith("https://fedlex.data.admin.ch/filestore/")
    # French label and title in a French hit
    fr = served._handle_search_botschaft(query="Rapport", doc_type="erlaeuternder_bericht",
                                         language="fr")
    assert fr["results"][0]["doc_type_label"] == "Rapport explicatif"
    assert fr["results"][0]["procedure_title"].startswith("Paquet")
    # an unknown type names the whole list
    err = served._handle_search_botschaft(query="x", doc_type="nope")
    assert "erlaeuternder_bericht" in err["error"] and "botschaft" in err["error"]


def test_search_botschaft_relaxes_to_or_in_vernehmlassungen(served):
    out = served._handle_search_botschaft(query="Aushubmaterial Weltraumbahnhof",
                                          doc_type="erlaeuternder_bericht")
    assert out["total"] == 1 and out.get("filters_relaxed") is True


def test_search_botschaft_without_consultations_db(monkeypatch, tmp_path):
    monkeypatch.setenv("SWISS_CASELAW_CONSULTATIONS_DB", str(tmp_path / "missing.db"))
    import mcp_server
    out = mcp_server._handle_search_botschaft(query="Aushub", doc_type="ergebnisbericht")
    assert out["total"] == 0 and "not available" in out["_hint"]


def test_consultations_for_sr_lists_documents_and_article_passages(served):
    conn = served._get_consultations_conn()
    try:
        procs = served._consultations_for_sr(conn, "814.680", "18")
        assert served._consultations_for_sr(conn, "999.9", None) == []
    finally:
        conn.close()
    assert len(procs) == 1
    p = procs[0]
    assert p["procedure_title"].startswith("Verordnungspaket")
    types = {d["doc_type"]: d for d in p["documents"]}
    assert types["erlaeuternder_bericht"]["text_searchable"] is True
    assert types["begleitschreiben"]["text_searchable"] is False
    assert {f["language"] for f in types["erlaeuternder_bericht"]["files"]} == {"de", "fr", "it"}
    assert types["stellungnahmen"]["text_searchable"] is True  # FR copy is same_as
    passages = p["erlaeuternder_bericht_passages"]
    assert passages and all(x["language"] == "de" for x in passages)
    assert any("Wiedereinbau" in x["text"] for x in passages)


def test_doc_type_labels_match_fedlex_vocabulary(served):
    # The server's static labels are Fedlex's skos:prefLabel (R3).
    conn = served._get_consultations_conn()
    try:
        row = conn.execute("SELECT label_de, label_fr FROM consultation_vocab WHERE uri=?",
                           (V + "draft-document-type/12",)).fetchone()
    finally:
        conn.close()
    lab = served._CONSULTATION_DOC_TYPE_LABELS["erlaeuternder_bericht"]
    assert (lab["de"], lab["fr"]) == (row["label_de"], row["label_fr"])
    assert set(served._CONSULTATION_DOC_TYPES) == {bcc.DOC_TYPES[c] for c in bcc.TEXT_TYPES}
    assert set(served._CONSULTATION_DOC_TYPE_LABELS) == set(bcc.DOC_TYPES.values())


def test_publish_due_only_when_missing_or_stale(work, tmp_path):
    _discover(work)
    out = tmp_path / "consultations.db"
    assert bcc.publish_due(work, out)            # missing
    bcc.publish(work, out)
    assert not bcc.publish_due(work, out)        # fresh
    work.execute("UPDATE meta SET value='2020-01-01T00:00:00+00:00' WHERE key='published_at'")
    work.commit()
    assert bcc.publish_due(work, out)            # a week old


def test_consultation_search_gives_up_at_its_deadline(served, monkeypatch):
    monkeypatch.setattr(served, "_CONSULTATION_PROGRESS_STEPS", 1)
    conn = served._get_consultations_conn()
    try:
        hits = served._search_consultations(
            conn, "Aushubmaterial", language=None, year_min=None, year_max=None,
            doc_type=None, limit=5, deadline_s=1e-9)
        assert hits == []  # interrupted, not raised
        # and the handler is cleared: the next call on the connection answers
        again = served._search_consultations(
            conn, "Aushubmaterial", language=None, year_min=None, year_max=None,
            doc_type=None, limit=5)
        assert again
    finally:
        conn.close()
