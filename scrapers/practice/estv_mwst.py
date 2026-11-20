"""
ESTV MWST-Infos & MWST-Branchen-Infos — Federal VAT administrative practice
===========================================================================

Source (PrimeFaces/JSF app, but the READ path is plain static HTML):
  MWST-Infos:         https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?label=true
  MWST-Branchen-Infos: …/sectorInfos/tableOfContent.xhtml?label=true

Issue #16. These are the day-to-day reference for Swiss VAT / Treuhänder
practice and were the one ESTV gap left after the BAZG (`scrapers/bazg.py`) and
Kreisschreiben work. The page is a JSF application, but no browser / ViewState /
AJAX is needed for the read path — three stateless GETs:

  1. tableOfContent.xhtml?label=true        → publications (publicationId links)
  2. tableOfContent.xhtml?publicationId=X   → cipher leaves (componentId links)
  3. cipherDisplay.xhtml?publicationId=X&componentId=Y → verbatim prose in
                                              <div id="formular:cipherText">

Language is selected by the **Accept-Language request header** (NOT a ?lang=
param): the same publicationId+componentId returns the DE / FR / IT text under
de-CH / fr-CH / it-CH. IDs are shared across languages, so the three language
versions of a publication link trivially (doc_id is language-keyed).

Record model: one document per (publication, language) — the whole MWST-Info,
body = its cipher sections concatenated in document order. This matches the
sibling `estv_kreisschreiben.py` (one publication = one document) rather than
fragmenting into ~5k per-section rows.

The PrimeFaces partial-AJAX channel exists but carries no document body, so it
is irrelevant here. robots.txt is a courtesy ``Disallow: /`` on a public
admin.ch portal — same posture as the already-shipped ESTV / BAZG scrapers; the
bot identifies itself (OpenCaseLawBot UA + contact) and rate-limits politely.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, sha256_hex

logger = logging.getLogger(__name__)

BASE = "https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages"

_PUBLICATION_DATE_RE = re.compile(
    r"Publiziert am.{0,300}?(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE | re.DOTALL
)
_LEADING_NUM_RE = re.compile(r"\b(\d{1,3})\b")


def _parse_toc(html: str) -> list[tuple[str, str]]:
    """Publications from a tableOfContent listing: [(publicationId, title)],
    deduped on publicationId, in document order."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="tableOfContent.xhtml?publicationId="]'):
        m = re.search(r"publicationId=(\d+)", a.get("href", ""))
        if not m:
            continue
        pub_id = m.group(1)
        if pub_id in seen:
            continue
        seen.add(pub_id)
        out.append((pub_id, re.sub(r"\s+", " ", a.get_text(" ", strip=True))))
    return out


def _parse_publication_ciphers(html: str) -> list[str]:
    """Cipher componentIds for one publication, deduped, in document order.
    (BeautifulSoup decodes the &amp; in hrefs, so a plain regex on the attr
    value works.)"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="cipherDisplay.xhtml"]'):
        m = re.search(r"componentId=(\d+)", a.get("href", ""))
        if not m:
            continue
        comp = m.group(1)
        if comp in seen:
            continue
        seen.add(comp)
        out.append(comp)
    return out


def _parse_publication_headings(html: str) -> dict[str, str]:
    """componentId -> its TOC label ("2.1.4 Von der Steuer ausgenommene
    Dienstleistungen"). The cipher pages carry no heading of their own, so
    this is where a section's number and title come from."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for a in soup.select('a[href*="cipherDisplay.xhtml"]'):
        m = re.search(r"componentId=(\d+)", a.get("href", ""))
        if m and m.group(1) not in out:
            out[m.group(1)] = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
    return out


_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "table", "tbody", "thead", "h1", "h2",
               "h3", "h4", "h5", "h6", "blockquote", "dl", "dt", "dd", "section"}


def _block_text(node) -> str:
    """HTML -> text with one line per paragraph, list item and table row.

    get_text(" ") ran every paragraph of a publication together (MWST-
    Branchen-Info 14: 148,798 characters on a handful of lines; user report
    2026-11-20). Inline elements keep the source's own whitespace, as a
    browser renders them; a table row becomes one line ("a. Die Gewährung
    …"), its cells separated by a space."""
    from bs4 import Comment, NavigableString

    lines: list[str] = []
    cur: list[str] = []

    def flush():
        line = re.sub(r"\s+", " ", "".join(cur)).strip()
        cur.clear()
        if line:
            lines.append(line)

    def walk(el):
        for ch in el.children:
            if isinstance(ch, Comment):
                continue
            if isinstance(ch, NavigableString):
                cur.append(str(ch))
            elif ch.name in ("script", "style"):
                continue
            elif ch.name == "br":
                flush()
            elif ch.name == "tr":
                flush()
                cells = [re.sub(r"\s+", " ", c.get_text(" ")).strip()
                         for c in ch.find_all(["td", "th"], recursive=False)]
                row = " ".join(c for c in cells if c)
                if row:
                    lines.append(row)
            elif ch.name in _BLOCK_TAGS:
                flush()
                walk(ch)
                flush()
            else:
                walk(ch)

    walk(node)
    flush()
    return "\n".join(lines)


def _parse_cipher(html: str) -> tuple[str, str]:
    """One cipher leaf → (body_text, date_iso). body_text is the verbatim prose
    in #formular:cipherText, one line per paragraph / list item / table row
    (empty for heading-only container nodes); date_iso is the 'Publiziert am
    DD.MM.YYYY' as ISO, or '' if absent."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id="formular:cipherText")
    body = _block_text(node) if node else ""
    date_iso = ""
    m = _PUBLICATION_DATE_RE.search(html)
    if m:
        date_iso = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return body, date_iso


def _leading_number(title: str) -> str | None:
    """Publication number from a TOC title ('MWST-Info 05 …' → '05')."""
    m = _LEADING_NUM_RE.search(title or "")
    return m.group(1) if m else None


class EstvMwstScraper(PracticeScraper):
    """ESTV MWST-Infos + MWST-Branchen-Infos (federal VAT administrative practice)."""

    SOURCE_KEY = "estv_mwst"
    ISSUING_AUTHORITY = "ESTV"
    DEFAULT_DOC_TYPE = "mwst_info"
    REQUEST_DELAY = 1.5
    MIN_BODY_CHARS = 200

    # (tree path, doc_type, human label)
    TREES = [
        ("taxInfos", "mwst_info", "MWST-Info"),
        ("sectorInfos", "mwst_branchen_info", "MWST-Branchen-Info"),
    ]

    def __init__(self, languages: tuple[str, ...] = ("de", "fr", "it")):
        super().__init__()
        self.languages = languages

    @staticmethod
    def _lang_headers(lang: str) -> dict:
        # Per-request Accept-Language is the verified language switch.
        return {"Accept-Language": f"{lang}-CH,{lang};q=0.9"}

    def _make_doc_id(self, stub: dict) -> str:
        # Language-keyed + stable across runs (idempotent vs _seen_ids).
        return f"{self.SOURCE_KEY}_{stub['language']}_{stub['publication_id']}"

    def discover_documents(self) -> Iterator[dict]:
        """Yield one lightweight stub per (language, publication). Body is
        fetched later in run() so already-seen publications are skipped before
        any cipher GETs."""
        for lang in self.languages:
            for path, doc_type, label in self.TREES:
                toc_url = f"{BASE}/{path}/tableOfContent.xhtml?label=true"
                try:
                    r = self.get(toc_url, headers=self._lang_headers(lang))
                    r.raise_for_status()
                except Exception as e:
                    logger.warning("[estv_mwst] %s/%s TOC failed: %s", lang, path, e)
                    continue
                pubs = _parse_toc(r.text)
                logger.info("[estv_mwst] %s/%s: %d publications", lang, path, len(pubs))
                for pub_id, pub_title in pubs:
                    num = _leading_number(pub_title)
                    yield {
                        "publication_id": pub_id,
                        "path": path,
                        "language": lang,
                        "doc_type": doc_type,
                        "title": pub_title,
                        "doc_number": f"{label} {num}" if num else label,
                        "url": f"{BASE}/{path}/tableOfContent.xhtml?publicationId={pub_id}",
                        "topics": ["MWST", "Mehrwertsteuer", "VAT"],
                    }

    def _fetch_publication(self, stub: dict) -> tuple[str, str]:
        """Fetch every cipher section of a publication, concatenate the prose in
        document order, and return (body_text, date_iso)."""
        lang, path, pub = stub["language"], stub["path"], stub["publication_id"]
        hdr = self._lang_headers(lang)
        pub_toc = f"{BASE}/{path}/tableOfContent.xhtml?publicationId={pub}"
        try:
            r = self.get(pub_toc, headers=hdr)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[estv_mwst] pub %s TOC failed: %s", pub, e)
            return "", ""

        comp_ids = _parse_publication_ciphers(r.text)
        headings = _parse_publication_headings(r.text)
        parts: list[str] = []
        date_iso = ""
        for comp in comp_ids:
            cu = f"{BASE}/{path}/cipherDisplay.xhtml?publicationId={pub}&componentId={comp}"
            try:
                cr = self.get(cu, headers=hdr)
                cr.raise_for_status()
            except Exception as e:
                logger.debug("[estv_mwst] cipher %s/%s failed: %s", pub, comp, e)
                continue
            body, d = _parse_cipher(cr.text)
            # The section's number and title go in as its own line, also for
            # heading-only container nodes, so the text keeps the outline.
            head = headings.get(comp, "")
            if head and body:
                parts.append(f"{head}\n{body}")
            elif head:
                parts.append(head)
            elif body:
                parts.append(body)
            if d and not date_iso:
                date_iso = d
        return "\n\n".join(parts), date_iso

    def run(self, *, max_new: int | None = None, force_refresh: bool = False) -> dict:
        """Publication-level override of the PDF-centric base loop (the content
        here is HTML, assembled from several cipher GETs per document). Reuses
        the base dedup / normalize / append / stats machinery."""
        import time
        t0 = time.time()
        new_count = skipped_count = failed_count = 0

        for stub in self.discover_documents():
            doc_id = self._make_doc_id(stub)
            if doc_id in self._seen_ids and not force_refresh:
                skipped_count += 1
                continue

            body, date_iso = self._fetch_publication(stub)
            if len(body) < self.MIN_BODY_CHARS:
                logger.warning("[estv_mwst] body too short for %s (%d chars)",
                               doc_id, len(body))
                failed_count += 1
                continue
            if date_iso:
                stub["date"] = date_iso

            doc = self._normalize(stub, body)
            self._append(doc)
            new_count += 1
            logger.info("[estv_mwst] +%s '%s' (%d chars)",
                        doc["doc_number"], doc["title"][:70], len(body))

            if max_new and new_count >= max_new:
                logger.info("[estv_mwst] hit max_new=%d, stopping early", max_new)
                break

        summary = {
            "source": self.SOURCE_KEY,
            "new": new_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "duration_s": round(time.time() - t0, 1),
        }
        logger.info("[estv_mwst] done: %s", summary)
        return summary


# urljoin/sha256_hex imported for parity with sibling scrapers + future use.
_ = (urljoin, sha256_hex)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    EstvMwstScraper(languages=("de",)).run(max_new=2)
