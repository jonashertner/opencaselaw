"""Historical Botschaften (1850-1999) from the Bundesarchiv scans — offline.

Covers the four pieces of the backfill without any network:

* discovery: SPARQL fixture rows → candidates (ELI volume/page pattern,
  memorialPage first, Fedlex citation, host classification);
* the OCR cleaner on a 1930s-style page fixture (markers, masthead,
  hyphenation, headings, salutations, page-break stitching, archive sheet);
* the polite fetcher with a scripted transport (spacing, backoff, hard
  stop, 404 handling, Retry-After);
* ingest + resume on a temp materialien.db (additive columns, state
  table, --max-per-run, collisions, host opt-in, idempotent re-runs).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers import fedlex_materialien as fm  # noqa: E402
from search_stack import bar_fetcher as bf  # noqa: E402
from search_stack import botschaft_historical as bh  # noqa: E402
from search_stack.build_botschaft_corpus import (  # noqa: E402
    ensure_schema,
    parse_botschaft_text,
)

BAR = "https://www.amtsdruckschriften.bar.admin.ch/viewOrigDoc.do?id=%d&action=open"
# A 200 body the fetcher accepts: PDF magic bytes and at least 1 KB.
PDF = b"%PDF-1.4 stub\n" + b"%" * 1100


# ── Discovery ─────────────────────────────────────────────────────────


def test_parse_historical_fga_eli_volume_and_pages() -> None:
    p = fm.parse_historical_fga_eli("https://fedlex.data.admin.ch/eli/fga/1975/1_609__")
    assert p == {"year": 1975, "volume": 1, "pages": {"de": 609, "fr": None, "it": None}}
    p = fm.parse_historical_fga_eli("https://fedlex.data.admin.ch/eli/fga/1998/1_269_205_179")
    assert p["pages"] == {"de": 269, "fr": 205, "it": 179}
    p = fm.parse_historical_fga_eli("https://fedlex.data.admin.ch/eli/fga/1955/2_1013_1009_")
    assert p["volume"] == 2 and p["pages"]["fr"] == 1009 and p["pages"]["it"] is None
    # Modern ELIs have no volume segment.
    assert fm.parse_historical_fga_eli("https://fedlex.data.admin.ch/eli/fga/2011/1325") is None


def test_classify_file_host() -> None:
    assert fm.classify_file_host(BAR % 1) == "bar"
    assert fm.classify_file_host("https://fedlex.data.admin.ch/filestore/x.pdf") == "fedlex"
    assert fm.classify_file_host("https://example.org/x.pdf") == "example.org"
    assert fm.classify_file_host(None) == ""


def _row(act, title, mem=None, ident=None, pub=None, file=None, hid=None):
    r = {"act": {"value": act}, "title": {"value": title}}
    if mem is not None:
        r["memPage"] = {"value": mem}
    if ident:
        r["ident"] = {"value": ident}
    if hid:
        r["hid"] = {"value": hid}
    if pub:
        r["pubDate"] = {"value": pub}
    if file:
        r["file"] = {"value": file}
    return r


def test_discover_untyped_parses_sparql_rows(monkeypatch) -> None:
    rows = [
        _row("https://fedlex.data.admin.ch/eli/fga/1975/1_609__",
             "Botschaft des Bundesrates über den Erwerb von zwei Heizöltanks",
             mem="609", ident="BBl 1975 I 609", pub="1975-02-24", file=BAR % 10046299),
        # memorialPage wins over the ELI segment when they disagree.
        _row("https://fedlex.data.admin.ch/eli/fga/1998/1_269_205_179",
             "Botschaft zur Volksinitiative", mem="270", file=BAR % 10054544),
        # No dct:identifier but a historicalLegalId: that string is the citation.
        _row("https://fedlex.data.admin.ch/eli/fga/1866/2_223__",
             "Botschaft betreffend X", hid="BBl 1866 II 223", file=BAR % 5),
        # Duplicate row for the same work (second title) — dropped.
        _row("https://fedlex.data.admin.ch/eli/fga/1975/1_609__",
             "Botschaft (Zweittitel)", mem="609", file=BAR % 10046299),
        # Title does not start with the prefix — dropped.
        _row("https://fedlex.data.admin.ch/eli/fga/1975/1_700__", "Bericht des Bundesrates",
             mem="700", file=BAR % 6),
        # No file — nothing to fetch, dropped.
        _row("https://fedlex.data.admin.ch/eli/fga/1975/1_800__", "Botschaft ohne Datei",
             mem="800"),
        # Fedlex's own store: kept, host 'fedlex'.
        _row("https://fedlex.data.admin.ch/eli/fga/1999/1_9000__", "Botschaft auf Fedlex",
             mem="9000", ident="BBl 1999 I 9000",
             file="https://fedlex.data.admin.ch/filestore/abc-pdf-a.pdf"),
    ]
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=600: rows)
    out = fm.discover_fga_botschaften_untyped("de")
    assert [c.eli_uri.rsplit("/", 1)[-1] for c in out] == ["1_609__", "1_269_205_179", "2_223__", "1_9000__"]
    c0 = out[0]
    assert (c0.year, c0.page, c0.title, c0.eli_uri, c0.file_url, c0.file_host) == (
        1975, 609, "Botschaft des Bundesrates über den Erwerb von zwei Heizöltanks",
        "https://fedlex.data.admin.ch/eli/fga/1975/1_609__", BAR % 10046299, "bar")
    assert c0.citation == "BBl 1975 I 609" and c0.publication_date == "1975-02-24"
    assert c0.volume == 1 and c0.language == "de"
    assert out[1].page == 270 and out[1].volume == 1
    assert out[2].citation == "BBl 1866 II 223" and out[2].volume == 2   # from historicalLegalId
    assert out[3].file_host == "fedlex"


def test_discover_untyped_french_page_from_eli_when_no_mempage(monkeypatch) -> None:
    """No identifier of either kind: the parts stay separate and the
    citation is None — never 'FF 1998 I 205' assembled from them."""
    rows = [_row("https://fedlex.data.admin.ch/eli/fga/1998/1_269_205_179",
                 "Message concernant l'initiative", file=BAR % 1)]
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=600: rows)
    out = fm.discover_fga_botschaften_untyped("fr")
    assert out[0].page == 205 and out[0].volume == 1 and out[0].year == 1998
    assert out[0].citation is None


def test_discover_untyped_query_asks_for_both_citation_predicates(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=600: seen.setdefault("q", q) and [])
    fm.discover_fga_botschaften_untyped("de")
    assert "dct:identifier ?ident" in seen["q"] and "jolux:historicalLegalId ?hid" in seen["q"]


def test_discover_untyped_query_excludes_type_23_and_rejects_language(monkeypatch) -> None:
    seen = {}

    def fake(q, timeout=600):
        seen["q"] = q
        return []

    monkeypatch.setattr(fm, "sparql_query", fake)
    assert fm.discover_fga_botschaften_untyped("it") == []
    assert "FILTER NOT EXISTS" in seen["q"] and "resource-type/23" in seen["q"]
    assert '"messaggio"' in seen["q"]
    with pytest.raises(ValueError):
        fm.discover_fga_botschaften_untyped("rm")


# ── OCR cleaner ───────────────────────────────────────────────────────

# Shape of the 1935 scan (BBl 1935 I 350): page number and Geschäftsnummer on
# top, #ST# markers, the title block on several centred lines, salutations,
# justified body with line-end hyphenation, an enumerated heading, a
# paragraph running over the page break, a printer's foot signature, and
# the archive's catalogue sheet as the last page.
PAGE_1 = """350
3228
#ST#
Botschaft
des
Bundesrates an die Bundesversammlung über die Fortsetzung
und Erweiterung der Hilfsmassnahmen für das Hotelgewerbe.
(Vom 8. März 1935.)
Herr Präsident!
Hochgeehrte Herren!
Wir beehren uns, Ihnen hiermit eine Vorlage über die Fortsetzung und die
Erweiterung der Hilfsmassnahmen für das Hotelgewerbe samt dem Entwurf von
zwei Bundesbeschlüssen mit folgender Botschaft zu unterbreiten.
I. Die bisherigen staatlichen Hilfsmassnahmen.
Der Bund kam schon wiederholt in die Lage, dem Hotelgewerbe helfen zu müssen.
Diese Stützungsaktionen umfassen insbesondere die Beteiligung an der Schweize-
rischen Hotel-Treuhand-Gesellschaft und die Hilfe des grossen Kantons Basel-
Stadt sowie deren Subventionierung durch den Bund.
Der Regierungsrat des Kantons Basel-Stadt richtete mit Schreiben vom
12. März 1934 an den Bundesrat das Gesuch, der Bund wolle an die Kosten der
Baggerungen einen Beitrag gewähren, was den Wiederaufschwung des Verkehrs im
Fremdenverkehrs ziemlich genau mit dem Ablauf der Geltungsdauer der Ordnung
Bundesblatt. 87. Jahrg. Bd. I. 25"""

PAGE_2 = """351
zusammen. Der Hôtellerie gelang es fast durchwegs, in den Jahren 1926 bis
und mit 1930 ihren Verpflichtungen nachzukommen und Reserven zu bilden.
Sehr geehrter Herr Präsident,
sehr geehrte Damen und Herren,
Wir unterbreiten Ihnen ein Objektkreditbegehren im Betrag von 7 200 000
Franken für den Erwerb von zwei Heizöltanks in Eclépens.
l Bemessung der Vorratshaltung
Mit Bundesratsbeschluss vom 4. Juli 1958 wurde das Oberkriegskommissa-
riat beauftragt, die organisatorischen Massnahmen zu ergreifen.
Art. 2
1 Dieser Beschluss ist nicht allgemein verbindlich; er untersteht nicht dem
Referendum.
1975-45"""

PAGE_3 = """Schweizerisches Bundesarchiv, Digitale Amtsdruckschriften
Archives fédérales suisses, Publications officielles numérisées
Botschaft des Bundesrates an die Bundesversammlung über die Fortsetzung
In Bundesblatt
Jahr 1935
Das Dokument wurde durch das Schweizerische Bundesarchiv digitalisiert."""


def test_cleaner_strips_markers_numbers_masthead_and_archive_sheet() -> None:
    cleaned = bh.clean_historical_pages([PAGE_1, PAGE_2, PAGE_3])
    joined = "\n".join(cleaned)
    assert "#ST#" not in joined
    assert "3228" not in joined                 # Geschäftsnummer line at the top
    assert "Jahrg. Bd." not in joined           # printer's foot signature
    assert "1975-45" not in joined              # print-job number
    assert "Bundesarchiv digitalisiert" not in joined
    assert cleaned[2] == ""                     # the catalogue sheet is not the document
    for para in joined.split("\n\n"):
        assert para.strip() not in ("350", "351")


def test_cleaner_joins_title_block_and_separates_salutations() -> None:
    paras = bh.clean_historical_pages([PAGE_1, PAGE_2, PAGE_3])[0].split("\n\n")
    assert paras[0] == ("Botschaft des Bundesrates an die Bundesversammlung über die "
                        "Fortsetzung und Erweiterung der Hilfsmassnahmen für das "
                        "Hotelgewerbe. (Vom 8. März 1935.)")
    assert paras[1] == "Herr Präsident!"
    assert paras[2] == "Hochgeehrte Herren!"
    assert paras[3].startswith("Wir beehren uns") and paras[3].endswith("zu unterbreiten.")


def test_cleaner_hyphenation_headings_and_dates() -> None:
    page1 = bh.clean_historical_pages([PAGE_1, PAGE_2, PAGE_3])[0]
    paras = page1.split("\n\n")
    assert "I. Die bisherigen staatlichen Hilfsmassnahmen." in paras   # its own paragraph
    body = next(p for p in paras if p.startswith("Der Bund kam"))
    assert "Schweizerischen Hotel-Treuhand-Gesellschaft" in body        # hyphen rejoined
    assert "Basel-Stadt" in body                                         # compound kept
    # "12. März 1934 …" continues a sentence: not a heading, no split.
    gesuch = next(p for p in paras if p.startswith("Der Regierungsrat"))
    assert "Schreiben vom 12. März 1934 an den Bundesrat" in gesuch


def test_cleaner_stitches_paragraph_across_page_break() -> None:
    cleaned = bh.clean_historical_pages([PAGE_1, PAGE_2, PAGE_3])
    p1_last = cleaned[0].split("\n\n")[-1]
    assert p1_last.endswith("Geltungsdauer der Ordnung zusammen. Der Hôtellerie gelang es fast durchwegs, "
                            "in den Jahren 1926 bis und mit 1930 ihren Verpflichtungen "
                            "nachzukommen und Reserven zu bilden.")
    assert not cleaned[1].startswith("zusammen.")
    # The corpus parser keeps the page the paragraph starts on.
    rows = list(parse_botschaft_text(cleaned, "de"))
    stitched = next(r for r in rows if "Der Hôtellerie gelang" in r["text"])
    assert stitched["page_number"] == 1


def test_cleaner_page_two_salutation_heading_and_article() -> None:
    paras = bh.clean_historical_pages([PAGE_1, PAGE_2, PAGE_3])[1].split("\n\n")
    assert "Sehr geehrter Herr Präsident," in paras
    assert "sehr geehrte Damen und Herren," in paras
    assert "l Bemessung der Vorratshaltung" in paras       # OCR'd "1" — still a heading line
    assert any(p.startswith("Mit Bundesratsbeschluss") and "Oberkriegskommissariat" in p
               for p in paras)
    # A bare "Art. 2" line heads the paragraph that follows it, so the
    # corpus parser (article on the first line) anchors it.
    assert any(p.startswith("Art. 2\n1 Dieser Beschluss") for p in paras)
    rows = list(parse_botschaft_text(bh.clean_historical_pages([PAGE_2]), "de"))
    after = next(r for r in rows if "1 Dieser Beschluss" in r["text"])
    assert after["article_anchor"] == "2"
    assert after["text"].endswith("nicht dem Referendum.")


def test_cleaner_suspended_hyphen_keeps_its_space() -> None:
    page = ("610\n"
            "Vor die Notwendigkeit gestellt, das Hilfswerk der Kriegs- und Nachkriegs-\n"
            "zeit zugunsten der Hôtellerie zu sichern, entschloss sich der Rat, die Kriegs-\n"
            "und Nachkriegszeit gleich zu behandeln und die Sache zu beenden.\n")
    out = bh.clean_historical_page(page)
    assert "Kriegs- und Nachkriegszeit zugunsten" in out
    assert "die Kriegs- und Nachkriegszeit gleich" in out


def test_cleaner_never_rewrites_words() -> None:
    """OCR misreads stay: the corpus is verbatim (R1-R3)."""
    page = "610\nDer Begierungsrat des Kantons prüfte die Eeihe von Verordnungen.\n"
    out = bh.clean_historical_page(page)
    assert "Begierungsrat" in out and "Eeihe" in out


def test_cleaner_keeps_bare_numbers_and_gazette_citations_in_the_body() -> None:
    """Body text is never deleted: a line is only dropped by position (the
    first / last lines of a page) and pattern together."""
    table = ("612\n"
             "Die Beiträge des Bundes betrugen in den letzten Jahren:\n"
             "1930\n450 000\n1931\n380 000\n"
             "Die Summe ist beträchtlich.\n")
    out = bh.clean_historical_page(table)
    assert "612" not in out.split()                       # the page number went
    for tok in ("1930", "450 000", "1931", "380 000"):
        assert tok in out                                  # the table stayed
    cite = ("614\n"
            "Siehe dazu die Ausführungen in der Botschaft vom 12. März 1934, abgedruckt im\n"
            "Bundesblatt 1934, Bd. I, S. 350 und die dortigen Ausführungen.\n")
    out = bh.clean_historical_page(cite)
    assert "Bundesblatt 1934, Bd. I, S. 350 und die dortigen Ausführungen." in out
    # A foot line at the very end of the page is still recognised.
    foot = cite + "Bundesblatt. 87. Jahrg. Bd. I. 25\n"
    out = bh.clean_historical_page(foot)
    assert "Jahrg." not in out and "S. 350 und die dortigen" in out


def test_cleaner_masthead_words_survive_on_ordinary_pages() -> None:
    """The masthead patterns only apply below the gazette's title line."""
    for first in ("Die Nachnahme wird von der Post nur gegen Gebühr besorgt, was die",
                  "Im ersten Halbjahr 1934 stiegen die Einnahmen der Bundesbahnen,",
                  "Der Preis von 12 Franken je Tonne war für die Bauern zu hoch, weshalb",
                  "Band II der Gesetzessammlung enthält die einschlägigen Bestimmungen,",
                  "Der 87. Jahrgang des Bundesblattes bringt eine Reihe von Botschaften,"):
        page = f"613\n{first}\nEinnahmen der Verwaltung schmälert.\n"
        out = bh.clean_historical_page(page)
        assert first in out, first
    # An issue-first page: the masthead block above the title is dropped,
    # the Geschäftsnummer too, the title block stays.
    issue = ("Bundesblatt\n"
             "87. Jahrgang. Bern, den 13. März 1935. Band I.\n"
             "Erscheint wöchentlich. Preis 20 Franken im Jahr, 10 Franken im Halbjahr.\n"
             "Einrückungsgebühr: 50 Rappen die Petitzeile. Inserate an Stämpfli & Cie. in Bern.\n"
             "#ST#\n3228\nBotschaft\ndes\nBundesrates an die Bundesversammlung.\n"
             "(Vom 8. März 1935.)\nWir beehren uns, Ihnen zu berichten.\n"
             "Bundesblatt. 87. Jahrg. Bd. I. 25")
    out = bh.clean_historical_page(issue)
    assert out.startswith("Botschaft des Bundesrates an die Bundesversammlung. (Vom 8. März 1935.)")
    assert "Stämpfli" not in out and "3228" not in out and "Jahrg." not in out
    assert "Wir beehren uns, Ihnen zu berichten." in out


def test_cleaner_second_line_number_is_body_unless_a_title_follows() -> None:
    page = "351\n1930\nwar ein schwieriges Jahr für die Hôtellerie, wie die Zahlen zeigen.\n"
    out = bh.clean_historical_page(page)
    assert "1930" in out and "351" not in out.split()


@pytest.mark.parametrize("prev, nxt, lang, want", [
    ("Schweize-", "rischen", "de", "Schweizerischen"),
    ("Basel-", "Stadt", "de", "Basel-Stadt"),
    ("Conseil fé-", "déral", "fr", "Conseil fédéral"),
    ("est peut-", "être nécessaire", "fr", "est peut-être nécessaire"),
    ("c'est-à-", "dire indispensable", "fr", "c'est-à-dire indispensable"),
    ("au-", "dessus", "fr", "au-dessus"),
    ("vis-à-", "vis", "fr", "vis-à-vis"),
    ("le porte-", "parole", "fr", "le porte-parole"),
    ("Saint-", "Gall", "fr", "Saint-Gall"),
    ("a-t-", "il", "fr", "a-t-il"),
    ("nous-", "mêmes", "fr", "nous-mêmes"),
    ("ci-", "joint", "fr", "ci-joint"),
    ("un contre-", "projet", "fr", "un contre-projet"),
    ("l'avant-", "projet", "fr", "l'avant-projet"),
    ("la pré-", "sidence", "fr", "la présidence"),         # a syllable, not a prefix
    ("l'entre-", "prise", "fr", "l'entreprise"),
    ("l'au-", "torité", "fr", "l'autorité"),
    ("le commer-", "ce qui", "fr", "le commerce qui"),
    ("il vice-", "presidente", "it", "il vice-presidente"),
    ("il Consi-", "glio federale", "it", "il Consiglio federale"),
    ("gli anni 1934-", "1935", "it", "gli anni 1934-1935"),
    ("Kriegs-", "und Nachkriegszeit", "de", "Kriegs- und Nachkriegszeit"),
    ("Absatz 2-", "), der", "de", "Absatz 2- ), der"),      # in doubt: hyphen and a space
])
def test_join_hyphen_is_conservative(prev, nxt, lang, want) -> None:
    assert bh._join_hyphen(prev, nxt, lang) == want


def test_cleaner_french_page_keeps_real_hyphens() -> None:
    page = ("205\n"
            "Le Conseil fédéral estime qu'il est peut-\n"
            "être nécessaire, c'est-à-\n"
            "dire indispensable, de placer le porte-\n"
            "parole du canton de Saint-\n"
            "Gall au-\n"
            "dessus de tout soupçon.\n")
    out = bh.clean_historical_page(page, "fr")
    assert "peut-être" in out and "c'est-à-dire" in out and "porte-parole" in out
    assert "Saint-Gall" in out and "au-dessus" in out
    assert "peutêtre" not in out and "audessus" not in out


# ── Fetcher ───────────────────────────────────────────────────────────


class Script:
    """Scripted transport: each entry is (status, headers, body) or an
    Exception instance to raise. Records URLs and the headers sent."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        e = self.entries.pop(0)
        if isinstance(e, Exception):
            raise e
        return e


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _fetcher(entries, **kw):
    clock = Clock()
    script = Script(entries)
    f = bf.PoliteFetcher(transport=script, sleep=clock.sleep, clock=clock, **kw)
    return f, script, clock


def test_fetcher_identifies_itself_and_spaces_requests() -> None:
    f, script, clock = _fetcher([(200, {}, PDF + b"1"), (200, {}, PDF + b"2")])
    r1 = f.fetch("u1")
    assert r1.ok and r1.content == PDF + b"1"
    ua = script.calls[0][1]["User-Agent"]
    assert ua.startswith("OpenCaseLaw/1.0") and "jonashertner@protonmail.ch" in ua
    assert "opencaselaw.ch" in ua
    clock.t += 0.4           # the download took 0.4 s; the next call comes at once
    f.fetch("u2")
    assert clock.sleeps == [pytest.approx(2.5 - 0.4)]     # >= 2.5 s after the last response
    assert f.requests_made == 2 and f.consecutive_failures == 0


def test_fetcher_rejects_interval_below_floor() -> None:
    with pytest.raises(ValueError):
        bf.PoliteFetcher(min_interval=1.0, transport=lambda *a: (200, {}, b""))


def test_fetcher_backs_off_then_stops_after_three_failures() -> None:
    f, script, clock = _fetcher([(503, {}, b""), (429, {"retry-after": "45"}, b""), (500, {}, b"")])
    r = f.fetch("u1")
    assert not r.ok and r.status == 503 and f.consecutive_failures == 1
    assert clock.sleeps[-1] == 15.0                  # backoff 15 s
    f.fetch("u2")
    assert clock.sleeps[-1] == 45.0                  # max(30 s backoff, Retry-After 45)
    with pytest.raises(bf.FetchStopped):
        f.fetch("u3")
    assert f.stopped and "3 consecutive failures" in f.stop_reason
    with pytest.raises(bf.FetchStopped):             # nothing more this run
        f.fetch("u4")
    assert len(script.calls) == 3


def test_fetcher_transport_error_counts_as_failure_and_success_resets() -> None:
    f, script, clock = _fetcher([ConnectionError("boom"), (200, {}, PDF), (403, {}, b"")])
    r = f.fetch("u1")
    assert r.status is None and "ConnectionError" in r.error and f.consecutive_failures == 1
    assert f.fetch("u2").ok and f.consecutive_failures == 0
    f.fetch("u3")
    assert f.consecutive_failures == 1               # counter restarted after the success


def test_fetcher_soft_block_with_200_counts_toward_the_stop() -> None:
    """A login / captcha / 'too many requests' page served with 200, a
    body without PDF magic bytes, or one under 1 KB is a failure."""
    html = b"<html><body>Too many requests - please solve the CAPTCHA</body></html>"
    f, script, clock = _fetcher([(200, {"content-type": "text/html"}, html),
                                 (200, {}, b"%PDF-1.4 short"),
                                 (200, {}, b"<html>Login</html>")])
    r = f.fetch("u1")
    assert not r.ok and r.status == 200 and r.content is None
    assert "not a PDF" in r.error and "captcha" in r.error and f.consecutive_failures == 1
    assert clock.sleeps[-1] == 15.0                       # backed off like a 503
    r = f.fetch("u2")
    assert not r.ok and "short body" in r.error and f.consecutive_failures == 2
    with pytest.raises(bf.FetchStopped):
        f.fetch("u3")
    assert f.stopped and "3 consecutive failures" in f.stop_reason
    # A real PDF resets the counter.
    f2, _, _ = _fetcher([(200, {}, b"<html>captcha</html>"), (200, {}, PDF)])
    f2.fetch("a")
    assert f2.fetch("b").ok and f2.consecutive_failures == 0


def test_fetcher_404_is_per_document_not_a_burst() -> None:
    f, script, clock = _fetcher([(404, {}, b""), (404, {}, b""), (404, {}, b""), (404, {}, b"")])
    for u in ("a", "b", "c", "d"):
        r = f.fetch(u)
        assert r.status == 404 and not r.ok
    assert not f.stopped and f.consecutive_failures == 0
    assert all(s == pytest.approx(2.5) for s in clock.sleeps)   # only pacing, no backoff


# ── Ingest + resume ───────────────────────────────────────────────────


def _cand(year, page, eli_tail, *, host="bar", title="Botschaft X", lang="de", vol=1, n=1):
    url = BAR % n if host == "bar" else "https://fedlex.data.admin.ch/filestore/%d.pdf" % n
    return fm.UntypedBotschaft(
        year=year, page=page, title=title,
        eli_uri=f"https://fedlex.data.admin.ch/eli/fga/{year}/{eli_tail}",
        file_url=url, file_host=host, citation=f"BBl {year} I {page}",
        publication_date=f"{year}-01-01", volume=vol, language=lang,
    )


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "materialien.db")
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture()
def fake_pdf(monkeypatch):
    """Transport bytes are a stub PDF; the page extractor returns the fixture."""
    monkeypatch.setattr(bh, "_extract_pages", lambda b: [PAGE_1, PAGE_2, PAGE_3])


def _run(conn, cands, fetcher, **kw):
    opts = dict(language="de", host_allow={"bar"}, min_year=1850, max_year=1999,
                max_per_run=None, max_attempts=3, min_interval=2.5,
                discover=lambda lang: cands, fetcher=fetcher, lock_path=None)
    opts.update(kw)
    return bh.run_historical_backfill(conn, **opts)


def test_ensure_schema_adds_columns_to_an_older_db(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("""CREATE TABLE botschaft_documents (
        botschaft_id INTEGER PRIMARY KEY AUTOINCREMENT, bbl_year INTEGER NOT NULL,
        bbl_page INTEGER NOT NULL, bbl_citation TEXT NOT NULL, eli_uri TEXT, title TEXT,
        publication_date TEXT, source_url TEXT NOT NULL,
        format TEXT NOT NULL CHECK(format IN ('akoma-ntoso-xml','pdf')),
        language TEXT NOT NULL, page_count INTEGER, text_hash TEXT, ingested_at TEXT NOT NULL,
        UNIQUE(bbl_year, bbl_page, language))""")
    conn.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, source_url, "
                 "format, language, ingested_at) VALUES (2020, 1, 'BBl 2020 1', 'u', 'pdf', 'de', 't')")
    ensure_schema(conn)
    ensure_schema(conn)      # idempotent
    cols = [r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")]
    assert cols[-2:] == ["source_collection", "bbl_volume"]
    row = conn.execute("SELECT source_collection, bbl_volume FROM botschaft_documents").fetchone()
    assert row == (None, None)                       # old rows untouched
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "botschaft_fetch_state" in tables
    with pytest.raises(sqlite3.IntegrityError):      # the CHECK is still there
        conn.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, "
                     "source_url, format, language, ingested_at) VALUES (2020, 2, 'x', 'u', "
                     "'docx', 'de', 't')")
    conn.close()


def test_backfill_stores_document_with_fedlex_metadata(db, fake_pdf) -> None:
    f, script, clock = _fetcher([(200, {}, PDF)])
    cands = [_cand(1935, 350, "1_350__", title="Botschaft über das Hotelgewerbe", n=10032582)]
    stats = _run(db, cands, f)
    assert stats["done"] == 1 and stats["requests_made"] == 1
    doc = db.execute("SELECT bbl_year, bbl_page, bbl_citation, eli_uri, title, publication_date, "
                     "source_url, format, language, page_count, text_hash, source_collection, "
                     "bbl_volume FROM botschaft_documents").fetchone()
    assert doc[:5] == (1935, 350, "BBl 1935 I 350", cands[0].eli_uri,
                       "Botschaft über das Hotelgewerbe")
    assert doc[5] == "1935-01-01" and doc[6] == BAR % 10032582
    assert doc[7] == "pdf" and doc[8] == "de" and doc[9] == 3
    assert len(doc[10]) == 64 and doc[11] == "bar-amtsdruckschriften" and doc[12] == 1
    n_par = db.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0]
    assert n_par >= 10
    assert db.execute("SELECT COUNT(*) FROM botschaft_paragraphs_fts WHERE "
                      "botschaft_paragraphs_fts MATCH 'Hotelgewerbe'").fetchone()[0] >= 1
    st = db.execute("SELECT status, attempts, http_status, botschaft_id FROM botschaft_fetch_state").fetchone()
    assert st == ("done", 1, 200, doc and 1)


def test_backfill_respects_max_per_run_and_resumes(db, fake_pdf) -> None:
    cands = [_cand(1975, 609, "1_609__", n=1), _cand(1955, 1077, "1_1077__", n=2),
             _cand(1935, 350, "1_350__", n=3)]
    f1, s1, _ = _fetcher([(200, {}, PDF)] * 3)
    st1 = _run(db, cands, f1, max_per_run=1)
    assert st1["done"] == 1 and st1["stopped"] == "max_per_run" and len(s1.calls) == 1
    assert s1.calls[0][0] == BAR % 1                  # newest first
    f2, s2, _ = _fetcher([(200, {}, PDF)] * 3)
    st2 = _run(db, cands, f2)
    assert st2["done"] == 2 and st2["skipped"]["terminal"] == 1
    assert [c[0] for c in s2.calls] == [BAR % 2, BAR % 3]   # the done one is not re-fetched
    f3, s3, _ = _fetcher([])
    st3 = _run(db, cands, f3)
    assert st3["eligible"] == 0 and s3.calls == []         # idempotent: no request at all
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 3


def test_backfill_collision_and_host_opt_in(db, fake_pdf) -> None:
    cands = [_cand(1866, 223, "1_223__", n=1),
             _cand(1866, 223, "2_223__", vol=2, n=2),          # same (year, page): collision
             _cand(1999, 9000, "1_9000__", host="fedlex", n=3)]  # not allowed by --host-allow bar
    f, script, _ = _fetcher([(200, {}, PDF)] * 3)
    st = _run(db, cands, f)
    assert st["skipped"]["host"] == 1
    assert st["done"] == 1 and st["collision"] == 1 and len(script.calls) == 1
    rows = dict(db.execute("SELECT eli_uri, status FROM botschaft_fetch_state").fetchall())
    assert rows[cands[0].eli_uri] == "done" and rows[cands[1].eli_uri] == "collision"
    assert cands[2].eli_uri not in rows
    err = db.execute("SELECT error FROM botschaft_fetch_state WHERE status='collision'").fetchone()[0]
    assert cands[0].eli_uri in err
    # With both hosts allowed the Fedlex file is fetched too.
    f2, s2, _ = _fetcher([(200, {}, PDF)])
    st2 = _run(db, cands, f2, host_allow={"bar", "fedlex"})
    assert st2["done"] == 1 and s2.calls[0][0].startswith("https://fedlex.data.admin.ch/")


def test_backfill_hard_stop_records_state_and_retries_next_run(db, fake_pdf) -> None:
    cands = [_cand(1975, 609, "1_609__", n=1), _cand(1955, 1077, "1_1077__", n=2),
             _cand(1935, 350, "1_350__", n=3), _cand(1900, 1, "1_1__", n=4)]
    f, script, _ = _fetcher([(503, {}, b""), (503, {}, b""), (503, {}, b"")])
    st = _run(db, cands, f)
    assert st["stopped"] and "3 consecutive failures" in st["stopped"]
    assert st["done"] == 0 and len(script.calls) == 3
    state = {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT eli_uri, status, attempts FROM botschaft_fetch_state")}
    assert state[cands[0].eli_uri] == ("error", 1)      # 503, backoff 15 s, moved on
    assert state[cands[1].eli_uri] == ("error", 1)      # 503, backoff 30 s
    assert state[cands[2].eli_uri] == ("error", 1)      # 503 → hard stop
    assert cands[3].eli_uri not in state                # never attempted this run
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    # Next run: the three are retried (attempts < max) and the fourth is new.
    f2, s2, _ = _fetcher([(200, {}, PDF)] * 4)
    st2 = _run(db, cands, f2)
    assert st2["done"] == 4
    assert sorted(r[0] for r in db.execute("SELECT attempts FROM botschaft_fetch_state")) == [1, 2, 2, 2]
    # Exhausted attempts are left alone.
    db.execute("UPDATE botschaft_fetch_state SET status='error', attempts=3")
    db.execute("DELETE FROM botschaft_documents")
    db.commit()
    f3, s3, _ = _fetcher([])
    st3 = _run(db, cands, f3)
    assert st3["skipped"]["attempts_exhausted"] == 4 and s3.calls == []


def test_backfill_not_a_pdf_and_no_text(db, monkeypatch) -> None:
    cands = [_cand(1975, 609, "1_609__", n=1), _cand(1955, 1077, "1_1077__", n=2)]
    monkeypatch.setattr(bh, "_extract_pages", lambda b: ["", "  "])
    f, script, clock = _fetcher([(200, {}, b"<html>login</html>"), (200, {}, PDF)])
    st = _run(db, cands, f)
    assert st["error"] == 1 and st["no_text"] == 1
    rows = dict(db.execute("SELECT eli_uri, status FROM botschaft_fetch_state").fetchall())
    assert rows[cands[0].eli_uri] == "error" and rows[cands[1].eli_uri] == "no_text"
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    err = db.execute("SELECT error FROM botschaft_fetch_state WHERE status='error'").fetchone()[0]
    assert "login" in err and 15.0 in clock.sleeps          # the soft block was backed off


def test_backfill_soft_block_three_times_stops_the_run(db, fake_pdf) -> None:
    """A portal answering every request with a login page is a block."""
    cands = [_cand(1975, 609, "1_609__", n=1), _cand(1955, 1077, "1_1077__", n=2),
             _cand(1935, 350, "1_350__", n=3), _cand(1900, 1, "1_1__", n=4)]
    page = (200, {"content-type": "text/html"}, b"<html>Too many requests</html>")
    f, script, _ = _fetcher([page, page, page, page])
    st = _run(db, cands, f)
    assert st["stopped"] and "3 consecutive failures" in st["stopped"]
    assert len(script.calls) == 3 and st["done"] == 0
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0


class _FailingParagraphs(sqlite3.Connection):
    """A connection whose paragraph insert fails while ``fail`` is set."""
    fail = False

    def executemany(self, sql, rows):
        if self.fail and "INSERT INTO botschaft_paragraphs" in sql:
            raise sqlite3.OperationalError("disk I/O error")
        return super().executemany(sql, rows)


def test_backfill_never_commits_a_document_without_its_paragraphs(tmp_path, fake_pdf) -> None:
    db = sqlite3.connect(tmp_path / "m.db", factory=_FailingParagraphs)
    db.execute("PRAGMA foreign_keys = ON")
    ensure_schema(db)
    cands = [_cand(1975, 609, "1_609__", n=1)]
    db.fail = True
    f, script, _ = _fetcher([(200, {}, PDF)])
    st = _run(db, cands, f)
    assert st["error"] == 1 and not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] == 0
    row = db.execute("SELECT status, attempts, error FROM botschaft_fetch_state").fetchone()
    assert row[0] == "error" and row[1] == 1 and "disk I/O error" in row[2]
    # Next run: the document is fetched again and lands whole.
    db.fail = False
    f2, s2, _ = _fetcher([(200, {}, PDF)])
    st2 = _run(db, cands, f2)
    assert st2["done"] == 1 and len(s2.calls) == 1
    assert db.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] >= 10
    db.close()


def test_backfill_run_loop_catch_all_rolls_back_and_stops(db, fake_pdf, monkeypatch) -> None:
    """An exception outside the document transaction (a state write that
    fails) must not commit anything and must end the run."""
    cands = [_cand(1975, 609, "1_609__", n=1), _cand(1955, 1077, "1_1077__", n=2)]
    monkeypatch.setattr(bh, "record_state",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    f, script, _ = _fetcher([(200, {}, PDF)] * 2)
    st = _run(db, cands, f)
    assert st["stopped"].startswith("persistence failure") and len(script.calls) == 1
    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM botschaft_fetch_state").fetchone()[0] == 0


def test_backfill_refetches_a_bar_row_without_paragraphs(db, fake_pdf) -> None:
    c = _cand(1975, 609, "1_609__", n=1)
    db.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
               "source_url, format, language, ingested_at, source_collection) VALUES "
               "(1975, 609, 'BBl 1975 I 609', ?, 'u', 'pdf', 'de', 't', 'bar-amtsdruckschriften')",
               (c.eli_uri,))
    db.commit()
    f, script, _ = _fetcher([(200, {}, PDF)])
    st = _run(db, [c], f)
    assert st["done"] == 1 and len(script.calls) == 1
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] >= 10


def test_backfill_without_a_fedlex_citation_is_recorded_not_fetched(db, fake_pdf) -> None:
    c = _cand(1866, 223, "2_223__", vol=2, n=1)._replace(citation=None)
    f, script, _ = _fetcher([(200, {}, PDF)])
    st = _run(db, [c], f)
    assert st["no_citation"] == 1 and script.calls == []
    row = db.execute("SELECT status, attempts, error FROM botschaft_fetch_state").fetchone()
    assert row[0] == "no_citation" and row[1] == 0 and "vol 2, page 223" in row[2]
    assert db.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    # Terminal: the next run does not touch it either.
    f2, s2, _ = _fetcher([])
    st2 = _run(db, [c], f2)
    assert st2["skipped"]["terminal"] == 1 and s2.calls == []


def test_backfill_host_lock_refuses_a_second_run(db, fake_pdf, tmp_path) -> None:
    lock = tmp_path / "bar_fetch.lock"
    c = _cand(1975, 609, "1_609__", n=1)
    with bh._RunLock(lock):                       # a run in progress on this host
        f, script, _ = _fetcher([(200, {}, PDF)])
        with pytest.raises(bh.BackfillLocked):
            _run(db, [c], f, lock_path=lock)
        assert script.calls == []
        # A dry run needs no lock (no request).
        st = _run(db, [c], f, lock_path=lock, dry_run=True)
        assert st["eligible"] == 1
    # Released: the run goes through and releases the lock at the end.
    st = _run(db, [c], f, lock_path=lock)
    assert st["done"] == 1
    with bh._RunLock(lock):
        pass


def test_backfill_dry_run_makes_no_request(db) -> None:
    f, script, _ = _fetcher([])
    st = _run(db, [_cand(1975, 609, "1_609__")], f, dry_run=True)
    assert st["eligible"] == 1 and script.calls == []
    assert db.execute("SELECT COUNT(*) FROM botschaft_fetch_state").fetchone()[0] == 0


def test_existing_row_is_marked_without_a_request(db, fake_pdf) -> None:
    c = _cand(1999, 9000, "1_9000__", n=1)
    db.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
               "source_url, format, language, ingested_at) VALUES (1999, 9000, 'BBl 1999 9000', "
               "?, 'u', 'pdf', 'de', 't')", (c.eli_uri,))
    db.commit()
    f, script, _ = _fetcher([])
    st = _run(db, [c], f)
    assert st["exists"] == 1 and script.calls == []


# ── CLI guards ────────────────────────────────────────────────────────


def test_cli_untyped_requires_ingest_all_and_known_hosts(tmp_path, monkeypatch, capsys) -> None:
    from search_stack import build_botschaft_corpus as bbc
    dbp = tmp_path / "m.db"
    sqlite3.connect(dbp).close()
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dbp), "--discover-untyped-by-title"])
    assert bbc.main() == 2
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dbp), "--ingest-all",
                                      "--discover-untyped-by-title", "--host-allow", "bar,evil"])
    assert bbc.main() == 2
    # Default host allow-list does not include the Bundesarchiv: a dry run
    # over one BAR candidate reports it skipped by host.
    monkeypatch.setattr(bh, "run_historical_backfill",
                        lambda conn, **kw: {"host_allow": sorted(kw["host_allow"]),
                                            "min_year": kw["min_year"], "max_year": kw["max_year"]})
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dbp), "--ingest-all",
                                      "--discover-untyped-by-title", "--dry-run"])
    assert bbc.main() == 0
    out = capsys.readouterr().out
    assert '"host_allow": [\n    "fedlex"\n  ]' in out and '"min_year": 1850' in out
    assert '"max_year": 1999' in out


def test_cli_exits_3_when_another_run_holds_the_lock(tmp_path, monkeypatch) -> None:
    from search_stack import build_botschaft_corpus as bbc
    dbp = tmp_path / "m.db"
    sqlite3.connect(dbp).close()
    lock = tmp_path / "bar_fetch.lock"
    monkeypatch.setattr(bh, "PoliteFetcher", lambda **kw: (_ for _ in ()).throw(AssertionError("no fetcher")))
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dbp), "--ingest-all",
                                      "--discover-untyped-by-title", "--host-allow", "bar",
                                      "--lock-file", str(lock)])
    with bh._RunLock(lock):
        assert bbc.main() == 3


def test_lock_is_taken_in_process_only_and_runbook_agrees(tmp_path, monkeypatch) -> None:
    """The --lock-file flock is the ONLY lock mechanism. An outer
    ``flock -n`` on the same file (a shell wrapper) holds the lock on
    another open file description, so the process's own LOCK_NB attempt
    fails and the run refuses itself — exactly the mistake the runbook once
    made. Pin the behaviour, the runbook's night command and the CLI help."""
    import fcntl
    import os
    import re
    from search_stack import build_botschaft_corpus as bbc
    dbp = tmp_path / "m.db"
    sqlite3.connect(dbp).close()
    lock = tmp_path / "bar_fetch.lock"
    monkeypatch.setattr(bh, "PoliteFetcher", lambda **kw: (_ for _ in ()).throw(AssertionError("no fetcher")))
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(dbp), "--ingest-all",
                                      "--discover-untyped-by-title", "--host-allow", "bar",
                                      "--lock-file", str(lock)])
    # What `flock -n <lock> python3 ...` does: a separate open file
    # description holding LOCK_EX, inherited by the child.
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert bbc.main() == 3, "an outer flock on the same file makes the run refuse itself"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # The runbook's night command passes --lock-file and is not wrapped in flock.
    runbook = (REPO / "runbooks" / "materialien_historical_backfill.md").read_text()
    commands = re.findall(r"/bin/bash -c '(.*?)'", runbook, flags=re.S)
    assert commands, "runbook night command not found"
    for cmd in commands:
        assert "--lock-file /opt/caselaw/repo/logs/bar_fetch.lock" in cmd
        assert "flock" not in cmd
    assert "Do **not** wrap the command in an outer\n`flock -n`" in runbook
    # The CLI help says the same (argparse re-wraps, so compare on one line).
    import contextlib
    import io
    monkeypatch.setattr(sys, "argv", ["x", "--help"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        bbc.main()
    parser_help = re.sub(r"\s+", " ", buf.getvalue())
    assert "do NOT wrap the command in an outer 'flock -n' on the same file" in parser_help
