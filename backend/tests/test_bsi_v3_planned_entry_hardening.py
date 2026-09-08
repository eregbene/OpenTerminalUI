from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _v3_execution_confidence_blockers
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.ownership import is_bensim_owned_position, is_bensim_owned_order
from backend.mt5_strategies.context import REGIME_NEUTRAL, StrategyContext
from backend.mt5_strategies.families.bsi_v3_runtime_detectors import (
    RUNTIME_SPECS,
    _lower_tf_confirms,
    _same_poi_group,
    planned_entry_queue_path,
)


class _FakeAdapter:
    def __init__(self, account_id: str) -> None:
        self.config = MT5Config(account_id=account_id, enabled=True, autonomous_submission_enabled=True)


def _rows_with_recent_fvg(*, timeframe_seconds: int = 60) -> list[dict]:
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = []
    price = Decimal("1.1000")
    for idx in range(10):
        rows.append(
            {
                "time": (start + timedelta(seconds=timeframe_seconds * idx)).isoformat(),
                "open": float(price),
                "high": float(price + Decimal("0.0002")),
                "low": float(price - Decimal("0.0002")),
                "close": float(price + Decimal("0.0001")),
                "tick_volume": 100,
            }
        )
        price += Decimal("0.0001")
    rows[-3]["high"] = 1.1000
    rows[-1]["low"] = 1.1010
    rows[-1]["high"] = 1.1018
    rows[-1]["close"] = 1.1016
    return rows


def _ctx(*, m1_rows: list[dict] | None = None, m5_rows: list[dict] | None = None) -> StrategyContext:
    return StrategyContext(
        account_id="demo_10k",
        symbol="EURUSD",
        broker_symbol="EURUSD",
        generated_at=datetime(2026, 9, 7, 12, 10, tzinfo=timezone.utc),
        regime=REGIME_NEUTRAL,
        regime_confidence=0.0,
        htf_trend_h4="LONG",
        htf_trend_h1="LONG",
        m15_snapshot=None,  # type: ignore[arg-type]
        m15_rows=[],
        h1_rows=[],
        h4_rows=[],
        atr_m15=Decimal("0.0010"),
        bid=Decimal("1.1012"),
        ask=Decimal("1.1013"),
        spread=Decimal("0.0001"),
        m1_rows=m1_rows or [],
        m5_rows=m5_rows or [],
    )


def test_same_thesis_and_entry_opportunity_merge_but_different_thesis_stays_separate() -> None:
    base = {
        "symbol": "EURUSD",
        "direction": "LONG",
        "source_timeframe": "M15",
        "entry": 1.1,
        "stop": 1.099,
        "target": 1.102,
        "fvg_low": 1.0998,
        "fvg_high": 1.1002,
        "bsi_v3_entry_opportunity_id": "entry:eurusd:long:one",
    }
    same = {**base, "strategy_id": "bsi_v3_spectre"}
    different = {**base, "bsi_v3_entry_opportunity_id": "entry:eurusd:long:two"}

    assert _same_poi_group(base, same)
    assert not _same_poi_group(base, different)


def test_m1_required_strategy_does_not_fallback_to_m5() -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_asian_v2")
    confirmed, tf = _lower_tf_confirms(_ctx(m5_rows=_rows_with_recent_fvg(timeframe_seconds=300)), spec, "LONG")

    assert not confirmed
    assert tf == "NONE"


def test_m5_required_strategy_accepts_m5_confirmation() -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_mmxm")
    confirmed, tf = _lower_tf_confirms(_ctx(m5_rows=_rows_with_recent_fvg(timeframe_seconds=300)), spec, "LONG")

    assert confirmed
    assert tf == "M5"


def test_confluence_submission_marks_all_linked_plans_consumed_atomically(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    path = planned_entry_queue_path("demo_10k")
    path.write_text(
        json.dumps(
            [
                {"plan_id": "p1", "status": "CONFIRMED_FOR_ENTRY"},
                {"plan_id": "p2", "status": "CONFIRMED_FOR_ENTRY"},
                {"plan_id": "p3", "status": "CONFIRMED_FOR_ENTRY"},
            ]
        ),
        encoding="utf-8",
    )

    service._mark_planned_entry_submission(["p1", "p2"], "CONSUMED", {"status": "ACCEPTED"})

    rows = json.loads(path.read_text(encoding="utf-8"))
    statuses = {row["plan_id"]: row["status"] for row in rows}
    assert statuses == {"p1": "CONSUMED", "p2": "CONSUMED", "p3": "CONFIRMED_FOR_ENTRY"}


def test_confluence_reject_does_not_mark_consumed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    path = planned_entry_queue_path("demo_10k")
    path.write_text(json.dumps([{"plan_id": "p1", "status": "CONFIRMED_FOR_ENTRY"}]), encoding="utf-8")

    service._mark_planned_entry_submission(["p1"], "BROKER_SUBMISSION_REJECTED", {"status": "RISK_REJECTED"})

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert rows[0]["status"] == "BROKER_SUBMISSION_REJECTED"


def test_bsm_position_and_order_are_bensim_owned() -> None:
    position = SimpleNamespace(magic=0, comment="BSM|v3react+v3spec|0559")
    order = SimpleNamespace(magic=0, comment="BSM|v3mmxm|0915")

    assert is_bensim_owned_position(position, bensim_magic=5601001)
    assert is_bensim_owned_order(order, bensim_magic=5601001)


def test_legacy_bensim_auto_position_still_owned() -> None:
    position = SimpleNamespace(magic=0, comment="BENSIM_AUTO EURUSD")

    assert is_bensim_owned_position(position, bensim_magic=5601001)


def test_magic_number_is_authoritative_even_without_comment() -> None:
    position = SimpleNamespace(magic=5601001, comment="")

    assert is_bensim_owned_position(position, bensim_magic=5601001)


def test_v3_confidence_7999_rejected_and_80_eligible(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80")

    assert _v3_execution_confidence_blockers(79.99) == ["BSI_V3_EXECUTION_CONFIDENCE_BELOW_MIN"]
    assert _v3_execution_confidence_blockers(80.0) == []


def test_confluence_cannot_bypass_execution_confidence_floor(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80")

    blockers = _v3_execution_confidence_blockers(69.0)

    assert "BSI_V3_EXECUTION_CONFIDENCE_BELOW_MIN" in blockers
