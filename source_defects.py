"""
source_defects.py — references whose stored text is not (only) their own ruling.

A short, reviewed list. Every entry was read on the source's scan image
(runbooks/historical_bge_source_errors_2026-10-07.md and its .tsv): the DFR
documents of BGE 52 I 1, 8, 23, 39, 149 and 230 are scans of BGE 65 I, the one
of 39 I 469 shows 39 I 483, the HTML page indexed for 22 I 12 is 22 I 1012, and
the PDF of 71 II 223 carries 77 II 154-161 after its own pages.

The server serves such a reference with its metadata and a note, never with
the other ruling's text, and never with considerations extracted from it:

  withhold   the whole stored text is another ruling's: full_text and regeste
             are emptied, the date falls back to the volume placeholder, the
             structure (Sachverhalt/Erwägungen) and search hits are withheld.
  truncate   the stored text is the ruling's own up to a point, then another
             ruling's pages: the text is cut there, and search hits on the
             appended pages are dropped, but only while the stored text is the
             one the cut was verified on (SHA-256 of full_text); after a
             re-segmentation the cut lapses by itself. The structure and the
             outgoing citations, extracted from the whole text, stay withheld
             until the entry is removed: lifting them on the hash alone could
             serve the old structure before the structure DB is rebuilt from
             the repaired text.

The list is the serving-side answer until the shard repair removes the foreign
text (see the runbook); it keeps answering for the reference after that, so a
lookup tells why the text is missing instead of a bare not-found. Take an entry
out when its source is fixed and re-scraped.

Pure: no I/O.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

VERIFIED = "2026-10-07, DFR scan image"
RUNBOOK = "runbooks/historical_bge_source_errors_2026-10-07.md"
BGE_VOLUME_EPOCH = 1874


@dataclass(frozen=True)
class SourceDefect:
    reference: str              # "52 I 8"
    kind: str                   # foreign_volume | other_pages | other_reference | foreign_pages_appended
    action: str                 # withhold | truncate
    holds: str                  # what the stored text is: "65 I 8", "71 II 223 + 77 II 154"
    copy_of: str | None         # the corpus row that holds that ruling, if any
    note: str
    text_sha256: str | None = None   # truncate: the stored text the cut was verified on
    keep_chars: int | None = None    # truncate: the ruling's own text is full_text[:keep_chars]

    @property
    def volume(self) -> int:
        return int(self.reference.split()[0])

    @property
    def placeholder_date(self) -> str:
        return f"{self.volume + BGE_VOLUME_EPOCH}-01-01"

    def as_dict(self) -> dict:
        out = {"kind": self.kind, "reference": f"BGE {self.reference}", "action": self.action,
               "source_holds": self.holds, "note": self.note, "verified": VERIFIED}
        if self.copy_of:
            out["copy_in_corpus"] = self.copy_of
        return out


def _foreign_volume(ref: str, holds: str, copy_of: str) -> SourceDefect:
    return SourceDefect(
        reference=ref, kind="foreign_volume", action="withhold", holds=holds, copy_of=copy_of,
        note=(f"The DFR source document filed under BGE {ref} is a scan of BGE {holds} (1939), "
              f"not of BGE {ref}. Its text is withheld here: the text, holding and considerations "
              f"of BGE {ref} are not available on this server, and its date is not established. "
              f"Do not describe the content of BGE {ref} from this server."),
    )


DEFECTS: tuple[SourceDefect, ...] = (
    _foreign_volume("52 I 1", "65 I 1", "bge_65_I_1"),
    _foreign_volume("52 I 8", "65 I 8", "bge_65_I_8"),
    _foreign_volume("52 I 23", "65 I 23", "bge_65_I_23"),
    _foreign_volume("52 I 39", "65 I 39", "bge_65_I_39"),
    _foreign_volume("52 I 149", "65 I 149", "bge_65_I_149"),
    _foreign_volume("52 I 230", "65 I 230", "bge_65_I_230"),
    SourceDefect(
        reference="39 I 469", kind="other_pages", action="withhold", holds="39 I 483",
        copy_of="bge_39_I_483",
        note=("The DFR source document filed under BGE 39 I 469 shows pages 482-483 of the "
              "volume, i.e. BGE 39 I 483, not BGE 39 I 469. Its text and date are withheld here: "
              "the text, holding and considerations of BGE 39 I 469 are not available on this "
              "server. Do not describe the content of BGE 39 I 469 from this server."),
    ),
    SourceDefect(
        reference="22 I 12", kind="other_reference", action="withhold", holds="22 I 1012",
        copy_of=None,
        note=("The DFR page indexed for BGE 22 I 12 holds BGE 22 I 1012, not BGE 22 I 12. Its "
              "text and date are withheld here. BGE 22 I 12 is on DFR as a scan "
              "(https://www.fallrecht.ch/c1022012.pdf); BGE 22 I 1012 as "
              "https://www.fallrecht.ch/c1022A12.pdf. Do not describe the content of "
              "BGE 22 I 12 from this server."),
    ),
    SourceDefect(
        reference="71 II 223", kind="foreign_pages_appended", action="truncate",
        holds="71 II 223 + 77 II 154", copy_of="bge_77_II_154",
        text_sha256="1a8ea90b4f40fa9f9070c086f7bf6e41e540bd2c9ed5dd9a407ebd441b1edbc5",
        keep_chars=5729,
        note=("The DFR source document of BGE 71 II 223 carries pages 154-161 of BGE 77 II "
              "after its own pages. The text served here ends before them; the extracted "
              "considerations, which came from those pages, are withheld."),
    ),
)

_REF_RE = re.compile(
    r"^\s*(?:(?:bge|bge_historical)[_ ]+|(?:BGE|ATF|DTF)\s+)?"
    r"(\d{1,2})[_ ]+([IVX]+)[_ ]+(\d{1,4})\s*$",
    re.IGNORECASE,
)


def _parts(text: str | None) -> tuple[int, str, int] | None:
    m = _REF_RE.match(str(text or ""))
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper(), int(m.group(3))


_BY_PARTS: dict[tuple[int, str, int], SourceDefect] = {
    _parts(d.reference): d for d in DEFECTS  # type: ignore[misc]
}


def lookup(text: str | None) -> SourceDefect | None:
    """The defect for a decision id or BGE reference of volumes 1-79
    ('bge_52_I_8', 'bge_historical_52_I_8', 'BGE 52 I 8', '52_I_8'), else None."""
    parts = _parts(text)
    return _BY_PARTS.get(parts) if parts else None


def lookup_parts(volume, division, page) -> SourceDefect | None:
    """The defect for a parsed BGE reference (volume, division, page)."""
    try:
        return _BY_PARTS.get((int(volume), str(division).upper(), int(page)))
    except (TypeError, ValueError):
        return None


def withholds_text(decision_id: str | None) -> bool:
    """True when nothing of the stored text may be served (search hits included)."""
    d = lookup(decision_id)
    return bool(d and d.action == "withhold")


def withholds_structure(decision_id: str | None) -> bool:
    """True when the extracted Sachverhalt/Erwägungen must not be served."""
    return lookup(decision_id) is not None


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def own_text(defect: SourceDefect, stored_text: str | None) -> str | None:
    """The ruling's own text for a truncate entry; None when the stored text is
    no longer the one the cut was verified on (the entry has lapsed)."""
    text = stored_text or ""
    if (defect.action != "truncate" or defect.keep_chars is None
            or defect.text_sha256 is None or _sha256(text) != defect.text_sha256):
        return None
    return text[:defect.keep_chars].rstrip()


_MARK_RE = re.compile(r"</?mark>")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def snippet_in(snippet: str | None, text: str) -> bool:
    """True when a search snippet ('<mark>' highlights, '...' elisions) comes
    from ``text``: its longest fragment occurs there. A snippet with no
    fragment of 20 characters says nothing either way and counts as in."""
    frags = [_squash(f).casefold() for f in _MARK_RE.sub("", snippet or "").split("...")]
    frags = [f for f in frags if len(f) >= 20]
    return not frags or max(frags, key=len) in _squash(text).casefold()


def apply(row: dict) -> dict:
    """The row as it may be served: unchanged when not listed (same object);
    otherwise a copy with the text withheld or cut and a `source_defect` entry.
    A truncate entry whose verified text no longer matches leaves the row as it
    is (the text changed, e.g. after re-segmentation)."""
    court = row.get("court")
    if court not in (None, "bge", "bge_historical"):
        return row          # another court's docket may look like '52 I 8'
    d = lookup(row.get("decision_id")) or (lookup(row.get("docket_number")) if court else None)
    if d is None:
        return row
    if d.action == "truncate":
        text = row.get("full_text") or ""
        own = own_text(d, text)
        if own is None:
            return row
        out = dict(row)
        out["full_text"] = own
        out["cited_decisions"] = None   # extracted from the whole text, appended pages included
        out["source_defect"] = {**d.as_dict(), "chars_withheld": len(text) - len(out["full_text"])}
        out["text_available"] = True
        return out
    out = dict(row)
    out["full_text"] = ""
    out["regeste"] = None
    out["cited_decisions"] = None       # the other ruling's citations
    out["decision_date"] = d.placeholder_date
    out.pop("date_provenance", None)
    out["source_defect"] = d.as_dict()
    out["text_available"] = False
    return out


def filter_hits(rows: list[dict], stored_text=None) -> tuple[list[dict], int]:
    """The search hits that may be shown, and how many were dropped: none of a
    withheld reference; of a truncated one only those whose snippet lies in the
    ruling's own text. ``stored_text(decision_id)`` gives the stored full_text
    and is called for truncate entries only; without it they are kept."""
    kept: list[dict] = []
    for r in rows:
        d = lookup(r.get("decision_id"))
        if d is None:
            kept.append(r)
            continue
        if d.action == "withhold":
            continue
        if stored_text is not None:
            own = own_text(d, stored_text(r.get("decision_id")))
            if own is not None and not snippet_in(r.get("snippet"), own):
                continue        # matched on the appended pages
        kept.append(r)
    return kept, len(rows) - len(kept)
