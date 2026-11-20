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
_RZ_TEXT = re.compile(r"^\d{1,5}(?:\.\d{1,3})*[a-z]?(?: ?\*)?$")   # also "319 *" (2008/19)
_AMEND_MARK = re.compile(r"^\d{1,2}/\d{2}$")
_RZ_OPEN = re.compile(r"^\d{1,5}(?:\.\d{1,3})*[a-z]?(?: ?\*)?\s*[-–]$")   # "2023.1-": a range's first line
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


def _body_extent(lines: list[dict], doc_size: float = 0.0) -> tuple[float, float, float] | None:
    """(left edge, right edge, font size) of the body column, from lines long
    enough to be running text, in body type: footnotes may run wider than
    the body, into the margin column (FINMA-RS 2013/3)."""
    long_ = [ln for ln in lines if len(ln["text"].strip()) >= 25]
    if len(long_) < 3:
        return None
    ref = doc_size or statistics.median(ln["size"] for ln in long_)
    body = [ln for ln in long_ if ln["size"] >= 0.9 * ref]
    if len(body) >= 3:
        long_ = body
    left = statistics.mode(round(ln["x0"]) for ln in long_)
    rights = sorted(ln["x1"] for ln in long_)
    right = rights[int(0.95 * (len(rights) - 1))]
    size = statistics.median(ln["size"] for ln in long_)
    return float(left), float(right), float(size)


def _target(lines: list[dict], body_idx: list[int], y0: float, y1: float) -> int | None:
    """Index of the body line a margin number at (y0, y1) stands next to:
    the line whose vertical middle is closest, within half a line height
    (of several lines on that row, the one rendered first: a section
    number "3.1" before its heading); else the first body line starting
    just below it."""
    mid = (y0 + y1) / 2
    near = []
    for i in body_idx:
        ln = lines[i]
        d = abs((ln["y0"] + ln["y1"]) / 2 - mid)
        if d <= max(3.0, 0.6 * (ln["y1"] - ln["y0"])):
            near.append((d, i))
    if near:
        best_d = min(d for d, _ in near)
        return min(i for d, i in near if d <= best_d + 1.5)
    below = [i for i in body_idx if lines[i]["y0"] >= y0 - 3 and lines[i]["y0"] - y0 <= 20]
    return min(below, key=lambda i: lines[i]["y0"]) if below else None


def _render(lines: list[dict], skip: set[int], before: dict[int, list[str]]) -> str:
    out = []
    for i, ln in enumerate(lines):
        for marker in before.get(i, ()):
            out.append(marker + "\n")
        if i not in skip:
            out.append(ln["text"] + "\n")
    for marker in before.get(len(lines), ()):
        out.append(marker + "\n")
    return "".join(out)


# A table of contents' heading. "Inhalt", "Indice", "Sommaire" and
# "Contents" are ordinary words too ("Indice, il faut ..." in fr body text,
# an "Inhalt" column head over a table of Rz): they count only as a line
# of their own.
_TOC_HEAD = re.compile(r"(?:Inhaltsverzeichnis|Table des matières|Table of contents)\b.*"
                       r"|(?:Inhalt|Indice|Sommaire|Contents)\s*:?", re.IGNORECASE)


def _toc_heading(lines: list[dict]) -> bool:
    return any(_TOC_HEAD.fullmatch(ln["text"].strip()) for ln in lines)


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


def _rz_text(t: str) -> str:
    """A margin number as the marker prints it: "319 *" -> "319*"."""
    return re.sub(r"\s+\*", "*", t.strip())


def _rz_key(t: str) -> tuple:
    """Sort key of a margin number: "2009.1" -> (2009, 1), "12a" -> (12, 0, 1)."""
    m = re.fullmatch(r"([\d.]+?)([a-z]?)", t.strip().rstrip("*").strip())
    nums = tuple(int(x) for x in m.group(1).split(".") if x)
    return nums + ((0, ord(m.group(2)) - 96) if m.group(2) else ())


# The label a TOC prints over or beside its margin-number column: Rz (de),
# Cm (fr, "chiffre marginal"), nm. (it), N / Rn, margin no. (en).
_RZ_LABEL = re.compile(r"(?:Rz|Rn|Cm|Nm|N|Cifra|Cifre|marg|margin nos?)\.?", re.IGNORECASE)


def _is_rz_label(t: str) -> bool:
    return bool(_RZ_LABEL.fullmatch(t.strip()))


def _is_toc_page(lines: list[dict]) -> bool:
    return (sum(1 for ln in lines if _LEADERS.search(ln["text"])) >= 3
            or sum(1 for ln in lines if _is_rz_label(ln["text"])) >= 3)


def _aligned(a: dict, b: dict) -> bool:
    """Two margin lines in one column: they overlap horizontally or share
    a left or right edge."""
    return (min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) > -2
            or abs(a["x0"] - b["x0"]) <= 8 or abs(a["x1"] - b["x1"]) <= 8)


def _columns(lines: list[dict], idx: list[int], right: float) -> list[list[int]]:
    """Margin lines grouped into columns. FINMA balance-sheet pages print
    section numbers ("3.1") in the left margin and the Rz in the right one;
    read as one column they never rise, and the whole page was refused."""
    cols: list[tuple[bool, list[int]]] = []
    for i in sorted(idx, key=lambda k: lines[k]["x0"]):
        side = lines[i]["x0"] >= right + 4
        for s, members in cols:
            if s == side and any(_aligned(lines[i], lines[j]) for j in members):
                members.append(i)
                break
        else:
            cols.append((side, [i]))
    return [members for _, members in cols]


def _text_margin_plans(lines: list[dict], doc_size: float = 0.0,
                       page_count: int = 0, page_height: float = 0.0,
                       page_width: float = 0.0) -> list[dict]:
    """Where a page's text margin numbers (BSV layout, left or right) go:
    one plan per margin column whose numbers can be Rz. Empty when the page
    has none or is a table of contents; a column whose numbers do not rise
    strictly down the page (a column of figures, not Rz) has no plan.

    `doc_size` is the document's running-text font size: footnote numbers
    sit in the same margin column, but in footnote type and next to
    footnote-size text, so both must be near body size. `page_count` keeps
    a "18/88" page number from passing as an amendment mark."""
    ext = _body_extent(lines, doc_size)
    if ext is None or _is_toc_page(lines):
        return []
    left, right, page_size = ext
    # The document's body size: a page of "Abrogato" lines under long
    # headings measures its headings (FINMA-RS 2010/2 it).
    size = doc_size or page_size

    def is_mark(t: str) -> bool:
        return bool(_AMEND_MARK.match(t)) and int(t.split("/")[1]) != page_count

    margin = []
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
    toc_head = _toc_heading(lines)
    plans = []
    for col in _columns(lines, margin, right):
        plan = _column_plan(lines, col, size, is_mark, page_height)
        if plan is None:
            continue
        # A table of contents under its heading lists section starts, with
        # gaps; a page of Rz under an "Inhalt" column head has none.
        if toc_head and not plan["consecutive"]:
            continue
        side = "right" if lines[col[0]]["x0"] >= right + 4 else "left"
        plan.update({"annex": _annex_label(lines), "w": round(page_width), "side": side,
                     "gap": plan["x"] - right if side == "right" else left - plan["x"]})
        plans.append(plan)
    return plans


def _column_plan(lines: list[dict], margin: list[int], size: float, is_mark,
                 page_height: float) -> dict | None:
    """Plan for one margin column; the page's other lines are body text."""
    in_col = set(margin)
    # No target: a 72 pt "aufgehoben" stamp laid over repealed paragraphs
    # (FINMA) shares their rows but is drawn first, at the page's top.
    body_idx = [i for i, ln in enumerate(lines) if i not in in_col and ln["text"].strip()
                and ln["size"] <= 2.5 * size]
    big_idx = [i for i in body_idx if lines[i]["size"] >= 0.85 * size]
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
            label_of[i] = f"{_rz_text(t.rstrip('-–'))}–{_rz_text(lines[j]['text'])}"
            joined.add(j)
    rz = sorted((i for i in margin if i not in joined and (
                    _RZ_TEXT.match(lines[i]["text"].strip()) or i in label_of)),
                key=lambda i: lines[i]["y0"])
    if not rz:
        return None
    for i in rz:
        label_of.setdefault(i, _rz_text(lines[i]["text"]))
    keys = [_rz_key(label_of[i].split("–")[0]) for i in rz]
    last_key = _rz_key(label_of[rz[-1]].split("–")[-1])
    if any(b <= a for a, b in zip(keys, keys[1:])):
        return None
    # Rz on a page mostly follow on from each other; the page numbers of a
    # journal's table of contents rise in jumps (AHI-Praxis: 153, 154, 155,
    # 159, 161, 162, 165, 168). Inserted Rz may skip a little where the
    # integer ones are glyph images (FINMA-RS 2015/2 it: 224.1*, 226.1*);
    # page numbers have no ".1".
    def follows(a: tuple, b: tuple) -> bool:
        return _next_rz(a, b) or ((len(a) > 1 or len(b) > 1) and b[0] - a[0] <= 2)
    if len(keys) >= 3 and 2 * sum(follows(a, b) for a, b in zip(keys, keys[1:])) < len(keys) - 1:
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
        y0, y1 = lines[i]["y0"], lines[i]["y1"]
        # Body-size text first: a formula's subscript beside an Rz is no
        # footnote (FINMA-RS 2017/7, Rz 87 next to "1/2").
        tgt = _target(lines, big_idx, y0, y1)
        if tgt is None:
            if _target(lines, body_idx, y0, y1) is not None:
                return None               # only footnote-size text beside it: footnote numbers
            if i != rz[-1] or any(lines[k]["y0"] > y0 and not (
                    page_height and lines[k]["y0"] > 0.93 * page_height) for k in body_idx):
                return None               # a number we cannot place: page as it was
            # The page's last Rz with nothing below it: its paragraph starts
            # on the next page (FINMA-RS 2020/1, Rz 89).
            above = [k for k in body_idx if lines[k]["y0"] < y0]
            tgt = max(above) + 1 if above else len(lines)
        ms = marks.get(i, [])
        label = ", ".join(lines[j]["text"].strip() for j in ms)
        before.setdefault(tgt, []).append(
            f"Rz {label_of[i]}" + (f" ({label})" if label else ""))
        skip.add(i)
        skip.update(ms)
    skip.update(joined)
    return {"before": before, "skip": skip, "first": keys[0], "last": last_key,
            "count": len(rz), "x": statistics.median(lines[i]["x1"] for i in rz),
            "consecutive": all(_next_rz(a, b) for a, b in zip(keys, keys[1:]))}


def _next_rz(a: tuple, b: tuple) -> bool:
    """b is the Rz right after a: 12 -> 13, 12 -> 12a / 12.1, 12.1 -> 12.2,
    12.3 -> 13, 1002.1 -> 1003."""
    if len(b) > len(a) and b[:len(a)] == a:
        return True
    return any(b == a[:k - 1] + (a[k - 1] + 1,) for k in range(1, len(a) + 1))


def _dense(plan: dict) -> bool:
    """Running Rz leave few gaps on a page; table row numbers jump."""
    span = plan["last"][0] - plan["first"][0] + 1
    return plan["count"] >= 0.5 * span


def _same_column(a: dict, b: dict) -> bool:
    """Same margin column: same side of the body text, and within 30 pt on
    pages of the same width. Landscape pages move the column (FINMA-RS
    2018/2: x 547 portrait, 722 landscape) but keep its distance from the
    body text, give or take the body's ragged edge (2018/2: 49 pt portrait,
    18-24 pt landscape); a landscape table's cells stand far off it
    (FINMA-RS 2016/1 Anhang 1: 184 pt)."""
    if a["side"] != b["side"]:
        return False
    if a["w"] == b["w"]:
        return abs(a["x"] - b["x"]) <= 30
    return abs(a["gap"] - b["gap"]) <= 60


def _chain_in_column(plans: list[tuple[int, dict]], anchor: dict) -> tuple[int, list[tuple[int, dict]]]:
    """The largest run (by number of Rz) of pages in the anchor's column in
    which every page starts above the previous page's last Rz, or starts
    again at Rz 1 under an annex heading (FINMA annexes number their own
    Rz). One column per page."""
    plans = [(pno, p) for pno, p in plans if _same_column(p, anchor)]
    n = len(plans)
    if not n:
        return 0, []
    best = [p["count"] for _, p in plans]
    prev = [-1] * n
    # The annex whose numbering the chain ending at each page is in.
    cur = [p["annex"] if p["first"] == (1,) else None for _, p in plans]
    for j in range(n):
        pj = plans[j][1]
        for i in range(j):
            pi = plans[i][1]
            # Rz stand in one margin column; a table's row numbers at the
            # table's own edge are another column.
            fits = plans[i][0] < plans[j][0] and _same_column(pi, pj) and (
                pi["last"] < pj["first"]
                # An annex starts its own Rz 1, once: FINMA disclosure
                # templates repeat "Anhang 2" as a running head over
                # tables whose row numbers (1, 2, 19, 20) are no Rz.
                or (pj["first"] == (1,) and pj["annex"] and pj["annex"] != cur[i]
                    and _dense(pj)))
            if fits and best[i] + pj["count"] > best[j]:
                best[j] = best[i] + pj["count"]
                prev[j] = i
                cur[j] = pj["annex"] if pi["last"] >= pj["first"] else cur[i]
    j = max(range(n), key=lambda k: best[k])
    total, chosen = best[j], []
    while j != -1:
        chosen.append(plans[j])
        j = prev[j]
    return total, chosen[::-1]


def _rising_chain(plans: list[tuple[int, dict]]) -> dict[int, dict]:
    """The plan placed on each page: the margin column whose numbers
    continue each other across the most Rz."""
    # The Rz column is where the main text's numbering starts, its Rz 1: an
    # annex table's row numbers stand at the table's own edge and may
    # outnumber the Rz (FINMA-RS 2016/1), so they must not win. Of several
    # columns starting at 1 on that page (section numbers "1." beside the
    # Rz), the one whose numbers run on across pages. Without an Rz 1 (BSV
    # numbers from 1001), any column; an early register table (old BSV
    # booklets, page 2) runs on nowhere.
    if not plans:
        return {}
    ones = [(pno, p) for pno, p in plans if p["first"] == (1,)]
    if ones:
        first = min(pno for pno, _ in ones)
        anchors = [p for pno, p in ones if pno == first]
    else:
        anchors = [p for _, p in plans]
    reps: list[dict] = []
    for a in anchors:
        if not any(a["w"] == r["w"] and _same_column(a, r) for r in reps):
            reps.append(a)
    _, chosen = max((_chain_in_column(plans, a) for a in reps), key=lambda t: t[0])
    return dict(chosen)


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
             for plan in _text_margin_plans(lines, doc_size, len(all_lines),
                                            doc[pno].rect.height, doc[pno].rect.width)]
    plan_of = _rising_chain(plans)
    pages, placed = [], 0
    annex, last = None, None
    for pno, lines in enumerate(all_lines):
        plan = plan_of.get(pno)
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
