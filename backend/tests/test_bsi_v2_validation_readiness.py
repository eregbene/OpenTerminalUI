from __future__ import annotations

from backend.historical_intelligence.bsi_v2_replay import evaluate_bsi_v2_context
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSIDurableLifecycleStore, build_bsi_account_execution_id
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL
from backend.mt5_strategies.families.bsi_v2_primitives import BSILifecycleState
from backend.tests.test_bsi_v2_all_strategies import NOW, _base_ctx, _break, _fvg


def test_durable_lifecycle_survives_restart_and_blocks_duplicate_consumption(tmp_path):
    path = tmp_path / "bsi_v2_lifecycle.json"
    ctx = _base_ctx(imbalances=[_fvg("bullish", "1.1040", "1.1060")], breaks=[_break("bullish", "bos", 3)])
    object.__setattr__(ctx, "account_id", "demo_10k")
    object.__setattr__(ctx, "replay_run_id", "run_a")

    store = BSIDurableLifecycleStore(path, namespace=BSI_BASELINE_V2_AUDIOVISUAL, replay_run_id="run_a")
    first_signals, first_report = evaluate_bsi_v2_context(ctx, lifecycle=store, subtype_order=("bsi_order_flow",))

    assert first_signals[0].valid is True
    assert first_report.total_executable_entries == 1
    opportunity_id = first_signals[0].evidence["bsi_entry_opportunity_id"]
    record = next(iter(store.opportunities.values()))
    assert record.state == BSILifecycleState.CONSUMED
    assert record.armed_at is not None
    assert record.available_at is not None
    assert record.consumed_at is not None
    assert record.last_evaluated_at == record.consumed_at
    assert build_bsi_account_execution_id(opportunity_id, "demo_10k") in record.account_execution_ids

    restarted = BSIDurableLifecycleStore(path, namespace=BSI_BASELINE_V2_AUDIOVISUAL, replay_run_id="run_a")
    second_signals, second_report = evaluate_bsi_v2_context(ctx, lifecycle=restarted, subtype_order=("bsi_order_flow",))

    assert second_signals[0].valid is False
    assert second_signals[0].rejection_reason == "OPPORTUNITY_ALREADY_CONSUMED"
    assert second_report.total_executable_entries == 0


def test_durable_lifecycle_replay_run_scope_does_not_contaminate_new_run(tmp_path):
    path = tmp_path / "bsi_v2_lifecycle.json"
    ctx = _base_ctx(imbalances=[_fvg("bullish", "1.1040", "1.1060")], breaks=[_break("bullish", "bos", 3)])
    object.__setattr__(ctx, "generated_at", NOW)

    run_a = BSIDurableLifecycleStore(path, namespace=BSI_BASELINE_V2_AUDIOVISUAL, replay_run_id="run_a")
    run_b = BSIDurableLifecycleStore(path, namespace=BSI_BASELINE_V2_AUDIOVISUAL, replay_run_id="run_b")

    assert evaluate_bsi_v2_context(ctx, lifecycle=run_a, subtype_order=("bsi_order_flow",))[0][0].valid is True
    assert evaluate_bsi_v2_context(ctx, lifecycle=run_a, subtype_order=("bsi_order_flow",))[0][0].valid is False
    assert evaluate_bsi_v2_context(ctx, lifecycle=run_b, subtype_order=("bsi_order_flow",))[0][0].valid is True


def test_replay_funnel_counts_all_nine_subtypes_without_production_registration():
    ctx = _base_ctx()
    signals, report = evaluate_bsi_v2_context(ctx)

    assert len(signals) == 9
    assert report.contexts_evaluated == 1
    assert set(report.strategy_funnels) == {
        "bsi_order_flow",
        "bsi_asian",
        "bsi_new_york",
        "bsi_abc",
        "bsi_under_over",
        "bsi_0930",
        "bsi_reactionary",
        "bsi_abcd",
        "bsi_ob_liquidity",
    }
    assert sum(row.evaluated for row in report.strategy_funnels.values()) == 9
    assert report.total_valid_setups == 0
