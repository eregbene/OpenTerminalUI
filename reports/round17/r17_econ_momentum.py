"""Round 17: economic momentum (EM) and output-gap (OG) currency sorts. Rules: PREREG.md (fixed before results)."""
import json, math
import numpy as np, pandas as pd
from pathlib import Path
from statsmodels.tsa.filters.hp_filter import hpfilter

ROOT = Path(__file__).resolve().parents[2]; HERE = Path(__file__).parent
CC = ['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'JPY', 'NZD', 'USD']  # alphabetical = tie-break order
FOREIGN = [c for c in CC if c != 'USD']
END = pd.Period('2026-10', 'M')

econ = pd.read_csv(ROOT / 'data/macro/econ/econ_tidy.csv')
econ['ref'] = pd.PeriodIndex(econ.ref, freq='M')
econ['avail'] = econ.ref + econ.freq.map({'M': 2, 'Q': 3})
fx = pd.read_csv(ROOT / 'data/macro/cpi/WS_XRU_g8_eop.csv', index_col=0); fx.index = pd.PeriodIndex(fx.index, freq='M')
pol = pd.read_csv(ROOT / 'data/macro/policy/policy_rates_monthly_eop.csv', index_col=0); pol.index = pd.PeriodIndex(pol.index, freq='M')
pol = pol.ffill()
MONTHS = pd.period_range('1986-01', END, freq='M')

def zs(row, need=4):
    r = row.dropna()
    if len(r) < need or r.std(ddof=0) == 0: return pd.Series(np.nan, index=row.index)
    return (row - r.mean()) / r.std(ddof=0)

def avail_series(df):
    """df: ref, avail, value (one ccy/series) -> monthly series of latest value usable at end of each month."""
    s = df.sort_values('ref').groupby('avail').value.last()  # same avail month -> latest ref wins (sorted)
    return s.reindex(MONTHS.union(s.index)).ffill().reindex(MONTHS)

# ---- EM components
comp = {}
for (c, ser), g in econ.groupby(['ccy', 'series']):
    g = g.sort_values('ref').copy()
    if ser == 'ip':
        k = 12 if g.freq.iloc[0] == 'M' else 4
        g = g.set_index('ref'); full = g.value.reindex(pd.period_range(g.index[0], g.index[-1], freq='M'))
        g['value'] = (full / full.shift(12) - 1).reindex(g.index) * 100  # yoy by calendar month (works for M and Q refs)
        g = g.reset_index().dropna(subset=['value'])
    if ser == 'une':
        g['value'] = -g.value
    x = avail_series(g)
    comp.setdefault(ser, {})[c] = x - x.shift(12)
zc = {ser: pd.DataFrame(d).reindex(columns=CC).apply(zs, axis=1) for ser, d in comp.items()}
EM = pd.concat(zc.values()).groupby(level=0).mean().reindex(MONTHS)  # mean of available z's per currency

# ---- OG: recursive HP on usable IP obs
og = pd.DataFrame(index=MONTHS, columns=CC, dtype=float)
for c in CC:
    g = econ[(econ.ccy == c) & (econ.series == 'ip')].sort_values('ref')
    lam = 129600 if g.freq.iloc[0] == 'M' else 1600
    cache = {}
    for t in MONTHS:
        obs = g[g.avail <= t]
        n = len(obs)
        if n < 60: continue
        if n not in cache:
            cyc, _ = hpfilter(np.log(obs.value.values), lamb=lam); cache[n] = cyc[-1]
        og.loc[t, c] = cache[n]
OGz = og.apply(zs, axis=1)
EMz = EM.apply(zs, axis=1)
H3s = pd.concat([EMz, OGz]).groupby(level=0).mean().reindex(MONTHS)  # mean of available (one alone if other missing)

# ---- returns
e = pd.DataFrame(0.0, index=fx.index, columns=CC)
for c in FOREIGN:
    e[c] = fx[c].shift(1) / fx[c] - 1 + (pol[c].shift(1) - pol['USD'].shift(1)) / 1200
e['USD'] = 0.0
gross_e = e

def weights(score):
    W = pd.DataFrame(0.0, index=score.index, columns=CC)
    for t, row in score.iterrows():
        r = row.dropna()
        if len(r) < 6: continue
        order = sorted(r.index, key=lambda c: (-r[c], CC.index(c)))
        W.loc[t, order[:3]] = 1 / 3; W.loc[t, order[-3:]] = -1 / 3
    return W

def backtest(score, months):
    # bug fix (post-run, labelled in REPORT): EUR has no policy rate before 1999-01 -> its return is NaN and was silently
    # summed as 0; a currency whose holding-month return is undefined is now unscored at that signal date.
    ok = e.shift(-1).reindex(score.index).notna()
    score = score.where(ok)
    W = weights(score).reindex(months[:-1])  # signal at t
    hold = months[1:]
    Wh = W.copy(); Wh.index = hold          # position held over month t+1
    gross = (Wh * e.reindex(hold)).sum(axis=1)
    dw = Wh[FOREIGN].diff().abs().sum(axis=1); dw.iloc[0] = Wh[FOREIGN].iloc[0].abs().sum()
    cost = 0.0003 * dw + 0.0075 / 12 * Wh[FOREIGN].abs().sum(axis=1)
    return gross - cost, gross, Wh

def tstat(x): return x.mean() / x.std(ddof=1) * math.sqrt(len(x))
def maxdd(x): c = x.cumsum(); return float((c - c.cummax()).min())
def per(x, a, b): return x[(x.index.year >= a) & (x.index.year <= b)]

def stats(net, gross):
    out = {'n': len(net), 'mean_net_bp': net.mean() * 1e4, 'mean_gross_bp': gross.mean() * 1e4, 't': tstat(net),
           'sharpe': net.mean() / net.std() * math.sqrt(12), 'maxdd_pct': maxdd(net) * 100,
           'worst_month_pct': net.min() * 100, 'worst_month': str(net.idxmin()), 'mean_since_2022_bp': per(net, 2022, 2026).mean() * 1e4}
    for p, (a, b) in {'P1': (2000, 2008), 'P2': (2009, 2017), 'P3': (2018, 2026)}.items():
        s = per(net, a, b); out[p] = {'n': len(s), 'mean_net_bp': s.mean() * 1e4, 't': tstat(s), 'sharpe': s.mean() / s.std() * math.sqrt(12)}
    yrs = len(net) / 12
    out['dollars'] = {L: {'per_year': net.sum() * 1e5 * L / yrs, 'total': net.sum() * 1e5 * L, 'worst_month': net.min() * 1e5 * L,
                          'maxdd': maxdd(net) * 1e5 * L, 'per_year_since_2022': per(net, 2022, 2026).mean() * 12 * 1e5 * L} for L in (1, 5, 7)}
    return out

scores = {'H1': EM, 'H2': OGz, 'H3': H3s}
cnt = pd.DataFrame({h: s.notna().sum(axis=1) for h, s in scores.items()})
start = cnt[(cnt >= 6).all(axis=1)].index[0]
months = pd.period_range(start, END - 0, freq='M')  # signal months start..2026-09, holding start+1..2026-10
res, nets = {}, {}
for h, s in scores.items():
    net, gross, Wh = backtest(s, months)
    nets[h] = net; res[h] = stats(net, gross)
    res[h]['flat_months'] = int((Wh.abs().sum(axis=1) == 0).sum())
    res[h]['avg_turnover'] = float(Wh[FOREIGN].diff().abs().sum(axis=1).mean())

# BH over H1-H3, one-sided normal p
ps = {h: 0.5 * math.erfc(res[h]['t'] / math.sqrt(2)) for h in res}
order = sorted(ps, key=ps.get); k = 0
for i, h in enumerate(order, 1):
    if ps[h] <= 0.05 * i / 3: k = i
for i, h in enumerate(order, 1):
    r = res[h]; r['p_one_sided'] = ps[h]; r['bh_q05'] = i <= k
    r['all_periods_pos'] = all(r[p]['mean_net_bp'] > 0 for p in ('P1', 'P2', 'P3'))
    r['PASS'] = bool(r['all_periods_pos'] and r['t'] >= 2 and r['bh_q05'])

# simple carry, reported only
carry_score = pol[CC].reindex(MONTHS)
cnet, cgross, _ = backtest(carry_score, months)
res['carry_ref'] = stats(cnet, cgross)
res['corr_H3_carry'] = float(nets['H3'].corr(cnet))
res['corr_H1_H2'] = float(nets['H1'].corr(nets['H2']))
res['sample'] = {'first_signal': str(start), 'first_hold': str(start + 1), 'last_hold': str(END),
                 'scored_counts_first_signal': cnt.loc[start].to_dict()}

# sanity samples: scores and the latest usable ref month per series at a few signal dates
san = {}
for t in [pd.Period('2008-09', 'M'), pd.Period('2020-05', 'M'), pd.Period('2026-09', 'M')]:
    lr = {f'{c}_{s}': str(econ[(econ.ccy == c) & (econ.series == s) & (econ.avail <= t)].ref.max()) for c in CC for s in ('ip', 'cpi', 'une')}
    san[str(t)] = {'EM': EM.loc[t].round(3).to_dict(), 'OGz': OGz.loc[t].round(3).to_dict(), 'H3': H3s.loc[t].round(3).to_dict(),
                   'og_raw_pct': (og.loc[t] * 100).round(2).to_dict(), 'latest_ref_used': lr}
res['sanity'] = san
# lag check: no value used at t may have avail > t (by construction) -> assert on one example
assert econ[(econ.ccy == 'USD') & (econ.series == 'ip') & (econ.avail <= pd.Period('2026-09', 'M'))].ref.max() == pd.Period('2026-07', 'M')

json.dump(res, open(HERE / 'r17_results.json', 'w'), indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
pd.DataFrame({**nets, 'carry': cnet}).to_csv(HERE / 'r17_monthly.csv')
for h in ('H1', 'H2', 'H3', 'carry_ref'):
    r = res[h]
    print(h, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k not in ('dollars', 'P1', 'P2', 'P3')})
    print('   ', {p: {k: round(v, 2) for k, v in r[p].items()} for p in ('P1', 'P2', 'P3')})
    print('   ', {L: {k: round(v) for k, v in d.items()} for L, d in r['dollars'].items()})
print('corr H3-carry', round(res['corr_H3_carry'], 3), 'corr H1-H2', round(res['corr_H1_H2'], 3), res['sample'])
