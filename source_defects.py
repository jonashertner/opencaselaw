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

Recovered rulings (runbooks/historical_bge_recovered_2026-10-08/): a withheld
entry may carry the SHA-256 of the ruling's own text, recovered from DFR
material at hand. While the stored text is exactly that text, the row is served
with it and a note saying where it comes from and which pages are missing;
otherwise the entry withholds as before. The gate makes the switch follow the
data: nothing changes until the shard repair has written the recovered text
and a build has stored it. The structure and the outgoing citations stay
withheld for these references.

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
    recovered: str | None = None             # withhold: "complete" | "partial" recovered own text
    recovered_sha256: str | None = None      # withhold: SHA-256 of that text (the manifest's)
    recovered_note: str | None = None        # withhold: the note served with it

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


def _foreign_volume(ref: str, holds: str, copy_of: str, **recovery) -> SourceDefect:
    return SourceDefect(
        reference=ref, kind="foreign_volume", action="withhold", holds=holds, copy_of=copy_of,
        note=(f"The DFR source document filed under BGE {ref} is a scan of BGE {holds} (1939), "
              f"not of BGE {ref}. Its text is withheld here: the text, holding and considerations "
              f"of BGE {ref} are not available on this server, and its date is not established. "
              f"Do not describe the content of BGE {ref} from this server."),
        **recovery,
    )


def _partial(ref: str, holds: str, held: str, missing: str, sha: str) -> dict:
    """Recovery fields for a 52 I ruling recovered in part from its neighbours' scans."""
    return {
        "recovered": "partial", "recovered_sha256": sha,
        "recovered_note": (
            f"The DFR source document filed under BGE {ref} is a scan of BGE {holds} (1939). "
            f"The text served here is BGE {ref}'s own, recovered from the neighbouring rulings' "
            f"DFR scans: {held}. {missing} missing from every source at hand and "
            f"marked in the text. The extracted considerations are not served for this reference."),
    }


DEFECTS: tuple[SourceDefect, ...] = (
    _foreign_volume("52 I 1", "65 I 1", "bge_65_I_1"),
    _foreign_volume("52 I 8", "65 I 8", "bge_65_I_8"),
    _foreign_volume("52 I 23", "65 I 23", "bge_65_I_23", **_partial(
        "52 I 23", "65 I 23", "pages 23 and 26-27 of the volume", "Pages 24-25 are",
        "06d30dfce56498e1387505e1c8d28461de703b114b96d45a8f9cbda2a615c5f9")),
    _foreign_volume("52 I 39", "65 I 39", "bge_65_I_39", **_partial(
        "52 I 39", "65 I 39", "pages 39 and 44 of the volume", "Pages 40-43 are",
        "357aafe639db2f344fd8cca580e07379651f0951e5e328ed3415b9f298e1d419")),
    _foreign_volume("52 I 149", "65 I 149", "bge_65_I_149", **_partial(
        "52 I 149", "65 I 149", "page 149 of the volume, its first page", "Pages 150-153 are",
        "9e3d377548ba1985eb9a5c6a7352827b5e238118ed459155953a2268048d8164")),
    _foreign_volume("52 I 230", "65 I 230", "bge_65_I_230"),
    SourceDefect(
        reference="39 I 469", kind="other_pages", action="withhold", holds="39 I 483",
        copy_of="bge_39_I_483",
        note=("The DFR source document filed under BGE 39 I 469 shows pages 482-483 of the "
              "volume, i.e. BGE 39 I 483, not BGE 39 I 469. Its text and date are withheld here: "
              "the text, holding and considerations of BGE 39 I 469 are not available on this "
              "server. Do not describe the content of BGE 39 I 469 from this server."),
        recovered="complete",
        recovered_sha256="2f6bfe360cafbade849812d0136a68bfd95c951e025f73cc8e1d7069acdec789",
        recovered_note=("The DFR source document filed under BGE 39 I 469 shows pages 482-483 of "
                        "the volume (BGE 39 I 483). The text served here is BGE 39 I 469's own, "
                        "pages 469-471, read by OCR from the neighbouring DFR scans "
                        "https://www.fallrecht.ch/c1039465.pdf and "
                        "https://www.fallrecht.ch/c1039471.pdf. It is not proofread: figures may "
                        "be misread, so check them against those scans before relying on them. "
                        "The extracted considerations are not served for this reference."),
    ),
    SourceDefect(
        reference="22 I 12", kind="other_reference", action="withhold", holds="22 I 1012",
        copy_of=None,
        note=("The DFR page indexed for BGE 22 I 12 holds BGE 22 I 1012, not BGE 22 I 12. Its "
              "text and date are withheld here. BGE 22 I 12 is on DFR as a scan "
              "(https://www.fallrecht.ch/c1022012.pdf); BGE 22 I 1012 as "
              "https://www.fallrecht.ch/c1022A12.pdf. Do not describe the content of "
              "BGE 22 I 12 from this server."),
        recovered="complete",
        recovered_sha256="764d6a72075ab35c32aa4b0c547fd08c4f715863b569ce91831bb72e8096cd1b",
        recovered_note=("The DFR page indexed for BGE 22 I 12 holds BGE 22 I 1012. The text served "
                        "here is BGE 22 I 12's own, read from its DFR scan "
                        "(https://www.fallrecht.ch/c1022012.pdf). The extracted considerations "
                        "are not served for this reference."),
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


def recovered_in(defect: SourceDefect, stored_text: str | None) -> bool:
    """True when the stored text is the ruling's own, recovered text."""
    return bool(defect.recovered_sha256) and _sha256(stored_text or "") == defect.recovered_sha256


def served_dict(defect: SourceDefect, stored_text: str | None) -> dict:
    """The `source_defect` entry served for a reference with this stored text:
    the recovery note while it is the recovered text, else the defect's own."""
    if recovered_in(defect, stored_text):
        return {**defect.as_dict(), "recovered": defect.recovered, "note": defect.recovered_note}
    return defect.as_dict()


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
    A withheld reference whose stored text is its recovered own text is served
    with it, `source_defect` carrying the recovery note. A truncate entry whose
    verified text no longer matches leaves the row as it is (the text changed,
    e.g. after re-segmentation)."""
    court = row.get("court")
    if court not in (None, "bge", "bge_historical"):
        return row          # another court's docket may look like '52 I 8'
    d = lookup(row.get("decision_id")) or (lookup(row.get("docket_number")) if court else None)
    if d is None:
        return row
    if recovered_in(d, row.get("full_text")):
        out = dict(row)
        out["source_defect"] = served_dict(d, row.get("full_text"))
        out["text_available"] = True
        return out
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
    """The search hits that may be shown, and how many were dropped: of a
    withheld reference only those on its recovered text; of a truncated one only
    those whose snippet lies in the ruling's own text. ``stored_text(decision_id)``
    gives the stored full_text; without it, withheld references' hits are dropped
    and truncated ones' kept."""
    kept: list[dict] = []
    for r in rows:
        d = lookup(r.get("decision_id"))
        if d is None:
            kept.append(r)
            continue
        if d.action == "withhold":
            if stored_text is not None and recovered_in(d, stored_text(r.get("decision_id"))):
                kept.append(r)          # the ruling's own, recovered text
            continue
        if stored_text is not None:
            own = own_text(d, stored_text(r.get("decision_id")))
            if own is not None and not snippet_in(r.get("snippet"), own):
                continue        # matched on the appended pages
        kept.append(r)
    return kept, len(rows) - len(kept)
