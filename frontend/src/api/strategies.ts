import { api } from "./base";
import type { ChartPoint } from "../types";

export type StrategyRegistration = {
  strategy_id: string;
  name: string;
  family: string;
  version: string;
  status: string;
  required_features: string[];
  required_history: number;
  allows_long: boolean;
  allows_short: boolean;
};

export type StrategyDecision = {
  decision_id: string;
  strategy_id: string;
  strategy_version: string;
  symbol: string;
  timeframe: string;
  as_of_timestamp: string;
  direction: string;
  decision_type: string;
  quality_score: number;
  explanation: string;
  rule_result?: unknown;
  proposal?: TradeProposal | null;
};

export type TradeProposal = {
  proposal_id: string;
  direction: string;
  reference_price: number;
  invalidation_price?: number | null;
  target_levels: Array<{ type: string; price: number; rationale: string }>;
  rationale: string;
  status: string;
};

export type StrategyEvaluation = {
  evaluation_id: string;
  strategy_id: string;
  strategy_version: string;
  symbol: string;
  timeframe: string;
  decisions: StrategyDecision[];
  proposals: TradeProposal[];
  warnings: string[];
};

function pointToBar(point: ChartPoint) {
  return {
    timestamp: new Date(point.t * 1000).toISOString(),
    open: point.o,
    high: point.h,
    low: point.l,
    close: point.c,
    volume: point.v,
    is_complete: true,
  };
}

export async function listStrategies(): Promise<StrategyRegistration[]> {
  const response = await api.get<StrategyRegistration[]>("/strategies");
  return response.data;
}

export async function evaluateStrategy(params: {
  strategyId: string;
  symbol: string;
  timeframe: string;
  bars: ChartPoint[];
  assetClass?: string;
}): Promise<StrategyEvaluation> {
  const response = await api.post<StrategyEvaluation>("/strategies/evaluate", {
    strategy_id: params.strategyId,
    symbol: params.symbol,
    timeframe: params.timeframe,
    asset_class: params.assetClass ?? "unknown",
    bars: params.bars.map(pointToBar),
  });
  return response.data;
}
