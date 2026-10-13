"""Today's page for BOTH live strategies -- works without Claude: double-click bensim\\today.bat.

    python -m bensim.today [--no-update] [--local-utc-offset 3]

  * open positions, labelled by strategy (fib618 = magic 770101 / comment "fib618 ...", Bennett = 770102 / "bennett ..."),
    with entry, stop, target and P&L now
  * today's signals of both strategies and what the engine did with each (sent / skipped and why)
  * per pair: fib618's next trigger (the M15 close level, any waiting order) + its plan chart, next to Bennett's plan
Pulls the newest bars from MT5 first (bridge off -> uses what the DB has and says so).
Output: docs/charts/today/<date>/index.html (opened in the browser).
"""
from __future__ import annotations

import argparse
import asyncio
import html
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import bennett_strategy as bs
from . import bridge, db, indicators, live, plan_chart
from .backtest import load_m5, resample
from .bennett_report import plan_ahead, update_bars
from .strategy import trend

NAMES = {live.MAGIC: "fib618", live.MAGIC_BN: "Bennett"}
COLOR = {"fib618": "#1565c0", "Bennett": "#6a1b9a"}


def _tag(name: str) -> str:
    return f"<span class='tag' style='background:{COLOR.get(name, '#555')}'>{name}</span>"


def positions_html() -> str:
    try:
        ps = [p for p in bridge.positions() if p.get("magic") in NAMES]
        acct = bridge.account()
    except Exception as exc:
        return f"<p class='warn'>MT5 bridge not reachable ({html.escape(repr(exc))}) -- positions unknown.</p>"
    head = f"<p>Account equity <b>${acct['equity']:,.2f}</b> (balance ${acct['balance']:,.2f}).</p>"
    if not ps:
        return head + "<p>No open positions.</p>"
    rows = []
    for p in ps:
        side = "BUY" if p["type"] == 0 else "SELL"
        opened = datetime.fromtimestamp(int(live.server_to_utc(np.array([p["time"]]))[0]), timezone.utc)
        rows.append(f"<tr><td>{_tag(NAMES[p['magic']])}</td><td>{p['symbol']}</td><td>{side}</td><td>{p['volume']}</td>"
                    f"<td>{p['price_open']}</td><td>{p['sl']}</td><td>{p['tp']}</td><td>{p['price_current']}</td>"
                    f"<td class='{'w' if p['profit'] >= 0 else 'l'}'>${p['profit']:+,.2f}</td><td>{opened:%a %d %H:%M} UTC</td></tr>")
    return head + ("<table><tr><th>Strategy</th><th>Pair</th><th>Side</th><th>Lots</th><th>Entry</th><th>Stop</th><th>Target</th>"
                   "<th>Now</th><th>P&amp;L</th><th>Opened</th></tr>" + "".join(rows) + "</table>")


def signals_html() -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = []
    with db.connect() as c:
        for table, name in (("live_signals", "fib618"), ("live_signals_bennett", "Bennett")):
            if not c.execute("select to_regclass(%s)", (table,)).fetchone()[0]:
                continue
            for r in c.execute(f"select created, symbol, dir, entry, stop, target, decision, detail->>'risk_usd' from {table} "
                               "where created >= %s order by created", (since,)):
                rows.append((r[0], name, *r[1:]))
    if not rows:
        return "<p>No signals in the last 24 hours.</p>"
    rows.sort()
    body = "".join(f"<tr><td>{t:%a %H:%M} UTC</td><td>{_tag(n)}</td><td>{s}</td><td>{'BUY' if d == 1 else 'SELL'}</td>"
                   f"<td>{e:.5g}</td><td>{sl:.5g}</td><td>{tp:.5g}</td><td>{html.escape(str(dec))}</td><td>{risk or ''}</td></tr>"
                   for t, n, s, d, e, sl, tp, dec, risk in rows)
    return ("<table><tr><th>Time</th><th>Strategy</th><th>Pair</th><th>Side</th><th>Entry</th><th>Stop</th><th>Target</th>"
            "<th>What the engine did</th><th>Risk $</th></tr>" + body + "</table>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true", help="skip pulling the newest bars from MT5")
    ap.add_argument("--local-utc-offset", type=int, default=3)
    ap.add_argument("--out", default="docs/charts/today")
    a = ap.parse_args()
    fib_pairs, bn_pairs = live.LIVE_SYMBOLS, (live.BN_SYMBOLS if live.BN_ON else [])
    pairs = list(dict.fromkeys(fib_pairs + bn_pairs))
    now = datetime.now(timezone.utc)
    out = Path(a.out) / f"{now:%Y-%m-%d_%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    if not a.no_update:
        print("pulling the newest bars from MT5 ...", flush=True)
        update_bars(pairs)
    cards, svgs = [], []
    for sym in pairs:
        print(f"{sym}: plans ...", flush=True)
        if sym in fib_pairs:
            try:
                fib_txt = "\n".join(live.setup_lines(sym))
            except Exception as exc:
                fib_txt = f"(MT5 bridge not reachable: {exc!r})"
            m5 = load_m5(sym, (now - timedelta(days=40)).isoformat(), None)
            m15, h1 = resample(m5, 900), resample(m5, 3600)
            m = {k: v[-300:] for k, v in m15.items()}
            f = out / f"{sym}_fib618.svg"
            f.write_text(plan_chart.svg(sym, m, indicators.compute(m), int(trend(h1)[-1]), a.local_utc_offset), encoding="utf-8")
            svgs.append(f)
            fib = f"<pre>{html.escape(fib_txt)}</pre><img src='{f.with_suffix('.png').name}'>"
        else:
            fib = "<p class='muted'>fib618 does not trade this pair.</p>"
        if sym in bn_pairs:
            try:
                bn = f"<pre>{html.escape(plan_ahead(sym, bs.LIVE))}</pre>"
            except Exception as exc:
                bn = f"<p class='warn'>{html.escape(repr(exc))}</p>"
        else:
            bn = "<p class='muted'>Bennett does not trade this pair.</p>"
        cards.append(f"<div class='card'><h2>{sym}</h2><div class='cols'><div class='col'><h3>{_tag('fib618')}</h3>{fib}</div>"
                     f"<div class='col'><h3>{_tag('Bennett')}</h3>{bn}</div></div></div>")
    print("rendering charts ...", flush=True)
    asyncio.run(plan_chart.render(svgs))
    kz = ", ".join(f"{x:02d}-{y:02d}" for x, y in bs.LIVE.killzones)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Today -- both strategies</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:16px;max-width:1600px;color:#222}}table{{border-collapse:collapse;margin:8px 0}}
td,th{{border:1px solid #ccc;padding:4px 8px;font-size:14px}}.tag{{color:#fff;border-radius:4px;padding:2px 8px;font-size:13px}}
.w{{color:#2e7d32;font-weight:bold}}.l{{color:#c62828;font-weight:bold}}.warn{{color:#c62828}}.muted{{color:#888}}
.card{{border-top:3px solid #eee;margin-top:24px}}.cols{{display:flex;gap:24px;flex-wrap:wrap}}.col{{flex:1 1 600px;min-width:0}}
pre{{white-space:pre-wrap;background:#f6f6f6;padding:8px;font-size:13px}}img{{width:100%;border:1px solid #ddd}}</style></head><body>
<h1>Today -- {now:%a %d %b %Y %H:%M} UTC</h1>
<p>{_tag('fib618')} M15 Donchian break -> entry at the 61.8% fib + 2 of (killzone, POI, sweep); pairs {', '.join(fib_pairs)};
0.5% risk (half with 2 of 3); MT5 magic {live.MAGIC}, comment "fib618 ..." (older: "bs ...").<br>
{_tag('Bennett')} 4H trend + 1H leg -> relief into the OTE -> 5M CHoCH, only {kz} UTC, not Sunday; pairs {', '.join(bn_pairs) or 'off'};
{live.BN_RISK_SCALE:g} x risk; MT5 magic {live.MAGIC_BN}, comment "bennett ...".<br>Shared: one trade per pair, -4% brake (no new trades), kill switch file bensim.STOP.</p>
<h2>Open positions</h2>{positions_html()}<h2>Signals, last 24 h</h2>{signals_html()}{''.join(cards)}</body></html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"page: {(out / 'index.html').resolve()}", flush=True)
    webbrowser.open((out / "index.html").resolve().as_uri())


if __name__ == "__main__":
    main()
