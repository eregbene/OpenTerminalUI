# Market Calendars

Implemented wrapper: `backend/market_data/calendar.py`.

It reuses existing `backend/shared/market_calendar.py` sessions and holidays where available.

Supported or approximated:

- NSE/BSE/NFO
- NASDAQ/NYSE
- CME approximation from existing shared calendar
- crypto continuous trading
- forex weekday 24/5 approximation

Unsupported calendars remain documented as approximate; no invented paid calendars were added.
