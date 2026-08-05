import { useEffect, useMemo, useState } from "react";
import { fetchChartsBatchWithMeta, type ChartBatchSource } from "../api/client";
import type { ChartResponse } from "../types";
import type { ChartSlot } from "../store/chartWorkstationStore";
import {
  BATCH_TIMEFRAME_MAP,
  buildChartBatchRequestKey,
  chartMarketToApiMarket,
  supportsExtendedHoursRequest,
} from "../shared/chart/chartBatchRequest";

type BatchRecord = {
  data: ChartResponse | null;
  loading: boolean;
  error: string | null;
};

type Result = {
  bySlotId: Record<string, BatchRecord>;
  loadingAny: boolean;
  source: ChartBatchSource | "idle";
};

type BatchChartPayload = ChartResponse & { error?: string };

export function useBatchChartData(slots: ChartSlot[]): Result {
  const [byRequestKey, setByRequestKey] = useState<Record<string, BatchRecord>>({});
  const [source, setSource] = useState<ChartBatchSource | "idle">("idle");

  const requestSignature = useMemo(
    () => slots.map((slot) => buildChartBatchRequestKey(slot)).join("||"),
    [slots],
  );

  const requestItems = useMemo(
    () =>
      slots
        .filter((slot) => Boolean(slot.ticker))
        .map((slot) => ({
          slotId: slot.id,
          key: buildChartBatchRequestKey(slot),
          symbol: slot.ticker!.toUpperCase(),
          interval: BATCH_TIMEFRAME_MAP[slot.timeframe],
          range: "1y",
          market: chartMarketToApiMarket(slot.market),
          extended: slot.extendedHours.enabled && supportsExtendedHoursRequest(slot),
        })),
    [requestSignature],
  );

  useEffect(() => {
    let cancelled = false;
    if (!requestItems.length) {
      setByRequestKey({});
      setSource("idle");
      return;
    }

    setByRequestKey((prev) => {
      const next: Record<string, BatchRecord> = {};
      for (const item of requestItems) {
        next[item.key] = prev[item.key] ?? { data: null, loading: true, error: null };
        next[item.key] = { ...next[item.key], loading: true, error: null };
      }
      return next;
    });

    fetchChartsBatchWithMeta(
      requestItems.map((item) => ({
        symbol: item.symbol,
        interval: item.interval,
        range: item.range,
        market: item.market,
        extended: item.extended,
      })),
    )
      .then(({ data: dataMap, source }) => {
        if (cancelled) return;
        setSource(source);
        setByRequestKey((prev) => {
          const next = { ...prev };
          for (const item of requestItems) {
            const directKey = item.key;
            // The backend keys batch results as MARKET:SYMBOL|interval|range|ext=bool
            // (see _batch_result_key); the parallel fallback uses the same shape.
            const backendKey = `${item.market}:${item.symbol}|${item.interval}|${item.range}|ext=${item.extended ? "true" : "false"}`;
            const maybeRaw = (dataMap[backendKey] ??
              dataMap[directKey] ??
              dataMap[item.symbol] ??
              dataMap[`${item.market}:${item.symbol}`]) as
              | BatchChartPayload
              | undefined;
            const payloadError =
              typeof maybeRaw?.error === "string" && maybeRaw.error.trim().length > 0
                ? maybeRaw.error
                : null;
            const hasBars = Array.isArray(maybeRaw?.data) && maybeRaw.data.length > 0;
            next[directKey] = {
              data: (maybeRaw as ChartResponse | undefined) ?? null,
              loading: false,
              error: payloadError ?? (maybeRaw && hasBars ? null : maybeRaw ? "No chart data returned" : "Batch response missing chart payload"),
            };
          }
          return next;
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setSource("fallback");
        const message = err instanceof Error ? err.message : "Failed to load charts";
        setByRequestKey((prev) => {
          const next = { ...prev };
          for (const item of requestItems) {
            next[item.key] = {
              data: prev[item.key]?.data ?? null,
              loading: false,
              error: message,
            };
          }
          return next;
        });
      });

    return () => {
      cancelled = true;
    };
  }, [requestItems]);

  const bySlotId = useMemo<Record<string, BatchRecord>>(() => {
    const result: Record<string, BatchRecord> = {};
    for (const slot of slots) {
      if (!slot.ticker) {
        result[slot.id] = { data: null, loading: false, error: null };
        continue;
      }
      result[slot.id] = byRequestKey[buildChartBatchRequestKey(slot)] ?? { data: null, loading: true, error: null };
    }
    return result;
  }, [byRequestKey, slots]);

  const loadingAny = Object.values(bySlotId).some((entry) => entry.loading);
  return { bySlotId, loadingAny, source };
}
