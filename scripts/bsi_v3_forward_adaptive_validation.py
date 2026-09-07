from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "scripts" / "bsi_v3_august_validation.py"
OUT_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation"
RESEARCH_DIR = ROOT / "data" / "research"
SYMBOLS = os.getenv(
    "BSI_V3_FORWARD_SYMBOLS",
    "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD",
)
TRAIN_START = os.getenv("BSI_V3_FORWARD_TRAIN_START", "2026-07-01T00:00:00+00:00")
TRAIN_END = os.getenv("BSI_V3_FORWARD_TRAIN_END", "2026-08-01T00:00:00+00:00")
TEST_START = os.getenv("BSI_V3_FORWARD_TEST_START", "2026-08-01T00:00:00+00:00")
TEST_END = os.getenv("BSI_V3_FORWARD_TEST_END", "2026-09-01T00:00:00+00:00")
RUN_LABEL = os.getenv("BSI_V3_FORWARD_LABEL", "july_train_august_test")

TRAIN_JSON = RESEARCH_DIR / f"bsi_v3_forward_train_{RUN_LABEL}_full.json"
TEST_JSON = RESEARCH_DIR / f"bsi_v3_forward_test_{RUN_LABEL}_full.json"
FORWARD_JSON = RESEARCH_DIR / f"bsi_v3_forward_adaptive_{RUN_LABEL}.json"
FORWARD_DOC = OUT_DIR / f"BSI_V3_FORWARD_ADAPTIVE_{RUN_LABEL.upper()}.md"


def run_validation(start: str, end: str, output: Path, golden_output: Path, golden_doc: str, compact: bool) -> None:
    env = os.environ.copy()
    env.update(
        {
            "BSI_V3_START": start,
            "BSI_V3_END": end,
            "BSI_V3_SYMBOLS": SYMBOLS,
            "BSI_V3_GOLDEN_SYMBOLS": SYMBOLS,
            "BSI_V3_OUTPUT_JSON": str(output.relative_to(ROOT)),
            "BSI_V3_GOLDEN_OUTPUT_JSON": str(golden_output.relative_to(ROOT)),
            "BSI_V3_GOLDEN_DOC": golden_doc,
            "BSI_V3_COMPACT_OUTPUT": "true" if compact else "false",
        }
    )
    subprocess.run([sys.executable, str(VALIDATION)], cwd=ROOT, env=env, check=True)


def summarize(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "net_r": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "max_losing_streak": 0,
            "max_drawdown_r": 0.0,
        }
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = max_drawdown = 0.0
    losing_streak = max_losing_streak = 0
    for value in vals:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if value <= 0:
            losing_streak += 1
        else:
            losing_streak = 0
        max_losing_streak = max(max_losing_streak, losing_streak)
    return {
        "trades": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(vals) * 100, 2),
        "net_r": round(sum(vals), 4),
        "expectancy": round(sum(vals) / len(vals), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_losing_streak": max_losing_streak,
        "max_drawdown_r": round(max_drawdown, 4),
    }


def outcomes_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in report["strategy_results"].values():
        out.extend(item.get("outcomes", []))
    return out


def main() -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not TRAIN_JSON.exists():
        run_validation(
            TRAIN_START,
            TRAIN_END,
            TRAIN_JSON,
            RESEARCH_DIR / f"bsi_v3_forward_train_{RUN_LABEL}_golden.json",
            f"BSI_V3_FORWARD_TRAIN_{RUN_LABEL.upper()}_GOLDEN_RECONSTRUCTIONS.md",
            compact=True,
        )
    if not TEST_JSON.exists():
        run_validation(
            TEST_START,
            TEST_END,
            TEST_JSON,
            RESEARCH_DIR / f"bsi_v3_forward_test_{RUN_LABEL}_golden.json",
            f"BSI_V3_FORWARD_TEST_{RUN_LABEL.upper()}_GOLDEN_RECONSTRUCTIONS.md",
            compact=False,
        )

    train = json.loads(TRAIN_JSON.read_text(encoding="utf-8"))
    test = json.loads(TEST_JSON.read_text(encoding="utf-8"))
    allowed_keys = {
        (row["strategy_id"], row["symbol"])
        for row in train["adaptive_manager_tuning"]["allowed_buckets"]
    }
    test_outcomes = outcomes_from_report(test)
    kept = [
        out
        for out in test_outcomes
        if (out["strategy_id"], out["symbol"]) in allowed_keys
    ]
    skipped = [
        out
        for out in test_outcomes
        if (out["strategy_id"], out["symbol"]) not in allowed_keys
    ]
    result = {
        "methodology": "BSI_V3_FORWARD_ADAPTIVE_TRAIN_TEST",
        "label": RUN_LABEL,
        "symbols": SYMBOLS.split(","),
        "train_window": train["window"],
        "test_window": test["window"],
        "train_allowed_bucket_count": len(allowed_keys),
        "test_raw_baseline": test["portfolio"]["one_trade_one_r"],
        "test_raw_mentor_management": test["portfolio"]["mentor_management"],
        "test_forward_adaptive": summarize([float(out["managed_r"]) for out in kept if out.get("managed_r") is not None]),
        "test_skipped": summarize([float(out["managed_r"]) for out in skipped if out.get("managed_r") is not None]),
        "kept_trades": len(kept),
        "skipped_trades": len(skipped),
        "allowed_buckets": sorted([{"strategy_id": sid, "symbol": symbol} for sid, symbol in allowed_keys], key=lambda x: (x["strategy_id"], x["symbol"])),
        "account_estimates": {},
        "warning": "Forward split only: the train window built the bucket gate, and the test window applied it. Still not a live-routing guarantee.",
    }
    net_r = result["test_forward_adaptive"]["net_r"]
    for risk_percent in [0.10, 0.25, 0.50]:
        profit = 20000 * (risk_percent / 100.0) * net_r
        result["account_estimates"][f"{risk_percent:.2f}%_risk"] = {
            "profit": round(profit, 2),
            "ending_balance": round(20000 + profit, 2),
        }

    FORWARD_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    rows = [
        ["Metric", "Test Raw Baseline", "Test Mentor Managed", "Train-Window Adaptive on Test"],
        ["Trades", result["test_raw_baseline"]["trades"], result["test_raw_mentor_management"]["trades"], result["test_forward_adaptive"]["trades"]],
        ["Win Rate", result["test_raw_baseline"]["win_rate"], result["test_raw_mentor_management"]["win_rate"], result["test_forward_adaptive"]["win_rate"]],
        ["Net R", result["test_raw_baseline"]["net_r"], result["test_raw_mentor_management"]["net_r"], result["test_forward_adaptive"]["net_r"]],
        ["Profit Factor", result["test_raw_baseline"]["profit_factor"], result["test_raw_mentor_management"]["profit_factor"], result["test_forward_adaptive"]["profit_factor"]],
        ["Max Losing Streak", result["test_raw_baseline"]["max_losing_streak"], result["test_raw_mentor_management"]["max_losing_streak"], result["test_forward_adaptive"]["max_losing_streak"]],
        ["Max DD R", result["test_raw_baseline"]["max_drawdown_r"], result["test_raw_mentor_management"]["max_drawdown_r"], result["test_forward_adaptive"]["max_drawdown_r"]],
    ]
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    table = "\n".join(
        ["| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |" for row in rows[:1]]
        + ["| " + " | ".join("-" * width for width in widths) + " |"]
        + ["| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |" for row in rows[1:]]
    )
    estimates = "\n".join(
        f"- `{risk}`: profit `${stats['profit']}`, ending balance `${stats['ending_balance']}`"
        for risk, stats in result["account_estimates"].items()
    )
    FORWARD_DOC.write_text(
        "# BSI V3 Forward Adaptive Validation\n\n"
        f"Label: `{RUN_LABEL}`\n\n"
        f"Train: `{TRAIN_START}` to `{TRAIN_END}`. Test: `{TEST_START}` to `{TEST_END}`.\n\n"
        f"Symbols: `{SYMBOLS}`.\n\n"
        f"Train allowed buckets: `{result['train_allowed_bucket_count']}`\n\n"
        f"{table}\n\n"
        "## 20K Account Estimate\n\n"
        f"{estimates}\n\n"
        "This is forward-split replay math, not compounded and not live-routing proof.\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
