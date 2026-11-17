"""Round 8k: why the live model (broker bars) picks different trades than the 10-year test (FXCM cache).
Same live model scores both feature sets for Sep-Oct 2026; compare feature by feature, then swap one feature group at a
time (broker -> FXCM values) to see which one moves the signals. Read-only."""
import pickle, sys
import numpy as np, pandas as pd
sys.path.insert(0, "D:/Devops/trading/bensim_phase20_dev")
from intel import flow_shadow as FS  # noqa: E402
F, em = FS.F, FS.em
L5 = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]
A, B = pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-10")
mdl = F.model(); feats = mdl["feats"]

def pred(f):
    pl = np.mean([ml.predict(f[feats]) for ml, _ in mdl["models"]], axis=0)
    ps = np.mean([ms.predict(f[feats]) for _, ms in mdl["models"]], axis=0)
    return np.maximum(pl, ps), np.where(pl >= ps, 1, -1)

# broker features (exactly like the live engine / r8i)
frames = []
for s in F.UNIVERSE:
    m = F.h1(s, 2000)
    if not len(m["t"]): continue
    hrs = ((m["t"] + 3600) // 3600 % 24).astype(int)
    m["spr"] = np.array([mdl["spread_bp"].get((s, h), np.nan) for h in hrs]) / 1e4 * m["close"]
    frames.append(em.features(s, m))
br = F.add_gaps(em.add_strength(pd.concat(frames, ignore_index=True)))
br = br[(br.t >= A) & (br.t < B) & br.symbol.isin(L5)]
# FXCM features (the 10-year test's cache) with the live yields so only bars/spread differ
fx = pickle.load(open("D:/Devops/trading/OpenTerminalUI/" + str(em.PATH), "rb"))
fx = fx[(fx.t >= A) & (fx.t < B) & fx.symbol.isin(L5)].drop(columns=[c for c in fx.columns if c.startswith("gap_")], errors="ignore")
fx = F.add_gaps(fx)
k = ["symbol", "t"]
M = br[k + feats].merge(fx[k + feats], on=k, suffixes=("_br", "_fx"))
M = M[~M.t.dt.hour.isin([20, 21, 22, 23, 0])]
print("matched rows", len(M), "of broker", len(br), "fxcm", len(fx))
print("\nfeature differences (median |broker - fxcm|, and correlation):")
for c in feats:
    a, b = M[c + "_br"], M[c + "_fx"]
    print(f"  {c:18} med|diff| {np.nanmedian(np.abs(a - b)):10.5f}  corr {a.corr(b):6.3f}")
P = {}
for name, src in (("broker", "_br"), ("fxcm", "_fx")):
    P[name] = pred(M[[c + src for c in feats]].set_axis(feats, axis=1))
thr = mdl["thr"]
sb, sf = P["broker"][0] > thr, P["fxcm"][0] > thr
print(f"\nsignals above threshold: broker {sb.sum()}, fxcm {sf.sum()}, both {(sb & sf).sum()}; prediction corr {np.corrcoef(P['broker'][0], P['fxcm'][0])[0,1]:.3f}")
groups = {"spr": ["spr"], "returns": [c for c in feats if c.startswith("ret_")], "vol_ratio": ["vol_ratio"],
          "time": ["hour", "dow", "days_to_month_end", "day_of_month"], "day range pos": ["pos_prev_day", "pos_today"],
          "strength": [c for c in feats if c.startswith("str_")], "yield gaps": [c for c in feats if c.startswith("gap_")]}
print("\nswap ONE group broker->FXCM, how many signals then agree with FXCM:")
base = M[[c + "_br" for c in feats]].set_axis(feats, axis=1)
for g, cols in groups.items():
    x = base.copy()
    for c in cols: x[c] = M[c + "_fx"].to_numpy()
    p = pred(x)[0]; s = p > thr
    print(f"  {g:14} agree-with-fxcm {(s == sf).mean():.1%}  both-signal {(s & sf).sum():3d}  pred corr to fxcm {np.corrcoef(p, P['fxcm'][0])[0,1]:.3f}")

# path dependence: average outcome of EVERY signal hour (no one-trade-per-pair chaining), broker prices, live costs
mid = {s: pd.Series(g.mid.to_numpy(), index=g.t) for s, g in br.groupby("symbol")}
def fwd(row_sym, t, d):
    e = t + pd.Timedelta(hours=6); s = mid[row_sym]
    if e not in s.index or t not in s.index: return np.nan
    c = mdl["spread_bp"].get((row_sym, t.hour), np.nan) / 2 + mdl["spread_bp"].get((row_sym, e.hour), np.nan) / 2 + 0.3
    return d * (s[e] - s[t]) / s[t] * 1e4 - c
for name in ("broker", "fxcm"):
    p, d = P[name]; sel = p > thr
    r = np.array([fwd(s, t, dd) for s, t, dd in zip(M.symbol[sel], M.t[sel], d[sel])])
    print(f"every signal hour ({name} features): n {np.isfinite(r).sum()}, mean {np.nanmean(r):.2f} bp")
