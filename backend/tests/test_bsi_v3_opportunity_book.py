from __future__ import annotations

from backend.brokers.mt5.v3_opportunity_book import (
    canonicalize_opportunities,
    currency_exposure,
    exposure_vector,
    load_queue_rows,
    select_portfolio_opportunities,
    signal_explosion_summary,
)


def _plan(account_id: str, *, opportunity_id: str = "entry:gbpusd:long:1", symbol: str = "GBPUSD", direction: str = "LONG", confidence: float = 87.0, status: str = "CONFIRMED_FOR_ENTRY") -> dict:
    return {
        "account_id": account_id,
        "symbol": symbol,
        "direction": direction,
        "status": status,
        "strategy_id": "bsi_v3_reactionary_block",
        "confluence_strategy_ids": ["bsi_v3_reactionary_block", "bsi_v3_spectre"],
        "bsi_v3_market_context_id": f"context:{symbol}:{direction}:H1",
        "bsi_v3_market_thesis_id": f"thesis:{symbol}:{direction}:H1:2026-09-08",
        "bsi_v3_poi_id": f"poi:{symbol}:{direction}:zone-a",
        "bsi_v3_entry_opportunity_id": opportunity_id,
        "current_plan_confidence": confidence,
        "rr": 1.8,
    }


def test_four_accounts_share_one_canonical_market_opportunity() -> None:
    rows = [_plan(account) for account in ("demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k")]

    canonical = canonicalize_opportunities(rows)
    summary = signal_explosion_summary(rows, select_portfolio_opportunities(canonical))

    assert len(canonical) == 1
    assert canonical[0]["raw_observation_count"] == 4
    assert canonical[0]["account_ids"] == ["demo_10k", "ftmo_demo_100k", "ftmo_demo_25k", "ftmo_demo_50k"]
    assert summary["raw_observations"] == 4
    assert summary["unique_opportunities"] == 1
    assert summary["duplicate_reduction_pct"] == 75.0


def test_m1_confirmation_does_not_create_new_htf_thesis() -> None:
    first = _plan("demo_10k", confidence=85.0)
    second = {
        **_plan("demo_10k", confidence=88.0),
        "bsi_v3_confirmation_id": "confirm:entry:gbpusd:long:1:M1:poll-500",
    }

    canonical = canonicalize_opportunities([first, second])

    assert len(canonical) == 1
    assert canonical[0]["market_thesis_id"] == "thesis:GBPUSD:LONG:H1:2026-09-08"
    assert canonical[0]["quality_score"] >= 88.0


def test_portfolio_selector_defers_weaker_same_usd_short_theme() -> None:
    eurusd = _plan("demo_10k", opportunity_id="entry:eurusd:long:1", symbol="EURUSD", confidence=88.0)
    gbpusd = _plan("demo_10k", opportunity_id="entry:gbpusd:long:1", symbol="GBPUSD", confidence=84.0)

    selected = select_portfolio_opportunities(canonicalize_opportunities([eurusd, gbpusd]))
    decisions = {row["symbol"]: row["portfolio_decision"] for row in selected}

    assert decisions["EURUSD"] == "SELECTED"
    assert decisions["GBPUSD"] == "DEFERRED_CORRELATED_EXPOSURE"


def test_unrelated_opportunities_remain_independent() -> None:
    eurusd = _plan("demo_10k", opportunity_id="entry:eurusd:long:1", symbol="EURUSD", confidence=87.0)
    usdjpy = _plan("demo_10k", opportunity_id="entry:usdjpy:long:1", symbol="USDJPY", confidence=86.0)

    selected = select_portfolio_opportunities(canonicalize_opportunities([eurusd, usdjpy]))

    assert {row["symbol"] for row in selected if row["portfolio_decision"] == "SELECTED"} == {"EURUSD", "USDJPY"}


def test_currency_exposure_for_gold_and_fx() -> None:
    assert currency_exposure("XAUUSD", "LONG").theme == "XAU-long/USD-short"
    assert currency_exposure("EURUSD", "SHORT").theme == "EUR-short/USD-long"
    assert exposure_vector([{"symbol": "EURUSD", "direction": "LONG"}, {"symbol": "GBPUSD", "direction": "LONG"}]) == {
        "EUR": 1.0,
        "GBP": 1.0,
        "USD": -2.0,
    }


def test_load_queue_rows_preserves_full_account_id(tmp_path) -> None:
    path = tmp_path / "queue_ftmo_demo_100k.json"
    path.write_text('[{"symbol":"EURUSD","direction":"LONG"}]', encoding="utf-8")

    rows = load_queue_rows([path], base_stem="queue")

    assert rows[0]["account_id"] == "ftmo_demo_100k"
