"""Weekly poster: the counting rule and the dot layout (no git, no browser)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import weekly_poster as wp


def snap(ts, rows, total=0):
    return {"generated_at": ts, "total": total, "court_count": len(rows),
            "by_court": [{"court": c, "canton": k, "count": n} for c, k, n in rows],
            "corpus": {"citation_edges": 10_058_552}}


BASE = snap("2026-09-24T21:00:00+00:00", [
    ("bger", "CH", 100), ("zh_obergericht", "ZH", 50), ("vd_gerichte", "VD", 900),
    ("vd_findinfo", "VD", 10), ("ecthr_chamber", "CH", 5), ("ecthr_chamber", "CE", 5),
])
CUR = snap("2026-10-01T23:00:00+00:00", [
    ("bger", "CH", 130), ("zh_obergericht", "ZH", 57), ("vd_gerichte", "VD", 600),
    ("vd_findinfo", "VD", 14), ("ecthr_chamber", "CH", 6), ("ecthr_chamber", "CE", 7000),
], total=7807)


def test_cleanup_night_does_not_hide_additions_and_is_reported():
    d = wp.weekly_delta([BASE, CUR])
    assert d["by_canton"] == {"CH": 31, "ZH": 7, "VD": 4}
    assert d["added"] == 42
    assert d["removed"] == 300


def test_same_court_in_two_cantons_is_diffed_per_canton_and_foreign_rows_stay_out():
    d = wp.weekly_delta([BASE, CUR])
    assert d["by_court"]["ecthr_chamber"] == 1  # the CE bulk load is not Swiss case law


def test_window_picks_snapshot_closest_to_seven_days_back():
    mid = snap("2026-09-28T21:00:00+00:00", [("bger", "CH", 110)])
    base, cur = wp.pick_window([BASE, mid, CUR])
    assert (base, cur) == (BASE, CUR)


def test_dots_stay_one_per_decision_until_the_fullest_tile_overflows():
    widths = {"ZH": wp.CELL_W}
    assert wp.dot_grid({"ZH": 150}, widths)[0] == 1
    unit, pitch = wp.dot_grid({"ZH": 5000}, widths)
    assert unit > 1 and -(-5000 // unit) <= (wp.CELL_W // pitch) * ((wp.CELL_H - wp.LABEL_H) // pitch)


def test_poster_headline_equals_the_dots_drawn():
    d = wp.weekly_delta([BASE, CUR])
    page = wp.build_html(d, CUR, "de", "red")
    assert page.count('fill="#ffffff"/>') == d["added"]
    assert "1’" not in wp.fmt(999, "de") and wp.fmt(7807, "de") == "7’807"
    assert "Woche 40" in page and "42" in wp.build_caption(d, CUR, "de")


def test_treemap_areas_are_proportional_and_fill_the_box():
    sizes = [274, 198, 159, 84, 68, 37, 3, 1]
    rects = wp.squarify(sizes, 28, 488, 1024, 604)
    per_unit = 1024 * 604 / sum(sizes)
    for n, (x, y, w, h) in zip(sizes, rects):
        assert abs(w * h - n * per_unit) < 1e-6
        assert x >= 28 - 1e-6 and y >= 488 - 1e-6 and x + w <= 1052 + 1e-6 and y + h <= 1092 + 1e-6


def test_type_poster_names_every_canton_once_and_lists_the_silent_ones():
    d = wp.weekly_delta([BASE, CUR])
    page = wp.build_html_type(d, CUR, "en")
    for code in ("CH", "ZH", "VD"):
        assert page.count(f">{code}</text>") == 1
    assert "Nothing new this week from AG AI AR" in page


def test_additions_before_a_cleanup_night_still_count_and_a_dip_is_not_counted_twice():
    mid = snap("2026-09-27T21:00:00+00:00", [
        ("bger", "CH", 90), ("zh_obergericht", "ZH", 50), ("vd_gerichte", "VD", 930),
        ("vd_findinfo", "VD", 10), ("ecthr_chamber", "CH", 5), ("ecthr_chamber", "CE", 5),
    ])
    d = wp.weekly_delta([BASE, mid, CUR])
    assert d["by_court"]["vd_gerichte"] == 30  # added before the dedupe took 330 away
    assert d["by_court"]["bger"] == 30  # 100 -> 90 -> 130 is thirty new, not forty
    assert d["removed"] == 330
    assert sum(sum(day.values()) for day in d["by_day"].values()) == d["added"]


def test_archive_manifest_replaces_the_same_week_and_keeps_newest_first():
    import weekly_poster_editions as ed

    m = ed.update_manifest({}, {"week": "2026-W39", "added": 1})
    m = ed.update_manifest(m, {"week": "2026-W40", "added": 2})
    m = ed.update_manifest(m, {"week": "2026-W39", "added": 3})  # a re-run of week 39
    assert [(w["week"], w["added"]) for w in m["weeks"]] == [("2026-W40", 2), ("2026-W39", 3)]
    assert m["schema"] == ed.SCHEMA


def test_every_site_language_has_exactly_one_edition_and_a_caption():
    import weekly_poster_editions as ed

    assert set(ed.EDITION) == set(ed.CAPTION) == {"de", "fr", "it", "rm", "en"}
    assert len({name for name, _ in ed.EDITION.values()}) == 5


def test_a_snapshot_from_a_development_database_is_ignored():
    dev = snap("2026-10-02T11:11:00+00:00", [("bger", "CH", 3)], total=37_360)
    good = [snap(f"2026-09-{d}T21:00:00+00:00", [("bger", "CH", 1)], total=1_078_000 + d) for d in (24, 25, 26)]
    assert wp.drop_implausible([*good, dev]) == good


def _split(s, bger_vd, bger_lu):
    s = {**s, "by_court": s["by_court"] + [{"court": "bger_x", "canton": "XX", "count": 0}][:0]}
    s["federal_seats"] = {"bger": {"VD": bger_vd, "LU": bger_lu}}
    return s


def test_federal_supreme_court_is_split_by_seat_when_every_snapshot_carries_it():
    base = _split(BASE, 70, 30)      # bger 100
    cur = _split(CUR, 85, 45)        # bger 130
    d = wp.weekly_delta([base, cur])
    assert d["federal_split"] is True
    assert d["by_court"]["bger@VD"] == 15 and d["by_court"]["bger@LU"] == 15
    assert "bger" not in d["by_court"] and d["by_canton"]["CH"] == 31


def test_no_split_when_one_snapshot_lacks_it_or_does_not_add_up():
    d = wp.weekly_delta([BASE, _split(CUR, 85, 45)])
    assert d["federal_split"] is False and d["by_court"]["bger"] == 30
    d = wp.weekly_delta([_split(BASE, 70, 30), _split(CUR, 85, 44)])  # 129 != 130
    assert d["federal_split"] is False and d["by_court"]["bger"] == 30


def test_luzern_rulings_stand_in_luzern_on_the_map():
    import weekly_poster_editions as ed

    d = wp.weekly_delta([_split(BASE, 70, 30), _split(CUR, 85, 45)])
    seats = ed.seat_heights(d)
    assert seats["LU"] >= 15 and seats["VD"] >= 15 + 4  # vd_findinfo adds 4 in Lausanne


def test_each_language_alternates_between_its_designs_week_by_week():
    import weekly_poster_editions as ed

    lib = ed.library()
    assert {lang: ed.pick(lang, 40, lib)[0] for lang in lib} == {
        "de": "topo", "fr": "sediment", "it": "score", "rm": "fields", "en": "receipt"}
    assert {lang: ed.pick(lang, 41, lib)[0] for lang in lib} == {
        "de": "board", "fr": "rose", "it": "mosaic", "rm": "sgraffito", "en": "forecast"}
    assert ed.pick("de", 42, lib)[0] == "topo"
    assert all(name in ed.CAPTION_BY_DESIGN or lang in ed.CAPTION for lang, designs in lib.items() for name, _ in designs)


def test_forecast_trend_words():
    import weekly_poster_designs as dz

    assert dz.trend(557, 198) == "557, rising sharply from 198"
    assert dz.trend(172, 159) == "172, steady from 159"
    assert dz.trend(0, 3) == "quiet, after 3"
    assert dz.trend(15, 0) == "15, new this week"
    assert dz.trend(0, 0) == ""


def test_new_designs_render_from_a_two_snapshot_week():
    import weekly_poster_designs as dz

    d = wp.weekly_delta([BASE, CUR])
    for lang, (name, fn) in dz.DESIGNS.items():
        usual = {"by_canton": d["by_canton"], "added": d["added"], "weeks": 8}
        extra = {"board": {"prev": d}, "forecast": {"usual": usual}, "rose": {"usual": usual},
                 "sgraffito": {"usual": usual}}.get(name, {})
        page = fn(d, CUR, lang, **extra)  # no git: every baseline is passed in
        assert page.startswith("<!doctype html>") and "opencaselaw.ch" in page, name


def test_multi_court_sources_split_by_court_code_when_every_snapshot_carries_it():
    import weekly_poster_designs as dz

    def chambered(s, atas, acjc, other):
        rows = [r for r in s["by_court"] if r["court"] != "ge_gerichte"] + [
            {"court": "ge_gerichte", "canton": "GE", "count": atas + acjc + other}]
        return {**s, "by_court": rows, "court_chambers": {"ge_gerichte": {"ATAS": atas, "ACJC": acjc, "A": other}}}

    d = wp.weekly_delta([chambered(BASE, 10, 5, 1), chambered(CUR, 15, 6, 2)])
    assert d["by_court"]["ge_gerichte#ATAS"] == 5 and d["by_court"]["ge_gerichte#ACJC"] == 1
    folded = dz.fold_chambers(d["by_court"])
    assert folded["ge_gerichte#Cour de justice, Chambre des assurances sociales"] == 5
    assert folded["ge_gerichte"] == 1  # "A" is a docket prefix, not a court: back to the canton row
    badge, city, via, fed = dz.board_row("ge_gerichte#Cour de justice, Chambre des assurances sociales", "GE")
    assert (badge, city, fed) == ("ATAS", "Genève", False) and via.endswith("Kanton Genf")
ZH_BASE = snap("2026-10-02T21:00:00+00:00", [
    ("zh_gerichte", "ZH", 100), ("zh_obergericht", "ZH", 50), ("zh_bezirksgericht_horgen", "ZH", 5),
    ("vd_gerichte", "VD", 900), ("bger", "CH", 100),
])


def _zh_cur(gerichte, obergericht, horgen, ts="2026-10-09T13:00:00+00:00"):
    return snap(ts, [
        ("zh_gerichte", "ZH", gerichte), ("zh_obergericht", "ZH", obergericht),
        ("zh_bezirksgericht_horgen", "ZH", horgen), ("vd_gerichte", "VD", 600), ("bger", "CH", 120),
    ])


def test_rulings_leaving_the_catch_all_for_a_named_court_are_refiled_not_new():
    # 90 leave zh_gerichte; the Obergericht gains 70, Horgen 15: all 85 are re-filed.
    d = wp.weekly_delta([ZH_BASE, _zh_cur(10, 120, 20)])
    assert d["by_canton"].get("ZH", 0) == 0
    assert d["refiled"] == {"ZH": 85}
    assert d["added"] == 20                     # the Bundesgericht's 20, nothing from Zurich
    assert d["removed"] == 300 + 5              # Vaud's clean-up + the 5 that left the corpus
    assert sum(sum(day.values()) for day in d["by_day"].values()) == d["added"]


def test_only_what_the_catch_all_lost_is_taken_off():
    # 30 leave zh_gerichte, Zurich's named courts gain 85: 55 stay new, the largest court gives first.
    mid = _zh_cur(70, 100, 5, ts="2026-10-05T21:00:00+00:00")
    d = wp.weekly_delta([ZH_BASE, mid, _zh_cur(70, 120, 20)])
    assert d["refiled"] == {"ZH": 30}
    assert d["by_canton"]["ZH"] == 55
    assert d["by_court"]["zh_obergericht"] == 40 and d["by_court"]["zh_bezirksgericht_horgen"] == 15
    assert sum(sum(day.values()) for day in d["by_day"].values()) == d["added"]


def test_a_dedupe_elsewhere_is_not_mistaken_for_re_filing():
    d = wp.weekly_delta([BASE, CUR])
    assert d["refiled"] == {} and d["by_canton"] == {"CH": 31, "ZH": 7, "VD": 4}
