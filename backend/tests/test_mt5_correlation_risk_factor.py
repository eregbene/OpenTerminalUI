"""Priority 5.5: direction-aware rolling-correlation risk reduction
(MT5AutonomousTradingService._correlation_matrix_risk_factor) -- the previously-computed-but-
unused correlation_engine.matrix() now feeds a size taper when a candidate would CONCENTRATE
risk with an already-open position, and explicitly leaves size untouched when it would OFFSET
one. Covers the scenarios the user asked for directly: highly correlated instruments without a
shared currency, negatively correlated offsetting exposure, and unrelated positions.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import backend.brokers.mt5.autonomous as autonomous_module
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.brokers.mt5.models import MT5Position
from backend.tests.test_mt5_adapter import fake_adapter


def _position(symbol: str, direction: str, volume: float = 1.0) -> MT5Position:
    return MT5Position(
        ticket=1, symbol=symbol, type=(0 if direction == "LONG" else 1), volume=Decimal(str(volume)),
        price_open=Decimal("1.1000"), price_current=Decimal("1.1010"), sl=Decimal("1.0950"), tp=Decimal("1.1100"),
        profit=Decimal("0"),
    )


def _candidate(symbol: str, direction: str) -> dict:
    return {"broker_symbol": symbol, "direction": direction}


def _matrix(pairs: dict[tuple[str, str], float]) -> dict:
    """Builds a matrix dict covering exactly the symbol pairs the test cares about --
    correlation_engine.matrix's own real shape (matrix[left][right])."""
    symbols = sorted({s for pair in pairs for s in pair})
    matrix = {left: {right: (1.0 if left == right else pairs.get((left, right), pairs.get((right, left)))) for right in symbols} for left in symbols}
    return {"timeframe": "M15", "symbols": symbols, "matrix": matrix, "highly_correlated": []}


def _service(monkeypatch, *, positions: list[MT5Position], matrix: dict) -> MT5AutonomousTradingService:
    service = MT5AutonomousTradingService(fake_adapter())

    async def _fake_positions():
        return positions

    async def _fake_matrix(symbols, adapter=None):
        return matrix

    monkeypatch.setattr(service.adapter, "mt5_positions", _fake_positions)
    monkeypatch.setattr(autonomous_module.correlation_engine, "matrix", _fake_matrix)
    return service


def test_no_open_positions_means_no_reduction(monkeypatch):
    service = _service(monkeypatch, positions=[], matrix=_matrix({}))

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("EURUSD", "LONG")))

    assert factor == 1.0
    assert detail["status"] == "NO_OPEN_POSITIONS"


def test_unrelated_low_correlation_position_means_no_reduction(monkeypatch):
    service = _service(
        monkeypatch,
        positions=[_position("USDJPY", "LONG")],
        matrix=_matrix({("EURUSD", "USDJPY"): 0.05}),
    )

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("EURUSD", "LONG")))

    assert factor == 1.0
    assert detail["status"] == "NO_CONCENTRATION"


def test_highly_correlated_same_direction_without_shared_currency_reduces_size(monkeypatch):
    # XAUUSD and AUDUSD share no base/quote currency pairing relevant here in spirit -- the
    # point is this reduction fires purely off rolling-return correlation, independent of the
    # currency-factor mechanism entirely (which is tested separately in
    # test_portfolio_execution_manager.py).
    service = _service(
        monkeypatch,
        positions=[_position("XAUUSD", "LONG")],
        matrix=_matrix({("AUDUSD", "XAUUSD"): 0.85}),
    )

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("AUDUSD", "LONG")))

    assert factor < 1.0
    assert detail["status"] == "CONCENTRATION_REDUCED"
    assert detail["matched_position"]["symbol"] == "XAUUSD"


def test_negatively_correlated_offsetting_exposure_is_never_reduced(monkeypatch):
    # EURUSD LONG existing, candidate USDCHF LONG: EURUSD/USDCHF are strongly negatively
    # correlated in reality, and here BOTH are LONG (same direction) -- a genuine offsetting
    # combination (a real negative-correlation pair, same direction) must not be penalized.
    service = _service(
        monkeypatch,
        positions=[_position("EURUSD", "LONG")],
        matrix=_matrix({("EURUSD", "USDCHF"): -0.85}),
    )

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("USDCHF", "LONG")))

    assert factor == 1.0
    assert detail["status"] == "NO_CONCENTRATION"


def test_negatively_correlated_pair_in_opposite_directions_is_treated_as_concentration(monkeypatch):
    # EURUSD LONG existing (a "long EUR, short USD" bet). Candidate: USDCHF SHORT (also
    # "short USD, long CHF"). EURUSD/USDCHF are strongly negatively correlated, and the
    # directions are OPPOSITE -- two negatives make a positive: this is effectively the SAME
    # short-USD bet twice, exactly the user's own EURUSD-long+USDCHF-short example.
    service = _service(
        monkeypatch,
        positions=[_position("EURUSD", "LONG")],
        matrix=_matrix({("EURUSD", "USDCHF"): -0.85}),
    )

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("USDCHF", "SHORT")))

    assert factor < 1.0
    assert detail["status"] == "CONCENTRATION_REDUCED"


def test_reduction_never_exceeds_the_configured_maximum(monkeypatch):
    monkeypatch.setenv("MT5_CORRELATION_CONCENTRATION_MAX_REDUCTION", "0.50")
    service = _service(
        monkeypatch,
        positions=[_position("GBPUSD", "LONG")],
        matrix=_matrix({("EURUSD", "GBPUSD"): 1.0}),
    )

    factor, _ = asyncio.run(service._correlation_matrix_risk_factor(_candidate("EURUSD", "LONG")))

    assert factor == pytest.approx(0.50, abs=1e-4)


def test_correlation_matrix_unavailable_fails_open_to_no_reduction(monkeypatch):
    service = MT5AutonomousTradingService(fake_adapter())

    async def _fake_positions():
        return [_position("GBPUSD", "LONG")]

    async def _boom(symbols, adapter=None):
        raise RuntimeError("broker down")

    monkeypatch.setattr(service.adapter, "mt5_positions", _fake_positions)
    monkeypatch.setattr(autonomous_module.correlation_engine, "matrix", _boom)

    factor, detail = asyncio.run(service._correlation_matrix_risk_factor(_candidate("EURUSD", "LONG")))

    assert factor == 1.0
    assert detail["status"] == "UNAVAILABLE"
