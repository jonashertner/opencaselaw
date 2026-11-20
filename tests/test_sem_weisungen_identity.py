"""SEM Weisungen: renamed files must not become twin documents (user report
2026-11-20: 37 x / x_d pairs with identical text), and the language label
must follow the file / the text, not the DE index page it was linked from."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice.sem_weisungen import file_identity, text_language  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "repair_sem", REPO / "scripts" / "repair_sem_weisungen_twins.py")
repair_sem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair_sem)

BASE = "https://www.sem.admin.ch/dam/sem/de/data/rechtsgrundlagen/weisungen/weitere/bis-2003/"


def test_renamed_file_keeps_identity():
    old = file_identity(BASE + "20030601-rs-ch-fl.pdf.download.pdf/20030601-rs-ch-fl.pdf")
    new = file_identity(BASE + "20030601-rs-CH-FL-d.pdf.download.pdf/20030601-rs-CH-FL-d.pdf")
    assert old == ("20030601-rs-ch-fl", None)
    assert new == ("20030601-rs-ch-fl", "de")


def test_language_suffix_gives_language():
    assert file_identity(BASE + "x/weisungen-integration-f.pdf") == ("weisungen-integration", "fr")
    assert file_identity(BASE + "x/weisungen-integration-i.pdf") == ("weisungen-integration", "it")


def test_text_language():
    fr = "La présente directive est applicable pour les cantons et le SEM dans une procédure. " * 10
    it = "Il presente regolamento della SEM che per il cantone e una procedura nel diritto. " * 10
    de = "Die Weisung ist für die Kantone und das SEM nicht mit der Praxis auf. " * 10
    assert text_language(fr) == "fr"
    assert text_language(it) == "it"
    assert text_language(de) == "de"
    # A German handbook with some French form text stays German.
    assert text_language(de * 3 + fr) == "de"


def _rec(doc_id, url, h, scraped, body="Die Weisung ist für die Kantone und das SEM. "):
    return json.dumps({"doc_id": doc_id, "doc_number": url.rsplit("/", 1)[-1][:-4],
                       "pdf_url": url, "content_hash": h, "scraped_at": scraped,
                       "language": "de", "body_text": body * 5}) + "\n"


def test_repair_folds_twins_and_relabels():
    fr_body = "La présente directive est applicable pour les cantons et le SEM dans une procédure. " * 5
    lines = [
        _rec("sem_weisungen_20020708_rs_fza", BASE + "a/20020708-rs-fza.pdf", "h1", "2026-05-01"),
        _rec("sem_weisungen_20020708_rs_fza_d", BASE + "a/20020708-rs-fza-d.pdf", "h1", "2026-08-08"),
        _rec("sem_weisungen_weisung_x", BASE + "a/weisung-x.pdf", "h2", "2026-08-08", body=fr_body),
    ]
    out, st = repair_sem.repair(lines)
    rows = [json.loads(x) for x in out]
    assert [r["doc_id"] for r in rows] == ["sem_weisungen_20020708_rs_fza", "sem_weisungen_weisung_x"]
    assert rows[0]["pdf_url"].endswith("-d.pdf")          # newest scrape kept
    assert rows[1]["language"] == "fr"
    assert st["dropped"] == ["sem_weisungen_20020708_rs_fza"]   # the older line
    assert st["lines_out"] == 2
    assert st["text_differs"] == []
