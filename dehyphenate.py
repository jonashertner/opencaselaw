"""Rejoin words a PDF broke across lines, without rewriting the text.

A ruling extracted from a PDF keeps the typesetter's line breaks: "Über-⏎
weisung". Copied out of a reader, the hyphen and the break come along, and
the search index sees two tokens ("über", "weisung"), so a search for
"Überweisung" misses the passage. The same mark also stands for real
hyphens ("jours-amende", "procès-verbal", "elle-même"), for suspended
compounds ("Kindes- und Erwachsenenschutz") and for French inclusive
writing ("Enseignant-e"), and some portals put soft hyphens (U+00AD) at
every syllable ("pour<SHY>sui<SHY>vie") while also using one as a real
hyphen ("40 jours<SHY>amende"). Deleting every hyphen corrupts a verbatim
text.

So each candidate is decided first by how the corpus itself writes the
word when no line break is involved (``Lexicon``): "überweisung" is common
and "über-weisung" is not, so the halves join; "jours-amende" is common and
"joursamende" is not, so the hyphen stays and only the break goes. The
closed form has to dominate (CLOSED_DOMINANCE): where both spellings are in
real use the hyphen stays. Only when the corpus has seen neither form does a
language rule decide, and it leaves the source as it is when in doubt.

The governing rule: a hyphen is removed only where the evidence says it is a
line break and serves no other purpose.

What this never does:
  * touch an ordinary in-line hyphen ("Basel-Stadt", "IV-Rente");
  * guess across a blank line: a hyphen before a blank line is only
    resolved on lexicon evidence (NE's PDFs put a stray blank line inside
    sentences, "recou-⏎⏎rant"), never by a fallback rule;
  * touch digits ("1934-⏎1935" stays as it is);
  * rewrite a letter: the only edits are removing a hyphen, a soft hyphen
    or the whitespace of a line break, turning a kept soft hyphen into "-",
    and mapping the two Unicode hyphens U+2010 / U+2011 to "-".

The function is idempotent: run over its own output it changes nothing, so
the incremental structure build (which reads decisions.db, already
normalised) and the full one (which reads the raw shards) agree.

The raw JSONL shards are never modified; this runs where the served text is
built (build_fts5 and the decision_structure extractor) and is switched on
by pointing ``OPENCASELAW_DEHYPHEN_LEXICON`` at a lexicon built by
``scripts/build_dehyphen_lexicon.py``. Unset, nothing changes.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

LEXICON_ENV = "OPENCASELAW_DEHYPHEN_LEXICON"

SHY = "\u00ad"   # soft hyphen

# Evidence the lexicon needs before it decides: the winning form must be
# seen at least this often, and strictly more often than the other form.
MIN_EVIDENCE = 2
# The governing rule: a hyphen is removed only where the evidence says it
# is a line break and nothing else. The closed form must outnumber the
# hyphenated one at least this many times; where both spellings are in real
# use ("porte-monnaie" / "portemonnaie") the hyphen stays and only the line
# break goes.
CLOSED_DOMINANCE = 100
# German capitalises the part after the hyphen in a hyphenated noun
# compound ("Kranken-Versicherung"), so a lower-case continuation after a
# hyphen inside a noun ("Strafprozess-⏎ordnung") can only be a line break.
# A closed form written capitalised in at least this share of its
# occurrences is a noun; its in-line "strafprozess-ordnung" counts are
# artefacts of sources that dropped the break and kept the hyphen.
NOUN_SHARE = 0.8
# ... and only where the noun's closed form occurs at least NOUN_MIN times
# and NOUN_DOMINANCE times as often as the lower-case hyphenated artefact.
NOUN_MIN = 10
NOUN_DOMINANCE = 10
# A capitals pair with a side of two letters or fewer ("MEY-ER") joins only
# on a closed form seen this often: abbreviation pairs have accidental ones.
CAPS_SHORT_MIN = 1000
# A pair the corpus writes spaced in-line ("Rechtfertigungs- noch", "Validen-
# wie", "Geschäfts- ins": suspended compounds) is joined only when the closed
# form is at least this many times as frequent as the spaced one: a real
# break ("wer- den" → "werden") has a closed form orders of magnitude more
# common; a suspended compound's closed form exists only where some source
# corrupted it.
SPACED_RATIO = 5
# French / Italian fallback: a continuation that is a word of its own at
# least this often ("ment", "témoin") is not joined on a guess.
STANDALONE_MIN = 3

_L = r"[^\W\d_]"                        # one letter, any script
_LETTERS_TAIL_RE = re.compile(_L + r"+$")
_LETTERS_HEAD_RE = re.compile(_L + r"+")

# A candidate separator: a hyphen or soft hyphen after a letter, then one
# line break, or one blank line, or spaces, then a letter. Only the
# separator is matched, so chains ("c'est-à-⏎dire") never overlap.
_CANDIDATE_RE = re.compile(
    # The hyphen comes first so the engine can search for it directly; the
    # "letter before it" check follows as a lookbehind over both characters.
    r"(?P<h>[-\u00ad])(?<=" + _L + r"[-\u00ad])"
    r"(?P<ws>[ \t]*\r?\n[ \t]*(?:\r?\n[ \t]*)?|[ \t]+)"
    r"(?=" + _L + r")"
)
# Cheap pre-check: no hyphen followed by whitespace and no soft hyphen means
# nothing to do (most HTML-sourced rulings).
_ANY_CANDIDATE_RE = re.compile(r"-[ \t\r\n]|\u00ad")
# A word carrying soft hyphens inside it: "pour<SHY>sui<SHY>vie".
_SHY_WORD_RE = re.compile(_L + r"+(?:\u00ad" + _L + r"+)+")
_UNICODE_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})

# A suspended hyphen: "Kindes- und", "Straf- oder", "hétéro- et",
# "tri- voire". Italian's one-letter "e" / "o" only in Italian text: in
# French "Enseignant-⏎e" is inclusive writing, not "and".
_CONJUNCTIONS = frozenset({
    "und", "oder", "bzw", "beziehungsweise", "sowie", "resp", "respektive",
    "bis", "als", "et", "ou", "ni", "voire", "respectivement", "ed", "od",
    "oppure", "nonché",
})
_CONJUNCTIONS_IT = frozenset({"e", "o"})
# German function words: never the second half of a broken word unless the
# corpus says so ("so-⏎wie" → "sowie" is decided by the lexicon first).
_DE_FUNCTION_WORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "eines", "mit", "von", "vom", "zu", "zum", "zur", "im", "in", "an",
    "am", "auf", "aus", "bei", "nach", "über", "um", "unter", "vor", "für",
    "gegen", "durch", "ist", "sind", "wird", "werden", "wurde", "hat", "haben",
    "nicht", "auch", "sich", "so", "es", "er", "sie", "wir", "ich", "dass",
    "wenn", "ob", "aber", "weil", "da", "wo", "wie", "noch", "nur", "kein",
    "keine",
})
# French inclusive endings: "Enseignant-e", "inscrit-e-s", "citoyen-ne".
# "e" / "es" never stand alone on a line after a typesetter's break; the
# others ("ne", "trice", …) are also ordinary word endings ("person-⏎ne"),
# so they keep the hyphen only when the corpus writes the pair that way.
_INCLUSIVE_ALWAYS = frozenset({"e", "es"})
_INCLUSIVE_ATTESTED = frozenset({
    "ne", "nes", "trice", "trices", "euse", "euses", "ère", "ères",
    "fe", "fes", "le", "les", "x",
})

# Fragments before a hyphen that carry a real hyphen in the language
# ("peut-" / "être", "porte-" / "parole"), and fragments after it that close
# a hyphenated compound ("au-" / "dessus", "ci-" / "joint", "moi-" /
# "même"). Extends the lists in search_stack/botschaft_historical.py, which
# applies the same rules to the historical Bundesblatt OCR.
_HYPHEN_PREFIXES: dict[str, frozenset[str]] = {
    "de": frozenset({"quasi", "semi", "non"}),
    "fr": frozenset({
        "peut", "est", "sont", "ont", "faut", "doit", "avons", "avez",
        "sous", "non", "demi", "semi", "vice", "quasi", "anti", "contre", "avant",
        "arrière", "arriere", "sans", "porte", "garde", "après", "apres", "outre",
        "pseudo", "néo", "neo", "socio", "franco", "italo", "germano", "nord", "sud",
        "ouest", "grand", "grande", "petit", "petite", "haut", "haute", "saint",
        "sainte", "lieu", "chef", "vis", "tout", "toute", "même", "meme", "ci",
        "bien", "nue", "procès", "proces", "eux", "elle", "elles", "lui", "ceux",
        "celle", "celles", "celui",
    }),
    "it": frozenset({
        "ex", "vice", "anti", "semi", "quasi", "neo", "post", "non", "filo", "ultra",
        "extra", "socio", "franco", "italo", "svizzero", "nord", "ovest", "medio",
        "afro", "euro", "multi", "pluri", "mono",
    }),
}
_HYPHEN_SUFFIXES: dict[str, frozenset[str]] = {
    "de": frozenset(),
    "fr": frozenset({
        "être", "etre", "dire", "dessus", "dessous", "delà", "dela", "là", "vis",
        "même", "meme", "mêmes", "memes", "parole", "après", "apres", "contre",
        "joint", "jointe", "joints", "jointes", "annexé", "annexée", "inclus", "incluse",
        "il", "ils", "elle", "elles", "je", "nous", "vous", "moi", "toi", "lui", "ci",
        "verbal", "verbaux", "amende", "amendes", "fondé", "propriété",
    }),
    "it": frozenset({"presidente", "direttore", "console"}),
}


class Lexicon(Protocol):
    def word(self, w: str) -> int: ...
    def hyphenated(self, w: str) -> int: ...
    def spaced(self, w: str) -> int: ...
    def capitalized(self, w: str) -> int: ...


class DictLexicon:
    """In-memory lexicon (tests, rehearsals)."""

    def __init__(self, words: dict[str, int] | None = None,
                 hyphenated: dict[str, int] | None = None,
                 spaced: dict[str, int] | None = None,
                 capitalized: dict[str, int] | None = None):
        self._w = dict(words or {})
        self._h = dict(hyphenated or {})
        self._s = dict(spaced or {})
        self._c = dict(capitalized or {})

    def word(self, w: str) -> int:
        return self._w.get(w, 0)

    def hyphenated(self, w: str) -> int:
        return self._h.get(w, 0)

    def spaced(self, w: str) -> int:
        return self._s.get(w, 0)

    def capitalized(self, w: str) -> int:
        return self._c.get(w, 0)


class SqliteLexicon:
    """Lexicon file written by scripts/build_dehyphen_lexicon.py, opened
    read-only and immutable like every other production database."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._conn = sqlite3.connect(
            f"file:{self.path}?mode=ro&immutable=1", uri=True, check_same_thread=False,
        )
        self.word = lru_cache(maxsize=200_000)(self._word)            # type: ignore[method-assign]
        self.hyphenated = lru_cache(maxsize=200_000)(self._hyphenated)  # type: ignore[method-assign]
        tables = {r[0] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self._has_spaced = "spaced" in tables
        self._has_capitalized = "capitalized" in tables
        self.spaced = lru_cache(maxsize=200_000)(self._spaced)  # type: ignore[method-assign]
        self.capitalized = lru_cache(maxsize=200_000)(self._capitalized)  # type: ignore[method-assign]

    def _word(self, w: str) -> int:
        row = self._conn.execute("SELECT n FROM words WHERE w = ?", (w,)).fetchone()
        return row[0] if row else 0

    def _hyphenated(self, w: str) -> int:
        row = self._conn.execute("SELECT n FROM hyphenated WHERE w = ?", (w,)).fetchone()
        return row[0] if row else 0

    def _spaced(self, w: str) -> int:
        if not self._has_spaced:
            return 0
        row = self._conn.execute("SELECT n FROM spaced WHERE w = ?", (w,)).fetchone()
        return row[0] if row else 0

    def _capitalized(self, w: str) -> int:
        if not self._has_capitalized:
            return 0
        row = self._conn.execute("SELECT n FROM capitalized WHERE w = ?", (w,)).fetchone()
        return row[0] if row else 0

    def meta(self) -> dict[str, str]:
        return dict(self._conn.execute("SELECT key, value FROM meta").fetchall())


_lexicon_cache: dict[str, SqliteLexicon | None] = {}


def configured_lexicon() -> SqliteLexicon | None:
    """The lexicon named by OPENCASELAW_DEHYPHEN_LEXICON, or None (feature
    off). A path that does not open is logged once and treated as off: a
    missing lexicon must not fail a nightly build."""
    path = os.environ.get(LEXICON_ENV, "").strip()
    if not path:
        return None
    if path not in _lexicon_cache:
        try:
            if not Path(path).is_file():
                raise FileNotFoundError(path)
            lex = SqliteLexicon(path)
            lex.word("und")  # schema check
            _lexicon_cache[path] = lex
            logger.info("dehyphenation ON, lexicon %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("dehyphenation OFF: lexicon %s unusable (%s)", path, e)
            _lexicon_cache[path] = None
    return _lexicon_cache[path]


# ── decisions ─────────────────────────────────────────────────────────

def _is_fragment(part: str, closed: int, lexicon: Lexicon) -> bool:
    """``part`` (lower case) is no word of its own next to the closed form
    it is part of: it occurs less often alone than the closed form does.
    Counts alone are not enough, since the corpus also counts broken halves
    ("lich" 27k, from sources that broke "öffent-lich"); "öffentlich" 295k."""
    return lexicon.word(part) < closed

JOIN = "join"            # "Über-⏎weisung" → "Überweisung"
HYPHEN = "hyphen"        # "jours-⏎amende" → "jours-amende"
SUSPENDED = "suspended"  # "Kindes-⏎und" → "Kindes- und"
KEEP = "keep"            # left as the source has it


def _decide(a: str, b: str, h: str, ws: str, before_a: str, after_b: str,
            lang: str, lexicon: Lexicon | None) -> tuple[str, str]:
    """(action, reason) for the separator ``h`` + ``ws`` between fragments
    ``a`` and ``b``. Rules in order; the first that applies decides."""
    blank = ws.count("\n") >= 2          # a blank line in between
    line = "\n" in ws and not blank
    midline = not ("\n" in ws)
    # A visible change other than joining is only made at a line break or a
    # soft hyphen; a mid-line "x- y" the source wrote is left alone.
    breakish = line or h == SHY
    al, bl = a.lower(), b.lower()
    all_caps = a.isupper() and b.isupper() and len(b) > 1

    # 0. a soft hyphen is an invisible break point, never a hyphen the author
    #    wrote: it joins ("Online-Ver<SHY>⏎steigerung", "EGz<SHY>⏎SchKG"),
    #    unless a conjunction follows (a suspended hyphen some portal
    #    encoded as a soft one) or the corpus writes the pair hyphenated
    #    ("jours<SHY>⏎amende", NE).
    if h == SHY:
        if bl in _CONJUNCTIONS and len(bl) >= 3:
            return SUSPENDED, "conjunction"
        if lexicon is not None:
            ch0 = lexicon.hyphenated(al + "-" + bl)
            if ch0 >= MIN_EVIDENCE and lexicon.word(al + bl) < CLOSED_DOMINANCE * ch0:
                return HYPHEN, "soft_hyphen_attested"
        return JOIN, "soft_hyphen"
    # 1. French inclusive writing: "Enseignant-⏎e"; Italian "Straf-⏎e".
    if lang == "fr" and b in _INCLUSIVE_ALWAYS:
        return (HYPHEN, "inclusive") if (breakish or blank) else (KEEP, "inclusive")
    if lang == "it" and b in _CONJUNCTIONS_IT:
        return (SUSPENDED, "conjunction") if breakish else (KEEP, "conjunction")
    # 2. a one-letter continuation, or a one-letter first part ("E-mail",
    #    "A-Post", "U-Haft"), is no typesetter's break: hyphenation never
    #    leaves a single letter on either side.
    #    The hyphen always stays; the line break goes only where the
    #    compound is attested ("e-mail") or a chain ("c'est-à-⏎dire").
    #    One exception: a one-letter continuation before an apostrophe is
    #    an elided word ("del-⏎l'esecuzione" → "dell'esecuzione").
    if len(b) == 1 or len(a) == 1:
        reason = "single_letter_continuation" if len(b) == 1 else "single_letter_fragment"
        if (len(b) == 1 and len(a) >= 2 and after_b == "'" and not midline
                and lexicon is not None and b.islower()):
            cj1 = lexicon.word(al + bl)
            if cj1 >= MIN_EVIDENCE and cj1 >= CLOSED_DOMINANCE * lexicon.hyphenated(al + "-" + bl):
                return JOIN, "elision"
        # ... and a one-letter fragment right after an apostrophe is the
        # head of an elided word ("d'e-⏎sistenza" → "d'esistenza").
        if (len(a) == 1 and len(b) >= 2 and before_a in ("'", "\u2019") and not midline
                and lexicon is not None and b.islower()):
            cj1 = lexicon.word(al + bl)
            if cj1 >= MIN_EVIDENCE and cj1 >= CLOSED_DOMINANCE * lexicon.hyphenated(al + "-" + bl):
                return JOIN, "elision"
        attested = (lexicon is not None and not b[:1].isupper()
                    and lexicon.hyphenated(al + "-" + bl) >= MIN_EVIDENCE)
        if (breakish or blank) and (attested or before_a == "-" or after_b == "-"):
            return HYPHEN, reason
        return KEEP, reason
    # 3. an upper-case continuation: a real hyphen after a capitalised or
    #    upper-case fragment ("Basel-⏎Stadt", "IV-⏎Rente", "Chaux-de-⏎
    #    Fonds"); after a lower-case one it is usually OCR ("juge-⏎Inent"
    #    for "jugement"): left as it is.
    if b[:1].isupper() and not all_caps:
        if (a[:1].isupper() or before_a == "-") and breakish:
            return HYPHEN, "upper_case_continuation"
        return KEEP, "upper_case_continuation"

    # 3a. a suspended compound before a conjunction of three letters or
    #     more ("Kindes-⏎und", "Mitwirkungs- bzw."), or any conjunction
    #     mid-line, is decided before the lexicon: the corpus also holds
    #     closed forms that some source corrupted ("straferhöhungsoder",
    #     "mitwirkungsbzw"), enough of them to outvote nothing. A two-letter
    #     one at a line break ("fi-⏎ni") is left to the lexicon (rule 7).
    if bl in _CONJUNCTIONS and (len(bl) >= 3 or midline):
        return (SUSPENDED, "conjunction") if breakish else (KEEP, "conjunction")
    # 3b'. a continuation in capitals after a capitalised fragment: names and
    #     citations ("SUTTER-⏎SOMM", "BSK ZPO-⏎SPRECHER", "LPA-⏎VD") keep
    #     the hyphen; an all-caps heading broken at a line end
    #     ("SCHULD-⏎BETREIBUNG") joins where the corpus writes it closed.
    #     The closed form must dominate the hyphenated and spaced forms
    #     together (names broken at a syllable: "STAEHE-⏎LIN" → STAEHELIN,
    #     "MÜL-⏎LER"). Abbreviation pairs must not join, and their closed
    #     forms exist by accident ("lpavd" 9x from sources that glued it,
    #     "euch" 1,398x, a German word): a two-letter fragment before a
    #     continuation of three letters or fewer never joins ("EU-⏎CH",
    #     "CH-⏎ZPO", "BE-⏎AG", "IV-⏎EO"), and a pair with a side of two letters
    #     or fewer needs a closed form seen 1,000 times ("MEY-⏎ER" 91k,
    #     "AN-⏎DREAS" 40k join; "LPA-⏎GE" 20x, "LPA-⏎VD" 9x keep the hyphen).
    if all_caps:
        if (lexicon is not None and not midline
                and not (len(a) <= 2 and len(b) <= 3)):
            cjc = lexicon.word(al + bl)
            other = lexicon.hyphenated(al + "-" + bl) + lexicon.spaced(al + "- " + bl)
            floor = CAPS_SHORT_MIN if (len(a) <= 2 or len(b) <= 2) else NOUN_MIN
            if cjc >= floor and cjc >= CLOSED_DOMINANCE * other:
                return JOIN, "lexicon_caps"
        return (HYPHEN, "upper_case_continuation") if (breakish or blank) \
            else (KEEP, "upper_case_continuation")
    # 3c. the fragment is itself the tail of a hyphen chain ("c'est-à-⏎dire",
    #     "Nord-Süd-⏎achse", "EU-Mitglied-⏎staaten"): the hyphen stays,
    #     unless the fragment is no word and the corpus writes the pair
    #     closed ("non-divul-⏎gation" → "non-divulgation").
    if before_a == "-":
        fragment_joins = False
        if lexicon is not None and not midline:
            cj3, ch3 = lexicon.word(al + bl), lexicon.hyphenated(al + "-" + bl)
            fragment_joins = (cj3 >= MIN_EVIDENCE and cj3 >= CLOSED_DOMINANCE * ch3
                              and _is_fragment(al, cj3, lexicon))
        if fragment_joins:
            return JOIN, "lexicon_chain_fragment"
        return (HYPHEN, "compound_chain") if (breakish or blank) else (KEEP, "compound_chain")

    cj = ch = sp = 0
    if lexicon is not None:
        cj = lexicon.word(al + bl)
        ch = lexicon.hyphenated(al + "-" + bl)
        sp = lexicon.spaced(al + "- " + bl)
    # 3b. the corpus writes the pair spaced, as a suspended compound, and
    #     not overwhelmingly closed: "Rechtfertigungs- noch", "Validen- wie".
    #     Not where the hyphenated spelling is commoner than the spaced one
    #     ("ci-dessus", which OCR also gives as "ci- dessus").
    if sp >= MIN_EVIDENCE and cj < SPACED_RATIO * sp and ch < sp:
        return (SUSPENDED, "lexicon_spaced") if breakish else (KEEP, "lexicon_spaced")
    # 4. French inclusive writing the corpus attests: "citoyen-ne" (before
    #    the closed form, which "citoyenne" also is).
    if (lang == "fr" and bl in _INCLUSIVE_ATTESTED and ch >= MIN_EVIDENCE
            and ch * CLOSED_DOMINANCE >= cj):
        return (HYPHEN, "inclusive") if (breakish or blank) else (KEEP, "inclusive")
    # 4a. a lower-case hyphen chain continues after the continuation, which
    #     is a word of its own: "Just-⏎in-time" (not "Justin-time"),
    #     "Ponts-⏎et-chaussées". A fragment that is no word still joins:
    #     "émet-⏎teurs-récepteurs", "charak-⏎terlich-psychischen".
    if (after_b == "-" and lexicon is not None and lexicon.word(bl) >= STANDALONE_MIN
            and not _is_fragment(bl, cj, lexicon)):
        return (HYPHEN, "compound_chain") if (breakish or blank) else (KEEP, "compound_chain")
    # 4b. German noun compound with a lower-case continuation: a hyphen
    #     there is not German orthography, so it is the line break
    #     ("Strafprozess-⏎ordnung", "Kranken-⏎versicherung").
    #     Not after an abbreviation ("AHV-⏎pflichtig", "US-⏎amerikanische"
    #     are correct German and look capitalised), and only where the closed
    #     form clearly dominates the hyphenated one, so that place names and
    #     foreign compounds in German text ("Chaux-⏎de-Fonds") keep it.
    #     Mid-line ("Rechts- öffnung", the break collapsed by the source or
    #     a page reflow) too, since "- " before a lower-case noun part is not
    #     German either, unless a function word follows ("Kauf- noch") and
    #     only where the closed form dominates the spaced one as well.
    if (lang == "de" and not all_caps and not a.isupper()
            and len(a) >= 2 and cj >= NOUN_MIN and cj >= NOUN_DOMINANCE * ch
            and lexicon is not None and lexicon.capitalized(al + bl) >= NOUN_SHARE * cj
            and (not midline or (bl not in _DE_FUNCTION_WORDS
                                 and cj >= CLOSED_DOMINANCE * (ch + sp)))):
        return JOIN, "lexicon_noun_midline" if midline else "lexicon_noun"
    # 5. the corpus writes it closed, overwhelmingly: "überweisung",
    #    "recourant". Mid-line ("Ver- fügung", no line break in the text)
    #    the break can only be inferred, so also the continuation must be no
    #    word of its own ("adé- quats", "Durchfüh- rung"); "Vor- instanz",
    #    "wer- den" stay as written.
    if cj >= MIN_EVIDENCE and cj >= CLOSED_DOMINANCE * ch:
        if not midline:
            return JOIN, "lexicon"
        if lexicon is not None and lexicon.word(bl) * CLOSED_DOMINANCE < cj:
            return JOIN, "lexicon_midline"
    # 6. an in-line chain continues after the fragment, which the corpus
    #    does not close up with ``a``: "Ponts-⏎et-chaussées", "Commis-⏎
    #    greffier-ère" (``after_b`` is "-" only when an in-line hyphen
    #    follows). Not in German, where "Zwei-⏎drittel-Mehrheit" is a break.
    if after_b == "-" and lang != "de":
        return (HYPHEN, "compound_chain") if (breakish or blank) else (KEEP, "compound_chain")
    # 7. a suspended compound: "Kindes-⏎und", "tri-⏎voire".
    if bl in _CONJUNCTIONS:
        return (SUSPENDED, "conjunction") if breakish else (KEEP, "conjunction")
    if midline and h != SHY:
        return KEEP, "midline_no_evidence"
    # 8. the corpus writes it with a hyphen, as the only or a real
    #    alternative spelling: "procès-verbal", "bien-fondé", "e-mail".
    if ch >= MIN_EVIDENCE:
        return HYPHEN, "lexicon"
    # Nothing below is a guess worth making across a blank line.
    if blank:
        return KEEP, "blank_line_no_evidence"

    # ── fallback: the corpus has seen neither form ──
    # 9. known hyphenated compounds of the language, or (French, Italian)
    #     the fragment is itself the tail of one ("c'est-à-⏎dire").
    if (before_a == "-" and lang != "de") or al in _HYPHEN_PREFIXES.get(lang, ()) \
            or bl in _HYPHEN_SUFFIXES.get(lang, ()):
        return HYPHEN, "known_compound"
    if len(a) == 1:
        return KEEP, "single_letter_fragment"
    # 10. German writes compounds closed: a lower-case continuation after
    #     a line-end hyphen is the typesetter's break, unless it is a
    #     function word ("in Uniform-⏎mit der", a stray hyphen) or both
    #     halves are words of their own, where the hyphen may be real
    #     ("physisch-⏎psychisch", "deutsch-⏎französisch").
    if lang == "de":
        if bl in _DE_FUNCTION_WORDS:
            return KEEP, "function_word"
        if lexicon is not None and lexicon.word(al) >= STANDALONE_MIN \
                and lexicon.word(bl) >= STANDALONE_MIN:
            # Two words of their own. If the continuation is a noun (written
            # capitalised on its own), a real hyphen would read "Miet-
            # Bewerbung": the lower-case "bewerbung" can only be a break.
            # Adjectives ("physisch-⏎psychisch") keep the hyphen.
            wb = lexicon.word(bl)
            if lexicon.capitalized(bl) >= NOUN_SHARE * wb:
                return JOIN, "fallback_noun"
            return KEEP, "fallback_two_words"
        return JOIN, "fallback_de"
    # 11. French / Italian: join only a continuation that is no word of its
    #     own ("Dé-⏎partement"); anything else stays as the source has it.
    if lexicon is not None and lexicon.word(bl) < STANDALONE_MIN:
        return JOIN, "fallback_fragment"
    return KEEP, "fallback_undecided"


_DECISION_CACHE: dict = {}
_DECISION_CACHE_MAX = 500_000


def _decide_cached(a: str, b: str, h: str, ws: str, before_a: str, after_b: str,
                   lang: str, lexicon: Lexicon | None) -> tuple[str, str]:
    """_decide is a pure function of its arguments and the lexicon; the same
    broken pairs recur thousands of times ("Be-⏎schwerde"), so decisions are
    remembered. The whitespace is reduced to its kind, which is all _decide
    reads of it."""
    kind = ws.count("\n")
    # The lexicon object itself (not its id, which Python reuses) is part
    # of the key.
    key = (a, b, h, min(kind, 2), before_a, after_b, lang, lexicon)
    hit = _DECISION_CACHE.get(key)
    if hit is None:
        hit = _decide(a, b, h, ws, before_a, after_b, lang, lexicon)
        if len(_DECISION_CACHE) >= _DECISION_CACHE_MAX:
            _DECISION_CACHE.clear()
        _DECISION_CACHE[key] = hit
    return hit


def _resolve_shy_words(text: str, lexicon: Lexicon | None,
                       stats: Counter | None, record: list | None) -> str:
    """Soft hyphens inside a word ("pour<SHY>sui<SHY>vie"): a soft hyphen is
    discretionary by definition and goes, unless the corpus writes that
    pair with a real hyphen ("jours-amende")."""
    def _sub(m: re.Match) -> str:
        word = m.group(0)
        parts = word.split(SHY)
        pieces = [parts[0]]
        reason = "soft_hyphen"
        for left, right in zip(parts, parts[1:]):
            pair = (left + "-" + right).lower()
            # A soft hyphen is invisible; it becomes a real one only where the
            # corpus really writes that pair hyphenated ("jours-amende").
            if (lexicon is not None and lexicon.hyphenated(pair) >= MIN_EVIDENCE
                    and lexicon.word((left + right).lower())
                    < CLOSED_DOMINANCE * lexicon.hyphenated(pair)):
                pieces.append("-")
                reason = "lexicon"
            pieces.append(right)
        out = "".join(pieces)
        action = HYPHEN if "-" in out else JOIN
        if stats is not None:
            stats[f"{action}:{reason}"] += len(parts) - 1
        if record is not None:
            s, e = max(0, m.start() - 30), min(len(text), m.end() + 30)
            record.append((action, reason, text[s:e], text[s:m.start()] + out + text[m.end():e]))
        return out
    return _SHY_WORD_RE.sub(_sub, text)


_TOKEN_START_RE = re.compile(r"\S*$")
_URL_MARK_RE = re.compile(r"://|\bwww\.|@[^\W_]", re.IGNORECASE)


def _in_url_or_email(tail: str, text: str, end: int) -> bool:
    """The hyphen closing ``tail`` sits in a URL or an e-mail address: the
    token around it ("https://www.ag.ch/de/gesetze-", "info@zootier-") or
    its continuation ("lexikon.org", "entscheide/") carries a URL mark."""
    head = _TOKEN_START_RE.search(tail).group(0)
    if _URL_MARK_RE.search(head):
        return True
    nxt = re.match(r"\S*", text[end:end + 200]).group(0)
    return bool(_URL_MARK_RE.search(nxt)) and ("/" in nxt or "@" in nxt)


def _one_pass(text: str, lang: str, lexicon: Lexicon | None,
              stats: Counter | None, record: list | None) -> str:
    if SHY in text:
        text = _resolve_shy_words(text, lexicon, stats, record)
    out: list[str] = []
    tail = ""          # the last characters of the output built so far
    last = 0
    for m in _CANDIDATE_RE.finditer(text):
        h, ws = m.group("h"), m.group("ws")
        s, e = m.start(), m.end()
        chunk = text[last:s]
        out.append(chunk)
        tail = (tail + chunk)[-400:]
        last = e
        ma = _LETTERS_TAIL_RE.search(tail)
        if h == "-" and _in_url_or_email(tail, text, e):
            # a hyphen inside a URL or an e-mail address is never touched
            out.append(m.group(0))
            tail = (tail + m.group(0))[-400:]
            if stats is not None:
                stats["keep:url_or_email"] += 1
            continue
        mb = _LETTERS_HEAD_RE.match(text, e)
        if not ma or not mb:
            rep = m.group(0)
        else:
            # ``a`` is read from the output, so a word broken twice
            # ("Ver-⏎fügungs-⏎pflicht") is decided on its rejoined head.
            a, b = ma.group(0), mb.group(0)
            before_a = tail[ma.start() - 1] if ma.start() > 0 else ""
            j = mb.end()
            nxt = text[j:j + 1]
            if nxt == "-" and text[j + 1:j + 2].islower():
                # an in-line chain continues in lower case: "Just-in-time",
                # "Ponts-et-chaussées" (not "Outsourcing-Verhältnis")
                after_b = "-"
            elif nxt in ("'", "\u2019"):
                after_b = "'"           # an elision: "l'esecuzione"
            else:
                after_b = ""
            action, reason = _decide_cached(a, b, h, ws, before_a, after_b, lang, lexicon)
            if action == JOIN:
                rep = ""
            elif action == HYPHEN:
                rep = "-"
            elif action == SUSPENDED:
                rep = "- "
            elif h == SHY:
                rep = "-" + ws          # a kept soft hyphen becomes visible
            else:
                rep = m.group(0)
            if stats is not None:
                stats[f"{action}:{reason}"] += 1
            if record is not None and rep != m.group(0):
                ctx_s = max(0, s - len(a) - 30)
                ctx_e = min(len(text), mb.end() + 30)
                record.append((action, reason, text[ctx_s:ctx_e],
                               text[ctx_s:s] + rep + text[e:ctx_e]))
        out.append(rep)
        tail = (tail + rep)[-400:]
    out.append(text[last:])
    res = "".join(out)
    if SHY in res:                     # stray: not between two letters
        if stats is not None:
            stats["drop:stray_soft_hyphen"] += res.count(SHY)
        res = res.replace(SHY, "")
    return res


def dehyphenate(text: str | None, language: str | None = "de",
                lexicon: Lexicon | None = None,
                stats: Counter | None = None,
                record: list | None = None) -> str | None:
    """Text with line-break hyphenation removed (see the module docstring).

    ``stats`` (a Counter) is incremented per ``action:reason`` of the first
    pass; ``record`` collects ``(action, reason, before, after)`` context
    pairs of every change, for review.

    Runs to a fixed point (at most three passes), so the function is
    idempotent even where one change alters the fragment another candidate
    is decided on.
    """
    if not text:
        return text
    if "\u2010" in text or "\u2011" in text:
        text = text.translate(_UNICODE_HYPHENS)
    if not _ANY_CANDIDATE_RE.search(text):
        return text
    lang = (language or "de").lower()[:2]
    cur = _one_pass(text, lang, lexicon, stats, record)
    for _ in range(2):
        nxt = _one_pass(cur, lang, lexicon, None, None)
        if nxt == cur:
            break
        cur = nxt
    return cur


# ── lexicon counting (used by scripts/build_dehyphen_lexicon.py) ─────

_SENTINEL = "\x00"
# Every place a word may be broken is cut out before counting, so the
# halves of a broken word never count as words, and nothing that spans a
# break counts as a hyphenated pair.
_BREAKS_RE = re.compile(
    r"(?<=" + _L + r")(?:-[ \t]*\r?\n[ \t\r\n]*|-[ \t]+|\u00ad[ \t\r\n]*)"
)
_NOT_BEFORE = r"(?<!" + _L + r")(?<!\x00)"
_NOT_AFTER = r"(?!" + _L + r")(?!\x00)"
_WORD_RE = re.compile(_NOT_BEFORE + r"(" + _L + r"+)" + _NOT_AFTER)
_PAIR_RE = re.compile(_NOT_BEFORE + r"(" + _L + r"+)-(?=(" + _L + r"+)" + _NOT_AFTER + r")")


_SPACED_RE = re.compile(
    r"(?<!" + _L + r")(" + _L + r"+)-[ \t]+(" + _L + r"+)(?!" + _L + r")"
)


def count_tokens(text: str, words: Counter, hyphenated: Counter,
                 spaced: Counter | None = None,
                 capitalized: Counter | None = None) -> None:
    """Add the clean in-line words and hyphenated pairs of ``text``, and
    (``spaced``) the in-line "x- y" pairs, keyed "x- y", and
    (``capitalized``) the words written with a capital and then lower case.

    Hyphenated pairs count only with a lower-case continuation as written
    ("procès-verbal", not "Hans-Ruedi"): the evidence is used for line
    breaks with a lower-case continuation only."""
    if not text:
        return
    text = text.translate(_UNICODE_HYPHENS)
    if spaced is not None:
        for m in _SPACED_RE.finditer(text):
            spaced[(m.group(1) + "- " + m.group(2)).lower()] += 1
    t = _BREAKS_RE.sub(_SENTINEL, text)
    for m in _WORD_RE.finditer(t):
        w = m.group(1)
        lw = w.lower()
        words[lw] += 1
        if capitalized is not None and w[0].isupper() and not w.isupper():
            capitalized[lw] += 1
    for m in _PAIR_RE.finditer(t):
        if m.group(2)[0].islower():
            hyphenated[(m.group(1) + "-" + m.group(2)).lower()] += 1
