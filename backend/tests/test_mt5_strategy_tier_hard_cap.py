"""2026-08-25 strategy-tier hard-cap fix.

Real bug found via live executed-trade data (Tier A/B/C average dollar risk was nearly
identical across all 4 DEMO accounts despite the 1.0/0.5/0.25 multiplier table): the tier
factor previously pre-scaled `base_risk_budget` in autonomous.py::_submit, but that scaled
Decimal was discarded (`_, risk_adjustment_detail = effective_risk_budget_usd(...)`) -- only
`risk_adjustment_detail`'s multiplier/components (confidence/drawdown/economic taper) survived
into calculate_risk_size, which recomputes its own equity_risk_cap fresh from raw account_equity.
The tier reduction never reached any real order's dollar sizing.

Fix: `strategy_tier_cap_multiplier` is now passed into calculate_risk_size explicitly and
enforced via min() as the LAST step (after the confidence/drawdown/economic taper and the
portfolio/prop caps), so final dollar risk can never exceed
`tier_cap_multiplier * equity_risk_cap` regardless of what any other factor computed upstream.
"""
from __future__ import annotations

from decimal import Decimal

from backend.brokers.mt5.execution import MT5ExecutionService
from backend.tests.test_mt5_multi_account_risk_protection_fix import FakeExecutionAdapter, _fake_symbol


def _svc() -> MT5ExecutionService:
    return MT5ExecutionService(FakeExecutionAdapter("demo_10k", login=1))


def _run(svc, symbol, *, equity, entry="4400", stop="4380", target="4460", risk_budget_adjustment=None, tier_cap=None):
    import asyncio

    return asyncio.run(
        svc.calculate_risk_size(
            account_equity=Decimal(equity), symbol=symbol, direction="LONG",
            entry=Decimal(entry), stop=Decimal(stop), target=Decimal(target),
            risk_budget_adjustment=risk_budget_adjustment, strategy_tier_cap_multiplier=tier_cap,
        )
    )


def test_tier_a_unaffected_confidence_taper_only():
    """Tier A (multiplier=1.0) must behave identically to before this fix -- confidence is the
    only reduction, tier applies no additional ceiling."""
    result = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 1.0}, tier_cap=1.0)
    assert result.effective_risk_usd == result.equity_risk_cap_usd
    assert result.strategy_tier_cap_multiplier == Decimal("1.0")
    assert result.strategy_tier_cap_usd == result.equity_risk_cap_usd


def test_tier_b_caps_final_risk_at_half_even_with_full_confidence():
    """The core fix: full confidence (multiplier=1.0, i.e. no taper at all) must NOT let Tier B
    exceed its 0.5x ceiling -- this is exactly the scenario that was previously broken."""
    result = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 1.0}, tier_cap=0.5)
    assert result.effective_risk_usd == (result.equity_risk_cap_usd * Decimal("0.5")).quantize(Decimal("0.01"))
    assert result.strategy_tier_cap_usd == (result.equity_risk_cap_usd * Decimal("0.5")).quantize(Decimal("0.01"))


def test_tier_c_caps_final_risk_at_quarter_even_with_full_confidence():
    result = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 1.0}, tier_cap=0.25)
    assert result.effective_risk_usd == (result.equity_risk_cap_usd * Decimal("0.25")).quantize(Decimal("0.01"))


def test_confidence_can_reduce_below_tier_cap_but_never_above():
    """Confidence taper (0.3x here, deliberately BELOW the 0.5x tier ceiling) must still bind --
    "confidence may reduce risk further" -- while a taper ABOVE the ceiling (1.0x, tested above)
    must be clamped down to the ceiling, never let through."""
    low_confidence = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 0.3}, tier_cap=0.5)
    assert low_confidence.effective_risk_usd == (low_confidence.equity_risk_cap_usd * Decimal("0.3")).quantize(Decimal("0.01"))

    high_confidence = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 0.9}, tier_cap=0.5)
    # 0.9x confidence WOULD exceed the 0.5x tier ceiling -- must be clamped to the ceiling.
    assert high_confidence.effective_risk_usd == (high_confidence.equity_risk_cap_usd * Decimal("0.5")).quantize(Decimal("0.01"))


def test_no_tier_cap_multiplier_is_backward_compatible():
    """A caller that doesn't pass strategy_tier_cap_multiplier (None) gets byte-identical
    behavior to before this parameter existed -- no capping, fields left None."""
    with_cap = _run(_svc(), _fake_symbol(), equity="100000", risk_budget_adjustment={"multiplier": 1.0}, tier_cap=None)
    assert with_cap.strategy_tier_cap_multiplier is None
    assert with_cap.strategy_tier_cap_usd is None
    assert with_cap.effective_risk_usd == with_cap.equity_risk_cap_usd


def test_minimum_lot_risk_exceeding_tier_cap_rejects_not_inflates():
    """The XAUUSD-incident invariant extended to the tier cap: if even the broker's minimum
    tradeable lot's monetary risk exceeds what the tier allows, REJECT -- never round up past
    the tier ceiling to satisfy volume_min."""
    # Small equity + a deliberately wide stop (400 price units on a 4400 entry, ~9%) pushes
    # loss_per_lot high enough that Tier C's small dollar cap can't afford even volume_min.
    result = _run(
        _svc(), _fake_symbol(volume_min="0.5"), equity="1000", entry="4400", stop="4000", target="5200",
        risk_budget_adjustment={"multiplier": 1.0}, tier_cap=0.25,
    )
    assert result.status == "REJECTED"
    assert "VOLUME_BELOW_MINIMUM_RISK_TOO_HIGH" in result.reasons
    assert result.volume == Decimal("0")


def test_tier_cap_applied_after_portfolio_and_prop_caps_still_never_exceeds_tier():
    """Portfolio/prop caps and the tier cap can interact (whichever is tighter binds) -- but the
    tier ceiling must hold even when the OTHER caps would have allowed more."""
    import asyncio

    svc = _svc()
    result = asyncio.run(
        svc.calculate_risk_size(
            account_equity=Decimal("100000"), symbol=_fake_symbol(), direction="LONG",
            entry=Decimal("4400"), stop=Decimal("4380"), target=Decimal("4460"),
            risk_budget_adjustment={"multiplier": 1.0},
            portfolio_available_risk_usd=Decimal("50000"),  # deliberately generous, must not override the tier cap
            prop_remaining_budget_usd=Decimal("50000"),
            strategy_tier_cap_multiplier=0.25,
        )
    )
    assert result.effective_risk_usd == (result.equity_risk_cap_usd * Decimal("0.25")).quantize(Decimal("0.01"))
