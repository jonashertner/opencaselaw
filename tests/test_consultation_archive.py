"""Vernehmlassungs-Materialien outside Fedlex (2026-10-20).

Pins search_stack/consultation_archive.py against golden excerpts of the
real pages (tests/fixtures/consultation_archive/, captured 2026-10-20):

  * the Federal Chancellery's index ind<YEAR>.html in both layouts — the
    2006+ one linking gg/pc/documents/<id>/<Name>_<d|f|i>.pdf under
    "Eröffnung"/"Ergebnis", and the pre-2006 one linking the offices' sites —
    with deadlines, departments, the page's own labels and no Bundesblatt
    links; DE/FR/IT entries merged into one procedure,
  * the live bj.admin.ch project pages (download items under their heading,
    the Nuxt page record's translation slugs, the Vernehmlassung opening
    date) and the old BJ archive layout,
  * matching to Fedlex (deadline/opening date + title, a Fedlex link, an
    ambiguous pair attached to neither) and storing with provenance on every
    file row, files Fedlex already has skipped, idempotent,
  * the Wayback client (>= 2 s per host, 429 stops, 5xx retried, cache) and
    the fetch side (id_ download URL, per-host pacing and stop),
  * the server surface: an archive document shows the source's own label,
    its original URL and the page that linked it.

Offline: a fake opener serves the fixtures and CDX answers.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_consultation_corpus as bcc  # noqa: E402
from search_stack import consultation_archive as ca  # noqa: E402
from search_stack.build_botschaft_corpus import SourceUnavailable  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "consultation_archive"
PC = {"de": ("pc_ind2004_d_20160414232001.html", "20160414232001",
             "https://www.admin.ch/ch/d/gg/pc/ind2004.html"),
      "fr": ("pc_ind2004_f_20110514063603.html", "20110514063603",
             "http://www.admin.ch/ch/f/gg/pc/ind2004.html"),
      "it": ("pc_ind2004_i_20110514065421.html", "20110514065421",
             "http://www.admin.ch/ch/i/gg/pc/ind2004.html")}
OLD_2003 = ("pc_ind2003_d_20050205083311.html", "20050205083311",
            "http://www.admin.ch:80/ch/d/gg/pc/ind2003.html")


def page(name: str) -> str:
    return ca.decode_html((FIX / name).read_bytes())


def pc_entries(lang: str):
    name, _, url = PC[lang]
    return ca.parse_pc_index(page(name), lang=lang, year=2004, base_url=url)


# ── the Chancellery's index ──────────────────────────────────────────────


def test_pc_index_2006_layout_entries_labels_and_types():
    es = pc_entries("de")
    assert [e.department for e in es] == ["BK", "BK", "BK", "EJPD"]
    e = es[0]
    assert e.title.startswith("Teilrevision des Regierungs- und Verwaltungsorganisationsgesetzes")
    assert e.deadline == "2004-04-30"
    # the page's own words, in order; Bundesblatt links (BBl 2004 477, the
    # Botschaft, the Referendumsvorlage) are not documents here
    assert [lk.label for lk in e.links] == ["Vorlage", "Bericht", "Ergebnis: Bericht"]
    assert [lk.group for lk in e.links] == ["Eröffnung", "Eröffnung", "Ergebnis"]
    assert e.links[0].url == "https://www.admin.ch/ch/d/gg/pc/documents/1105/Vorlage_d.pdf"
    assert [ca.classify(lk.group, lk.text, lk.url.rsplit("/", 1)[-1]) for lk in e.links] \
        == [11, 12, 31]
    assert es[2].links == [] and es[2].deadline == "2004-05-31"


def test_pc_index_french_page_has_opening_date_and_canonical_department():
    es = pc_entries("fr")
    assert [e.department for e in es] == ["BK", "BK", "BK", "EJPD"]  # CHF / DFJP on the page
    assert es[0].opened == "2004-01-28" and es[0].deadline == "2004-04-30"
    assert [lk.label for lk in es[0].links] == ["Projet", "Rapport", "Résultat: Rapport"]


def test_pc_index_pre_2006_layout_links_the_office_site():
    name, _, url = OLD_2003
    es = ca.parse_pc_index(page(name), lang="de", year=2003, base_url=url)
    e = es[0]
    assert (e.opened, e.deadline) == ("2003-01-15", "2003-04-30")
    assert [(lk.label, lk.group) for lk in e.links] == [("Ergebnisse", None)]
    assert e.links[0].url == "http://www.admin.ch/ch/d/bk/recht/publikationsrecht/Ergebnisbericht.pdf"
    assert ca.classify(None, "Ergebnisse", "Ergebnisbericht.pdf") == 31
    # an HTML result page is counted, not ingested
    assert es[1].links == [] and es[1].other_links == 1


def test_pc_index_1995_mostly_without_files():
    es = ca.parse_pc_index(page("pc_ind1995_d_20210127212004.html"), lang="de", year=1995,
                           base_url="http://www.admin.ch/ch/d/gg/pc/ind1995.html")
    assert [len(e.links) for e in es] == [2, 0, 0]
    assert [lk.label for lk in es[0].links] == ["Vorlage und Bericht", "Ergebnisbericht"]
    assert [ca.classify(lk.group, lk.text) for lk in es[0].links] == [12, 31]
    assert all(e.deadline for e in es)


def test_document_key_joins_language_versions():
    k = ca.pc_document_key
    assert k("http://www.admin.ch/ch/d/gg/pc/documents/1105/Bericht_d.pdf") == ("1105/Bericht", "de")
    assert k("http://www.admin.ch/ch/f/gg/pc/documents/1105/Bericht_f.pdf") == ("1105/Bericht", "fr")
    # one PDF in three languages: no language from the name
    assert k("http://www.admin.ch/ch/d/gg/pc/documents/1206/Ergebnisse_d_f_i.pdf") \
        == ("1206/Ergebnisse_d_f_i", None)
    assert k("http://www.admin.ch/ch/f/bk/recht/x/Ergebnisbericht.pdf") \
        == ("www.admin.ch/ch/x/bk/recht/x/Ergebnisbericht", None)


def test_merge_aligns_de_fr_it_and_the_old_layout():
    procs = ca.merge_pc_languages(2004, {lg: pc_entries(lg) for lg in ca.LANGS})
    assert len(procs) == 4
    assert all(set(p.entries) == {"de", "fr", "it"} for p in procs)
    p = procs[0]
    assert p.title("fr").startswith("Révision partielle de la loi sur l'organisation")
    assert p.key() == "1105" and p.deadline == "2004-04-30"
    # an entry without documents aligns by department + deadline
    assert procs[2].title("it").startswith("Convenzione concernente la collaborazione")


def test_merge_never_aligns_by_position():
    """1998: the DE page lists AHV 11 before BVG 1, the FR page the other way
    round, same department and deadline. Each entry is matched alone (its own
    language's title) and grouped by the procedure it matched."""
    E = ca.PcEntry
    de = [E("de", 1998, "EDI", "Bundesgesetz über die Alters- und Hinterlassenenversicherung, "
            "11. Revision", None, "1998-11-30", ordinal=1),
          E("de", 1998, "EDI", "Bundesgesetz über die berufliche Alters-, Hinterlassenen- und "
            "Invalidenvorsorge, 1. Revision", None, "1998-11-30", ordinal=2)]
    fr = [E("fr", 1998, "EDI", "Loi fédérale sur la prévoyance professionnelle vieillesse, "
            "survivants et invalidité, 1ère révision", None, "1998-11-30", ordinal=1),
          E("fr", 1998, "EDI", "Loi fédérale sur l'assurance vieillesse et survivants, "
            "11e révision", None, "1998-11-30", ordinal=2)]
    cands = [dict(_cand("AHV", de[0].title, end="1998-11-30", year=1998),
                  title_fr=fr[1].title),
             dict(_cand("BVG", de[1].title, end="1998-11-30", year=1998),
                  title_fr=fr[0].title)]

    def match(e):
        return ca.match_procedure({e.lang: e.title}, year=e.year, opened=None,
                                  deadline=e.deadline, candidates=cands)
    procs = ca.merge_pc_languages(1998, {"de": de, "fr": fr}, match=match)
    assert len(procs) == 2
    got = {p.match_key: (p.title("de"), p.title("fr")) for p in procs}
    assert got["AHV"] == (de[0].title, fr[1].title)
    assert got["BVG"] == (de[1].title, fr[0].title)
    assert all(p.match().method == "deadline_title" for p in procs)


def test_classify_document_types():
    c = ca.classify
    assert c("Vernehmlassungsverfahren", "Vorentwurf", "entw-zpo") == 11
    assert c("Vernehmlassungsverfahren", "Begleitbericht für eine Schweizerische ZPO", "vn-ber") == 12
    assert c("Vernehmlassungsergebnisse", "Zusammenstellung der Vernehmlassungen", "ve-ber") == 21
    assert c("Vernehmlassungsergebnisse", "Bericht über das Ergebnis des "
             "Vernehmlassungsverfahrens", "ve-ber") == 31
    assert c("Ergebnis", "Bericht", "Protokoll_Kant_d.pdf") == 15
    assert c("Dokumentation", "Bericht der Expertenkommission", "ber-ek") == 12
    assert c(None, "Gutachten", "gutachten") == 15


# ── matching ─────────────────────────────────────────────────────────────


def _cand(uri, title, start=None, end=None, year=2004, event_id=None):
    return {"procedure_uri": uri, "event_id": event_id or uri, "year": year,
            "title_de": title, "title_fr": None, "title_it": None,
            "start_date": start, "end_date": end, "source": None}


def test_match_by_deadline_and_title():
    cands = [_cand("A", "Teilrevision des Regierungs- und Verwaltungsorganisationsgesetzes (RVOG)",
                   "2004-01-28", "2004-04-30"),
             _cand("B", "Verordnung über die Krankenversicherung", "2004-01-28", "2004-04-30")]
    m = ca.match_procedure({"de": "Teilrevision des Regierungs- und Verwaltungsorganisations"
                                  "gesetzes (RVOG): Genehmigung kantonaler Erlasse"},
                           year=2004, opened=None, deadline="2004-04-30", candidates=cands)
    assert (m.procedure_uri, m.method) == ("A", "deadline_title")


def test_match_rejects_unrelated_title_with_the_same_deadline():
    cands = [_cand("B", "Teilrevision der Verordnung über die Integration von AusländerInnen",
                   end="2004-04-30")]
    m = ca.match_procedure({"de": "Verordnung über die Krankenversicherung"}, year=2004,
                           opened=None, deadline="2004-04-30", candidates=cands)
    assert m.procedure_uri is None and m.method == "unmatched"


def test_match_title_only_must_not_contradict_the_deadline():
    t = "Bundesgesetz über die Familienzulagen"
    ok = ca.match_procedure({"de": t}, year=1995, opened=None, deadline=None,
                            candidates=[_cand("A", t, year=1995, end="1995-10-31")])
    assert (ok.procedure_uri, ok.method) == ("A", "title")
    bad = ca.match_procedure({"de": t}, year=1995, opened=None, deadline="1995-10-31",
                             candidates=[_cand("A", t, year=1995, end="1997-03-31")])
    assert bad.procedure_uri is None


def test_match_ambiguous_attaches_to_neither():
    t = "Änderung der Verordnung über den Strassenverkehr"
    m = ca.match_procedure({"de": t}, year=2004, opened=None, deadline="2004-06-30",
                           candidates=[_cand("A", t, end="2004-06-30"),
                                       _cand("B", t, end="2004-06-30")])
    assert m.procedure_uri is None and m.method == "ambiguous"


def test_match_a_fedlex_link_wins_and_containment_with_the_opening_date():
    cands = [_cand("Z", "Vereinheitlichung des Zivilprozessrechts (Schaffung einer "
                   "gesamtschweizerischen Zivilprozessordnung)", "2003-06-25", "2003-12-31",
                   year=2002, event_id="proj/2002/17/cons_1"),
             _cand("E", "Vorentwurf für eine Revision des ZGB (Erwachsenenschutz)",
                   "2003-06-25", "2004-01-15", year=2003)]
    m = ca.match_procedure({"de": "Vereinheitlichung des Zivilprozessrechts"}, year=2003,
                           opened="2003-06-25", deadline=None, candidates=cands)
    assert (m.procedure_uri, m.method) == ("Z", "opened_title")
    m = ca.match_procedure({"de": "anything"}, year=None, opened=None, deadline=None,
                           candidates=cands, fedlex_event_ids=["proj/2003/137/cons_1",
                                                               "proj/2002/17/cons_1"][1:])
    assert (m.procedure_uri, m.method) == ("Z", "fedlex_link")


# ── the Wayback client ───────────────────────────────────────────────────


class FakeWeb:
    """opener(url) -> bytes over fixtures and canned CDX answers."""

    def __init__(self, pages: dict[str, bytes], cdx: dict[str, list[list[str]]]):
        self.pages, self.cdx, self.calls = pages, cdx, []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url.startswith(ca.CDX):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            rows = self.cdx.get(q["url"][0], [])
            return json.dumps([list(ca.CDX_FIELDS)] + rows).encode()
        v = self.pages.get(url)
        if v is None:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if isinstance(v, Exception):
            raise v
        return v


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s

    def __call__(self):
        return self.t


def test_archive_http_paces_per_host_retries_5xx_and_stops_on_429(tmp_path):
    clock = Clock()
    web = FakeWeb({"https://web.archive.org/a": b"1", "https://web.archive.org/b": b"2",
                   "https://www.bj.admin.ch/c": b"3"}, {})
    h = ca.ArchiveHttp(opener=web, sleep=clock.sleep, clock=clock)
    assert h.get("https://web.archive.org/a") == b"1"
    assert h.get("https://www.bj.admin.ch/c") == b"3"   # other host: no wait
    assert h.get("https://web.archive.org/b") == b"2"
    assert clock.sleeps == [2.0]
    flaky = iter([urllib.error.HTTPError("u", 503, "x", {}, None), b"ok"])

    def opener(url):
        v = next(flaky)
        if isinstance(v, Exception):
            raise v
        return v
    h2 = ca.ArchiveHttp(opener=opener, sleep=clock.sleep, clock=clock)
    assert h2.get("https://web.archive.org/x") == b"ok"
    h3 = ca.ArchiveHttp(opener=FakeWeb({"https://web.archive.org/z": urllib.error.HTTPError(
        "u", 429, "x", {}, None)}, {}), sleep=clock.sleep, clock=clock)
    with pytest.raises(SourceUnavailable):
        h3.get("https://web.archive.org/z")
    with pytest.raises(ca.NotFound):
        h3.get("https://web.archive.org/missing")
    # the cache answers a second time without the network
    h4 = ca.ArchiveHttp(cache_dir=tmp_path, opener=web, sleep=clock.sleep, clock=clock)
    h4.get("https://web.archive.org/a")
    n = len(web.calls)
    h5 = ca.ArchiveHttp(cache_dir=tmp_path, opener=lambda u: 1 / 0)
    assert h5.get("https://web.archive.org/a") == b"1" and len(web.calls) == n


def test_cdx_query_follows_the_resume_key():
    answers = iter([
        json.dumps([list(ca.CDX_FIELDS), ["k1", "20100101000000", "http://a/1.pdf",
                                           "application/pdf", "200", "d"], [], ["RESUME"]]),
        json.dumps([list(ca.CDX_FIELDS), ["k2", "20110101000000", "http://a/2.pdf",
                                           "application/pdf", "200", "d"]]),
    ])
    seen = []

    def opener(url):
        seen.append(url)
        return next(answers).encode()
    rows = ca.cdx_query(ca.ArchiveHttp(opener=opener, intervals={}), "a/", match_type="prefix")
    assert [r["original"] for r in rows] == ["http://a/1.pdf", "http://a/2.pdf"]
    assert "resumeKey=RESUME" in seen[1]


# ── end to end: admin-pc into the work DB ────────────────────────────────


def _cdx_row(original, ts, mime="text/html"):
    return [ca.norm_url_key(original), ts, original, mime, "200", "D"]


def _pc_web():
    pages, cdx = {}, {}
    for lang, (name, ts, orig) in PC.items():
        pages[f"https://web.archive.org/web/{ts}id_/{orig}"] = (FIX / name).read_bytes()
        cdx[ca.pc_index_url(lang, 2004).split("://", 1)[1]] = [_cdx_row(orig, ts)]
    # 1105: Vorlage + Bericht archived in DE and FR, the Ergebnisbericht only in DE
    docs = []
    for lang, d in (("de", "d"), ("fr", "f")):
        for name in ("Vorlage", "Bericht"):
            docs.append(_cdx_row(f"http://www.admin.ch:80/ch/{d}/gg/pc/documents/1105/"
                                 f"{name}_{d}.pdf", "20070101000000", "application/pdf"))
    docs.append(_cdx_row("http://www.admin.ch/ch/d/gg/pc/documents/1105/Ergebnisbericht_d.pdf",
                         "20080101000000", "application/pdf"))
    for d in ("d", "f", "i"):
        cdx[f"www.admin.ch/ch/{d}/gg/pc/documents/"] = [
            r for r in docs if f"/ch/{d}/" in r[2]]
    return FakeWeb(pages, cdx)


@pytest.fixture
def work(tmp_path):
    conn = bcc.connect_work_db(tmp_path / "work.db")
    now = "2026-10-20T00:00:00+00:00"
    F = "https://fedlex.data.admin.ch/eli/dl/proj/"
    for uri, eid, title, start, end in [
            (F + "2004/7/cons_1", "proj/2004/7/cons_1",
             "Teilrevision des Regierungs- und Verwaltungsorganisationsgesetzes (RVOG): "
             "Genehmigung kantonaler Erlasse, Information über Verträge der Kantone unter sich "
             "oder mit dem Ausland", "2004-01-28", "2004-04-30"),
            (F + "2004/103/cons_1", "proj/2004/103/cons_1",
             "Bundesgesetz über die Einführung der allgemeinen Volksinitiative und Revision der "
             "Bundesgesetzgebung über die politischen Rechte", "2004-11-17", "2005-02-28")]:
        conn.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
                     "start_date, end_date, discovered_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (uri, eid, 2004, title, start, end, now, now))
    # Fedlex already holds the Ergebnisbericht of 2004/103 in German
    conn.execute("INSERT INTO consultation_documents (document_uri, procedure_uri, doc_type_code, "
                 "doc_type) VALUES (?, ?, 31, 'ergebnisbericht')",
                 (F + "2004/103/cons_1/doc_1", F + "2004/103/cons_1"))
    conn.execute("INSERT INTO consultation_files (document_uri, language, file_url, text_policy, "
                 "status) VALUES (?, 'de', 'https://fedlex.data.admin.ch/filestore/x.pdf', "
                 "'text', 'done')", (F + "2004/103/cons_1/doc_1",))
    conn.commit()
    yield conn
    conn.close()


def _discover_pc(conn, web=None):
    web = web or _pc_web()
    http = ca.ArchiveHttp(opener=web, intervals={})
    return ca.discover(conn, [ca.SOURCE_ADMIN_PC], http, years=[2004]), web


def test_admin_pc_discovery_attaches_documents_with_provenance(work):
    report, web = _discover_pc(work)
    st = report[ca.SOURCE_ADMIN_PC]
    assert st["entries"] == 4
    assert st["procedures_matched"] == 2 and st["procedures_created"] == 2
    assert st["files_already_held"] == 1   # the Fedlex Ergebnisbericht of 2004/103
    rows = {(r["file_name"], r["language"]): r for r in work.execute(
        "SELECT f.*, d.procedure_uri, d.doc_type, d.title_de, d.title_fr FROM consultation_files f "
        "JOIN consultation_documents d USING(document_uri) WHERE f.source=?",
        (ca.SOURCE_ADMIN_PC,))}
    v = rows[("Vorlage_d.pdf", "de")]
    assert v["procedure_uri"].endswith("proj/2004/7/cons_1")
    assert v["status"] == "pending" and v["doc_type"] == "vernehmlassungsvorlage"
    assert v["file_url"] == ("https://web.archive.org/web/20070101000000/"
                             "http://www.admin.ch:80/ch/d/gg/pc/documents/1105/Vorlage_d.pdf")
    assert v["original_url"] == "https://www.admin.ch/ch/d/gg/pc/documents/1105/Vorlage_d.pdf"
    assert v["source_page_url"] == ("https://web.archive.org/web/20160414232001/"
                                    "https://www.admin.ch/ch/d/gg/pc/ind2004.html")
    assert (v["title_de"], v["title_fr"]) == ("Vorlage", "Projet")
    # linked, never captured
    assert rows[("Vorlage_i.pdf", "it")]["status"] == "not_archived"
    assert rows[("Vorlage_i.pdf", "it")]["file_url"] is None
    assert rows[("Ergebnisbericht_f.pdf", "fr")]["status"] == "not_archived"
    # the trilingual result of 2004/103: German held by Fedlex, so only FR/IT
    # pages could add it, and it is one URL: never stored twice
    assert not [k for k, r in rows.items() if k == ("Ergebnisse_d_f_i.pdf", "de")
                and r["procedure_uri"].endswith("proj/2004/103/cons_1")]
    e = {r["match_method"]: r for r in work.execute(
        "SELECT * FROM consultation_archive_entries")}
    assert {"deadline_title", "created"} <= set(e)
    created = work.execute("SELECT * FROM consultation_procedures WHERE source=?",
                           (ca.SOURCE_ADMIN_PC,)).fetchall()
    assert len(created) == 2
    assert all(r["procedure_uri"].startswith("urn:opencaselaw:admin_pc_wayback:2004/")
               for r in created)
    assert {r["end_date"] for r in created} == {"2004-05-31", "2004-04-30"}
    # polite: index pages and one prefix query per language, nothing else
    assert sum(u.startswith(ca.CDX) for u in web.calls) == 6
    assert len(web.calls) == 9


def test_admin_pc_discovery_is_idempotent(work):
    _discover_pc(work)
    n = work.execute("SELECT COUNT(*) FROM consultation_files").fetchone()[0]
    work.execute("UPDATE consultation_files SET status='done' WHERE file_name='Bericht_d.pdf'")
    work.commit()
    report, _ = _discover_pc(work)
    assert report[ca.SOURCE_ADMIN_PC]["files_new"] == 0
    assert work.execute("SELECT COUNT(*) FROM consultation_files").fetchone()[0] == n
    assert work.execute("SELECT status FROM consultation_files WHERE file_name='Bericht_d.pdf'"
                        ).fetchone()[0] == "done"
    assert work.execute("SELECT COUNT(*) FROM consultation_procedures WHERE source IS NOT NULL"
                        ).fetchone()[0] == 2


def test_census_counts_per_source(work):
    _discover_pc(work)
    c = ca.census(work)[ca.SOURCE_ADMIN_PC]
    assert c["entries"] == 4 and c["entries_by_match"]["created"] == 2
    assert c["files_by_status"]["pending"] >= 5 and c["files_by_status"]["not_archived"] >= 1
    assert c["documents_by_type"]["erlaeuternder_bericht"] == 1


# ── the fetch side ───────────────────────────────────────────────────────


def _pdf(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class FetchHttp:
    def __init__(self, files):
        self.files, self.gets, self.heads = files, [], []
        self.last_from_cache = False

    def head_length(self, url):
        self.heads.append(url)
        raise AssertionError("no HEAD on the archive hosts")

    def get_prefix(self, url, n):
        raise AssertionError("no range request on the archive hosts")

    def get(self, url, max_bytes):
        self.gets.append(url)
        v = self.files[url]
        if isinstance(v, Exception):
            raise v
        return v


def test_fetch_downloads_the_id_form_paced_and_stops_only_wayback(work):
    _discover_pc(work)
    pending = [r[0] for r in work.execute(
        "SELECT file_url FROM consultation_files WHERE source IS NOT NULL AND status='pending' "
        "ORDER BY file_id")]
    assert pending
    text = ("Der Bundesrat schlägt vor, das Verfahren für die Genehmigung kantonaler Erlasse "
            "gesetzlich neu zu regeln und die Information des Bundes über Verträge zu ordnen. ") * 4
    files = {bcc.download_url(u): _pdf(text) for u in pending}
    first = bcc.download_url(pending[0])
    assert "/web/20070101000000id_/" in first or "/web/20080101000000id_/" in first
    http = FetchHttp(files)
    pauses = []
    st = bcc.fetch(work, rate_limit=0, http=http, sleep=pauses.append)
    assert st["stopped"] is None and st["by_status"]["done"] == len(pending)
    assert http.gets == [bcc.download_url(u) for u in sorted(
        pending, key=lambda u: http.gets.index(bcc.download_url(u)))]
    assert set(pauses) == {2.0}   # >= 2 s after every Wayback request
    hit = work.execute("SELECT COUNT(*) FROM consultation_paragraphs_fts WHERE "
                       "consultation_paragraphs_fts MATCH 'kantonaler'").fetchone()[0]
    assert hit


def test_fetch_a_wayback_refusal_stops_that_host_only(work):
    _discover_pc(work)
    work.execute("UPDATE consultation_files SET file_url='https://fedlex.data.admin.ch/f/x.pdf', "
                 "status='pending' WHERE source IS NULL")
    work.commit()
    pending = [r[0] for r in work.execute(
        "SELECT file_url FROM consultation_files WHERE source IS NOT NULL AND status='pending'")]
    files = {bcc.download_url(u): urllib.error.HTTPError(u, 429, "slow down", {}, None)
             for u in pending}
    files["https://fedlex.data.admin.ch/f/x.pdf"] = _pdf("Erläuternder Bericht " * 30)
    http = FetchHttp(files)
    st = bcc.fetch(work, rate_limit=0, http=http, sleep=lambda s: None)
    assert st["stopped"] is None
    assert st["stopped_hosts"] == {"web.archive.org": "http_429"}
    assert sum("web.archive.org" in u for u in http.gets) == 1
    assert work.execute("SELECT status FROM consultation_files WHERE source IS NULL"
                        ).fetchone()[0] == "done"


# ── bj.admin.ch ──────────────────────────────────────────────────────────

BJ_DE = "https://www.bj.admin.ch/de/vereinheitlichung-des-zivilprozessrechts"
BJ_FR = "https://www.bj.admin.ch/fr/unification-de-la-procedure-civile"


def test_bj_listing_and_project_pages():
    assert [s for s, _ in ca.parse_bj_listing(
        page("bj_listing_abgeschlossene_staat_20261020.html"))] == [
        "finanzierung-des-instituts-fuer-foederalismus", "staerkung-des-datenschutzes",
        "vereinheitlichung-des-zivilprozessrechts"]
    de = ca.parse_bj_project(page("bj_project_zpo_de_20261020.html"), url=BJ_DE, lang="de")
    assert de.title == "Vereinheitlichung des Zivilprozessrechts"
    assert de.group_id == "Gh-cx2epEhnk"
    assert de.translations["fr"] == "unification-de-la-procedure-civile"
    assert de.opened == "2003-06-25"   # "Der Bundesrat eröffnet am 25. Juni 2003 das Vernehmlassungsverfahren"
    assert [(i.group, i.title) for i in de.items] == [
        ("Vernehmlassungsverfahren", "Erläuternder Bericht zur Eröffnung des Vernehmlassungsverfahrens"),
        ("Vernehmlassungsverfahren", "Vorentwurf"),
        ("Vernehmlassungsergebnisse", "Bericht über das Ergebnis des Vernehmlassungsverfahrens"),
        ("Vernehmlassungsergebnisse",
         "Bericht über das Ergebnis des Vernehmlassungsverfahrens. Zusammenfassung")]
    assert de.items[0].url == "https://www.bj.admin.ch/dam/de/sd-web/34VW9vnBSJBq/vn-ber.pdf"
    assert ca._bj_item_date(de.items[0].meta) == "2003-06-26"


def test_bj_old_archive_layout():
    o = ca.parse_bj_old_project(
        page("bj_old_zivilprozessrecht_de_20230608085444.html"), url="snap", lang="de",
        base_url="https://www.bj.admin.ch/bj/de/home/staat/gesetzgebung/archiv/zivilprozessrecht.html")
    assert [i.title for i in o.items] == [
        "Vorentwurf für eine Schweizerische Zivilprozessordnung",
        "Begleitbericht für eine Schweizerische Zivilprozessordnung",
        "Zusammenstellung der Vernehmlassungen", "Zusammenfassung der Vernehmlassungen"]
    assert ca._bj_old_file_url(o.items[0].url) == (
        "https://www.bj.admin.ch/dam/bj/de/data/staat/gesetzgebung/archiv/zivilprozessrecht/"
        "entw-zpo-d.pdf")
    assert ca._bj_file_stem(o.items[0].url) == "entw-zpo"


def test_bj_live_discovery_matches_fedlex_and_skips_held_types(work):
    F = "https://fedlex.data.admin.ch/eli/dl/proj/2002/17/cons_1"
    work.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
                 "start_date, end_date, discovered_at, updated_at) VALUES (?, 'proj/2002/17/cons_1', "
                 "2002, 'Vereinheitlichung des Zivilprozessrechts (Schaffung einer "
                 "gesamtschweizerischen Zivilprozessordnung)', '2003-06-25', '2003-12-31', 'n', 'n')",
                 (F,))
    work.commit()
    listing = (FIX / "bj_listing_abgeschlossene_staat_20261020.html").read_bytes()
    pages = {BJ_DE: (FIX / "bj_project_zpo_de_20261020.html").read_bytes(),
             BJ_FR: (FIX / "bj_project_zpo_fr_20261020.html").read_bytes()}
    for kind in ca.BJ_LISTING_KINDS:
        for area in ca.BJ_AREAS:
            pages[ca.bj_listing_url(kind, area)] = listing if (kind, area) == (
                "abgeschlossene", "staat-und-buerger") else b"<html></html>"
    web = FakeWeb(pages, {})
    report = ca.discover(work, [ca.SOURCE_BJ], ca.ArchiveHttp(opener=web, intervals={}))
    st = report[ca.SOURCE_BJ]
    # two listed projects and the Italian page have no fixture (404): reported
    assert len(st["pages_failed"]) == 3 and st["entries"] == 1
    assert {f["reason"] for f in st["pages_failed"]} == {"404"}
    assert st["match_methods"] == {"opened_title": 1}
    docs = work.execute("SELECT * FROM consultation_documents WHERE source='bj' "
                        "ORDER BY document_uri").fetchall()
    assert {d["procedure_uri"] for d in docs} == {F}
    by = {d["document_uri"].rsplit("/", 1)[-1]: d for d in docs}
    assert by["vn-ber"]["doc_type"] == "erlaeuternder_bericht"
    assert by["vn-ber"]["title_fr"] == ("Rapport explicatif relatif à l’ouverture de la "
                                        "procédure de consultation")
    assert by["entw-zpo"]["doc_type"] == "vernehmlassungsvorlage"
    assert by["ve-ber"]["doc_type"] == "ergebnisbericht"
    f = work.execute("SELECT * FROM consultation_files WHERE document_uri=? AND language='fr'",
                     (by["vn-ber"]["document_uri"],)).fetchone()
    assert f["file_url"] == f["original_url"] == \
        "https://www.bj.admin.ch/dam/fr/sd-web/34VW9vnBSJBq/vn-ber.pdf" or \
        f["file_url"].startswith("https://www.bj.admin.ch/dam/fr/")
    assert f["source_page_url"] == BJ_FR and f["status"] == "pending"
    assert f["publication_date"] == "2003-06-26"


# ── the server surface ───────────────────────────────────────────────────


def test_server_shows_the_source_label_and_provenance(work, tmp_path, monkeypatch):
    _discover_pc(work)
    pending = [r[0] for r in work.execute(
        "SELECT file_url FROM consultation_files WHERE source IS NOT NULL AND status='pending'")]
    body = ("Der Bundesrat schlägt vor, das Verfahren für die Genehmigung kantonaler Erlasse "
            "gesetzlich neu zu regeln. ") * 5
    bcc.fetch(work, rate_limit=0, http=FetchHttp({bcc.download_url(u): _pdf(body)
                                                  for u in pending}), sleep=lambda s: None)
    out = bcc.publish(work, tmp_path / "consultations.db")
    monkeypatch.setenv("SWISS_CASELAW_CONSULTATIONS_DB", str(out))
    import mcp_server
    conn = mcp_server._get_consultations_conn()
    hits = mcp_server._search_consultations(conn, "Genehmigung", language="de", year_min=None,
                                            year_max=None, doc_type="vernehmlassungsvorlage",
                                            limit=5)
    conn.close()
    assert hits
    h = hits[0]
    assert h["source"] == "admin_ch_vernehmlassung_wayback"
    assert h["doc_type_label"] == "Vorlage"     # the page's own label, not a Fedlex one
    assert h["original_url"].endswith("/gg/pc/documents/1105/Vorlage_d.pdf")
    assert h["linked_from"].startswith("https://web.archive.org/web/20160414232001/")
    assert h["file_url"].startswith("https://web.archive.org/web/")
    assert h["procedure_id"] == "proj/2004/7/cons_1"
    assert "archived_copy" in h


def test_bj_wayback_adds_only_what_the_live_page_lacks(work):
    old_url = "https://www.bj.admin.ch/bj/de/home/staat/gesetzgebung/archiv/zivilprozessrecht.html"
    ts = "20230608085444"
    html = (FIX / "bj_old_zivilprozessrecht_de_20230608085444.html").read_text()
    # the old page as captured, plus one file the live page no longer lists
    extra = ('<li><a href="/dam/bj/de/data/staat/gesetzgebung/archiv/zivilprozessrecht/'
             'ber-ek-d.pdf.download.pdf/ber-ek-d.pdf" title="Bericht der Expertenkommission">'
             'Bericht der Expertenkommission</a></li>')
    html = html.replace("</ul>", extra + "</ul>", 1)
    dam = "www.bj.admin.ch/dam/bj/de/data/staat/gesetzgebung/archiv/zivilprozessrecht/"
    web = FakeWeb(
        {f"https://web.archive.org/web/{ts}id_/{old_url}": html.encode()},
        {"www.bj.admin.ch/bj/de/home/staat/gesetzgebung/archiv/": [_cdx_row(old_url, ts)],
         "www.bj.admin.ch/dam/bj/de/data/staat/gesetzgebung/archiv/": [
             _cdx_row("https://" + dam + "ber-ek-d.pdf.download.pdf/ber-ek-d.pdf",
                      "20200101000000", "application/pdf")]})
    live = ca.ArchiveProcedure(
        source=ca.SOURCE_BJ, key="Gh-cx2epEhnk", year=2003, opened="2003-06-25", deadline=None,
        titles={"de": "Vereinheitlichung des Zivilprozessrechts"}, department="EJPD/BJ",
        pages={}, documents=[ca.ArchiveDocument(key=f"Gh-cx2epEhnk/{s}", doc_type_code=12)
                             for s in ("vn-ber", "entw-zpo", "ve-ber", "ve-ber-res")])
    out = ca.bj_wayback_procedures(ca.ArchiveHttp(opener=web, intervals={}), [live],
                                   areas=["staat"])
    assert len(out) == 1
    ap = out[0]
    assert ap.source == ca.SOURCE_BJ_WAYBACK and ap.twin_key == "Gh-cx2epEhnk"
    assert [d.titles["de"] for d in ap.documents] == ["Bericht der Expertenkommission"]
    f = ap.documents[0].files["de"]
    assert f.snapshot == ("https://web.archive.org/web/20200101000000/https://" + dam
                          + "ber-ek-d.pdf.download.pdf/ber-ek-d.pdf")
    assert f.source_page_url == f"https://web.archive.org/web/{ts}/{old_url}"


def test_a_suppressed_document_stays_down(work):
    _discover_pc(work)
    uri = work.execute("SELECT document_uri FROM consultation_documents WHERE source=? "
                       "AND title_de='Bericht'", (ca.SOURCE_ADMIN_PC,)).fetchone()[0]
    out = ca.suppress_document(work, uri)
    assert out["files_deleted"] == 3
    report, _ = _discover_pc(work)
    assert report[ca.SOURCE_ADMIN_PC]["documents_suppressed"] == 1
    assert not work.execute("SELECT 1 FROM consultation_documents WHERE document_uri=?",
                            (uri,)).fetchone()


def test_a_bj_project_without_documents_is_not_a_procedure(work):
    ap = ca.ArchiveProcedure(source=ca.SOURCE_BJ, key="g1", year=None, opened=None,
                             deadline=None, titles={"de": "Coronavirus und Justiz"},
                             department="EJPD/BJ", pages={"de": "https://www.bj.admin.ch/de/x"})
    st = ca.store_all(work, [ap], ca._new_stats())
    assert st["procedures_created"] == 0 and st["entries_without_documents_unmatched"] == 1
    row = work.execute("SELECT procedure_uri, match_method FROM consultation_archive_entries "
                       "WHERE entry_id='bj:g1'").fetchone()
    assert tuple(row) == (None, "no_documents")


def test_bj_opening_date_ignores_news_teasers_below_the_documentation():
    html = page("bj_project_zpo_de_20261020.html")
    teaser = ('<p>Am 14. Oktober 2026 hat der Bundesrat die entsprechende Vernehmlassung '
              'eröffnet. Mehr über «Etwas anderes»</p>')
    i = html.index('<ul class="accordion"')
    pr = ca.parse_bj_project(html[:i] + teaser + html[i:], url=BJ_DE, lang="de")
    assert pr.opened == "2003-06-25"


def test_match_bj_by_opening_date_and_office():
    cands = [dict(_cand("E", "Änderung des Zivilgesetzbuches (Erbrecht)", "2016-03-04",
                        "2016-06-20", year=2016), institution_label="Bundesamt für Justiz"),
             dict(_cand("X", "Änderung des Zivilgesetzbuches (Erbrecht)", "2016-03-04",
                        "2016-06-20", year=2016), institution_label="Bundesamt für Umwelt")]
    m = ca.match_procedure({"de": "Erbrecht"}, year=2016, opened="2016-03-04", deadline=None,
                           candidates=cands, institution=ca.is_justice_institution)
    assert (m.procedure_uri, m.method) == ("E", "opened_institution")
    # without the office hint a one-word title never matches
    m = ca.match_procedure({"de": "Erbrecht"}, year=2016, opened="2016-03-04", deadline=None,
                           candidates=cands[:1])
    assert m.procedure_uri is None


def test_a_capture_that_keeps_failing_is_skipped_not_fatal():
    clock = Clock()
    err = urllib.error.HTTPError("u", 500, "INTERNAL SERVER ERROR", {}, None)
    web = FakeWeb({"https://web.archive.org/web/1id_/x": err}, {})
    h = ca.ArchiveHttp(opener=web, sleep=clock.sleep, clock=clock, intervals={})
    st = {}
    assert ca.get_page(h, "https://web.archive.org/web/1id_/x", st) is None
    assert st["pages_failed"][0]["reason"].startswith("HTTPError")
    assert len(web.calls) == 1 + ca.RETRIES
    refuse = FakeWeb({"https://web.archive.org/r": urllib.error.HTTPError(
        "u", 429, "x", {}, None)}, {})
    with pytest.raises(SourceUnavailable):
        ca.get_page(ca.ArchiveHttp(opener=refuse, intervals={}), "https://web.archive.org/r", {})


def test_wayback_403_is_one_excluded_capture_not_a_stop(work):
    _discover_pc(work)
    pending = [r[0] for r in work.execute(
        "SELECT file_url FROM consultation_files WHERE source IS NOT NULL AND status='pending' "
        "ORDER BY file_id")]
    body = ("Der Bundesrat schlägt vor, das Verfahren für die Genehmigung kantonaler Erlasse "
            "gesetzlich neu zu regeln. ") * 5
    files = {bcc.download_url(u): _pdf(body) for u in pending}
    files[bcc.download_url(pending[0])] = urllib.error.HTTPError(pending[0], 403, "x", {}, None)
    st = bcc.fetch(work, rate_limit=0, http=FetchHttp(files), sleep=lambda s: None)
    assert st["stopped"] is None and st["stopped_hosts"] == {}
    assert st["by_status"]["not_archived"] == 1 and st["by_status"]["done"] == len(pending) - 1
    st = {}
    h = ca.ArchiveHttp(opener=FakeWeb({"https://web.archive.org/web/1id_/x": urllib.error.HTTPError(
        "u", 403, "x", {}, None)}, {}), intervals={})
    assert ca.get_page(h, "https://web.archive.org/web/1id_/x", st) is None
    assert st["pages_failed"][0]["reason"] == "403"


def test_bj_documents_outside_the_matched_consultation_stay_on_the_project(work):
    F = "https://fedlex.data.admin.ch/eli/dl/proj/2002/17/cons_1"
    work.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
                 "start_date, discovered_at, updated_at) VALUES (?, 'proj/2002/17/cons_1', 2002, "
                 "'Vereinheitlichung des Zivilprozessrechts', '2003-06-25', 'n', 'n')", (F,))
    work.commit()

    def doc(key, code, day):
        return ca.ArchiveDocument(key=f"g/{key}", doc_type_code=code, titles={"de": key},
                                  files={"de": ca.ArchiveFile("de", f"https://www.bj.admin.ch/dam/{key}.pdf",
                                                              "https://www.bj.admin.ch/de/p",
                                                              f"https://www.bj.admin.ch/dam/{key}.pdf",
                                                              f"{key}.pdf", day)})
    ap = ca.ArchiveProcedure(
        source=ca.SOURCE_BJ, key="g", year=2003, opened="2003-06-25", deadline=None,
        titles={"de": "Vereinheitlichung des Zivilprozessrechts"}, department="EJPD/BJ",
        pages={"de": "https://www.bj.admin.ch/de/p"},
        documents=[doc("vn-ber", 12, "2003-06-26"), doc("ve-ber", 31, "2004-09-15"),
                   doc("vn-ber-2014", 12, "2014-03-12")])
    st = ca.store_all(work, [ap], ca._new_stats())
    assert st["documents_outside_matched_window"] == 1
    procs = dict(work.execute("SELECT document_uri, procedure_uri FROM consultation_documents "
                              "WHERE source='bj'").fetchall())
    assert procs["urn:opencaselaw:bj:doc:g/vn-ber"] == F
    assert procs["urn:opencaselaw:bj:doc:g/ve-ber"] == F
    assert procs["urn:opencaselaw:bj:doc:g/vn-ber-2014"] == "urn:opencaselaw:bj:g/rest"
    rest = work.execute("SELECT title_de, source FROM consultation_procedures WHERE "
                        "procedure_uri='urn:opencaselaw:bj:g/rest'").fetchone()
    assert tuple(rest) == ("Vereinheitlichung des Zivilprozessrechts", "bj")


def test_bj_documents_date_themselves_to_the_fedlex_procedure(work):
    F = "https://fedlex.data.admin.ch/eli/dl/proj/2022/20/cons_1"
    work.execute("INSERT INTO consultation_vocab (uri, label_de) VALUES (?, 'Bundesamt für Justiz')",
                 ("https://fedlex.data.admin.ch/vocabulary/legal-institution/5",))
    work.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
                 "start_date, institution_uri, discovered_at, updated_at) VALUES (?, "
                 "'proj/2022/20/cons_1', 2022, 'Totalrevision des Bundesgesetzes über das "
                 "Verwaltungsstrafrecht (VStrR)', '2024-01-31', "
                 "'https://fedlex.data.admin.ch/vocabulary/legal-institution/5', 'n', 'n')", (F,))
    # Fedlex already holds the Erläuternder Bericht in German
    work.execute("INSERT INTO consultation_documents (document_uri, procedure_uri, doc_type_code, "
                 "doc_type) VALUES ('FD', ?, 12, 'erlaeuternder_bericht')", (F,))
    work.execute("INSERT INTO consultation_files (document_uri, language, file_url, text_policy, "
                 "status) VALUES ('FD', 'de', 'https://fedlex.data.admin.ch/x.pdf', 'text', 'done')")
    work.commit()
    vs = ca.parse_bj_project(page("bj_project_zpo_de_20261020.html"), url=BJ_DE, lang="de")
    # the Verwaltungsstrafrecht page as listed 2026-10-20: three items, no
    # opening sentence in the timeline (so no page-level match)
    vs.title, vs.opened, vs.group_id = "Verwaltungsstrafrecht", None, "Co3i8TYQrqyg"
    vs.items = [ca.BjItem("Vernehmlassungsverfahren", "Erläuternder Bericht zur Eröffnung des "
                          "Vernehmlassungsverfahrens",
                          "https://www.bj.admin.ch/dam/de/sd-web/up63WnvkTwUk/vn-ber.pdf",
                          "PDF 1.68 MB 31. Januar 2024"),
                ca.BjItem("Vernehmlassungsverfahren", "Vorentwurf",
                          "https://www.bj.admin.ch/dam/de/sd-web/Gkrw5yfBkWkK/vorentw.pdf",
                          "PDF 1.69 MB 31. Januar 2024"),
                ca.BjItem("Stellungnahmen des Vernehmlassungsverfahrens",
                          "Stellungnahmen des Vernehmlassungsverfahrens",
                          "https://www.bj.admin.ch/dam/de/sd-web/KHppiw0zmh-B/stgn.pdf",
                          "PDF 24.22 MB 3. Dezember 2024")]
    ap = ca._bj_project_procedure({"de": vs}, ca.SOURCE_BJ)
    assert ap.opened is None
    st = ca.store_all(work, [ap], ca._new_stats())
    assert st["documents_dated_to_procedure"] == 3 and st["files_already_held"] == 1
    rows = work.execute("SELECT d.doc_type, d.procedure_uri FROM consultation_documents d "
                        "WHERE d.source='bj'").fetchall()
    assert sorted(r[0] for r in rows) == ["stellungnahmen", "vernehmlassungsvorlage"]
    assert {r[1] for r in rows} == {F}
    e = dict(work.execute("SELECT entry_id, match_method FROM consultation_archive_entries"
                          ).fetchall())
    assert e["bj:Co3i8TYQrqyg@proj/2022/20/cons_1"] == "document_date"
    assert e["bj:Co3i8TYQrqyg"] == "documents_dated"  # the page itself, for the audit
    # no procedure invented for the project page itself
    assert not work.execute("SELECT 1 FROM consultation_procedures WHERE source='bj'").fetchone()


def test_an_archive_file_identical_to_a_fedlex_file_is_not_indexed_twice(work):
    _discover_pc(work)
    data = _pdf("Der Bundesrat schlägt vor, das Verfahren für die Genehmigung kantonaler Erlasse "
                "gesetzlich neu zu regeln. " * 5)
    import hashlib
    work.execute("UPDATE consultation_files SET status='done', sha256=? WHERE source IS NULL",
                 (hashlib.sha256(data).hexdigest(),))
    work.commit()
    url = work.execute("SELECT file_url FROM consultation_files WHERE source IS NOT NULL AND "
                       "status='pending' ORDER BY file_id").fetchone()[0]
    row = [r for r in bcc._queue(work) if r["file_url"] == url][0]
    assert bcc.fetch_one(work, row, FetchHttp({bcc.download_url(url): data})) == "same_as"
    twin = work.execute("SELECT f2.source FROM consultation_files f JOIN consultation_files f2 "
                        "ON f2.file_id = f.same_as_file_id WHERE f.file_url=?", (url,)).fetchone()
    assert twin[0] is None   # the Fedlex file
