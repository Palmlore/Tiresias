"""Frame.period_label names a 52/53-week year by the fiscal year, not by the end date's year."""

from types import SimpleNamespace

from tiresias_agent.agents.common import Frame


def label(start: str, end: str) -> str:
    return Frame.period_label.fget(SimpleNamespace(period_start=start, period_end=end))


def test_year_ending_in_early_january_is_the_year_before():
    assert label("2021-01-04", "2022-01-02") == "FY2021"


def test_calendar_and_52_week_december_years_keep_the_end_year():
    assert label("2021-01-01", "2021-12-31") == "FY2021"
    assert label("2021-01-03", "2021-12-26") == "FY2021"


def test_odd_length_period_shows_dates():
    assert label("2021-01-01", "2021-06-30") == "2021-01-01..2021-06-30"
