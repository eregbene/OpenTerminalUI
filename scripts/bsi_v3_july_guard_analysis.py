from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JULY_FULL = ROOT / "data" / "research" / "bsi_v3_forward_test_rolling_july_2026_rr1p5_full.json"
JULY_FORWARD = ROOT / "data" / "research" / "bsi_v3_forward_adaptive_rolling_july_2026_rr1p5.json"
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_july_guard_analysis.json"
OUT_DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_JULY_GUARD_ANALYSIS.md"


@dataclass(frozen=True)
class GuardParams:
    warmup: int
    lookback: int
    recent_net_lte: float
    loss_streak_gte: int
    cooldown_days: int
    daily_stop_r: float | None


def _load_outcomes() -> list[dict[str, Any]]:
    report = json.loads(JULY_FULL.read_text(encoding="utf-8"))
    outcomes: list[dict[str, Any]] = []
    for item in report["strategy_results"].values():
        outcomes.extend(item.get("outcomes", []))
    for row in outcomes:
        row["dt"] = datetime.fromisoformat(str(row["entry_time"]).replace("Z", "+00:00"))
        row["r"] = float(row.get("managed_r") or 0.0)
    return sorted(outcomes, key=lambda item: item["dt"])


def _summary(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = peak = max_drawdown = 0.0
    losing_streak = max_losing_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if value <= 0:
            losing_streak += 1
        else:
            losing_streak = 0
        max_losing_streak = max(max_losing_streak, losing_streak)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100, 2) if values else None,
        "net_r": round(sum(values), 4),
        "expectancy": round(sum(values) / len(values), 4) if values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_r": round(max_drawdown, 4),
        "max_losing_streak": max_losing_streak,
    }


def _simulate_guard(outcomes: list[dict[str, Any]], params: GuardParams) -> dict[str, Any]:
    history: dict[tuple[str, str], list[float]] = defaultdict(list)
    blocked_until: dict[tuple[str, str], datetime] = {}
    daily_r: dict[str, float] = defaultdict(float)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    reasons: dict[str, int] = defaultdict(int)

    for row in outcomes:
        key = (row["strategy_id"], row["symbol"])
        day = row["dt"].date().isoformat()
        if params.daily_stop_r is not None and daily_r[day] <= params.daily_stop_r:
            skipped.append(row)
            reasons["daily_stop"] += 1
            history[key].append(row["r"])
            continue
        if blocked_until.get(key) and row["dt"] < blocked_until[key]:
            skipped.append(row)
            reasons["bucket_cooldown"] += 1
            history[key].append(row["r"])
            continue

        bucket_history = history[key]
        block_reason: str | None = None
        if len(bucket_history) >= params.warmup:
            recent = bucket_history[-params.lookback :]
            recent_net = sum(recent)
            losing_streak = 0
            for value in reversed(recent):
                if value <= 0:
                    losing_streak += 1
                else:
                    break
            wins = sum(1 for value in recent if value > 0)
            losses = len(recent) - wins
            if recent_net <= params.recent_net_lte:
                block_reason = "bucket_recent_net"
            if losing_streak >= params.loss_streak_gte:
                block_reason = "bucket_losing_streak"
            if losses and wins / max(1, losses) < 0.5 and recent_net < 0:
                block_reason = "bucket_recent_win_loss"

        if block_reason:
            skipped.append(row)
            reasons[block_reason] += 1
            blocked_until[key] = row["dt"] + timedelta(days=params.cooldown_days)
        else:
            kept.append(row)
            daily_r[day] += row["r"]
        history[key].append(row["r"])

    return {
        "params": asdict(params),
        "kept": _summary([row["r"] for row in kept]),
        "skipped": _summary([row["r"] for row in skipped]),
        "skip_reasons": dict(sorted(reasons.items())),
    }


def _best_live_guard(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for warmup in (2, 3, 4, 5, 8):
        for lookback in (3, 5, 8, 10):
            for recent_net_lte in (-1.0, -2.0, -3.0, -4.0, -5.0):
                for loss_streak_gte in (2, 3, 4, 5):
                    for cooldown_days in (1, 2, 3, 5, 10):
                        for daily_stop_r in (None, -10.0, -15.0, -25.0):
                            result = _simulate_guard(
                                outcomes,
                                GuardParams(
                                    warmup=warmup,
                                    lookback=lookback,
                                    recent_net_lte=recent_net_lte,
                                    loss_streak_gte=loss_streak_gte,
                                    cooldown_days=cooldown_days,
                                    daily_stop_r=daily_stop_r,
                                ),
                            )
                            if result["kept"]["trades"] < 300:
                                continue
                            if best is None or (
                                result["kept"]["net_r"],
                                result["kept"]["profit_factor"] or 0.0,
                                -result["kept"]["max_drawdown_r"],
                            ) > (
                                best["kept"]["net_r"],
                                best["kept"]["profit_factor"] or 0.0,
                                -best["kept"]["max_drawdown_r"],
                            ):
                                best = result
    return best or {}


def _month_kill_switch(outcomes: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    equity = 0.0
    stopped_at: str | None = None
    for row in outcomes:
        if stopped_at is not None:
            skipped.append(row)
            continue
        kept.append(row)
        equity += row["r"]
        if equity <= threshold:
            stopped_at = row["entry_time"]
    return {
        "threshold_r": threshold,
        "stopped_at": stopped_at,
        "kept": _summary([row["r"] for row in kept]),
        "skipped": _summary([row["r"] for row in skipped]),
    }


def _table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[idx])) for row in rows) for idx in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(str(row[col]).ljust(widths[col]) for col in range(len(row))) + " |")
        if idx == 0:
            lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(lines)


def main() -> None:
    outcomes = _load_outcomes()
    july_full = json.loads(JULY_FULL.read_text(encoding="utf-8"))
    july_forward = json.loads(JULY_FORWARD.read_text(encoding="utf-8"))
    same_month = july_full["portfolio"]["adaptive_filtered_management"]
    live_guard = _best_live_guard(outcomes)
    kill_switches = [_month_kill_switch(outcomes, threshold) for threshold in (-10.0, -25.0, -50.0)]
    payload = {
        "methodology": "BSI_V3_JULY_GUARD_ANALYSIS",
        "source_full": str(JULY_FULL.relative_to(ROOT)),
        "source_forward": str(JULY_FORWARD.relative_to(ROOT)),
        "forward_jan_june_trained_on_july": july_forward["test_forward_adaptive"],
        "same_month_hindsight_filter": same_month,
        "best_no_lookahead_guard": live_guard,
        "month_kill_switches": kill_switches,
        "conclusion": (
            "July cannot be fixed by RR threshold alone. Hindsight same-month buckets are positive, "
            "but no-lookahead rolling bucket cooldown remains negative; therefore the practical guard "
            "is a monthly/day kill switch plus next-session profile regeneration."
        ),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    rows = [["Mode", "Trades", "Win %", "Net R", "PF", "Max DD R", "Max LS"]]
    for label, stats in (
        ("Jan-Jun trained forward", payload["forward_jan_june_trained_on_july"]),
        ("July hindsight buckets", payload["same_month_hindsight_filter"]),
        ("Best no-lookahead guard", payload["best_no_lookahead_guard"]["kept"]),
    ):
        rows.append([label, stats["trades"], stats["win_rate"], stats["net_r"], stats["profit_factor"], stats["max_drawdown_r"], stats["max_losing_streak"]])
    OUT_DOC.write_text(
        "# BSI V3 July Guard Analysis\n\n"
        + _table(rows)
        + "\n\n## Month Kill Switches\n\n"
        + "\n".join(
            f"- `{item['threshold_r']}R`: stopped at `{item['stopped_at']}`, kept net `{item['kept']['net_r']}R`, skipped net `{item['skipped']['net_r']}R`"
            for item in kill_switches
        )
        + "\n\nConclusion: July needs circuit-breaker/profile-regeneration logic, not a higher RR-only rule.\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT_JSON), "doc": str(OUT_DOC), "best_no_lookahead_guard": live_guard["kept"], "kill_switches": kill_switches}, indent=2, default=str))


if __name__ == "__main__":
    main()
