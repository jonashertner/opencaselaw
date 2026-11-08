"""
Keep every language version a source really has, and nothing else.

Several sites answer a request for a translation that does not exist with
another language's file instead of a 404: takeover.ch serves the German PDF
under a French file name, its Italian report page links the French file,
FINMA's Sitecore falls back to the German media item, the BJ lists a
German-only Weisung on its French page, and the RAB links the current
version of a circular as its "Erstversion". Indexed as they come, those rows
would put German text under language=fr (or a 2024 text under a 2007 date).

`TextDedupMixin.with_body(stub, group)` downloads the file, extracts the
text, and drops the row when a row already taken for the same document
(`group`: a circular's number, a notice's number) has the same text. Rows
stored on an earlier run take part through their stored content_hash, so a
later run never lets the copy in once the original is skipped as known.

Sources yield the original language first (DE, then FR/IT/EN) so the copy,
not the original, is the one dropped.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from .base import _NO_TEXT_LAYER_SENTINEL, sha256_hex

logger = logging.getLogger(__name__)


class TextDedupMixin:
    """Mix into a PracticeScraper subclass (before it in the MRO)."""

    def _dedup_init(self) -> None:
        self._group_hashes: dict[str, set[str]] = defaultdict(set)
        self._stored_hash: dict[str, str] = {}
        if self.output_path.exists():
            with open(self.output_path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("doc_id"):
                        self._stored_hash[rec["doc_id"]] = rec.get("content_hash") or ""

    def with_body(self, stub: dict, group: str) -> dict | None:
        if not hasattr(self, "_group_hashes"):
            self._dedup_init()
        doc_id = self._make_doc_id(stub)
        if doc_id in self._seen_ids and not self._is_reissue(doc_id, stub):
            h = self._stored_hash.get(doc_id)
            if h:
                self._group_hashes[group].add(h)
            return stub                   # run() skips it as known; no download
        self._last_ocr = False
        text = self.fetch_pdf_text(stub["pdf_url"])
        if not text:
            return None                   # download failed; run() would count it failed
        if text == _NO_TEXT_LAYER_SENTINEL:
            text = self._placeholder_body(stub)
        h = sha256_hex(text)
        if h in self._group_hashes[group]:
            logger.info("[%s] %s: same text as another version of %s, skipped",
                        self.SOURCE_KEY, stub["pdf_url"], group)
            return None
        self._group_hashes[group].add(h)
        if self._last_ocr:
            stub = {**stub, "topics": [*stub.get("topics", []), "OCR"]}
        return {**stub, "body_text": text}


# Function words that are frequent in one language and rare in the other two
# ("la"/"le"/"des" are shared by French and Italian or French and German and
# are left out).
_FUNCTION_WORDS = {
    "de": {"der", "die", "das", "und", "nicht", "ist", "wird", "für", "mit", "von", "den", "dem", "eine", "werden"},
    "fr": {"les", "du", "est", "pour", "dans", "une", "que", "au", "aux", "sont", "qui", "sur"},
    "it": {"il", "gli", "della", "del", "che", "per", "nel", "sono", "dei", "alla", "delle", "di"},
}


def body_lang(text: str) -> str:
    """The language the body is written in, or "" when it is not clear.

    For sources whose listing does not say: the CLDJP's file names often carry
    no language ("Vade mecum tri-PLESORR_V260101"), a BWO extract can close
    with a German citation line under a French judgment, and the NKVF lists
    French-only reports on its German page. The first 300 characters are
    skipped: the CLDJP's Italian règlements open with a French letterhead."""
    words = re.findall(r"[a-zàâäçéèêëîïôöùûüß]+", (text or "")[300:6000].lower())
    scores = {lang: sum(w in fw for w in words) for lang, fw in _FUNCTION_WORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    return best if top >= 20 and top >= 2 * second else ""
