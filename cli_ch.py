"""
cli:ch — Swiss-native Caselaw Identifier.

A Swiss-native canonical identifier for court decisions. The five
axes are: court / chamber / docket / version / lang+pinpoint.

The design captures five features of Swiss jurisprudence that the
EU's ECLI flattens:

  1. Trilingualism. DE/FR/IT versions of a BGE are legally equal
     authentic texts (Art. 14 PublG). cli:ch treats language as a
     first-class FRBR-Expression dimension via `?lang=<l>`.
  2. Cantonal sovereignty. 26 distinct judicial systems remain
     visible in the identifier (`cli:ch:zh:…`, `cli:ch:ti:…`)
     instead of collapsing into an opaque court code.
  3. Pinpoint addressing. Erwägung-level citation as a first-class
     fragment (`#e-2.3`) matches the Schweizer Citation convention.
  4. Chamber granularity. The cantonal chamber (Obergericht,
     Verwaltungsgericht, …) is explicit, not opaque.
  5. Versioning. `@YYYY-MM-DD` addresses a specific anonymization
     date when a decision is republished — rare but legally
     meaningful when it happens.

Compact form (canonical for citation):

    cli:ch:<court>[:<chamber>]:<docket>[@<version>][?<query>][#<pinpoint>]

Examples:

    cli:ch:bge:140-III-86
    cli:ch:bge:140-III-86?lang=fr
    cli:ch:bge:140-III-86#e-2.3
    cli:ch:bger:6B_1234/2025
    cli:ch:bvger:A-1234/2024
    cli:ch:zh:obergericht:LB230012
    cli:ch:ti:tribunale-appello:34.2025.27
    cli:ch:finma:2024-12345

Relationship to ECLI:

    cli_ch_to_ecli("cli:ch:bge:140-III-86", date(2014, 1, 15))
        → "ECLI:CH:BGE:2014:140.III.86"

The projection is many-to-one: language, pinpoint, version, and
some chamber detail are not preserved by ECLI. cli:ch is strictly
more information-rich; ECLI is the lossy export format used when a
European resolver needs to address the same decision.

This identifier scheme is a proposed open standard published at
docs/standards/. The design is Swiss-native by intent: it captures
features that matter for Swiss legal practice — trilingual
authentic texts, cantonal sovereignty, Erwägung-level pinpoint
addressing — as first-class identifier axes, while ECLI remains
available as the European-interop projection.

Pure function over the existing schema; no DB migration required.
"""
from __future__ import annotations

from typing import Optional

from ecli import mint_ecli


# Federal courts and federal agencies — no canton prefix.
# Internal court code → cli:ch court segment.
_FEDERAL_COURTS: dict[str, str] = {
    # Federal — top tier
    "bger": "bger",
    "bge": "bge",
    "bge_egmr": "bge",         # BGE-collection EGMR refs share BGE namespace
    "bge_historical": "bge",
    "bvger": "bvger",
    "bstger": "bstger",
    "bpatger": "bpatger",
    # Federal — administrative bodies
    "ch_bundesrat": "bundesrat",
    "mkg": "mkg",
    "bazg": "bazg",
    # Federal — regulators
    "finma": "finma",
    "finma_versicherungsrecht": "finma-versicherungsrecht",
    "weko": "weko",
    "edoeb": "edoeb",
    "ubi": "ubi",
    "elcom": "elcom",
    "postcom": "postcom",
    "comcom": "comcom",
    # Legacy
    "emark": "emark",          # Eidg. Migrationskommission
}

# Non-Swiss courts in our corpus. These keep their native identifier
# (e.g. ECHR application number); we do not claim a cli:ch for them.
_NON_SWISS_COURTS = frozenset({
    "ecthr",          # European Court of Human Rights
    "hudoc_ch",       # ECHR Swiss-related (HUDOC mirror)
    "ta_sst",         # Schweizer Sportgericht (Swiss Sports Tribunal, Bern)
})

# ISO 3166-2:CH canton codes.
_CANTON_CODES = frozenset({
    "zh", "be", "lu", "ur", "sz", "ow", "nw", "gl", "zg",
    "fr", "so", "bs", "bl", "sh", "ar", "ai", "sg", "gr",
    "ag", "tg", "ti", "vd", "vs", "ne", "ge", "ju",
})

# Languages cli:ch recognises in `?lang=` (Swiss official languages
# plus the two languages of Bundesgericht decisions).
_LANGS = frozenset({"de", "fr", "it", "rm", "en"})


def _normalize_docket(docket: str) -> str:
    """Normalise a docket for use in a cli:ch identifier.

    cli:ch preserves the docket more faithfully than ECLI:
      - '/' is retained (it's part of the canonical BGer citation)
      - whitespace runs are collapsed to '.'
      - leading/trailing whitespace stripped
    """
    s = " ".join(docket.split())
    s = s.replace(" ", ".")
    return s


def _bge_docket_compact(docket_number: str) -> Optional[str]:
    """Convert 'BGE 140 III 86' OR '140 III 86' → '140-III-86'.

    The corpus stores BGE citations both with and without the collection
    prefix; both must yield the SAME canonical hyphenated form (else the same
    kind of citation renders inconsistently, e.g. '152.II.1' vs '131-III-12').
    Returns None if `docket_number` is not in canonical BGE form.
    """
    parts = docket_number.strip().split()
    if parts and parts[0].upper() in ("BGE", "ATF", "DTF"):
        parts = parts[1:]                      # drop an optional collection prefix
    if len(parts) != 3:
        return None
    vol, div, page = parts[0], parts[1].upper(), parts[2]
    if not vol.isdigit() or not page.isdigit():
        return None
    # Roman numeral check — BGE has divisions I, II, III, IV, V, VI, Ia, …
    return f"{vol}-{div}-{page}"


def mint_cli_ch(
    decision_id: str,
    court: str,
    docket_number: Optional[str] = None,
    language: Optional[str] = None,
    pinpoint: Optional[str] = None,
    version: Optional[str] = None,
) -> Optional[str]:
    """Mint a cli:ch Swiss-native caselaw identifier.

    Returns the cli:ch string, or None if:
      - the decision is from a non-Swiss court (use the native id)
      - the decision_id and docket are both empty

    Language, pinpoint, and version are optional; when omitted the
    identifier addresses the Work as a whole. When present:

      ?lang=<l>     names a specific authentic-text Expression
      #<pinpoint>   addresses an Erwägung or other intra-decision
                    location (e.g. 'e-2.3' for 'Erwägung 2.3')
      @<version>    addresses an anonymization-date version
    """
    if not court or not decision_id:
        return None

    court_lc = court.lower()
    if court_lc in _NON_SWISS_COURTS:
        return None

    # --- Federal courts and agencies ---
    if court_lc in _FEDERAL_COURTS:
        court_segment = _FEDERAL_COURTS[court_lc]

        # BGE: try the canonical 'BGE <vol> <div> <page>' form first.
        if court_segment == "bge" and docket_number:
            compact = _bge_docket_compact(docket_number)
            if compact:
                return _assemble(court_segment, None, compact, language, pinpoint, version)
            # Fall through to generic normalisation otherwise.

        docket = _docket_or_fallback(decision_id, court_lc, docket_number)
        if not docket:
            return None
        return _assemble(court_segment, None, docket, language, pinpoint, version)

    # --- Cantonal: <canton>_<chamber> form ---
    if "_" in court_lc:
        canton, rest = court_lc.split("_", 1)
        if canton in _CANTON_CODES:
            chamber = rest.replace("_", "-")
            docket = _docket_or_fallback(decision_id, court_lc, docket_number)
            if not docket:
                return None
            return _assemble(canton, chamber, docket, language, pinpoint, version)

    # --- Unknown / unprefixed cantonal court ---
    if not docket_number:
        return None
    return _assemble(court_lc, None, _normalize_docket(docket_number),
                     language, pinpoint, version)


def _docket_or_fallback(decision_id: str, court_lc: str,
                        docket_number: Optional[str]) -> str:
    """Return a normalised docket, falling back to the decision-id tail."""
    if docket_number:
        return _normalize_docket(docket_number)
    tail = decision_id
    if tail.lower().startswith(court_lc + "_"):
        tail = tail[len(court_lc) + 1:]
    return _normalize_docket(tail)


def _assemble(court: str, chamber: Optional[str], docket: str,
              language: Optional[str], pinpoint: Optional[str],
              version: Optional[str]) -> str:
    """Assemble the cli:ch compact form from validated components."""
    if chamber:
        cli = f"cli:ch:{court}:{chamber}:{docket}"
    else:
        cli = f"cli:ch:{court}:{docket}"
    if version:
        cli += f"@{version}"
    if language and language.lower() in _LANGS:
        cli += f"?lang={language.lower()}"
    if pinpoint:
        cli += f"#{pinpoint}"
    return cli


def mint_cli_ch_from_row(row) -> Optional[str]:
    """Convenience: mint a cli:ch from a sqlite3.Row or dict."""
    if hasattr(row, "keys"):
        def get(k):
            return row[k] if k in row.keys() else None
    else:
        def get(k):
            return row.get(k)
    return mint_cli_ch(
        decision_id=get("decision_id"),
        court=get("court"),
        docket_number=get("docket_number"),
    )


def parse_cli_ch(s: str) -> Optional[dict]:
    """Parse a cli:ch identifier into structured components.

    Returns dict with keys: court, chamber (or None), docket,
    version (or None), pinpoint (or None), lang (or None).
    Returns None if not a valid cli:ch identifier.
    """
    if not s or not s.startswith("cli:ch:"):
        return None
    rest = s[len("cli:ch:"):]
    if not rest:
        return None

    # Strip off pinpoint, query, version from the tail.
    pinpoint = None
    if "#" in rest:
        rest, pinpoint = rest.split("#", 1)
    query_str = None
    if "?" in rest:
        rest, query_str = rest.split("?", 1)
    version = None
    if "@" in rest:
        rest, version = rest.split("@", 1)

    # Remaining: court[:chamber]:docket  (split by ':' — Swiss dockets
    # don't contain ':' in any known scheme.)
    segs = rest.split(":")
    if len(segs) == 2:
        court, docket = segs
        chamber = None
    elif len(segs) == 3:
        court, chamber, docket = segs
        # Cantonal form: first segment must be a canton code, otherwise
        # the identifier is malformed.
        if court not in _CANTON_CODES:
            return None
    else:
        return None
    if not court or not docket:
        return None

    lang = None
    if query_str:
        for kv in query_str.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k.strip() == "lang":
                    lang = v.strip().lower()

    return {
        "court": court,
        "chamber": chamber,
        "docket": docket,
        "version": version,
        "pinpoint": pinpoint,
        "lang": lang,
    }


def cli_ch_to_ecli(cli_ch: str, decision_date=None) -> Optional[str]:
    """Project a cli:ch identifier to its ECLI form.

    The projection is many-to-one — language, pinpoint, version, and
    cantonal-chamber granularity are not preserved by ECLI.

    decision_date is used to derive the year component of ECLI when
    the docket does not embed a year (e.g. for BGE, where year is
    derived from volume number, or cantonal dockets without /YYYY).
    """
    parts = parse_cli_ch(cli_ch)
    if not parts:
        return None

    court = parts["court"]
    chamber = parts["chamber"]
    docket = parts["docket"]

    # Reconstruct an internal court code that ecli.mint_ecli understands.
    if chamber:
        # Cantonal: canton + chamber-as-internal-name
        ecli_court_code = f"{court}_{chamber.replace('-', '_')}"
    else:
        ecli_court_code = court

    # Reverse the BGE compact form so mint_ecli's BGE-specific branch
    # recognises it.
    if court == "bge":
        bits = docket.split("-")
        if len(bits) == 3 and bits[0].isdigit() and bits[2].isdigit():
            docket = f"BGE {bits[0]} {bits[1]} {bits[2]}"

    return mint_ecli(
        decision_id=parts["docket"],
        court=ecli_court_code,
        docket_number=docket,
        decision_date=decision_date,
    )


def cli_ch_to_decision_id(cli_ch: str) -> Optional[str]:
    """Reconstruct the internal decision_id that a cli:ch identifier
    refers to.

    Approximate inverse of ``mint_cli_ch``. The caller should verify the
    returned id exists in the corpus — this function reconstructs the
    *expected* id without consulting the DB.

    Returns None if the cli:ch is malformed or refers to a non-Swiss
    body (since non-Swiss courts don't get cli:ch ids).
    """
    parts = parse_cli_ch(cli_ch)
    if not parts:
        return None
    court = parts["court"]
    chamber = parts["chamber"]
    docket = parts["docket"]

    # BGE special form: cli:ch:bge:140-III-86 → bge_BGE_140_III_86
    if court == "bge" and not chamber:
        m = docket.split("-")
        if len(m) == 3 and m[0].isdigit() and m[2].isdigit():
            return f"bge_BGE_{m[0]}_{m[1]}_{m[2]}"
        # Fall through to generic handling.

    # Federal courts and regulators: <court>_<docket-with-slash-as-underscore>
    if not chamber:
        # Federal cli:ch court segment → internal court key (mostly identity).
        # finma-versicherungsrecht / bundesrat are renamed in mint_cli_ch.
        internal_court = {
            "bundesrat": "ch_bundesrat",
            "finma-versicherungsrecht": "finma_versicherungsrecht",
        }.get(court, court)
        docket_internal = docket.replace("/", "_")
        return f"{internal_court}_{docket_internal}"

    # Cantonal: cli:ch:<canton>:<chamber>:<docket> → <canton>_<chamber_with_-_as_>_<docket>
    chamber_internal = chamber.replace("-", "_")
    docket_internal = docket.replace("/", "_")
    return f"{court}_{chamber_internal}_{docket_internal}"


def cli_ch_to_url(cli_ch: str,
                  base: str = "https://opencaselaw.ch") -> Optional[str]:
    """Project a cli:ch identifier to its HTTP-resolvable URL form.

    Note: actually serving these URLs requires a resolver at the
    given base URL. The function is useful regardless for documentation
    and for embedding clickable identifiers in pages.
    """
    parts = parse_cli_ch(cli_ch)
    if not parts:
        return None

    segs = ["cli", "ch", parts["court"]]
    if parts["chamber"]:
        segs.append(parts["chamber"])
    # Percent-encode '/' in docket so it doesn't act as a path separator.
    docket = parts["docket"].replace("/", "%2F")
    segs.append(docket)

    url = base.rstrip("/") + "/" + "/".join(segs)
    if parts["version"]:
        url += f"@{parts['version']}"
    if parts["lang"]:
        url += f"?lang={parts['lang']}"
    if parts["pinpoint"]:
        url += f"#{parts['pinpoint']}"
    return url


if __name__ == "__main__":
    from datetime import date

    print("=== mint_cli_ch ===")
    cases = [
        ("bge_BGE_140_III_86", "bge", "BGE 140 III 86",
         "cli:ch:bge:140-III-86"),
        ("bger_6B_1234_2025", "bger", "6B_1234/2025",
         "cli:ch:bger:6B_1234/2025"),
        ("bvger_A-1234_2024", "bvger", "A-1234/2024",
         "cli:ch:bvger:A-1234/2024"),
        ("bstger_SK.2024.5", "bstger", "SK.2024.5",
         "cli:ch:bstger:SK.2024.5"),
        ("bpatger_O2024_001", "bpatger", "O2024_001",
         "cli:ch:bpatger:O2024_001"),
        ("zh_obergericht_LB230012", "zh_obergericht", "LB230012",
         "cli:ch:zh:obergericht:LB230012"),
        ("ti_tribunale_appello_x", "ti_tribunale_appello", "34.2025.27",
         "cli:ch:ti:tribunale-appello:34.2025.27"),
        ("be_obergericht_x", "be_obergericht", "ZS.2024.50",
         "cli:ch:be:obergericht:ZS.2024.50"),
        ("finma_x", "finma", "2024-12345",
         "cli:ch:finma:2024-12345"),
        ("weko_x", "weko", "32-0432",
         "cli:ch:weko:32-0432"),
        # Non-Swiss → None
        ("ecthr_36417_16", "ecthr", "36417/16", None),
        ("hudoc_ch_x", "hudoc_ch", "12345/20", None),
    ]
    for did, court, docket, expected in cases:
        result = mint_cli_ch(did, court, docket)
        ok = "OK" if result == expected else "FAIL"
        print(f"  [{ok}] {court:30s} {str(docket):20s} → {result}")
        if result != expected:
            print(f"         expected: {expected}")

    print("\n=== mint_cli_ch with lang / pinpoint / version ===")
    examples = [
        mint_cli_ch("bge_BGE_140_III_86", "bge", "BGE 140 III 86",
                    language="fr"),
        mint_cli_ch("bge_BGE_140_III_86", "bge", "BGE 140 III 86",
                    pinpoint="e-2.3"),
        mint_cli_ch("bger_6B_1234_2025", "bger", "6B_1234/2025",
                    version="2025-03-15"),
        mint_cli_ch("bge_BGE_140_III_86", "bge", "BGE 140 III 86",
                    language="fr", pinpoint="e-2.3"),
    ]
    for ex in examples:
        print(f"  {ex}")

    print("\n=== parse_cli_ch ===")
    for cli in [
        "cli:ch:bge:140-III-86",
        "cli:ch:bge:140-III-86?lang=fr",
        "cli:ch:bger:6B_1234/2025#e-2.3",
        "cli:ch:zh:obergericht:LB230012",
        "cli:ch:bger:6B_1234/2025@2025-03-15",
        "cli:ch:bge:140-III-86?lang=fr#e-2.3",
        "not a cli ch",
        "cli:ch:zh:nonexistent:foo:bar",  # 4 segments → invalid
    ]:
        print(f"  {cli!r}")
        print(f"    → {parse_cli_ch(cli)}")

    print("\n=== cli_ch_to_ecli (projection) ===")
    projs = [
        ("cli:ch:bge:140-III-86", date(2014, 1, 15)),
        ("cli:ch:bger:6B_1234/2025", None),  # year derivable from docket
        ("cli:ch:bvger:A-1234/2024", None),
        ("cli:ch:zh:obergericht:LB230012", date(2023, 6, 1)),
        ("cli:ch:ti:tribunale-appello:34.2025.27", date(2025, 4, 1)),
        ("cli:ch:bge:140-III-86?lang=fr#e-2.3", date(2014, 1, 15)),  # lang/pinpoint lost
    ]
    for cli, d in projs:
        print(f"  {cli}  (date={d})")
        print(f"    → {cli_ch_to_ecli(cli, d)}")

    print("\n=== cli_ch_to_url ===")
    for cli in [
        "cli:ch:bge:140-III-86",
        "cli:ch:bger:6B_1234/2025",
        "cli:ch:zh:obergericht:LB230012",
        "cli:ch:bger:6B_1234/2025#e-2.3",
        "cli:ch:bge:140-III-86?lang=fr",
    ]:
        print(f"  {cli}\n    → {cli_ch_to_url(cli)}")
