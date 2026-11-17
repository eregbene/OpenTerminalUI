"""Round 33b: PATTERN MEMORY -- "it has the past and can see". For every imbalance (round 32/33 definition, H1..D1) the chart
picture = the last 30 candles of its timeframe + the last 10 daily candles, all in ATR units around the current price
(mirrored for spikes up). For each new imbalance, find the K most similar pictures in the PAST and average what happened next
(FILL / FOLLOW / HOLD / BREAK results). Trade only when the look-alikes agree strongly.
K and the threshold are chosen on 2021-10..2024-11 only (library < 2023-06, checked on 2023-06..2024-11);
then the library = all of 2021-10..2024-11 and it is scored on 2024-11..2026-11 (never seen).
    python reports/round33/analogs.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round33")
OUT = Path(ROOT + "/reports/round33")
TFS = {"H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400}
SPLIT = pd.Timestamp("2024-11-07", tz="UTC").timestamp()
VAL = pd.Timestamp("2023-06-01", tz="UTC").timestamp()
DEC = ("FILL", "FOLLOW", "HOLD", "BREAK")


def pics(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from bensim.backtest import load_m5, resample
    ev = pd.read_pickle(OUT / "events.pkl")  # our own local cache
    ev = ev[(ev.sym == sym) & ev.tf.isin(TFS)]
    raw = load_m5(sym, "2021-01-01", "2026-12-31")
    out = []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        db_ = resample(m, 86400)
        dtr = np.r_[np.nan, np.maximum(db_["high"][1:], db_["close"][:-1]) - np.minimum(db_["low"][1:], db_["close"][:-1])]
        datr = pd.Series(dtr).rolling(14).mean().to_numpy()
        for tf, sec in TFS.items():
            b = resample(m, sec)
            tr = np.r_[np.nan, np.maximum(b["high"][1:], b["close"][:-1]) - np.minimum(b["low"][1:], b["close"][:-1])]
            atr = pd.Series(tr).rolling(14).mean().to_numpy()
            close_t = b["t"] + sec
            for _, e in ev[(ev.dir == d) & (ev.tf == tf)].iterrows():
                i = int(np.searchsorted(close_t, e.created))
                if i < 30 or close_t[i] != e.created:
                    continue
                c0, a = b["close"][i], atr[i]
                own = np.concatenate([(b[k][i - 29:i + 1] - c0) / a for k in ("open", "high", "low", "close")])
                j = int(np.searchsorted(db_["t"] + 86400, e.created, "right")) - 1  # last CLOSED daily bar
                if j < 10 or not np.isfinite(datr[j]):
                    continue
                day = np.concatenate([(db_[k][j - 9:j + 1] - c0) / datr[j] for k in ("open", "high", "low", "close")])
                out.append((sym, d, tf, e.created, np.concatenate([own, 0.5 * day]).astype(np.float32)))
    return out


def knn_pred(lib_x, lib_y, q_x, k):
    from sklearn.neighbors import NearestNeighbors
    nn = NearestNeighbors(n_neighbors=k).fit(lib_x)
    idx = nn.kneighbors(q_x, return_distance=False)
    return np.nanmean(lib_y[idx], axis=1)


def tstat(x):
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    f = OUT / "pictures.pkl"  # our own local cache
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(pics, ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]) for r in part]
        pd.DataFrame(rows, columns=["sym", "dir", "tf", "created", "x"]).to_pickle(f)
    px = pd.read_pickle(f)
    ev = pd.read_pickle(OUT / "events.pkl").merge(px, on=["sym", "dir", "tf", "created"])
    print(f"Round 33b: pattern memory (30 candles + 10 daily candles), {len(ev)} imbalances H1..D1, 7 pairs\n")
    print(f"{'TF':4s} {'decision':7s} {'K':>3s} | {'checked on 2023-06..2024-11':^30s} | {'TEST 2024-11..2026-11 (never seen)':^44s} | test deciles low->high")
    res = []
    for tf in TFS:
        g = ev[ev.tf == tf]
        X = np.stack(g.x.to_numpy())
        for dcs in DEC:
            y = g[f"R_{dcs}"].to_numpy(dtype=float)
            ok = np.isfinite(y)
            lib = (g.created < VAL).to_numpy() & ok
            val = ((g.created >= VAL) & (g.created < SPLIT)).to_numpy() & ok
            tst = (g.created >= SPLIT).to_numpy() & ok
            if lib.sum() < 500 or val.sum() < 200 or tst.sum() < 200:
                continue
            best = None
            for k in (25, 50, 100):
                pv = knn_pred(X[lib], y[lib], X[val], k)
                for q in (0.8, 0.9):
                    thr = np.quantile(pv, q)
                    r = y[val][pv > thr]
                    if len(r) >= 50 and (best is None or r.sum() > best[0]):
                        best = (r.sum(), k, thr, len(r), r.mean())
            if best is None or best[0] <= 0:
                print(f"{tf:4s} {dcs:7s}   - | no K/threshold made money on the check years -> no trades")
                continue
            _, k, thr, nv, mv = best
            full = (g.created < SPLIT).to_numpy() & ok
            pt = knn_pred(X[full], y[full], X[tst], k)
            r = y[tst][pt > thr]
            dec = pd.qcut(pt, 10, labels=False, duplicates="drop")
            curve = pd.Series(y[tst]).groupby(dec).mean()
            print(f"{tf:4s} {dcs:7s} {k:3d} | n {nv:5d} {mv:+.3f}R {best[0]:+7.1f}R       | n {len(r):5d} win {100 * (r > 0).mean():4.1f}% {r.mean():+.3f}R "
                  f"{r.sum():+7.1f}R t {tstat(pd.Series(r)):+4.1f} | " + " ".join(f"{v:+.2f}" for v in curve))
            res.append((tf, dcs, len(r), r.sum(), tstat(pd.Series(r))))
    print("\nVERDICT (pass = total > 0 and t >= 2 on the unseen 2024-26):")
    for tf, dcs, n, tot, t_ in res:
        print(f"   {tf:4s} {dcs:7s} {n:5d} trades total {tot:+7.1f}R t {t_:+4.1f} -> {'PASS' if tot > 0 and t_ >= 2 else 'FAIL'}")


if __name__ == "__main__":
    main()
