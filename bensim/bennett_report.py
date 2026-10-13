"""Bennett report for any day / week, no lookahead: replays the Bennett engine bar by bar on every symbol, draws each
trade in his chart style, a snapshot (engine view at the end) for symbols without a trade, and writes a summary page
with R and dollars.

    python -m bensim.bennett_report --when "last week" [--update] [--symbols EURUSD,XAUUSD]
    python -m bensim.bennett_report --from 2026-10-05 --to 2026-10-09 [--balance 97677 --risk 0.5] [--out docs/charts/report]
    python -m bensim.bennett_report --plan [--update]      # text plan for the next session, no charts
    (double-click: bensim/bennett_report.bat)

--when takes plain English: last week, this week, last friday, yesterday, 6 december, last week of december 2025,
december 2025, 2025-12-01 to 2025-12-05 (bensim/periods.py). Nothing given = last week.

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
from .periods import parse_period
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
<ol><li><b>Bias:</b> {st['ctx']} {leg['kind']} {'down' if d < 0 else 'up'} (close through {f(st['brk'])}) &rarr; {side.lower()}s only.</li>
<li><b>Leg / fib:</b> {f(leg['start'])} &rarr; {f(leg['ext'])}; 50% = {f((leg['start'] + leg['ext']) / 2)}, <b>OTE {f(st['ote'][0])}&ndash;{f(st['ote'][1])}</b>.</li>
<li><b>Area of interest:</b> {gaps} inside the OTE{liq}.</li>
<li><b>Trigger:</b> price traded into the OTE, then a 5M CHoCH &rarr; {side.lower()} at <b>{f(tr.entry)}</b>, stop <b>{f(tr.sl)}</b> ({risk / pip(sym):.1f} pips, beyond the 5M lower {'high' if d < 0 else 'low'} before the CHoCH + 1 pip{' / equal levels' if st['eq'] else ''}), target <b>{f(tr.tp)}</b> ({tr.chk_rr_planned}R, {'T1 = the leg extreme' if tr.chk_target_is_t1 else 'T2 = next liquidity'}).</li>
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
<li>{st['ctx']} {leg['kind']} &rarr; {'sells' if d < 0 else 'buys'}; leg {f(leg['start'])} &rarr; {f(leg['ext'])}, OTE {f(st['ote'][0])}&ndash;{f(st['ote'][1])}.</li>
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


def update_bars(syms: list[str]) -> None:
    """Pull the bars missing since the newest one in the DB from MT5. A stopped bridge only warns."""
    from . import db, history
    for s in syms:
        try:
            with db.connect() as c:
                last = c.execute("select max(ts) from candles where symbol=%s and tf='M5'", [s]).fetchone()[0]
            gap_days = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(last)).days + 2 if last is not None else 30
            n = history.download(s, "M5", years=max(gap_days, 3) / 365.25)
            print(f"{s}: {n} bars refreshed from MT5")
        except Exception as exc:  # bridge / terminal not running: report on what the DB has
            print(f"{s}: could not update from MT5 ({type(exc).__name__}: {exc}) -- using the bars already in the DB")


def plan_ahead(sym: str, p: bs.BennettParams) -> str:
    """The engine's view after the last bar, in words: the leg, where the OTE is, what must happen next."""
    end = pd.Timestamp.now(tz="UTC")
    m5 = load_m5(sym, (end - pd.Timedelta(days=75)).isoformat(), None)
    if len(m5["t"]) < 2000:
        return f"{sym}: not enough bars in the DB"
    f = lambda x: _p(sym, x)  # noqa: E731
    now = int(m5["t"][-1]) + 300
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    h = bs.closed({k: v[-p.htf_bars:] for k, v in h1.items()}, 3600, now)
    hb, sec = (bs.closed(bs.agg(h, 14400), 14400, now), 14400) if p.leg_tf == "H4" else (h, 3600)
    leg = bs.leg_of(hb, p)
    px = float(m5["close"][-1])
    head = f"{sym} (last bar {pd.Timestamp(now - 300, unit='s'):%a %d %b %H:%M} UTC, price {f(px)})"
    if leg is None:
        return f"{head}\n  no {p.leg_tf} structure yet -- nothing to do"
    d, side = leg["d"], ("SELL" if leg["d"] < 0 else "BUY")
    lines = [head, f"  {bs.context_text(p)} {leg['kind']} {'down' if d < 0 else 'up'} -> {side}S only. "
                   f"Leg {f(leg['start'])} -> {f(leg['ext'])}, 50% {f((leg['start'] + leg['ext']) / 2)}"]
    if p.bias == "H4" and p.leg_tf == "H1":
        if bs.structure(bs.closed(bs.agg(h, 14400), 14400, now), p)["trend"][-1] != d:
            lines.append("  4H trend disagrees -> no trade on this leg (bias filter)")
    elif p.bias not in ("off", "H4"):
        if bs.structure(bs.closed(bs.agg(h, 86400), 86400, now), p, seed=5)["trend"][-1] != d:
            lines.append("  daily trend disagrees -> no trade on this leg (bias filter)")
    t_ext = int(hb["t"][leg["x_bar"]])
    rl = bs.relief(leg, m15, t_ext, p)
    if rl is None:
        lines.append("  the leg's extreme is still forming -- wait for the relief")
        return "\n".join(lines)
    if rl["invalid"]:
        lines.append(f"  INVALID: a 15M candle closed beyond the protected level {f(leg['prot'])} -- wait for a new {p.leg_tf} break")
        return "\n".join(lines)
    leg, after, reach = rl["leg"], rl["after"], rl["reach"]
    z_lo, z_hi = rl["ote"]
    near, far = (z_lo, z_hi) if d == -1 else (z_hi, z_lo)
    lines.append(f"  OTE (area of interest) {f(min(z_lo, z_hi))} - {f(max(z_lo, z_hi))}; "
                 f"early warning if a candle closes {'above' if d < 0 else 'below'} {f(far)} (79%)")
    t0, t1 = int(hb["t"][leg["s_bar"]]), t_ext + sec
    zones = []
    for name, b in ((f"{p.leg_tf} FVG", hb), ("1H FVG", h), ("15M FVG", m15)):
        if name == "1H FVG" and hb is h:
            continue
        tb = int(np.searchsorted(b["t"], t_ext))
        zones += [f"{name} {f(z['bottom'])}-{f(z['top'])}" for z in smc.fvgs(b) if z["dir"] == d
                  and t0 <= b["t"][z["formed"]] <= t1 and z["touched"] >= tb and z["top"] >= z_lo and z["bottom"] <= z_hi]
    lines.append("  FVG in the OTE: " + (", ".join(zones) if zones else "none -- no trade unless one forms"))
    if (reach - near) * d > 0:
        lines.append(f"  WAITING: the relief has not reached the OTE yet (best so far {f(reach)}; {f(near)} is "
                     f"{abs(near - px) / pip(sym):.0f} pips from price). When it trades into the OTE, drop to the 5M and wait for a CHoCH.")
    else:
        ltf = {k: v[-p.m5_bars:] for k, v in m5.items()}
        ms = bs.structure(ltf, p, seed=6, brk=p.ltf_break_atr)
        tr5, prot = int(ms["trend"][-1]), float(ms["prot"][-1])
        if tr5 == -d:
            lines.append(f"  IN THE OTE (reached {f(reach)}). TRIGGER: a 5M candle CLOSE {'below' if d < 0 else 'above'} "
                         f"{f(prot)} (5M protected {'low' if d < 0 else 'high'}) = 5M CHoCH -> {side} on that close; "
                         f"stop beyond the 5M lower {'high' if d < 0 else 'low'} before it (+1 pip).")
        else:
            lines.append(f"  IN THE OTE (reached {f(reach)}); the 5M already points {'down' if tr5 < 0 else 'up' if tr5 > 0 else 'nowhere'} -- "
                         f"wait for the next 5M pullback and a CHoCH {'down' if d < 0 else 'up'}.")
        ia = np.flatnonzero(after)
        push = ia[0] + int(np.argmax(m15["high"][after]) if d == -1 else np.argmin(m15["low"][after]))
        exp = int(m15["t"][push]) + int(p.expire_h * 3600)
        if exp <= now:
            lines[-1] = (f"  EXPIRED: in the OTE but no new push into it for {p.expire_h:g} h "
                         f"(since {pd.Timestamp(exp, unit='s'):%a %d %b %H:%M} UTC) -- no trade on this leg")
        else:
            lines.append(f"  The idea expires {pd.Timestamp(exp, unit='s'):%a %d %b %H:%M} UTC if price does not push into the area again.")
    eq_tol = p.eq_tol_atr * leg["atr"]
    cands = sorted({x for x in [leg["ext"]] + smc.pools(hb, leg["ms"], d, len(hb["t"]) - 1, eq_tol) if (x - px) * d > 0},
                   key=lambda x: abs(x - px))[:3]
    lines.append("  Targets (liquidity): " + (", ".join(f(x) for x in cands) if cands else "none ahead")
                 + f"; minimum {p.min_rr}R or no trade")
    pl = bs.plan(_win(m15, len(m15["t"]), 300), _win(h1, len(h1["t"]), 200), _win(m5, len(m5["t"]), p.m5_bars), sym, p,
                 htf=_win(h1, len(h1["t"]), p.htf_bars))
    if pl and pl["status"] == "TRIGGERED":
        lines.append(f"  >>> TRIGGERED on the last bar: {side} {f(pl['entry'])}, stop {f(pl['stop'])}, target {f(pl['target'])} "
                     f"({pl['checks']['rr_planned']}R)")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--when", default=None, help='plain English: "last week", "last friday", "6 december", ...')
    ap.add_argument("--from", dest="a", default=None)
    ap.add_argument("--to", dest="b", default=None, help="inclusive day")
    ap.add_argument("--plan", action="store_true", help="text plan for the next session (no charts)")
    ap.add_argument("--symbols", default=",".join(bs.LIVE_SYMBOLS), help="default: the pairs Bennett trades live")
    ap.add_argument("--no-open", action="store_true", help="do not open the report in the browser")
    ap.add_argument("--update", action="store_true", help="pull the latest bars from MT5 first")
    ap.add_argument("--balance", type=float, default=97_677.0)
    ap.add_argument("--risk", type=float, default=0.5, help="risk per trade in %% (1R)")
    ap.add_argument("--out", default="docs/charts/report")
    ap.add_argument("--csv", default=None, help="draw the trades of an existing backtest CSV instead of replaying")
    a = ap.parse_args()
    syms = [s for s in a.symbols.split(",") if s]
    if a.update:
        update_bars(syms)
    if a.plan:
        p = bs.LIVE  # the configuration the live engine trades
        txt = "\n\n".join(plan_ahead(s, p) for s in syms)
        day = pd.Timestamp.now(tz="UTC")
        kz = ", ".join(f"{x:02d}-{y:02d}" for x, y in p.killzones) or "any time"
        txt = (f"Bennett plan -- made {day:%a %d %b %Y %H:%M} UTC ({bs.context_text(p)} leg, 5M trigger; triggers only {kz} UTC"
               f"{', not Sunday' if p.no_sunday else ''}{', clear path to the target' if p.clear_path else ''})\n\n{txt}")
        print(txt)
        out = Path(a.out) / "plans"
        out.mkdir(parents=True, exist_ok=True)
        fp = out / f"plan_{day:%Y-%m-%d_%H%M}.txt"
        fp.write_text(txt, encoding="utf-8")
        print(f"\nsaved: {fp.resolve()}")
        return
    if a.a is None or a.when is not None:
        d0, d1 = parse_period(a.when or "last week")
        a.a, a.b = d0.isoformat(), d1.isoformat()
    elif a.b is None:
        a.b = a.a
    print(f"period: {a.a} to {a.b}")
    t0 = pd.Timestamp(a.a, tz="UTC")
    t1 = pd.Timestamp(a.b, tz="UTC") + pd.Timedelta(days=1)
    out = Path(a.out) / f"{a.a}_{a.b}"
    out.mkdir(parents=True, exist_ok=True)
    p, ex = bs.LIVE, Exec()  # the configuration the live engine trades
    usd = a.balance * a.risk / 100.0
    rows, svgs, no_trade, notes = [], [], [], {}
    given = pd.read_csv(a.csv) if a.csv else None
    for s in syms:
        if given is not None:
            g = given[(given.symbol == s) & (given.t_entry >= t0.timestamp()) & (given.t_entry < t1.timestamp())]
            trades = [dict(r, still_open=False) for _, r in g.iterrows()]
        else:
            trades = replay(s, t0, t1, p, ex)
        print(f"{s}: {len(trades)} trade(s)", flush=True)
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
    if not a.no_open:
        import webbrowser
        webbrowser.open((out / "index.html").resolve().as_uri())


if __name__ == "__main__":
    main()
