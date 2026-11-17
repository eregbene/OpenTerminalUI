"""Fixed monetary risk per trade -> lot size per trade (FXTM pip-calculator method, broker-exact).

    python -m bensim.sizing demo [RISK_USD]     # live DEMO quotes: EURUSD GBPJPY AUDUSD USDJPY, same $ risk, lots differ
    python -m bensim.sizing history [RISK_USD]  # last 3 months of H1 bars: historical rates -> lots + worst-case loss

pip value per 1.0 lot (account ccy) = pip size / tick size x tick value. MT5's trade_tick_value is already converted to the
account currency at the CURRENT rate (USDJPY: 0.001 x 100,000 / 157.96 = 0.633 $), which is exactly the FXTM formula
pip x contract size / (quote -> account rate); `fxtm_pip_value` recomputes it independently as a cross-check.

loss per lot if the stop is hit = (stop pips + spread pips + 2 x slippage pips) x pip value + round-trip commission.
lots = risk / loss per lot, rounded DOWN to the volume step, capped at volume_max (also rounded down);
below volume_min -> rejected (never rounded up). Estimated total loss = lots x loss per lot <= the risk budget.

Settings (env, read once): BENSIM_RISK_USD (fixed $ per trade), BENSIM_COMMISSION_PER_LOT_RT (0: this demo charges none,
measured on 205 deals 2026-09/11), BENSIM_SLIPPAGE_PIPS (per side, 0.5 = an estimate, not measured).
WIRED 2026-11-09 (user GO) via live.size_trade: fib618 + the books $500 each, Bennett $1,000 (live.BUDGETS),
the manager (its 0.25%). Flow ML/AM keep live.lots_for (% of equity at the disaster stop).
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

RISK_USD = float(os.environ.get("BENSIM_RISK_USD", "500"))
COMMISSION_RT = float(os.environ.get("BENSIM_COMMISSION_PER_LOT_RT", "0"))
SLIPPAGE_PIPS = float(os.environ.get("BENSIM_SLIPPAGE_PIPS", "0.5"))


@dataclass(frozen=True)
class Costs:
    commission_per_lot_rt: float = COMMISSION_RT
    slippage_pips: float = SLIPPAGE_PIPS  # per side: entry and stop fill


def pip_size(q: dict) -> float:
    return q["point"] * (10 if q["digits"] in (3, 5) else 1)  # 5-digit majors, 3-digit JPY; metals/indices: 1 point


def _decimals(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _down(x: float, step: float) -> float:
    return round(math.floor(x / step + 1e-9) * step, _decimals(step))


def size(q: dict, entry: float, stop: float, risk_usd: float, costs: Costs = Costs()) -> dict:
    """q = bridge.tick(symbol) (bid, ask, point, digits, trade_tick_value/size, volume_min/max/step)."""
    pip = pip_size(q)
    pip_value = pip / q["trade_tick_size"] * q["trade_tick_value"]  # account ccy per pip per 1.0 lot
    stop_pips = abs(entry - stop) / pip
    spread_pips = max(0.0, (q["ask"] - q["bid"]) / pip)
    loss_per_lot = (stop_pips + spread_pips + 2 * costs.slippage_pips) * pip_value + costs.commission_per_lot_rt
    out = dict(symbol=q.get("symbol"), risk_usd=round(risk_usd, 2), stop_pips=round(stop_pips, 1),
               spread_pips=round(spread_pips, 2), slippage_pips=costs.slippage_pips,
               commission_rt=costs.commission_per_lot_rt, pip_value=round(pip_value, 4),
               loss_per_lot=round(loss_per_lot, 2), lots=0.0, est_loss=0.0, ok=False, reason="")
    if stop_pips <= 0 or pip_value <= 0 or risk_usd <= 0:
        return {**out, "reason": "no stop distance / pip value / risk"}
    step = q["volume_step"]
    lots = min(_down(risk_usd / loss_per_lot, step), _down(q["volume_max"], step))
    if lots < q["volume_min"]:
        return {**out, "reason": f"min lot {q['volume_min']} would lose {q['volume_min'] * loss_per_lot:.2f} > risk {risk_usd:.2f}"}
    return {**out, "lots": lots, "est_loss": round(lots * loss_per_lot, 2), "ok": True,
            "reason": "capped at volume_max" if lots == _down(q["volume_max"], step) else "ok"}


def log_line(d: dict) -> str:
    return (f"SIZE {d['symbol']} risk ${d['risk_usd']:.2f} stop {d['stop_pips']} pips (+spread {d['spread_pips']} "
            f"+slip 2x{d['slippage_pips']}) pip value ${d['pip_value']:.4f}/lot -> {d['lots']} lots, "
            f"est. total loss ${d['est_loss']:.2f} [{d['reason']}]")


def fxtm_pip_value(symbol: str, usd_rates: dict[str, float], pip: float, contract: float = 100_000) -> float:
    """FXTM: pip x contract / (quote ccy -> USD). usd_rates: price of 1 USD in that ccy (JPY 157.96, CAD 1.41, ...)."""
    quote = symbol[3:6]
    return pip * contract if quote == "USD" else pip * contract / usd_rates[quote]


def _demo(risk: float) -> None:
    from . import bridge
    jpy = bridge.tick("USDJPY")
    usd_rates = {"JPY": (jpy["bid"] + jpy["ask"]) / 2}
    print(f"fixed risk ${risk:.2f} per trade, account {bridge.account().get('currency')}, live DEMO quotes\n")
    for stop_desc, stop_of in (("20-pip stop", lambda q, px: 20 * pip_size(q)), ("Flow ML disaster stop 145 bp", lambda q, px: px * 0.0145)):
        print(f"== {stop_desc}")
        for s in ("EURUSD", "GBPJPY", "AUDUSD", "USDJPY"):
            q = bridge.tick(s)
            d = size(q, q["ask"], q["ask"] - stop_of(q, q["ask"]), risk)
            print(f"  {log_line(d)}   (FXTM check pip value ${fxtm_pip_value(s, usd_rates, pip_size(q)):.4f})")
        print()


def _history(risk: float) -> None:
    """3 months of H1 closes: at every 10:00 UTC bar, a buy with a 20-pip and a 145-bp stop sized with THAT hour's rates
    (pip value from the FXTM formula on historical USDJPY), the bar's own spread, 0.01/0.01..100 lot limits."""
    import numpy as np

    from . import bridge
    from .mt5_time import server_to_utc
    bars = {}
    for s in ("EURUSD", "GBPJPY", "AUDUSD", "USDJPY"):
        rows = sorted(bridge.rates(s, "H1", 1, 24 * 66), key=lambda r: r["time"])
        bars[s] = {int(t): r for t, r in zip(server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64)), rows)}
    hours = sorted(t for t in bars["USDJPY"] if t % 86400 == 10 * 3600)
    print(f"fixed risk ${risk:.2f}; {len(hours)} days at 10:00 UTC, {min(hours) and __import__('time').strftime('%Y-%m-%d', __import__('time').gmtime(hours[0]))}"
          f" .. {__import__('time').strftime('%Y-%m-%d', __import__('time').gmtime(hours[-1]))}")
    for stop_desc, stop_of in (("20-pip stop", lambda pip, px: 20 * pip), ("145-bp stop", lambda pip, px: px * 0.0145)):
        print(f"== {stop_desc}")
        for s in bars:
            lots, losses, rejects = [], [], 0
            for t in hours:
                r = bars[s].get(t)
                if not r or t not in bars["USDJPY"]:
                    continue
                digits = 3 if s.endswith("JPY") else 5
                point = 10.0 ** -digits
                pip = point * 10
                pv = fxtm_pip_value(s, {"JPY": bars["USDJPY"][t]["close"]}, pip)
                q = dict(symbol=s, bid=r["close"], ask=r["close"] + r.get("spread", 0) * point, point=point, digits=digits,
                         trade_tick_size=point, trade_tick_value=pv / 10, volume_min=0.01, volume_max=100.0, volume_step=0.01)
                d = size(q, q["ask"], q["ask"] - stop_of(pip, q["ask"]), risk)
                if not d["ok"]:
                    rejects += 1
                    continue
                lots.append(d["lots"])
                losses.append(d["est_loss"])
            print(f"  {s}: {len(lots)} trades  lots {min(lots):.2f}..{max(lots):.2f} (median {sorted(lots)[len(lots) // 2]:.2f})  "
                  f"est. loss ${min(losses):.2f}..${max(losses):.2f}  max over budget: {max(losses) > risk}  rejected {rejects}")
        print()


if __name__ == "__main__":
    risk = float(sys.argv[2]) if len(sys.argv) > 2 else RISK_USD
    if sys.argv[1:2] == ["demo"]:
        _demo(risk)
    elif sys.argv[1:2] == ["history"]:
        _history(risk)
    else:
        sys.exit(__doc__)
