"""Text for every Vernehmlassung file we hold (2026-10-21).

Pins the extraction added to search_stack/build_consultation_corpus.py for
the files that were held without text:

  * legacy Word (.doc, converted by LibreOffice; also behind a PDF link),
    Excel workbooks (row text, per sheet) and ZIP archives (their members,
    named as provenance; Windows code-page names; junk skipped),
  * the format read from the bytes, not the label; a zero-byte file is
    empty_file, an archived copy cut at the Common Crawl 5 MiB limit is
    truncated (and repaired from a complete older capture, which a later
    discovery keeps),
  * files above the in-memory limit streamed to disk, the language copies
    still deduplicated,
  * the OCR pass: only text-less pages, every OCR paragraph flagged
    text_source='ocr', the file 'ocr' or 'mixed', engine and pages recorded,
    a file changed upstream re-queued, the budget never storing half a file,
  * the guarded re-queue and the counts of the status / completeness report,
  * the server: OCR passages flagged (R2), archive members named, an old
    database without the columns still served.

Offline: PDFs generated with PyMuPDF, Office files written with zipfile, a
fake HTTP client, a fake OCR engine and a fake LibreOffice.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_consultation_corpus as bcc  # noqa: E402

fitz = pytest.importorskip("fitz")

P = "https://fedlex.data.admin.ch/eli/dl/proj/2024/7/cons_1"
FS = "https://fedlex.data.admin.ch/filestore/fedlex.data.admin.ch/eli/dl/proj/2024/7/cons_1"
LONG = ("Mit dieser Vorlage sollen die Hinterlassenenleistungen in erster Linie "
        "während der Erziehungszeit ausgerichtet werden, wie der Bundesrat es beantragt. ")
OCR_TEXT = ("Der Regierungsrat des Kantons Glarus nimmt zur Vorlage wie folgt Stel-\n"
            "lung und begrüsst die vorgeschlagene Regelung der Witwenrente.\n\n"
            "Wir danken Ihnen für die Gelegenheit zur Stellungnahme und ersuchen "
            "Sie, unsere Anliegen zu berücksichtigen.")


# ── fixtures: files ────────────────────────────────────────────────────


def _pdf(paragraphs: list[str] | None, pages: int = 1, blank_pages: int = 0) -> bytes:
    """``pages`` pages with text, then ``blank_pages`` pages without a text
    layer (what a scan looks like to the extractor)."""
    doc = fitz.open()
    for _ in range(pages if paragraphs else 0):
        page = doc.new_page()
        y = 72
        for t in paragraphs:
            page.insert_textbox(fitz.Rect(72, y, 520, y + 90), t, fontsize=10)
            y += 100
    for _ in range(blank_pages):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(100, 100, 300, 140), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/word/document.xml" ContentType="application/'
       'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
         'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
_WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    def p(t):
        runs = "".join(f"<w:r><w:t xml:space=\"preserve\">{x}</w:t></w:r>" if i == 0 else
                       f"<w:r><w:tab/><w:t>{x}</w:t></w:r>" for i, x in enumerate(t.split("\t")))
        return f"<w:p>{runs}</w:p>"
    body = "".join(p(t) for t in paragraphs)
    if table:
        body += "<w:tbl>" + "".join(
            "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in row) + "</w:tr>"
            for row in table) + "</w:tbl>"
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document {_WNS}><w:body>{body}<w:sectPr/></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


_XNS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    shared: list[str] = []

    def cell(ref, v):
        if isinstance(v, bool):
            return f'<c r="{ref}" t="b"><v>{int(v)}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        if isinstance(v, tuple):          # ("inline", text)
            return f'<c r="{ref}" t="inlineStr"><is><t>{v[1]}</t></is></c>'
        if v is None:
            return ""
        shared.append(v)
        return f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>'

    parts = {}
    for i, (name, rows) in enumerate(sheets.items(), 1):
        xml_rows = "".join(
            f'<row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row))
            + "</row>" for r, row in enumerate(rows, 1))
        parts[f"xl/worksheets/sheet{i}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet {_XNS}><sheetData>{xml_rows}'
            f'</sheetData></worksheet>')
    wb = (f'<?xml version="1.0" encoding="UTF-8"?><workbook {_XNS} {_RNS}><sheets>'
          + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>'
                    for i, n in enumerate(sheets, 1)) + "</sheets></workbook>")
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/'
                      f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(sheets) + 1)) + "</Relationships>")
    sst = (f'<?xml version="1.0" encoding="UTF-8"?><sst {_XNS}>'
           + "".join(f"<si><t>{t}</t></si>" for t in shared) + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        for k, v in parts.items():
            z.writestr(k, v)
    return buf.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _ole_doc() -> bytes:
    """The bytes sniffing looks at in a Word 97 file: the OLE signature and
    the WordDocument stream's directory entry."""
    return bcc._OLE_MAGIC + b"\0" * 504 + "WordDocument".encode("utf-16-le") + b"\0" * 400


def _fake_soffice(calls: list):
    def convert(data: bytes, src_ext: str, target: str) -> bytes:
        calls.append((src_ext, target))
        assert data.startswith(bcc._OLE_MAGIC)
        return _docx(["Erläuternder Bericht zur Änderung der Verordnung", LONG * 2],
                     table=[["Artikel", "Neu"], ["Art. 3", "aufgehoben"]])
    return convert


# ── fixtures: work DB and HTTP ─────────────────────────────────────────


@pytest.fixture
def work(tmp_path):
    conn = bcc.connect_work_db(tmp_path / "work.db")
    conn.execute("INSERT INTO consultation_procedures (procedure_uri, event_id, year, title_de, "
                 "discovered_at, updated_at) VALUES (?, 'proj/2024/7/cons_1', 2024, "
                 "'Änderung der AHV-Verordnung', 'n', 'n')", (P,))
    conn.commit()
    yield conn
    conn.close()


def _add(conn, name: str, fmt: str, *, code: int = 12, lang: str = "de", url: str | None = None,
         source: str | None = None, original_url: str | None = None, status: str = "pending",
         **cols) -> int:
    doc = f"{P}/{name}"
    conn.execute("INSERT OR IGNORE INTO consultation_documents (document_uri, procedure_uri, "
                 "doc_type_code, doc_type, source) VALUES (?,?,?,?,?)",
                 (doc, P, code, bcc.DOC_TYPES[code], source))
    cur = conn.execute(
        "INSERT INTO consultation_files (document_uri, language, file_url, format, text_policy, "
        "status, source, original_url) VALUES (?,?,?,?, 'text', ?,?,?)",
        (doc, lang, url or f"{FS}/{name}/{lang}/{fmt}/{name}-{lang}.{fmt}", fmt, status,
         source, original_url))
    fid = cur.lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE consultation_files SET {k}=? WHERE file_id=?", (v, fid))
    conn.commit()
    return fid


def _row(conn, fid):
    return conn.execute("SELECT * FROM consultation_files WHERE file_id=?", (fid,)).fetchone()


def _paras(conn, fid):
    return conn.execute("SELECT * FROM consultation_paragraphs WHERE file_id=? ORDER BY para_order",
                        (fid,)).fetchall()


class FakeHttp:
    def __init__(self, files: dict[str, object], stream_ok: bool = True):
        self.files = files
        self.gets: list[str] = []
        self.streams: list[str] = []
        self.heads: list[str] = []
        self.prefixes: list[str] = []
        self.last_from_cache = False
        if not stream_ok:
            self.get_to_file = None
            del self.get_to_file

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

    def get_to_file(self, url, path, max_bytes):
        self.streams.append(url)
        data = self._data(url)
        if len(data) > max_bytes:
            raise bcc._TooLarge(len(data), max_bytes)
        Path(path).write_bytes(data)
        return len(data)


def _fetch(conn, http):
    return bcc.fetch(conn, rate_limit=0, http=http, sleep=lambda s: None)


# ── format sniffing ────────────────────────────────────────────────────


def test_sniff_reads_the_bytes_not_the_label():
    assert bcc.sniff_format(b"", "pdf") == "empty"
    assert bcc.sniff_format(_pdf([LONG])[:1024], "doc") == "pdf"
    ole = _ole_doc()
    assert bcc.sniff_format(ole[:1024], "pdf", whole=ole) == "doc"
    xls = bcc._OLE_MAGIC + b"\0" * 100 + "Workbook".encode("utf-16-le")
    assert bcc.sniff_format(xls, "xls", whole=xls) == "xls"
    for data, want in ((_docx(["x"]), "docx"), (_xlsx({"A": [["x"]]}), "xlsx"),
                       (_zip({"a.pdf": b"%PDF-1.4"}), "zip")):
        assert bcc.sniff_format(data[:1024], "zip", whole=data) == want
    assert bcc.sniff_format(b"<html>", "pdf-a") == "pdf"   # unknown bytes: the label


# ── Word, Excel ────────────────────────────────────────────────────────


def test_docx_without_python_docx_reads_paragraphs_then_tables(monkeypatch):
    data = _docx(["Erster Absatz", "Zweiter\tTeil", "  "], table=[["A", "B"], ["A", "C"]])
    blocks = bcc._docx_page_blocks_xml(data)
    assert blocks == [["Erster Absatz", "Zweiter\tTeil", "A | B", "A | C"]]
    try:
        import docx  # noqa: F401
    except ImportError:
        return
    assert bcc.docx_page_blocks(data) == blocks   # python-docx reads the same


def test_xlsx_rows_per_sheet_with_shared_inline_numbers_and_booleans():
    data = _xlsx({"Übersicht": [["Kanton", "Stellungnahme", "Anzahl"],
                                ["ZH", ("inline", "Zustimmung"), 3],
                                [None, None, None], ["GL", "Ablehnung", True]],
                  "Leer": []})
    assert bcc.xlsx_sheet_blocks(data) == [
        ("Übersicht", ["Kanton | Stellungnahme | Anzahl", "ZH | Zustimmung | 3",
                       "GL | Ablehnung | TRUE"]),
        ("Leer", [])]


def test_fetch_doc_through_libreoffice(work, monkeypatch):
    calls = []
    monkeypatch.setattr(bcc, "OFFICE_CONVERTER", _fake_soffice(calls))
    fid = _add(work, "doc_3", "doc")
    url = _row(work, fid)["file_url"]
    st = _fetch(work, FakeHttp({url: _ole_doc()}))
    assert st["by_status"] == {"done": 1} and calls == [("doc", "docx")]
    r = _row(work, fid)
    assert json.loads(r["extraction_json"]) == {"format": "doc", "method": "libreoffice doc->docx"}
    assert r["page_count"] is None and r["text_source"] is None
    texts = [p["text"] for p in _paras(work, fid)]
    assert "Art. 3 | aufgehoben" in texts and any("Erziehungszeit" in t for t in texts)
    assert all(p["page_number"] is None and p["text_source"] is None for p in _paras(work, fid))


def test_a_doc_behind_a_pdf_link_is_read_as_doc(work, monkeypatch):
    # bj.admin.ch: gmbh-musterstatuten-ohne-kommentar.doc filed as 'pdf'
    monkeypatch.setattr(bcc, "OFFICE_CONVERTER", _fake_soffice([]))
    fid = _add(work, "doc_4", "pdf", code=15, source="bj",
               url="https://www.bj.admin.ch/dam/de/sd-web/x/gmbh-musterstatuten.doc")
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: _ole_doc()}))
    r = _row(work, fid)
    assert r["status"] == "done" and json.loads(r["extraction_json"])["format"] == "doc"


def test_doc_conversion_failure_is_a_parse_error(work, monkeypatch):
    def broken(data, a, b):
        raise RuntimeError("soffice doc->docx failed (rc=1)")
    monkeypatch.setattr(bcc, "OFFICE_CONVERTER", broken)
    fid = _add(work, "doc_3", "doc")
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: _ole_doc()}))
    r = _row(work, fid)
    assert r["status"] == "parse_error" and "soffice" in r["error"]


def test_fetch_xlsx_sheet_names_are_the_paragraphs_member(work):
    fid = _add(work, "doc_9", "xlsx", code=16)
    data = _xlsx({"Synopse": [["Geltendes Recht", "Vorentwurf"],
                              [LONG, LONG + " geändert"]]})
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: data}))
    r = _row(work, fid)
    assert r["status"] == "done"
    assert json.loads(r["extraction_json"]) == {"format": "xlsx", "sheets": ["Synopse"]}
    assert {p["source_member"] for p in _paras(work, fid)} == {"Synopse"}


# ── ZIP ────────────────────────────────────────────────────────────────


def _cp850_named_zip() -> bytes:
    """A Windows-made archive: member names in the OEM code page, no UTF-8 flag."""
    data = _zip({"Stellungnahmen/Kanton_Z?rich.pdf": _pdf([LONG * 2]),
                 "Stellungnahmen/Verband.xlsx": _xlsx({"Antworten": [["Frage 1", LONG]]}),
                 "Stellungnahmen/liesmich.txt": b"Bitte beachten",
                 "__MACOSX/._Kanton_Z?rich.pdf": b"junk",
                 "Stellungnahmen/kaputt.pdf": b"%PDF-1.4 broken"})
    return data.replace(b"Z?rich", "Zürich".encode("cp850"))


def test_fetch_zip_members_with_provenance(work):
    fid = _add(work, "doc_21", "zip", code=21)
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: _cp850_named_zip()}))
    r = _row(work, fid)
    assert r["status"] == "done"
    ex = json.loads(r["extraction_json"])
    assert [m["member"] for m in ex["members"]] == ["Stellungnahmen/Kanton_Zürich.pdf",
                                                     "Stellungnahmen/Verband.xlsx"]
    assert {s["member"] for s in ex["skipped"]} == {"Stellungnahmen/kaputt.pdf",
                                                    "Stellungnahmen/liesmich.txt"}
    members = [p["source_member"] for p in _paras(work, fid)]
    assert members[0] == "Stellungnahmen/Kanton_Zürich.pdf"
    assert "Stellungnahmen/Verband.xlsx/Antworten" in members
    assert [p["para_order"] for p in _paras(work, fid)] == list(range(1, len(members) + 1))
    pdf_para = _paras(work, fid)[0]
    assert pdf_para["page_number"] == 1 and r["page_count"] == 1
    hit = work.execute("SELECT COUNT(*) FROM consultation_paragraphs_fts WHERE "
                       "consultation_paragraphs_fts MATCH 'Erziehungszeit'").fetchone()[0]
    assert hit >= 2


def test_zip_limits(work, monkeypatch):
    monkeypatch.setattr(bcc, "MAX_ZIP_MEMBERS", 2)
    fid = _add(work, "doc_21", "zip", code=21)
    data = _zip({f"{i}.pdf": _pdf([LONG]) for i in range(3)})
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: data}))
    r = _row(work, fid)
    assert r["status"] == "parse_error" and "members" in r["error"]


# ── empty, truncated, too large ────────────────────────────────────────


def test_zero_byte_file_is_empty_file_not_parse_error(work):
    fid = _add(work, "doc_1", "pdf-a", code=11, lang="it")
    _fetch(work, FakeHttp({_row(work, fid)["file_url"]: b""}))
    r = _row(work, fid)
    assert (r["status"], r["byte_size"]) == ("empty_file", 0)
    assert "zero-byte" in r["error"]
    assert bcc.ST_EMPTY in bcc.FINAL_STATUSES


def test_wayback_copy_cut_at_5_mib_is_truncated(work):
    snap = "https://web.archive.org/web/20260512030853/https://www.bj.admin.ch/dam/x/stgn.pdf"
    fid = _add(work, "doc_7", "pdf", code=21, url=snap, source="bj_wayback",
               original_url="https://www.bj.admin.ch/dam/x/stgn.pdf")
    full = _pdf([LONG], pages=3)
    cut = (full * (bcc.WAYBACK_TRUNCATED_BYTES // len(full) + 1))[:bcc.WAYBACK_TRUNCATED_BYTES]
    _fetch(work, FakeHttp({bcc.download_url(snap): cut}))
    r = _row(work, fid)
    assert r["status"] == "truncated" and "cut short" in r["error"]
    # a Fedlex PDF without pages is a parse error, not 'truncated'
    fid2 = _add(work, "doc_8", "pdf-a")
    _fetch(work, FakeHttp({_row(work, fid2)["file_url"]: b"%PDF-1.4\n%%EOF"}))
    assert _row(work, fid2)["status"] == "parse_error"


def test_file_above_the_memory_limit_is_streamed_and_deduped(work, monkeypatch, tmp_path):
    big = _pdf([LONG * 3])
    monkeypatch.setattr(bcc, "MAX_FILE_BYTES", len(big) - 1)
    de = _add(work, "doc_8", "pdf-a", code=21, lang="de")
    fr = _add(work, "doc_8", "pdf-a", code=21, lang="fr")
    files = {_row(work, de)["file_url"]: big, _row(work, fr)["file_url"]: big}
    http = FakeHttp(files)
    st = _fetch(work, http)
    assert st["by_status"] == {"done": 1, "same_as": 1}
    assert http.streams == [_row(work, de)["file_url"]]
    r = _row(work, de)
    assert r["byte_size"] == len(big) and r["page_count"] == 1
    assert r["sha256"] == __import__("hashlib").sha256(big).hexdigest()
    assert _row(work, fr)["same_as_file_id"] == de
    assert not list((tmp_path / "consultations_tmp").glob("*"))   # temp file removed
    # above the streaming limit: still too_large
    monkeypatch.setattr(bcc, "MAX_STREAM_BYTES", len(big) - 1)
    third = _add(work, "doc_9", "pdf-a", code=21)
    _fetch(work, FakeHttp({_row(work, third)["file_url"]: big}))
    assert _row(work, third)["status"] == "too_large"


def test_a_streamed_zip_is_read_from_disk(work, monkeypatch):
    # 2026-10-21: a 297 MB ZIP of Stellungnahmen filed under all three languages
    data = _zip({"A/Kanton Bern.pdf": _pdf([LONG * 2]), "A/Verband.pdf": _pdf([LONG + "Verband"])})
    monkeypatch.setattr(bcc, "MAX_FILE_BYTES", len(data) - 1)
    fid = _add(work, "doc_21", "zip", code=21)
    http = FakeHttp({_row(work, fid)["file_url"]: data})
    _fetch(work, http)
    r = _row(work, fid)
    assert http.streams and r["status"] == "done"
    assert [m["member"] for m in json.loads(r["extraction_json"])["members"]] == [
        "A/Kanton Bern.pdf", "A/Verband.pdf"]


# ── OCR ────────────────────────────────────────────────────────────────


class FakeOcr:
    def __init__(self, text: str = OCR_TEXT):
        self.text, self.calls = text, []

    def __call__(self, png: bytes, langs: str) -> str:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        self.calls.append(langs)
        return self.text


def _ocr(conn, http, fake, **kw):
    kw.setdefault("scopes", ("no_text", "partial"))
    return bcc.ocr(conn, http=http, image_to_text=fake, rate_limit=0, sleep=lambda s: None, **kw)


def test_ocr_a_scan_flags_every_paragraph(work):
    scan = _pdf(None, blank_pages=2)
    fid = _add(work, "doc_21", "pdf-a", code=21, lang="fr")
    http = FakeHttp({_row(work, fid)["file_url"]: scan})
    assert _fetch(work, http)["by_status"] == {"no_text": 1}
    assert _row(work, fid)["empty_pages"] == 2
    fake = FakeOcr()
    st = _ocr(work, http, fake, workers=2)
    assert (st["ocr_files"], st["ocr_pages"], st["now_done"]) == (1, 2, 1)
    assert fake.calls == ["fra+deu+ita"] * 2      # Stellungnahmen: every language, own first
    r = _row(work, fid)
    assert (r["status"], r["text_source"], r["ocr_pages"], r["empty_pages"]) == ("done", "ocr", 2, 0)
    assert r["ocr_engine"].startswith("tesseract ") and r["ocr_engine"].endswith("fra+deu+ita 300dpi")
    paras = _paras(work, fid)
    assert {p["page_number"] for p in paras} == {1, 2}
    assert all(p["text_source"] == "ocr" for p in paras)
    assert paras[0]["text"].startswith("Der Regierungsrat") and "Stellung und" in paras[0]["text"]
    # looked at once: a second pass has nothing to do
    assert _ocr(work, http, fake)["queued"] == 0


def test_ocr_partial_reads_only_the_empty_pages(work):
    mixed = _pdf([LONG * 2], pages=2, blank_pages=1)
    fid = _add(work, "doc_21", "pdf-a", code=12)
    http = FakeHttp({_row(work, fid)["file_url"]: mixed})
    _fetch(work, http)
    before = [dict(p) for p in _paras(work, fid)]
    assert _row(work, fid)["status"] == "done" and _row(work, fid)["empty_pages"] == 1
    fake = FakeOcr()
    assert _ocr(work, http, fake, scopes=("no_text",))["queued"] == 0   # partial: opt-in
    st = _ocr(work, http, fake, scopes=("partial",))
    assert st["ocr_pages"] == 1 and fake.calls == ["deu"]
    r = _row(work, fid)
    assert (r["text_source"], r["ocr_pages"], r["empty_pages"]) == ("mixed", 1, 0)
    after = _paras(work, fid)
    born = [p for p in after if p["text_source"] is None]
    assert [p["text"] for p in born] == [p["text"] for p in before]   # text layer untouched
    assert {p["page_number"] for p in after if p["text_source"] == "ocr"} == {3}


def test_ocr_that_finds_nothing_is_recorded_not_repeated(work):
    fid = _add(work, "doc_21", "pdf-a", code=12)
    http = FakeHttp({_row(work, fid)["file_url"]: _pdf(None, blank_pages=1)})
    _fetch(work, http)
    st = _ocr(work, http, FakeOcr(text="  \n"))
    assert st["nothing_recognised"] == 1
    r = _row(work, fid)
    assert r["status"] == "no_text" and r["ocr_at"] and "no text recognised" in r["error"]
    assert _ocr(work, http, FakeOcr())["queued"] == 0


def test_ocr_requeues_a_file_changed_upstream(work):
    fid = _add(work, "doc_21", "pdf-a", code=12)
    url = _row(work, fid)["file_url"]
    _fetch(work, FakeHttp({url: _pdf(None, blank_pages=1)}))
    st = _ocr(work, FakeHttp({url: _pdf(None, blank_pages=2)}), FakeOcr())
    assert st["requeued"] == 1
    r = _row(work, fid)
    assert r["status"] == "pending" and r["ocr_at"] is None and "changed upstream" in r["error"]


def test_ocr_budget_never_stores_half_a_file(work):
    fid = _add(work, "doc_21", "pdf-a", code=12)
    http = FakeHttp({_row(work, fid)["file_url"]: _pdf(None, blank_pages=3)})
    _fetch(work, http)
    ticks = iter([0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50])
    st = bcc.ocr(work, http=http, image_to_text=FakeOcr(), rate_limit=0, sleep=lambda s: None,
                 max_seconds=10, clock=lambda: next(ticks))
    assert st["stopped"] == "max_seconds"
    r = _row(work, fid)
    assert r["status"] == "no_text" and r["ocr_at"] is None and not _paras(work, fid)


def test_ocr_streams_a_large_file(work, monkeypatch):
    scan = _pdf(None, blank_pages=1)
    fid = _add(work, "doc_21", "pdf-a", code=12)
    http = FakeHttp({_row(work, fid)["file_url"]: scan})
    _fetch(work, http)
    monkeypatch.setattr(bcc, "MAX_FILE_BYTES", 100)
    _ocr(work, http, FakeOcr())
    assert http.streams and _row(work, fid)["text_source"] == "ocr"


def test_ocr_langs():
    assert bcc.ocr_langs("it", 12) == "ita"
    assert bcc.ocr_langs("de", 21) == "deu+fra+ita"
    assert bcc.ocr_langs("rm", 12) == "deu+fra+ita"


def test_ocr_text_blocks_split_on_blank_lines():
    assert bcc.ocr_text_blocks("a\nb\n\n \nc\n") == ["a\nb", "c"]


# ── requeue, status, completeness ──────────────────────────────────────


def test_requeue_counts_then_resets_and_logs(work):
    a = _add(work, "doc_3", "doc", status="unsupported_format")
    _add(work, "doc_4", "pptx", status="unsupported_format")          # still no extractor
    _add(work, "doc_5", "pdf", status="unsupported_format", error="host not allowed: x")
    b = _add(work, "doc_6", "pdf-a", status="too_large", byte_size=512_420_734)
    c = _add(work, "doc_7", "docx", status="parse_error", error="parse: No module named 'docx'")
    d = _add(work, "doc_8", "pdf", status="no_text", page_count=0, source="bj_wayback")
    _add(work, "doc_9", "pdf", status="no_text", page_count=4)          # a scan: the OCR pass
    dry = bcc.requeue(work, ["unsupported", "too_large", "parse_error", "no_pages"])
    assert dry["files"] == 4 and not dry["apply"]
    assert dry["selected"]["unsupported"]["by_source_format"] == {"fedlex/doc": 1}
    assert _row(work, a)["status"] == "unsupported_format"            # dry run changes nothing
    out = bcc.requeue(work, ["unsupported", "too_large", "parse_error", "no_pages"], apply=True)
    assert out["files"] == 4
    for fid in (a, b, c, d):
        r = _row(work, fid)
        assert (r["status"], r["attempts"], r["error"], r["byte_size"]) == ("pending", 0, None, None)
    log = json.loads(work.execute("SELECT value FROM meta WHERE key='requeue_log'").fetchone()[0])
    assert log[-1]["files"] == 4
    with pytest.raises(ValueError):
        bcc.requeue(work, ["everything"])


def test_requeue_refuses_while_the_unit_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(bcc, "_unit_busy", lambda units=bcc.GUARD_UNITS:
                        "opencaselaw-vernehmlassungen.service is active")
    db = tmp_path / "w.db"
    assert bcc.main(["requeue", "--work-db", str(db), "--what", "unsupported"]) == 3
    assert not db.exists()
    assert bcc.main(["requeue", "--work-db", str(db), "--what", "unsupported",
                     "--no-unit-check"]) == 0


def test_status_and_completeness_count_ocr_apart(work):
    _add(work, "doc_1", "pdf-a", status="done")
    _add(work, "doc_2", "pdf-a", status="done", text_source="ocr", ocr_pages=12,
         ocr_at="2026-10-21")
    _add(work, "doc_3", "pdf-a", status="done", text_source="mixed", ocr_pages=3,
         ocr_at="2026-10-21")
    _add(work, "doc_4", "pdf-a", status="no_text", page_count=40)
    _add(work, "doc_5", "pdf-a", status="done", empty_pages=7, page_count=90)
    _add(work, "doc_6", "pdf", status="no_text", page_count=2, source="bj", ocr_at="x")
    ts = bcc.text_source_summary(work)
    assert ts["files"] == {"document": 2, "ocr": 1, "mixed": 1} and ts["ocr_pages"] == 15
    assert ts["ocr_backlog"] == {"no_text_files": 1, "no_text_pages": 40,
                                 "partial_files": 1, "partial_empty_pages": 7}
    assert ts["no_text_after_ocr"] == 1
    sys.path.insert(0, str(REPO / "scripts"))
    import check_materialien_completeness as cmc
    fed = cmc._text_source(work, " AND source IS NULL")
    assert fed["files_with_text"] == {"document": 2, "ocr": 1, "mixed": 1}
    assert fed["no_text_after_ocr"] == 0
    assert cmc._text_source(work, " AND source IS NOT NULL")["no_text_after_ocr"] == 1


# ── server ─────────────────────────────────────────────────────────────


@pytest.fixture
def served(work, tmp_path, monkeypatch):
    born = _add(work, "doc_10", "pdf-a", code=12)
    scan = _add(work, "doc_21", "pdf-a", code=21)
    arch = _add(work, "doc_22", "zip", code=21, lang="fr")
    http = FakeHttp({_row(work, born)["file_url"]: _pdf(["Art. 23", LONG * 2]),
                     _row(work, scan)["file_url"]: _pdf(None, blank_pages=1),
                     _row(work, arch)["file_url"]: _zip({"Antworten/Kanton.pdf": _pdf(
                         ["Le canton approuve la modification des prestations de survivants "
                          "et demande une entrée en vigueur rapide de la révision proposée. " * 2])})})
    _fetch(work, http)
    _ocr(work, http, FakeOcr())
    work.execute("INSERT INTO consultation_laws (procedure_uri, work_uri, sr_number) "
                 "VALUES (?, 'https://fedlex.data.admin.ch/eli/cc/63/837_843_843', '831.10')", (P,))
    work.commit()
    out = bcc.publish(work, tmp_path / "consultations.db")
    monkeypatch.setenv("SWISS_CASELAW_CONSULTATIONS_DB", str(out))
    import mcp_server
    return mcp_server, out


def test_server_flags_ocr_passages_and_names_members(served):
    srv, _ = served
    out = srv._handle_search_botschaft(query="Witwenrente", doc_type="stellungnahmen")
    hit = out["results"][0]
    assert hit["text_source"] == "ocr" and hit["verbatim_quotation"] == "ocr"
    assert "OCR" in hit["ocr_note"] and hit["page"] == 1
    born = srv._handle_search_botschaft(query="Erziehungszeit", doc_type="erlaeuternder_bericht")
    assert born["results"] and "text_source" not in born["results"][0]
    member = srv._handle_search_botschaft(query="survivants", doc_type="stellungnahmen",
                                          language="fr")["results"][0]
    assert member["file_member"] == "Antworten/Kanton.pdf" and "text_source" not in member
    conn = srv._get_consultations_conn()
    try:
        procs = srv._consultations_for_sr(conn, "831.10", None)
    finally:
        conn.close()
    files = [f for d in procs[0]["documents"] for f in d["files"]]
    assert [f.get("text_source") for f in files].count("ocr") == 1
    # R2 in the server instructions names the flag
    assert "`text_source: ocr`" in (srv.server.instructions or "")


def test_server_reads_a_database_without_the_new_columns(served):
    srv, path = served
    c = sqlite3.connect(path)
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("DROP COLUMN needs SQLite 3.35")
    c.execute("ALTER TABLE consultation_paragraphs DROP COLUMN text_source")
    c.execute("ALTER TABLE consultation_paragraphs DROP COLUMN source_member")
    c.commit()
    c.close()
    out = srv._handle_search_botschaft(query="Witwenrente", doc_type="stellungnahmen")
    assert out["results"] and "text_source" not in out["results"][0]


# ── the real tools, where installed (the production host) ──────────────

import shutil  # noqa: E402


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("libreoffice")),
                    reason="LibreOffice not installed")
def test_real_soffice_reads_a_word_97_file():
    doc = bcc.soffice_convert(_docx(["Bundesgesetz über die Alters- und Hinterlassenenversicherung"]),
                              "docx", "doc")
    assert doc.startswith(bcc._OLE_MAGIC) and bcc.sniff_format(doc[:1024], "doc", whole=doc) == "doc"
    parts, info = bcc.extract_parts(doc, "doc")
    assert info["method"] == "libreoffice doc->docx"
    assert "Hinterlassenenversicherung" in " ".join(parts[0].pages[0])


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract not installed")
def test_real_tesseract_reads_a_rendered_page():
    d = fitz.open()
    d.new_page().insert_textbox(fitz.Rect(72, 72, 520, 300),
                                "Die Kantone begrüssen die Vorlage.", fontsize=20)
    png = d[0].get_pixmap(dpi=200, colorspace=fitz.csGRAY).tobytes("png")
    assert "Kantone" in bcc.tesseract_image_to_text(png, "deu")
    assert bcc.tesseract_version() != "unknown"
