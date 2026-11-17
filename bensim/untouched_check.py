"""Final check of the two Reddit strategies on years never used before (FXCM H1, 25 pairs, 2012 - now).

    python -m bensim.untouched_check

Periods: the author's own (CTA: Nov 2012 - Sep 2014; ParallaxFX tester: 2017-2019), all untouched years
2012-01 .. 2021-09, and the recent years 2021-10 .. now that the earlier tests already used.
Rules are exactly bensim.parallax (strict "author", -100 target) and bensim.cta_momentum v1 / v2 -- nothing tuned.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import cta_momentum as cta
from . import parallax as px
from .fxcm_history import OUT

PERIODS = {"CTA author period 2012-11..2014-09": ("2012-11-01", "2014-10-01"),
           "Parallax tester period 2017..2019": ("2017-01-01", "2020-01-01"),
           "ALL untouched 2012-01..2021-09": ("2012-01-01", "2021-10-01"),
           "recent (already used) 2021-10..now": ("2021-10-01", "2030-01-01")}


def line(t: pd.DataFrame, label: str) -> str:
    if t.empty:
        return f"  {label:36} no trades"
    r = t.r.to_numpy()
    ys = t.groupby(pd.to_datetime(t.t_fill, unit="s").dt.year).r.sum()
    return (f"  {label:36} {len(r):5} trades avg {r.mean():+.3f}R total {r.sum():+7.1f}R t {r.mean() / r.std() * np.sqrt(len(r)):+.2f} "
            f"years+ {(ys > 0).sum()}/{len(ys)}")


def main() -> None:
    pairs = sorted(f.stem.replace("_H1", "") for f in OUT.glob("*_H1.npz"))
    v1 = pd.concat([cta.run(s, "fxcm", "v1", "2012-01-01") for s in pairs], ignore_index=True)
    v2 = pd.concat([cta.run(s, "fxcm", "v2", "2012-01-01") for s in pairs], ignore_index=True)
    pxt = pd.concat([px.run_symbol(s, ["D1"], "2012-01-01", None, source="fxcm") for s in pairs], ignore_index=True)
    pxa = pxt[(pxt.variant == "author") & (pxt.target == "-100")]
    pd.concat([v1, v2]).to_csv("data/cta_untouched.csv", index=False)
    pxt.to_csv("data/parallax_untouched.csv", index=False)
    print(f"FXCM H1, {len(pairs)} pairs, 2012 - now. R net of the real spread.")
    for name, (a, b) in PERIODS.items():
        ta, tb = pd.Timestamp(a, tz="UTC").timestamp(), pd.Timestamp(b, tz="UTC").timestamp()
        print(f"\n{name}")
        for label, t in (("CTA v1 (text reading)", v1), ("CTA v2 (picture reading)", v2), ("ParallaxFX strict, -100", pxa)):
            print(line(t[(t.t_fill >= ta) & (t.t_fill < tb)], label))


if __name__ == "__main__":
    main()
