"""scripts/check_materialien_completeness.py against fake Fedlex answers and
fixture databases: a typed work missing past the grace period is a gap, a
fresh one is not; historical works are counted stored / duplicate / by fetch
state; Vernehmlassungen discovery lag and text backlog are gaps; one ntfy
message per run with a gap, none without."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import check_materialien_completeness as cmc  # noqa: E402
from scrapers import fedlex_materialien as fm  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402
from search_stack import build_consultation_corpus as bcc  # noqa: E402

FGA = "https://fedlex.data.admin.ch/eli/fga/"


def _materialien(tmp_path):
    db = tmp_path / "materialien.db"
    c = sqlite3.connect(db)
    bbc.ensure_schema(c)
    for lang in ("de", "fr", "it"):
        c.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
                  "source_url, format, language, ingested_at) VALUES (2011, 7705, 'x', ?, 'u', "
                  "'pdf', ?, 'now')", (FGA + "2011/1325", lang))
    c.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_volume, bbl_citation, "
              "eli_uri, source_url, format, language, ingested_at) VALUES (1975, 609, 1, 'x', ?, "
              "'u', 'pdf', 'de', 'now')", (FGA + "1975/1_609__",))
    c.execute("INSERT INTO botschaft_fetch_state (eli_uri, language, status) VALUES (?, 'de', 'collision')",
              (FGA + "1975/1_609_x",))
    c.execute("INSERT INTO botschaft_fetch_state (eli_uri, language, status) VALUES (?, 'de', 'no_text')",
              (FGA + "1935/1_350__",))
    c.commit()
    c.close()
    return db


def _consultations(tmp_path):
    db = tmp_path / "consultations.db"
    c = bcc.connect_work_db(db)
    c.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, start_date, "
              "discovered_at, updated_at) VALUES ('P', 'proj/2023/1/cons_1', 2023, '2023-01-10', 'n', 'n')")
    c.execute("INSERT INTO consultation_documents (document_uri, procedure_uri, doc_type_code, "
              "doc_type) VALUES ('D', 'P', 12, 'erlaeuternder_bericht')")
    c.execute("INSERT INTO consultation_files (document_uri, language, text_policy, status, publication_date) "
              "VALUES ('D', 'de', 'text', 'done', '2023-01-10')")
    c.execute("INSERT INTO consultation_files (document_uri, language, text_policy, status, publication_date) "
              "VALUES ('D', 'fr', 'text', 'pending', '2023-01-10')")
    c.commit()
    c.close()
    return db


def _typed(lang, types):
    out = [{"eli_uri": FGA + "2011/1325", "doc_type": "botschaft", "year": 2011,
            "publication_date": "2011-10-25", "title": "Botschaft VVG"}]
    if lang == "de":
        out += [{"eli_uri": FGA + "2020/1", "doc_type": "bericht_kommission", "year": 2020,
                 "publication_date": "2020-03-01", "title": "Bericht alt"},
                {"eli_uri": FGA + "2026/9", "doc_type": "botschaft", "year": 2026,
                 "publication_date": "2026-10-19", "title": "Botschaft neu"}]
    return out


def _untyped(lang):
    if lang != "de":
        return []
    mk = lambda tail, y: fm.UntypedBotschaft(y, 1, "Botschaft", FGA + f"{y}/{tail}", "f", "bar",
                                            "BBl", None, 1, "de", "botschaft")
    return [mk("1_609__", 1975), mk("1_609_x", 1975), mk("1_350__", 1935), mk("1_1__", 1900)]


def _sparql(q):
    if "COUNT(DISTINCT ?c)" in q:
        return [{"n": {"value": "2"}}]  # Fedlex has one procedure more than the DB
    if "language/ITA" in q:
        return []  # no Italian file on Fedlex either
    dt = "https://fedlex.data.admin.ch/vocabulary/draft-document-type/12"
    return [{"dt": {"value": dt}, "n": {"value": "1"}}]


def _args(tmp_path, m, c, **kw):
    base = dict(materialien_db=str(m), consultations_db=str(c), report=str(tmp_path / "r.json"),
                grace_days=7, skip_historical=False, historical_strict=False, no_ntfy=False,
                fail_on_gap=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_census_counts_gaps_and_alerts_once(tmp_path):
    m, c = _materialien(tmp_path), _consultations(tmp_path)
    sent = []
    report, gap = cmc.run(_args(tmp_path, m, c), sparql=_sparql, discover_typed=_typed,
                          discover_untyped=_untyped, today=date(2026, 10, 20),
                          sender=lambda t, msg: sent.append((t, msg)))
    t = report["bundesblatt_typed"]["by_language"]["de"]
    assert (t["fedlex"], t["held"], t["missing"], t["missing_past_grace"]) == (3, 1, 2, 1)
    assert report["bundesblatt_typed"]["gap"] == 1  # the 2026-10-19 one is inside the grace
    h = report["bundesblatt_historical"]["by_language"]["de"]
    assert (h["stored"], h["accounted"], h["not_stored"]) == (1, 2, 2)
    assert h["not_stored_by_state"] == {"fedlex_duplicate_of_stored": 1, "no_text": 1,
                                        "never_attempted": 1}
    v = report["vernehmlassungen"]
    assert v["discovery_lag"]["procedures"] == 1
    assert v["text"]["backlog"] == 1 and v["text"]["backlog_past_grace"] == 1
    assert v["gap"] == 2
    assert gap and report["gap"] == 3 and len(sent) == 1
    assert (tmp_path / "r.json").exists()


def test_no_alert_without_a_gap(tmp_path):
    m, c = _materialien(tmp_path), _consultations(tmp_path)
    con = sqlite3.connect(c)
    con.execute("UPDATE consultation_files SET status='same_as'")
    con.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, discovered_at, "
                "updated_at) VALUES ('P2', 'proj/2023/2/cons_1', 2023, 'n', 'n')")
    con.commit(); con.close()
    sent = []
    report, gap = cmc.run(_args(tmp_path, m, c), sparql=_sparql,
                          discover_typed=lambda lang, t: _typed("fr", t),
                          discover_untyped=_untyped, today=date(2026, 10, 20),
                          sender=lambda t, msg: sent.append(t))
    assert not gap and sent == [] and report["gap"] == 0


def test_missing_consultations_db_is_a_gap(tmp_path):
    m = _materialien(tmp_path)
    report, gap = cmc.run(_args(tmp_path, m, tmp_path / "none.db", no_ntfy=True, skip_historical=True),
                          sparql=_sparql, discover_typed=lambda l, t: _typed("fr", t),
                          discover_untyped=_untyped, today=date(2026, 10, 20))
    assert gap and report["vernehmlassungen"]["error"]


def _with_archive_rows(c):
    """An archive-created procedure, an archive document on a Fedlex procedure
    of 1999 (pending) and a second one never archived."""
    con = sqlite3.connect(c)
    con.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, discovered_at, "
                "updated_at) VALUES ('F99', 'proj/1999/4/cons_1', 1999, 'n', 'n')")
    con.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, discovered_at, "
                "updated_at, source) VALUES ('urn:opencaselaw:admin_pc_wayback:1998/h1', "
                "'admin_pc_wayback/1998/h1', 1998, 'n', 'n', 'admin_pc_wayback')")
    con.execute("INSERT INTO consultation_documents (document_uri, procedure_uri, doc_type_code, "
                "doc_type, title_de, source) VALUES ('AD', 'F99', 12, 'erlaeuternder_bericht', "
                "'Bericht', 'admin_pc_wayback')")
    con.execute("INSERT INTO consultation_files (document_uri, language, file_url, text_policy, "
                "status, source, original_url) VALUES ('AD', 'de', 'https://web.archive.org/web/"
                "20070101000000/http://www.admin.ch/x.pdf', 'text', 'pending', 'admin_pc_wayback', "
                "'http://www.admin.ch/x.pdf')")
    con.execute("INSERT INTO consultation_files (document_uri, language, text_policy, status, "
                "source, original_url) VALUES ('AD', 'fr', 'text', 'not_archived', "
                "'admin_pc_wayback', 'http://www.admin.ch/y.pdf')")
    con.execute("INSERT INTO consultation_archive_entries (entry_id, source, year, page_urls_json, "
                "procedure_uri, match_method, documents, discovered_at) VALUES "
                "('admin_pc_wayback:1999/1', 'admin_pc_wayback', 1999, '{}', 'F99', "
                "'deadline_title', 1, 'n'), ('admin_pc_wayback:1998/h1', 'admin_pc_wayback', 1998, "
                "'{}', 'urn:opencaselaw:admin_pc_wayback:1998/h1', 'created', 0, 'n')")
    con.commit()
    con.close()


def _sparql_three(q):
    if "COUNT(DISTINCT ?c)" in q:
        return [{"n": {"value": "2"}}]  # P and F99: the archive procedure is not Fedlex's
    return _sparql(q)


def test_archive_sources_reported_apart_from_fedlex(tmp_path):
    m, c = _materialien(tmp_path), _consultations(tmp_path)
    _with_archive_rows(c)
    con = sqlite3.connect(c)
    con.execute("UPDATE consultation_files SET status='done' WHERE source IS NULL")
    con.commit(); con.close()
    report, gap = cmc.run(_args(tmp_path, m, c, no_ntfy=True, skip_historical=True),
                          sparql=_sparql_three, discover_typed=lambda l, t: _typed("fr", t),
                          discover_untyped=_untyped, today=date(2026, 10, 20))
    v = report["vernehmlassungen"]
    # the archive procedure, document and files do not hide or fake Fedlex lag
    assert v["held"]["procedures"] == 2 and v["discovery_lag"]["procedures"] == 0
    assert v["held"]["documents"] == {"erlaeuternder_bericht": 1}
    assert v["text"]["backlog"] == 0 and v["gap"] == 0
    a = report["archive_sources"]
    src = a["sources"]["admin_pc_wayback"]
    assert src["entries"] == 2 and src["entries_by_match"] == {"created": 1, "deadline_title": 1}
    assert src["files_by_status"] == {"not_archived": 1, "pending": 1}
    assert a["backlog"] == 1
    assert a["fedlex_before_2005"] == {"procedures": 1, "with_fedlex_document": 0,
                                       "with_any_document": 1,
                                       "by_year": {"1999": {"procedures": 1,
                                                            "with_fedlex_document": 0,
                                                            "with_any_document": 1}}}
    assert not gap   # the one-off backfill is not a nightly gap ...
    report, gap = cmc.run(_args(tmp_path, m, c, no_ntfy=True, skip_historical=True,
                                archive_strict=True),
                          sparql=_sparql_three, discover_typed=lambda l, t: _typed("fr", t),
                          discover_untyped=_untyped, today=date(2026, 10, 20))
    assert gap and report["gap"] == 1   # ... unless asked
    assert any("before 2005: 1/1" in line for line in report["summary"])
