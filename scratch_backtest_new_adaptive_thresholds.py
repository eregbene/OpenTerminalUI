"""OLD vs NEW Adaptive Trade Manager threshold backtest -- commit 6f3c06c
("fix(adaptive-management): add zone_25_60, lower MFE protection floor to 0.25R"), replayed
against every real closed DEMO trade from Thu 2026-08-13 and Fri 2026-08-14 (UTC calendar days).

WHAT CHANGED tonight (see `git show 6f3c06c`):
  1. tp_protection.progress_zone(): added a new zone_25_60 tier below the prior 60% floor
     (previously anything under 60% TP progress was "below_60" and got NO zone-based
     management at all). zone_25_60 is wired into the SAME two places every other zone already
     is: the TP_PROGRESS_PARTIAL_PROTECT staged-partial ladder and the TP_PROGRESS_STRUCTURE_STOP
     ATR-trailing logic in service.py::_evaluate_position.
  2. ADAPTIVE_MFE_MIN_R lowered 0.5 -> 0.25 -- the giveback threshold at which
     MFE_PROTECTION_CLOSE becomes eligible to fire at all.

REUSE, NOT REIMPLEMENTATION:
  - The REAL, unmodified _evaluate_position()/_select_action() decision engine
    (backend/adaptive_management/service.py) is replayed bar-by-bar against real M15 candle
    history, using the exact synthetic-state-replay pattern already proven in
    scratch_adaptive_manager_forensic_audit.py's reconstruct_forensics(). This is NOT the
    separate, simplified default_policies()/simulate_policy() shadow-family framework in
    service.py (that framework has no branch for the real "conservative_demo_manager_v1"
    zone/MFE-protection logic at all -- it's a different, coarser research tool for a different
    purpose). Faithfully reusing the real production function is what makes OLD vs NEW mean
    anything.
  - Real M15 candle history: AdaptiveManagementService._replay_candles() (point-in-time-safe,
    reads MT5CandleRevisionORM/MT5CanonicalCandleORM) -- no new candle-fetch path.
  - Real closed trades: adaptive_position_states joined to adaptive_trade_events DEAL rows.
    IMPORTANT, verified against real data before trusting it: AdaptivePositionStateORM.
    closed_detected_at is NOT a reliable close timestamp (it's set to the Python-side utcnow()
    of whichever adaptive-management poll cycle first noticed the position was gone from MT5's
    open-positions list; for this dataset it is consistently ~3 hours EARLIER than the real
    closing deal's own broker utc_time -- e.g. position 57961402504's real DEAL data shows entry
    2026-08-13T10:49:13Z / exit 2026-08-13T10:51:45Z, while its closed_detected_at reads
    2026-08-13T07:50:13Z). It is used here ONLY as a boolean "has this position been detected
    closed" filter (that part is fine), never as the actual exit timestamp -- exit_time always
    comes from the real closing DEAL's own utc_time, exactly like
    scratch_adaptive_manager_forensic_audit.py already does.
  - ALSO verified and fixed here: AdaptiveTradeEventORM.position_id stores the BARE MT5 ticket
    (no account prefix), while AdaptivePositionStateORM.position_id is prefixed for some accounts
    (e.g. "ftmo_demo_100k:57961308366"). Joining without stripping the prefix silently drops
    every prefixed-account trade's deal data (confirmed: 119/167 candidates in this window
    matched zero deals before the fix, 0/167 after).

OLD vs NEW is realized via ad-hoc parameter overrides only -- no new named policy was added to
default_policies(), per the instruction to prefer that when the replay engine parameterizes
cleanly enough:
  - NEW = current deployed code + current real env (ADAPTIVE_MFE_MIN_R=0.25, zone_25_60 wired
    in) -- i.e. exactly what's live in the container right now.
  - OLD = tp_protection.progress_zone monkeypatched back to the pre-commit-6f3c06c version
    (zones start at 60% TP progress; below that is "below_60", which matches none of the
    zone-gated candidate branches) + ADAPTIVE_MFE_MIN_R overridden to 0.5 for that pass only.
  Every other real, currently-deployed threshold (ADAPTIVE_BREAKEVEN_R=0.5,
  ADAPTIVE_PARTIAL_PROFIT_R=0.5, ADAPTIVE_PARTIAL_PROFIT_FRACTION=0.30, ADAPTIVE_TRAIL_R=0.75,
  etc. -- confirmed live via `docker exec ... python -c "import os; ..."`) is IDENTICAL between
  the OLD and NEW passes, isolating exactly and only the two changes from commit 6f3c06c.

R -> $ conversion uses each trade's own AdaptivePositionStateORM.original_risk_money (the real
dollar value of 1.0 price-distance R for that trade, the same monetary basis _compute_r's own
"MONEY" branch would use) -- hypothetical_pnl_usd = hypothetical_realized_r * original_risk_money.

Nothing here writes to a real table (session rolled back at the end, never committed) or touches
any broker/live production code path.
"""
from __future__ import annotations

import contextlib
import os
from collections import Counter
from datetime import datetime, timezone

from backend.adaptive_management import service as svc
from backend.adaptive_management import tp_protection
from backend.adaptive_management.orm import AdaptivePartialExitStageORM, AdaptivePositionStateORM as PS, AdaptiveTradeEventORM as TE
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.mt5_strategies.models import normalize_strategy_id
from backend.shared.db import SessionLocal

WINDOW_START = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
REGIME_LOOKBACK_BARS = 60


# ------------------------------------------------------------------------- real trade loading ---
def _bare_ticket(position_id: str) -> str:
    return position_id.split(":")[-1] if ":" in position_id else position_id


def load_real_closed_trades() -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(PS)
            .filter(PS.closed_detected_at.isnot(None), PS.contaminated.is_(False))
            .filter(PS.opened_at >= WINDOW_START, PS.opened_at < WINDOW_END)
            .order_by(PS.opened_at.asc())
            .all()
        )
        trades = []
        skipped_no_deals = 0
        for row in rows:
            deals = db.query(TE).filter(TE.position_id == _bare_ticket(row.position_id), TE.event_type == "DEAL").order_by(TE.utc_time.asc()).all()
            if len(deals) < 2:
                skipped_no_deals += 1
                continue
            entry_deal = deals[0]
            exit_deal = max(deals, key=lambda d: d.utc_time or row.opened_at)
            realized_pnl = compute_trade_costs([{"profit": d.realized_pnl, "commission": d.commission, "swap": d.swap, "fee": d.fee, "volume": d.volume} for d in deals]).net_pnl
            if not row.original_sl or not row.original_tp or not row.entry_price:
                continue
            trades.append({
                "trade_id": row.position_id,
                "symbol": row.symbol,
                "direction": row.direction.upper(),
                "volume": row.original_volume,
                "entry": row.entry_price,
                "stop_loss": row.original_sl,
                "take_profit": row.original_tp,
                "entry_time": entry_deal.utc_time or row.opened_at,
                "exit_time": exit_deal.utc_time or row.opened_at,
                "actual_pnl": realized_pnl,
                "strategy_id": normalize_strategy_id(row.strategy_id) or "UNKNOWN",
                "account_fingerprint": row.account_fingerprint,
                "original_risk_money": float(row.original_risk_money) if row.original_risk_money else None,
            })
    print(f"  candidates in window: {len(rows)}  usable (>=entry+exit deal, sl/tp/entry present): {len(trades)}  skipped_no_deal_pair: {skipped_no_deals}", flush=True)
    return trades


REPLAY_TIMEFRAME = "M5"  # matches production's actual default: os.getenv("ADAPTIVE_MANAGEMENT_TIMEFRAME", "M5")
# (the env var isn't set in this container, so M5 -- not M15 -- is what _evaluate_position's own
# retracement_allowance() call actually uses live; M15 was tried first and skipped 145/167 trades
# purely from having <3 completed bars -- this bot's real median holding time is ~11 minutes, so
# M15 was simply too coarse for most of these trades, not a genuine data gap).


async def fetch_candles(symbol: str, entry_time, exit_time):
    return await svc._replay_candles(symbol, REPLAY_TIMEFRAME, entry_time, exit_time)


def _normalized_window(candles, entry_time, exit_time):
    normalized = sorted((svc._normalize_candle(c) for c in candles), key=lambda c: c["time"] if c else datetime.min)
    return [c for c in normalized if c and entry_time <= c["time"] <= exit_time]


def _signed_r(entry: float, price: float, risk: float, direction: str) -> float:
    return (price - entry) / risk if direction == "LONG" else (entry - price) / risk


# --------------------------------------------------------------- OLD-settings monkeypatch scope ---
def _old_progress_zone(progress: float | None) -> str:
    """Pre-commit-6f3c06c version of tp_protection.progress_zone: zones started at 60% TP
    progress; anything below that was "below_60" and matched none of the zone-gated candidate
    branches in _evaluate_position (which only ever check for "zone_25_60"/"zone_60_75"/etc.)."""
    if progress is None:
        return "none"
    if progress < 0.60:
        return "below_60"
    if progress < 0.75:
        return "zone_60_75"
    if progress < 0.85:
        return "zone_75_85"
    if progress < 0.95:
        return "zone_85_95"
    return "zone_95_plus"


def _env_override(name: str):
    @contextlib.contextmanager
    def _ctx(value: str | None):
        original_env = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        try:
            yield
        finally:
            if original_env is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original_env
    return _ctx


_mfe_min_r_override = _env_override("ADAPTIVE_MFE_MIN_R")

# commit ea7b1211 (2026-08-18, "split MFE_PROTECTION_CLOSE into partial (early) vs full (late)")
# made the partial-vs-full split UNCONDITIONAL in service.py -- it is not behind its own feature
# flag, only behind the pre-existing ADAPTIVE_MFE_MIN_R gate that all four variants below already
# share. Since this script imports `service` once and every variant replays through that same
# live module, OLD/COMBINED/REFINED -- which are all meant to reproduce configs that were tested
# and measured BEFORE ea7b1211 existed (bare full-close, no split) -- would otherwise silently
# inherit today's split behavior too, contaminating the very comparison this script exists to
# make (see the round-1/round-2/round-3 narrative in ea7b1211's own commit message). Forcing
# ADAPTIVE_MFE_FULL_CLOSE_R down to -1 makes `max_r >= full_close_r` always true (max_r is always
# > 0 whenever MFE_PROTECTION_CLOSE's own max_r >= ADAPTIVE_MFE_MIN_R gate has already passed),
# i.e. always requests the FULL current volume -- byte-identical to the pre-ea7b1211 code path
# (`requested_volume=float(state.current_volume)`, unconditional). Only SPLIT below leaves this
# knob untouched (real default 1.0), because SPLIT is the one variant meant to exercise the split.
_mfe_full_close_r_override = _env_override("ADAPTIVE_MFE_FULL_CLOSE_R")


@contextlib.contextmanager
def old_settings():
    """OLD (baseline, pre-commit-6f3c06c): no zone_25_60 (progress_zone monkeypatched back to the
    pre-fix version, zones start at 60% TP progress) + ADAPTIVE_MFE_MIN_R=0.5 + split neutralized
    (bare full-close, matching the pre-ea7b1211 code this config was actually tested against)."""
    original_zone_fn = tp_protection.progress_zone
    tp_protection.progress_zone = _old_progress_zone
    try:
        with _mfe_min_r_override("0.5"), _mfe_full_close_r_override("-1"):
            yield
    finally:
        tp_protection.progress_zone = original_zone_fn


@contextlib.contextmanager
def combined_settings():
    """COMBINED (round 1, tested first, found net-negative): current deployed code (zone_25_60
    wired in, real progress_zone -- no monkeypatch needed) + ADAPTIVE_MFE_MIN_R forced to 0.25 +
    split neutralized (bare full-close) to faithfully reproduce round 1's actual tested config,
    which predates ea7b1211's split -- without this override, replaying "combined_settings()"
    against today's live module would silently become identical to SPLIT below, since the split
    is no longer gated behind anything this context manager controls."""
    with _mfe_min_r_override("0.25"), _mfe_full_close_r_override("-1"):
        yield


@contextlib.contextmanager
def refined_settings():
    """REFINED (round 2, currently-superseded by SPLIT/ea7b1211): current deployed code
    (zone_25_60 wired in, real progress_zone -- no monkeypatch needed) + ADAPTIVE_MFE_MIN_R forced
    to 0.5 + split neutralized (bare full-close), reproducing round 2's actual tested config
    (live in the container before ea7b1211 shipped)."""
    with _mfe_min_r_override("0.5"), _mfe_full_close_r_override("-1"):
        yield


@contextlib.contextmanager
def split_settings():
    """SPLIT (round 3, ea7b1211, actually live now): current deployed code UNCHANGED -- real
    progress_zone (zone_25_60), and the partial/early-vs-full/late MFE_PROTECTION_CLOSE split left
    exactly as coded (ADAPTIVE_MFE_FULL_CLOSE_R at its real default of 1.0, ADAPTIVE_MFE_EARLY_PARTIAL_FRACTION
    at its real default of 0.5) -- plus ADAPTIVE_MFE_MIN_R forced to 0.25 to match what's actually
    live in the container right now (confirmed via `docker exec ... print(os.getenv(...))` ==
    "0.25 None None", i.e. MIN_R=0.25 via env, FULL_CLOSE_R and EARLY_PARTIAL_FRACTION on their
    code defaults). This is the ONLY variant that exercises the real split logic -- the other
    three deliberately neutralize it above to stay faithful to what they were originally tested
    as."""
    with _mfe_min_r_override("0.25"):
        yield


# ------------------------------------------------------------- synthetic-state replay (reused) ---
# Same pattern as scratch_adaptive_manager_forensic_audit.py's Policy A / reconstruct_forensics --
# the REAL _evaluate_position()/_select_action() engine, walked bar-by-bar over real candles.
class _State:
    __slots__ = (
        "position_id", "symbol", "direction", "entry_price", "opened_at",
        "original_sl", "original_tp", "current_sl", "current_tp",
        "original_volume", "current_volume",
        "max_achieved_r", "min_achieved_r", "tp_progress", "max_tp_progress",
        "winner_classification", "partial_profit_stage", "current_giveback_r", "last_management_at",
        "original_risk_money", "account_fingerprint",
    )


def _replay_cooldown_elapsed(last_management_at, bar_time) -> bool:
    if not last_management_at:
        return True
    cooldown = svc._env_int("ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS", 120, minimum=10, maximum=3600)
    return (bar_time - last_management_at).total_seconds() >= cooldown


def _new_state(trade: dict, position_id: str) -> _State:
    s = _State()
    s.position_id = position_id
    s.symbol = trade["symbol"]
    s.direction = trade["direction"]
    s.entry_price = float(trade["entry"])
    s.opened_at = trade["entry_time"]
    s.original_sl = float(trade["stop_loss"])
    s.original_tp = float(trade["take_profit"])
    s.current_sl, s.current_tp = s.original_sl, s.original_tp
    s.original_volume = float(trade["volume"] or 1.0)
    s.current_volume = s.original_volume
    s.max_achieved_r = s.min_achieved_r = s.tp_progress = s.max_tp_progress = s.current_giveback_r = 0.0
    s.winner_classification = "healthy_pullback"
    s.partial_profit_stage = "NONE"
    s.last_management_at = None
    s.original_risk_money = trade.get("original_risk_money")
    s.account_fingerprint = trade.get("account_fingerprint")
    return s


def _update_state_for_cycle(state: _State, *, current_price: float, candles_window: list[dict]):
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    r_now, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=current_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
    state.max_achieved_r = max(state.max_achieved_r, r_now)
    state.min_achieved_r = min(state.min_achieved_r, r_now)
    regime_info = svc.detect_regime(candles_window, context={})
    atr = float(regime_info.get("features", {}).get("atr") or 0) or None
    progress = tp_protection.tp_progress(state.direction, state.entry_price, current_price, state.current_tp)
    state.tp_progress = progress if progress is not None else state.tp_progress
    if progress is not None and progress > state.max_tp_progress:
        state.max_tp_progress = progress
    state.current_giveback_r = max(0.0, state.max_achieved_r - r_now)
    atr_r = (atr / risk) if atr and risk else 0.0
    allowance_fraction = tp_protection.retracement_allowance(atr_r=atr_r, regime=regime_info.get("regime", "insufficient_data"), timeframe=REPLAY_TIMEFRAME)
    allowance_r = allowance_fraction * state.max_achieved_r
    retracement_state = tp_protection.classify_retracement(state.current_giveback_r, allowance_r)
    candles_held = svc._candles_held(state.opened_at, candles_window)
    winner = tp_protection.classify_winner_preservation({
        "opposing_candles": svc._opposing_candles(candles_window, state.direction), "retracement_state": retracement_state,
        "regime": regime_info.get("regime", "insufficient_data"), "direction": state.direction, "candles_held": candles_held,
        "remaining_reward_r": (1.0 - progress) if progress is not None else None,
    })
    state.winner_classification = winner["classification"]
    return r_now


def _apply_choice(state: _State, choice, *, bar_time, current_r: float, db, legs: list):
    if choice.requested_sl is not None:
        state.current_sl = choice.requested_sl
    if choice.requested_tp is not None:
        state.current_tp = choice.requested_tp
    if choice.requested_volume and choice.requested_volume > 0 and state.current_volume > 0:
        fraction_of_original = min(1.0, choice.requested_volume / state.original_volume)
        legs.append({"fraction": fraction_of_original, "r": current_r, "action": choice.action_type, "time": bar_time})
        state.current_volume = max(0.0, state.current_volume - choice.requested_volume)
        if choice.action_type == "PARTIAL_PROFIT":
            state.partial_profit_stage = "PARTIAL_1_EXECUTED" if state.partial_profit_stage == "NONE" else "PARTIAL_2_EXECUTED"
        if choice.action_type == "TP_PROGRESS_PARTIAL_PROTECT":
            stage = (choice.evidence or {}).get("stage")
            if stage:
                db.add(AdaptivePartialExitStageORM(stage_id=f"{state.position_id}_{stage}", position_id=state.position_id, stage=stage))
                db.flush()
    if choice.action_type != "HOLD":
        state.last_management_at = bar_time
    return state.current_volume <= 1e-9


def replay_trade(trade: dict, candles: list[dict], svc_instance, db, *, label: str) -> dict | None:
    """Replays the REAL _evaluate_position/_select_action engine bar-by-bar for one trade under
    whichever settings context (old_settings()/combined_settings()/refined_settings()/
    split_settings()) is currently active. `label` picks a position_id namespace
    ("OLD_"/"COMBINED_"/"REFINED_"/"SPLIT_") so each pass's AdaptivePartialExitStageORM rows
    (staged-partial "already executed" gating) never leak into each other or into a prior run."""
    normalized = _normalized_window(candles, trade["entry_time"], trade["exit_time"])
    if len(normalized) < 3:
        return None
    position_id = f"{label}_{trade['trade_id']}"
    state = _new_state(trade, position_id)
    risk = abs(state.entry_price - state.original_sl) or 1e-5
    long = state.direction == "LONG"

    mfe_r = 0.0
    for bar in normalized:
        bar_high_r = _signed_r(state.entry_price, bar["high"] if long else bar["low"], risk, state.direction)
        mfe_r = max(mfe_r, bar_high_r)

    legs: list[dict] = []
    action_log: list[dict] = []
    final_r, hard_stop_hit, natural_end = 0.0, False, True

    for i, bar in enumerate(normalized):
        window = normalized[max(0, i - REGIME_LOOKBACK_BARS):i + 1]
        sl_touched = (bar["low"] <= state.current_sl) if long else (bar["high"] >= state.current_sl)
        tp_touched = (bar["high"] >= state.current_tp) if long else (bar["low"] <= state.current_tp)
        if sl_touched or tp_touched:
            touch_price = state.current_sl if sl_touched else state.current_tp
            final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=touch_price, original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)
            hard_stop_hit, natural_end = True, False
            action_log.append({"time": bar["time"].isoformat(), "action": "SL_HIT" if sl_touched else "TP_HIT", "r": round(final_r, 4)})
            break

        r_now = _update_state_for_cycle(state, current_price=bar["close"], candles_window=window)
        payload = {"price_current": bar["close"], "price_open": state.entry_price, "sl": state.current_sl, "tp": state.current_tp, "volume": state.current_volume, "profit": None, "symbol": state.symbol}
        candidates = svc_instance._evaluate_position(db, state, payload, {}, window, economic_result=None, symbol_info=None, account_equity=None)
        if not _replay_cooldown_elapsed(state.last_management_at, bar["time"]):
            candidates = [c for c in candidates if c.action_type in ("HOLD", "HOLD_WITH_GIVEBACK_RISK")] or candidates
        choice = svc_instance._select_action(candidates)
        if choice.action_type != "HOLD":
            action_log.append({"time": bar["time"].isoformat(), "action": choice.action_type, "reason": choice.reason, "r": round(r_now, 4)})
        closed = _apply_choice(state, choice, bar_time=bar["time"], current_r=r_now, db=db, legs=legs)
        final_r = r_now
        if closed:
            natural_end = False
            break

    if natural_end and normalized:
        final_r, _ = svc._compute_r(profit_usd=None, original_risk_money=None, entry=state.entry_price, current_price=normalized[-1]["close"], original_sl_for_risk=state.original_sl, direction=state.direction, risk=risk)

    remaining_fraction = max(0.0, 1.0 - sum(leg["fraction"] for leg in legs))
    if remaining_fraction > 1e-9:
        legs.append({"fraction": remaining_fraction, "r": final_r, "action": "SL_HIT" if hard_stop_hit else ("NATURAL_END" if natural_end else "MANAGER_FULL_CLOSE"), "time": normalized[-1]["time"] if normalized else trade["exit_time"]})
    blended_r = sum(leg["fraction"] * leg["r"] for leg in legs)
    risk_usd = trade.get("original_risk_money")
    hypothetical_pnl_usd = blended_r * risk_usd if risk_usd else None
    final_leg = legs[-1] if legs else None

    return {
        "trade_id": trade["trade_id"], "symbol": trade["symbol"], "direction": trade["direction"],
        "strategy_id": trade["strategy_id"], "entry_time": trade["entry_time"],
        "mfe_r": round(mfe_r, 4), "realized_r": round(blended_r, 4),
        "pnl_usd": round(hypothetical_pnl_usd, 2) if hypothetical_pnl_usd is not None else None,
        "final_action": final_leg["action"] if final_leg else "UNKNOWN",
        "action_log": action_log, "hard_stop_hit": hard_stop_hit,
        "num_management_legs": len(legs) - 1,
    }


# ---------------------------------------------------------------------------------- reporting ---
async def main():
    print("Loading real closed DEMO trades from 2026-08-13 and 2026-08-14 (UTC)...", flush=True)
    trades = load_real_closed_trades()
    if not trades:
        print("ZERO usable real closed trades in this window -- nothing to replay. Reporting this "
              "honestly rather than widening the date range silently.")
        return

    svc_instance = svc.AdaptiveManagementService()
    db = SessionLocal()
    rows: list[dict] = []
    skipped = []

    try:
        for trade in trades:
            try:
                candles = await fetch_candles(trade["symbol"], trade["entry_time"], trade["exit_time"])
                if not candles:
                    skipped.append((trade["trade_id"], "no_candles"))
                    continue
                with old_settings():
                    old_result = replay_trade(trade, candles, svc_instance, db, label="OLD")
                with combined_settings():
                    combined_result = replay_trade(trade, candles, svc_instance, db, label="COMBINED")
                with refined_settings():
                    refined_result = replay_trade(trade, candles, svc_instance, db, label="REFINED")
                with split_settings():
                    split_result = replay_trade(trade, candles, svc_instance, db, label="SPLIT")
                if old_result is None or combined_result is None or refined_result is None or split_result is None:
                    skipped.append((trade["trade_id"], "insufficient_candle_window"))
                    continue
                rows.append({"trade": trade, "old": old_result, "combined": combined_result, "refined": refined_result, "split": split_result})
            except Exception as exc:
                skipped.append((trade["trade_id"], f"{exc.__class__.__name__}: {exc}"))
    finally:
        db.rollback()
        db.close()

    print(f"\nReplayed {len(rows)} real trades under OLD / COMBINED / REFINED / SPLIT settings ({len(skipped)} skipped).", flush=True)
    if skipped:
        reason_counts = Counter(reason for _, reason in skipped)
        print(f"  skip reasons: {dict(reason_counts)}")
    if not rows:
        print("No trades could be replayed (no candle data available for this window). Reporting "
              "this honestly rather than padding the result.")
        return

    print("\n" + "=" * 220)
    print(f"{'symbol':8s} {'dir':5s} {'strategy':16s} {'entry_time':17s} {'mfe_r':>6s} | {'OLD_r':>7s} {'OLD_action':24s} | {'COMB_r':>7s} {'COMB_action':24s} | {'REF_r':>7s} {'REF_action':24s} | {'SPLIT_r':>7s} {'SPLIT_action':24s}")
    print("=" * 220)
    total_old_r = total_combined_r = total_refined_r = total_split_r = 0.0
    total_old_usd = total_combined_usd = total_refined_usd = total_split_usd = 0.0
    usd_known = 0
    roundtrip_fixed_combined, roundtrip_still_broken_combined = [], []
    roundtrip_fixed_refined, roundtrip_still_broken_refined = [], []
    roundtrip_fixed_split, roundtrip_still_broken_split = [], []

    for row in rows:
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        total_old_r += o["realized_r"]
        total_combined_r += c["realized_r"]
        total_refined_r += r["realized_r"]
        total_split_r += s["realized_r"]
        if o["pnl_usd"] is not None and c["pnl_usd"] is not None and r["pnl_usd"] is not None and s["pnl_usd"] is not None:
            total_old_usd += o["pnl_usd"]
            total_combined_usd += c["pnl_usd"]
            total_refined_usd += r["pnl_usd"]
            total_split_usd += s["pnl_usd"]
            usd_known += 1
        print(f"{t['symbol']:8s} {t['direction']:5s} {t['strategy_id']:16s} {t['entry_time'].strftime('%Y-%m-%d %H:%M'):17s} {o['mfe_r']:6.2f} | {o['realized_r']:7.3f} {o['final_action']:24s} | {c['realized_r']:7.3f} {c['final_action']:24s} | {r['realized_r']:7.3f} {r['final_action']:24s} | {s['realized_r']:7.3f} {s['final_action']:24s}")

        # Round-trip-to-loss: peaked meaningfully positive, OLD ended <=0, variant ended >0 -- the
        # exact failure mode commit 6f3c06c targeted.
        if o["mfe_r"] >= 0.2 and o["realized_r"] <= 0 and c["realized_r"] > 0:
            roundtrip_fixed_combined.append(row)
        elif o["mfe_r"] >= 0.2 and o["realized_r"] <= 0:
            roundtrip_still_broken_combined.append(row)
        if o["mfe_r"] >= 0.2 and o["realized_r"] <= 0 and r["realized_r"] > 0:
            roundtrip_fixed_refined.append(row)
        elif o["mfe_r"] >= 0.2 and o["realized_r"] <= 0:
            roundtrip_still_broken_refined.append(row)
        if o["mfe_r"] >= 0.2 and o["realized_r"] <= 0 and s["realized_r"] > 0:
            roundtrip_fixed_split.append(row)
        elif o["mfe_r"] >= 0.2 and o["realized_r"] <= 0:
            roundtrip_still_broken_split.append(row)

    print("=" * 220)
    print("\nAGGREGATE")
    print(f"  trades replayed:  {len(rows)}")
    print(f"  total realized R   OLD: {total_old_r:+.3f}R   COMBINED: {total_combined_r:+.3f}R   REFINED: {total_refined_r:+.3f}R   SPLIT: {total_split_r:+.3f}R")
    print(f"    COMBINED vs OLD: {total_combined_r - total_old_r:+.3f}R   REFINED vs OLD: {total_refined_r - total_old_r:+.3f}R   REFINED vs COMBINED: {total_refined_r - total_combined_r:+.3f}R")
    print(f"    SPLIT vs OLD: {total_split_r - total_old_r:+.3f}R   SPLIT vs REFINED: {total_split_r - total_refined_r:+.3f}R   SPLIT vs COMBINED: {total_split_r - total_combined_r:+.3f}R")
    if usd_known:
        print(f"  total realized $   OLD: {total_old_usd:+.2f}   COMBINED: {total_combined_usd:+.2f}   REFINED: {total_refined_usd:+.2f}   SPLIT: {total_split_usd:+.2f}   ({usd_known}/{len(rows)} trades had known original_risk_money)")
        print(f"    COMBINED vs OLD: {total_combined_usd - total_old_usd:+.2f}   REFINED vs OLD: {total_refined_usd - total_old_usd:+.2f}   REFINED vs COMBINED: {total_refined_usd - total_combined_usd:+.2f}")
        print(f"    SPLIT vs OLD: {total_split_usd - total_old_usd:+.2f}   SPLIT vs REFINED: {total_split_usd - total_refined_usd:+.2f}   SPLIT vs COMBINED: {total_split_usd - total_combined_usd:+.2f}")
    else:
        print("  total realized $: original_risk_money unavailable for all replayed trades -- R-only comparison above.")

    print(f"\n  ROUND-TRIP-TO-LOSS TRADES FIXED BY COMBINED (peaked >=0.2R, OLD ended <=0, COMBINED ended >0): {len(roundtrip_fixed_combined)}")
    for row in roundtrip_fixed_combined:
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  peak_mfe={o['mfe_r']:.3f}R  OLD={o['realized_r']:+.3f}R ({o['final_action']})  COMBINED={c['realized_r']:+.3f}R ({c['final_action']})  REFINED={r['realized_r']:+.3f}R ({r['final_action']})  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})")

    print(f"\n  ROUND-TRIP-TO-LOSS TRADES FIXED BY REFINED (peaked >=0.2R, OLD ended <=0, REFINED ended >0): {len(roundtrip_fixed_refined)}")
    for row in roundtrip_fixed_refined:
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  peak_mfe={o['mfe_r']:.3f}R  OLD={o['realized_r']:+.3f}R ({o['final_action']})  COMBINED={c['realized_r']:+.3f}R ({c['final_action']})  REFINED={r['realized_r']:+.3f}R ({r['final_action']})  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})")

    print(f"\n  ROUND-TRIP-TO-LOSS TRADES FIXED BY SPLIT (peaked >=0.2R, OLD ended <=0, SPLIT ended >0): {len(roundtrip_fixed_split)}")
    for row in roundtrip_fixed_split:
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  peak_mfe={o['mfe_r']:.3f}R  OLD={o['realized_r']:+.3f}R ({o['final_action']})  COMBINED={c['realized_r']:+.3f}R ({c['final_action']})  REFINED={r['realized_r']:+.3f}R ({r['final_action']})  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})")

    # Cross-check: which of COMBINED's saves does REFINED preserve, and which does it lose?
    fixed_combined_ids = {row["trade"]["trade_id"] for row in roundtrip_fixed_combined}
    fixed_refined_ids = {row["trade"]["trade_id"] for row in roundtrip_fixed_refined}
    fixed_split_ids = {row["trade"]["trade_id"] for row in roundtrip_fixed_split}
    preserved = fixed_combined_ids & fixed_refined_ids
    lost = fixed_combined_ids - fixed_refined_ids
    gained = fixed_refined_ids - fixed_combined_ids
    print(f"\n  SAVE OVERLAP: of {len(fixed_combined_ids)} round-trip saves COMBINED achieved, REFINED preserves {len(preserved)}, loses {len(lost)}, and finds {len(gained)} new ones not in COMBINED's set.")
    if lost:
        print("  SAVES LOST going from COMBINED to REFINED:")
        for row in roundtrip_fixed_combined:
            if row["trade"]["trade_id"] in lost:
                t, o, c, r = row["trade"], row["old"], row["combined"], row["refined"]
                print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  OLD={o['realized_r']:+.3f}R  COMBINED={c['realized_r']:+.3f}R ({c['final_action']})  REFINED={r['realized_r']:+.3f}R ({r['final_action']})")

    # Cross-check: which of COMBINED's saves does SPLIT preserve, and which does it lose? These
    # are the "peaked meaningfully positive, round-tripped to a loss under COMBINED's full-close-
    # only behavior, ended net positive" trades -- this is the primary question tonight's SPLIT fix
    # (ea7b1211) is meant to answer, since SPLIT now closes only PARTIALLY below ADAPTIVE_MFE_FULL_CLOSE_R
    # instead of fully, so a "save" may now mean a smaller net-positive outcome, not necessarily the
    # same magnitude as COMBINED's full-close save.
    split_preserved = fixed_combined_ids & fixed_split_ids
    split_lost = fixed_combined_ids - fixed_split_ids
    split_gained = fixed_split_ids - fixed_combined_ids
    print(f"\n  SPLIT SAVE OVERLAP: of {len(fixed_combined_ids)} round-trip saves COMBINED achieved, SPLIT preserves {len(split_preserved)}, loses {len(split_lost)}, and finds {len(split_gained)} new ones not in COMBINED's set.")
    print("  PER-TRADE DETAIL for every COMBINED round-trip save, showing exactly what SPLIT does to it (full action_log, not just final_action):")
    for row in roundtrip_fixed_combined:
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        split_protect_actions = [a for a in s["action_log"] if a["action"] not in ("SL_HIT", "TP_HIT")]
        outcome = "PRESERVED (net positive)" if s["realized_r"] > 0 else "LOST (net <=0 again)"
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} id={t['trade_id']} entry={t['entry_time'].isoformat()}  peak_mfe={o['mfe_r']:.3f}R")
        print(f"        OLD={o['realized_r']:+.3f}R ({o['final_action']})  COMBINED={c['realized_r']:+.3f}R ({c['final_action']}, full-close-only)  SPLIT={s['realized_r']:+.3f}R ({s['final_action']}) -- {outcome}")
        print(f"        SPLIT full action_log: {split_protect_actions if split_protect_actions else 'NONE (MFE_PROTECTION_CLOSE / zone_25_60 never fired under SPLIT)'}")

    # Cut-short losses: trades where COMBINED did WORSE than OLD (the 7 premature MFE_PROTECTION_CLOSE
    # fires) -- check whether REFINED and SPLIT avoid the same cut-shorts.
    combined_worse = [row for row in rows if row["combined"]["realized_r"] < row["old"]["realized_r"] - 1e-6]
    refined_worse = [row for row in rows if row["refined"]["realized_r"] < row["old"]["realized_r"] - 1e-6]
    split_worse = [row for row in rows if row["split"]["realized_r"] < row["old"]["realized_r"] - 1e-6]
    print(f"\n  Trades where COMBINED did WORSE than OLD (cut-short winners etc.): {len(combined_worse)}")
    for row in sorted(combined_worse, key=lambda rr: rr["combined"]["realized_r"] - rr["old"]["realized_r"]):
        t, o, c, r, s = row["trade"], row["old"], row["combined"], row["refined"], row["split"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} id={t['trade_id']} entry={t['entry_time'].isoformat()}  OLD={o['realized_r']:+.3f}R ({o['final_action']})  COMBINED={c['realized_r']:+.3f}R ({c['final_action']})  delta={c['realized_r']-o['realized_r']:+.3f}R  |  REFINED={r['realized_r']:+.3f}R ({r['final_action']})  refined_delta_vs_old={r['realized_r']-o['realized_r']:+.3f}R  |  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})  split_delta_vs_old={s['realized_r']-o['realized_r']:+.3f}R")

    combined_worse_ids = {row["trade"]["trade_id"] for row in combined_worse}
    refined_worse_ids = {row["trade"]["trade_id"] for row in refined_worse}
    split_worse_ids = {row["trade"]["trade_id"] for row in split_worse}
    refined_still_worse = combined_worse_ids & refined_worse_ids
    refined_recovered = combined_worse_ids - refined_worse_ids
    split_still_worse = combined_worse_ids & split_worse_ids
    split_recovered = combined_worse_ids - split_worse_ids
    print(f"\n  Of {len(combined_worse_ids)} trades where COMBINED cut a winner short vs OLD: REFINED still does WORSE than OLD on {len(refined_still_worse)}, and matches/beats OLD on {len(refined_recovered)}.")
    print(f"  Of {len(combined_worse_ids)} trades where COMBINED cut a winner short vs OLD: SPLIT still does WORSE than OLD on {len(split_still_worse)}, and matches/beats OLD on {len(split_recovered)}.")
    if split_still_worse:
        print("  Of COMBINED's cut-shorts, SPLIT is STILL worse than OLD on:")
        for row in combined_worse:
            if row["trade"]["trade_id"] in split_still_worse:
                t, o, s = row["trade"], row["old"], row["split"]
                print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} id={t['trade_id']}  OLD={o['realized_r']:+.3f}R ({o['final_action']})  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})  delta={s['realized_r']-o['realized_r']:+.3f}R")

    print(f"\n  Trades where REFINED did WORSE than OLD (independent check, not derived from COMBINED's list): {len(refined_worse)}")
    for row in sorted(refined_worse, key=lambda rr: rr["refined"]["realized_r"] - rr["old"]["realized_r"])[:15]:
        t, o, c, r = row["trade"], row["old"], row["combined"], row["refined"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  OLD={o['realized_r']:+.3f}R ({o['final_action']})  REFINED={r['realized_r']:+.3f}R ({r['final_action']})  delta={r['realized_r']-o['realized_r']:+.3f}R")

    print(f"\n  Trades where SPLIT did WORSE than OLD (independent check, not derived from COMBINED's list): {len(split_worse)}")
    for row in sorted(split_worse, key=lambda rr: rr["split"]["realized_r"] - rr["old"]["realized_r"])[:15]:
        t, o, s = row["trade"], row["old"], row["split"]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} id={t['trade_id']} entry={t['entry_time'].isoformat()}  OLD={o['realized_r']:+.3f}R ({o['final_action']})  SPLIT={s['realized_r']:+.3f}R ({s['final_action']})  delta={s['realized_r']-o['realized_r']:+.3f}R")

    # Diagnostic: does zone_25_60's TP_PROGRESS_PARTIAL_PROTECT ever actually fire under REFINED
    # (MFE_MIN_R back at 0.5)? final_action only shows the LAST leg, so a trade could have a
    # partial-protect leg mid-trade that isn't visible in the summary table above. Check the full
    # action_log for every trade REFINED matches OLD on (the 4 "lost saves" + the 7 combined
    # cut-shorts) to see whether zone_25_60 fired at all, or fired and simply wasn't enough.
    interesting_ids = {row["trade"]["trade_id"] for row in roundtrip_fixed_combined} | combined_worse_ids
    print("\nZONE_25_60 ACTIVITY CHECK (full action_log for the 4 lost-saves + 7 combined-cut-shorts, REFINED pass):")
    for row in rows:
        if row["trade"]["trade_id"] not in interesting_ids:
            continue
        t, r = row["trade"], row["refined"]
        protect_actions = [a for a in r["action_log"] if a["action"] not in ("SL_HIT", "TP_HIT")]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} entry={t['entry_time'].isoformat()}  REFINED legs={r['num_management_legs']}  management_actions={protect_actions if protect_actions else 'NONE (zone_25_60 never fired)'}")

    print("\nZONE_25_60 / SPLIT ACTIVITY CHECK (full action_log for the 4 lost-saves + 7 combined-cut-shorts, SPLIT pass):")
    for row in rows:
        if row["trade"]["trade_id"] not in interesting_ids:
            continue
        t, s = row["trade"], row["split"]
        protect_actions = [a for a in s["action_log"] if a["action"] not in ("SL_HIT", "TP_HIT")]
        print(f"    {t['symbol']} {t['direction']} {t['strategy_id']} id={t['trade_id']}  SPLIT legs={s['num_management_legs']}  management_actions={protect_actions if protect_actions else 'NONE'}")

    print("\nVERDICT INPUTS:")
    print(f"  REFINED vs OLD:      {'BETTER' if total_refined_r > total_old_r else ('WORSE' if total_refined_r < total_old_r else 'TIED')} ({total_refined_r - total_old_r:+.3f}R)")
    print(f"  REFINED vs COMBINED: {'BETTER' if total_refined_r > total_combined_r else ('WORSE' if total_refined_r < total_combined_r else 'TIED')} ({total_refined_r - total_combined_r:+.3f}R)")
    print(f"  SPLIT vs OLD:        {'BETTER' if total_split_r > total_old_r else ('WORSE' if total_split_r < total_old_r else 'TIED')} ({total_split_r - total_old_r:+.3f}R)")
    print(f"  SPLIT vs REFINED:    {'BETTER' if total_split_r > total_refined_r else ('WORSE' if total_split_r < total_refined_r else 'TIED')} ({total_split_r - total_refined_r:+.3f}R)")
    print(f"  SPLIT vs COMBINED:   {'BETTER' if total_split_r > total_combined_r else ('WORSE' if total_split_r < total_combined_r else 'TIED')} ({total_split_r - total_combined_r:+.3f}R)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
