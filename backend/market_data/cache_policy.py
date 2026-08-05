from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CachePolicy:
    data_type: str
    ttl_seconds: int
    stale_while_revalidate_seconds: int = 0
    negative_ttl_seconds: int = 0
    may_use_for_trading_decisions: bool = False
    persistence: str = "redis"
    acceptable_staleness_seconds: int = 0

    def key(self, *parts: object) -> str:
        safe = ":".join(str(part).strip().lower().replace(" ", "_") for part in parts if part is not None and str(part).strip())
        return f"market_data:{self.data_type}:{safe}"


CACHE_POLICIES: dict[str, CachePolicy] = {
    "quote": CachePolicy("quote", ttl_seconds=5, stale_while_revalidate_seconds=15, acceptable_staleness_seconds=15),
    "order_book": CachePolicy("order_book", ttl_seconds=2, stale_while_revalidate_seconds=5),
    "intraday_bars": CachePolicy("intraday_bars", ttl_seconds=60, stale_while_revalidate_seconds=300, acceptable_staleness_seconds=300),
    "daily_bars": CachePolicy("daily_bars", ttl_seconds=3600, stale_while_revalidate_seconds=86400, acceptable_staleness_seconds=86400),
    "instrument": CachePolicy("instrument", ttl_seconds=86400, stale_while_revalidate_seconds=604800, acceptable_staleness_seconds=604800),
    "fundamentals": CachePolicy("fundamentals", ttl_seconds=86400, stale_while_revalidate_seconds=604800),
    "economic": CachePolicy("economic", ttl_seconds=21600, stale_while_revalidate_seconds=86400),
    "news": CachePolicy("news", ttl_seconds=300, stale_while_revalidate_seconds=900),
    "options_chain": CachePolicy("options_chain", ttl_seconds=30, stale_while_revalidate_seconds=120),
    "market_calendar": CachePolicy("market_calendar", ttl_seconds=86400, stale_while_revalidate_seconds=604800),
}


def get_cache_policy(data_type: str) -> CachePolicy:
    return CACHE_POLICIES[data_type]
