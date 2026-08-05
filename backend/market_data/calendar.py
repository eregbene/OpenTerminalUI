from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.shared.market_calendar import SESSIONS, is_market_open, next_market_open
from backend.market_data.resampling import timeframe_delta


@dataclass(frozen=True)
class SessionState:
    venue: str
    name: str
    is_open: bool
    opened_at: datetime | None
    closes_at: datetime | None
    next_open: datetime


class MarketCalendar:
    def __init__(self, venue: str) -> None:
        self.venue = venue.upper()

    def is_market_open(self, ts: datetime | None = None) -> bool:
        if self.venue in {"CRYPTO"}:
            return True
        if self.venue in {"FOREX", "FX"}:
            value = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
            return value.weekday() < 5
        return is_market_open(self.venue, ts)

    def current_session(self, ts: datetime | None = None) -> SessionState:
        value = ts or datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if self.venue in {"CRYPTO"}:
            return SessionState(self.venue, "continuous", True, None, None, value)
        opened = self.is_market_open(value)
        nxt = self.next_open(value)
        close = self.next_close(value) if opened else None
        return SessionState(self.venue, "regular" if opened else "closed", opened, None, close, nxt)

    def next_open(self, ts: datetime | None = None) -> datetime:
        value = ts or datetime.now(timezone.utc)
        if self.venue in {"CRYPTO"}:
            return value.astimezone(timezone.utc)
        if self.venue in {"FOREX", "FX"}:
            current = value.astimezone(timezone.utc)
            while current.weekday() >= 5:
                current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return current
        return next_market_open(self.venue, value).astimezone(timezone.utc)

    def next_close(self, ts: datetime | None = None) -> datetime | None:
        if self.venue in {"CRYPTO"}:
            return None
        session = SESSIONS.get(self.venue)
        if session is None:
            return None
        value = (ts or datetime.now(timezone.utc)).astimezone(session.tz)
        candidate = value.replace(
            hour=session.close_time.hour,
            minute=session.close_time.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= value:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def session_for_timestamp(self, ts: datetime) -> SessionState:
        return self.current_session(ts)

    def expected_bar_timestamps(self, start: datetime, end: datetime, timeframe: str) -> list[datetime]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        step = timeframe_delta(timeframe)
        out: list[datetime] = []
        cursor = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        while cursor < end_utc:
            if self.is_market_open(cursor) or self.venue in {"CRYPTO", "FOREX", "FX"}:
                out.append(cursor)
            cursor += step
        return out
