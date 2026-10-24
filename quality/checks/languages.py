"""Language-attribution checks.

Every Swiss decision is in DE, FR, IT, or RM (Rumantsch). Anything
else is a scraper bug — the field was populated from the wrong
column or character-set decoded badly.

EN is the one exception, and only for Strasbourg: HUDOC publishes about
half the ECtHR corpus with English as the sole authoritative text (the
French listing row exists but carries no document — `isplaceholder`).
Those judgments are ingested as `en`. An `en` row on any *Swiss* court is
still the same scraper bug it always was, so the check is court-scoped
rather than simply widened.
"""
from __future__ import annotations

import sqlite3

from quality.types import CheckResult, Severity


VALID_LANGUAGES = {"de", "fr", "it", "rm"}
# Courts that reproduce Strasbourg text, the only ones allowed to carry `en`.
ECTHR_COURTS = {
    "ecthr_chamber", "ecthr_committee", "ecthr_grand_chamber",
    "hudoc_ch", "bge_egmr",
}
# UN treaty-body decisions and WGAD opinions: French where the UN has a French
# text, otherwise English (scrapers/untb.py) — the same rule as the ECtHR.
UNTB_COURTS = {
    "untb_ccpr",
    "untb_cat",
    "untb_cerd",
    "untb_cedaw",
    "untb_crc",
    "untb_crpd",
    "untb_cescr",
    "untb_ced",
    "unwgad",    # Working Group on Arbitrary Detention, same rule
}
NULL_LANGUAGE_FLOOR_PCT = 0.5  # WARNING if >0.5% of rows have no language


def check_unexpected_language_values(conn: sqlite3.Connection, **_) -> CheckResult:
    """Language must be in {de, fr, it, rm}, or `en` on an ECtHR or UN
    treaty-body court, or NULL."""
    # Two queries on purpose. The first stays on idx_decisions_language as a
    # covering scan; adding `court` to it would drop the index cover and make
    # the gate seek into a 62 GB table ~1M times, and the gate has already had
    # its wall-clock ceiling raised twice (600 -> 1800 -> 3600 s). The second
    # runs only for language values that are not unconditionally valid, which
    # in a healthy corpus means `en` alone.
    distribution = dict(conn.execute(
        "SELECT language, COUNT(*) FROM decisions "
        "WHERE language IS NOT NULL AND language != '' "
        "GROUP BY language"
    ).fetchall())
    suspect = [lang for lang in distribution if lang not in VALID_LANGUAGES]
    bad: list[tuple[str, str, int]] = []
    if suspect:
        placeholders = ",".join("?" * len(suspect))
        for lang, court, n in conn.execute(
            f"SELECT language, court, COUNT(*) FROM decisions "
            f"WHERE language IN ({placeholders}) GROUP BY language, court",
            suspect,
        ):
            if lang == "en" and (court in ECTHR_COURTS or court in UNTB_COURTS):
                continue
            bad.append((lang, court, n))
    return CheckResult(
        name="languages.unexpected_values",
        severity=Severity.CRITICAL,
        passed=(not bad),
        metric_value=sum(n for _, _, n in bad),
        threshold=0,
        message=f"{len(bad)} unexpected language/court combinations" if bad else
                "all language codes ∈ {de, fr, it, rm} (+ en on ECtHR / UN treaty-body courts)",
        sample_rows=[
            {"language": repr(l), "court": c, "count": n} for l, c, n in bad[:5]
        ],
        extra={"distribution": distribution},
        fix_advice="language must be a 2-letter ISO code, and `en` is reserved "
                   "for English ECtHR judgments and UN treaty-body decisions. Investigate the "
                   "scraper that wrote this row's language column.",
    )


def check_null_language_pct(conn: sqlite3.Connection, **_) -> CheckResult:
    """NULL language is acceptable when not detectable from source, but
    >0.5% of corpus means a scraper regression."""
    n_total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    n_null = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE language IS NULL OR language=''"
    ).fetchone()[0]
    pct = round(100 * n_null / n_total, 3) if n_total else 0
    return CheckResult(
        name="languages.null_pct",
        severity=Severity.WARNING,
        passed=(pct <= NULL_LANGUAGE_FLOOR_PCT),
        metric_value=pct,
        threshold=NULL_LANGUAGE_FLOOR_PCT,
        message=f"{n_null:,} rows ({pct:.2f}%) without language",
        extra={"null": n_null, "total": n_total},
    )


def check_language_distribution_plausible(conn: sqlite3.Connection, **_) -> CheckResult:
    """Swiss case-law has roughly equal DE+FR contributions (~46% / ~45%
    each at 2026-04-30, with IT ~8%, RM <0.01%). A nightly where any of
    DE / FR drops below 30% means a multi-court import failure (e.g.
    every German cantonal scraper crashed)."""
    rows = dict(conn.execute(
        "SELECT language, COUNT(*) FROM decisions "
        "WHERE language IN ('de','fr','it','rm') GROUP BY language"
    ).fetchall())
    n_total = sum(rows.values())
    if not n_total:
        return CheckResult(
            name="languages.distribution_plausible",
            severity=Severity.CRITICAL,
            passed=False,
            metric_value=0,
            threshold=None,
            message="zero rows with a language assigned — corpus broken",
        )
    de_pct = 100 * rows.get("de", 0) / n_total
    fr_pct = 100 * rows.get("fr", 0) / n_total
    it_pct = 100 * rows.get("it", 0) / n_total
    # Both DE and FR must each contribute ≥30% (catastrophic-import-loss
    # threshold). IT must be at least 3% (TI gerichte alone has ~58k).
    ok = de_pct >= 30 and fr_pct >= 30 and it_pct >= 3
    return CheckResult(
        name="languages.distribution_plausible",
        severity=Severity.CRITICAL,
        passed=ok,
        metric_value=de_pct,
        threshold=30.0,
        message=f"DE {de_pct:.1f}% / FR {fr_pct:.1f}% / "
                f"IT {it_pct:.1f}% / RM "
                f"{100*rows.get('rm',0)/n_total:.2f}%",
        extra=rows,
        fix_advice="DE and FR each need ≥30%, IT ≥3%; under-floor means a "
                   "language-block of scrapers crashed in publish.py Step 1",
    )
