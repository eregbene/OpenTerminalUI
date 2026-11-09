"""Round 6e: seven more "outside the box" tests (written before running; nothing tuned on the result).
Common: FXCM H1 (mid = bid + half spread), costs = half the real spread at entry + half at exit + 0.3 bp slippage,
splits DISC <= 2018 / VAL 2019-22 / LOCK 2023-26, week-clustered t, grade as round 6 (r6_box.grade). Many tests ->
read single "PROMISING" results with caution; PROVEN needs significance in all three periods.

X1 FADE QUIET BREAKOUTS: round-6 T1 setup (expected-move model trained 2012-18 only; buy/sell triggers 0.5 ATR24
   either side, valid 12 bars), but when the forecast is QUIET (prediction < 1.5 x cost) the first trigger is FADED:
   sell at the upper trigger / buy at the lower, stop 1 ATR beyond, target the opposite trigger (1 ATR), exit after
   24 bars, stop first; evaluated from the bar after the fill. Controls: the same fade when the forecast is HIGH (>= 3x).
X2 ROUND NUMBERS: first touch in 24 h of a big figure (x.x000 / JPY xx0.00) from either side. FADE = bet on a bounce
   (sell from below / buy from above), stop 0.5 ATR, target 0.5 ATR, exit 12 bars; RUN = the opposite bet.
X3 TURN OF MONTH: short USD across 7 USD pairs (EURUSD GBPUSD AUDUSD NZDUSD long, USDJPY USDCHF USDCAD short) from
   00:00 UTC two trading days before month end to 00:00 UTC of the second trading day of the next month; bp per pair.
   Control: the same holding length starting mid-month (15th).
X4 PAIR DIVERGENCE: for (EURUSD, GBPUSD), (AUDUSD, NZDUSD), (EURJPY, GBPJPY): z = (6 h log return A - B) / std of
   that spread over the previous 120 h. |z| >= 2.5 -> long the laggard, short the leader (equal notional), hold 12 h,
   both legs' costs. One trade per pair-of-pairs at a time. bp.
X5 SECOND MOVE AFTER NEWS: every high-impact release with an actual value (economic_events), on the pair vs USD
   (EURUSD GBPUSD USDJPY AUDUSD NZDUSD USDCAD USDCHF; USD news on EURUSD, inverted). First reaction = the release hour's
   H1 bar (open -> close). At that bar's close: CONT = trade with the reaction, FADE = against it, hold 6 h. bp.
X6 CARRY: monthly, hold the higher-2y-yield side of EURUSD, USDJPY, USDCAD, EURJPY, CADJPY, EURCAD (yields lagged one
   day); bp = price move + yield-difference accrual (diff / 365 per day held) - one round trip per month. Note: broker
   swaps are below the yield difference (markups), so this is an upper bound.
X7 FLOW ML CHEAP MOMENTS: the flow ML walk-forward trades (data/macro/ensemble_trades.csv, bp net) split by the FXCM
   spread at entry vs that symbol-hour's median over the previous 250 days: CHEAP (<= 0.9 x median) vs the rest.
    python reports/round6/r6e_box2.py
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/bensim_phase20_dev", "D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import PAIRS, bars, by_split, grade  # noqa: E402
from skills import expected_move as EM  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
D_ = "D:/Devops/trading/OpenTerminalUI/data/"
USD = {"EURUSD": 1, "GBPUSD": 1, "AUDUSD": 1, "NZDUSD": 1, "USDJPY": -1, "USDCHF": -1, "USDCAD": -1}  # +1 = long pair is short USD


def frame(sym: str) -> pd.DataFrame | None:
    b = bars(sym)
    if b is None:
        return None  # no FXCM H1 file for this pair
    return pd.DataFrame({"t": pd.to_datetime(b["t"], unit="s"), "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"], "spr": b["spr_bp"]})


def net_bp(entry: float, exit_: float, d: int, s_in: float, s_out: float) -> float:
    return d * (exit_ - entry) / entry * 1e4 - s_in / 2 - s_out / 2 - 0.3


# ---------------------------------------------------------------- X1
def x1() -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    d, _ = EM.dataset()
    d = d[d.symbol != "XAUUSD"]
    tr = d[(d.year <= 2018) & d.y12.notna()].iloc[::3]
    m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **EM.HGB).fit(tr[EM.FEATS], tr.y12)
    rows = {"FADE_QUIET": [], "FADE_HIGH": []}
    for sym in PAIRS:
        b = bars(sym)
        if b is None:
            continue
        f = EM.features(b, sym)
        ok = f[EM.FEATS].notna().all(axis=1).to_numpy()
        pred = np.full(len(f), np.nan)
        pred[ok] = m.predict(f.loc[ok, EM.FEATS])
        h, lo, c, sp = b["high"], b["low"], b["close"], b["spr_bp"]
        A_ = pd.Series(h - lo).rolling(24, min_periods=12).mean().to_numpy()
        ratio = pred / (sp + 0.3)
        n = len(c)
        for kind, cond in (("FADE_QUIET", ratio < 1.5), ("FADE_HIGH", ratio >= 3)):
            busy = -1
            for i in np.flatnonzero(cond & np.isfinite(ratio) & np.isfinite(A_)):
                if i <= busy or i + 40 >= n:
                    continue
                A = A_[i]
                up, dn = c[i] + 0.5 * A, c[i] - 0.5 * A
                fill, side = None, 0
                for j in range(i + 1, i + 13):
                    hu, hd = h[j] >= up, lo[j] <= dn
                    if hu and hd:
                        fill, side = j, 0
                        break
                    if hu or hd:
                        fill, side = j, (-1 if hu else 1)  # FADE: sell the upper trigger, buy the lower
                        break
                if fill is None or side == 0:
                    if fill is not None:  # both triggers in one bar: unknowable order -> count as a loss
                        rows[kind].append({"t": pd.Timestamp(int(b["t"][i]) + 3600, unit="s"), "r": -1.0 - (sp[fill] + 0.3) / (A / c[i] * 1e4)})
                        busy = fill
                    continue
                entry = up if side < 0 else dn
                stop, tgt = entry - side * A, entry + side * A
                r, k = None, fill
                for k in range(fill + 1, min(fill + 25, n)):
                    if (side > 0 and lo[k] <= stop) or (side < 0 and h[k] >= stop):
                        r = -1.0
                        break
                    if (side > 0 and h[k] >= tgt) or (side < 0 and lo[k] <= tgt):
                        r = 1.0
                        break
                if r is None:
                    r = side * (c[k] - entry) / A
                rows[kind].append({"t": pd.Timestamp(int(b["t"][i]) + 3600, unit="s"), "r": r - (sp[fill] / 2 + sp[k] / 2 + 0.3) / (A / c[i] * 1e4)})
                busy = k
        print("X1", sym, {k: len(v) for k, v in rows.items()}, flush=True)
    return {k: by_split(pd.DataFrame(v)) for k, v in rows.items()}


# ---------------------------------------------------------------- X2
def x2() -> dict:
    out = {"FADE": [], "RUN": []}
    for sym in PAIRS:
        F = frame(sym)
        if F is None:
            continue
        step = 1.0 if sym.endswith("JPY") else 0.01
        A_ = (F.h - F.l).rolling(24, min_periods=12).mean().to_numpy()
        h, lo, c, sp, t = F.h.to_numpy(), F.l.to_numpy(), F.c.to_numpy(), F.spr.to_numpy(), F.t
        last_touch: dict[float, int] = {}
        busy = -1
        for i in range(25, len(c) - 13):
            lvl_lo, lvl_hi = np.floor(lo[i] / step) * step + step, np.floor(h[i] / step) * step  # big figures inside the bar
            if lvl_hi < lvl_lo or i <= busy or not np.isfinite(A_[i]):
                continue
            lvl = round(lvl_lo, 4)
            if i - last_touch.get(lvl, -10**9) < 24:
                last_touch[lvl] = i
                continue
            last_touch[lvl] = i
            from_below = c[i - 1] < lvl
            A = A_[i]
            for kind, side in (("FADE", -1 if from_below else 1), ("RUN", 1 if from_below else -1)):
                stop, tgt = lvl - side * 0.5 * A, lvl + side * 0.5 * A
                r, k = None, i
                for k in range(i + 1, i + 13):
                    if (side > 0 and lo[k] <= stop) or (side < 0 and h[k] >= stop):
                        r = -1.0
                        break
                    if (side > 0 and h[k] >= tgt) or (side < 0 and lo[k] <= tgt):
                        r = 1.0
                        break
                if r is None:
                    r = side * (c[k] - lvl) / (0.5 * A)
                out[kind].append({"t": t.iat[i], "r": r - (sp[i] / 2 + sp[k] / 2 + 0.3) / (0.5 * A / lvl * 1e4)})
            busy = i + 1
        print("X2", sym, len(out["FADE"]), flush=True)
    return {k: by_split(pd.DataFrame(v)) for k, v in out.items()}


# ---------------------------------------------------------------- X3
def x3() -> dict:
    days = pd.bdate_range("2012-01-01", "2026-10-31")
    s = pd.Series(days)
    ym = s.dt.to_period("M")
    last = s.groupby(ym).transform("max")
    rank_from_end = s.groupby(ym).cumcount(ascending=False)
    rank_from_start = s.groupby(ym).cumcount()
    entries = s[rank_from_end == 1].to_list()            # 2 trading days before month end (the day before the last)
    exits = s[rank_from_start == 1].to_list()            # 2nd trading day of the month
    exits_map = {e.to_period("M"): e for e in exits}
    res ={"TOM": [], "CONTROL_mid_month": []}
    frames = {p: frame(p).set_index("t") for p in USD}
    for e in entries:
        nxt = (e.to_period("M") + 1)
        x = exits_map.get(nxt)
        if x is None:
            continue
        hold = x - e
        mid = pd.Timestamp(e.year, e.month, 15)
        while mid.weekday() >= 5:
            mid += timedelta(days=1)
        for lab, a, z in (("TOM", e, x), ("CONTROL_mid_month", mid, mid + hold)):
            vals = []
            for p, sgn in USD.items():
                F = frames[p]
                ia, iz = F.index.searchsorted(a), F.index.searchsorted(z)
                if ia >= len(F) or iz >= len(F):
                    continue
                vals.append(net_bp(F.o.iat[ia], F.o.iat[iz], sgn, F.spr.iat[ia], F.spr.iat[iz]))
            if vals:
                res[lab].append({"t": a, "r": float(np.mean(vals))})
    return {k: by_split(pd.DataFrame(v)) for k, v in res.items()}


# ---------------------------------------------------------------- X4
def x4() -> dict:
    res = {}
    for a, b_ in (("EURUSD", "GBPUSD"), ("AUDUSD", "NZDUSD"), ("EURJPY", "GBPJPY")):
        A, B = frame(a).set_index("t"), frame(b_).set_index("t")
        J = A.join(B, lsuffix="_a", rsuffix="_b", how="inner")
        la, lb = np.log(J.c_a), np.log(J.c_b)
        sp6 = (la - la.shift(6)) - (lb - lb.shift(6))
        z = (sp6 / sp6.rolling(120, min_periods=60).std().shift(1)).to_numpy()
        rows, busy = [], -1
        ca, cb, sa, sb = J.c_a.to_numpy(), J.c_b.to_numpy(), J.spr_a.to_numpy(), J.spr_b.to_numpy()
        for i in np.flatnonzero(np.abs(np.nan_to_num(z)) >= 2.5):
            if i <= busy or i + 12 >= len(J):
                continue
            d = -int(np.sign(z[i]))  # z > 0: A outran B -> short A, long B
            k = i + 12
            r = (net_bp(ca[i], ca[k], d, sa[i], sa[k]) + net_bp(cb[i], cb[k], -d, sb[i], sb[k])) / 2
            rows.append({"t": J.index[i], "r": r})
            busy = k
        res[f"{a}-{b_}"] = by_split(pd.DataFrame(rows))
        res[f"{a}-{b_}"]["n_all"] = len(rows)
    return res


# ---------------------------------------------------------------- X5
def x5() -> dict:
    from bensim import db
    with db.connect() as c:
        ev = pd.DataFrame(c.execute("select ts, currency from economic_events where impact='high' and actual is not null and ts >= '2012-01-01'").fetchall(),
                          columns=["ts", "currency"])
    ev["ts"] = pd.to_datetime(ev.ts, utc=True).dt.tz_localize(None)
    sym_of = {"EUR": ("EURUSD", 1), "GBP": ("GBPUSD", 1), "JPY": ("USDJPY", 1), "AUD": ("AUDUSD", 1), "NZD": ("NZDUSD", 1), "CAD": ("USDCAD", 1),
              "CHF": ("USDCHF", 1), "USD": ("EURUSD", 1)}
    frames = {}
    rows = {"CONT": [], "FADE": []}
    for (ts, ccy), _ in ev.groupby(["ts", "currency"]):
        if ccy not in sym_of:
            continue
        sym = sym_of[ccy][0]
        if sym not in frames:
            frames[sym] = frame(sym).set_index("t")
        F = frames[sym]
        i = F.index.searchsorted(ts.floor("h"))
        if i >= len(F) - 7 or F.index[i] != ts.floor("h"):
            continue
        react = np.sign(F.c.iat[i] - F.o.iat[i])
        if react == 0:
            continue
        for kind, d in (("CONT", int(react)), ("FADE", -int(react))):
            rows[kind].append({"t": ts, "r": net_bp(F.c.iat[i], F.c.iat[i + 6], d, F.spr.iat[i], F.spr.iat[i + 6])})
    return {k: by_split(pd.DataFrame(v).drop_duplicates("t")) for k, v in rows.items()}


# ---------------------------------------------------------------- X6
def x6() -> dict:
    y = pd.read_csv(D_ + "macro/yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    pairs = {"EURUSD": ("EUR", "USD"), "USDJPY": ("USD", "JPY"), "USDCAD": ("USD", "CAD"), "EURJPY": ("EUR", "JPY"), "CADJPY": ("CAD", "JPY"), "EURCAD": ("EUR", "CAD")}
    rows = []
    for sym, (b_, q) in pairs.items():
        if b_ not in y or q not in y:
            continue
        F = frame(sym)
        if F is None:
            continue
        F = F.set_index("t")
        M = F.resample("MS").first().dropna()
        for a, z in zip(M.index[:-1], M.index[1:]):
            yd = y.loc[:a].iloc[-1] if len(y.loc[:a]) else None
            if yd is None or pd.isna(yd[b_]) or pd.isna(yd[q]):
                continue
            diff = float(yd[b_] - yd[q])
            d = 1 if diff > 0 else -1
            days = (z - a).days
            r = net_bp(M.o.loc[a], M.o.loc[z], d, M.spr.loc[a], M.spr.loc[z]) + abs(diff) * 100 * days / 365  # % -> bp
            rows.append({"t": a, "sym": sym, "r": r})
    R = pd.DataFrame(rows)
    basket = R.groupby("t").r.mean().reset_index()
    return {"CARRY_basket_per_month": by_split(basket), "per_pair_mean_bp": R.groupby("sym").r.mean().round(2).to_dict()}


# ---------------------------------------------------------------- X7
def x7() -> dict:
    e = pd.read_csv(D_ + "macro/ensemble_trades.csv", parse_dates=["t"])
    out = []
    for sym, g in e.groupby("symbol"):
        F = frame(sym)
        F["hour"] = F.t.dt.hour
        F["med"] = F.groupby("hour").spr.transform(lambda s: s.rolling(250, min_periods=60).median().shift(1))
        F = F.set_index("t")
        j = F.index.searchsorted(g.t.to_numpy())
        ok = j < len(F)
        g = g[ok].copy()
        g["spr"], g["med"] = F.spr.to_numpy()[j[ok]], F.med.to_numpy()[j[ok]]
        out.append(g)
    E = pd.concat(out).dropna(subset=["med"])
    E["cheap"] = E.spr <= 0.9 * E.med
    return {"CHEAP": by_split(E[E.cheap].rename(columns={"bp": "r"})), "REST": by_split(E[~E.cheap].rename(columns={"bp": "r"})),
            "share_cheap": round(float(E.cheap.mean()), 3)}


def main() -> None:
    only = [a for a in sys.argv[1:] if a.startswith("X")]
    try:
        res = json.load(open(OUT + "r6e_results.json")) if only else {}
    except FileNotFoundError:
        res = {}
    res.pop("_grades", None)
    for name, fn in (("X3_turn_of_month", x3), ("X4_pair_divergence", x4), ("X5_news_second_move", x5), ("X6_carry", x6),
                     ("X7_flowml_cheap", x7), ("X2_round_numbers", x2), ("X1_fade_quiet_breakouts", x1)):
        if only and name.split("_")[0] not in only:
            continue
        try:
            res[name] = fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            res[name] = {"error": repr(exc), "trace": traceback.format_exc()[-600:]}
        json.dump(res, open(OUT + "r6e_results.json", "w"), indent=1, default=str)
        print("DONE", name, flush=True)
    grades = {}
    for name, v in res.items():
        for k, x in v.items():
            if isinstance(x, dict) and "DISC" in x:
                grades[f"{name}:{k}"] = grade(x)
    res["_grades"] = grades
    json.dump(res, open(OUT + "r6e_results.json", "w"), indent=1, default=str)
    print(json.dumps(grades, indent=1))


if __name__ == "__main__":
    main()
