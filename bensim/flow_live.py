"""Flow/rate book, live (DEMO): the rate-aware ML model + the EURUSD European-morning short, run from live.cycle().

    python -m bensim.flow_live train          # train the frozen models for this year (5 seeds x long/short)
    python -m bensim.flow_live check          # one dry decision now (no orders), printing what it sees
    BENSIM_FLOW=off|dry|live                  # in the engine; default live since 2026-10-19 (dry = log only)

Exactly the research rules (bensim.macro_yields ensemble + bensim.combo), nothing re-tuned:
  ML     every new H1 close: inputs = bensim.edge_miner.features + add_strength over the 25 pairs (MT5 H1 bars, UTC) +
         2-year yield gaps (data/macro/yields_2y.csv, lagged one day); the spread input = the FXCM median spread of that
         pair and hour (what the model was trained on, not the broker's). Prediction = average of 5 seeds; trade the pair
         when its best side > the threshold (95th percentile of the previous year's predictions). Pairs EURUSD USDJPY
         USDCAD EURJPY CADJPY, one open trade per pair, closed after 6 hours, BENSIM_FLOW_ML_LOTS (0.80).
  MORNING  weekdays: sell EURUSD at the first cycle from 09:00 UTC, close from 12:00 UTC, BENSIM_FLOW_AM_LOTS (2.5).
  Safety   own magic 770103; a disaster stop 4 typical moves away (ML 145 bp, morning 68 bp); no new orders when the
         kill switch file exists, the guard's daily flatten / drawdown halt applies, or the inputs are stale; every decision
         is written to live_flow (unique per kind + symbol + decision time: never sent twice); yields refreshed daily in
         a background thread. Models are retrained once a year (train <= Y-2, threshold from Y-1) -- WARN if outdated.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import bridge, db, guard, news_block
from . import edge_miner as em
from . import macro_yields as my
from .fxcm_history import PAIRS as ALL28
from .mt5_time import server_to_utc, utc_to_server

MAGIC = 770103
MODE = os.environ.get("BENSIM_FLOW", "live").lower()  # 2026-10-19 user: live on the demo
ML_LOTS = float(os.environ.get("BENSIM_FLOW_ML_LOTS", "0.80"))
AM_LOTS = float(os.environ.get("BENSIM_FLOW_AM_LOTS", "2.5"))
PAIRS = my.PAIRS  # the 5 pairs with yields on both sides
UNIVERSE = [p for p in ALL28 if p not in ("EURCAD", "GBPAUD", "CHFJPY")]  # the 25 pairs the model learned strength from
MODELS = Path("data/flow_models")
FEATS = em.FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
HOLD_ML, HOLD_AM = 6 * 3600, 3 * 3600
# 2026-11-06 user GO (Stage 4 items 1 + 2). RISK_PCT > 0: lots = that % of equity at the disaster stop (broker tick
# value / size, rounded down to the lot step; no broker spec -> no order); 0 = the old fixed lots above.
RISK_PCT = float(os.environ.get("BENSIM_FLOW_RISK_PCT", "0.5"))
# ML: no new entries at these UTC decision hours (rollover spreads; 2-year advisor backtest -4.0 vs +2.6 bp, p 0.009)
ML_SKIP_UTC = {int(h) for h in os.environ.get("BENSIM_FLOW_ML_SKIP_UTC", "20,21,22,23,0").split(",") if h.strip() != ""}
# ML: enter only while short-term volatility expands: mean H1 range of the last 6 closed bars / last 48 > this (0 = off).
# Round 8/9 research (reports/round8 MORNING_REPORT, round9 REPORT): live 5 walk-forward 2016-26 +4.32 vs +1.44 bp, 11/11 years.
ML_ATR_MIN = float(os.environ.get("BENSIM_FLOW_ML_ATR_MIN", "1.2"))
STOP_BP = {"ml": 145.0, "am": 68.0}
SEEDS = (0, 7, 13, 21, 42)
SCHEMA = """create table if not exists live_flow (
  id bigserial primary key, created timestamptz default now(), kind text, symbol text, decision_time bigint,
  dir int, pred double precision, thr double precision, decision text, detail jsonb,
  unique (kind, symbol, decision_time))"""


def add_gaps(f: pd.DataFrame) -> pd.DataFrame:
    """2-year yield gap inputs, lagged one business day (identical to the research code)."""
    y = pd.read_csv(my.OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for c in ("level", "d5", "d20", "d60"):
        f[f"gap_{c}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b in y and q in y:
            g, k = y[b] - y[q], f.symbol == sym
            f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
            for n in (5, 20, 60):
                f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(day[k], method="ffill").to_numpy()
    return f


def train(year: int | None = None) -> Path:
    """Models for trading `year`: trained on every pair, years <= year-2; threshold = q95 of the best-side ensemble
    prediction on year-1 for the 5 pairs; plus the FXCM median spread per pair and UTC hour (the spread input)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    year = year or datetime.now(timezone.utc).year
    with open(em.PATH, "rb") as fh:  # our own cache (bensim.edge_miner build)
        f = add_gaps(pickle.load(fh))
    tr = f[f.year < year - 1]
    tr = tr[np.isfinite(tr.long_6) & np.isfinite(tr.short_6)]
    prev = f[(f.year == year - 1) & f.symbol.isin(PAIRS)]
    models, pl, ps = [], 0.0, 0.0
    for s in SEEDS:
        mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                      min_samples_leaf=2000, l2_regularization=1.0, random_state=s).fit(tr[FEATS], yy)
        ml, ms = mk(np.clip(tr.long_6, -300, 300)), mk(np.clip(tr.short_6, -300, 300))
        models.append((ml, ms))
        pl, ps = pl + ml.predict(prev[FEATS]) / len(SEEDS), ps + ms.predict(prev[FEATS]) / len(SEEDS)
        print(f"  seed {s} trained", flush=True)
    thr = float(np.quantile(np.maximum(pl, ps), 0.95))
    recent = f[f.year >= year - 2]
    spr = recent.groupby(["symbol", "hour"]).spr.median().to_dict()
    MODELS.mkdir(parents=True, exist_ok=True)
    out = MODELS / f"flow_{year}.pkl"
    with open(out, "wb") as fh:
        pickle.dump({"year": year, "models": models, "thr": thr, "spread_bp": spr, "feats": FEATS,
                     "trained_on": f"<= {year - 2}", "threshold_from": year - 1}, fh)
    print(f"saved {out}: threshold {thr:.2f} bp (q95 of {year - 1})")
    return out


_MODEL: dict | None = None


def model() -> dict:
    global _MODEL
    if _MODEL is None:
        files = sorted(MODELS.glob("flow_*.pkl"))
        if not files:
            raise RuntimeError("no flow model -- run: python -m bensim.flow_live train")
        with open(files[-1], "rb") as fh:  # our own file written by train()
            _MODEL = pickle.load(fh)
    return _MODEL


def h1(sym: str, n: int = 700) -> dict[str, np.ndarray]:
    bridge.tick(sym)  # wakes a symbol MT5 is not streaming yet (else its bars can lag by hours)
    # from position 0 and keep closed bars by time: position 1 is one bar too old until the new hour's first tick
    rows = sorted(bridge.rates(sym, "H1", 0, n + 1), key=lambda r: r["time"])
    t = server_to_utc(np.array([r["time"] for r in rows], dtype=np.int64))
    done = t + 3600 <= time.time()
    return {"t": t[done], **{k: np.array([r[k] for r in rows], dtype=float)[done] for k in ("open", "high", "low", "close")}}


def atr_ratio(high: np.ndarray, low: np.ndarray) -> float:
    """Mean range of the last 6 closed bars / last 48 (NaN when fewer than 48 bars)."""
    r = np.asarray(high, dtype=float) - np.asarray(low, dtype=float)
    return float(r[-6:].mean() / r[-48:].mean()) if len(r) >= 48 and r[-48:].mean() > 0 else float("nan")


def scores(now: float) -> tuple[pd.DataFrame, list[str]]:
    """Ensemble predictions for the 5 pairs at the latest H1 close; returns (rows, stale pairs)."""
    mdl = model()
    hour_open = int(now // 3600) * 3600 - 3600  # the H1 bar that just closed
    frames, stale = [], []
    for s in UNIVERSE:
        m = h1(s)
        if not len(m["t"]) or int(m["t"][-1]) != hour_open:
            stale.append(s)
            continue
        hrs = ((m["t"] + 3600) // 3600 % 24).astype(int)
        spr_bp = np.array([mdl["spread_bp"].get((s, h), np.nan) for h in hrs])
        m["spr"] = spr_bp / 1e4 * m["close"]  # the spread the model was trained with (FXCM median, this pair/hour)
        frames.append(em.features(s, m))
    fresh = {s for s in UNIVERSE if s not in stale}
    if not frames or any(not any(c in (p[:3], p[3:]) for p in fresh) for c in em.CCY):
        # market closed, or a currency has no fresh pair (e.g. just after a restart): strength can't be built -> skip hour
        return pd.DataFrame(), stale
    f = em.add_strength(pd.concat(frames, ignore_index=True))
    f = add_gaps(f[f.t == pd.Timestamp(hour_open + 3600, unit="s")].copy())
    f = f[f.symbol.isin(PAIRS)].copy()
    pl = np.mean([ml.predict(f[mdl["feats"]]) for ml, _ in mdl["models"]], axis=0) if len(f) else np.array([])
    ps = np.mean([ms.predict(f[mdl["feats"]]) for _, ms in mdl["models"]], axis=0) if len(f) else np.array([])
    return f.assign(pl=pl, ps=ps), stale


def _record(kind: str, sym: str, dt: int, d: int, pred: float, thr: float, decision: str, detail: dict) -> bool:
    with db.connect(autocommit=True) as c:
        c.execute(SCHEMA)
        return bool(c.execute("""insert into live_flow (kind, symbol, decision_time, dir, pred, thr, decision, detail)
                                 values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing returning 1""",
                              (kind, sym, dt, d, pred, thr, decision, json.dumps(detail, default=str))).fetchone())


def _send(kind: str, sym: str, d: int, lots: float, hold: int, log, dry: bool, gate=None) -> dict:
    q = bridge.tick(sym)
    px = q["ask"] if d == 1 else q["bid"]
    sl = round(px * (1 - d * STOP_BP[kind] / 1e4), int(q["digits"]))
    if gate is not None:  # risk-based size + the shared guard (open-risk cap, currency sides, ...)
        from .live import lots_for
        equity, check = gate
        if RISK_PCT > 0:
            if not q.get("trade_tick_value") or not q.get("trade_tick_size") or not q.get("volume_step"):
                return {"price": px, "sl": sl, "lots": 0.0, "result": {"ok": False, "comment": "no broker spec -> no order"}, "skip": "SKIP_NO_SPEC"}
            lots = lots_for(q, px, sl, equity * RISK_PCT / 100)
            if lots < (q.get("volume_min") or 0.01):
                return {"price": px, "sl": sl, "lots": lots, "result": {"ok": False, "comment": "below min lot"}, "skip": "SKIP_MIN_LOT"}
        risk = abs(px - sl) / q["trade_tick_size"] * q["trade_tick_value"] * lots if q.get("trade_tick_size") else None
        ok, why = check(sym, d, risk)
        if not ok:
            return {"price": px, "sl": sl, "lots": lots, "risk_usd": risk, "result": {"ok": False, "comment": why}, "skip": f"SKIP_GUARD_{why.split()[0]}"}
    if dry:
        return {"dry": True, "price": px, "sl": sl, "lots": lots}
    res = bridge.order(sym, "BUY" if d == 1 else "SELL", lots, sl, 0.0, MAGIC, f"flow{kind} {hold // 3600}h")
    log("TRADE" if res.get("ok") else "ERROR", f"FLOW {kind.upper()} {sym} {'BUY' if d == 1 else 'SELL'} {lots} lots: {res}")
    return {"price": px, "sl": sl, "lots": lots, "result": res, "risk_pct": RISK_PCT}


def _gate(initial: float, day_start: float, equity: float):
    """(equity, check) for _send: the same guard.check every other bensim book uses, over ALL bensim positions."""
    from .live import open_risk
    pos = [p for p in bridge.positions() if 770100 < (p.get("magic") or 0) < 770200]
    quotes = {p["symbol"]: bridge.tick(p["symbol"]) for p in pos}
    opens = [guard.Open(p["symbol"], 1 if p["type"] == 0 else -1, open_risk(p, quotes[p["symbol"]])) for p in pos]

    def check(sym: str, d: int, risk: float | None):
        ok, why = guard.check(initial, day_start, equity, opens, sym, d, risk)
        return ok, why
    return equity, check


_yields_busy = threading.Event()


def _refresh_yields(log) -> None:
    try:
        my.fetch()
        log("INFO", "FLOW yields refreshed")
    except Exception as exc:
        log("WARN", f"FLOW yields refresh failed (keeps the last file): {exc!r}")
    finally:
        _yields_busy.clear()


def step(now: float, initial: float, day_start: float, equity: float, halted: bool, dry_engine: bool, log, state) -> None:
    """One engine cycle for the flow book. Never raises into the engine (live.cycle wraps it)."""
    if MODE == "off":
        return
    dry = dry_engine or MODE != "live"
    utc = datetime.fromtimestamp(now, timezone.utc)
    # daily yields refresh (background thread: the engine cycle is not delayed)
    if utc.hour >= 1 and state("flow_yields_day") != utc.date().isoformat() and not _yields_busy.is_set():
        state("flow_yields_day", utc.date().isoformat())
        _yields_busy.set()
        threading.Thread(target=_refresh_yields, args=(log,), daemon=True).start()
    pos = bridge.positions()
    ours = [p for p in pos if p.get("magic") == MAGIC]
    held = {p["symbol"] for p in pos if 770100 < (p.get("magic") or 0) < 770200}  # one trade per pair across ALL bensim books (2026-10-20)
    for p in ours:  # time exits
        opened = int(server_to_utc(np.array([p["time"]], dtype=np.int64))[0])
        hold = HOLD_AM if str(p.get("comment", "")).startswith("flowam") else HOLD_ML
        if now - opened >= hold:
            log("INFO", f"FLOW TIME EXIT {p['symbol']} #{p['ticket']}: {bridge.close(p['ticket'], 'flow time') if not dry else 'dry'}")
    blocked = halted or guard.flatten_now(initial, day_start, equity) or guard.risk_scale(initial, equity) == 0.0
    if utc.weekday() >= 5:
        return
    # EURUSD morning short
    if utc.hour == 9 and utc.minute < 15:
        dt = int(now // 86400) * 86400 + 9 * 3600
        if _record("am", "EURUSD", dt, -1, 0.0, 0.0, "PENDING", {}):
            if blocked:
                decision, detail = "SKIP_BLOCKED", {"halted": halted}
            elif "EURUSD" in held:
                decision, detail = "SKIP_PAIR_OPEN", {}
            elif news := news_block.blocked("EURUSD"):
                decision, detail = "SKIP_NEWS", {"news": news}
            else:
                detail = _send("am", "EURUSD", -1, AM_LOTS, HOLD_AM, log, dry, _gate(initial, day_start, equity))
                decision = detail.get("skip") or ("DRY" if dry else ("SENT" if detail.get("result", {}).get("ok") else "FAILED"))
            with db.connect(autocommit=True) as c:
                c.execute("update live_flow set decision=%s, detail=%s where kind='am' and symbol='EURUSD' and decision_time=%s",
                          (decision, json.dumps(detail, default=str), dt))
            log("INFO", f"FLOW AM EURUSD SELL: {decision} {detail}")
    # ML: once per new H1 close (first cycle in the hour)
    hour_dt = int(now // 3600) * 3600
    if utc.minute >= 15 or state("flow_ml_hour") == hour_dt:
        return
    state("flow_ml_hour", hour_dt)
    if datetime.fromtimestamp(hour_dt, timezone.utc).hour in ML_SKIP_UTC:
        log("INFO", f"FLOW ML skipped this hour: late-hour rule ({sorted(ML_SKIP_UTC)} UTC)")
        return
    if utc_to_server(hour_dt) // 3600 % 24 == 0:  # broker midnight = daily break (22:00Z, 21:00Z in US summer): orders get "Market closed"
        log("INFO", "FLOW ML skipped this hour: broker daily break")
        return
    mdl = model()
    if mdl["year"] != utc.year:
        log("WARN", f"FLOW model is for {mdl['year']} (now {utc.year}) -- run: python -m bensim.flow_live train")
    f, stale = scores(now)
    if stale:
        log("INFO", f"FLOW stale/missing H1 for {', '.join(stale)} (left out this hour)")
    for r in f.itertuples():
        d = 1 if r.pl >= r.ps else -1
        best = max(r.pl, r.ps)
        if best <= mdl["thr"]:
            continue
        if not _record("ml", r.symbol, hour_dt, d, float(best), mdl["thr"], "PENDING", {}):
            continue
        atr = float("nan")
        if ML_ATR_MIN > 0:
            m = h1(r.symbol, 60)
            atr = atr_ratio(m["high"], m["low"]) if len(m["t"]) and int(m["t"][-1]) == hour_dt - 3600 else float("nan")
        if blocked:
            decision, detail = "SKIP_BLOCKED", {}
        elif ML_ATR_MIN > 0 and not atr > ML_ATR_MIN:  # NaN (missing bars) also skips
            decision, detail = "SKIP_ATR_QUIET", {"atr6_48": None if np.isnan(atr) else round(atr, 3), "min": ML_ATR_MIN}
        elif r.symbol in held:
            decision, detail = "SKIP_PAIR_OPEN", {}
        elif news := news_block.blocked(r.symbol):
            decision, detail = "SKIP_NEWS", {"news": news}
        else:
            detail = _send("ml", r.symbol, d, ML_LOTS, HOLD_ML, log, dry, _gate(initial, day_start, equity))
            decision = detail.get("skip") or ("DRY" if dry else ("SENT" if detail.get("result", {}).get("ok") else "FAILED"))
            if decision in ("SENT", "DRY"):
                held.add(r.symbol)
        with db.connect(autocommit=True) as c:
            c.execute("update live_flow set decision=%s, detail=%s where kind='ml' and symbol=%s and decision_time=%s",
                      (decision, json.dumps(detail, default=str), r.symbol, hour_dt))
        log("INFO", f"FLOW ML {r.symbol} {'BUY' if d == 1 else 'SELL'} pred {best:+.2f} bp > thr {mdl['thr']:.2f}: {decision} {detail}")


def check() -> None:
    """Print what the model sees right now (no orders, nothing recorded)."""
    now = time.time()
    t0 = time.time()
    f, stale = scores(now)
    mdl = model()
    print(f"model {mdl['year']} (trained {mdl['trained_on']}, threshold {mdl['thr']:.2f} bp from {mdl['threshold_from']}); "
          f"scored in {time.time() - t0:.1f}s; stale: {stale or 'none'}")
    print(f[["symbol", "t", "pl", "ps", "spr", "gap_level", "gap_d60", "ret_24", "str_diff_24"]].round(3).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train", "check"])
    ap.add_argument("--year", type=int, default=None)
    a = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "3")
    if a.cmd == "train":
        train(a.year)
    else:
        check()


if __name__ == "__main__":
    main()
