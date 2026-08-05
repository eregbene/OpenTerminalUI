import type { UTCTimestamp } from "lightweight-charts";

import type { ChartPoint } from "../../types";

export type ComparisonMode = "normalized" | "price";

const MAX_COMPARISON_CACHE_ENTRIES = 32;
const comparisonCache = new WeakMap<ChartPoint[], Partial<Record<ComparisonMode, Array<{ time: UTCTimestamp; value: number }>>>>();
const comparisonCacheOrder: ChartPoint[][] = [];

function cacheComparison(
  data: ChartPoint[],
  mode: ComparisonMode,
  points: Array<{ time: UTCTimestamp; value: number }>,
) {
  let byMode = comparisonCache.get(data);
  if (!byMode) {
    byMode = {};
    comparisonCache.set(data, byMode);
    comparisonCacheOrder.push(data);
    if (comparisonCacheOrder.length > MAX_COMPARISON_CACHE_ENTRIES) {
      comparisonCacheOrder.shift();
    }
  }
  byMode[mode] = points;
  return points;
}

export function buildComparisonPoints(data: ChartPoint[], mode: ComparisonMode): Array<{ time: UTCTimestamp; value: number }> {
  if (!data?.length) return [];
  const cached = comparisonCache.get(data)?.[mode];
  if (cached) return cached;
  const normalized: Array<{ time: number; close: number }> = [];
  let sorted = true;
  let previousTime = Number.NEGATIVE_INFINITY;
  for (const point of data) {
    const time = Number(point.t);
    const close = Number(point.c);
    if (!Number.isFinite(time) || !Number.isFinite(close)) continue;
    if (time < previousTime) {
      sorted = false;
    }
    previousTime = time;
    normalized.push({ time, close });
  }
  if (!normalized.length) return [];
  if (!sorted) {
    normalized.sort((left, right) => left.time - right.time);
  }
  if (mode === "price") {
    const out = new Array(normalized.length) as Array<{ time: UTCTimestamp; value: number }>;
    for (let index = 0; index < normalized.length; index += 1) {
      const point = normalized[index]!;
      out[index] = {
        time: point.time as UTCTimestamp,
        value: point.close,
      };
    }
    return cacheComparison(data, mode, out);
  }
  const base = normalized[0]?.close || 1;
  const baseDenominator = Math.abs(base || 1);
  const out = new Array(normalized.length) as Array<{ time: UTCTimestamp; value: number }>;
  for (let index = 0; index < normalized.length; index += 1) {
    const point = normalized[index]!;
    out[index] = {
      time: point.time as UTCTimestamp,
      value: ((point.close - base) / baseDenominator) * 100,
    };
  }
  return cacheComparison(data, mode, out);
}
