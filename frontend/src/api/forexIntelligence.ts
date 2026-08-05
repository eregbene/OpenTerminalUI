import { api } from "./base";

export type ForexIntelligenceSnapshot = {
  snapshot_id: string;
  symbol: string;
  read_only: boolean;
  timeframe: string;
  feature_vector_id?: string | null;
  feature_version?: string;
  engine_version?: string;
  provider?: string;
  provider_symbol?: string | null;
  data_status?: string;
  freshness?: string;
  bars_analyzed: number;
  current_feature: {
    session: string;
    structure_trend: string;
    premium_discount: string;
    trend_score: number;
    momentum_score: number;
    volatility_state: string;
    confluence_score: number;
    confidence: number;
    evidence: string[];
  };
  confluence: {
    score: number;
    confidence: number;
    direction_bias: string;
    evidence: string[];
  };
  regime: {
    label: string;
    volatility: string;
    news_risk: string;
  };
  trade_explanation: {
    market_summary: string;
    invalidation: string;
    expected_probability: number;
    missing_evidence: string[];
  };
};

export async function analyzeForexIntelligence(params: {
  symbol: string;
  timeframe: string;
  bars: Array<{ t: number; o: number; h: number; l: number; c: number; v: number }>;
}): Promise<ForexIntelligenceSnapshot> {
  const response = await api.post<ForexIntelligenceSnapshot>("/forex-intelligence/analyze", {
    symbol: params.symbol,
    timeframe: params.timeframe,
    bars: params.bars.map((bar) => ({
      timestamp: new Date(bar.t * 1000).toISOString(),
      open: bar.o,
      high: bar.h,
      low: bar.l,
      close: bar.c,
      volume: bar.v,
      is_complete: true,
    })),
  });
  return response.data;
}
