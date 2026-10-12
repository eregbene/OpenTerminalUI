"""FTMO-style risk guard for the live engine: may this new order be sent, and at what risk scale?

Rules (FTMO 2-Step objectives: 5% max daily loss vs the 00:00 CE(S)T balance incl. floating P/L, 10% static
max loss; https://ftmo.com/en/trading-objectives/), with buffers so the engine stops BEFORE a breach:
  * worst-case day: today's loss so far + what every open trade still risks to its stop + the new trade's risk
    must stay <= daily_stop_pct of initial capital (4% of the 5% limit)
  * flatten: today's loss (equity basis) >= daily_flat_pct -> close everything, no new trades today
  * total drawdown: equity <= initial - reduce_at_pct -> half risk; <= initial - halt_at_pct -> no new trades
  * concentration: at most max_per_side open trades long (or short) the same currency, max_open trades in all
All amounts are account-currency (USD). Pure functions -- the live engine feeds them broker numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

CURRENCIES = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
              "USDCHF": ("USD", "CHF"), "USDCAD": ("USD", "CAD"), "XAUUSD": ("XAU", "USD")}


@dataclass(frozen=True)
class GuardCfg:
    daily_stop_pct: float = 4.0
    daily_flat_pct: float = 4.5
    reduce_at_pct: float = 2.5  # 2026-10-13: half risk from -2.5% (was 6%)
    halt_at_pct: float = 4.0  # 2026-10-13 brake: no new trades from -4% (was 8%; FTMO max loss is 10%)
    max_per_side: int = 2
    max_open: int = 4


@dataclass(frozen=True)
class Open:
    symbol: str
    dir: int  # +1 buy, -1 sell
    risk_usd: float  # what it still loses if its stop is hit from the current price (0 if the stop is past entry)


def legs(symbol: str, d: int) -> set[tuple[str, int]]:
    base, quote = CURRENCIES[symbol]
    return {(base, d), (quote, -d)}


def flatten_now(initial: float, day_start: float, equity: float, cfg: GuardCfg = GuardCfg()) -> bool:
    return day_start - equity >= cfg.daily_flat_pct / 100 * initial


def risk_scale(initial: float, equity: float, cfg: GuardCfg = GuardCfg()) -> float:
    dd = (initial - equity) / initial * 100
    return 0.0 if dd >= cfg.halt_at_pct else (0.5 if dd >= cfg.reduce_at_pct else 1.0)


def check(initial: float, day_start: float, equity: float, opens: list[Open], symbol: str, d: int, new_risk_usd: float,
          cfg: GuardCfg = GuardCfg()) -> tuple[bool, str]:
    if flatten_now(initial, day_start, equity, cfg):
        return False, "DAILY_FLATTEN"
    if risk_scale(initial, equity, cfg) == 0.0:
        return False, "TOTAL_DRAWDOWN_HALT"
    if len(opens) >= cfg.max_open:
        return False, "MAX_OPEN"
    if any(o.symbol == symbol for o in opens):
        return False, "SYMBOL_ALREADY_OPEN"
    used = [leg for o in opens for leg in legs(o.symbol, o.dir)]
    if any(used.count(leg) >= cfg.max_per_side for leg in legs(symbol, d)):
        return False, "CURRENCY_SIDE_FULL"
    worst = (day_start - equity) + sum(o.risk_usd for o in opens) + new_risk_usd
    if worst > cfg.daily_stop_pct / 100 * initial:
        return False, f"DAILY_RISK worst-case {worst:.0f} > {cfg.daily_stop_pct}%"
    return True, "OK"


def _selfcheck() -> None:
    i = 100_000.0
    assert check(i, i, i, [], "EURUSD", 1, 500) == (True, "OK")
    two_long_usd = [Open("USDCAD", 1, 500), Open("EURUSD", -1, 500)]
    assert check(i, i, i, two_long_usd, "XAUUSD", -1, 500)[1] == "CURRENCY_SIDE_FULL"  # third long-USD bet
    assert check(i, i, i, two_long_usd, "XAUUSD", 1, 500)[0]  # short USD is fine
    assert check(i, i, i - 3_200, [Open("GBPUSD", 1, 500)], "EURUSD", -1, 500)[1].startswith("DAILY_RISK")  # 3.2k + 0.5k + 0.5k > 4k
    assert check(i, i, i - 4_600, [], "EURUSD", 1, 500)[1] == "DAILY_FLATTEN"
    assert risk_scale(i, 97_000) == 0.5 and risk_scale(i, 95_900) == 0.0 and risk_scale(i, 99_000) == 1.0
    assert check(i, 92_000, 91_900, [], "EURUSD", 1, 100)[1] == "TOTAL_DRAWDOWN_HALT"
    assert check(i, i, i, [Open("EURUSD", 1, 0)], "EURUSD", 1, 100)[1] == "SYMBOL_ALREADY_OPEN"
    print("guard selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
