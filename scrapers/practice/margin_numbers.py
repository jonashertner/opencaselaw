"""
Margin numbers (Randziffern) back next to the paragraph they number.
=====================================================================

Two layouts lost the Rz → paragraph link in plain PDF text extraction
(user report 2026-11-20):

* BSV Weisungen / Kreisschreiben print the Rz ("2009.1") and the amendment
  mark ("1/26") as text in a LEFT margin column. The PDF stores that column
  as its own text blocks after the body, so plain extraction put every
  page's numbers in a block at the page end.

* FINMA Rundschreiben since 2019 print the Rz in the RIGHT margin as tiny
  bitmaps (a 5x7-pixel glyph image per number, drawn through a soft mask),
  not as text: plain extraction dropped them entirely. The glyphs are too
  small for OCR to read reliably (best tesseract setting: 57 of 105 right
  on FINMA-RS 2019/1). They are numbered by position instead: the n-th
  margin image is Rz n. That is accepted only when the document proves it:
  the image widths must step from one to two to three digits exactly at
  Rz 10 and Rz 100, and the highest Rz the circular's own table of contents
  lists must equal the number of images. A circular with an inserted
  "Rz 10.1", a renumbered or a dropped Rz fails one of the two checks and
  keeps its text without markers: a missing Rz is a gap, a wrong one is a
  citation error.

The page text is rebuilt line by line from PyMuPDF's dict output, which is
byte-identical to `page.get_text()` (checked on both layouts), with a line
"Rz <n>" (plus " (<mark>)" for an amendment mark) inserted before the body
line the number stands next to. Pages without margin numbers come out
unchanged.
"""
from __future__ import annotations

import logging
import re
import statistics

logger = logging.getLogger(__name__)

# FINMA marks an amended Rz with an asterisk ("16*"); it stays in the marker.
_RZ_TEXT = re.compile(r"^\d{1,5}(?:\.\d{1,3})*[a-z]?\*?$")
_AMEND_MARK = re.compile(r"^\d{1,2}/\d{2}$")
_RZ_OPEN = re.compile(r"^\d{1,5}(?:\.\d{1,3})*[a-z]?\*?\s*[-–]$")   # "2023.1-": a range's first line
_LEADERS = re.compile(r"\.{4,}|…{2,}")


def _page_lines(page) -> list[dict]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            text = "".join(s["text"] for s in ln["spans"])
            sizes = [s["size"] for s in ln["spans"] if s["text"].strip()]
            out.append({
                "text": text,
                "x0": ln["bbox"][0], "y0": ln["bbox"][1],
                "x1": ln["bbox"][2], "y1": ln["bbox"][3],
                "size": max(sizes) if sizes else 0.0,
            })
    return out


def _body_extent(lines: list[dict]) -> tuple[float, float, float] | None:
    """(left edge, right edge, font size) of the body column, from lines long
    enough to be running text."""
    long_ = [ln for ln in lines if len(ln["text"].strip()) >= 25]
    if len(long_) < 3:
        return None
    left = statistics.mode(round(ln["x0"]) for ln in long_)
    rights = sorted(ln["x1"] for ln in long_)
    right = rights[int(0.95 * (len(rights) - 1))]
    size = statistics.median(ln["size"] for ln in long_)
    return float(left), float(right), float(size)


def _target(lines: list[dict], body_idx: list[int], y0: float, y1: float) -> int | None:
    """Index of the body line a margin number at (y0, y1) stands next to:
    the line whose vertical middle is closest, within half a line height;
    else the first body line starting just below it."""
    mid = (y0 + y1) / 2
    best, best_d = None, None
    for i in body_idx:
        ln = lines[i]
        d = abs((ln["y0"] + ln["y1"]) / 2 - mid)
        if d <= max(3.0, 0.6 * (ln["y1"] - ln["y0"])) and (best_d is None or d < best_d):
            best, best_d = i, d
    if best is not None:
        return best
    below = [i for i in body_idx if lines[i]["y0"] >= y0 - 3 and lines[i]["y0"] - y0 <= 20]
    return min(below, key=lambda i: lines[i]["y0"]) if below else None


def _render(lines: list[dict], skip: set[int], before: dict[int, list[str]]) -> str:
    out = []
    for i, ln in enumerate(lines):
        for marker in before.get(i, ()):
            out.append(marker + "\n")
        if i not in skip:
            out.append(ln["text"] + "\n")
    return "".join(out)


_TOC_HEAD = re.compile(r"^(?:Inhaltsverzeichnis|Inhalt|Table des matières|Sommaire|Indice"
                       r"|Contents|Table of contents)\b", re.IGNORECASE)


_ANNEX_HEAD = re.compile(r"^(Anhang|Annexe|Allegato|Annex|Appendice|Appendix)\s*(\d{1,2}|[IVX]{1,5})?\b",
                         re.IGNORECASE)


def _annex_label(lines: list[dict]) -> str | None:
    """"Anhang 2" when one of the page's first lines heads an annex."""
    head = [ln["text"].strip() for ln in sorted(lines, key=lambda ln: ln["y0"])
            if ln["text"].strip()][:8]
    for t in head:
        m = _ANNEX_HEAD.match(t)
        if m:
            return f"{m.group(1)} {m.group(2)}" if m.group(2) else m.group(1)
    return None


def _rz_key(t: str) -> tuple:
    """Sort key of a margin number: "2009.1" -> (2009, 1), "12a" -> (12, 0, 1)."""
    m = re.fullmatch(r"([\d.]+?)([a-z]?)", t.strip().rstrip("*"))
    nums = tuple(int(x) for x in m.group(1).split(".") if x)
    return nums + ((0, ord(m.group(2)) - 96) if m.group(2) else ())


# The label a TOC prints over or beside its margin-number column: Rz (de),
# Cm (fr, "chiffre marginal"), nm. (it), N / Rn, margin no. (en).
_RZ_LABEL = re.compile(r"(?:Rz|Rn|Cm|Nm|N|Cifra|Cifre|marg|margin nos?)\.?", re.IGNORECASE)


def _is_rz_label(t: str) -> bool:
    return bool(_RZ_LABEL.fullmatch(t.strip()))


def _is_toc_page(lines: list[dict]) -> bool:
    return (sum(1 for ln in lines if _LEADERS.search(ln["text"])) >= 3
            or sum(1 for ln in lines if _is_rz_label(ln["text"])) >= 3
            or any(_TOC_HEAD.match(ln["text"].strip()) for ln in lines))


def _text_margin_plan(lines: list[dict], doc_size: float = 0.0,
                      page_count: int = 0, page_height: float = 0.0) -> dict | None:
    """Where a page's text margin numbers (BSV layout, left or right) go.
    None when the page has none, is a table of contents, or its numbers do
    not rise strictly down the page (a column of figures, not Rz).

    `doc_size` is the document's running-text font size: footnote numbers
    sit in the same margin column, but in footnote type and next to
    footnote-size text, so both must be near body size. `page_count` keeps
    a "18/88" page number from passing as an amendment mark."""
    ext = _body_extent(lines)
    if ext is None or _is_toc_page(lines):
        return None
    left, right, size = ext
    size = max(size, doc_size)

    def is_mark(t: str) -> bool:
        return bool(_AMEND_MARK.match(t)) and int(t.split("/")[1]) != page_count

    margin, body_idx = [], []
    for i, ln in enumerate(lines):
        t = ln["text"].strip()
        in_margin = ln["x1"] <= left - 4 or ln["x0"] >= right + 4
        # FINMA prints the page number in the margin column's footer
        in_footer = bool(page_height) and (ln["y0"] > 0.93 * page_height
                                           or ln["y1"] < 0.06 * page_height)
        if (in_margin and not in_footer and t
                and (_RZ_TEXT.match(t) or is_mark(t) or _RZ_OPEN.match(t))
                and ln["size"] >= 0.6 * size):
            margin.append(i)
        elif t:
            body_idx.append(i)
    # A range printed over two lines ("2023.1-" above "2023.4"): one label.
    label_of: dict[int, str] = {}
    joined: set[int] = set()
    for i in sorted(margin, key=lambda k: lines[k]["y0"]):
        t = lines[i]["text"].strip()
        if _RZ_OPEN.match(t):
            nxt = [j for j in margin if j != i and _RZ_TEXT.match(lines[j]["text"].strip())
                   and 0 < lines[j]["y0"] - lines[i]["y0"] <= 25
                   and abs(lines[j]["x0"] - lines[i]["x0"]) < 20]
            if not nxt:
                return None
            j = min(nxt, key=lambda k: lines[k]["y0"])
            label_of[i] = f"{t.rstrip('-–').strip()}–{lines[j]['text'].strip()}"
            joined.add(j)
    rz = sorted((i for i in margin if i not in joined and (
                    _RZ_TEXT.match(lines[i]["text"].strip()) or i in label_of)),
                key=lambda i: lines[i]["y0"])
    if not rz:
        return None
    for i in rz:
        label_of.setdefault(i, lines[i]["text"].strip())
    keys = [_rz_key(label_of[i].split("–")[0]) for i in rz]
    last_key = _rz_key(label_of[rz[-1]].split("–")[-1])
    if any(b <= a for a, b in zip(keys, keys[1:])):
        return None
    # amendment marks belong to the nearest Rz above them in the same column
    marks: dict[int, list[int]] = {}
    for i in margin:
        if is_mark(lines[i]["text"].strip()):
            above = [j for j in rz if lines[j]["y0"] < lines[i]["y0"]
                     and abs(lines[j]["x0"] - lines[i]["x0"]) < 20
                     and lines[i]["y0"] - lines[j]["y0"] < 60]
            if above:
                marks.setdefault(max(above, key=lambda j: lines[j]["y0"]), []).append(i)
    before: dict[int, list[str]] = {}
    skip: set[int] = set()
    for i in rz:
        tgt = _target(lines, body_idx, lines[i]["y0"], lines[i]["y1"])
        if tgt is None:
            return None                   # a number we cannot place: page as it was
        if lines[tgt]["size"] < 0.85 * size:
            return None                   # footnote-size text: footnote numbers
        ms = marks.get(i, [])
        label = ", ".join(lines[j]["text"].strip() for j in ms)
        before.setdefault(tgt, []).append(
            f"Rz {label_of[i]}" + (f" ({label})" if label else ""))
        skip.add(i)
        skip.update(ms)
    skip.update(joined)
    return {"before": before, "skip": skip, "first": keys[0], "last": last_key,
            "count": len(rz), "annex": _annex_label(lines)}


def _rising_chain(plans: list[tuple[int, dict]]) -> set[int]:
    """Pages whose numbers continue each other: the largest set (by number
    of Rz) in which every page starts above the previous page's last Rz, or
    starts again at Rz 1 under an annex heading (FINMA annexes number
    their own Rz)."""
    n = len(plans)
    best = [p["count"] for _, p in plans]
    prev = [-1] * n
    for j in range(n):
        for i in range(j):
            fits = (plans[i][1]["last"] < plans[j][1]["first"]
                    # an annex starts its own Rz 1
                    or (plans[j][1]["first"] == (1,) and plans[j][1]["annex"]))
            if fits and best[i] + plans[j][1]["count"] > best[j]:
                best[j] = best[i] + plans[j][1]["count"]
                prev[j] = i
    if not n:
        return set()
    j = max(range(n), key=lambda k: best[k])
    chosen = set()
    while j != -1:
        chosen.add(plans[j][0])
        j = prev[j]
    return chosen


# ── FINMA: margin numbers as glyph images ─────────────────────────────

def fitz_rect(bbox):
    import fitz
    return fitz.Rect(bbox)


def _margin_images(doc) -> list[tuple[int, object, int]]:
    """(page index, rect, pixel width) of every small soft-masked image in
    the right margin, in reading order. The margin is the outer fifth of
    the page (FINMA: x0 ~520 of 595), and the numbers are right-aligned, so
    a candidate must end within a few points of the column's common right
    edge; a stray logo or bullet image elsewhere does not count."""
    cands = []
    for pno, page in enumerate(doc):
        if not any(img[1] for img in page.get_images(full=True)):
            continue                      # glyph images carry a soft mask
        # get_image_info, not get_image_rects: the glyph images share one
        # all-black base image, and get_image_rects matches by its digest,
        # returning every glyph's rect for each of them.
        seen = set()
        for info in page.get_image_info():
            w, h = info["width"], info["height"]
            r = fitz_rect(info["bbox"])
            key = (round(r.x0, 1), round(r.y0, 1))
            if (h > 12 or w > 30 or r.height > 12 or key in seen
                    or r.x0 < 0.8 * page.rect.width):
                continue
            seen.add(key)
            cands.append((pno, r, w))
    if not cands:
        return []
    edge = statistics.median(r.x1 for _, r, _ in cands)
    found = [c for c in cands if abs(c[1].x1 - edge) <= 4]
    found.sort(key=lambda t: (t[0], t[1].y0))
    return found


def _width_steps_ok(widths: list[int]) -> bool:
    """Image widths must grow with the digit count exactly at Rz 10 / 100."""
    def digits(n):
        return len(str(n))
    by = {}
    for n, w in enumerate(widths, 1):
        by.setdefault(digits(n), []).append(w)
    keys = sorted(by)
    for a, b in zip(keys, keys[1:]):
        if max(by[a]) >= min(by[b]):
            return False
    return True


def _toc_max_rz(doc, first_rz_page: int) -> int | None:
    """Highest Rz number the table of contents lists (the number column
    of the pages before the first numbered page)."""
    nums = []
    seen_rz = False
    for pno in range(min(first_rz_page, 6)):
        for ln in _page_lines(doc[pno]):
            t = ln["text"].strip()
            if _is_rz_label(t) or _is_rz_label(t.split(" ", 1)[0]):
                seen_rz = True
            m = re.fullmatch(r"(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?", t)
            if m:
                nums.append(int(m.group(2) or m.group(1)))
    if not seen_rz or not nums:
        return None
    return max(nums)


def _image_margin_doc(doc) -> tuple[list[str], int] | None:
    imgs = _margin_images(doc)
    if len(imgs) < 3:
        return None
    widths = [w for _, _, w in imgs]
    toc_max = _toc_max_rz(doc, imgs[0][0])
    if not _width_steps_ok(widths) or (len(imgs) >= 10 and toc_max is None) \
            or (toc_max is not None and toc_max != len(imgs)):
        logger.info("margin-number images not provably sequential "
                    "(images %d, toc max %s, width steps %s): text left without Rz",
                    len(imgs), toc_max, _width_steps_ok(widths))
        return None
    by_page: dict[int, list[tuple[int, object]]] = {}
    for n, (pno, rect, _) in enumerate(imgs, 1):
        by_page.setdefault(pno, []).append((n, rect))
    pages = []
    placed = 0
    for pno, page in enumerate(doc):
        lines = _page_lines(page)
        if pno not in by_page:
            pages.append(_render(lines, set(), {}))
            continue
        body_idx = [i for i, ln in enumerate(lines) if ln["text"].strip()]
        before: dict[int, list[str]] = {}
        for n, rect in by_page[pno]:
            tgt = _target(lines, body_idx, rect.y0, rect.y1)
            if tgt is None:
                return None          # a number we cannot place: no partial Rz
            before.setdefault(tgt, []).append(f"Rz {n}")
            placed += 1
        pages.append(_render(lines, set(), before))
    return pages, placed


def extract_with_margin_numbers(pdf_bytes: bytes, *, images: bool = False) -> tuple[str, int] | None:
    """Page text with Rz markers, joined like base.extract_pdf_text, and the
    number of markers placed. None when the PDF has no margin numbers this
    module can place (the caller keeps its plain extraction)."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # pragma: no cover - fitz missing / broken PDF
        logger.debug("margin numbers: cannot open PDF: %s", e)
        return None
    if images:
        res = _image_margin_doc(doc)
        if res is not None:
            pages, placed = res
            return "\n\n".join(pages).strip(), placed
    all_lines = [_page_lines(page) for page in doc]
    running = [ln["size"] for lines in all_lines for ln in lines
               if len(ln["text"].strip()) >= 25]
    doc_size = statistics.median(running) if running else 0.0
    plans = [(pno, plan) for pno, lines in enumerate(all_lines)
             if (plan := _text_margin_plan(lines, doc_size, len(all_lines),
                                           doc[pno].rect.height)) is not None]
    chosen = _rising_chain(plans)
    plan_of = dict(plans)
    pages, placed = [], 0
    annex, last = None, None
    for pno, lines in enumerate(all_lines):
        plan = plan_of.get(pno) if pno in chosen else None
        if plan is None:
            pages.append(_render(lines, set(), {}))
            continue
        if last is not None and plan["first"] <= last:
            annex = plan["annex"]          # restart: the annex's own numbering
        last = plan["last"]
        before = plan["before"]
        if annex:
            before = {k: [f"{annex}, {m}" for m in v] for k, v in before.items()}
        pages.append(_render(lines, plan["skip"], before))
        placed += plan["count"]
    if placed < 3:
        return None
    return "\n\n".join(pages).strip(), placed


class MarginNumbersMixin:
    """Mix into a PracticeScraper subclass (before it in the MRO): the body
    text carries "Rz <n>" before each numbered paragraph. OCR'd scans and
    PDFs this module cannot place keep the plain extraction."""

    MARGIN_NUMBER_IMAGES = False      # FINMA: Rz drawn as glyph images

    def _extract(self, pdf_bytes: bytes) -> str:
        text = super()._extract(pdf_bytes)
        if getattr(self, "_last_ocr", False) or not text.strip():
            return text
        try:
            res = extract_with_margin_numbers(pdf_bytes, images=self.MARGIN_NUMBER_IMAGES)
        except Exception as e:      # never lose a document over the Rz pass
            logger.warning("margin numbers failed, plain text kept: %s", e)
            return text
        return res[0] if res else text
