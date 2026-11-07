"""attest_response: the ZGB final title and quotes from dated editions
(2026-10 agent-bench pilot, results/2026-10-14_pilot_sonnet_k5).

Two false alarms in the closing audit:

1. "Art. 49 SchlT ZGB" / "art. 49 Tit. fin. CC" parsed as law 'SchlT' /
   'Tit' -> law_abbreviation_unknown, and a quote from the final title was
   never checked against it. The name now resolves through the section
   entries of docs/api/law_aliases.json to block disp_u1 of SR 210, and the
   main-body article under the same number is never used instead.
2. A verbatim quote of an article's former wording, read with
   get_law(as_of=...), was reported as fabricated because the pool held the
   current text only. A quote missing from the current text is now looked up
   in the edition in force on a date the draft states next to it.

Offline: the statutes mirror is an in-memory fixture with the production
schema, and the Fedlex edition fetch is stubbed. Article texts are fixture
placeholders, not statute wording. Every positive test has a negative
control.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mcp_server as m  # noqa: E402
from _statutes_fixture import make_statutes_conn  # noqa: E402

SCHLT = {
    "de": "Schlusstitel: Anwendungs- und Einführungsbestimmungen",
    "fr": "Titre final: De l’entrée en vigueur et de l’application du code civil",
    "it": "Titolo finale: Dell’entrata in vigore e dell’applicazione del Codice civile",
}
FINAL_FR = ("Texte de test du titre final : la règle transitoire de ce fixture s'applique "
            "aux délais qui courent encore lors du changement de droit.")
FINAL_IT = ("Testo di prova del titolo finale: la regola transitoria di questo fixture si "
            "applica ai termini che decorrono ancora al cambiamento del diritto.")
FINAL_DE = ("Testtext des Schlusstitels: die Übergangsregel dieses Fixtures gilt für die "
            "Fristen, die beim Wechsel des Rechts noch laufen.")
MAIN_FR = ("Texte de test du corps du code : cette disposition principale du fixture porte "
           "le même numéro que celle du titre final.")
CO60_NOW = ("Texte de test de l'édition actuelle : dans ce fixture le délai relatif est de "
            "trois ans dès la connaissance du dommage.")
CO60_OLD = ("Texte de test de l'ancienne édition : dans ce fixture le délai relatif est d'un "
            "an dès la connaissance du dommage.")

ROWS = [
    {"sr_number": "210", "article_num": "49", "lang": "fr", "text": MAIN_FR},
    {"sr_number": "210", "article_num": "49", "lang": "fr", "section": "disp_u1",
     "section_heading": SCHLT["fr"], "text": FINAL_FR},
    {"sr_number": "210", "article_num": "49", "lang": "it", "section": "disp_u1",
     "section_heading": SCHLT["it"], "text": FINAL_IT},
    {"sr_number": "210", "article_num": "49", "lang": "de", "section": "disp_u1",
     "section_heading": SCHLT["de"], "text": FINAL_DE},
    {"sr_number": "220", "article_num": "60", "lang": "fr", "text": CO60_NOW},
]


def _conn(rows):
    def make():
        con = make_statutes_conn(rows)
        con.row_factory = sqlite3.Row            # as _get_statutes_conn opens it
        m._install_unicode_casing(con)
        return con
    return make


@pytest.fixture
def mirror(monkeypatch, tmp_path):
    def use(rows=ROWS):
        monkeypatch.setattr(m, "_get_statutes_conn", _conn(rows))
        monkeypatch.setattr(m, "_statute_text_cache", {})
    fake_db = tmp_path / "statutes.db"
    fake_db.touch()
    monkeypatch.setattr(m, "STATUTES_DB_PATH", fake_db)
    monkeypatch.setattr(m, "_law_alias_cache", None)
    use()
    return use


@pytest.fixture
def editions(monkeypatch):
    """Stub of the Fedlex edition fetch: CO 60 before 2020 is CO60_OLD."""
    calls: list[tuple] = []

    def fetch(sr, article, language, as_of):
        calls.append((sr, article, language, as_of))
        if sr == "220" and article == "60" and as_of < "2020-01-01":
            return {"sr_number": "220", "snapshot_date": "2017-04-01", "as_of": as_of,
                    "source_url": "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/20170401/fr",
                    "articles": [{"article_num": "60", "text": f"Art. 60\n1 {CO60_OLD}",
                                  "text_status": "ok"}]}
        return {"error": "no edition in this stub"}

    monkeypatch.setattr(m, "_fetch_historical_law_version", fetch)
    return calls


def _attest(draft: str) -> dict:
    return m._handle_attest_response(draft_text=draft, audit_quotes=True)


# ── 1. The final title ─────────────────────────────────────────────

@pytest.mark.parametrize("ref", [
    "Art. 49 SchlT ZGB", "Art. 49 SchlT", "Art. 49 ZGB SchlT", "Art. 49 Schlusstitel ZGB",
    "Art. 49 Schlusstitel des ZGB", "art. 49 al. 1 Tit. fin. CC", "art. 49 tit. fin. CC",
    "art. 49 Titre final CC", "art. 49 du titre final du CC", "art. 49 Titolo finale CC",
    "art. 49 cpv. 1 del Titolo finale del CC",
])
def test_every_final_title_name_resolves_to_its_block(mirror, ref):
    hit = m._STATUTE_AUDIT_PATTERN.search(ref)
    assert hit and hit.group("section_name"), ref
    _, law_code, article = m._statute_ref_key(hit)
    assert article == "49"
    got = m._fetch_statute_text(law_code=law_code, article=article, lang="fr", full=True)
    assert got["sr_number"] == "210" and got["section"] == "disp_u1", ref
    assert got["text"] == FINAL_FR, ref                  # never the main-body Art. 49
    assert m._audit_statutes(f"Voir {ref}.") == [], ref


def test_final_title_article_is_checked_not_waved_through(mirror):
    ledger: dict = {}
    issues = m._audit_statutes("Art. 49 SchlT ZGB und Art. 999 SchlT ZGB", ledger=ledger)
    assert [i["problem"] for i in issues] == ["article_not_in_law"]
    assert ledger["verified"] == 1 and ledger["missing_article"] == 1
    assert ledger["unknown_law"] == 0


def test_final_title_quote_is_checked_against_the_final_title(mirror):
    good = f"Selon l'art. 49 al. 1 Tit. fin. CC : « {FINAL_FR} »"
    res = _attest(good)
    assert res["issues_by_category"]["quote"] == 0 and res["issues_by_category"]["statute"] == 0
    assert res["ledger"]["quotations"]["matches"][0]["matched_source"] == "statute:TIT. FIN. CC_49"
    # Negative control: the main-body Art. 49 is a different provision.
    assert _attest(f"Selon l'art. 49 al. 1 Tit. fin. CC : « {MAIN_FR} »")[
        "issues_by_category"]["quote"] == 1


def test_italian_final_title_quote_passes(mirror):
    assert _attest(f"Secondo l'art. 49 Titolo finale CC: «{FINAL_IT}»")[
        "issues_by_category"]["quote"] == 0
    assert _attest(f"Secondo l'art. 49 Titolo finale CC: «{FINAL_IT.replace('regola', 'norma')}»")[
        "issues_by_category"]["quote"] == 1


def test_final_title_without_the_block_is_not_checked_not_unknown(mirror):
    mirror([r for r in ROWS if not r.get("section")])   # a build without disp_u1
    ledger: dict = {}
    assert m._audit_statutes("Art. 49 SchlT ZGB", ledger=ledger) == []
    assert ledger["not_checked"] == 1 and ledger["unknown_law"] == 0
    got = m._fetch_statute_text(law_code="SCHLT ZGB", article="49", lang="fr")
    assert got.get("section_unavailable") and not got.get("text")


def test_other_alias_kinds_stay_misses_in_the_audit(mirror):
    # Only section entries are read by the audit's resolver: a former act's
    # name (ANAG, in the table as kind 'former') still reports an unknown
    # abbreviation, as before.
    assert m._law_alias_data()["aliases"]["ANAG"]["kind"] == "former"
    issues = m._audit_statutes("Art. 1 ANAG")
    assert [i["problem"] for i in issues] == ["law_abbreviation_unknown"]


# ── 2. Quotes from a dated edition ─────────────────────────────────

OLD_DRAFT = ("**Ancien art. 60 al. 1 CO.** Selon l'édition Fedlex de 2017 : « {q} ».\n\n"
             "**Nouvel art. 60 al. 1 CO** (en vigueur depuis le 1er janvier 2020).")


def test_quote_of_the_dated_edition_passes(mirror, editions):
    res = _attest(OLD_DRAFT.format(q=CO60_OLD))
    assert res["issues_by_category"]["quote"] == 0
    match = res["ledger"]["quotations"]["matches"][0]
    assert match["matched_source"] == "statute:CO_60@2017-04-01"
    assert "statute:CO_60@2017-04-01" in res["ledger"]["quotations"]["searched_sources"]
    # The year next to the quote is tried first, in the quote's language.
    assert editions[0] == ("220", "60", "fr", "2017-12-31")


def test_fabricated_quote_with_a_date_stays_flagged(mirror, editions):
    bad = CO60_OLD.replace("d'un an", "de six mois")
    res = _attest(OLD_DRAFT.format(q=bad))
    assert res["issues_by_category"]["quote"] == 1
    issue = next(i for i in res["issues"] if i["category"] == "quote")
    assert issue["problem"] == "quote_not_in_cited_sources"
    searched = issue["dated_editions_searched"]
    assert {"law": "CO", "article": "60", "as_of": "2017-12-31", "edition": "2017-04-01",
            "status": "searched"} in searched


def test_current_text_still_passes_without_a_fetch(mirror, editions):
    assert _attest(f"Art. 60 al. 1 CO (2024) : « {CO60_NOW} »")["issues_by_category"]["quote"] == 0
    assert editions == []


def test_no_stated_date_means_no_fetch(mirror, editions):
    res = _attest(f"Selon l'ancien art. 60 al. 1 CO : « {CO60_OLD} »")
    assert res["issues_by_category"]["quote"] == 1
    assert editions == []


def test_edition_fetch_failure_keeps_the_flag_and_says_so(mirror, monkeypatch):
    monkeypatch.setattr(m, "_fetch_historical_law_version", lambda *a: None)
    res = _attest(OLD_DRAFT.format(q=CO60_OLD))
    issue = next(i for i in res["issues"] if i["category"] == "quote")
    assert {e["status"] for e in issue["dated_editions_searched"]} == {"unavailable"}


def test_edition_fetches_are_bounded(mirror, editions):
    dates = " ".join(f"{d}.03.20{y:02d}" for d, y in zip(range(1, 13), range(5, 17)))
    bad = CO60_OLD.replace("d'un an", "de six mois")
    _attest(f"Art. 60 al. 1 CO, versions {dates} : « {bad} »")
    assert 0 < len(editions) <= m._EDITION_MAX_FETCHES


def test_amendment_date_also_tries_the_day_before():
    cands = m._edition_as_of_candidates("révision au 1er janvier 2020", 0)
    assert cands[:2] == ["2020-01-01", "2019-12-31"]
    assert m._edition_as_of_candidates("Fassung vom 1.7.2019", 0)[:2] == ["2019-07-01", "2019-06-30"]
    assert m._edition_as_of_candidates("Stand 2017", 0) == ["2017-12-31", "2017-01-01"]
    assert m._edition_as_of_candidates("2999-01-01", 0) == []        # no future editions


def test_quote_language_picks_the_edition_language():
    assert m._quote_language(CO60_OLD) == "fr"
    assert m._quote_language(FINAL_IT) == "it"
    assert m._quote_language(FINAL_DE) == "de"
