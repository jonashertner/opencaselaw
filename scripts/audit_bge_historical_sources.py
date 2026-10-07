#!/usr/bin/env python3
"""
audit_bge_historical_sources.py — read-only: historical BGE rows (volumes 1-79)
whose DFR source document holds another reference's ruling.

Why (2026-10-07, runbooks/historical_bge_source_errors_2026-10-07.md): DFR
serves volumes 1-79 as scanned page ranges (www.fallrecht.ch/c<S><VVV><PPP>.pdf)
or as HTML pages (servat.unibe.ch/dfr/c<S><VVV><PPP>.html). Some of those
documents hold another reference's pages: the PDFs of BGE 52 I 1, 8, 23, 39,
149 and 230 are scans of BGE 65 I (1939) at the same page numbers, the PDF of
39 I 469 shows pages 482/483 (= 39 I 483), the PDF of 71 II 223 carries
77 II 154-161 after its own two spreads, and the HTML page behind "22 I 12"
is BGE 22 I 1012. Such a row serves another ruling's text under the cited
reference, and the per-row date gate cannot see it once the ruling's year is
plausible for the volume.

Checks, per row of the input (only references of volumes 1-79):
  same_text    the whitespace-normalised text equals another row's. Reported
               as 'same_spread' when both references are pages of one spread
               (same volume and part, pages at most 4 apart: two rulings that
               start on facing pages get the same PDF, e.g. 62 II 48/49),
               otherwise 'other_pages' or 'other_volume'.
  shared_text  the row shares a long run of text with a row of ANOTHER volume
               (sampled 8-word shingles; a later ruling quoting an earlier one
               shares a few sentences, a misfiled scan shares pages).
  page_jump    after the reference page, the printed page numbers restart well
               below it and run on (two or more consecutive page lines):
               pages of another volume appended to the range.
  html_label   a DFR HTML page whose own title names another reference.

Read-only: reads a JSONL shard (output/decisions/bge_historical.jsonl, or the
volume 1-79 rows of the published dataset exported to JSONL) and writes a TSV
(stdout or --out) plus counts on stderr. It never writes the input. Offline.

Usage:
  python3 scripts/audit_bge_historical_sources.py output/decisions/bge_historical.jsonl
  python3 scripts/audit_bge_historical_sources.py shard.jsonl --out /tmp/bge_sources.tsv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

MAX_VOLUME = 79
_REF_RE = re.compile(r"(?:^|_)(\d{1,3})_([IVX]+[ab]?)_(\d{1,4})$", re.IGNORECASE)
# The DFR HTML page names its reference in its first line: "DFR - BGE 22 I 1012 - ..."
_HTML_LABEL_RE = re.compile(r"\A\s*DFR - BGE (\d{1,3}) ([IVX]+[ab]?) (\d{1,4})\b")
# A page number stands alone on its line; OCR sometimes adds a quote ('81"').
_PAGE_LINE_RE = re.compile(r"(?m)^[ \t]*(\d{1,4})[ \t]*[\"'’`]?[ \t]*$")

# Facing pages and the next spread: two references this close share a PDF.
SPREAD_PAGES = 4
MIN_TEXT = 200
# shared_text: 8-word shingles, one in SAMPLE kept (content-defined, so two
# texts keep the same shingles of a shared passage). A page holds ~300 words,
# i.e. ~37 sampled shingles; a quotation of a few sentences shares a handful.
SHINGLE_WORDS = 8
SAMPLE = 8
MIN_SHARED = 12
MIN_SHARED_FRACTION = 0.25
# A shingle found in more rows than this is boilerplate (running heads,
# formulas), not evidence of a shared page.
MAX_POSTINGS = 12
# page_jump: the restart must lie this far below the reference page, run on
# for this many page lines, and the text must not come back to its own pages
# (OCR of a run: "452 / 454 / 11 / 13 / 457", "450 / 451 / 152 / 153 / 454";
# lists and tables: "6 / 7 / 8"; measured on the 14,578 published rows).
PAGE_JUMP_MIN = 5
PAGE_JUMP_RUN = 3
PAGE_JUMP_FLOOR = 10
OWN_RANGE_PAGES = 200


@dataclass(frozen=True)
class Ref:
    volume: int
    part: str
    page: int

    def __str__(self) -> str:
        return f"{self.volume} {self.part} {self.page}"


def parse_ref(row: dict) -> Ref | None:
    """(volume, part, page) from the docket ('78_IV_83') or the id; None for a
    volume after 79 or an unparsed reference."""
    for key in ("docket_number", "decision_id"):
        m = _REF_RE.search(str(row.get(key) or "").strip())
        if m:
            vol = int(m.group(1))
            if 1 <= vol <= MAX_VOLUME:
                return Ref(vol, m.group(2).upper(), int(m.group(3)))
            return None
    return None


def normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def text_key(text: str) -> str | None:
    t = normalised(text)
    if len(t) < MIN_TEXT:
        return None
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def same_text_kind(a: Ref, b: Ref) -> str:
    if a.volume != b.volume:
        return "other_volume"
    if a.part == b.part and abs(a.page - b.page) <= SPREAD_PAGES:
        return "same_spread"
    return "other_pages"


def sampled_shingles(text: str) -> set[int]:
    words = re.findall(r"[^\W\d_]{3,}", (text or "").lower())
    out: set[int] = set()
    for i in range(len(words) - SHINGLE_WORDS + 1):
        s = " ".join(words[i:i + SHINGLE_WORDS]).encode("utf-8")
        h = (zlib.crc32(s) << 32) | zlib.adler32(s)
        if h % SAMPLE == 0:
            out.add(h)
    return out


def page_jump(text: str, ref: Ref) -> tuple[int, int] | None:
    """(offset, first foreign page) when the printed page numbers, after
    reaching the reference page, restart well below it, run on there and
    never come back (BGE 71 II 223: 224, then 154, 155, 156, 157, 159, 161)."""
    marks = [(m.start(), int(m.group(1))) for m in _PAGE_LINE_RE.finditer(text or "")]
    reached = False
    for i, (pos, n) in enumerate(marks):
        if not reached:
            reached = ref.page - 1 <= n <= ref.page + SPREAD_PAGES
            continue
        if not PAGE_JUMP_FLOOR <= n <= ref.page - PAGE_JUMP_MIN:
            continue
        run = [n]
        for _, m in marks[i + 1:]:
            if run[-1] < m <= run[-1] + 2:
                run.append(m)
            else:
                break
        back = any(ref.page - 1 <= m <= ref.page + OWN_RANGE_PAGES for _, m in marks[i + 1:])
        if len(run) >= PAGE_JUMP_RUN and not back:
            return pos, n
    return None


def html_label(text: str) -> str | None:
    m = _HTML_LABEL_RE.match(text or "")
    return f"{int(m.group(1))} {m.group(2)} {int(m.group(3))}" if m else None


def audit(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """Findings as (check, decision_id, other decision_id or '', detail)."""
    refs: list[Ref] = []
    keep: list[dict] = []
    for row in rows:
        ref = parse_ref(row)
        if ref is not None:
            refs.append(ref)
            keep.append(row)
    out: list[tuple[str, str, str, str]] = []

    groups: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(keep):
        k = text_key(row.get("full_text") or "")
        if k:
            groups[k].append(i)
    for members in groups.values():
        for x in members:
            for y in members:
                if x < y:
                    kind = same_text_kind(refs[x], refs[y])
                    out.append(("same_text", keep[x]["decision_id"], keep[y]["decision_id"], kind))

    shingles = [sampled_shingles(row.get("full_text") or "") for row in keep]
    postings: dict[int, list[int]] = defaultdict(list)
    for i, sh in enumerate(shingles):
        for h in sh:
            postings[h].append(i)
    shared: Counter = Counter()
    for members in postings.values():
        if len(members) > MAX_POSTINGS:
            continue
        for a in members:
            for b in members:
                if a < b and refs[a].volume != refs[b].volume:
                    shared[(a, b)] += 1
    for (a, b), n in sorted(shared.items()):
        small = min(len(shingles[a]), len(shingles[b]))
        if n < MIN_SHARED or not small or n / small < MIN_SHARED_FRACTION:
            continue
        if text_key(keep[a].get("full_text") or "") == text_key(keep[b].get("full_text") or ""):
            continue        # already reported as same_text
        out.append(("shared_text", keep[a]["decision_id"], keep[b]["decision_id"],
                    f"{n} shared of {len(shingles[a])}/{len(shingles[b])} sampled shingles"))

    for ref, row in zip(refs, keep):
        text = row.get("full_text") or ""
        label = html_label(text)
        if label is not None:
            if label != str(ref):
                out.append(("html_label", row["decision_id"], "", f"page titled BGE {label}"))
            continue        # HTML pages number paragraphs, not pages
        jump = page_jump(text, ref)
        if jump is not None:
            pos, n = jump
            out.append(("page_jump", row["decision_id"], "",
                        f"page {n} at offset {pos} of {len(text)}"))
    return out


def iter_rows(path: Path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard", type=Path, help="JSONL with decision_id, docket_number, full_text")
    ap.add_argument("--out", type=Path, help="TSV path (default: stdout)")
    args = ap.parse_args()
    rows = [{"decision_id": r.get("decision_id"), "docket_number": r.get("docket_number"),
             "full_text": r.get("full_text")} for r in iter_rows(args.shard)]
    findings = audit(rows)
    lines = ["check\tdecision_id\tother_id\tdetail"]
    lines += ["\t".join(f) for f in findings]
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    counts = Counter(f[0] if f[0] != "same_text" else f"same_text:{f[3]}" for f in findings)
    print(f"rows read: {len(rows)}", file=sys.stderr)
    for k, v in sorted(counts.items()):
        print(f"{v:>8}  {k}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
