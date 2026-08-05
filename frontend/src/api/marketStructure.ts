import { api } from "./base";
import type { ChartPoint } from "../types";

export type MarketStructureOverlay = {
  overlay_id: string;
  type: string;
  timestamp?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  price?: string | number | null;
  price_low?: string | number | null;
  price_high?: string | number | null;
  direction: "bullish" | "bearish" | "neutral" | "unknown";
  label: string;
  style_role: string;
  source_event_id?: string | null;
  status: string;
  tooltip_evidence: string[];
};

export type MarketStructureSnapshot = {
  snapshot_id: string;
  symbol: string;
  timeframe: string;
  configuration_version: string;
  configuration_hash: string;
  warnings: string[];
  trend?: {
    state: string;
    evidence: string[];
  } | null;
  breaks: Array<{ id: string; break_kind: string; direction: string; explanation: string }>;
  swings: Array<{ id: string; swing_type: string; price: string | number; confirmation_time: string }>;
  liquidity_levels: Array<{ id: string; side: string; level: string | number; status: string }>;
  liquidity_sweeps: Array<{ id: string; side: string; swept_price: string | number; status: string }>;
  imbalances: Array<{ id: string; direction: string; price_low: string | number; price_high: string | number; status: string }>;
  order_blocks: Array<{ id: string; direction: string; price_low: string | number; price_high: string | number; status: string }>;
  dealing_ranges: Array<{ id: string; price_low: string | number; price_high: string | number; normalized_current_position?: number | null }>;
  overlays: MarketStructureOverlay[];
  score?: { total_score: number } | null;
  explanations: string[];
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

export async function analyzeMarketStructure(params: {
  symbol: string;
  timeframe: string;
  bars: ChartPoint[];
  profile?: "balanced" | "internal" | "external";
}): Promise<MarketStructureSnapshot> {
  const response = await api.post<MarketStructureSnapshot>("/market-structure/analyze", {
      symbol: params.symbol,
      timeframe: params.timeframe,
      profile: params.profile ?? "balanced",
      bars: params.bars.map(pointToBar),
      page_limit: 300,
  });
  return response.data;
}
