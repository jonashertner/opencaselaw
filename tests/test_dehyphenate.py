"""dehyphenate.py: line-break hyphens go, real hyphens stay, nothing else changes."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dehyphenate as dh  # noqa: E402
from dehyphenate import DictLexicon, SqliteLexicon, count_tokens, dehyphenate  # noqa: E402

LEX = DictLexicon(
    words={"überweisung": 9, "notamment": 9, "poursuite": 9, "staatsanwaltschaft": 9,
           "appel": 9, "verfügung": 9, "département": 9, "taxe": 9, "témoin": 9,
           "presupposto": 9, "poursuivie": 9, "stipulées": 9, "recourant": 9,
           "sowie": 9, "konkurs": 9, "personne": 9, "citoyenne": 9, "fondé": 9, "bien": 9,
           "ment": 9, "émetteurs": 9, "recourante": 9},
    hyphenated={"procès-verbal": 9, "jours-amende": 9, "citoyen-ne": 3, "bien-fondé": 9},
)


@pytest.mark.parametrize("lang,src,want", [
    # broken words rejoin
    ("de", "die Über-\nweisung", "die Überweisung"),
    ("de", "die Über-\r\nweisung", "die Überweisung"),
    ("de", "die Über- \n  weisung", "die Überweisung"),
    ("fr", "notam\u00adment", "notamment"),
    ("fr", "la pour-\nsuite", "la poursuite"),
    ("fr", "l'ap-\npel", "l'appel"),
    ("fr", "du Dé-\npartement", "du Département"),
    ("it", "un presup-\nposto", "un presupposto"),
    ("de", "Staats-\nanwalt-\nschaft", "Staatsanwaltschaft"),
    ("de", "die Ver- fügung", "die Verfügung"),          # break already collapsed
    # real hyphens stay, only the break goes
    ("fr", "un procès-\nverbal", "un procès-verbal"),
    ("fr", "40 jours\u00adamende", "40 jours-amende"),   # NE's soft hyphen as a real one
    ("fr", "elle-\nmême", "elle-même"),
    ("fr", "c'est-à-\ndire", "c'est-à-dire"),
    ("fr", "le bien-\nfondé", "le bien-fondé"),
    ("fr", "de La Chaux-de-\nFonds", "de La Chaux-de-Fonds"),
    ("fr", "pour\u00adsui\u00advie", "poursuivie"),          # NE: a soft hyphen per syllable
    ("fr", "sti\u00adpu\u00adlées par contrat", "stipulées par contrat"),
    ("fr", "le recou-\n\nrant", "le recourant"),              # NE: stray blank line, lexicon
    ("de", "die Über-\n\nweisung", "die Überweisung"),
    ("de", "Kon-\nkurs so-\nwie", "Konkurs sowie"),           # "wie" is no conjunction here
    # French inclusive writing
    ("fr", "d'« Enseignant-\ne postobligatoire »", "d'« Enseignant-e postobligatoire »"),
    ("fr", "étudiant-e-s inscrit-\ne-s", "étudiant-e-s inscrit-e-s"),
    ("fr", "un-e citoyen-\nne", "un-e citoyen-ne"),
    ("fr", "une per-\nsonne", "une personne"),
    ("fr", "les Ponts-\net-chaussées", "les Ponts-et-chaussées"),
    ("fr", "de \"Commis-\ngreffier-ère MP\"", "de \"Commis-greffier-ère MP\""),
    # a broken fragment that continues a chain still rejoins
    ("fr", "deux émet-\nteurs-récepteurs", "deux émetteurs-récepteurs"),
    ("fr", "la recou-\n\nrante-jointe", "la recourante-jointe"),
    ("de", "aus charak-\nterlich-psychischen Gründen", "aus charakterlich-psychischen Gründen"),
    # a stray German hyphen before a function word is left alone
    ("de", "in Uniform-\nmit der", "in Uniform-\nmit der"),
    ("de", "diese An-\nordnung", "diese Anordnung"),
    ("de", "Basel-\nStadt", "Basel-Stadt"),
    ("de", "IV-\nRente", "IV-Rente"),
    # suspended compounds keep their hyphen and space
    ("de", "Kindes-\nund Erwachsenenschutz", "Kindes- und Erwachsenenschutz"),
    ("de", "Kindes- und Erwachsenenschutz", "Kindes- und Erwachsenenschutz"),
    ("fr", "hétéro- et auto-agressif", "hétéro- et auto-agressif"),
    ("fr", "une bi- ou tri-\nvoire", "une bi- ou tri- voire"),
    ("it", "Straf-\ne Zivilkläger", "Straf- e Zivilkläger"),
    # left exactly as the source has it
    ("de", "1934-\n1935", "1934-\n1935"),
    ("fr", "taxe-\n\ntémoin", "taxe-\n\ntémoin"),        # a blank line needs evidence
    ("fr", "une taxe-\ntémoin", "une taxe-\ntémoin"),      # undecided: left as it is
    ("fr", "du juge-\nInent", "du juge-\nInent"),          # OCR "In" for "m": left as it is
    ("fr", "sur opposition-\nE. Il", "sur opposition-\nE. Il"),
    ("de", "Bundes- Gericht", "Bundes- Gericht"),
    ("de", "Art. 5 - 7", "Art. 5 - 7"),
    ("de", "Bern - Zürich", "Bern - Zürich"),
    ("de", "A.________-\nB.", "A.________-\nB."),
    ("de", "E-\nmail", "E-\nmail"),
    ("de", "Abs. 2 lit. a-\n", "Abs. 2 lit. a-\n"),
    ("de", "Basel-Stadt", "Basel-Stadt"),
    # Unicode hyphens become ASCII; a stray soft hyphen goes
    ("fr", "sous\u2011couverture", "sous-couverture"),
    ("de", "Bundes\u2010gericht", "Bundes-gericht"),
    ("de", "Ende.\u00ad", "Ende."),
])
def test_cases(lang, src, want):
    assert dehyphenate(src, lang, LEX) == want


def test_no_letter_is_rewritten():
    """The only differences are removed hyphens, soft hyphens and
    whitespace: the letters of the text, in order, are unchanged."""
    src = ("Der Beschwerde-\nführer verlangt die Über-\nweisung; procès-\nverbal; "
           "Kindes-\nund Erwachsenenschutz; notam\u00adment; 1934-\n1935.")
    out = dehyphenate(src, "de", LEX)
    keep = lambda s: "".join(ch for ch in s if ch.isalnum())  # noqa: E731
    assert keep(out) == keep(src)


@pytest.mark.parametrize("src", [
    "Staats-\nanwalt-\nschaft", "elle-\nmême", "Kindes-\nund", "Ver- fügung",
    "inconnu-\nmot", "x\u00ad\ny", "1934-\n1935", "c'est-à-\ndire",
])
@pytest.mark.parametrize("lex", [None, LEX])
def test_idempotent(src, lex):
    once = dehyphenate(src, "fr", lex)
    assert dehyphenate(once, "fr", lex) == once


def test_without_lexicon_french_line_break_stays():
    """No lexicon: German joins, French keeps a visible hyphen it cannot decide."""
    assert dehyphenate("die Über-\nweisung", "de") == "die Überweisung"
    assert dehyphenate("un mot-\nvalise", "fr") == "un mot-\nvalise"
    assert dehyphenate("notam\u00adment", "fr") == "notamment"
    # A mid-line "x- y" is never joined without evidence.
    assert dehyphenate("die Ver- fügung", "de") == "die Ver- fügung"


def test_conjunction_beats_corrupted_closed_forms():
    """The full corpus holds closed forms some source corrupted
    ("straferhöhungsoder"); they must never join a suspended compound."""
    lex = DictLexicon(words={"mitwirkungsbzw": 40, "straferhöhungsoder": 12,
                             "kindesund": 5, "fini": 50})
    assert dehyphenate("von Mitwirkungs- bzw. Auskunftspflichten", "de", lex) \
        == "von Mitwirkungs- bzw. Auskunftspflichten"
    assert dehyphenate("als Straferhöhungs- oder Strafminderungsgrund", "de", lex) \
        == "als Straferhöhungs- oder Strafminderungsgrund"
    assert dehyphenate("Kindes-\nund Erwachsenenschutz", "de", lex) \
        == "Kindes- und Erwachsenenschutz"
    # a two-letter "conjunction" at a line break is still a word's tail
    assert dehyphenate("le délai est fi-\nni", "fr", lex) == "le délai est fini"
    assert dehyphenate("hétéro- et auto-agressif", "fr", DictLexicon(words={"hétéroet": 9})) \
        == "hétéro- et auto-agressif"


def test_spaced_pairs_mark_suspended_compounds():
    """"weder Rechtfertigungs- noch Schuldausschliessungsgründe": the corpus
    writes the pair spaced hundreds of times and closed only where a source
    corrupted it; a real break ("wer- den") is closed orders of magnitude
    more often than spaced."""
    lex = DictLexicon(
        words={"rechtfertigungsnoch": 12, "werden": 900_000, "geschäftsins": 4,
               "dennoch": 50_000},
        spaced={"rechtfertigungs- noch": 673, "wer- den": 96, "geschäfts- ins": 306},
    )
    assert dehyphenate("weder Rechtfertigungs- noch Schuld", "de", lex) \
        == "weder Rechtfertigungs- noch Schuld"
    assert dehyphenate("weder Rechtfertigungs-\nnoch Schuld", "de", lex) \
        == "weder Rechtfertigungs- noch Schuld"
    assert dehyphenate("vom Geschäfts- ins Privatvermögen", "de", lex) \
        == "vom Geschäfts- ins Privatvermögen"
    assert dehyphenate("muss wer- den", "de", lex) == "muss werden"
    assert dehyphenate("den-\nnoch", "de", lex) == "dennoch"
    # found in BJ SchKG text: OCR writes "ci- dessus" too, but the hyphenated
    # spelling is far commoner, so the hyphen stays and the break goes
    lex2 = DictLexicon(hyphenated={"ci-dessus": 40_000}, spaced={"ci- dessus": 300})
    assert dehyphenate("nommée ci-\ndessus a", "fr", lex2) == "nommée ci-dessus a"


def test_head_to_head_findings():
    """Four defects found comparing against entscheidsuche's rule on 855 real
    SchKG / cantonal texts (counts from the full-corpus lexicon)."""
    lex = DictLexicon(
        words={"öffentlich": 294_776, "lich": 27_290, "esistenza": 88_801, "e": 14_000_000,
               "sistenza": 70, "divulgation": 5_296, "divul": 0, "gation": 146,
               "miet": 1_121, "bewerbung": 5_452, "alimenten": 702, "inkassostellen": 86,
               "physisch": 900, "psychisch": 2_000, "süd": 9_000, "südachse": 12},
        hyphenated={"öffentlich-rechtliche": 50_000, "esistenza": 0, "e-sistenza": 3},
        capitalized={"bewerbung": 5_300, "inkassostellen": 85, "psychisch": 30},
    )
    # 1. "-rechtliche" after a broken fragment is no reason to keep the break
    assert dehyphenate("die öffent-\nlich-rechtliche Streitigkeit", "de", lex) \
        == "die öffentlich-rechtliche Streitigkeit"
    # 2. a one-letter fragment after an apostrophe is an elided word's head
    assert dehyphenate("il diritto d'e-\nsistenza", "it", lex) == "il diritto d'esistenza"
    # 3. a broken fragment at the tail of a chain, hyphen or soft hyphen
    assert dehyphenate("clause de non-divul-\ngation", "fr", lex) == "clause de non-divulgation"
    assert dehyphenate("clause de non-divul­\ngation", "fr", lex) == "clause de non-divulgation"
    # 4. a noun continuation in lower case is a break; an adjective keeps it
    assert dehyphenate("Stellen- oder Miet-\nbewerbung", "de", lex) == "Stellen- oder Mietbewerbung"
    assert dehyphenate("die Alimenten-\ninkassostellen", "de", lex) == "die Alimenteninkassostellen"
    assert dehyphenate("physisch-\npsychisch", "de", lex) == "physisch-\npsychisch"
    # a chain tail that is a word keeps its hyphen
    assert dehyphenate("die Nord-Süd-\nachse", "de", lex) == "die Nord-Süd-achse"


def test_soft_hyphen_at_a_line_end_never_becomes_a_visible_hyphen():
    """A soft hyphen is invisible in the source: it joins, also inside a
    chain or before a capital, unless the pair is really written hyphenated."""
    lex = DictLexicon(words={"ver": 200_000, "versteigerung": 20_000, "of": 900_000},
                      hyphenated={"jours-amende": 9_000})
    assert dehyphenate("und Online-Ver­\nsteigerung", "de", lex) == "und Online-Versteigerung"
    assert dehyphenate("[EGz­\nSchKG]", "de", lex) == "[EGzSchKG]"
    assert dehyphenate("le-pilotage-des-of­\nfices-des", "fr", lex) == "le-pilotage-des-offices-des"
    assert dehyphenate("20 jours­\namende", "fr", lex) == "20 jours-amende"
    assert dehyphenate("Betreibungs­\nund Konkurs", "de", lex) == "Betreibungs- und Konkurs"


def test_independent_audit_findings():
    """Independent audit (3,659 docs, 402 hand-judged edits): capitals names
    and citations got a stray space; hyphens inside URLs and e-mail
    addresses were removed."""
    lex = DictLexicon(
        words={"gesetzeentscheide": 5, "zootierlexikon": 3, "schuldbetreibung": 40_000,
               "lpavd": 9, "chzpo": 40, "schwarzenegger": 900},
        spaced={"zpo- sprecher": 12, "sutter- somm": 30, "lpa- vd": 30, "hug- beeli": 3},
    )
    # re-audit: polluted closed forms of abbreviation pairs never join
    assert dehyphenate("art. 1 al. 1 LPA-\nVD (loi vaudoise", "fr", lex) \
        == "art. 1 al. 1 LPA-VD (loi vaudoise"
    assert dehyphenate("die CH-\nZPO", "de", lex) == "die CH-ZPO"
    assert dehyphenate("vgl. SCHWARZEN-\nEGGER, a.a.O.", "de", lex) == "vgl. SCHWARZENEGGER, a.a.O."


def test_capitals_names_join_abbreviation_pairs_do_not():
    """Round 3 audit, with the full-corpus counts: author names broken at a
    syllable join; abbreviation pairs keep the hyphen, also where an
    accidental closed form exists ("euch")."""
    lex = DictLexicon(
        words={"staehelin": 36_343, "meyer": 91_236, "müller": 115_230, "geiser": 31_661,
               "andreas": 40_375, "besonderer": 46_735, "walter": 43_445, "zweifel": 199_314,
               "lpavd": 9, "lpage": 20, "euch": 1_398, "beag": 18, "chzpo": 0,
               "schuldbetreibung": 40_000},
        hyphenated={"müller": 0},
        spaced={"lpa- vd": 30, "mül- ler": 40, "staehe- lin": 5},
    )
    for src, want in [("STAEHE-\nLIN", "STAEHELIN"), ("MEY-\nER", "MEYER"),
                      ("MÜL-\nLER", "MÜLLER"), ("GEI-\nSER", "GEISER"),
                      ("AN-\nDREAS", "ANDREAS"), ("BE-\nSONDERER", "BESONDERER"),
                      ("WAL-\nTER", "WALTER"), ("ZWEI-\nFEL", "ZWEIFEL"),
                      ("SCHULD-\nBETREIBUNG", "SCHULDBETREIBUNG"),
                      ("LPA-\nVD", "LPA-VD"), ("LPA-\nGE", "LPA-GE"), ("EU-\nCH", "EU-CH"),
                      ("BE-\nAG", "BE-AG"), ("CH-\nZPO", "CH-ZPO")]:
        assert dehyphenate(f"vgl. {src}, N 1", "de", lex) == f"vgl. {want}, N 1", src
    # capitals: the hyphen stays, only the break goes; no stray space
    assert dehyphenate("BSK ZPO-\nSPRECHER, Art. 1", "de", lex) == "BSK ZPO-SPRECHER, Art. 1"
    assert dehyphenate("SUTTER-\nSOMM/HOSTETTLER", "de", lex) == "SUTTER-SOMM/HOSTETTLER"
    assert dehyphenate("(LPA-\nVD)", "fr", lex) == "(LPA-VD)"
    assert dehyphenate("HUG- BEELI", "de", lex) == "HUG- BEELI"           # mid-line: as written
    # an all-caps heading broken at a line end still joins
    assert dehyphenate("ÜBER SCHULD-\nBETREIBUNG", "de", lex) == "ÜBER SCHULDBETREIBUNG"
    # URLs and e-mail addresses are never touched
    url = "siehe https://www.ag.ch/de/gesetze-\nentscheide/ und"
    assert dehyphenate(url, "de", lex) == url
    mail = "an info@zootier-\nlexikon.org senden"
    assert dehyphenate(mail, "de", lex) == mail
    slug = "https://x.ch/abo/monats-\nund-jahresabo"
    assert dehyphenate(slug, "de", lex) == slug
    # ordinary text next to a URL is still handled
    assert dehyphenate("Über-\nweisung, https://x.ch", "de", LEX) == "Überweisung, https://x.ch"


def test_inclusive_ending_needs_real_use():
    lex = DictLexicon(words={"personnelles": 40_000, "citoyenne": 3_000},
                      hyphenated={"personnel-les": 3, "citoyen-ne": 50})
    assert dehyphenate("données personnel-\nles", "fr", lex) == "données personnelles"
    assert dehyphenate("un-e citoyen-\nne", "fr", lex) == "un-e citoyen-ne"


def test_lower_case_chain_keeps_a_word_continuation():
    """Found on the full corpus: "Just-⏎in-time" became "Justin-time"."""
    lex = DictLexicon(
        words={"justin": 219, "in": 23_000_000, "émetteurs": 2_000, "teurs": 5,
               "outsourcing": 5_000, "sourcing": 30},
        hyphenated={"just-in": 21},
        capitalized={"justin": 219, "outsourcing": 4_000},
    )
    assert dehyphenate("ein Just-\nin-time-Prinzip", "de", lex) == "ein Just-in-time-Prinzip"
    assert dehyphenate("deux émet-\nteurs-récepteurs", "fr", lex) == "deux émetteurs-récepteurs"
    assert dehyphenate("ein Out-\nsourcing-Verhältnis", "de", lex) == "ein Outsourcing-Verhältnis"


def test_count_tokens_spaced_pairs():
    w, h, s = Counter(), Counter(), Counter()
    count_tokens("weder Rechtfertigungs- noch Schuld; Kindes- und Erwachsenen", w, h, s)
    assert s["rechtfertigungs- noch"] == 1 and s["kindes- und"] == 1


def test_no_hyphen_removed_unless_it_is_only_a_line_break():
    """The governing rule: a hyphen goes only where the evidence says it is
    a line break and nothing else."""
    lex = DictLexicon(
        words={"portemonnaie": 300, "werden": 900_000, "den": 5_000_000,
               "vorinstanz": 400_000, "instanz": 60_000, "adéquats": 800,
               "durchführung": 300_000, "rung": 40, "physisch": 900, "psychisch": 2000,
               "email": 500},
        hyphenated={"porte-monnaie": 40, "e-mail": 600},
    )
    # both spellings in real use: the hyphen stays, only the break goes
    assert dehyphenate("son porte-\nmonnaie", "fr", lex) == "son porte-monnaie"
    assert dehyphenate("per e-\nmail", "de", lex) == "per e-mail"
    # mid-line, no line break in the text: only two non-words are joined
    assert dehyphenate("ces terrains adé- quats", "fr", lex) == "ces terrains adéquats"
    assert dehyphenate("die Durchfüh- rung", "de", lex) == "die Durchführung"
    assert dehyphenate("die Vor- instanz", "de", lex) == "die Vor- instanz"
    assert dehyphenate("muss wer- den", "de", lex) == "muss wer- den"
    # at a line break the closed form decides: "wer-⏎den" is a break
    assert dehyphenate("muss wer-\nden", "de", lex) == "muss werden"
    # no evidence and both halves are words: the hyphen may be real
    assert dehyphenate("physisch-\npsychisch", "de", lex) == "physisch-\npsychisch"
    # a soft hyphen becomes a real one where the pair is really written so
    assert dehyphenate("son porte­monnaie", "fr", lex) == "son porte-monnaie"


def test_midline_needs_clear_evidence():
    lex = DictLexicon(words={"verfügung": 2}, hyphenated={"ver-fügung": 1})
    assert dehyphenate("die Ver- fügung", "de", lex) == "die Ver- fügung"


def test_stats_and_record():
    st, rec = Counter(), []
    dehyphenate("die Über-\nweisung und das procès-\nverbal", "fr", LEX, st, rec)
    assert st["join:lexicon"] == 1 and st["hyphen:lexicon"] == 1
    assert [r[0] for r in rec] == ["join", "hyphen"]
    assert "Überweisung" in rec[0][3]


def test_count_tokens_ignores_broken_halves():
    w, h = Counter(), Counter()
    count_tokens("Die Ver-\nfügung, die Über- weisung, notam\u00adment, ein procès-verbal.", w, h)
    assert w["fügung"] == w["ver"] == w["weisung"] == w["notam"] == w["ment"] == 0
    assert w["die"] == 2 and h["procès-verbal"] == 1
    assert "ver-fügung" not in h


def test_lexicon_build_roundtrip(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_dehyphen_lexicon as bl

    shard = tmp_path / "x.jsonl"
    rows = [{"full_text": "Die Überweisung. Ein procès-verbal. Ein procès-verbal."},
            {"full_text": "Die Überweisung und die Über-\nweisung."}]
    shard.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "lex.db"
    meta = bl.build([shard], out, min_count=2)
    assert meta["rows"] == "2"
    lex = SqliteLexicon(out)
    assert lex.word("überweisung") == 2      # the broken one does not count
    assert lex.hyphenated("procès-verbal") == 2
    assert lex.word("ein") == 2 and lex.word("und") == 0  # min_count prunes

    monkeypatch.setenv(dh.LEXICON_ENV, str(out))
    dh._lexicon_cache.clear()
    assert dh.configured_lexicon() is not None


def test_configured_lexicon_off_or_missing(monkeypatch, tmp_path):
    dh._lexicon_cache.clear()
    monkeypatch.delenv(dh.LEXICON_ENV, raising=False)
    assert dh.configured_lexicon() is None
    monkeypatch.setenv(dh.LEXICON_ENV, str(tmp_path / "absent.db"))
    assert dh.configured_lexicon() is None   # warns, never raises


def test_quote_match_folds_hyphenation_both_ways():
    """attest_response / check_claim_support: a quote copied from the text
    before dehyphenation and one copied after both verify against either."""
    from mcp_server import _normalise_for_quote_match as norm

    old_source = "Der Beschwerde-\nführer verlangt die Über-\nweisung des Betrags."
    new_source = dehyphenate(old_source, "de", LEX)
    for quote in ("Beschwerdeführer verlangt die Überweisung",
                  "Beschwerde-\nführer verlangt die Über-\nweisung",
                  "Beschwerde- führer verlangt die Über- weisung"):
        assert norm(quote) in norm(old_source)
        assert norm(quote) in norm(new_source)
    assert norm("procès-\nverbal") == norm("procès-verbal") == norm("procès\u00adverbal")
    # Hyphens that are not between two letters are left as they are.
    assert "1934-1935" in norm("1934-1935")


def test_lexicon_build_from_parquet_with_workers(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_dehyphen_lexicon as bl

    for i in range(2):
        table = pa.table({
            "decision_id": [f"x_{i}_a", f"x_{i}_b"],
            "full_text": ["Die Überweisung. Ein procès-verbal.", "Die Über-\nweisung."],
            "regeste": [None, "Überweisung"],
        })
        pq.write_table(table, tmp_path / f"c{i}.parquet")
    out = tmp_path / "lex.db"
    meta = bl.build(bl.input_paths([], [str(tmp_path)]), out, min_count=2, workers=2)
    assert meta["rows"] == "4" and meta["inputs"] == "2"
    lex = SqliteLexicon(out)
    assert lex.word("überweisung") == 4     # 2 full_text + 2 regeste; broken ones not
    assert lex.hyphenated("procès-verbal") == 2
    assert lex.capitalized("überweisung") == 4


def test_german_noun_lower_case_continuation_is_a_break():
    """German capitalises after the hyphen in a noun compound, so a
    lower-case continuation inside a noun is the line break, whatever
    artefacts the in-line counts carry; an adjective compound keeps it."""
    lex = DictLexicon(
        words={"strafprozessordnung": 72_000, "sozialpsychiatrische": 300, "email": 900},
        hyphenated={"strafprozess-ordnung": 969, "sozial-psychiatrische": 15},
        capitalized={"strafprozessordnung": 71_000, "sozialpsychiatrische": 20,
                     "email": 800},
    )
    assert dehyphenate("die Strafprozess-\nordnung", "de", lex) == "die Strafprozessordnung"
    assert dehyphenate("eine sozial-\npsychiatrische", "de", lex) == "eine sozial-psychiatrische"
    # one-letter fragment: no noun rule ("E-mail" is a Swiss spelling)
    assert dehyphenate("per E-\nmail", "de", lex) == "per E-\nmail"
    # not in French: no capitalisation signal there
    assert dehyphenate("la Strafprozess-\nordnung", "fr", lex) == "la Strafprozess-ordnung"


def test_noun_rule_never_beats_a_dominant_hyphenated_spelling():
    """Found on the full corpus: place names and abbreviation compounds in
    German text look like capitalised closed nouns ("Chauxde" 16x,
    "IVfremden" 2x) and must keep their hyphen."""
    lex = DictLexicon(
        words={"chauxde": 16, "valde": 5, "ivfremden": 2, "usamerikanische": 5,
               "ahvpflichtige": 22, "klinischneurologisch": 6},
        hyphenated={"chaux-de": 12870, "val-de": 9221, "iv-fremden": 647,
                    "us-amerikanische": 666, "ahv-pflichtige": 943,
                    "klinisch-neurologisch": 852},
        capitalized={"chauxde": 16, "valde": 5, "ivfremden": 2, "usamerikanische": 5,
                     "ahvpflichtige": 22, "klinischneurologisch": 6},
    )
    assert dehyphenate("La Chaux-\nde-Fonds", "de", lex) == "La Chaux-de-Fonds"
    assert dehyphenate("im Val-\nde-Travers", "de", lex) == "im Val-de-Travers"
    assert dehyphenate("die IV-\nfremden Faktoren", "de", lex) == "die IV-fremden Faktoren"
    assert dehyphenate("die US-\namerikanische", "de", lex) == "die US-amerikanische"
    assert dehyphenate("die AHV-\npflichtige", "de", lex) == "die AHV-pflichtige"
    assert dehyphenate("Klinisch-\nneurologisch", "de", lex) == "Klinisch-neurologisch"


def test_count_tokens_capitalized_and_lower_case_pairs():
    w, h, s, c = Counter(), Counter(), Counter(), Counter()
    count_tokens("Die Strafprozessordnung, ein procès-verbal, Hans-Ruedi, DIE", w, h, s, c)
    assert c["strafprozessordnung"] == 1 and c["die"] == 1 and c["procès"] == 0
    assert h["procès-verbal"] == 1 and h["hans-ruedi"] == 0


# Cases from real SchKG / cantonal text, handed over by the serving-side
# session (quality/hyphen_handover_2026-11-05), with corpus-like counts.
SCHKG_LEX = DictLexicon(
    words={"rechtsöffnung": 30_000, "öffnung": 3_000, "april": 200_000, "untersuchen": 50_000,
           "sottoposti": 4_000, "toposti": 0, "fallimento": 20_000, "extraits": 9_000,
           "traits": 2_000, "dell": 90_000, "sowie": 800_000, "wie": 900_000,
           "poursuite": 60_000, "suite": 80_000, "schuldbetreibungs": 0,
           "vorinstanz": 2_500_000, "instanz": 60_000},
    hyphenated={"celle-ci": 30_000, "peut-être": 20_000, "non-entrée": 9_000,
                "ex-traits": 3},
    spaced={"rechts- öffnung": 40, "kauf- noch": 30, "vor- instanz": 216},
    capitalized={"rechtsöffnung": 29_900, "april": 199_000, "vorinstanz": 2_400_000},
)


@pytest.mark.parametrize("lang,src,want", [
    ("de", "Rechts-\nöffnung", "Rechtsöffnung"),
    ("de", "die Rechts- öffnung", "die Rechtsöffnung"),     # German noun, mid-line
    ("de", "die Vor- instanz", "die Vorinstanz"),
    ("de", "16. Ap-\nril 2021", "16. April 2021"),
    ("de", "zu un-\n  tersuchen", "zu untersuchen"),
    ("it", "sot- toposti al prelievo", "sottoposti al prelievo"),
    ("it", "fallimen-\nto", "fallimento"),
    ("fr", "ex-\ntraits", "extraits"),
    ("it", "del-\nl'esecuzione", "dell'esecuzione"),
    ("it", "del-\nl’esecuzione", "dell’esecuzione"),
    ("de", "so-\nwie", "sowie"),
    ("de", "Schuldbetrei-\nbungs- und Konkurs", "Schuldbetreibungs- und Konkurs"),
    ("de", "Rechts­öffnung", "Rechtsöffnung"),
    # kept
    ("fr", "la pour- suite", "la pour- suite"),            # French mid-line: no proof
    ("de", "Betreibungs- und Konkursamt", "Betreibungs- und Konkursamt"),
    ("de", "Betreibungs-\nund Konkursamt", "Betreibungs- und Konkursamt"),
    ("de", "Haupt- bzw. Eventualbegehren", "Haupt- bzw. Eventualbegehren"),
    ("de", "weder Kauf- noch Mietvertrag", "weder Kauf- noch Mietvertrag"),
    ("it", "socio- e politico", "socio- e politico"),
    ("de", "SchKG-\nBeschwerde", "SchKG-Beschwerde"),
    ("de", "SchKG- Beschwerde", "SchKG- Beschwerde"),
    ("fr", "celle-\nci", "celle-ci"),
    ("fr", "peut-\nêtre", "peut-être"),
    ("fr", "c'est-\nà-dire", "c'est-à-dire"),
    ("fr", "non-\nentrée", "non-entrée"),
    ("de", "lit. a-\nc SchKG", "lit. a-\nc SchKG"),
    ("de", "2- jährig", "2- jährig"),
    ("de", "Satz - und dann", "Satz - und dann"),
    ("de", "Art. 8-12 BV", "Art. 8-12 BV"),
    ("de", "E-Mail", "E-Mail"),
    ("de", "die Nord-Süd-\nachse", "die Nord-Süd-achse"),   # chain: the hyphen stays
])
def test_schkg_cases(lang, src, want):
    assert dehyphenate(src, lang, SCHKG_LEX) == want


def test_practice_db_build_rejoins_only_when_switched_on(tmp_path, monkeypatch):
    """practice.db is built apart from decisions.db (BJ SchKG scans, KBKS
    PDFs): the same switch rejoins its body_text."""
    import sqlite3
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_dehyphen_lexicon as bl
    from search_stack import build_practice_db as bp

    src = tmp_path / "practice"
    src.mkdir()
    (src / "bj_schkg.jsonl").write_text(json.dumps({
        "doc_id": "bj_x", "source": "bj_schkg", "issuing_authority": "BJ",
        "doc_type": "Kreisschreiben", "language": "de", "title": "KS 11",
        "body_text": "gestützt auf Art. 93 SchKG ist ge-\nstützt und zu prü-\nfen",
    }) + "\n", encoding="utf-8")
    shard = tmp_path / "lex.jsonl"
    shard.write_text("\n".join(json.dumps({"full_text": "gestützt prüfen gestützt prüfen"})
                               for _ in range(2)), encoding="utf-8")
    lex = tmp_path / "lex.db"
    bl.build([shard], lex, min_count=2)

    monkeypatch.delenv(dh.LEXICON_ENV, raising=False)
    dh._lexicon_cache.clear()
    off = tmp_path / "off.db"
    bp.build(src, off)
    body_off = sqlite3.connect(off).execute("SELECT body_text FROM practice").fetchone()[0]
    assert "ge-\nstützt" in body_off

    monkeypatch.setenv(dh.LEXICON_ENV, str(lex))
    dh._lexicon_cache.clear()
    on = tmp_path / "on.db"
    bp.build(src, on)
    body_on = sqlite3.connect(on).execute("SELECT body_text FROM practice").fetchone()[0]
    assert body_on == "gestützt auf Art. 93 SchKG ist gestützt und zu prüfen"
