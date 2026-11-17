"""Re-check the round 34 survivors with the REAL spread at the exact entry and exit bar (FXCM ask-bid), and without the
rollover / Friday-close hours. A pattern is kept only if it stays positive with t >= 2 in 2023-26 under both checks.
    python reports/round34/spread_check.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round34")
from discover import TFS, P1, P2, daily_t  # noqa: E402
OUT = Path(ROOT + "/reports/round34")


def main():
    from bensim.fxcm_history import load
    ev = pd.read_pickle(OUT / "events.pkl")  # our own local cache
    sv = pd.read_csv(OUT / "survivors.csv")
    sv = sv[(sv.net_f > 0) & (sv.t_f >= 2)]
    ev["period"] = np.select([ev.t < P1, ev.t < P2], ["disc", "val"], "final")
    # real spread at entry and exit (M5 bar that ends at that moment), in price; 0 or missing -> that hour's median
    ev["spr_in"], ev["spr_out"], ev["pip"] = np.nan, np.nan, np.where(ev.sym.str.endswith("JPY"), 0.01, 1e-4)
    need = {int(h) for h in sv.h}
    for h in need:
        ev[f"spr_out{h}"] = np.nan
    for sym in ev.sym.unique():
        z = load(sym, "M5")
        t, spr = z["t"], z["spr"].astype(float)
        hr = (t % 86400) // 3600
        med = pd.Series(np.where(spr > 0, spr, np.nan)).groupby(hr).median()
        spr = np.where(spr > 0, spr, med.reindex(hr).to_numpy())
        m = ev.sym == sym
        for tf, sec in TFS.items():
            mm = m & (ev.tf == tf)
            tt = ev.loc[mm, "t"].to_numpy()
            ev.loc[mm, "spr_in"] = spr[np.clip(np.searchsorted(t, tt) - 1, 0, len(t) - 1)]
            for h in need:
                ev.loc[mm, f"spr_out{h}"] = spr[np.clip(np.searchsorted(t, tt + h * sec) - 1, 0, len(t) - 1)]
    hour = (ev.t % 86400) // 3600
    dow = ((ev.t // 86400) + 3) % 7
    print(f"{len(sv)} survivors re-checked with the REAL spread at the entry/exit bar (FADE of a down candle = buy at the ask, etc.)\n")
    print(f"{'TF':4s} {'h':>3s} {'condition':38s} | {'2023-26 before':^16s} | {'real spread 2023-26':^22s} | {'no 20-24h UTC / Fri close':^24s}")
    keep = 0
    for _, r in sv.iterrows():
        x = ev[ev.tf == r.tf]
        mk = pd.Series(True, index=x.index)
        for part in r.cond.split(" & "):
            c, v = part.split("=")
            col = x[c]
            mk &= col.astype(str) == v
        side = 1 if r.side == "FOLLOW" else -1
        g = side * x[f"r{int(r.h)}"]
        long_ = (side * x.e) > 0
        atr = (x.cost * 0 + 1)  # r is in ATR units; rebuild ATR from the stored cost: cost = (spr_hour + 0.2 pip) / atr
        sec = TFS[r.tf]
        # ATR in price: back out from the stored cost using the hour-median spread is not stored -> use ratio of real/median spread
        real_cost = x.cost * ((np.where(long_, x.spr_in, x[f"spr_out{int(r.h)}"]) + 0.2 * x.pip) /
                              (x.cost * 0 + np.nan_to_num(x.spr_in.groupby(hour[x.index]).transform("median")) + 0.2 * x.pip))
        net_real = g - real_cost
        fin = mk & (x.period == "final")
        quiet = fin & ~hour[x.index].between(20, 23) & ~((dow[x.index] == 4) & (hour[x.index] >= 19)) & \
            ~(((x.t + int(r.h) * sec) // 86400 + 3) % 7 >= 5)
        a, b = net_real[fin], net_real[quiet]
        ta = daily_t(a.to_numpy(), x.t[fin].to_numpy()) if len(a) > 2 else np.nan
        tb = daily_t(b.to_numpy(), x.t[quiet].to_numpy()) if len(b) > 2 else np.nan
        ok = len(b) > 100 and b.mean() > 0 and tb >= 2 and a.mean() > 0 and ta >= 2
        keep += ok
        print(f"{r.tf:4s} {int(r.h):3d} {r.cond[:38]:38s} | {r.net_f:+.3f} t{r.t_f:4.1f}   | n {len(a):5d} {a.mean():+.3f} t{ta:5.1f} | "
              f"n {len(b):5d} {b.mean() if len(b) else np.nan:+.3f} t{tb:5.1f} {'KEEP' if ok else ''}")
    print(f"\nsurvive the real-spread AND no-rollover checks: {keep} of {len(sv)}")


if __name__ == "__main__":
    main()
