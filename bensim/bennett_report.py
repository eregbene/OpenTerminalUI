"""Bennett report for any day / week, no lookahead: replays the Bennett engine bar by bar on every symbol, draws each
trade in his chart style, a snapshot (engine view at the end) for symbols without a trade, and writes a summary page
with R and dollars.

    python -m bensim.bennett_report --from 2026-10-05 --to 2026-10-09 [--update] [--symbols EURUSD,XAUUSD]
                                    [--balance 97677 --risk 0.5] [--out docs/charts/report]
    (double-click: bensim/bennett_report.bat)

--to is inclusive (the whole day). --update first pulls the latest bars from MT5 (the bridge must be running).
Output: <out>/<from>_<to>/index.html (+ trades.csv and one PNG per trade / snapshot).
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from . import bennett_chart as bc
from . import bennett_strategy as bs
from . import bennett_smc as smc
from .backtest import CONTRACT, Exec, _simulate, _spread, _usd_per_unit_move, _win, load_m5, resample
from .config import SYMBOLS
from .strategy import pip


def replay(sym: str, a: pd.Timestamp, b: pd.Timestamp, p: bs.BennettParams, ex: Exec) -> list[dict]:
    """The backtester's loop for one symbol between a and b (one open trade at a time)."""
    m5 = load_m5(sym, (a - pd.Timedelta(days=75)).isoformat(), (b + pd.Timedelta(days=14)).isoformat())
    if not len(m5["t"]):
        return []
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    k0, k1 = int(np.searchsorted(m5["t"], a.timestamp())), int(np.searchsorted(m5["t"], b.timestamp()))
    open_until, out = -1, []
    for k in range(k0, k1):
        if k <= open_until:
            continue
        now = int(m5["t"][k]) + 300
        i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
        i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
        pl = bs.plan(_win(m15, i15, 300), _win(h1, i1, 200), _win(m5, k + 1, p.m5_bars), sym, p, htf=_win(h1, i1, p.htf_bars))
        if not (pl and pl["status"] == "TRIGGERED"):
            continue
        bid = float(m5["close"][k])
        spr = _spread(sym, now, ex)
        order, _ = bs.order_for(pl, bid, bid + spr, p)
        if order is None:
            continue
        tr = _simulate(sym, m5, k, order, p, ex, spr)
        tr["still_open"] = tr["exit_index"] >= len(m5["t"]) - 1
        open_until = tr["exit_index"]
        out.append(tr)
    return out


def _p(sym: str, x: float) -> str:
    return f"{x:.2f}" if sym in ("XAUUSD", "USDJPY") else f"{x:.5f}"


def _ts(t: int) -> str:
    return pd.Timestamp(int(t), unit="s").strftime("%a %d %b %H:%M")


def trade_notes(tr: pd.Series, st: dict, usd: float) -> str:
    """The write-up under each chart: the setup in his steps, what happened, R, $ and size."""
    sym, d = tr.symbol, int(tr.dir)
    side, leg = ("SELL" if d < 0 else "BUY"), st["leg"]
    f = lambda x: _p(sym, x)  # noqa: E731
    risk = abs(tr.entry - tr.sl)
    lots = usd / (risk * _usd_per_unit_move(sym, tr.entry) * CONTRACT.get(sym, 100_000.0))
    m5 = load_m5(sym, pd.Timestamp(int(tr.t_entry) - 300, unit="s", tz="UTC").isoformat(),
                 pd.Timestamp(int(tr.t_exit) + 3 * 86400, unit="s", tz="UTC").isoformat())
    inn = m5["t"] <= int(tr.t_exit)
    best_px = m5["low"][inn].min() if d < 0 else m5["high"][inn].max()
    best_t = m5["t"][inn][int(np.argmin(m5["low"][inn]) if d < 0 else np.argmax(m5["high"][inn]))]
    after = m5["t"] > int(tr.t_exit)
    gaps = ", ".join(sorted({g[0] for g in st["gaps"]})) or "no FVG"
    liq = f"; liquidity: equal {'highs' if d < 0 else 'lows'} at {', '.join(f(x) for x in st['eq'][:2])}" if st["eq"] else ""
    held = (int(tr.t_exit) - int(tr.t_entry)) / 3600
    how = {"target": f"hit the target {f(tr.tp)}", "stop": f"was stopped at {f(tr.exit)}",
           "time": f"was closed on time (held {held:.0f} h) at {f(tr.exit)}"}.get(tr.exit_reason, tr.exit_reason)
    if tr.still_open:
        how = f"is still open, now {f(tr.exit)}"
    post = ""
    if after.any() and not tr.still_open:
        ext = m5["low"][after].min() if d < 0 else m5["high"][after].max()
        post = (f" In the next 3 days price went on to {f(ext)} "
                f"({(ext - tr.entry) * d / risk:+.1f}R from the entry{', the target would have been hit' if (ext - tr.tp) * d >= 0 else ''}).")
    res = "WIN" if tr.r > 0 else "LOSS"
    return f"""<div class='notes'><h3>{sym} {side}, {_ts(tr.t_entry)} UTC -- {res} {tr.r:+.2f}R (${tr.usd:+,.0f})</h3>
<ol><li><b>Bias:</b> daily trend {'down' if d < 0 else 'up'} + 4H {leg['kind']} (close through {f(st['brk'])}) &rarr; {side.lower()}s only.</li>
<li><b>Leg / fib:</b> {f(leg['start'])} &rarr; {f(leg['ext'])}; 50% = {f((leg['start'] + leg['ext']) / 2)}, <b>OTE {f(st['ote'][0])}&ndash;{f(st['ote'][1])}</b>.</li>
<li><b>Area of interest:</b> {gaps} inside the OTE{liq}.</li>
<li><b>Trigger:</b> price traded into the OTE, then a 5M CHoCH &rarr; {side.lower()} at <b>{f(tr.entry)}</b>, stop <b>{f(tr.sl)}</b> ({risk / pip(sym):.1f} pips, beyond the 5M swing{' / equal levels' if st['eq'] else ''}), target <b>{f(tr.tp)}</b> ({tr.chk_rr_planned}R, {'T1 = the leg extreme' if tr.chk_target_is_t1 else 'T2 = next liquidity'}).</li>
<li><b>What happened:</b> best move {(best_px - tr.entry) * d / risk:+.1f}R (to {f(best_px)} on {_ts(best_t)}); the trade {how} = <b>{tr.r:+.2f}R</b>.{post}</li>
<li><b>Money:</b> 1R = ${usd:,.0f} &rarr; size &asymp; {lots:.2f} lots &rarr; <b>${tr.usd:+,.0f}</b> (R includes spread + commission).</li></ol></div>"""


def snapshot_notes(sym: str, st: dict, px: float) -> str:
    leg, d = st["leg"], st["leg"]["d"]
    pos = smc.fib_pos(px, leg["start"], leg["ext"])
    f = lambda x: _p(sym, x)  # noqa: E731
    state = ("price is in the OTE -- waiting for a 5M CHoCH" if 0.62 <= pos <= 0.79 else
             "price went beyond the leg start -- the idea is invalid, waiting for a new leg" if pos > 1 else
             "waiting for a relief into the OTE" if pos < 0.62 else "price is deep in premium/discount above the OTE")
    return f"""<div class='notes'><h3>{sym}: no trade in this period</h3><ul>
<li>4H {leg['kind']} &rarr; {'sells' if d < 0 else 'buys'} (if the daily agrees); leg {f(leg['start'])} &rarr; {f(leg['ext'])}, OTE {f(st['ote'][0])}&ndash;{f(st['ote'][1])}.</li>
<li>Price at the end: {f(px)} (fib {pos:.2f}) &rarr; {state}.</li></ul></div>"""


async def _render(paths: list[Path]) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        pg = await br.new_page(viewport={"width": bc.W, "height": bc.H})
        for fp in paths:
            await pg.goto(fp.resolve().as_uri())
            await pg.screenshot(path=str(fp.with_suffix(".png")))
        await br.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="a", required=True)
    ap.add_argument("--to", dest="b", required=True, help="inclusive day")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--update", action="store_true", help="pull the latest bars from MT5 first")
    ap.add_argument("--balance", type=float, default=97_677.0)
    ap.add_argument("--risk", type=float, default=0.5, help="risk per trade in %% (1R)")
    ap.add_argument("--out", default="docs/charts/report")
    ap.add_argument("--csv", default=None, help="draw the trades of an existing backtest CSV instead of replaying")
    a = ap.parse_args()
    syms = [s for s in a.symbols.split(",") if s]
    if a.update:
        from . import history
        for s in syms:
            history.download(s, "M5", years=0.05)
    t0 = pd.Timestamp(a.a, tz="UTC")
    t1 = pd.Timestamp(a.b, tz="UTC") + pd.Timedelta(days=1)
    out = Path(a.out) / f"{a.a}_{a.b}"
    out.mkdir(parents=True, exist_ok=True)
    p, ex = bs.BennettParams(), Exec()
    usd = a.balance * a.risk / 100.0
    rows, svgs, no_trade, notes = [], [], [], {}
    given = pd.read_csv(a.csv) if a.csv else None
    for s in syms:
        if given is not None:
            g = given[(given.symbol == s) & (given.t_entry >= t0.timestamp()) & (given.t_entry < t1.timestamp())]
            trades = [dict(r, still_open=False) for _, r in g.iterrows()]
        else:
            trades = replay(s, t0, t1, p, ex)
        print(f"{s}: {len(trades)} trade(s)")
        for tr in trades:
            tr["usd"] = round(tr["r"] * usd, 2)
            rows.append(tr)
        if not trades:
            no_trade.append(s)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("t_entry").reset_index(drop=True)
    if len(df):
        df.to_csv(out / "trades.csv", index=False)
    for i, tr in df.iterrows():
        tr = tr.copy()
        fp = out / f"trade_{i:02d}_{tr.symbol}_{pd.Timestamp(int(tr.t_entry), unit='s'):%Y%m%d_%H%M}.svg"
        st = bc.state(tr.symbol, tr, p)
        fp.write_text(bc.svg(tr.symbol, tr, st, p), encoding="utf-8")
        svgs.append(fp)
        notes[fp.stem] = trade_notes(tr, st, usd)
    end = min(t1, pd.Timestamp.now(tz="UTC"))
    for s in no_trade:  # engine view at the end of the period (nothing after it is drawn)
        last = load_m5(s, (end - pd.Timedelta(days=3)).isoformat(), end.isoformat())
        if not len(last["t"]):
            continue
        tq = int(last["t"][-1]) + 300
        tr = pd.Series({"symbol": s, "t_entry": tq, "t_exit": tq, "entry": float(last["close"][-1]), "sl": np.nan, "tp": np.nan,
                        "exit": float(last["close"][-1]), "r": np.nan, "exit_reason": "", "chk_target_is_t1": True,
                        "chk_rr_planned": "-", "snapshot": True, "note": "no trade in this period -- engine view at the end"})
        try:
            st = bc.state(s, tr, p)
        except Exception as exc:  # no leg yet etc.
            print(f"{s}: no snapshot ({exc!r})")
            continue
        fp = out / f"snapshot_{s}.svg"
        fp.write_text(bc.svg(s, tr, st, p), encoding="utf-8")
        svgs.append(fp)
        notes[fp.stem] = snapshot_notes(s, st, float(tr.entry))
    asyncio.run(_render(svgs))
    tot_r = float(df.r.sum()) if len(df) else 0.0
    items = []
    for _, tr in df.iterrows():
        side = "SELL" if tr.dir < 0 else "BUY"
        res = "OPEN" if tr.still_open else tr.exit_reason
        items.append(f"<tr><td>{tr.symbol}</td><td>{side}</td><td>{pd.Timestamp(int(tr.t_entry), unit='s'):%a %d %b %H:%M}</td>"
                     f"<td>{tr.entry:.5g}</td><td>{tr.sl:.5g}</td><td>{tr.tp:.5g}</td><td>{res}</td>"
                     f"<td class='{'w' if tr.r > 0 else 'l'}'>{tr.r:+.2f}R</td><td class='{'w' if tr.r > 0 else 'l'}'>${tr.usd:+,.0f}</td>"
                     f"<td>{abs(tr.entry - tr.sl) / pip(tr.symbol):.1f} pips</td></tr>")
    imgs = "".join(f"<div class='card'><img src='{f.with_suffix('.png').name}'>{notes.get(f.stem, '')}</div>" for f in svgs)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Bennett report {a.a} - {a.b}</title>
<style>body{{font-family:Segoe UI,Arial;margin:24px;max-width:1550px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px}}
.w{{color:#2e7d32;font-weight:bold}}.l{{color:#c62828;font-weight:bold}}img{{width:100%;border:1px solid #ddd}}
.card{{margin:28px 0;padding-bottom:12px;border-bottom:2px solid #eee}}.notes h3{{margin:10px 0 4px}}.notes li{{margin:3px 0}}</style></head><body>
<h1>Bennett engine -- {a.a} to {a.b}</h1>
<p>Replayed bar by bar (no lookahead) on {", ".join(syms)}. 1R = {a.risk}% of ${a.balance:,.0f} = ${usd:,.0f}. R includes spread + commission.</p>
<p><b>{len(df)} trade(s), {tot_r:+.2f}R = ${tot_r * usd:+,.0f}</b>{" -- no trade on: " + ", ".join(no_trade) if no_trade else ""}</p>
<table><tr><th>Symbol</th><th>Side</th><th>Entry time (UTC)</th><th>Entry</th><th>Stop</th><th>Target</th><th>Result</th><th>R</th><th>$</th><th>Stop size</th></tr>
{"".join(items)}</table>{imgs}</body></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"{len(df)} trade(s), {tot_r:+.2f}R = ${tot_r * usd:+,.0f}\nreport: {(out / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
