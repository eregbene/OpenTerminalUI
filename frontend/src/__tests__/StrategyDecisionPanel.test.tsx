import { render, screen } from "@testing-library/react";
import { StrategyDecisionPanel } from "../components/chart-workstation/StrategyDecisionPanel";
import type { StrategyEvaluation, StrategyRegistration } from "../api/strategies";

const strategies: StrategyRegistration[] = [
  {
    strategy_id: "ema_trend_continuation_v1",
    name: "EMA Trend Continuation",
    family: "trend_following",
    version: "1.0.0",
    status: "research",
    required_features: ["indicator.ema.20"],
    required_history: 60,
    allows_long: true,
    allows_short: true,
  },
];

const evaluation: StrategyEvaluation = {
  evaluation_id: "eval_1",
  strategy_id: "ema_trend_continuation_v1",
  strategy_version: "1.0.0",
  symbol: "TEST",
  timeframe: "15m",
  warnings: [],
  decisions: [
    {
      decision_id: "dec_1",
      strategy_id: "ema_trend_continuation_v1",
      strategy_version: "1.0.0",
      symbol: "TEST",
      timeframe: "15m",
      as_of_timestamp: "2026-01-01T00:00:00Z",
      direction: "long",
      decision_type: "long",
      quality_score: 1,
      explanation: "long entry rules passed",
      proposal: null,
    },
  ],
  proposals: [
    {
      proposal_id: "prop_1",
      direction: "long",
      reference_price: 100,
      invalidation_price: 98,
      target_levels: [{ type: "risk_reward", price: 104, rationale: "2:1 risk reward" }],
      rationale: "proposal",
      status: "proposed",
    },
  ],
};

describe("StrategyDecisionPanel", () => {
  it("renders deterministic decision evidence and proposal levels", () => {
    render(
      <StrategyDecisionPanel
        strategies={strategies}
        selectedStrategyId="ema_trend_continuation_v1"
        evaluation={evaluation}
        loading={false}
        error={null}
        onStrategyChange={() => undefined}
      />,
    );

    expect(screen.getByText("Deterministic Inspector")).toBeInTheDocument();
    expect(screen.getByText("long entry rules passed")).toBeInTheDocument();
    expect(screen.getByText("Invalidation")).toBeInTheDocument();
    expect(screen.getByText("98.00")).toBeInTheDocument();
  });
});
