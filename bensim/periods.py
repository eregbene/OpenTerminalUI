"""Plain-English periods for the reports: "last week", "last friday", "6 december", "last week of december 2025",
"this week", "yesterday", "december 2025", "2025-12-01 to 2025-12-05" -> (first day, last day), both inclusive.

Weeks are Monday-Friday. A date without a year that would lie in the future means last year's."""
from __future__ import annotations

import datetime as dt
import re

from dateutil import parser as dparser

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
D = dt.date


def _week(day: D) -> tuple[D, D]:
    mon = day - dt.timedelta(days=day.weekday())
    return mon, mon + dt.timedelta(days=4)


def _month(text: str) -> int | None:
    for i, m in enumerate(MONTHS):
        if re.search(rf"\b{m[:3]}[a-z]*\b", text):
            return i + 1
    return None


def _year(text: str, month: int, today: D) -> int:
    y = re.search(r"\b(19|20)\d{2}\b", text)
    if y:
        return int(y.group())
    return today.year if month <= today.month else today.year - 1


def _day(text: str, today: D) -> D:
    t = text.strip()
    if t in ("today", "now"):
        return today
    if t == "yesterday":
        return today - dt.timedelta(days=1)
    m = re.fullmatch(r"(last |this )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", t)
    if m:  # "friday" / "last friday" = the most recent one before today; "this friday" = in this week
        wd = DAYS.index(m.group(2))
        if m.group(1) == "this ":
            return today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=wd)
        back = (today.weekday() - wd) % 7 or 7
        return today - dt.timedelta(days=back)
    got = dparser.parse(t, fuzzy=True, default=dt.datetime(today.year, today.month, 1)).date()
    if not re.search(r"\b(19|20)\d{2}\b", t) and got > today:
        got = got.replace(year=got.year - 1)
    return got


def parse_period(text: str, today: D | None = None) -> tuple[D, D]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    t = re.sub(r"\s+", " ", text.lower().replace(",", " ")).strip() or "last week"
    for sep in (" to ", " - ", " until ", " till "):
        if sep in t:
            a, b = t.split(sep, 1)
            return parse_period(a, today)[0], parse_period(b, today)[1]
    if t == "this week":
        return _week(today)
    if t == "last week":
        return _week(today - dt.timedelta(days=7))
    m = re.fullmatch(r"last (\d+) (day|days|week|weeks)", t)
    if m:
        n = int(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
        return today - dt.timedelta(days=n), today
    if t in ("this month", "last month"):
        first = today.replace(day=1)
        if t == "last month":
            first = (first - dt.timedelta(days=1)).replace(day=1)
        nxt = (first + dt.timedelta(days=32)).replace(day=1)
        return first, min(nxt - dt.timedelta(days=1), today)
    m = re.fullmatch(r"(first|second|third|fourth|last) week of ([a-z]+)(?: (\d{4}))?", t)
    if m and _month(m.group(2)):
        mo = _month(m.group(2))
        yr = int(m.group(3)) if m.group(3) else _year(t, mo, today)
        first = D(yr, mo, 1)
        last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        if m.group(1) == "last":  # the week holding the month's last weekday (Dec 2025: 29 Dec - 2 Jan)
            return _week(last - dt.timedelta(days=max(0, last.weekday() - 4)))
        mon = first + dt.timedelta(days=(7 - first.weekday()) % 7)  # first Monday of the month
        k = ["first", "second", "third", "fourth"].index(m.group(1))
        return _week(mon + dt.timedelta(days=7 * k))
    m = re.fullmatch(r"(week of )(.+)", t)
    if m:
        return _week(_day(m.group(2), today))
    mo = _month(t)
    if mo and not re.search(r"\b\d{1,2}(st|nd|rd|th)?\b(?!\d)", re.sub(r"\b(19|20)\d{2}\b", "", t)):  # "december 2025"
        yr = _year(t, mo, today)
        first = D(yr, mo, 1)
        last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return first, min(last, today)
    d = _day(t, today)
    return d, d


def _selfcheck() -> None:
    mon = D(2026, 10, 12)  # a Monday
    cases = {
        "": (D(2026, 10, 5), D(2026, 10, 9)), "last week": (D(2026, 10, 5), D(2026, 10, 9)),
        "this week": (D(2026, 10, 12), D(2026, 10, 16)), "last friday": (D(2026, 10, 9), D(2026, 10, 9)),
        "friday": (D(2026, 10, 9), D(2026, 10, 9)), "yesterday": (D(2026, 10, 11), D(2026, 10, 11)),
        "6 december": (D(2025, 12, 6), D(2025, 12, 6)), "december 6 2025": (D(2025, 12, 6), D(2025, 12, 6)),
        "6-december": (D(2025, 12, 6), D(2025, 12, 6)),
        "last week of december 2025": (D(2025, 12, 29), D(2026, 1, 2)),
        "first week of december 2025": (D(2025, 12, 1), D(2025, 12, 5)),
        "december 2025": (D(2025, 12, 1), D(2025, 12, 31)), "october": (D(2026, 10, 1), D(2026, 10, 12)),
        "2025-12-01 to 2025-12-05": (D(2025, 12, 1), D(2025, 12, 5)), "2026-10-07": (D(2026, 10, 7), D(2026, 10, 7)),
        "1 dec to 5 dec": (D(2025, 12, 1), D(2025, 12, 5)), "week of 17 november": (D(2025, 11, 17), D(2025, 11, 21)),
        "last 3 days": (D(2026, 10, 9), D(2026, 10, 12)),
    }
    for txt, want in cases.items():
        got = parse_period(txt, mon)
        assert got == want, (txt, got, want)
    print("periods selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
