import type { ChartSlot, ChartSlotTimeframe } from "../../store/chartWorkstationStore";

export const BATCH_TIMEFRAME_MAP: Record<ChartSlotTimeframe, string> = {
  "1m": "1m",
  "5m": "5m",
  "15m": "15m",
  "1h": "60m",
  "1D": "1d",
  "1W": "1wk",
  "1M": "1mo",
};

export function supportsExtendedHoursRequest(slot: ChartSlot): boolean {
  return slot.market === "US" && (slot.timeframe === "1m" || slot.timeframe === "5m" || slot.timeframe === "15m" || slot.timeframe === "1h");
}

export function chartMarketToApiMarket(market: ChartSlot["market"]): string {
  if (market === "IN") return "NSE";
  if (market === "FX") return "FX";
  return "NASDAQ";
}

export function buildChartBatchRequestKey(slot: ChartSlot): string {
  const market = chartMarketToApiMarket(slot.market);
  const extended = slot.extendedHours.enabled && supportsExtendedHoursRequest(slot);
  return `${slot.id}|${market}|${String(slot.ticker ?? "").toUpperCase()}|${BATCH_TIMEFRAME_MAP[slot.timeframe]}|ext=${extended}`;
}
