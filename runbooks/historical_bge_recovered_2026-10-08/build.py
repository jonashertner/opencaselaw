#!/usr/bin/env python3
"""
build.py — how the texts in this directory were made (2026-10-08). Kept for
review and reproduction; it needs the published dataset and the DFR scans, so
no test runs it.

Five rulings withheld by source_defects.py are recovered from DFR material that
was already at hand (runbooks/historical_bge_source_errors_2026-10-07.md,
"Recovery from neighbouring scans"):

  52 I 23, 39, 149  pages printed in the neighbouring rulings' DFR scans, read
                    from those rows' text (the published dataset): the part
                    bge_historical_segment.segment cuts off a neighbour as not
                    its own is this ruling's first or last page(s). Partial.
  39 I 469          No 83, on the last spread of c1039465.pdf (p. 469) and the
                    first of c1039471.pdf (pp. 470-471). Neither text layer has
                    it, so it was read by OCR: Tesseract 5.3.4, model "fra"
                    (the ruling is French, set in Antiqua; the scraper's Fraktur
                    model read the headnote's "Art. 69 ch. 3" as "Art. 89 ch. 8"),
                    400 dpi, one page per crop, --psm 4. Not proofread.
  22 I 12           its own scan c1022012.pdf (the index linked an HTML page
                    holding 22 I 1012), read through the scraper's PDF path.

No text is edited by hand. A missing page range is marked in the text.

Usage (paths to the dataset parquet, the scans and the OCR output):
  python3 build.py DATASET.parquet SCAN_DIR OCR_DIR
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

import bge_historical_segment as seg
from base_scraper import BaseScraper
from models import detect_language
from scrapers.bge_historical import _extract_pdf_text

DFR = "https://www.fallrecht.ch/"


def gap(first: int, last: int) -> str:
    pages = f"Page {first}" if first == last else f"Pages {first}-{last}"
    verb = "is" if first == last else "are"
    return f"\n\n[{pages} {verb} missing from the source.]\n\n"


def main(dataset: str, scan_dir: str, ocr_dir: str) -> None:
    import pyarrow.parquet as pq

    ids = ["bge_52_I_14", "bge_52_I_27", "bge_52_I_34", "bge_52_I_44", "bge_52_I_145"]
    rows = pq.read_table(dataset, columns=["decision_id", "full_text"],
                         filters=[("decision_id", "in", ids)]).to_pylist()
    text = {r["decision_id"]: r["full_text"] for r in rows}

    def cut_off(did: str) -> tuple[str, str]:
        """(the part before the row's own ruling, the part after it)."""
        t = text[did]
        s = seg.segment(t, int(did.rsplit("_", 1)[1]))
        assert s is not None, did
        return t[:s.start], t[s.end:]

    def until(piece: str, heading: str) -> str:
        """The piece up to the next ruling's section heading."""
        i = piece.index(heading)
        return piece[:i]

    texts: dict[str, str] = {}
    texts["52_I_23"] = (cut_off("bge_52_I_14")[1] + gap(24, 25)
                        + until(cut_off("bge_52_I_27")[0], "\nIV. GERICHTSSTAND"))
    texts["52_I_39"] = (cut_off("bge_52_I_34")[1] + gap(40, 43)
                        + until(cut_off("bge_52_I_44")[0], "\nVI. BESTEUERUNGSGRUNDS"))
    texts["52_I_149"] = cut_off("bge_52_I_145")[1] + gap(150, 153)

    ocr = Path(ocr_dir)
    p469, p470, p471 = ((ocr / f"r{p}.txt").read_text(encoding="utf-8") for p in (469, 470, 471))
    start = re.search(r"(?m)^83\.\s+Arr", p469).start()
    end = p471.index("Le recours est écarté.") + len("Le recours est écarté.")
    texts["39_I_469"] = p469[start:] + "\n" + p470 + "\n" + p471[:end]

    pdf = (Path(scan_dir) / "c1022012.pdf").read_bytes()
    full = BaseScraper.clean_text(_extract_pdf_text(pdf))
    texts["22_I_12"], s = seg.own_text(full, 12)
    assert s is not None and s.serial == 4

    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    for e in manifest["recoveries"]:
        key = e["docket_number"]
        t = BaseScraper.clean_text(texts[key])
        (HERE / e["file"]).write_text(t + "\n", encoding="utf-8")
        e["text_sha256"] = hashlib.sha256(t.encode("utf-8")).hexdigest()
        e["language"] = detect_language(t)
        e["chars"] = len(t)
    for name in ("c1022012.pdf", "c1039465.pdf", "c1039471.pdf"):
        manifest["scan_sha256"][DFR + name] = hashlib.sha256(
            (Path(scan_dir) / name).read_bytes()).hexdigest()
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
