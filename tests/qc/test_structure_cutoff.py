"""structure.federal_have_erwaegungen samples BGer decisions older than
five years. The cutoff used to be the literal '2021-01-01'; it now rolls."""
from datetime import date

from quality.checks.structure import _years_ago


def test_cutoff_is_five_years_before_today():
    assert _years_ago(5, date(2026, 11, 25)) == "2021-11-25"


def test_leap_day_falls_back_to_28_february():
    assert _years_ago(5, date(2028, 2, 29)) == "2023-02-28"


def test_default_is_today():
    t = date.today()
    assert _years_ago(5) == _years_ago(5, t)
