"""Failure modes found by the 2026-09 field test and the BGE coverage measurement."""
from search_stack.extract_decision_structure import extract, parse_erwaegungen_paragraphs


def numbers(text, lang="de", decision_id="x"):
    return [p["e_number"] for p in extract(text, lang, decision_id).erwaegungen_paragraphs or [] if p["depth"] >= 1]


def test_italian_bge_excerpt_header_dai_considerandi():
    text = "Regeste\n Art. 8 CC.\n\nDai considerandi:\n\n2. \nLa ricorrente sostiene ...\n\n2.1 \nSecondo la giurisprudenza ...\n\n3. \nNe segue che ...\n"
    assert numbers(text, "it") == ["2", "2.1", "3"]


def test_german_bge_excerpt_header_and_a_block_that_starts_at_a_sub_number():
    text = "Regeste\n Art. 336 OR.\n\nAus den Erwägungen:\n\n4.1 Die Vorinstanz hat ...\n\n4.2 Nach der Rechtsprechung ...\n\n4.3 Daraus folgt ...\n\n5. Die Beschwerde ist abzuweisen.\n"
    assert numbers(text) == ["4.1", "4.2", "4.3", "5"]


def test_old_spelling_hat_in_erwaegung_gezogen():
    text = "90. Urtheil vom 8. Oktober 1886 in Sachen Fischl gegen Gröner.\n\nA. Durch Urtheil vom 4. Juni 1886 hat das Handelsgericht erkannt ...\n\nDas Bundesgericht hat in Erwägung gezogen:\n\n1. Die Berufung ist ...\n\n2. In der Sache selbst ...\n\n3. Demnach ...\n"
    assert numbers(text) == ["1", "2", "3"]


def test_a_running_case_number_before_the_first_marker_does_not_eat_the_sequence():
    erw = "12. Urteil vom 3. März 1950 i.S. X.\n\n1. Streitig ist ...\n\n2. Nach Art. 41 OR ...\n\n3. Die Klage ist ...\n"
    assert [p["e_number"] for p in parse_erwaegungen_paragraphs(erw)] == ["1", "2", "3"]


def test_a_date_at_a_line_start_is_not_a_marker():
    text = "Considérant en droit:\n\n1. Le recours est recevable.\n\n2. La recourante a été licenciée le\n4 février 2013, ce qui ...\n\n3. Il s'ensuit que ...\n"
    assert numbers(text, "fr") == ["1", "2", "3"]


def test_lettered_sub_erwaegungen_become_units_and_the_parent_stays_complete():
    erw = ("1. Vorbemerkung.\n\n2. Zur Sache.\na) Das Rekursgericht hat festgestellt ...\nb) Dagegen wendet der Beschwerdeführer ein ...\n\n"
           "3. Zur Beschwerde.\na) Zulässigkeit ...\nb) Legitimation ...\nc) aa) Erstens ...\nbb) Zweitens ...\n")
    paras = {p["e_number"]: p for p in parse_erwaegungen_paragraphs(erw)}
    assert "2a" in paras and paras["2a"]["parent"] == "2" and paras["2a"]["depth"] == 2 and paras["2a"]["text"].startswith("Das Rekursgericht")
    assert "2b" in paras and "b) Dagegen" in paras["2"]["text"] and "a) Das Rekursgericht" in paras["2"]["text"]
    assert paras["3c"]["text"].startswith("aa) Erstens") and paras["3c/aa"]["text"].startswith("Erstens") and paras["3c/bb"]["parent"] == "3c"


def test_a_stray_letter_in_prose_does_not_split_a_paragraph():
    erw = "1. Die Voraussetzungen sind:\nb) nicht erfüllt, weil ...\n\n2. Daher ...\n"
    assert [p["e_number"] for p in parse_erwaegungen_paragraphs(erw)] == ["1", "2"]


def test_regular_shapes_are_unchanged():
    text = "Sachverhalt\n\nA. ...\n\nErwägungen\n\n1. Die Beschwerde ...\n\n2. Streitig ist ...\n\n2.1 Nach Art. 336 OR ...\n\n2.2 Die Vorinstanz ...\n\n3. Die Beschwerde ist abzuweisen.\n\nDemnach erkennt das Bundesgericht:\n\n1. Die Beschwerde wird abgewiesen.\n"
    assert numbers(text) == ["1", "2", "2.1", "2.2", "3"]


# ── historical BGE (volumes 1-79): the page range runs into the next ruling ──
# BGE 78 IV 83 (Fyg gegen Born) has no numbered Erwägungen, only "Aus den
# Erwägungen :". Its text ran on into No 23, so the extractor served No 23's
# serial number as "Erwägung 23" with Friedlin's text, and No 23's lettered
# facts as the Sachverhalt (user report 2026-10-07).

_FYG_PAGES = (
    "82\nStrafgesetzbuch. No 21.\nauch im Brief vom 17. Juli 1951 hat das Mädchen\n"
    "nichts anderes geschrieben.\nStrafgesetzbuch. No 22.\n83\n"
    "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952\ni. S. Fyg gegen Born.\n"
    "Art. 57 Abs. 1 OR, Art. 32, 145 StGB. Sachbeschädigung durch Abschuss einer Katze.\n"
    "Friedrich Fyg sah am Nachmittag des 25. November 1951 eine fremde Katze.\n"
    "Aus den Erwägungen :\nDer Beschwerdeführer beruft sich auf Art. 57 OR, wonach der Besitzer\n"
    "eines Grundstückes berechtigt ist, Dritten angehörige Tiere zu töten.\n"
    "Diese Bestimmung trifft jedoch schon deshalb nicht zu.\n"
    "84\nStrafgesetzbuch. No 23.\njede Vorstellung bei Born zu unterlassen.\n"
    "23. Auszug aus dem Urteil des Kassationshofes vom 30. 1fai 1952\n"
    "i. S. Staatsanwaltschaft des Kantons Aargau gegen Friedlin.\n"
    "Art. 148 StGB trifft auf den sog. Prozessbetrug nicht zu.\nA. -\n"
    "Margrith Friedlin und ihr am 6. Juli 1948 geborenes Kind klagten.\nB. -\n"
    "Am 29. Januar 1952 sprach das Kriminalgericht Margrith Friedlin frei.\n"
)


def test_historical_bge_structure_stays_inside_its_own_ruling():
    for did in ("bge_78_IV_83", "bge_historical_78_IV_83"):
        s = extract(_FYG_PAGES, "de", did)
        assert s.erwaegungen_method == "ranked_de_BGE_aus_den"
        assert s.erwaegungen.startswith("Der Beschwerdeführer beruft sich auf Art. 57 OR")
        assert "Friedlin" not in s.erwaegungen
        assert "Friedlin" not in (s.sachverhalt or "")
        assert [p["e_number"] for p in s.erwaegungen_paragraphs if p["depth"] >= 1] == []


def test_a_ruling_heading_is_never_an_erwaegung_and_ends_the_paragraph_before_it():
    erw = ("1. Der Beschwerdeführer beruft sich auf Art. 57 OR.\n\n"
           "2. Diese Bestimmung trifft nicht zu.\n"
           "23. Auszug aus dem Urteil des Kassationshofes vom 30. Mai 1952\n"
           "i. S. Staatsanwaltschaft des Kantons Aargau gegen Friedlin.\nA. -\nMargrith Friedlin ...\n")
    paras = parse_erwaegungen_paragraphs(erw, ruling_bounds=True)
    assert [p["e_number"] for p in paras] == ["1", "2"]
    assert "Friedlin" not in paras[-1]["text"]
