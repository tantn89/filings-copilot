"""One rule for naming fiscal years, shared by filings and XBRL metrics."""

from __future__ import annotations

from datetime import date, timedelta

# 52/53-week fiscal years end on a fixed weekday near a month end, so a year meant to end
# on Dec 31 can end on Jan 1-6 of the next calendar year.
_WEEK_DRIFT = timedelta(days=7)


def fiscal_year(period_end: date) -> int:
    """Name a fiscal year after the calendar year in which it ends, allowing one week of drift.

    NVIDIA's year ending 2025-01-26 is FY2025 and Apple's ending 2025-09-27 is FY2025, while
    a 53-week year ending 2022-01-01 is FY2021 rather than colliding with the year ending
    2022-12-31. Filers that name the year after the calendar year in which it *starts*
    (some retailers) will appear one year later than in their own reports.
    """
    return (period_end - _WEEK_DRIFT).year
