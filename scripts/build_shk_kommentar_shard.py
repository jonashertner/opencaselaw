#!/usr/bin/env python3
"""Build the scholarship shard for the Kommentar zur Schaffhauser
Verwaltungsrechtspflege (Meyer/Herrmann/Bilger, EIZ Publishing 2021).

One record per Kommentierung (article of the VRG / JG), plus the two essays
and the three checklists, parsed from the publisher's ePub edition:

  https://eizpublishing.ch/publikationen/kommentar-zur-schaffhauser-verwaltungsrechtspflege/

Licence, from the imprint page of both the PDF and the ePub:
  "© 2021 – CC BY-NC-ND (Werk), CC BY-SA (Text)"
The records carry the TEXT, so they are stamped CC-BY-SA (no version is
stated by the publisher, none is invented here).

The text is reproduced verbatim. The only additions are structural markers
that the print edition shows typographically: "N <k>" in front of each
margin-numbered unit (Randziffer) and "[k]" footnote references with the
footnote texts appended under "Fussnoten". Margin numbers are not present in
the ePub markup; they are the running ordinal of the numbered units
(<p>, li.li-rz, li.li-rz-left) within an article. The ePub's own cross-reference
anchors (id="vrg2n14" = Art. 2 VRG N 14) are used to VERIFY that rule: the
build fails if an anchor disagrees with the computed ordinal, except for the
ids listed in KNOWN_BAD_ANCHORS (source typos, each explained there).

Usage:
    python3 scripts/build_shk_kommentar_shard.py --epub shk.epub \
        --out output/legal_scholarship/shk_kommentar.jsonl
    python3 scripts/build_shk_kommentar_shard.py --epub shk.epub --report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import warnings
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

SOURCE = "shk_kommentar"
BOOK = "Kommentar zur Schaffhauser Verwaltungsrechtspflege"
EDITORS = "Meyer/Herrmann/Bilger"
LANDING = ("https://eizpublishing.ch/publikationen/"
           "kommentar-zur-schaffhauser-verwaltungsrechtspflege/")
PDF_URL = ("https://eizpublishing.ch/wp-content/uploads/2021/11/Kommentar-zur-"
           "Schaffhauser-Verwaltungsrechtspflege-Digital-V1_02-20211005.pdf")
DOI = "10.36862/eiz-411"
LICENSE = "CC-BY-SA"
# The imprint says "CC BY-SA (Text)" without a version, and so does the
# publisher's site. No version-specific deed is asserted until EIZ confirms one.
LICENSE_URL = None
RIGHTS_RAW = "© 2021 – CC BY-NC-ND (Werk), CC BY-SA (Text)"
LAW_NAMES = {
    "VRG": "Gesetz über den Rechtsschutz in Verwaltungssachen "
           "(Verwaltungsrechtspflegegesetz, VRG; SHR 172.200)",
    "JG": "Justizgesetz (JG; SHR 173.200)",
}

ANCHOR_RE = re.compile(r"^(vrg|jg)(\d+[a-z]*)n(\d+)$")
# Anchors in the source that do not sit on the unit they name. Verified by hand
# against the PDF edition; they are cross-reference targets, not numbering.
KNOWN_BAD_ANCHORS = {
    "vrg12n3",   # placed inside a footnote <li>, not on N 3
}


def _clean(s: str) -> str:
    s = s.replace("\xa0", " ").replace("­", "")
    return re.sub(r"[ \t\r\n]+", " ", s).strip()


def _is_unit(el: Tag) -> bool:
    """True for elements that carry a margin number in the print edition."""
    if el.find_parent(class_="footnotes") or el.find_parent("ol", class_="eizlegal"):
        return False
    cls = el.get("class") or []
    if el.name == "p":
        if "shignore" in cls:
            return False
        return el.find_parent("li", class_=re.compile(r"^li-rz")) is None
    if el.name == "li":
        return "li-rz" in cls or "li-rz-left" in cls
    return False


def _inline(el: Tag, fn_map: dict[str, int]) -> str:
    """Text of an element with footnote references rendered as [k]."""
    parts: list[str] = []
    for node in el.descendants:
        if isinstance(node, NavigableString):
            par = node.parent
            if par is not None and par.name == "sup" and "footnote" in (par.get("class") or []):
                continue
            parts.append(str(node))
        elif isinstance(node, Tag) and node.name == "a" and "footnote" in (node.get("class") or []):
            target = (node.get("href") or "").lstrip("#")
            k = fn_map.get(target)
            parts.append(f"[{k}]" if k else "")
        elif isinstance(node, Tag) and node.name == "br":
            parts.append(" ")
    return _clean("".join(parts))


def _statute(ol: Tag, fn_map: dict[str, int], depth: int = 0) -> list[str]:
    """Render the ol.eizlegal statute block. Sub-lists get the letters the
    stylesheet generates (level 2: a) b) …; level 3: i. ii. …)."""
    out: list[str] = []
    for idx, li in enumerate(ol.find_all("li", recursive=False)):
        own = Tag(name="span")
        for child in li.children:
            if isinstance(child, Tag) and child.name in ("ol", "ul"):
                continue
            own.append(child.__copy__() if isinstance(child, Tag) else NavigableString(str(child)))
        text = _inline(own, fn_map)
        if depth == 1:
            text = f"{chr(ord('a') + idx)}) {text}"
        elif depth == 2:
            text = f"{_roman(idx + 1)}. {text}"
        elif depth >= 3:
            text = f"{idx + 1}) {text}"
        if text.strip():
            out.append("  " * depth + text)
        for sub in li.find_all(["ol", "ul"], recursive=False):
            out.extend(_statute(sub, fn_map, depth + 1))
    return out


def _roman(n: int) -> str:
    vals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    s = ""
    for v, r in vals:
        while n >= v:
            s += r
            n -= v
    return s


def _split_articles(ugc: Tag, wrap: Tag | None):
    """Yield (title_el, author_el, [content elements]) per article in a file.

    Chapter files: one article, title/author in .chapter-title-wrap.
    Part files: Pressbooks folds the first article(s) of a section into the
    part file, marked by h1.chapter-title + h2.chapter-author inside the body.
    """
    inner = ugc.find("div") or ugc
    cur = None
    if wrap is not None and wrap.select_one(".chapter-title") is not None \
            and _clean(wrap.select_one(".chapter-title").get_text(" ")):
        cur = (wrap.select_one(".chapter-title"), wrap.select_one(".chapter-author"),
               wrap.select_one(".chapter-subtitle"), [])
    sub_default = wrap.select_one(".chapter-subtitle") if wrap is not None else None
    for c in inner.children:
        if not isinstance(c, Tag):
            continue
        cls = c.get("class") or []
        if c.name in ("h1", "h2") and "chapter-title" in cls:
            # a further article folded into the same file (Pressbooks does this
            # for the first article of a section and after repealed articles)
            if cur:
                yield cur
            cur = (c, None, sub_default, [])
        elif cur and c.name == "h2" and "chapter-author" in cls:
            cur = (cur[0], c, cur[2], cur[3])
        elif cur:
            cur[3].append(c)
    if cur:
        yield cur


def _render(content: list[Tag], fn_map: dict[str, int], anchors_out: list,
            numbered: bool = True):
    """Render article content to text. Returns (text, n_units). The checklists
    in the back matter carry no margin numbers in print: numbered=False."""
    lines: list[str] = []
    n = 0

    def walk(el: Tag, list_depth: int = 0):
        nonlocal n
        cls = el.get("class") or []
        if "footnotes" in cls or (el.name == "hr"):
            return
        if el.name == "ol" and "eizlegal" in cls:
            lines.append("")
            lines.extend(_statute(el, fn_map))
            lines.append("")
            return
        if re.fullmatch(r"h[1-6]", el.name or ""):
            t = _inline(el, fn_map)
            if t:
                lines.extend(["", t, ""])
            return
        if numbered and el.name in ("p", "li") and _is_unit(el):
            n += 1
            for sp in el.find_all(id=ANCHOR_RE):
                anchors_out.append((sp["id"], n))
            if el.get("id") and ANCHOR_RE.match(el["id"]):
                anchors_out.append((el["id"], n))
            lines.append(f"N {n}  {_inline(el, fn_map)}")
            return
        if el.name == "p":
            t = _inline(el, fn_map)
            if t:
                lines.append(t)
            return
        if el.name == "li":
            own = Tag(name="span")
            for child in el.children:
                if isinstance(child, Tag) and child.name in ("ol", "ul"):
                    continue
                own.append(child.__copy__() if isinstance(child, Tag) else NavigableString(str(child)))
            t = _inline(own, fn_map)
            if t:
                lines.append("  " * max(0, list_depth - 1) + "– " + t)
            for sub in el.find_all(["ol", "ul"], recursive=False):
                walk(sub, list_depth)
            return
        if el.name in ("ol", "ul"):
            for li in el.find_all("li", recursive=False):
                walk(li, list_depth + 1)
            return
        if el.name == "table":
            for tr in el.find_all("tr"):
                cells = [_inline(td, fn_map) for td in tr.find_all(["td", "th"])]
                if any(cells):
                    lines.append(" | ".join(cells))
            return
        for child in el.children:
            if isinstance(child, Tag):
                walk(child, list_depth)

    for el in content:
        walk(el)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, n


def parse_epub(epub_path: Path) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    problems: list[str] = []
    z = zipfile.ZipFile(epub_path)
    names = sorted(n for n in z.namelist()
                   if re.search(r"OEBPS/(chapter|part|back-matter)-\d+.*\.html$", n))
    sha = hashlib.sha256(epub_path.read_bytes()).hexdigest()
    current_law = None
    for name in names:
        soup = BeautifulSoup(z.read(name).decode("utf-8"), "lxml")
        base = os.path.basename(name)
        ugc = soup.select_one(".chapter-ugc, .part-ugc, .back-matter-ugc")
        if ugc is None:
            continue
        part_title = soup.select_one(".part-title")
        if part_title is not None:
            pt = _clean(part_title.get_text(" "))
            if "(VRG)" in pt or "Verwaltungsrechtspflegegesetz" in pt:
                current_law = "VRG"
            elif "Justizgesetz" in pt:
                current_law = "JG"
        wrap = soup.select_one(".chapter-title-wrap, .back-matter-title-wrap")
        fn_map: dict[str, int] = {}
        fn_text: dict[int, str] = {}
        fdiv = soup.select_one("div.footnotes")
        if fdiv is not None:
            for k, li in enumerate(fdiv.find_all("li", recursive=True), start=1):
                if not li.get("id"):
                    continue
                fn_map[li["id"]] = k
                for a in li.find_all("a", class_="return-footnote"):
                    a.decompose()
                fn_text[k] = _clean(li.get_text(" "))
        if base.startswith("back-matter"):
            t = wrap.select_one(".back-matter-title") if wrap else None
            title = _clean(t.get_text(" ")) if t else base
            if "sachregister" in base:
                continue  # index of page references — no standalone content
            content = [c for c in (ugc.find("div") or ugc).children if isinstance(c, Tag)]
            arts = [(None, None, None, content)]
        else:
            arts = list(_split_articles(ugc, wrap))
            title = None
        for title_el, author_el, sub_el, content in arts:
            anchors: list = []
            if title_el is not None:
                # title footnote ("Fassung gemäss …") stays a footnote ref
                title = _inline(title_el, fn_map)
                title = re.sub(r"\s*\[\d+\]\s*$", "", title)
            author = _clean(author_el.get_text(" ")) if author_el is not None else ""
            law = _clean(sub_el.get_text(" ")) if sub_el is not None else ""
            m_id = ANCHOR_RE.match((title_el.get("id") or "") + "n0") if title_el is not None else None
            if not law and m_id:
                law = m_id.group(1).upper()
            m_art = re.match(r"Art\.\s*(\d+[a-z]*(?:\s+und\s+\d+[a-z]*)?)\s*/\s*(.*)$", title or "")
            if m_art and not law:
                law = current_law or ""
            if law in ("VRG", "JG"):
                current_law = law
            body, n_units = _render(content, fn_map, anchors,
                                    numbered=not base.startswith("back-matter"))
            used = sorted({int(k) for k in re.findall(r"\[(\d+)\]", body + " " + (title or ""))
                           if int(k) in fn_text})
            # footnotes referenced from the title (e.g. "Fassung gemäss …")
            title_refs = [fn_map[a.get("href", "").lstrip("#")] for a in
                          (title_el.find_all("a", class_="footnote") if title_el is not None else [])
                          if a.get("href", "").lstrip("#") in fn_map]
            used = sorted(set(used) | set(title_refs))
            for aid, got in anchors:
                want = int(ANCHOR_RE.match(aid).group(3))
                if want != got and aid not in KNOWN_BAD_ANCHORS:
                    problems.append(f"{base}: anchor {aid} sits on unit {got}")
            if not m_art and not body.strip() and not base.startswith(("chapter-00", "back-matter")):
                continue  # bare section heading ("A. Allgemeines", "Dbis … / Aufgehoben")
            if m_art:
                art, heading = m_art.group(1), m_art.group(2).strip()
                rid = f"{law.lower()}-art-" + re.sub(r"\s+und\s+", "-", art)
                full_title = f"Art. {art} {law} / {heading}"
                cite = (f"{author.split()[-1].upper() if author else 'BEARBEITER/IN'}, in: "
                        f"{EDITORS} (Hrsg.), {BOOK}, 2021, Art. {art} {law} N. X")
                pub_type = "commentary"
            else:
                art, heading = None, title
                rid = re.sub(r"^(chapter|back-matter|part)-\d+-", "", base[:-5])
                full_title = title
                cite = (f"{author.split()[-1].upper() if author else EDITORS}, {title}, in: "
                        f"{EDITORS} (Hrsg.), {BOOK}, 2021")
                pub_type = "chapter"
            if not body.strip():
                problems.append(f"{base}: '{full_title}' has no content")
                continue
            head = [full_title]
            if law in LAW_NAMES and art:
                head.append(LAW_NAMES[law])
            if author:
                head.append(author)
            text = "\n".join(head) + "\n\n" + body
            if used:
                text += "\n\nFussnoten\n" + "\n".join(f"[{k}] {fn_text[k]}" for k in used)
            first_para = next((ln for ln in body.split("\n") if ln.startswith("N 1  ")), "")
            records.append({
                "source": SOURCE,
                "source_record_id": rid,
                "datestamp": "2021-10-27",
                "pub_type": pub_type,
                "title": f"{full_title} — {BOOK}" if art else full_title,
                "authors": [author] if author else [],
                "abstract": _clean(first_para[5:])[:600] or None,
                "full_text": text,
                "publication_date": "2021-11-05",
                "year": 2021,
                "sources_raw": [f"{BOOK} ({EDITORS}, Hrsg.)"],
                "publisher": "EIZ Publishing",
                "doi": DOI,
                "url": LANDING,
                "pdf_url": PDF_URL,
                "language": "de",
                "license": LICENSE,
                "license_url": LICENSE_URL,
                "rights_raw": [RIGHTS_RAW],
                "subjects": [s for s in ["Verwaltungsrechtspflege", "Kanton Schaffhausen",
                                         law or None, f"Art. {art} {law}" if art else None] if s],
                "law": law or None,
                "article": art,
                "randziffern": n_units,
                "footnotes": len(used),
                "citation_suggestion": cite,
                "citation_suggestion_source": "imprint: BearbeiterIn, in: Meyer/Herrmann/Bilger "
                                              "(Hrsg.), Kommentar zur Schaffhauser "
                                              "Verwaltungsrechtspflege, 2021, Art. X VRG/JG N. X.",
                "edition_version": "1.02-20211005",
                "epub_file": base,
                "epub_sha256": sha,
            })
    # The ePub carries some repealed-article notices twice (once in the part
    # file, once in a chapter file). Identical text -> keep one; differing
    # text under one id is a real problem.
    seen: dict[str, str] = {}
    uniq: list[dict] = []
    for r in records:
        rid = r["source_record_id"]
        if rid in seen and seen[rid] == r["full_text"]:
            continue
        seen.setdefault(rid, r["full_text"])
        uniq.append(r)
    records = uniq
    return records, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--epub", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    records, problems = parse_epub(args.epub)
    ids = [r["source_record_id"] for r in records]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        problems.append(f"duplicate record ids: {dup}")
    if args.report or not args.out:
        for r in records:
            print(f"{r['source_record_id']:42} N={r['randziffern']:>3} fn={r['footnotes']:>3} "
                  f"chars={len(r['full_text']):>6}  {(r['authors'] or ['—'])[0]}")
        print(f"\nrecords: {len(records)}  commentary: "
              f"{sum(r['pub_type'] == 'commentary' for r in records)}  "
              f"chars: {sum(len(r['full_text']) for r in records)}")
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    if problems:
        return 1
    if args.out:
        tmp = args.out.with_suffix(args.out.suffix + ".tmp")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, args.out)
        print(f"wrote {len(records)} records -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
