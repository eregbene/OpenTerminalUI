"""Descriptive report, chart examples and the manual-review set for the FVG audit.

python -m bensim_research.fvg_audit_report

Reads data/research/artifacts/bensim/fvg_audit/fvgs.csv.gz (written by fvg_audit). Every table is
single-factor and descriptive: no combination search. "edge" = FVG entry minus the random-time
control on the same FVG (same direction, same R, same window), in percentage points of win rate.
A cell is called CONSISTENT only if |t| >= 3 (clustered by symbol-month) AND the edge has the same
sign before 2024 (DEV) and from 2024 (HOLD). This rule is fixed before reading any result.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from . import data as D
from . import features as F
from .fvg_audit import HTF, OUT

HOLD = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
FLAGS = [c for p in ("A", "B", "C", "D", "E", "T2", "T3") for c in (f"{p}_fill",)] + ["touch", "q25", "q50", "q75", "full", "beyond"]
CHARTS = OUT / "charts"


def load() -> pd.DataFrame:
    df = pd.read_csv(OUT / "fvgs.csv.gz", low_memory=False)
    for c in FLAGS:
        if c in df:
            df[c] = df[c].map({True: 1.0, "True": 1.0, False: 0.0, "False": 0.0}).fillna(0.0)
    for p in ("T2", "T3"):
        df[f"{p}_fill"] = df[f"{p}_mfe"].notna().astype(float)
    df["month"] =df.symbol + pd.to_datetime(df.t_known, unit="s").dt.strftime("%Y%m")
    df["hold"] = df.t_known >= HOLD
    df["htf_rel"] = np.where(df.htf_state == 0, "neutral", np.where(df.htf_state == df.dir, "aligned", "opposed"))
    df["size_b"] = pd.cut(df.size_atr, [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1e9],
                          labels=["<0.10", "0.10-0.20", "0.20-0.30", "0.30-0.40", "0.40-0.50", "0.50-0.75", "0.75-1.00", ">1.00"])
    df["sweep_cls"] = np.select([df.sweep_tier >= 2, df.sweep_tier == 1], ["C major sweep", "B minor sweep"], "A no sweep")
    df["struct_cls"] = df.struct.map({0: "none", 1: "BOS", 2: "MSS/CHoCH"})
    pdz = np.select([df.pd < 0.35, df.pd > 0.65], ["discount", "premium"], "middle")
    df["pd_zone"] = np.where(df.pd.isna(), "unknown", pdz)
    df["pd_rel"] = np.where(df.pd.isna(), "unknown", np.where(pdz == "middle", "middle",
                            np.where((pdz == "discount") == (df.dir == 1), "favourable (bull@discount / bear@premium)", "unfavourable")))
    zone = np.where(df.dir == 1, df.in_demand, df.in_supply).astype(bool)
    anti = np.where(df.dir == 1, df.in_supply, df.in_demand).astype(bool)
    df["sd_rel"] = np.select([zone, anti], ["inside aligned HTF zone", "inside opposing HTF zone"], "no HTF zone")
    df["away_b"] = pd.cut(df.touch_bars, [0, 1, 3, 10, 50, 1e9], labels=["1 bar", "2-3", "4-10", "11-50", ">50"])
    df["dirname"] = np.where(df.dir == 1, "bullish", "bearish")
    return df


def _t(diff: pd.Series, cl: pd.Series) -> float:
    m = diff.groupby(cl).mean().dropna()
    return float(m.mean() / m.std() * np.sqrt(len(m))) if len(m) > 2 and m.std() > 0 else np.nan


def stats(g: pd.DataFrame, p: str = "A") -> dict:
    """Reaction of entry model p vs the control on the same FVGs."""
    f = g[g[f"{p}_fill"] == 1] if f"{p}_fill" in g else g
    out = {"n": len(g), "fills": len(f)}
    if len(f) < 30:
        return out
    for k in (1, 2, 3):
        a, c = f[f"{p}_w{k}"], f[f"K_w{k}"]
        out[f"win{k}%"] = round(100 * a.mean(), 1)
        out[f"ctl{k}%"] = round(100 * c.mean(), 1)
        out[f"edge{k}"] = round(100 * (a - c).mean(), 1)
    d1 = f[f"{p}_w1"] - f.K_w1
    out["t1"] = round(_t(d1, f.month), 1)
    out["edge1 DEV"] = round(100 * d1[~f.hold].mean(), 1)
    out["edge1 HOLD"] = round(100 * d1[f.hold].mean(), 1)
    out["r20-ctl"] = round(float((f[f"{p}_r20"] - f.K_r20).mean()), 3)
    out["MFE med"] = round(float(f[f"{p}_mfe"].median()), 2)
    out["MAE med"] = round(float(f[f"{p}_mae"].median()), 2)
    out["cost R"] = round(float(f.cost_atr.median()), 3)
    out["1:1 net R"] = round(2 * f[f"{p}_w1"].mean() - 1 - float(f.cost_atr.median()), 3)
    out["consistent"] = bool(abs(out["t1"]) >= 3 and np.sign(out["edge1 DEV"]) == np.sign(out["edge1 HOLD"]) != 0) if not np.isnan(out["t1"]) else False
    return out


def by(df: pd.DataFrame, cols: list[str], p: str = "A", min_n: int = 100) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby(cols, observed=True, sort=True):
        if len(g) < min_n:
            continue
        k = k if isinstance(k, tuple) else (k,)
        rows.append({**dict(zip(cols, k)), **stats(g, p)})
    return pd.DataFrame(rows)


def lifecycle_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tf, g in df.groupby("tf", sort=False):
        t = g[g.touch == 1]
        rows.append({
            "tf": tf, "n": len(g), "revisited%": round(100 * g.touch.mean(), 1), "median bars to touch": t.touch_bars.median(),
            "touched after >50 bars (stale)%": round(100 * (t.touch_bars > 50).mean(), 1),
            "25%": round(100 * g.q25.mean(), 1), "50% CE": round(100 * g.q50.mean(), 1), "75%": round(100 * g.q75.mean(), 1),
            "full fill%": round(100 * g.full.mean(), 1), "close beyond%": round(100 * g.beyond.mean(), 1),
            "P(50|25)": round(g.q50.sum() / max(g.q25.sum(), 1), 3), "P(full|75)": round(g.full.sum() / max(g.q75.sum(), 1), 3),
            "P(beyond|full)": round(g.beyond.sum() / max(g.full.sum(), 1), 3),
            "median max penetration": round(float(t.max_pen.median()), 2), "median distinct touches": float(t.n_touches.median()),
            "chained (part of multi-candle run)%": round(100 * g.chained.astype(bool).mean(), 1),
        })
    return pd.DataFrame(rows)


def excursion_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tf, g in df.groupby("tf", sort=False):
        f = g[g.A_fill == 1]
        for name, p in (("FVG first touch (A)", "A"), ("random control (K)", "K")):
            rows.append({"tf": tf, "series": name, "n": len(f),
                         **{f"MFE p{q}": round(float(f[f"{p}_mfe"].quantile(q / 100)), 2) for q in (25, 50, 75)},
                         **{f"MAE p{q}": round(float(f[f"{p}_mae"].quantile(q / 100)), 2) for q in (25, 50, 75)},
                         "P(MFE>MAE)%": round(100 * (f[f"{p}_mfe"] > f[f"{p}_mae"]).mean(), 1),
                         **{f"ret{m} mean": round(float(f[f"{p}_r{m}"].mean()), 3) for m in (5, 10, 20, 50)},
                         "+1R first%": round(100 * f[f"{p}_w1"].mean(), 1), "+2R first%": round(100 * f[f"{p}_w2"].mean(), 1),
                         "+3R first%": round(100 * f[f"{p}_w3"].mean(), 1), "same-bar ambiguous%": round(100 * f[f"{p}_amb"].mean(), 2)})
    return pd.DataFrame(rows)


def touch_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tf, g in df.groupby("tf", sort=False):
        for p, name in (("A", "1st touch"), ("T2", "2nd touch"), ("T3", "3rd touch")):
            rows.append({"tf": tf, "touch": name, **stats(g, p)})
        t2 = g[g.T2_fill == 1]
        for lo, hi, name in ((0, 0.5, "2nd touch, prior penetration <50%"), (0.5, 1.0, "2nd touch, prior 50-100%"), (1.0, 99, "2nd touch, previously fully filled")):
            sub = t2[(t2.T2_prior_pen >= lo) & (t2.T2_prior_pen < hi)]
            rows.append({"tf": tf, "touch": name, **stats(sub.assign(T2_fill=1), "T2")})
    return pd.DataFrame(rows)


def entry_table(df: pd.DataFrame) -> pd.DataFrame:
    names = {"A": "A first touch", "B": "B 25%", "C": "C 50% CE", "D": "D 75%", "E": "E close back outside (confirmation)"}
    rows = []
    for tf, g in df.groupby("tf", sort=False):
        for p, name in names.items():
            s = stats(g, p)
            rows.append({"tf": tf, "entry": name, "fill% of all FVGs": round(100 * s["fills"] / max(s["n"], 1), 1), **s})
    return pd.DataFrame(rows)


def md(t: pd.DataFrame) -> str:
    if t.empty:
        return "_no cells with enough data_\n"
    cols = list(t.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in t.itertuples(index=False):
        lines.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else (f"**{v}**" if v is True else str(v)) for v in r) + " |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ charts

_cache: dict = {}


def _sym(symbol: str) -> dict:
    if symbol not in _cache:
        _cache.clear()
        base = D.load(symbol, "M15")[0]
        tfs = {tf: base if tf == "M15" else D.resample(base, tf) for tf in ("M15", "H1", "H4", "D1", "W1")}
        _cache[symbol] = {"tfs": tfs, "st": {tf: F.structure(tfs[tf], 2) for tf in ("M15", "H1", "H4", "D1")}}
    return _cache[symbol]


def chart(r, path) -> None:
    """Own-timeframe candles with FVG, CE, liquidity, swing pivots, entry/stop/1R/2R, MFE/MAE."""
    c = _sym(r.symbol)
    b, st = c["tfs"][r.tf], c["st"][r.tf]
    P = D.TF_SECONDS[r.tf]
    j, d, R = int(r.j), int(r.dir), float(r.atr)
    ti = j + int(r.touch_bars) if r.touch == 1 else None
    lo = max(0, j - 30)
    hi = min(len(b), (ti if ti is not None else j) + 35, lo + 130)
    E = r.top if d == 1 else r.bottom
    lv = {"entry (A)": E, "stop -1R": E - d * R, "+1R": E + d * R, "+2R": E + 2 * d * R}
    if r.A_fill == 1:
        lv["MFE"] = E + d * r.A_mfe * R
        lv["MAE"] = E - d * r.A_mae * R
    liq = {}
    if not np.isnan(r.dist_target_liq):
        liq["liq target"] = b.c[j] + d * r.dist_target_liq * R
    if not np.isnan(r.dist_behind_liq):
        liq["liq behind"] = b.c[j] - d * r.dist_behind_liq * R
    ymin, ymax = min(b.l[lo:hi].min(), *lv.values()), max(b.h[lo:hi].max(), *lv.values())
    span = ymax - ymin
    liq = {k: v for k, v in liq.items() if ymin - span < v < ymax + span}  # far levels: noted in the header only
    if liq:
        ymin, ymax = min(ymin, *liq.values()), max(ymax, *liq.values())
    pad = (ymax - ymin) * 0.04
    ymin, ymax = ymin - pad, ymax + pad
    W, H, L, T, Rm, Bm = 1100, 640, 10, 112, 120, 20
    im = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(im, "RGBA")
    font = ImageFont.load_default(size=13)
    n = hi - lo
    cw = (W - L - Rm) / n

    def X(i: float) -> float:
        return L + (i - lo + 0.5) * cw

    def Y(p: float) -> float:
        return T + (ymax - p) / (ymax - ymin) * (H - T - Bm)

    fx0, fx1 = X(j - 2) - cw / 2, X(hi - 1) + cw / 2
    g.rectangle([fx0, Y(r.top), fx1, Y(r.bottom)], fill=(34, 197, 94, 60) if d == 1 else (239, 68, 68, 60))
    mid = (r.top + r.bottom) / 2
    for x in range(int(fx0), int(fx1), 8):
        g.line([x, Y(mid), x + 4, Y(mid)], fill=(100, 100, 100))
    for i in range(lo, hi):
        up = b.c[i] >= b.o[i]
        col = (22, 163, 74) if up else (220, 38, 38)
        g.line([X(i), Y(b.h[i]), X(i), Y(b.l[i])], fill=col)
        y0, y1 = sorted((Y(b.o[i]), Y(b.c[i])))
        g.rectangle([X(i) - cw * 0.35, y0, X(i) + cw * 0.35, max(y1, y0 + 1)], fill=col)
    for p_ in st.ph[(st.ph >= lo) & (st.ph < hi) & (st.ph + 2 <= hi)]:
        g.polygon([(X(p_), Y(b.h[p_]) - 10), (X(p_) - 4, Y(b.h[p_]) - 16), (X(p_) + 4, Y(b.h[p_]) - 16)], fill=(30, 64, 175))
    for p_ in st.pl[(st.pl >= lo) & (st.pl < hi) & (st.pl + 2 <= hi)]:
        g.polygon([(X(p_), Y(b.l[p_]) + 10), (X(p_) - 4, Y(b.l[p_]) + 16), (X(p_) + 4, Y(b.l[p_]) + 16)], fill=(30, 64, 175))
    g.line([X(j), T, X(j), H - Bm], fill=(150, 150, 150, 120))
    sx = X(ti) if ti is not None else X(j)
    style = {"entry (A)": (0, 0, 0), "stop -1R": (220, 38, 38), "+1R": (22, 163, 74), "+2R": (22, 163, 74), "MFE": (124, 58, 237), "MAE": (234, 88, 12)}
    for k, v in lv.items():
        g.line([sx, Y(v), W - Rm, Y(v)], fill=style[k], width=1)
        g.text((W - Rm + 4, Y(v) - 7), k, fill=style[k], font=font)
    for k, v in liq.items():
        for x in range(L, W - Rm, 10):
            g.line([x, Y(v), x + 5, Y(v)], fill=(37, 99, 235))
        g.text((W - Rm + 4, Y(v) - 7), k, fill=(37, 99, 235), font=font)
    if ti is not None:
        g.ellipse([X(ti) - 5, Y(E) - 5, X(ti) + 5, Y(E) + 5], outline=(0, 0, 0), width=2)
    when = pd.Timestamp(int(r.t_known), unit="s").strftime("%Y-%m-%d %H:%M UTC")
    res = {1.0: "WIN +1R first", 0.0: "LOSS -1R first", 0.5: "ambiguous"}.get(r.A_w1, "unresolved / no fill")
    head = [
        f"{r.symbol} {r.tf} {r.dirname.upper()} FVG  known {when}  gap {r.bottom:.5g}-{r.top:.5g}  size {r.size_atr:.2f} ATR  ATR {R:.5g}",
        f"displacement {r.disp_class} (body {r.disp_atr:.2f} ATR, body/range {r.body_ratio:.2f})  session {r.session}  HTF({HTF[r.tf]}) {r.htf_rel}  "
        f"{r.sweep_cls}  structure {r.struct_cls}",
        f"location: {r.sd_rel}  premium/discount {r.pd_rel.split(' ')[0]}  near prev-day H/L {bool(r.near_pd)}  near prev-week H/L {bool(r.near_pw)}  "
        f"near equal H/L {bool(r.near_eq)}  chained {bool(r.chained)}",
        f"outcome model A: {res}  2R {r.A_w2}  3R {r.A_w3}  MFE {r.A_mfe:.2f}R  MAE {r.A_mae:.2f}R  ret20 {r.A_r20:.2f}R  "
        f"touches {r.n_touches:.0f}  max penetration {r.max_pen:.0%}  full fill {bool(r.full)}",
        "shaded = FVG, dashed grey = CE 50%, circle = first touch, blue triangles = confirmed swings, blue dashes = nearest liquidity at creation, grey line = FVG known",
    ]
    for i, s in enumerate(head):
        g.text((L, 6 + i * 20), s, fill=(17, 24, 39), font=font)
    im.save(path, optimize=True)


def draw_set(rows: pd.DataFrame, sub: str) -> list[str]:
    (CHARTS / sub).mkdir(parents=True, exist_ok=True)
    paths = []
    for i, r in enumerate(rows.sort_values(["symbol"]).itertuples(index=False)):
        p = CHARTS / sub / f"{sub}_{i:03d}_{r.symbol}_{r.tf}_{int(r.t_known)}.png"
        chart(r, p)
        paths.append(str(p.relative_to(OUT)).replace("\\", "/"))
    return paths


def gallery(df: pd.DataFrame, title: str, path) -> None:
    keep = ["review_id", "chart", "symbol", "tf", "dirname", "time_utc", "size_atr", "disp_class", "session", "htf_rel", "sweep_cls",
            "struct_cls", "sd_rel", "pd_rel", "A_w1", "A_mfe", "A_mae"]
    cards = []
    for r in df[[c for c in keep if c in df]].itertuples(index=False):
        meta = " · ".join(f"{k}={getattr(r, k)}" for k in keep[2:] if hasattr(r, k))
        cards.append(f'<figure><img loading="lazy" src="{r.chart}"><figcaption>#{r.review_id} {meta}</figcaption></figure>')
    path.write_text(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}img{{max-width:100%;border:1px solid #ddd}}
figure{{margin:0 0 28px}}figcaption{{color:#444}}</style><h1>{title}</h1>{''.join(cards)}""", encoding="utf-8")


def main() -> None:
    df = load()
    rep: dict = {}
    sections = []

    def add(key: str, title: str, t: pd.DataFrame, note: str = "") -> None:
        rep[key] = t.to_dict("records")
        sections.append(f"### {title}\n\n{note}\n\n{md(t)}")

    add("lifecycle", "3. Lifecycle (all FVGs, no filters)", lifecycle_table(df))
    add("timeframe", "17. Timeframe comparison (model A first touch vs control)", by(df, ["tf"]))
    add("instrument", "16. Instrument comparison (H1 and H4)", by(df[df.tf.isin(["H1", "H4"])], ["tf", "symbol"]))
    add("size", "4. FVG size (ATR-normalised)", by(df, ["tf", "size_b"]))
    add("displacement", "5. Displacement", by(df, ["tf", "disp_class"]), "weak = body < 0.5 ATR; strong = body >= 1.0 ATR and body/range >= 0.6; normal otherwise.")
    add("session", "6. Session (middle candle, UTC hours; M15 and H1)", by(df[df.tf.isin(["M15", "H1"])], ["tf", "session"]))
    add("htf", "7. HTF alignment (HTF = " + ", ".join(f"{k}->{v}" for k, v in HTF.items()) + ")", by(df, ["tf", "dirname", "htf_state"]),
        "htf_state: 1 bullish, -1 bearish, 0 neutral (HTF efficiency ratio < 0.2).")
    add("liquidity", "8. Liquidity sweep before the FVG", by(df, ["tf", "sweep_cls"]),
        "Sweep in candle 1-6 .. candle 3 of a level on the FVG's opposite side; major = HTF swing, Asia, previous day/week, or equal highs/lows.")
    add("structure", "9. Structure event at creation", by(df, ["tf", "struct_cls"]))
    loc = [by(df, ["tf", c]).rename(columns={c: "value"}).assign(factor=c) for c in ("sd_rel", "pd_rel", "near_pd", "near_pw", "near_eq")]
    lt = pd.concat(loc, ignore_index=True)
    add("location", "10. Location", lt[["factor"] + [c for c in lt.columns if c != "factor"]])
    add("touch", "11. First vs later touches (model A at each touch)", touch_table(df))
    add("entry", "12. Entry models", entry_table(df))
    add("excursion", "13-15. Penetration, MFE/MAE and 1R/2R/3R excursion (R = 1 ATR)", excursion_table(df))
    add("away", "Extra: bars price stayed away before the first touch (known at the touch, so usable live)", by(df, ["tf", "away_b"]),
        "A discretionary trader usually marks gaps price has left behind; most algorithmic FVGs are re-touched within 1-3 bars.")
    add("away_size", "Extra: price stayed away 4+ bars AND size >= 0.3 ATR (the 'visible imbalance' a human marks)",
        by(df.assign(visible=(df.touch_bars >= 4) & (df.size_atr >= 0.3)), ["tf", "visible"]))
    add("vol", "Extra: tick volume of the displacement candle (FX only, not gold)",
        by(df[df.vol_ratio.notna()].assign(vol_b=pd.qcut(df.vol_ratio.dropna(), 4, labels=["Q1 low", "Q2", "Q3", "Q4 high"])), ["tf", "vol_b"]))

    # visual examples and the manual-review set
    rng = np.random.default_rng(7)
    vis = df[(df.A_fill == 1) & df.tf.isin(["M15", "H1", "H4"])]
    pick = {
        "win": vis[vis.A_w1 == 1.0],
        "loss": vis[vis.A_w1 == 0.0],
        "neutral": vis[vis.A_w1.isna() | (vis.A_w1 == 0.5) | (vis.A_r20.abs() < 0.1)],
    }
    examples = {}
    for name, pool in pick.items():
        rows = pool.iloc[rng.choice(len(pool), size=min(50, len(pool)), replace=False)]
        examples[name] = draw_set(rows, name)
    rev = pd.concat([g.iloc[rng.choice(len(g), size=min(50, len(g)), replace=False)] for _, g in df.groupby("tf")], ignore_index=True)
    rev = rev.sort_values(["symbol", "tf", "t_known"]).reset_index(drop=True)
    rev["review_id"] = np.arange(1, len(rev) + 1)
    rev["time_utc"] = pd.to_datetime(rev.t_known, unit="s").dt.strftime("%Y-%m-%d %H:%M")
    rev["chart"] = draw_set(rev, "review")
    rev["human_meaningful_YN"] = ""
    rev["human_notes"] = ""
    drop = [c for c in rev.columns if c.startswith(("B_", "C_", "D_", "T3_")) or c in ("month",)]
    rev.drop(columns=drop).to_csv(OUT / "manual_review_200.csv", index=False)
    gallery(rev, "FVG manual review - 200 random FVGs (50 per timeframe)", OUT / "manual_review.html")
    for name in examples:
        gallery(pd.DataFrame({"review_id": range(1, len(examples[name]) + 1), "chart": examples[name]}), f"FVG examples - {name}", OUT / f"examples_{name}.html")
    rep["counts"] = {"fvgs": len(df), "by_tf": df.tf.value_counts().to_dict(), "by_symbol": df.symbol.value_counts().to_dict()}
    (OUT / "fvg_audit_report.json").write_text(json.dumps(rep, indent=1, default=str))
    (OUT / "fvg_audit_tables.md").write_text("\n".join(sections), encoding="utf-8")
    print("tables ->", OUT / "fvg_audit_tables.md")


if __name__ == "__main__":
    main()
