"""Failed-anonymization check: personal data left in published decisions.

Courts anonymize before they publish; occasionally something is missed (an
AHV number in a letterhead, a party's mobile number in the facts, a home
address after "wohnhaft"). OpenCaseLaw mirrors the source, so such a miss is
republished here. This check finds candidates in NEWLY scraped decisions so
the originating court can be told (docs/governance-and-removal-policy.md).

Process (decided 2026-10-02): alert first, then a notification to the court
is drafted, then its answer is awaited. The check never removes, de-lists or
edits anything.

Drafted notifications never go to GitHub — neither this repository nor the
private report repository. This script writes findings only, and commits
exactly its own two files there (never `git add -A`), so a draft left in that
working tree cannot ride along. tests/test_notification_drafts_guard.py

Two tiers, calibrated 2026-10-02 on 11,265 recent decisions and then on the
full corpus (1,069,118 decisions):

  alert   clear errors only: a direct identifier of a private person the
          decision otherwise anonymizes. Read by a human the same day.
            ahv           AHV number with a valid EAN-13 check digit, or an
                          old 11-digit AHV/AVS number written with its label
            mobile        Swiss mobile number (075–079) in the body, named as
                          someone's phone, line or connection
            home_address  street + number + postcode directly after an
                          anonymized name, or after a domicile word with an
                          anonymized party just before it
            iban          checksum-valid CH IBAN named as a person's account
            email         freemail address whose local part is not masked
  review  plausible but not certain, weekly reading, never alerted: the same
          detectors without the context that makes them certain, and
            surname       "Herr/Frau/Monsieur/Madame + surname" in a decision
                          that otherwise uses anonymization placeholders
  Contact details of authorities, insurers, companies, counsel and charities
  are dropped, not reviewed. Calibrated 2026-10-05 by reading a random 60 of
  the full-corpus findings: AHV and mobile 100 %, address and IBAN about 90 %.

Deliberately NOT flagged (calibration noise): landlines and e-mails in
letterheads, institutional addresses (Rechtsmittelbelehrung, Mitteilung
lists), already-masked addresses ("A____@bluewin.ch"), and full birth dates —
98% of those are vd_gerichte next to an anonymized name, i.e. court practice.

Reads only the TAIL of each JSONL shard (rows whose scraped_at falls in the
window), never decisions.db, so the nightly build window does not bind it
(CLAUDE.md invariant 9).

Confidentiality: a list of decisions with leaked personal data is itself an
index of that data. Details go ONLY to the private repository, with the value
masked; the ntfy topic receives a bare count. Every Monday a report is written
regardless, so silence is distinguishable from breakage.

State: anonymization/.seen.json inside the private repo.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

SHARD_DIR = Path(os.environ.get("OCL_ANON_SHARD_DIR", "output/decisions"))
PRIVATE_REPO = Path(os.environ.get("OCL_ANOMALY_PRIVATE_REPO",
                                   "/opt/caselaw/confidential-reports"))
NTFY_URL = os.environ.get("OCL_ANOMALY_NTFY") or (
    os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    + "/"
    + os.environ.get("NTFY_TOPIC", "opencaselaw-publish")
)
DEPLOY_KEY = os.environ.get("OCL_ANOMALY_DEPLOY_KEY",
                            "/root/.ssh/anomaly_reports_ed25519")
# Daily run, 4-day window: three missed runs are still covered; the seen
# ledger keeps the overlap from re-reporting.
WINDOW_DAYS = int(os.environ.get("OCL_ANON_WINDOW_DAYS", "4"))
CHUNK = 4 * 1024 * 1024
TAIL_CAP = 96 * 1024 * 1024          # per shard; bounds page-cache pressure
SNIPPET_RADIUS = 160
ALERT_TIER = ("ahv", "mobile", "home_address", "iban", "email")

LABEL_DE = {
    "ahv": "AHV-Nummer (Prüfziffer gültig)",
    "mobile": "Mobiltelefonnummer",
    "home_address": "Wohnadresse",
    "iban": "IBAN",
    "email": "private E-Mail-Adresse",
    "surname": "ausgeschriebener Name neben Anonymisierungs-Platzhaltern",
}

# --- detectors ---------------------------------------------------------------
_AHV = re.compile(r"(?<!\d)756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}(?!\d)")
_IBAN = re.compile(r"\bCH\d{2}(?:\s?[A-Z0-9]{4}){4}\s?[A-Z0-9]\b")
# \w local part: a court's stand-in such as "Klägerin@gmail.com" must match whole.
_EMAIL = re.compile(r"[\w.%+\-]+@[A-Za-z0-9.\-]+\.[a-z]{2,}\b")
# 075–079; the unprefixed form needs separators ("0791234567" is as often a
# reference number as a phone).
_MOBILE = re.compile(
    r"(?<![\d.])(?:(?:\+41|0041)[\s\-]?\(?0?\)?[\s\-]?7[5-9][\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"|07[5-9][\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2})(?!\d)"
)
_ADDRESS = re.compile(
    r"\b(?:(?:[A-ZÄÖÜ][a-zäöüé]+[\s\-])?[A-ZÄÖÜ][a-zäöüé\-]+(?:strasse|gasse|weg|platz|rain|halde)|"
    r"(?:rue|avenue|chemin|route|via|viale|vicolo)\s+(?:d[eu]s?\s+|de\s+la\s+|de\s+l'|della\s+|del\s+|ai\s+)?"
    r"[A-ZÉ][\w\-éèàç]+)"
    r"\s+\d{1,3}[a-z]?,?\s+(?:CH-)?\d{4}\s+[A-ZÄÖÜ]"
)
_DOMICILE = re.compile(
    r"(wohnhaft|Wohnsitz|whft\.|domicili[ée]e?s?\b|domiciliat[oaie]\b|r[ée]sidant\b|"
    r"demeurant\b|chez\s+ses\s+parents|residente\b)", re.I)
_PLACEHOLDER_BEFORE = re.compile(r"[A-Z](?:\.[A-Z])?\.?_{2,}\s*,\s*$")
# Between the domicile word and the street: counsel or a public body means the
# address is theirs ("domicilié ______, représenté par Me X, avocate, rue …").
_NOT_A_HOME = re.compile(
    r"(avocate?s?\b|\bMe\s|Ma[îi]tre|Rechtsanw|Advokat|\bavv\.|vertreten|repr[ée]sent|patrocinat|"
    r"amt\b|Office|Service|sekretariat|Secr[ée]tariat|Procura|anwaltschaft|Minist[èe]re|Tutrice|"
    r"Tuteur|curatelles|tutelles|SCTP|autorit[ée]|beh[öo]rde|KESB|Pr[ée]sident|Presidente|palazzo|"
    r"gerichts?\b|Tribunal|Corte|kasse\b|caisse|cassa|assurance|versicherung|SUVA|\bCNA\b|"
    r"fondation|stiftung|fondazione|institution|D[ée]partement|Direktion|polizei|police|Gemeinde|"
    r"Commune|Comune|\bVille\b|\bStadt\b|Kanzlei|[ÉE]tude|\bAG\b|\bSA\b|GmbH|S[àa]rl|\bsise?\b|"
    r"c/o|IV-Stelle|\bOAI\b|Bundesamt|Kanton\b|[ÉE]tat\b|h[ôo]pital|spital|clinique|klinik|"
    r"banque|bank|d[ée]tenu|prison|Gef[äa]ngnis|Strafanstalt|[ée]tablissement)", re.I)
_PLACEHOLDER = re.compile(r"\b[A-Z]\.(?:_{2,}|\.{3,}|[A-Z]\.(?=[\s,;)]))")
# An anonymized person anywhere in a stretch of text ("A.________", "X._____",
# "A______", "[...]", "B.K.").
_ANON_NAME = re.compile(r"\b[A-Z]{1,2}(?:\.[A-Z])?\.?_{2,}|\b[A-Z]_{3,}|\[\.\.\.\]|\b[A-Z]\.[A-Z]?\.?(?=[\s,;)])")
# Old (pre-2008) AHV/AVS numbers have no check digit worth trusting; only the
# label makes them certain ("N° AVS 260.68.476.118", "AHV-Nr. 123.45.678.113").
_AHV_OLD = re.compile(r"(?:AHV|AVS|AVS/AI)(?:[\s\-]?(?:Nr\.?|n[°o]\.?|num[ée]ro))?\s*:?\s*"
                      r"(\d{3}\.\d{2}\.\d{3}\.\d{3})(?!\d)", re.I)
# Whose details: an office, an insurer, a company, counsel or a charity.
_NOT_A_PERSON = re.compile(
    r"(liquidation|vormals|z\.\s?h\.|zuhanden|kommando|kommandant|armee|direkt(?:or|ion)|"
    r"departement|département|verwaltung|administration|pouvoir judiciaire|consignation|"
    r"tr[ée]sor|staatskasse|gerichtskasse|beratungsstelle|hotline|zentrale|permanence|"
    r"fondation|stiftung|fondazione|caisse|kasse\b|cassa|libre passage|freiz[üu]gigkeit|"
    r"assurance|versicherung|association|verein|m[ée]decins|croix-rouge|caritas|"
    r"fournisseur|entreprise|firma|soci[ée]t[ée]|\bAG\b|\bSA\b|GmbH|S[àa]rl|"
    r"\bMe\s|ma[îi]tre|rechtsanw|avocat|advokat|f[üu]rsprech|notai?r|[ÉE]tude|kanzlei|"
    r"tribunal|gericht|office|amt\b|ufficio|police|polizei|commune|gemeinde|comune)", re.I)
# Words that make a mobile number someone's phone.
_PHONE_OF = re.compile(
    r"(portable|natel|handy|mobile|cellulare|raccordement|anschluss|collegamento|ctr\b|"
    r"t[ée]l[ée]phone|telefon|num[ée]ro|nummer|appel|anruf|sms|whatsapp|joignable|erreichbar|"
    r"iphone|samsung|nokia|huawei)", re.I)
# Words that make an IBAN someone's account.
_ACCOUNT_OF = re.compile(
    r"(au nom de|ouvert au nom|son compte|sur le compte|compte (?:postfinance|bancaire|"
    r"personnel|commun|de|du)|ihr(?:em)? konto|sein(?:em)? konto|konto (?:des|der|von|bei)|"
    r"lautend auf|conto (?:di|intestato)|troisi[èe]me pilier|3\. s[äa]ule)", re.I)
_PUBLIC_BODY = re.compile(
    r"(Kanton|Canton|Staat|État|Etat|Stato|Gericht|Tribunal|Finanzverwaltung|"
    r"Gerichtskasse|Staatskasse|Amt\b|Office|Ufficio|Gemeinde|Commune|Comune|Bund\b)", re.I)
_FREEMAIL = re.compile(
    r"@(gmail|googlemail|bluewin|gmx|hotmail|outlook|live|msn|yahoo|ymail|icloud|me|mac|"
    r"protonmail|proton|pm|sunrise|hispeed|swissonline|sunrisemail|web|aol|mail|vtx|"
    r"netplus|citycable|ticino)\.(ch|com|de|fr|it|net|me|at)$", re.I)
# Local parts a court writes INSTEAD of the real one.
_MASKED_LOCAL = re.compile(
    r"(_{2,}|\.{2,}|^x+$|^y+$|^xyz$|^abc$|^[a-z]\d?$|^[a-z]{1,2}\.$|vorname|nachname|name|pr[ée]nom|nom\b|"
    r"kl[äa]ger|beklagte|beschwerdef|gesuchsteller|recourant|intim[ée]|pr[ée]venu)", re.I)
_HONORIFIC = re.compile(
    r"\b(Herrn?|Frau|Monsieur|Madame|Mme|Signor[ae]?)\s+"
    r"((?:(?:von|van|de|del|di|da|du)\s+)?[A-ZÄÖÜÉ][a-zäöüéèàçñ]{2,}(?:-[A-ZÄÖÜÉ][a-zäöüéèàçñ]+)?)"
    r"(?![.\w_])"
)
_ROLE_BEFORE = re.compile(
    r"(Rechtsanw[aä]lt(?:in)?|Advokat(?:in)?|F[üu]rsprecher(?:in)?|Notar(?:in)?|"
    r"Richter(?:in)?|Gerichtsschreiber(?:in)?|Pr[äa]sident(?:in)?|Staatsanw[aä]lt(?:in)?|"
    r"Dr\.|Prof\.|lic\.\s?iur\.|avocate?|Ma[îi]tre|Me|juge|greffi[eè]re?|procureure?|"
    r"pr[ée]sidente?|avvocat[oa]|giudice|cancellier[ea]|experte?|Gutachter(?:in)?)[\s,]*$",
    re.I)
# Words that follow an honorific without being a surname.
_NOT_A_SURNAME = {
    "Monsieur", "Madame", "Herr", "Frau", "Veuve", "Dr", "Prof", "Docteur", "Professeur",
    "Rechtsanwalt", "Rechtsanwältin", "Bundesrichter", "Bundesrichterin", "Präsident",
    "Präsidentin", "Richter", "Richterin", "Kollege", "Kollegin", "Staatsanwalt",
    "Staatsanwältin", "Juge", "Président", "Présidente", "Avocat", "Bundesrat", "Bundesrätin",
    "Regierungsrat", "Regierungsrätin", "Gemeindepräsident", "Maître", "Notar", "Notaire",
    "Directeur", "Directrice", "Direktor", "Direktorin", "Conservateur", "Procureur",
    "Greffier", "Préfet", "Syndic", "Ministre",
}


def ahv_valid(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if len(d) != 13:
        return False
    return (10 - sum(x * (3 if i % 2 else 1) for i, x in enumerate(d[:12])) % 10) % 10 == d[12]


def iban_valid(s: str) -> bool:
    s = re.sub(r"\s", "", s)
    if len(s) != 21:
        return False
    try:
        return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1
    except ValueError:
        return False


def zone(pos: int, n: int) -> str:
    """head = letterhead/Rubrum, tail = signatures/Mitteilung, else body."""
    if pos < max(1500, int(0.06 * n)):
        return "head"
    if pos > n - 1200:
        return "tail"
    return "body"


def mask(value: str) -> str:
    """Keep the first three and last two characters; the rest becomes •."""
    idx = [i for i, c in enumerate(value) if c.isalnum()]
    keep = set(idx[:3] + idx[-2:])
    return "".join(c if (i in keep or not c.isalnum()) else "•" for i, c in enumerate(value))


def _snippet(text: str, a: int, b: int) -> str:
    lo, hi = max(0, a - SNIPPET_RADIUS), min(len(text), b + SNIPPET_RADIUS)
    return " ".join((text[lo:a] + mask(text[a:b]) + text[b:hi]).split())


def _whose(before: str) -> str:
    """The words that say whose address follows: from the sentence start (or 35
    characters) before the last anonymized name up to the address. The bench
    line of a Rubrum ("Gerichtsschreiber Feller. Parteien X.________, …") is
    not part of it, "Korpskommandant B.________," and "B.________ in
    Liquidation, vormals C.________," are."""
    names = list(_ANON_NAME.finditer(before))
    if not names:
        return before[-70:]
    lo = max(0, names[-1].start() - 35)
    stops = list(re.finditer(r"[a-zäöüéè]{3,}\.\s|:\s|\n", before[lo:names[-1].start()]))
    if stops:
        lo += stops[-1].end()
    return before[lo:]


def find_hits(text: str, judges: str = "") -> list[dict]:
    """All findings in one decision text: [{detector, tier, value, start, end}]."""
    n = len(text)
    out: list[dict] = []
    if n < 200:
        return out

    def add(det: str, m: re.Match, tier: str = "alert") -> None:
        out.append({"detector": det, "tier": tier, "value": m.group(),
                    "start": m.start(), "end": m.end()})

    for m in _AHV.finditer(text):
        if ahv_valid(m.group()):
            add("ahv", m)
    for m in _AHV_OLD.finditer(text):
        out.append({"detector": "ahv", "tier": "alert", "value": m.group(1),
                    "start": m.start(1), "end": m.end(1)})
    for m in _MOBILE.finditer(text):
        if zone(m.start(), n) != "body":
            continue
        before = text[max(0, m.start() - 80):m.start()]
        if _NOT_A_PERSON.search(before[-60:]):
            continue                                # a counselling line, an office's number
        around = before + text[m.end():m.end() + 60]
        add("mobile", m, tier="alert" if _PHONE_OF.search(around) else "review")
    for m in _ADDRESS.finditer(text):
        if "_" in m.group():
            continue                                # "rue A______ 12": the court masked the street
        before = text[max(0, m.start() - 150):m.start()]
        if _NOT_A_PERSON.search(_whose(before)):
            continue                                # counsel, an office, a company
        dom = None
        for dom in _DOMICILE.finditer(before[-90:]):
            pass                                    # the last domicile word wins
        if dom is not None:
            # 60 characters ahead of the domicile word say whose domicile it is.
            if not _NOT_A_HOME.search(before[max(0, len(before) - 90 + dom.start() - 60):]):
                # Certain only when the person living there is an anonymized one.
                add("home_address", m,
                    tier="alert" if _ANON_NAME.search(before[-120:]) else "review")
        elif _PLACEHOLDER_BEFORE.search(before) and not _NOT_A_HOME.search(before[-80:]):
            add("home_address", m)
    for m in _IBAN.finditer(text):
        ctx = text[max(0, m.start() - 250):m.end() + 250]
        if not iban_valid(m.group()) or _PUBLIC_BODY.search(ctx):
            continue
        before = text[max(0, m.start() - 170):m.start()]
        if _NOT_A_PERSON.search(before + text[m.end():m.end() + 40]):
            continue                                # an office's, a company's or counsel's account
        add("iban", m, tier="alert" if _ACCOUNT_OF.search(before[-120:]) else "review")
    for m in _EMAIL.finditer(text):
        local = m.group().split("@")[0]
        if (zone(m.start(), n) == "body" and _FREEMAIL.search(m.group())
                and not _MASKED_LOCAL.search(local)):
            before = text[max(0, m.start() - 80):m.start()]
            if _NOT_A_PERSON.search(before) or re.search(r"(?i)(e-?mail|courriel|fax)\s*:\s*$", before):
                continue                            # an office's address in a letterhead block
            add("email", m)

    if len(_PLACEHOLDER.findall(text)) >= 5:
        head_tail = text[:max(1500, int(0.06 * n))] + text[-1200:]
        seen: set[str] = set()
        for m in _HONORIFIC.finditer(text):
            name = m.group(2)
            if (name in seen or name in _NOT_A_SURNAME or zone(m.start(), n) != "body"
                    or name in judges or name in head_tail      # bench or counsel
                    or _ROLE_BEFORE.search(text[max(0, m.start() - 40):m.start()])):
                continue
            seen.add(name)
            # A name used throughout is a public actor, not a slip.
            if len(re.findall(r"\b" + re.escape(name) + r"\b", text)) <= 3:
                add("surname", m, tier="review")
    return out


# --- shard tail reader -------------------------------------------------------
def recent_rows(path: Path, cutoff: str):
    """Rows with scraped_at >= cutoff, read backwards from the end of the shard.

    Shards are append-only, so recent rows sit at the tail; reading stops at
    the first chunk whose rows all predate the window, or at TAIL_CAP.
    """
    end = os.path.getsize(path)
    read = 0
    carry = b""
    with open(path, "rb") as fh:
        while end > 0 and read < TAIL_CAP:
            start = max(0, end - CHUNK)
            fh.seek(start)
            lines = (fh.read(end - start) + carry).split(b"\n")
            read += end - start
            end = start
            carry = lines.pop(0) if start > 0 else b""
            parsed = hit = False
            for ln in reversed(lines):
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                parsed = True
                if (row.get("scraped_at") or "") >= cutoff:
                    hit = True
                    yield row
            # A chunk holding no complete row (one row larger than CHUNK)
            # says nothing about the window: keep reading.
            if parsed and not hit:
                break


def scan(shard_dir: Path, window_days: int, raw: bool = False) -> dict:
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=window_days)).isoformat()
    hits: list[dict] = []
    scanned = 0
    errors: list[str] = []
    for f in sorted(glob.glob(str(shard_dir / "*.jsonl"))):
        try:
            for row in recent_rows(Path(f), cutoff):
                scanned += 1
                text = row.get("full_text") or ""
                judges = " ".join(str(row.get(k) or "") for k in ("judges", "clerks"))
                for h in find_hits(text, judges):
                    did = row.get("decision_id") or "?"
                    digest = hashlib.sha1(h["value"].encode()).hexdigest()[:12]
                    rec = {
                        "key": f"{did}|{h['detector']}|{digest}",
                        "decision_id": did,
                        "court": row.get("court") or "unbekannt",
                        "docket": row.get("docket_number") or did,
                        "date": row.get("decision_date"),
                        "source_url": row.get("pdf_url") or row.get("source_url"),
                        "detector": h["detector"],
                        "tier": h["tier"],
                        "zone": zone(h["start"], len(text)),
                        "masked": mask(h["value"]),
                        "snippet": _snippet(text, h["start"], h["end"]),
                    }
                    if raw:
                        rec["value"] = h["value"]
                    hits.append(rec)
        except OSError as e:
            errors.append(f"{os.path.basename(f)}: {e!r}")
    # One finding per (decision, detector, value): a number repeated in the
    # text is one leak.
    uniq = {h["key"]: h for h in reversed(hits)}
    return {"cutoff": cutoff, "scanned": scanned, "errors": errors,
            "hits": sorted(uniq.values(), key=lambda h: (h["tier"], h["court"], h["key"]))}


# --- report ------------------------------------------------------------------
def render(new_hits: list[dict], scanned: int, today: str) -> str:
    alerts = [h for h in new_hits if h["tier"] == "alert"]
    review = [h for h in new_hits if h["tier"] == "review"]
    lines = [f"# Anonymisierungsprüfung — Meldung vom {today}", "",
             f"Geprüft: {scanned} neu erfasste Entscheide.", ""]
    if not new_hits:
        lines += ["Keine neuen Befunde seit der letzten Meldung.", ""]
        return "\n".join(lines)

    def block(title: str, hits: list[dict]) -> None:
        if not hits:
            return
        lines.extend([f"## {title} ({len(hits)})", ""])
        by_court: dict[str, list[dict]] = {}
        for h in hits:
            by_court.setdefault(h["court"], []).append(h)
        for court in sorted(by_court):
            lines.append(f"### {court}")
            for h in by_court[court]:
                lines.append(f"- **{h['docket']}** vom {h.get('date') or '?'} — "
                             f"{LABEL_DE[h['detector']]}: `{h['masked']}`  ")
                lines.append("  Entscheid: https://mcp.opencaselaw.ch/entscheid/"
                             + quote(h["decision_id"], safe="") + "  ")
                if h.get("source_url"):
                    lines.append(f"  Quelle: {h['source_url']}  ")
                lines.append(f"  Kontext: «…{h['snippet']}…»")
                lines.append("")

    block("Zu melden", alerts)
    block("Zur Durchsicht", review)
    lines += ["---",
              "Vorgehen: Befund an der Quelle nachprüfen (steht die Angabe auch in der "
              "Publikation des Gerichts?), dann Meldung an das Gericht entwerfen und dessen "
              "Antwort abwarten. Diese Prüfung entfernt oder ändert nichts. Die Werte sind "
              "hier maskiert; der Kontext erlaubt das Auffinden der Stelle.", ""]
    return "\n".join(lines)


def _git(args: list[str]) -> None:
    env = {**os.environ,
           "GIT_SSH_COMMAND": f"ssh -i {DEPLOY_KEY} -o StrictHostKeyChecking=accept-new"}
    subprocess.run(["git", "-C", str(PRIVATE_REPO), *args],
                   check=True, env=env, capture_output=True, text=True)


def _ntfy(n: int) -> None:
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=(f"{n} neue(r) Anonymisierungsbefund(e) in neu erfassten Entscheiden — "
                  "Details im privaten Report-Repository.").encode(),
            headers={"Title": "OpenCaseLaw Anonymisierungspruefung"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"ntfy ping failed (non-fatal): {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--shard-dir", type=Path, default=SHARD_DIR)
    ap.add_argument("--scan-only", action="store_true",
                    help="print the findings as JSON and stop: no report, no push, no alert")
    ap.add_argument("--raw", action="store_true",
                    help="with --scan-only: include the unmasked value (source re-check)")
    args = ap.parse_args(argv)

    result = scan(args.shard_dir, args.window_days, raw=args.scan_only and args.raw)
    for e in result["errors"]:
        print(f"shard unreadable: {e}", file=sys.stderr)
    if args.scan_only:
        json.dump(result, sys.stdout, ensure_ascii=False)
        return 0

    today = datetime.date.today().isoformat()
    is_monday = datetime.date.today().weekday() == 0
    if not PRIVATE_REPO.exists():
        print(f"private repo missing: {PRIVATE_REPO}", file=sys.stderr)
        return 1
    try:
        _git(["pull", "--rebase", "-q"])
    except subprocess.CalledProcessError:
        pass                    # fresh/empty repo: no upstream branch yet

    seen_path = PRIVATE_REPO / "anonymization" / ".seen.json"
    seen: dict = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    new_hits = [h for h in result["hits"] if h["key"] not in seen]
    if not new_hits and not is_monday:
        print(f"scanned {result['scanned']}; no new findings; not Monday — nothing to send")
        return 0

    out = PRIVATE_REPO / "anonymization" / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(new_hits, result["scanned"], today))
    for h in new_hits:
        seen[h["key"]] = {"first_reported": today, "detector": h["detector"],
                          "court": h["court"]}
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1))

    alerts = sum(1 for h in new_hits if h["tier"] == "alert")
    # Own files only: anything else in the private tree stays uncommitted.
    own = [str(out.relative_to(PRIVATE_REPO)), str(seen_path.relative_to(PRIVATE_REPO))]
    _git(["add", "--", *own])
    _git(["-c", "user.name=OpenCaseLaw Anomaly Reporter",
          "-c", "user.email=noreply@opencaselaw.ch",
          "commit", "-q", "-m",
          f"Anonymisierung {today}: {alerts} zu melden, {len(new_hits) - alerts} zur Durchsicht",
          "--", *own])
    _git(["push", "-q", "-u", "origin", "HEAD"])
    print(f"report {out.name}: scanned {result['scanned']}, {alerts} alert(s), "
          f"{len(new_hits) - alerts} for review, pushed")
    if alerts:
        _ntfy(alerts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
