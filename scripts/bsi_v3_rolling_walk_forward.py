from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "scripts" / "bsi_v3_forward_adaptive_validation.py"
OUT = ROOT / "data" / "research" / "bsi_v3_rolling_walk_forward_jan_august_2026.json"
DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_ROLLING_WALK_FORWARD_JAN_AUGUST_2026.md"

MONTHS = [
    ("february", "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"),
    ("march", "2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00"),
    ("april", "2026-01-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00"),
    ("may", "2026-01-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00", "2026-05-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"),
    ("june", "2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
    ("july", "2026-01-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
    ("august", "2026-01-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"),
]


def _run(label: str, train_start: str, train_end: str, test_start: str, test_end: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "BSI_V3_FORWARD_LABEL": f"rolling_{label}_2026",
            "BSI_V3_FORWARD_TRAIN_START": train_start,
            "BSI_V3_FORWARD_TRAIN_END": train_end,
            "BSI_V3_FORWARD_TEST_START": test_start,
            "BSI_V3_FORWARD_TEST_END": test_end,
        }
    )
    subprocess.run([sys.executable, str(FORWARD)], cwd=ROOT, env=env, check=True)
    path = ROOT / "data" / "research" / f"bsi_v3_forward_adaptive_rolling_{label}_2026.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _combine(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_trades = sum(row["test_forward_adaptive"]["trades"] for row in rows)
    total_wins = sum(row["test_forward_adaptive"]["wins"] for row in rows)
    total_losses = sum(row["test_forward_adaptive"]["losses"] for row in rows)
    total_net_r = sum(row["test_forward_adaptive"]["net_r"] for row in rows)
    gross_profit = gross_loss = 0.0
    max_dd = 0.0
    max_ls = 0
    for row in rows:
        stats = row["test_forward_adaptive"]
        profit_factor = stats.get("profit_factor")
        net_r = float(stats.get("net_r") or 0.0)
        if profit_factor and profit_factor != 1:
            loss = abs(net_r / (profit_factor - 1))
            profit = profit_factor * loss
            gross_profit += profit
            gross_loss += loss
        max_dd = max(max_dd, stats["max_drawdown_r"])
        max_ls = max(max_ls, stats["max_losing_streak"])
    return {
        "trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": round(total_wins / total_trades * 100, 2) if total_trades else None,
        "net_r": round(total_net_r, 4),
        "expectancy": round(total_net_r / total_trades, 4) if total_trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_month_drawdown_r": round(max_dd, 4),
        "max_month_losing_streak": max_ls,
    }


def _table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(out)


def main() -> None:
    results = [_run(*month) for month in MONTHS]
    combined = _combine(results)
    estimates = {}
    for risk_percent in [0.10, 0.25, 0.50]:
        profit = 20000 * (risk_percent / 100.0) * combined["net_r"]
        estimates[f"{risk_percent:.2f}%_risk"] = {
            "profit": round(profit, 2),
            "ending_balance": round(20000 + profit, 2),
        }
    payload = {
        "methodology": "BSI_V3_ROLLING_WALK_FORWARD_EXPANDING_WINDOW",
        "periods": results,
        "combined_forward_result": combined,
        "account_estimates_20k": estimates,
        "september_profile_source": "Use the august row's Jan-July trained allowed buckets for September 2026.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = [["Test Month", "Train End", "Trades", "WR", "Net R", "PF", "Max DD R"]]
    for row in results:
        stats = row["test_forward_adaptive"]
        rows.append([row["label"].replace("rolling_", "").replace("_2026", ""), row["train_window"]["end"], stats["trades"], stats["win_rate"], stats["net_r"], stats["profit_factor"], stats["max_drawdown_r"]])
    estimates_text = "\n".join(f"- `{risk}`: profit `${item['profit']}`, ending balance `${item['ending_balance']}`" for risk, item in estimates.items())
    DOC.write_text(
        "# BSI V3 Rolling Walk-Forward January-August 2026\n\n"
        "Each month trains only on prior data, then tests the next month. Window expands after each month.\n\n"
        + _table(rows)
        + "\n\n"
        f"Combined trades: `{combined['trades']}`\n\n"
        f"Combined win rate: `{combined['win_rate']}`\n\n"
        f"Combined net R: `{combined['net_r']}`\n\n"
        f"Combined profit factor: `{combined['profit_factor']}`\n\n"
        f"Max monthly drawdown R: `{combined['max_month_drawdown_r']}`\n\n"
        "## 20K Account Estimate\n\n"
        f"{estimates_text}\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "doc": str(DOC), "combined": combined, "account_estimates_20k": estimates}, indent=2))


if __name__ == "__main__":
    main()
