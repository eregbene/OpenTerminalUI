"""Round 16: intraday-only G8 carry (08:00 -> 16:45 New York), rules in PREREG.md."""
import json, pathlib, datetime as dt, math
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/round16'
NY, UTC = ZoneInfo('America/New_York'), dt.timezone.utc
PAIRS = {'EUR': 'EURUSD', 'GBP': 'GBPUSD', 'AUD': 'AUDUSD', 'NZD': 'NZDUSD',
         'JPY': 'USDJPY', 'CHF': 'USDCHF', 'CAD': 'USDCAD'}
CCYS = ['USD', 'EUR', 'JPY', 'GBP', 'CAD', 'CHF', 'AUD', 'NZD']
SLIP = 0.3e-4
PERIODS = {'DISC': (2012, 2017), 'VAL': (2018, 2021), 'LOCK': (2022, 2026)}

bars = {}
for c, p in PAIRS.items():
    d = np.load(ROOT / f'data/bars/fxcm/{p}_M5.npz')
    bars[c] = (d['t'] + 300, d['c'], d['spr'])          # close time, bid, spread


def quote(c, target):
    ct, bid, spr = bars[c]
    i = np.searchsorted(ct, target, 'right') - 1
    if i < 0 or ct[i] < target - 900:
        return None
    return int(ct[i]), float(bid[i]), float(bid[i] + spr[i]), float(spr[i])


def ny_epoch(day, hh, mm):
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).timestamp())


rates = pd.read_csv(ROOT / 'data/macro/policy/policy_rates_daily.csv', parse_dates=['date']).set_index('date')
rates = rates.reindex(pd.date_range(rates.index[0], '2026-12-31')).ffill()
rel = pd.read_csv(ROOT / 'data/macro/us_releases/dates.csv')
rel_days = set(rel.date)


def ranking(day):
    r = rates.loc[pd.Timestamp(day) - pd.Timedelta(days=1), CCYS]
    assert r.notna().all(), (day, r)
    order = sorted(CCYS, key=lambda c: (-r[c], c))      # ties: alphabetical code ranks higher
    return order[:3], order[-3:], r


def ccy_ret(c, side, q0, q1, net):
    """Signed return vs USD of a long (side=+1) / short (-1) position in currency c, q=(time,bid,ask,spr)."""
    if c == 'USD':
        return 0.0
    _, b0, a0, _ = q0; _, b1, a1, _ = q1
    if not net:
        b0 = a0 = (b0 + a0) / 2; b1 = a1 = (b1 + a1) / 2
    # USD P&L per USD of notional, exact for each execution path
    if PAIRS[c].startswith(c):                       # XUSD
        r = b1 / a0 - 1 if side > 0 else 1 - a1 / b0
    else:                                            # USDX: long X = sell pair, short X = buy pair
        r = b0 / a1 - 1 if side > 0 else 1 - a0 / b1
    return r - (2 * SLIP if net else 0.0)


def port(longs, shorts, q0, q1, net):
    lr = [ccy_ret(c, +1, q0.get(c), q1.get(c), net) for c in longs]
    sr = [ccy_ret(c, -1, q0.get(c), q1.get(c), net) for c in shorts]
    return np.mean(lr) + np.mean(sr)


# ---- build day list ----
days, d = [], dt.date(2012, 1, 3)
zero_spr = 0
while d <= dt.date(2026, 10, 9):
    if d.weekday() < 5:
        e0, e1 = ny_epoch(d, 8, 0), ny_epoch(d, 16, 45)
        q0 = {c: quote(c, e0) for c in PAIRS}; q1 = {c: quote(c, e1) for c in PAIRS}
        if all(q0.values()) and all(q1.values()):
            days.append((d, e0, e1, q0, q1))
    d += dt.timedelta(days=1)

rows = []
for k, (d, e0, e1, q0, q1) in enumerate(days):
    longs, shorts, r = ranking(d)
    used = [c for c in longs + shorts if c != 'USD']
    zero_spr += sum(q0[c][3] <= 0 for c in used) + sum(q1[c][3] <= 0 for c in used)
    row = dict(date=d, release=d.isoformat() in rel_days, longs=','.join(longs), shorts=','.join(shorts),
               net=port(longs, shorts, q0, q1, True), gross=port(longs, shorts, q0, q1, False))
    if k + 1 < len(days):                                   # overnight: 16:45 D -> 08:00 next sample day
        d2, n0 = days[k + 1][0], days[k + 1][3]
        nights = sum(3 if (d + dt.timedelta(days=j)).weekday() == 2 else 1
                     for j in range((d2 - d).days) if (d + dt.timedelta(days=j)).weekday() < 5)
        carry = (np.mean([(r[c] - r['USD']) for c in longs]) - np.mean([(r[c] - r['USD']) for c in shorts])) / 100 / 365 * nights
        row.update(on_gross=port(longs, shorts, q1, n0, False), on_net=port(longs, shorts, q1, n0, True),
                   on_carry=carry, on_nights=nights)
    rows.append(row)
df = pd.DataFrame(rows)
df['date'] = pd.to_datetime(df.date)
df['year'] = df.date.dt.year
iso = df.date.dt.isocalendar(); df['week'] = iso.year * 100 + iso.week
df['on_total'] = df.on_gross + df.on_carry
df['on_total_net'] = df.on_net + df.on_carry


def cl_t(x, g):
    x = np.asarray(x, float); n = len(x); m = x.mean()
    s = pd.Series(x - m).groupby(np.asarray(g)).sum().values
    G = len(s); se = math.sqrt((s ** 2).sum() * G / (G - 1)) / n
    return m / se


def stats(sub, col):
    o = dict(n=int(len(sub)), mean_bp=float(sub[col].mean() * 1e4), t_week=float(cl_t(sub[col], sub.week)))
    for p, (a, b) in PERIODS.items():
        s = sub[(sub.year >= a) & (sub.year <= b)]
        o[p] = dict(n=int(len(s)), mean_bp=float(s[col].mean() * 1e4))
    return o


def dollars(sub, col):
    o = {}
    for lots in (1, 5, 7):
        pnl = sub.set_index('date')[col] * 1e5 * lots
        eq = pnl.cumsum(); dd = float((eq - eq.cummax()).min())
        yrs = (sub.date.iloc[-1] - sub.date.iloc[0]).days / 365.25
        o[f'{lots}_lot'] = dict(per_year=float(pnl.sum() / yrs), worst_month=float(pnl.resample('ME').sum().min()),
                                max_dd=dd, total=float(pnl.sum()))
    return o


sets = {'H1': df, 'H2': df[df.release], 'H3': df[~df.release]}
res = {'n_days': len(df), 'zero_spread_quotes_used': int(zero_spr), 'claims': {}, 'reported': {}}
for h, s in sets.items():
    res['claims' if h != 'H3' else 'reported'][h] = dict(net=stats(s, 'net'), gross=stats(s, 'gross'))
on = df.dropna(subset=['on_gross'])
for h, s in {'H1': on, 'H2': on[on.release], 'H3': on[~on.release]}.items():
    res['reported'][f'overnight_{h}'] = {c: stats(s, c) for c in ('on_gross', 'on_carry', 'on_total', 'on_net', 'on_total_net')}

# BH over H1, H2 (one-sided normal p)
ps = {h: 0.5 * math.erfc(res['claims'][h]['net']['t_week'] / math.sqrt(2)) for h in ('H1', 'H2')}
order = sorted(ps, key=ps.get); m = len(ps); bh = {h: False for h in ps}
kmax = max([i + 1 for i, h in enumerate(order) if ps[h] <= 0.05 * (i + 1) / m], default=0)
for h in order[:kmax]:
    bh[h] = True
for h in ('H1', 'H2'):
    c = res['claims'][h]['net']
    c['p_one_sided'] = ps[h]; c['bh_q05'] = bh[h]
    c['all_periods_pos'] = all(c[p]['mean_bp'] > 0 for p in PERIODS)
    c['PASS'] = bool(c['all_periods_pos'] and c['t_week'] >= 2 and bh[h])
    res['claims'][h]['dollars_net'] = dollars(sets[h], 'net')
res['by_year_net_bp'] = {h: {int(y): float(v * 1e4) for y, v in s.groupby('year').net.mean().items()} for h, s in sets.items()}
res['legs_freq'] = df.groupby(['longs', 'shorts']).size().sort_values(ascending=False).head(10).to_dict()
res['legs_freq'] = {f'L:{a} S:{b}': int(v) for (a, b), v in res['legs_freq'].items()}
json.dump(res, open(OUT / 'r16_results.json', 'w'), indent=1, default=str)
df.to_csv(OUT / 'r16_days.csv', index=False)

# ---- sanity prints ----
print('days', len(df), 'release days', int(df.release.sum()), 'zero-spread quotes used', zero_spr)
for want in ['2015-03-06', '2015-03-09', '2015-11-02', '2020-04-03', '2024-07-11']:
    for d, e0, e1, q0, q1 in days:
        if d.isoformat() == want:
            lo, sh, _ = ranking(d)
            print(f'\n{d} {d:%a} entry {dt.datetime.fromtimestamp(e0, UTC):%H:%M}Z exit {dt.datetime.fromtimestamp(e1, UTC):%H:%M}Z  L={lo} S={sh}')
            for c in lo + sh:
                if c == 'USD':
                    print(f'  {c}: zero leg'); continue
                side = 'LONG ' if c in lo else 'SHORT'
                print(f'  {side} {c} via {PAIRS[c]} entry {dt.datetime.fromtimestamp(q0[c][0], UTC):%H:%M}Z bid {q0[c][1]} ask {q0[c][2]:.5f}'
                      f' | exit {dt.datetime.fromtimestamp(q1[c][0], UTC):%H:%M}Z bid {q1[c][1]} ask {q1[c][2]:.5f}'
                      f' | ret {ccy_ret(c, 1 if c in lo else -1, q0[c], q1[c], True) * 1e4:+.2f}bp')
            r = df[df.date == pd.Timestamp(d)].iloc[0]
            print(f'  port net {r.net * 1e4:+.2f}bp gross {r.gross * 1e4:+.2f}bp release={r.release}')
print(json.dumps({h: {k: res["claims"][h]["net"][k] for k in ("n", "mean_bp", "t_week", "PASS")} for h in ("H1", "H2")}, indent=1))
