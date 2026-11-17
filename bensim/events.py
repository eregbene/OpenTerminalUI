"""Economic calendar (Forex Factory) history into `economic_events`, in UTC.

    python -m bensim.events --start 2021-10-01            # backfill week by week (resumable)
    python -m bensim.events --start 2022-02-27 --weeks 1  # one week (check)

Forex Factory shows anonymous visitors times in ITS OWN default zone (not the browser's), so every week is
calibrated on a release with a known clock time: US Unemployment Claims (or another USD 08:30 New York
release) -> offset = shown - true UTC; the whole week is shifted by it. Verified on RBA/ISM/CAD GDP times.
(The old scraper stored the site's local times as UTC -- 2-3 hours off.)
Week pages (not month pages, which lazy-load and came back with ~34 of ~400 rows).
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, datetime, timedelta, timezone

from . import db

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_JS = """() => Array.from(document.querySelectorAll("tr[class*='calendar__row']")).map(r => {
  const q = s => { const e = r.querySelector(s); return e ? e.innerText.trim() : ""; };
  const imp = r.querySelector("td[class*='calendar__impact'] span");
  return {date: q("td[class*='calendar__date']"), time: q("td[class*='calendar__time']"), cur: q("td[class*='calendar__currency']"),
          impact: imp ? (imp.className + " " + (imp.getAttribute("title") || "")) : "", name: q("td[class*='calendar__event']"),
          actual: q("td[class*='calendar__actual']"), forecast: q("td[class*='calendar__forecast']"), previous: q("td[class*='calendar__previous']")};
})"""


def _impact(raw: str) -> str:
    raw = raw.lower()
    for key, name in (("red", "high"), ("high", "high"), ("ora", "medium"), ("medium", "medium"), ("yel", "low"), ("low", "low"), ("gra", "holiday"), ("holiday", "holiday")):
        if key in raw:
            return name
    return "none"


def parse_week(rows: list[dict], year_hint: int) -> list[tuple]:
    out, cur_date, cur_time = [], None, None
    for r in rows:
        if r["date"]:
            m = re.search(r"([A-Za-z]{3})\s+(\d{1,2})", r["date"].replace("\n", " "))
            if m:
                mon, day = _MONTHS[m.group(1).lower()], int(m.group(2))
                # carry the year from the previous day: Dec -> Jan rolls over (all later January days keep the new year)
                year = (cur_date.year + (1 if cur_date.month == 12 and mon == 1 else 0)) if cur_date else year_hint
                cur_date, cur_time = date(year, mon, day), None
        if r["time"]:
            cur_time = r["time"]
        if not cur_date or not r["name"] or not r["cur"]:
            continue
        t = (cur_time or "").lower()
        m = re.match(r"(\d{1,2}):(\d{2})(am|pm)", t)
        if m:
            hh = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
            ts = datetime(cur_date.year, cur_date.month, cur_date.day, hh, int(m.group(2)), tzinfo=timezone.utc)
        else:  # "All Day", "Tentative", "Day 2" ... -> start of day, kept but marked by the name/time text
            ts = datetime(cur_date.year, cur_date.month, cur_date.day, tzinfo=timezone.utc)
        out.append((ts, r["cur"], _impact(r["impact"]), r["name"] + ("" if m else f" [{cur_time or 'no time'}]"),
                    r["actual"] or None, r["forecast"] or None, r["previous"] or None))
    return out


_ANCHORS = ("Unemployment Claims", "CPI m/m", "Core CPI m/m", "Retail Sales m/m", "Non-Farm Employment Change", "Core PCE Price Index m/m")


def calibrate(rows: list[tuple], fallback_hours: int | None) -> tuple[list[tuple], int | None]:
    """Shift a week's rows so USD 08:30-New-York anchor releases land on their true UTC time."""
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    offs = []
    for ts, cur, _imp, name, *_ in rows:
        if cur == "USD" and name in _ANCHORS:
            true = datetime(ts.year, ts.month, ts.day, 8, 30, tzinfo=ny).astimezone(timezone.utc)
            diff = (ts - true).total_seconds() / 3600
            if abs(diff - round(diff)) < 0.01 and -12 <= diff <= 12:
                offs.append(round(diff))
    off = max(set(offs), key=offs.count) if offs else fallback_hours
    if off is None:
        return [], None
    # timed rows shift by the site offset; all-day/tentative rows (name ends with "[...]") keep their date at 00:00 UTC
    return [((ts if name.endswith("]") else ts - timedelta(hours=off)), cur, imp, name, *rest) for ts, cur, imp, name, *rest in rows], off


async def fetch_week(page, monday: date) -> list[tuple]:
    token = f"{('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec')[monday.month - 1]}{monday.day}.{monday.year}"
    await page.goto(f"https://www.forexfactory.com/calendar?week={token}", wait_until="domcontentloaded")
    await page.wait_for_selector("tr[class*='calendar__row']", state="attached", timeout=60000)  # first row is a hidden day-breaker
    count, stable = -1, 0  # event cells fill in lazily as the page scrolls: scroll until the count stops growing
    for _ in range(40):
        await page.evaluate("() => window.scrollBy(0, 900)")
        await page.wait_for_timeout(500)
        n = await page.evaluate("() => Array.from(document.querySelectorAll(\"td[class*='calendar__event']\")).filter(e => e.innerText.trim()).length")
        stable = stable + 1 if n == count else 0
        count = n
        if stable >= 3:
            break
    return parse_week(await page.evaluate(_JS), monday.year)


async def backfill(start: date, weeks: int | None) -> None:
    from playwright.async_api import async_playwright

    monday = start - timedelta(days=start.weekday())
    end = date.today() + timedelta(days=7)
    n, last_off = 0, None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        while monday <= end and (weeks is None or n < weeks):
            with db.connect() as c:
                done = c.execute("select count(*) from economic_events where ts >= %s and ts < %s",
                                 (datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc),
                                  datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc) + timedelta(days=7))).fetchone()[0]
            if done < 50 or monday >= date.today() - timedelta(days=7):  # skip complete past weeks on resume
                for attempt in range(3):
                    # fresh context per week: after the first week, a reused session no longer renders the table
                    ctx = await browser.new_context(timezone_id="UTC", locale="en-US", viewport={"width": 1400, "height": 1000},
                                                    user_agent="BensimTrading-EconomicIntelligence/1.0 (+demo-mt5-research)")  # honest UA passes; a fake browser UA gets challenged
                    try:
                        rows = await fetch_week(await ctx.new_page(), monday)
                        break
                    except Exception as exc:  # network / page change: retry, then report and continue
                        rows, err = [], exc
                        await asyncio.sleep(10 * (attempt + 1))
                    finally:
                        await ctx.close()
                else:
                    print(f"week {monday}: FAILED {err}", flush=True)
                rows, off = calibrate(rows, last_off)
                last_off = off if off is not None else last_off
                with db.connect() as c, c.cursor() as cur:
                    cur.executemany("""insert into economic_events (ts, currency, impact, name, actual, forecast, previous, source)
                                       values (%s,%s,%s,%s,%s,%s,%s,'FOREXFACTORY') on conflict (ts, currency, name) do update set
                                       impact=excluded.impact, actual=excluded.actual, forecast=excluded.forecast, previous=excluded.previous""", rows)
                    c.commit()
                print(f"week {monday}: {len(rows)} events ({sum(1 for r in rows if r[2] == 'high')} high), site offset {off:+}h" if off is not None else f"week {monday}: no anchor and no previous offset -> skipped", flush=True)
                await asyncio.sleep(1.5)  # polite pacing
            monday += timedelta(days=7)
            n += 1
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--weeks", type=int, default=None)
    a = ap.parse_args()
    db.init()
    asyncio.run(backfill(date.fromisoformat(a.start), a.weeks))


if __name__ == "__main__":
    main()
