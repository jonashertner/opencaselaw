"""How references are parsed for identity checks: nothing here prints a citation."""
import pytest
from opencaselaw_cli.references import (docket_in_reference, docket_variants, label_key, normalise_pinpoint,
                                        parse_reference, pinpoint_parent)


@pytest.mark.parametrize("text, expected", [
    ("BGE 136 III 513 E. 2.3", dict(bge_label="BGE 136 III 513", pinpoint="2.3", core="BGE 136 III 513")),
    ("ATF 137 III 303 consid. 2 p. 305", dict(bge_label="BGE 137 III 303", pinpoint="2", pages=["p. 305"])),
    ("BGE 125 II 633 E. 2 S. 636", dict(pinpoint="2", pages=["S. 636"], core="BGE 125 II 633")),
    ("BGE 121 V 240 E. 3c/aa", dict(pinpoint="3c/aa")),
    ("BGE 134 III 354 ff.", dict(bge_label="BGE 134 III 354", core="BGE 134 III 354")),
    ("(BGE 136 III 510)", dict(core="BGE 136 III 510")),
    ("BGE 136 III 510.", dict(core="BGE 136 III 510")),
    ("BGE 134 III 354 (4A_45/2008)", dict(bge_label="BGE 134 III 354", dockets=["4A_45/2008"])),
    ("BGer 4A_255/2012", dict(dockets=["4A_255/2012"], courts={"bger", "bge"}, court_words=True)),
    ("Urteil des Bundesgerichts 4A 87/2019 vom 2. September 2019, E. 4.2.1",
     dict(dockets=["4A 87/2019"], date="2019-09-02", pinpoint="4.2.1")),
    ("arrêt du TF 4A_485/2015 du 15 février 2016 consid. 3", dict(dockets=["4A_485/2015"], date="2016-02-15", pinpoint="3")),
    ("sentenza del TF 9C_313/2016 del 22 dicembre 2016", dict(dockets=["9C_313/2016"], date="2016-12-22")),
    ("arrêt du Tribunal fédéral 4A_89/2021 du 1er avril 2022", dict(date="2022-04-01")),
    ("Obergericht ZH LA210005 vom 15. Juni 2021", dict(dockets=["LA210005"], canton="ZH", date="2021-06-15")),
    ("Urteil des Verwaltungsgerichts des Kantons Aargau WBE.2026.33", dict(dockets=["WBE.2026.33"], canton="AG")),
    ("Gericht GE C/11532/2013 vom 8. November 2016", dict(dockets=["C/11532/2013"], canton="GE")),
    ("arrêt de la Cour de justice de Genève ACJC/1234/2024 du 5 mars 2024", dict(dockets=["ACJC/1234/2024"], canton="GE")),
    ("Tribunal VD HC / 2020 / 38 du 6 mai 2020", dict(dockets=["HC / 2020 / 38"], canton="VD")),
    ("Tribunal cantonal VD, arrêt HC / 2018 / 391 du 26 janvier 2018", dict(dockets=["HC / 2018 / 391"], canton="VD", date="2018-01-26")),
    ("Gericht VD 1/2020 vom 14. Januar 2020", dict(dockets=["1/2020"], canton="VD")),
    ("Gericht OW AbR 1992/93 Nr. 8 vom 26. November 2015", dict(dockets=["AbR 1992/93 Nr. 8"], canton="OW")),
    ("Gericht SZ ZK1 2023 26 vom 2. April 2024", dict(dockets=["ZK1 2023 26"], canton="SZ")),
    ("Obergericht ZH LA210005 vom 15. Juni 2021, E. 3 (vgl. auch BGer 4A_747/2012)", dict(dockets=["LA210005", "4A_747/2012"], canton="ZH", courts=set(), pinpoint="3")),
    ("BGer 4A_9999/2012 und 4A_747/2012", dict(dockets=["4A_9999/2012", "4A_747/2012"], courts={"bger", "bge"})),
    ("4A_747/2012 (BGE 134 III 354)", dict(dockets=["4A_747/2012"], bge_label="BGE 134 III 354", bge_first=False)),
    ("VD.2020.89", dict(dockets=["VD.2020.89"], canton=None)),
    ("Appellationsgericht Basel-Stadt, Urteil VD.2020.89 vom 19. Mai 2020", dict(dockets=["VD.2020.89"], canton="BS")),
    ("Obergericht des Kantons Zürich, Urteil LA210005 vom 15. Juni 2021 i.S. A. AG", dict(dockets=["LA210005"], canton="ZH")),
    ("Gericht XX Foo 12 Bar vom 1. Januar 2020", dict(dockets=[], residual="Foo 12 Bar")),
    ("BVGer A-4843/2020 vom 1. April 2021", dict(dockets=["A-4843/2020"], courts={"bvger"})),
    ("Gericht BL 810 16 9 vom 10. August 2016", dict(dockets=["810 16 9"], canton="BL")),
    ("Verwaltungsgericht SG K 2015/3, K 2017/3 vom 18. November 2020", dict(dockets=["K 2015/3", "K 2017/3"], canton="SG")),
    # the EVG single-letter chambers and the two-digit BGer chambers, in every separator
    ("B 59/2001", dict(dockets=["B 59/2001"], long_form=False)),
    ("B.59/2001", dict(dockets=["B.59/2001"], long_form=False)),
    ("I 25/2005", dict(dockets=["I 25/2005"])),
    ("Urteil des EVG M 10/2004 vom 31. August 2005, E. 2", dict(dockets=["M 10/2004"], courts={"bger", "bge"}, date="2005-08-31", pinpoint="2")),
    ("arrêt du TFA U 100/00", dict(dockets=[], courts={"bger", "bge"}, residual="U 100/00")),
    ("12T 3/2013", dict(dockets=["12T 3/2013"], long_form=False)),
    ("BGer 13Y_1/2020 vom 4. Mai 2020", dict(dockets=["13Y_1/2020"], courts={"bger", "bge"})),
    # not EVG dockets: St. Gallen writes the year first, Geneva / Vaud numbers have no slash
    ("K 2015/3", dict(dockets=["K 2015/3"])),
    ("P 123 1999", dict(dockets=[])),
    # a bare N/YYYY behind a chamber-like token the parser does not know is the
    # tail of that docket, not a Vaud or Basel-Landschaft number
    ("X 59/2001", dict(dockets=[])),
    ("4Z 59/2001", dict(dockets=["4Z 59/2001"])),
    # cantonal shapes whose tail is a bare N/YYYY: the whole number is the docket
    ("BRGE I Nr. 0167/2014", dict(dockets=["BRGE I Nr. 0167/2014"], long_form=False)),
    ("BRKE II Nrn. 0012-0013/2015", dict(dockets=["BRKE II Nrn. 0012-0013/2015"])),
    ("Baurekursgericht des Kantons Zürich, BRGE I Nr. 0167/2014 vom 5. Dezember 2014", dict(dockets=["BRGE I Nr. 0167/2014"], canton="ZH")),
    ("AI 12/14 - 140/2014", dict(dockets=["AI 12/14 - 140/2014"], long_form=False)),
    ("4C.230/2006", dict(dockets=["4C.230/2006"], long_form=False)),
    ("OGer ZH, LA210005, 15.6.2021", dict(dockets=["LA210005"], date="2021-06-15")),
    ("1/2020", dict(dockets=["1/2020"], core="1/2020", long_form=False)),
    ("WBE.2026.33", dict(dockets=["WBE.2026.33"], pinpoint=None, long_form=False)),
    ("Bundesgericht, Urteil vom 5. April 2013", dict(dockets=[], date="2013-04-05", long_form=True)),
    ("BGE 136 III 513", dict(long_form=False, courts={"bge"}, court_words=False)),
    ("bge_BGE_125_II_633", dict(long_form=False, court_words=False, dockets=[])),
])
def test_references_are_parsed_as_written(text, expected):
    parsed = parse_reference(text)
    for key, value in expected.items():
        assert getattr(parsed, key) == value, (text, key, getattr(parsed, key))


def test_queries_ask_for_the_label_not_the_prose():
    assert parse_reference("BGer 4A 535/2018").queries() == ["4A_535/2018", "4A.535/2018", "4A 535/2018"]
    assert parse_reference("BGE 134 III 354 (4A_45/2008)").queries() == ["BGE 134 III 354"]
    assert parse_reference("Bundesgericht, Urteil vom 5. April 2013").queries() == ["Bundesgericht, Urteil vom 5. April 2013"]
    assert parse_reference("1/2020").queries() == ["1/2020"]
    assert parse_reference("Obergericht ZH LA210005 vom 15. Juni 2021 (vgl. auch BGer 4A_747/2012)").queries() == ["LA210005"]
    assert parse_reference("Gericht XX Foo 12 Bar vom 1. Januar 2020").queries() == ["Gericht XX Foo 12 Bar vom 1. Januar 2020", "Foo 12 Bar"]
    assert docket_variants("4C.230/2006") == ["4C_230/2006", "4C.230/2006"] and docket_variants("LA210005") == ["LA210005"]
    assert docket_variants("HC / 2018 / 391") == ["HC / 2018 / 391", "HC/2018/391"]
    assert docket_variants("HC/2018/391") == ["HC/2018/391", "HC / 2018 / 391"]
    assert docket_variants("B 59/2001") == ["B_59/2001", "B.59/2001", "B 59/2001"]
    assert docket_variants("12T 3/2013") == ["12T_3/2013", "12T.3/2013", "12T 3/2013"]
    assert docket_variants("K 2015/3") == ["K 2015/3"] and docket_variants("BRGE I Nr. 0167/2014") == ["BRGE I Nr. 0167/2014"]


def test_label_key_folds_only_for_comparison():
    assert label_key("BGE 136 III 513, E. 2.3") == label_key("ATF 136 III 513") == label_key("136 III 513") == "bge136iii513"
    assert label_key("BGE 134 III 354 ff.") == label_key("BGE 134 III 354 S. 357") == label_key("BGE 134 III 354, E. 2.1, S. 357")
    assert label_key("4A_747/2012") == label_key("4A 747/2012") == label_key("4A.747/2012")
    assert label_key("BGer 4A_747/2012 vom 5. April 2013") == label_key("BGer 4A 747/2012 vom 5. April 2013")
    assert label_key("B 59/2001") == label_key("B_59/2001") == label_key("B.59/2001") == "b59/2001"
    assert label_key("12T 3/2013") == label_key("12T_3/2013") and label_key("B 59/2001") != label_key("59/2001")
    assert label_key("140 III 86") != label_key("140 III 860") and label_key(None) is None


def test_docket_must_appear_whole_in_the_reference():
    assert docket_in_reference("BGer 4A_255/2012", "4A 255/2012")
    assert docket_in_reference("Obergericht ZH LA210005 vom 15. Juni 2021", "LA210005")
    assert docket_in_reference("Tribunal VD HC / 2020 / 38 du 6 mai 2020", "HC/2020/38")
    assert docket_in_reference("4C_230/2006", "4C.230/2006")
    assert not docket_in_reference("100/2015", "D-1100/2015")
    assert not docket_in_reference("BGE 134 III 354", "4A_45/2008")
    assert not docket_in_reference("1/2020", "11/2020") and not docket_in_reference("x", None)
    # a stored docket that is only the numeric tail of the written one is not "carried"
    assert not docket_in_reference("4A_747/2012", "747/2012") and not docket_in_reference("ACJC/1234/2024", "1234/2024")
    assert not docket_in_reference("A-4843/2020", "4843/2020")
    # the EVG and two-digit chambers compare equal across the stored separators, never to their tail
    assert docket_in_reference("B 59/2001", "B_59/2001") and docket_in_reference("EVG B.59/2001", "B_59/2001")
    assert docket_in_reference("12T 3/2013", "12T_3/2013") and docket_in_reference("BGer 12T_3/2013", "12T 3/2013")
    assert not docket_in_reference("B 59/2001", "59/2001") and not docket_in_reference("12T 3/2013", "3/2013")
    assert not docket_in_reference("X 59/2001", "59/2001")


def test_pinpoints_accept_the_authors_spelling_and_fail_only_themselves():
    assert normalise_pinpoint("consid. 2.3") == "2.3" and normalise_pinpoint("E. 3b") == "3b"
    assert normalise_pinpoint("3c/aa") == "3c/aa" and normalise_pinpoint(" 4.2.1 ") == "4.2.1" and normalise_pinpoint("") is None
    assert normalise_pinpoint("2.3 f.") == "2.3" and normalise_pinpoint("2.3 ff.") == "2.3" and normalise_pinpoint("consid. 2 s.") == "2"
    assert pinpoint_parent("3c/aa") == "3" and pinpoint_parent("2a") == "2" and pinpoint_parent("4.2.1") is None
    with pytest.raises(ValueError):
        normalise_pinpoint("foo")
    with pytest.raises(ValueError):
        normalise_pinpoint(12)


def test_court_scope_filters_candidates_but_keeps_unknown_courts():
    federal = parse_reference("BGer 4A_191/2019 vom 5. November 2019")
    assert federal.in_scope({"court": "bger"}) and not federal.in_scope({"court": "ge_gerichte", "canton": "GE"})
    assert federal.in_scope({})  # a candidate without court metadata cannot be ruled out
    geneva = parse_reference("arrêt de la Cour de justice de Genève ACJC/1234/2024")
    assert geneva.in_scope({"court": "ge_gerichte"}) and geneva.in_scope({"canton": "GE"}) and not geneva.in_scope({"court": "bger", "canton": "CH"})
    assert parse_reference("4A_191/2019").in_scope({"court": "ge_gerichte"})
