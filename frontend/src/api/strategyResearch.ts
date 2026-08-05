import { api } from "./base";

export type ResearchWorkflowResult = {
  experiment: { experiment_id: string; name: string };
  backtest: {
    run_id: string;
    result?: {
      metrics: {
        total_return: number;
        sharpe_ratio?: number | null;
        maximum_drawdown: number;
        profit_factor?: number | null;
        trade_count: number;
        warnings: string[];
      };
      trades: Array<{ trade_id: string; side: string; net_pnl: number; exit_reason: string; ambiguous_fill: boolean }>;
      warnings: string[];
    } | null;
    lineage: Record<string, unknown>;
  };
  optimization: { job_id?: string | null; trials: Array<{ trial_id: string; parameter_set: Record<string, unknown>; objective_value?: number | null; status: string }> };
  validation: { job_id?: string | null; folds: Array<{ fold_id: string; degradation?: number | null; validation_metrics?: { total_return: number } | null }>; robustness: Array<{ scenario_id: string; passed: boolean }> };
  scorecard: { scorecard_id: string; total_score: number; components: Record<string, number>; gates: Record<string, boolean>; passed: boolean; warnings: string[] };
  candidate: { candidate_id: string; promotion_status: string; promotion_reasons: string[]; rejection_reasons: string[] };
  manifest: Record<string, unknown>;
};

function makeBars(count = 90) {
  const start = Date.UTC(2026, 0, 1);
  let price = 100;
  return Array.from({ length: count }, (_, index) => {
    price += 0.5;
    return {
      timestamp: new Date(start + index * 15 * 60 * 1000).toISOString(),
      open: price - 0.2,
      high: price + 1.2,
      low: price - 0.8,
      close: price,
      volume: 1000,
      is_complete: true,
    };
  });
}

export async function runStrategyResearchWorkflow(strategyId: string): Promise<ResearchWorkflowResult> {
  const response = await api.post<ResearchWorkflowResult>("/research/strategy/workflow", {
    strategy_id: strategyId,
    symbol: "TEST",
    timeframe: "15m",
    bars: makeBars(),
    parameter_space: {
      parameters: [
        {
          name: "Target risk reward",
          path: "targets.0.value",
          type: "float",
          choices: [1.5, 2.0],
          default: 2.0,
        },
      ],
      max_combinations: 10,
    },
    backtest: {
      initial_capital: 100000,
      quantity: 10,
      execution_model: {
        execution_model_id: "next-bar-conservative-v1",
        version: "1.0.0",
        default_order_type: "market_on_next_bar",
        fill_policy: "conservative",
        allow_same_bar_fill: false,
        entry_delay_bars: 1,
      },
      cost_model: {
        cost_model_id: "basic-costs-v1",
        version: "1.0.0",
        fixed_commission: 1,
        per_unit_commission: 0,
        percentage_commission: 0,
        spread_bps: 1,
        slippage_bps: 2,
        atr_slippage_multiple: 0,
      },
      max_bars: 100000,
    },
    walk_forward: {
      train_bars: 40,
      validation_bars: 20,
      step_bars: 20,
      mode: "rolling",
      purge_bars: 0,
      embargo_bars: 0,
    },
    random_seed: 42,
  });
  return response.data;
}
