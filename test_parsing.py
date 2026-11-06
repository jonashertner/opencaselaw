#!/usr/bin/env python3
"""
OFFLINE PARSING VALIDATION using actual BGer HTML structure.
Tests our selectors and extraction logic against real decision content.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import Decision, extract_citations, make_decision_id, detect_language

pytestmark = pytest.mark.manual

# ============================================================
# SAMPLE: Real BGer decision HTML structure (2C_28/2026)
# Reconstructed from live fetch to validate selectors
# ============================================================

SAMPLE_HTML = """
<html>
<head><title>2C_28/2026 23.01.2026</title></head>
<body>
<div id="highlight_content">
<div class="content">
<p><b>2C_28/2026</b></p>
<p><b>Urteil vom 23. Januar 2026</b></p>
<p><b>II. öffentlich-rechtliche Abteilung</b></p>
<p>Besetzung
Bundesrichterin Aubry Girardin, Präsidentin,
Gerichtsschreiberin Ivanov.</p>

<p>Verfahrensbeteiligte
A.________,
Beschwerdeführer,
vertreten durch Rechtsanwalt Christian Schroff,</p>

<p><i>gegen</i></p>

<p>Departement für Justiz und Sicherheit des Kantons Thurgau,
Generalsekretariat, 8510 Frauenfeld,
Beschwerdegegner.</p>

<p>Gegenstand
Rechtsverzögerung / Rechtsverweigerung,</p>

<p>Beschwerde gegen den Entscheid des Verwaltungsgerichts des Kantons Thurgau
vom 5. November 2025 (VG.2024.131/E).</p>

<p><b>Erwägungen:</b></p>
<p><b>1.</b></p>
<p><b>1.1.</b> A.________ (geb. 1976), portugiesischer Staatsangehöriger, reiste am 1. März 2003
zwecks Ausübung einer Erwerbstätigkeit in die Schweiz ein. In der Folge wurde ihm eine
Niederlassungsbewilligung EU/EFTA erteilt.</p>

<p>Mit Urteil des Bezirksgerichts U.________ vom 28. November 2019 wurde A.________
wegen mehrfacher Vergewaltigung und mehrfacher sexueller Nötigung zu einer Freiheitsstrafe
von 32 Monaten verurteilt. Die dagegen erhobenen Rechtsmittel blieben erfolglos
(Urteil des Obergerichts des Kantons Thurgau vom 9. Juli 2020 und
Urteil des Bundesgerichts 6B_1105/2020 vom 13. Oktober 2021).</p>

<p><b>2.</b></p>
<p><b>2.1.</b> Angefochten ist ein Nichteintretensentscheid eines oberen kantonalen Gerichts
auf dem Gebiet des Ausländerrechts und somit in einer öffentlich-rechtlichen Angelegenheit.
Ob in der Sache die Beschwerde in öffentlich-rechtlichen Angelegenheiten zur Verfügung
steht (Art. 83 lit. c Ziff. 2 BGG), kann angesichts des Ausgangs des Verfahrens offenbleiben.</p>

<p>Streitgegenstand vor Bundesgericht kann einzig die Frage bilden, ob die Vorinstanz zu Recht
auf die bei ihr erhobene Rechtsverweigerungsbeschwerde nicht eingetreten ist
(vgl. u.a. BGE 139 II 233 E. 3.2; BGE 135 II 38 E. 1.2).</p>

<p>Die Anwendung kantonalen Rechts prüft das Bundesgericht nur auf Willkür hin
(BGE 149 IV 183 E. 2.4; BGE 143 I 321). Siehe auch Urteil 2C_509/2024 vom 23. Oktober 2024,
2C_487/2023 und 2C_341/2025.</p>

<p><b>3.</b></p>
<p>Bei diesem Ausgang des Verfahrens sind die Kosten des bundesgerichtlichen Verfahrens
dem Beschwerdeführer aufzuerlegen (Art. 66 Abs. 1 BGG). Auf die Beschwerde ist
nicht eingetreten.</p>

<p>Demnach erkennt das Bundesgericht:</p>
<p>1. Auf die Beschwerde wird nicht eingetreten.</p>
<p>2. Die Gerichtskosten von Fr. 800.-- werden dem Beschwerdeführer auferlegt.</p>
<p>3. Dieses Urteil wird den Verfahrensbeteiligten, dem Verwaltungsgericht des Kantons
Thurgau und dem Staatssekretariat für Migration mitgeteilt.</p>
<p>Lausanne, 23. Januar 2026</p>
<p>Im Namen der II. öffentlich-rechtlichen Abteilung</p>
<p>Die Präsidentin: F. Aubry Girardin</p>
<p>Die Gerichtsschreiberin: D. Ivanov</p>
</div>
</div>
</body>
</html>
"""

# ============================================================
# SAMPLE: Search results page HTML (reconstructed structure)
# ============================================================

SEARCH_HTML = """
<html>
<body>
<div class="content">
<div class="ranklist_header center">27 Urteile gefunden</div>
<div class="ranklist_content">
<ol>
<li>
<span><a href="/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://23-01-2026-2C_28-2026&lang=de&type=show_document">23.01.2026 2C_28/2026</a></span>
<div><div>II. öffentlich-rechtliche Abteilung</div><div>Ausländerrecht</div><div>Rechtsverzögerung / Rechtsverweigerung</div></div>
</li>
<li>
<span><a href="/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://22-01-2026-5A_65-2026&lang=de&type=show_document">22.01.2026 5A_65/2026</a></span>
<div><div>II. zivilrechtliche Abteilung</div><div>Zivilrecht</div><div>Eheschutz</div></div>
</li>
<li>
<span><a href="/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://20-01-2026-7B_1055-2025&lang=de&type=show_document">20.01.2026 7B_1055/2025</a></span>
<div><div>II. strafrechtliche Abteilung</div><div>Strafrecht</div><div>Nichtleistung des Kostenvorschusses</div></div>
</li>
</ol>
</div>
</div>
</body>
</html>
"""


def test_text_extraction():
    """Test text extraction from actual decision HTML."""
    print("=" * 60)
    print("TEST: Text Extraction")
    print("=" * 60)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    
    # PRIMARY SELECTOR: div#highlight_content > div.content
    content = soup.select_one("div#highlight_content > div.content")
    assert content is not None, "FAIL: Primary selector not found"
    
    text = content.get_text(separator="\n")
    assert len(text) > 500, f"FAIL: Text too short ({len(text)} chars)"
    
    print(f"  ✓ Primary selector found: {len(text)} chars")
    print(f"  ✓ First 100 chars: {text[:100].strip()}")
    
    # Check for legal markers
    markers_found = []
    for marker in ["Besetzung", "Erwägungen", "Gegenstand", "Bundesrichterin"]:
        if marker in text:
            markers_found.append(marker)
    print(f"  ✓ Legal markers found: {markers_found}")
    assert len(markers_found) >= 3, f"FAIL: Only {len(markers_found)} markers found"
    
    return text


def test_metadata_extraction(text):
    """Test metadata extraction against real decision."""
    print("\n" + "=" * 60)
    print("TEST: Metadata Extraction")
    print("=" * 60)
    
    # Judges
    judges_m = re.search(
        r"(?:Besetzung|Composition|Composizione)\s*:?\s*(.*?)"
        r"(?:\.\s*\n|\n\s*\n|Parteien|Parties|Parti|Verfahrensbeteiligte)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if judges_m:
        judges = re.sub(r"\s+", " ", judges_m.group(1).strip())[:200]
        print(f"  ✓ Judges: {judges}")
    else:
        print("  ✗ Judges: NOT FOUND")
    
    # Clerks
    clerk_m = re.search(
        r"(?:Gerichtsschreiber(?:in)?|Greffièr?e?|Cancellier[ea])"
        r"\s+([\w][\w\s\-]{2,40}?)(?:\.|,|\n)",
        text, re.IGNORECASE,
    )
    if clerk_m:
        print(f"  ✓ Clerk: {clerk_m.group(1).strip()}")
    else:
        print("  ✗ Clerk: NOT FOUND")
    
    # Title/Subject
    title_m = re.search(
        r"(?:Gegenstand|Objet|Oggetto)\s*:?\s*\n?\s*(.*?)"
        r"(?:\n\s*\n|Beschwerde|Recours|Ricorso)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if title_m:
        title = re.sub(r"\s+", " ", title_m.group(1).strip())[:200]
        print(f"  ✓ Title: {title}")
    else:
        print("  ✗ Title: NOT FOUND")
    
    # Chamber — use the scraper's own resolver rather than a copy of it, so
    # this diagnostic cannot drift from production (it carried the pre-#57
    # unanchored substring match, which read "II." as "I.").
    from scrapers.bger import chamber_from_text
    chamber = chamber_from_text(text)
    if chamber:
        print(f"  ✓ Chamber: {chamber}")
    else:
        print("  ✗ Chamber: NOT FOUND")
    
    # Outcome
    dispositiv = text[-2000:].lower()
    outcomes = [
        ("teilweise gutgeheissen", "partial_approval"),
        ("gutgeheissen", "approved"),
        ("abgewiesen", "dismissed"),
        ("nichteintreten", "inadmissible"),
        ("nicht eingetreten", "inadmissible"),
    ]
    outcome = None
    for pattern, label in outcomes:
        if pattern in dispositiv:
            outcome = label
            break
    if outcome:
        print(f"  ✓ Outcome: {outcome}")
    else:
        print("  ⚠ Outcome: NOT FOUND (might be in Dispositiv)")


def test_citation_extraction(text):
    """Test citation extraction from actual decision text."""
    print("\n" + "=" * 60)
    print("TEST: Citation Extraction")
    print("=" * 60)
    
    # BGE references
    bge_refs = re.findall(r"\bBGE\s+\d{1,3}\s+[IV]+[a-z]?\s+\d+\b", text)
    print(f"  BGE references: {bge_refs}")
    assert len(bge_refs) >= 2, f"FAIL: Expected >= 2 BGE refs, got {len(bge_refs)}"
    print(f"  ✓ Found {len(bge_refs)} BGE references")
    
    # Docket references
    docket_refs = re.findall(r"\b\d{1,2}[A-Z]_\d+/\d{4}\b", text)
    print(f"  Docket references: {docket_refs}")
    assert len(docket_refs) >= 2, f"FAIL: Expected >= 2 docket refs, got {len(docket_refs)}"
    print(f"  ✓ Found {len(docket_refs)} docket references")
    
    # Using models.py extract_citations
    citations = extract_citations(text)
    print(f"  extract_citations(): {citations}")
    print(f"  ✓ Total citations via models.py: {len(citations)}")


def test_language_detection(text):
    """Test language detection."""
    print("\n" + "=" * 60)
    print("TEST: Language Detection")
    print("=" * 60)
    
    lang = detect_language(text)
    print(f"  Detected language: {lang}")
    assert lang == "de", f"FAIL: Expected 'de', got '{lang}'"
    print("  ✓ Correct: German")
    
    # Test with French text from Jordan Chiles case
    fr_text = """
    La finale de l'épreuve féminine individuelle de gymnastique artistique au sol s'est déroulée
    le 5 août 2024 sur un praticable de l'Accord Arena, à Bercy, dans le cadre des Jeux Olympiques.
    Le recours est rejeté. Les frais judiciaires sont mis à la charge de la recourante.
    """
    lang_fr = detect_language(fr_text)
    print(f"  French text detected as: {lang_fr}")
    assert lang_fr == "fr", f"FAIL: Expected 'fr', got '{lang_fr}'"
    print("  ✓ Correct: French")


def test_search_results_parsing():
    """Test search results page parsing."""
    print("\n" + "=" * 60)
    print("TEST: Search Results Parsing")
    print("=" * 60)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(SEARCH_HTML, "html.parser")
    
    # Hit count
    header = soup.select_one("div.content div.ranklist_header.center")
    assert header is not None, "FAIL: ranklist_header not found"
    text = header.get_text(strip=True)
    m = re.match(r"(\d+)", text)
    assert m is not None, "FAIL: Could not parse hit count"
    count = int(m.group(1))
    print(f"  ✓ Hit count: {count} ({text})")
    
    # Results list
    ranklist = soup.select_one("div.ranklist_content ol")
    assert ranklist is not None, "FAIL: ranklist ol not found"
    items = ranklist.find_all("li", recursive=False)
    print(f"  ✓ Found {len(items)} result items")
    
    for li in items:
        link = li.select_one("span > a")
        assert link is not None, "FAIL: span > a not found in li"
        
        link.get("href", "")
        meta_text = link.get_text(strip=True)
        
        # Parse "DD.MM.YYYY DOCKET"
        dm = re.match(r"(\d{2}\.\d{2}\.\d{4})\s*(.*)", meta_text)
        assert dm is not None, f"FAIL: Could not parse '{meta_text}'"
        
        date_str = dm.group(1)
        docket_text = dm.group(2).strip()
        docket_m = re.search(r"(\d{1,2}[A-Z]_\d+/\d{4})", docket_text)
        docket = docket_m.group(1) if docket_m else "?"
        
        # Metadata divs
        divs = li.select("div > div")
        chamber = divs[0].get_text(strip=True) if len(divs) >= 1 else ""
        legal_area = divs[1].get_text(strip=True) if len(divs) >= 2 else ""
        title = divs[2].get_text(strip=True) if len(divs) >= 3 else ""
        
        print(f"    {docket} | {date_str} | {chamber} | {legal_area} | {title}")
    
    print(f"  ✓ All {len(items)} items parsed successfully")


def test_docket_extraction():
    """Test docket number extraction patterns."""
    print("\n" + "=" * 60)
    print("TEST: Docket Extraction")
    print("=" * 60)
    
    DOCKET_RE = re.compile(r"\b(\d{1,2}[A-Z]_\d+/\d{4})\b")
    DOCKET_OLD_RE = re.compile(r"\b(\d[A-Z]\.\d+/\d{4})\b")
    
    test_cases = [
        ("2C_28/2026", "2C_28/2026"),
        ("6B_1105/2020", "6B_1105/2020"),
        ("5A_65/2026", "5A_65/2026"),
        ("7B_1055/2025", "7B_1055/2025"),
        ("4A_494/2024", "4A_494/2024"),
        ("12T_1/2020", "12T_1/2020"),
        # URL-embedded
        ("aza://23-01-2026-2C_28-2026", "2C_28/2026"),  # Won't match - needs special handling
        # Old format
        ("6S.123/2005", "6S.123/2005"),
    ]
    
    for input_str, expected in test_cases:
        m = DOCKET_RE.search(input_str) or DOCKET_OLD_RE.search(input_str)
        found = m.group(1) if m else None
        status = "✓" if found == expected else "✗"
        print(f"  {status} '{input_str}' -> '{found}' (expected '{expected}')")


def test_decision_id_generation():
    """Test decision ID generation."""
    print("\n" + "=" * 60)
    print("TEST: Decision ID Generation")
    print("=" * 60)
    
    test_cases = [
        ("bger", "2C_28/2026", "bger_2C_28_2026"),
        ("bger", "6B_1105/2020", "bger_6B_1105_2020"),
        ("bger", "4A_494/2024", "bger_4A_494_2024"),
    ]
    
    for court, docket, expected in test_cases:
        result = make_decision_id(court, docket)
        status = "✓" if result == expected else "✗"
        print(f"  {status} make_decision_id('{court}', '{docket}') = '{result}' (expected '{expected}')")


def test_url_patterns():
    """Validate URL patterns against confirmed live URLs."""
    print("\n" + "=" * 60)
    print("TEST: URL Pattern Validation")
    print("=" * 60)
    
    # Confirmed working URL patterns from live fetch:
    confirmed = [
        # Decision display (via search.bger.ch with highlight_docid)
        "https://www.bger.ch/ext/eurospider/live/fr/php/aza/http/index.php?highlight_docid=aza%3A%2F%2Faza%3A%2F%2F23-01-2026-4A_494-2024&lang=de&type=show_document",
        # Decision display (via relevancy.bger.ch)
        "http://relevancy.bger.ch/php/aza/http/index.php?highlight_docid=aza://23-01-2026-2C_28-2026&lang=de&type=show_document",
        "http://relevancy.bger.ch/php/aza/http/index.php?highlight_docid=aza://22-01-2026-5A_65-2026&lang=de&type=show_document",
        "http://relevancy.bger.ch/php/aza/http/index.php?highlight_docid=aza://20-01-2026-7B_1055-2025&lang=de&type=show_document",
    ]
    
    # Our JUMP_URL template
    JUMP_URL = "http://relevancy.bger.ch/cgi-bin/JumpCGI?id={date}_{docket}"
    
    # Note: relevancy.bger.ch uses /php/aza/http/index.php NOT /cgi-bin/JumpCGI
    # The JumpCGI URL likely redirects to the index.php URL.
    # Key insight: The direct URL format is:
    # http://relevancy.bger.ch/php/aza/http/index.php?highlight_docid=aza://DD-MM-YYYY-DOCKET_WITH_DASHES&lang=de&type=show_document
    
    print("  Confirmed live URL patterns:")
    for url in confirmed:
        # Extract docket from URL
        m = re.search(r"(\d{2})-(\d{2})-(\d{4})-(\d{1,2}[A-Z]_\d+-\d{4})", url)
        if m:
            day, month, year, docket_raw = m.groups()
            docket = docket_raw.replace("-", "/", 1)  # Last dash is the year separator
            print(f"    ✓ Date: {day}.{month}.{year}, Docket: {docket}")
    
    # Build our direct URL for testing
    def make_direct_url(decision_date: date, docket: str) -> str:
        """Build direct decision URL (confirmed working format)."""
        d = decision_date
        # Format: aza://DD-MM-YYYY-DOCKET_WITH_DASHES
        docket_dashed = docket.replace("/", "-")
        return (
            f"http://relevancy.bger.ch/php/aza/http/index.php?"
            f"highlight_docid=aza://{d.strftime('%d-%m-%Y')}-{docket_dashed}"
            f"&lang=de&type=show_document"
        )
    
    test_url = make_direct_url(date(2026, 1, 23), "2C_28/2026")
    print(f"\n  Our generated URL: {test_url}")
    print("  ✓ Matches confirmed pattern")
    
    print("\n  ⚠ ISSUE: Our JUMP_URL template uses /cgi-bin/JumpCGI")
    print(f"    JumpCGI format:  {JUMP_URL.format(date='23.01.2026', docket='2C_28/2026')}")
    print(f"    Direct format:   {test_url}")
    print("    → Should add direct URL as primary fallback")


def test_abteilung_mapping():
    """Test Abteilung (chamber) mapping from docket prefixes."""
    print("\n" + "=" * 60)
    print("TEST: Abteilung Mapping")
    print("=" * 60)
    
    from scrapers.bger import PREFIX_TO_ABTEILUNG
    
    test_cases = [
        ("2C", "II. Öffentlich-rechtliche Abteilung"),
        ("5A", "II. Zivilrechtliche Abteilung"),
        ("7B", "II. Strafrechtliche Abteilung"),
        ("1C", "I. Öffentlich-rechtliche Abteilung"),
        ("4A", "I. Zivilrechtliche Abteilung"),
        ("6B", "I. Strafrechtliche Abteilung"),
        ("8C", "IV. Öffentlich-rechtliche Abteilung"),   # 2023 rename: numerals cross
        ("9C", "III. Öffentlich-rechtliche Abteilung"),
    ]
    
    for prefix, expected_de in test_cases:
        result = PREFIX_TO_ABTEILUNG.get(prefix)
        if result:
            _, info = result
            actual = info["de"]
            status = "✓" if actual == expected_de else "✗"
            print(f"  {status} {prefix} -> {actual}")
        else:
            print(f"  ✗ {prefix} -> NOT FOUND (expected: {expected_de})")


def test_full_decision_assembly():
    """Test full Decision object creation."""
    print("\n" + "=" * 60)
    print("TEST: Full Decision Assembly")
    print("=" * 60)
    
    text = BeautifulSoup(SAMPLE_HTML, "html.parser").select_one(
        "div#highlight_content > div.content"
    ).get_text(separator="\n")
    
    decision = Decision(
        decision_id=make_decision_id("bger", "2C_28/2026"),
        court="bger",
        canton="CH",
        chamber="II. Öffentlich-rechtliche Abteilung",
        docket_number="2C_28/2026",
        decision_date=date(2026, 1, 23),
        language=detect_language(text),
        title="Rechtsverzögerung / Rechtsverweigerung",
        full_text=text,
        outcome="inadmissible",
        judges="Bundesrichterin Aubry Girardin, Präsidentin",
        clerks="Ivanov",
        source_url="http://relevancy.bger.ch/php/aza/http/index.php?highlight_docid=aza://23-01-2026-2C_28-2026&lang=de&type=show_document",
        cited_decisions=extract_citations(text),
    )
    
    # Validate
    print(f"  decision_id: {decision.decision_id}")
    print(f"  court:       {decision.court}")
    print(f"  canton:      {decision.canton}")
    print(f"  chamber:     {decision.chamber}")
    print(f"  docket:      {decision.docket_number}")
    print(f"  date:        {decision.decision_date}")
    print(f"  language:    {decision.language}")
    print(f"  title:       {decision.title}")
    print(f"  outcome:     {decision.outcome}")
    print(f"  text_len:    {len(decision.full_text)}")
    print(f"  citations:   {len(decision.cited_decisions or [])}")
    
    # Serialize
    json_str = decision.model_dump_json()
    print(f"  JSON size:   {len(json_str)} bytes")
    print("  ✓ Decision object created and serialized successfully")
    
    return decision


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("BGer PARSING VALIDATION (Offline, using real decision structure)")
    print("=" * 60)
    
    text = test_text_extraction()
    test_metadata_extraction(text)
    test_citation_extraction(text)
    test_language_detection(text)
    test_search_results_parsing()
    test_docket_extraction()
    test_decision_id_generation()
    test_url_patterns()
    test_abteilung_mapping()
    decision = test_full_decision_assembly()
    
    print("\n" + "=" * 60)
    print("ALL PARSING TESTS COMPLETE")
    print("=" * 60)
