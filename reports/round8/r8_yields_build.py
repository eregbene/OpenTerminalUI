"""Build 2-year government yields for GBP (BoE nominal spot curve, 2y), CHF (SNB rendoblid '2J'), NZD (RBNZ B2 daily,
2-year government bond) from the raw downloads in data/macro/yields_ext/raw (read-only), and merge them with the live
file's USD / EUR / JPY / CAD into a NEW file data/macro/yields_ext/yields_2y_plus.csv. The live yields_2y.csv is never
touched. AUD: RBA blocks automated downloads (403) -> not available.
    python reports/round8/r8_yields_build.py
"""
from __future__ import annotations

import io
import zipfile

import openpyxl
import pandas as pd

R = "D:/Devops/trading/OpenTerminalUI/data/macro/yields_ext/raw/"
OUT = "D:/Devops/trading/OpenTerminalUI/data/macro/yields_ext/yields_2y_plus.csv"


def gbp() -> pd.Series:
    z = zipfile.ZipFile(R + "boe_glcnominalddata.zip")
    parts = []
    for name in z.namelist():
        if not any(y in name for y in ("2005", "2016", "2025")):
            continue
        ws = openpyxl.load_workbook(io.BytesIO(z.read(name)), read_only=True, data_only=True)["4. spot curve"]
        hdr, rows = None, []
        for row in ws.iter_rows(values_only=True):
            if row and row[0] == "years:":
                hdr = list(row)
                continue
            if hdr and hasattr(row[0], "year"):
                k = hdr.index(2)
                if row[k] is not None:
                    rows.append((pd.Timestamp(row[0]), float(row[k])))
        parts.append(pd.Series(dict(rows)))
    return pd.concat(parts).sort_index().groupby(level=0).last().rename("GBP")


def chf() -> pd.Series:
    d = pd.read_csv(R + "snb_rendoblid.csv", sep=";", skiprows=3)
    d = d[d.D0 == "2J"].dropna(subset=["Value"])
    return pd.Series(d.Value.astype(float).to_numpy(), index=pd.to_datetime(d.Date)).sort_index().rename("CHF")


def nzd() -> tuple[pd.Series, str]:
    """RBNZ B2: row 0 = series group, row 1 = maturity; the 2-year SECONDARY-MARKET GOVERNMENT BOND column."""
    out = {}
    for f in ("rbnz_hb2-daily-1985-2017.xlsx", "rbnz_hb2_daily.xlsx"):
        rows = list(openpyxl.load_workbook(R + f, data_only=True)["Data"].iter_rows(values_only=True))
        col = next(j for j, (g, m) in enumerate(zip(rows[0], rows[1])) if isinstance(g, str) and "government bond" in g.lower() and m == "2 year")
        for r in rows[5:]:
            if r and hasattr(r[0], "year") and isinstance(r[col], (int, float)):
                out[pd.Timestamp(r[0])] = float(r[col])
    return pd.Series(out).sort_index().rename("NZD"), "Secondary market government bond closing yields, 2 year"


def main() -> None:
    live = pd.read_csv("D:/Devops/trading/OpenTerminalUI/data/macro/yields_2y.csv", index_col=0, parse_dates=True)
    g, c = gbp(), chf()
    n, ncol = nzd()
    Y = live.join([g, c, n], how="outer").sort_index()
    Y = Y[Y.index >= "2010-01-01"]
    Y.to_csv(OUT)
    print("NZD column used:", ncol)
    print(Y.describe().round(2).T[["count", "mean", "min", "max"]])
    print(Y.tail(3))
    print("coverage from 2012:", Y[Y.index >= "2012-01-01"].notna().mean().round(3).to_dict())


if __name__ == "__main__":
    main()
