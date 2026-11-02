"""Which act a passage of a Bundesblatt or consultation document is about.

A document on one bill explains the articles of several acts: the new act, and
each act it amends in an annex, every one with its own Art. 26. An article
number alone therefore identifies nothing. These helpers judge a passage by
its section headings and its own heading line; the server (read side) and
scripts/enrich_botschaft_links.py (link side) must agree, so both use them.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_DRAFT_PREFIXES = r"(?:E|VE|AP|P|nE|D|PA)-"
_DRAFT_PREFIX_RE = re.compile("^" + _DRAFT_PREFIXES)


def _title_names(title: str | None) -> set[str]:
    """Names an Erläuternder Bericht uses for an act, from its official
    title: the title without its "vom <date>" clause ("Bundesgesetz über die
    Alters- und Hinterlassenenversicherung"), the short names in its
    parentheses ("Datenschutzgesetz", "Obligationenrecht" from "Fünfter
    Teil: Obligationenrecht"), and the noun of a "Schweizerisches X"
    title ("Zivilgesetzbuch")."""
    if not title:
        return set()
    t = re.sub(r"\s+", " ", title).strip()
    t = re.sub(r"\s(?:vom|du|del)\s\d{1,2}\.?\s?\w+\s\d{4}", "", t)   # date clause
    names = {re.sub(r"\s*\([^)]*\)\s*", " ", t).strip()}
    for inner in re.findall(r"\(([^)]*)\)", t):
        for part in re.split(r"[,;]", inner):
            part = part.split(":")[-1].strip()
            if len(part) >= 6 and not part.isupper():
                names.add(part)
    m = re.match(r"^(?:Schweizerische[rs]?|suisse|svizzer[oa])\s+(\w+)$", next(iter(names)), re.I)
    m = m or re.match(r"^(?:Code|Codice)\s+(\w+(?:\s\w+)?)\s+suisse$", next(iter(names)), re.I)
    if m:
        names.add(m.group(1))
    return {n for n in names if len(n) >= 6}


def _passage_names_act(section: str | None, text: str | None, abbreviations: set[str],
                       names: set[str] = frozenset()) -> bool:
    """True when the section heading, or the passage's own first line, names
    the act: an abbreviation as a whole word ("4.1 … (AHVG)", "Art. 23 E-AHVG")
    or one of its title names ("2.1 Zivilgesetzbuch")."""
    first = (text or "").split("\n", 1)[0][:200]
    hay = f"{section or ''} {first}"
    if any(re.search(rf"(?<![\w-])(?:{_DRAFT_PREFIXES})?{re.escape(a)}(?![\w-])", hay)
           for a in abbreviations):
        return True
    low = hay.lower()
    if any(n.lower() in low for n in names):
        return True
    # A heading may shorten the title: "Bundesgesetz über die Ziele im
    # Klimaschutz" for "… im Klimaschutz, die Innovation und die Stärkung …".
    bare = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", section or "").strip().lower()
    return len(bare) >= 24 and any(n.lower().startswith(bare) for n in names)


_ARTICLE_REF_ACT_RE = re.compile(
    r"^\s*(?:Art(?:ikel|icle|icolo)?\.?)\s*\d+[a-z]*"
    r"(?:\s+(?:Abs|al|cpv|Bst|let|lit|Ziff|ch|n|Satz|phrase|periodo)\.?\s*[\w]+)*"
    r"\s+((?:[A-Za-zÄÖÜäöü]+-)?[A-ZÄÖÜ][A-Za-zÄÖÜäöü]*[A-ZÄÖÜ][A-Za-zÄÖÜäöü]*)\b")


_ACT_WORD_RE = re.compile(
    r"(gesetz|gesetzbuch|verordnung|prozess|ordnung|reglement|\bloi\b|ordonnance|\bcode\b"
    r"|legge|ordinanza|codice|regolamento)", re.IGNORECASE)
_ACT_DATE_RE = re.compile(r"\b(?:vom|du|del)\s\d{1,2}\.?\s?\w+\s\d{4}\b")
_PAREN_ABBR_RE = re.compile(r"\(((?:[A-Za-zÄÖÜäöü]+-)?[A-ZÄÖÜ][\w-]*[A-ZÄÖÜ][\w-]*)\)")


def _section_names_other_act(section: str | None, abbreviations: set[str],
                             names: set[str] = frozenset()) -> bool:
    """True when the section heading is about an act and that act is not
    this one ("8.2.13 Militärstrafprozess vom 23. März 1979" for the DSG):
    it names none of this act's names and carries an act's date, an
    abbreviation in parentheses, or an act word (Gesetz, Verordnung, loi…)."""
    if not section or _passage_names_act(section, "", abbreviations, names):
        return False
    if _ACT_DATE_RE.search(section):
        return True
    if any(_DRAFT_PREFIX_RE.sub("", a) not in abbreviations for a in _PAREN_ABBR_RE.findall(section)):
        return True
    return bool(_ACT_WORD_RE.search(section))


def _passage_names_other_act(text: str | None, abbreviations: set[str]) -> bool:
    """True when the passage's heading line cites the article of ANOTHER act
    ("Art. 5 Abs. 1 E-VDSG" for the DSG): an abbreviation-like word (two or
    more capitals) right after the article reference, not one of this act's
    abbreviations once a draft prefix (E-, VE-, AP-…) is removed."""
    first = (text or "").split("\n", 1)[0][:200]
    m = _ARTICLE_REF_ACT_RE.match(first)
    if not m:
        return False
    tok = _DRAFT_PREFIX_RE.sub("", m.group(1))
    return tok not in abbreviations


# build_botschaft_corpus joins a heading path with this, and tags a heading
# whose footnote gives the act's SR number: "5.2.2 Verwaltungsverfahrensgesetz
# vom 20. Dezember 1968 [SR 172.021]".
SECTION_SEPARATOR = " > "
_SR_TAG_RE = re.compile(r"\[(?:SR|RS)\s+([\d.]+)\]")


def linked_anchor(article: str, evidence: str | None) -> str:
    """The article_anchor of a link: the document's own number where the link
    records one, else the article."""
    _, sep, anchor = (evidence or "").partition(DRAFT_ARTICLE_EVIDENCE)
    return anchor if sep and anchor else article


def anchor_runs(rows: list) -> list[list]:
    """Rows of one document with an article_anchor, in document order, cut into
    runs: an article's comment under one heading path. Each row needs
    ``article_anchor`` and ``section_path``. The same number comes back as a
    separate run in another act's section."""
    runs: list[list] = []
    for row in rows:
        if runs and (runs[-1][-1]["article_anchor"], runs[-1][-1]["section_path"]) == (
                row["article_anchor"], row["section_path"]):
            runs[-1].append(row)
        else:
            runs.append([row])
    return runs


# "Art. 26 Nichterreichbarkeit einer Plattform" → number and the rest. Used by
# the XML parser for headings and here for the title.
ARTICLE_LINE_RE = re.compile(
    r"^(?:(?:Zu|Ad|All['’])\s*)?(?:Art(?:ikel|icle|icolo)?\.?|art\.)\s*"
    r"(\d+(?:\.\d+)*[a-z]*)(?![\w])(.*)$",
    re.IGNORECASE | re.DOTALL)
# What may follow the number on a heading line that is not a title.
ARTICLE_DETAIL_RE = re.compile(
    r"^(?:\d|–|-|(?:Abs|Abschn|Absatz|Absätze|Bst|Buchstabe|Ziff|Satz|Einleitungssatz|Sachüberschrift"
    r"|Marginalie|einziger|al|alinéa|alinéas|let|ch|par|phrase|titre|cpv|capoverso|lett|n|periodo"
    r"|und|bis|et|e|sowie|à)\b)", re.IGNORECASE)
_TITLE_MATCH_RATIO = 0.85
# On a link whose document numbers the article differently from the act:
# "…:draft_art=40" on the link to Art. 33 means the paragraphs are anchored 40.
DRAFT_ARTICLE_EVIDENCE = "draft_art="


def article_title(head_text: str | None) -> str:
    """The title on an article's heading line, "" when it has none ("Art. 26
    Abs. 1", "Art. 5"). Fedlex's "[Marginalie]" tag is not part of it."""
    m = ARTICLE_LINE_RE.match((head_text or "").split("\n", 1)[0])
    if not m:
        return ""
    tail = m.group(2).replace("[Marginalie]", "").strip().lstrip(", ")
    return "" if not tail or ARTICLE_DETAIL_RE.match(tail) else tail


def _title_key(title: str | None) -> str:
    return re.sub(r"[\W_]+", " ", title or "").strip().casefold()


def _same_title(a: str, b: str) -> bool:
    """Parliament edits marginal titles lightly: "Regulierungskostenschätzungen"
    is still "Regulierungskostenschätzung"."""
    if a == b:
        return True
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= _TITLE_MATCH_RATIO


def enacted_article(title: str, number: str, enacted: dict[str, str]) -> str | None:
    """Which enacted article carries this title: ``number`` itself if it does,
    else the one other article that does; None when none does, or several."""
    key = _title_key(title)
    if not key:
        return None
    if _same_title(key, _title_key(enacted.get(number))):
        return number
    exact = [a for a, h in enacted.items() if _title_key(h) == key]
    hits = exact or [a for a, h in enacted.items() if _same_title(key, _title_key(h))]
    return hits[0] if len(hits) == 1 else None


def place_runs(rows: list, sr: str, abbreviations: set[str], names: set[str],
               several_acts: bool, enacted: dict[str, str]) -> dict[str, str]:
    """{enacted article: article_anchor} for the comments of act ``sr`` in one
    document's anchored rows, in document order.

    A Botschaft numbers its comments by the draft. Parliament inserts and drops
    articles: TJPG Art. 33 is Art. 40 of the Botschaft, BEKJ Art. 38 is its
    Art. 37, and "Art. 40" of the Botschaft explains another provision than
    Art. 40 of the act. The enacted heading (``enacted``: article_num → heading)
    is the only evidence, so it decides:

    * same title under the same number: the number holds;
    * the title of one other enacted article: the comment goes there;
    * no title to compare (not in force, untitled, reworded): the number holds
      if the placed comments around it kept theirs, otherwise there is no link.
      A wrong article quoted verbatim is worse than none.

    In a bill on several acts a title match also answers what no heading says;
    it never overrides a section about another act. Called for acts Fedlex's
    chain ties to the document, hence ``own_bill``.
    """
    placed: list[tuple[str, str | None]] = []   # (anchor, enacted article or None)
    for run in anchor_runs(rows):
        head = run[0]
        anchor, title = head["article_anchor"], article_title(head["text"])
        target = enacted_article(title, anchor, enacted) if title else None
        if not _heading_is_about_act(head["text"], head["section_path"], sr,
                                     abbreviations, names, several_acts, own_bill=True):
            undecided = (several_acts
                         and not _passage_names_other_act(head["text"], abbreviations)
                         and _section_verdict(head["section_path"], sr, abbreviations,
                                              names, own_bill=True) is None)
            if not (undecided and target):
                continue
        placed.append((anchor, target))
    shifted = any(target and target != anchor for anchor, target in placed)
    out: dict[str, str] = {}
    for i, (anchor, target) in enumerate(placed):
        if target:
            out.setdefault(target, anchor)
        elif not shifted:
            out.setdefault(anchor, anchor)
        else:
            before = next((t == a for a, t in reversed(placed[:i]) if t), None)
            after = next((t == a for a, t in placed[i + 1:] if t), None)
            if before is not False and after is not False and (before or after):
                out.setdefault(anchor, anchor)
    return out


def _law_names_from_row(row) -> tuple[set[str], set[str]]:
    """(abbreviations, title names) from (abbr_de, abbr_fr, abbr_it, title_de,
    title_fr, title_it) of statutes.db ``laws``."""
    abbrs = {a.strip() for a in row[:3] if a and a.strip()}
    names: set[str] = set()
    for title in row[3:]:
        names |= _title_names(title)
    return abbrs, names


# A Botschaft heads its own bill by the draft's title, undated: "5.1 Legge
# federale concernente le piattaforme … nella giustizia". Parliament may rename
# the act ("… in ambito giudiziario"), and for the act Fedlex ties to the
# document such a heading must not read as another act. An act the bill amends
# is cited with its date or SR number.
_BILL_HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:Bundesgesetz|Bundesbeschluss|Loi fédérale|Arrêté fédéral"
    r"|Legge federale|Decreto federale)\b", re.IGNORECASE)


def _section_verdict(section_path: str | None, sr: str, abbreviations: set[str],
                     names: set[str], own_bill: bool = False) -> bool | None:
    """Is this heading path about the act? The nearest heading that is about
    an act decides; None when no heading is about any act. ``own_bill``: the
    document is this act's own bill, per Fedlex."""
    for heading in reversed((section_path or "").split(SECTION_SEPARATOR)):
        tagged = _SR_TAG_RE.findall(heading)
        if tagged:
            return sr in tagged
        if _passage_names_act(heading, None, abbreviations, names):
            return True
        if own_bill and _BILL_HEADING_RE.match(heading) and not _ACT_DATE_RE.search(heading):
            continue
        if _section_names_other_act(heading, abbreviations, names):
            return False
    return None


def _heading_is_about_act(head_text: str | None, section_path: str | None, sr: str,
                          abbreviations: set[str], names: set[str],
                          several_acts: bool, own_bill: bool = False) -> bool:
    """Does the run of paragraphs under this "Art. N" heading explain this act?

    Its heading line decides first ("Art. 5 Abs. 1 VwVG" for the BEKJ: no), then
    its section. With no verdict either way the passage counts only if the
    document concerns no other act; ``several_acts`` says it does.
    """
    if _passage_names_other_act(head_text, abbreviations):
        return False
    if _passage_names_act(None, head_text, abbreviations, names):
        return True
    verdict = _section_verdict(section_path, sr, abbreviations, names, own_bill)
    return verdict if verdict is not None else not several_acts
