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
