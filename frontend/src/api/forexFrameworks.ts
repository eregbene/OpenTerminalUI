import { api } from "./base";

export type FrameworkSignal = {
  framework_id: string;
  framework_name: string;
  framework_version: string;
  symbol: string;
  timeframe: string;
  bias: string;
  signal_type: string;
  confidence: number;
  quality: number;
  status: string;
  market_regime: string;
  supporting_evidence: string[];
  conflicting_evidence: string[];
  missing_evidence: string[];
  limitations: string[];
};

export type FrameworkAnalysis = {
  symbol: string;
  timeframe: string;
  provider_symbol?: string | null;
  provider_metadata?: Record<string, unknown>;
  signals: FrameworkSignal[];
  comparison: {
    overall_framework_bias: string;
    agreement_ratio: number;
    conflict_ratio: number;
    data_quality_score: number;
    weighted_bullish_score: number;
    weighted_bearish_score: number;
    bullish_framework_count: number;
    bearish_framework_count: number;
    neutral_framework_count: number;
    unknown_framework_count: number;
    conflicts: string[];
    overlapping_evidence: string[];
  };
  thesis: {
    directional_bias: string;
    confidence: number;
    label: string;
    read_only: boolean;
    supporting_evidence: string[];
    conflicting_evidence: string[];
    missing_evidence: string[];
  };
};

export async function analyzeForexFrameworks(params: {
  symbol: string;
  timeframe: string;
  range?: string;
  framework_ids?: string[];
}): Promise<FrameworkAnalysis> {
  const response = await api.post<FrameworkAnalysis>("/forex-frameworks/analyze", {
    symbol: params.symbol,
    timeframe: params.timeframe,
    range: params.range ?? "3mo",
    framework_ids: params.framework_ids,
  });
  return response.data;
}
