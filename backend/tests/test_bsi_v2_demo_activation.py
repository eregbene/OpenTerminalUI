from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.market_structure.models import ConceptStatus, Direction, ImbalanceZone, LiquidityLevel, LiquiditySide
from backend.mt5_strategies.families import EVALUATORS, evaluate_all, evaluate_bsi
from backend.mt5_strategies.families import bsi_v2_engine
from backend.mt5_strategies.families.bsi_v2_engine import evaluate_bsi_v2_active
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL
from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5

NOW = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Ctx:
    symbol: str
    broker_symbol: str
    generated_at: datetime
    regime: str
    m15_snapshot: SimpleNamespace
    m15_rows: list[dict]
    atr_m15: Decimal
    bid: Decimal
    ask: Decimal
    spread: Decimal
    broker_min_stop_distance: Decimal | None = None
    trendline_pivots: tuple = ()


def _row(i: int, o: str, h: str, l: str, c: str) -> dict:
    return {"time": (NOW + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": l, "close": c}


def _fvg(direction: str = "bullish", low: str = "1.1040", high: str = "1.1060") -> ImbalanceZone:
    return ImbalanceZone(
        id=f"fvg_{direction}_{low}_{high}", symbol="EURUSD", timeframe="M15", start_time=NOW,
        end_time=NOW + timedelta(minutes=45), detected_time=NOW + timedelta(minutes=45),
        confirmation_time=NOW + timedelta(minutes=45), price_low=Decimal(low), price_high=Decimal(high),
        direction=direction, status=ConceptStatus.ACTIVE, strength=0.2,
        configuration_version="1.0", configuration_hash="hash", supporting_bar_indexes=[1, 2, 3],
        midpoint=(Decimal(low) + Decimal(high)) / 2, consequent_encroachment=(Decimal(low) + Decimal(high)) / 2,
    )


def _level(level_id: str, side: str, level: str) -> LiquidityLevel:
    return LiquidityLevel(
        id=level_id, symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW,
        detected_time=NOW, confirmation_time=NOW, price_low=Decimal(level), price_high=Decimal(level),
        direction=Direction.UNKNOWN, configuration_version="1.0", configuration_hash="hash",
        side=side, level=Decimal(level), source="swing", tolerance=Decimal("0.0001"),
    )


def _ctx() -> _Ctx:
    snap = SimpleNamespace(
        breaks=[],
        imbalances=[_fvg()],
        liquidity_levels=[
            _level("target_high", LiquiditySide.BUY_SIDE.value, "1.1150"),
            _level("target_low", LiquiditySide.SELL_SIDE.value, "1.0950"),
        ],
        liquidity_sweeps=[],
        session_levels=[],
        swings=[],
        bsi_v2_fixture={"abc": {"structure_direction": "bullish", "b_leg_target": "1.1150", "stop": "1.1020", "points": ["A", "B", "C"]}},
    )
    return _Ctx(
        "EURUSD",
        "EURUSD",
        NOW + timedelta(minutes=75),
        "NEUTRAL",
        snap,
        [_row(0, "1.1000", "1.1010", "1.0990", "1.1005"), _row(1, "1.1005", "1.1015", "1.0995", "1.1010"), _row(2, "1.1010", "1.1055", "1.1008", "1.1050")],
        Decimal("0.0010"),
        Decimal("1.1050"),
        Decimal("1.1050"),
        Decimal("0.0000"),
    )


def test_active_bsi_dispatch_is_v2_demo_methodology(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_BSI", ACTIVE_MT5)
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_ABC", ACTIVE_MT5)
    monkeypatch.setenv("BSI_V2_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    bsi_v2_engine._DEMO_LIFECYCLES.clear()

    assert EVALUATORS["bsi"] is evaluate_bsi_v2_active
    assert evaluate_bsi is evaluate_bsi_v2_active

    signal = evaluate_all(_ctx(), strategy_ids=["bsi"])[0]

    assert signal.valid is True
    assert signal.strategy_id == "bsi"
    assert signal.strategy_family == "bsi"
    assert signal.evidence["bsi_version"] == BSI_BASELINE_V2_AUDIOVISUAL
    assert signal.evidence["subtype_activation_status"] == ACTIVE_MT5


def test_v2_bsi_candidate_is_active_and_carries_gate_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_BSI", ACTIVE_MT5)
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_ABC", ACTIVE_MT5)
    monkeypatch.setenv("BSI_V2_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    bsi_v2_engine._DEMO_LIFECYCLES.clear()
    signal = evaluate_all(_ctx(), strategy_ids=["bsi"])[0]

    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="demo", signals=[signal], htf_trend_h4="bullish", now=NOW)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["strategy_activation"] == ACTIVE_MT5
    assert candidate["context"]["strategy_id"] == "bsi"
    evidence = candidate["context"]["strategy_evidence"]
    assert evidence["active_methodology"] == BSI_BASELINE_V2_AUDIOVISUAL
    assert evidence["legacy_confidence_hard_gate"] is False
    assert evidence["legacy_historical_intelligence_hard_gate"] is False


def test_bsi_v2_lifecycle_consumption_is_account_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_BSI", ACTIVE_MT5)
    monkeypatch.setenv("BSI_SUBTYPE_ACTIVATION_ABC", ACTIVE_MT5)
    monkeypatch.setenv("BSI_V2_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    bsi_v2_engine._DEMO_LIFECYCLES.clear()

    base = _ctx()
    demo_signal = evaluate_bsi_v2_active(SimpleNamespace(**base.__dict__, account_id="demo_10k"))
    ftmo_signal = evaluate_bsi_v2_active(SimpleNamespace(**base.__dict__, account_id="ftmo_demo_25k"))
    duplicate_demo_signal = evaluate_bsi_v2_active(SimpleNamespace(**base.__dict__, account_id="demo_10k"))

    assert demo_signal.valid is True
    assert ftmo_signal.valid is True
    assert duplicate_demo_signal.valid is False


def test_bsi_v2_runtime_helpers_bypass_only_legacy_gates():
    from backend.brokers.mt5.autonomous import _bsi_v2_submission_blockers, _is_bsi_v2_candidate

    candidate = {
        "context": {
            "strategy_family": "bsi",
            "strategy_evidence": {
                "bsi_version": BSI_BASELINE_V2_AUDIOVISUAL,
                "freshness_status": "AVAILABLE",
                "lifecycle_state": "CONSUMED",
                "bsi_thesis_id": "thesis",
                "bsi_entry_opportunity_id": "opp",
            },
        }
    }

    assert _is_bsi_v2_candidate(candidate) is True
    assert _bsi_v2_submission_blockers(candidate) == []


def test_bsi_v2_adaptive_manager_defaults_to_hold_only(monkeypatch: pytest.MonkeyPatch):
    from backend.adaptive_management.service import AdaptiveManagementService

    monkeypatch.delenv("BSI_V2_ADAPTIVE_ACTIONS_ENABLED", raising=False)
    state = SimpleNamespace(
        entry_price=1.1050,
        current_sl=1.1020,
        original_sl=1.1020,
        current_tp=1.1150,
        original_risk_money=100.0,
        direction="LONG",
        current_volume=0.1,
        max_achieved_r=0.0,
        min_achieved_r=0.0,
        max_tp_progress=0.0,
        tp_progress=0.0,
        winner_classification=None,
        last_management_at=None,
        account_fingerprint=None,
        strategy_id="bsi",
    )

    candidates = AdaptiveManagementService()._evaluate_position(
        db=SimpleNamespace(),
        state=state,
        payload={"price_current": 1.1040, "profit": -10.0},
        context={},
        candles=[],
    )

    assert [candidate.action_type for candidate in candidates] == ["HOLD"]
    assert candidates[0].reason == "bsi_v2_adaptive_actions_disabled"
