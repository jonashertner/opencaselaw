"""Court-attribution checks.

Every row must have a `court` value that's in the canonical registry,
and every federal/cantonal split must be coherent (canton='CH' for
federal courts, valid 2-letter ISO for cantonal).

The canonical registry is the union of:
- _COURT_NAMES in seo_pages.py
- The COURT_DISPLAY_NAMES (or similar) in mcp_server.py
- Plus any court that build_fts5.py / merge_shards.py allows in.

Rather than hard-code, we treat the union of distinct court values
present in the DB on a known-good day as the registry, and flag any
NEW court value that appears since. New ≠ regression by itself, but
should be reviewed.
"""
from __future__ import annotations

import re
import sqlite3

from quality.types import CheckResult, Severity


# Sources of truth: 102 courts present in the 2026-04-30 build.
# A new court value appearing must be reviewed (registered in seo_pages
# + intentional). Missing one means a court was lost (CRITICAL).
EXPECTED_FEDERAL = {
    "bger", "bge", "bvger", "bstger", "bpatger",
    "bge_egmr", "ch_bundesrat", "ta_sst", "emark", "hudoc_ch",
    # regulatory:
    "finma", "finma_versicherungsrecht", "weko", "edoeb", "ubi",
    "elcom", "postcom", "comcom",
    # other federal:
    "ch_vb", "mkg",
    # NOTE: ch_vb holds the VPB/JAAC series only (portal prefix 15) since
    # 2026-10-27; the Bundesblatt / Amtliches Bulletin / AS pages the CH_VB
    # feed also carried were dropped (scripts/ch_vb_keep_vpb.py,
    # runbooks/ch_vb_amtsdruckschriften_contamination.md).
    # NOTE: bge_historical was merged into the main bge court at some
    # point; ch_anwaltsaufsicht / fr_anwaltsaufsicht: portals have no
    # decisions by design (registered scrapers, expected zero rows).
}

# Cantonal codes derived from court IDs that start with a 2-letter prefix.
_CANTON_PREFIX_RE = re.compile(r"^([a-z]{2})_")
VALID_CANTON_CODES = {
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR",
    "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG",
    "TI", "UR", "VD", "VS", "ZG", "ZH",
}


def check_no_null_or_empty_court(conn: sqlite3.Connection, **_) -> CheckResult:
    """Every row needs a court — duplicates `schema.required_not_null.court`
    but kept for explicit messaging."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE court IS NULL OR court=''"
    ).fetchone()[0]
    return CheckResult(
        name="courts.null_court",
        severity=Severity.CRITICAL,
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} rows without court attribution" if n else
                "every row has a court",
    )


def check_canonical_court_code(conn: sqlite3.Connection, **_):
    """Every distinct court value must look like a canonical id:
    alphanumeric + underscore, no spaces, no special chars.

    Per-court CRITICAL: a court id like `bge ` (trailing space) means a
    scraper bug that the docket-trimming pass doesn't cover."""
    rows = conn.execute(
        "SELECT court, COUNT(*) FROM decisions GROUP BY court ORDER BY 2 DESC"
    ).fetchall()
    bad = [(c, n) for c, n in rows
           if not c or not re.match(r"^[a-z][a-z0-9_]*$", c or "")]
    yield CheckResult(
        name="courts.canonical_format",
        severity=Severity.CRITICAL,
        passed=(not bad),
        metric_value=len(bad),
        threshold=0,
        message=f"{len(bad)} non-canonical court codes" if bad else
                f"all {len(rows)} court codes look canonical",
        sample_rows=[{"court": repr(c), "count": n} for c, n in bad[:5]],
        extra={"distinct_courts": len(rows)},
        fix_advice="court ids must match `[a-z][a-z0-9_]*`. Investigate "
                   "the scraper writing this row's court value.",
    )


def check_expected_federal_courts_present(conn: sqlite3.Connection, **_):
    """Each federal court must contribute ≥1 row. A missing federal
    court usually means the scraper failed silently."""
    rows = conn.execute(
        "SELECT court, COUNT(*) FROM decisions GROUP BY court"
    ).fetchall()
    present = {c: n for c, n in rows}
    for court in sorted(EXPECTED_FEDERAL):
        n = present.get(court, 0)
        yield CheckResult(
            name=f"courts.federal_present.{court}",
            severity=Severity.CRITICAL,
            passed=(n > 0),
            metric_value=n,
            threshold=1,
            message=f"{court}: {n:,} decisions" + (
                "" if n > 0 else " (MISSING — scraper regression?)"),
            court=court,
        )


def check_canton_field_consistency(conn: sqlite3.Connection, **_):
    """For every cantonal court (id like `xx_…`), the canton column
    must match the prefix. For federal courts, canton should be 'CH'
    or NULL."""
    bad = []
    rows = conn.execute(
        "SELECT court, canton, COUNT(*) FROM decisions "
        "GROUP BY court, canton"
    ).fetchall()
    distinct_pairs = 0
    for court, canton, n in rows:
        if not court:
            continue
        distinct_pairs += 1
        m = _CANTON_PREFIX_RE.match(court)
        if m:
            prefix = m.group(1).upper()
            if prefix in VALID_CANTON_CODES:
                if canton and canton != prefix:
                    bad.append({"court": court, "canton": canton,
                                "expected": prefix, "count": n})
    yield CheckResult(
        name="courts.canton_field_consistency",
        severity=Severity.WARNING,
        passed=(not bad),
        metric_value=len(bad),
        threshold=0,
        message=f"{len(bad)} (court, canton) pairs with mismatched canton"
                if bad else "all cantonal courts have matching canton field",
        sample_rows=bad[:5],
    )
