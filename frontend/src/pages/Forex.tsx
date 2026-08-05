import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useQuery } from "@tanstack/react-query";

import { analyzeForexIntelligence } from "../api/forexIntelligence";
import { analyzeForexFrameworks, type FrameworkSignal } from "../api/forexFrameworks";
import {
  approveForexCandidate,
  cancelForexCandidate,
  generateForexCandidate,
  getIbkrPaperStatus,
  getForexSignalSummary,
  listIbkrForexContracts,
  listActiveForexTrades,
  listForexCandidates,
  listForexStrategies,
  rejectForexCandidate,
  type ForexCandidate,
} from "../api/forexSignals";
import { CrossRatesMatrix } from "../components/forex/CrossRatesMatrix";
import { CentralBankMonitor, type CentralBankEntry } from "../components/forex/CentralBankMonitor";
import { TerminalBadge } from "../components/terminal/TerminalBadge";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

type PairCandle = {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
};

type PairResponse = {
  pair: string;
  current_rate: number;
  candles: PairCandle[];
};

type ForexQuote = {
  symbol: string;
  display_name: string;
  price: number;
  change_percent?: number | null;
  delay_status: string;
  freshness: string;
  quality_status: string;
  price_precision: number;
  data_source_type?: string;
  is_proxy?: boolean;
  proxy_for?: string | null;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "") || "/api";
const FALLBACK_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"];
const ALLOWED_MAJOR_CURRENCIES = new Set(FALLBACK_CURRENCIES);
const MAJOR_PAIR_PRESETS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "XAUUSD"];
const SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const PAIR_PRECISION: Record<string, number> = {
  EURUSD: 5,
  GBPUSD: 5,
  USDJPY: 3,
  USDCHF: 5,
  USDCAD: 5,
  AUDUSD: 5,
  NZDUSD: 5,
  XAUUSD: 2,
};
const FRAMEWORK_GROUPS: Record<string, string> = {
  trend_following: "Quantitative",
  mean_reversion: "Quantitative",
  momentum: "Quantitative",
  breakout: "Quantitative",
  session_trading: "Quantitative",
  price_action: "Price Action",
  support_resistance: "Price Action",
  supply_demand: "Price Action",
  classical_technical: "Classical Technical",
  dow_theory: "Classical Technical",
  ichimoku: "Classical Technical",
  fibonacci: "Classical Technical",
  market_structure: "Smart Money",
  wyckoff: "Smart Money",
  vsa: "Smart Money",
  smc: "Smart Money",
  ict: "Smart Money",
  auction_market_theory: "Auction and Volume",
  market_profile: "Auction and Volume",
  volume_profile: "Auction and Volume",
  carry_macro: "Macro and Intermarket",
  correlation_intermarket: "Macro and Intermarket",
  harmonic_patterns: "Experimental",
  elliott_wave: "Experimental",
  gann: "Experimental",
};
const FRAMEWORK_FILTERS = ["all", "bullish", "bearish", "neutral", "research", "limited data"];
const USD_QUOTE_RATES: Record<string, number> = {
  USD: 1,
  EUR: 0.9231,
  GBP: 0.7852,
  JPY: 151.32,
  CHF: 0.8844,
  AUD: 1.5281,
  CAD: 1.3574,
  NZD: 1.6684,
};
const FALLBACK_BANKS: CentralBankEntry[] = [
  { currency: "USD", bank: "Federal Reserve", policy_rate: 5.25, last_decision_date: "2026-02-18", next_decision_date: "2026-03-31", last_action: "Hold", last_change_bps: 0, days_since_last_decision: 31, days_until_next_decision: 11, decision_cycle: "6 weeks" },
  { currency: "EUR", bank: "European Central Bank", policy_rate: 3.0, last_decision_date: "2026-03-05", next_decision_date: "2026-04-16", last_action: "Cut", last_change_bps: -25, days_since_last_decision: 14, days_until_next_decision: 27, decision_cycle: "6 weeks" },
  { currency: "GBP", bank: "Bank of England", policy_rate: 4.5, last_decision_date: "2026-02-06", next_decision_date: "2026-03-20", last_action: "Hold", last_change_bps: 0, days_since_last_decision: 42, days_until_next_decision: 0, decision_cycle: "6 weeks" },
  { currency: "JPY", bank: "Bank of Japan", policy_rate: 0.25, last_decision_date: "2026-01-23", next_decision_date: "2026-03-21", last_action: "Hike", last_change_bps: 10, days_since_last_decision: 57, days_until_next_decision: 1, decision_cycle: "2 months" },
  { currency: "CHF", bank: "Swiss National Bank", policy_rate: 1.25, last_decision_date: "2026-03-14", next_decision_date: "2026-06-13", last_action: "Hold", last_change_bps: 0, days_since_last_decision: 5, days_until_next_decision: 86, decision_cycle: "Quarterly" },
  { currency: "AUD", bank: "Reserve Bank of Australia", policy_rate: 4.1, last_decision_date: "2026-03-03", next_decision_date: "2026-04-07", last_action: "Hold", last_change_bps: 0, days_since_last_decision: 16, days_until_next_decision: 18, decision_cycle: "Monthly" },
  { currency: "CAD", bank: "Bank of Canada", policy_rate: 4.0, last_decision_date: "2026-03-12", next_decision_date: "2026-04-23", last_action: "Cut", last_change_bps: -25, days_since_last_decision: 7, days_until_next_decision: 34, decision_cycle: "6 weeks" },
  { currency: "NZD", bank: "Reserve Bank of New Zealand", policy_rate: 5.5, last_decision_date: "2026-02-25", next_decision_date: "2026-04-08", last_action: "Hold", last_change_bps: 0, days_since_last_decision: 23, days_until_next_decision: 19, decision_cycle: "6 weeks" },
];

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizePair(raw: string | null): string {
  const value = String(raw || "").trim().toUpperCase().replace(/[^A-Z]/g, "");
  const pair = value.length >= 6 ? value.slice(0, 6) : "EURUSD";
  return MAJOR_PAIR_PRESETS.includes(pair) ? pair : "EURUSD";
}

function normalizeTimeframe(raw: string | null): string {
  const value = String(raw || "").trim().toLowerCase();
  return SUPPORTED_TIMEFRAMES.includes(value) ? value : "1h";
}

function pairPrecision(pair: string): number {
  return PAIR_PRECISION[pair] ?? 5;
}

function pairLabel(pair: string): string {
  return `${pair.slice(0, 3)}/${pair.slice(3)}`;
}

function frameworkMatchesFilter(signal: FrameworkSignal, filter: string): boolean {
  const bias = signal.bias.toLowerCase();
  const status = signal.status.toLowerCase();
  if (filter === "bullish") return bias.includes("bullish");
  if (filter === "bearish") return bias.includes("bearish");
  if (filter === "neutral") return bias === "neutral" || bias === "unknown";
  if (filter === "research") return signal.limitations.some((item) => item.toLowerCase().includes("research"));
  if (filter === "limited data") return status.includes("insufficient") || signal.signal_type.includes("limited");
  return true;
}

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path.startsWith("/") ? path : `/${path}`}`);
  const raw = await response.text();
  const payload = raw ? JSON.parse(raw) : null;
  if (!response.ok) {
    throw new Error((payload && typeof payload.detail === "string" && payload.detail) || response.statusText || "Request failed");
  }
  return payload as T;
}

function buildFallbackMatrix(currencies: string[]): number[][] {
  return currencies.map((base) =>
    currencies.map((quote) => {
      const baseRate = USD_QUOTE_RATES[base] || 1;
      const quoteRate = USD_QUOTE_RATES[quote] || 1;
      return Number((quoteRate / baseRate).toFixed(6));
    }),
  );
}

function normalizeCrossRates(payload: unknown): { currencies: string[]; matrix: number[][] } {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return { currencies: FALLBACK_CURRENCIES, matrix: buildFallbackMatrix(FALLBACK_CURRENCIES) };
  }
  const raw = payload as Record<string, unknown>;
  const rawCurrencies = Array.isArray(raw.currencies)
    ? raw.currencies.map((row) => String(row || "").trim().toUpperCase()).filter(Boolean)
    : FALLBACK_CURRENCIES;
  const rawMatrix = Array.isArray(raw.matrix)
    ? raw.matrix.map((row) =>
        Array.isArray(row)
          ? row.map((value) => {
              const numeric = Number(value);
              return Number.isFinite(numeric) ? numeric : 0;
            })
          : [],
      )
    : buildFallbackMatrix(rawCurrencies);
  const includedIndices = rawCurrencies
    .map((currency, index) => ({ currency, index }))
    .filter((row) => ALLOWED_MAJOR_CURRENCIES.has(row.currency));
  const currencies = includedIndices.map((row) => row.currency);
  const matrix = includedIndices.map((base) =>
    includedIndices.map((quote) => {
      const numeric = Number(rawMatrix[base.index]?.[quote.index]);
      return Number.isFinite(numeric) && numeric > 0 ? numeric : buildFallbackMatrix(currencies)[currencies.indexOf(base.currency)]?.[currencies.indexOf(quote.currency)] ?? 1;
    }),
  );
  return {
    currencies: currencies.length ? currencies : FALLBACK_CURRENCIES,
    matrix: matrix.length ? matrix : buildFallbackMatrix(currencies.length ? currencies : FALLBACK_CURRENCIES),
  };
}

function buildFallbackPair(pair: string, currencies: string[], matrix: number[][]): PairResponse {
  if (pair === "XAUUSD") {
    const currentRate = 2350;
    const candles = Array.from({ length: 48 }, (_, index) => {
      const drift = Math.sin(index / 5.5) * 8 + (index - 24) * 0.45;
      const close = Number((currentRate + drift).toFixed(2));
      const open = Number((close - 1.2).toFixed(2));
      return { t: Math.floor(Date.now() / 1000) - (47 - index) * 3600, o: open, h: Number((Math.max(open, close) + 3).toFixed(2)), l: Number((Math.min(open, close) - 3).toFixed(2)), c: close, v: 1000 + index * 37 };
    });
    return { pair, current_rate: currentRate, candles };
  }
  const base = pair.slice(0, 3);
  const quote = pair.slice(3, 6);
  const baseIndex = currencies.indexOf(base);
  const quoteIndex = currencies.indexOf(quote);
  const currentRate = Number(matrix[baseIndex]?.[quoteIndex] ?? 1.08);
  const candles = Array.from({ length: 48 }, (_, index) => {
    const drift = Math.sin(index / 5.5) * currentRate * 0.003 + (index - 24) * currentRate * 0.00012;
    const close = Number((currentRate + drift).toFixed(5));
    const open = Number((close - currentRate * 0.0008).toFixed(5));
    const high = Number((Math.max(open, close) + currentRate * 0.0015).toFixed(5));
    const low = Number((Math.min(open, close) - currentRate * 0.0012).toFixed(5));
    return {
      t: Math.floor(Date.now() / 1000) - (47 - index) * 3600,
      o: open,
      h: high,
      l: low,
      c: close,
      v: 1000 + index * 37,
    };
  });
  return { pair, current_rate: currentRate, candles };
}

function normalizePairResponse(payload: unknown, pair: string, currencies: string[], matrix: number[][]): PairResponse {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return buildFallbackPair(pair, currencies, matrix);
  }
  const raw = payload as Record<string, unknown>;
  const candles = Array.isArray(raw.candles)
    ? raw.candles
        .map((candle) => {
          if (!candle || typeof candle !== "object" || Array.isArray(candle)) return null;
          const row = candle as Record<string, unknown>;
          return {
            t: Number(row.t),
            o: Number(row.o),
            h: Number(row.h),
            l: Number(row.l),
            c: Number(row.c),
            v: Number(row.v || 0),
          };
        })
        .filter((candle): candle is PairCandle => candle !== null && Number.isFinite(candle.t))
    : [];
  return {
    pair: String(raw.pair || pair).toUpperCase(),
    current_rate: Number(raw.current_rate ?? raw.currentRate ?? candles[candles.length - 1]?.c ?? 0),
    candles: candles.length ? candles : buildFallbackPair(pair, currencies, matrix).candles,
  };
}

function normalizeBanks(payload: unknown): CentralBankEntry[] {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return FALLBACK_BANKS;
  }
  const raw = payload as Record<string, unknown>;
  const banks = Array.isArray(raw.banks)
    ? raw.banks
        .map((entry) => {
          if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
          const row = entry as Record<string, unknown>;
          return {
            currency: String(row.currency || ""),
            bank: String(row.bank || ""),
            policy_rate: Number(row.policy_rate),
            last_decision_date: String(row.last_decision_date || ""),
            next_decision_date: String(row.next_decision_date || ""),
            last_action: String(row.last_action || ""),
            last_change_bps: Number(row.last_change_bps || 0),
            days_since_last_decision: Number(row.days_since_last_decision || 0),
            days_until_next_decision: Number(row.days_until_next_decision || 0),
            decision_cycle: String(row.decision_cycle || ""),
          };
        })
        .filter((entry): entry is CentralBankEntry => entry !== null && Boolean(entry.currency) && Boolean(entry.bank))
    : [];
  return banks.length ? banks : FALLBACK_BANKS;
}

function currencyStrengthRows(currencies: string[], matrix: number[][]) {
  const usdIndex = currencies.indexOf("USD");
  const values = currencies.map((currency, index) => {
    const usdValue = usdIndex >= 0 ? Number(matrix[index]?.[usdIndex] ?? 1) : 1;
    const safeValue = usdValue > 0 ? usdValue : 1;
    return { currency, usdValue: safeValue, logValue: Math.log(safeValue) };
  });
  const mean = values.reduce((sum, row) => sum + row.logValue, 0) / Math.max(values.length, 1);
  const variance = values.reduce((sum, row) => sum + (row.logValue - mean) ** 2, 0) / Math.max(values.length, 1);
  const deviation = Math.sqrt(variance) || 1;
  return values
    .map((row) => ({
      ...row,
      score: (row.logValue - mean) / deviation,
    }))
    .sort((left, right) => right.score - left.score);
}

function strengthTone(score: number): string {
  const opacity = 10 + Math.round(clamp(Math.abs(score), 0, 2) * 14);
  if (score >= 0) return `bg-emerald-500/${opacity} text-emerald-200`;
  return `bg-rose-500/${opacity} text-rose-200`;
}

export function ForexPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [crossRates, setCrossRates] = useState<{ currencies: string[]; matrix: number[][] }>({
    currencies: FALLBACK_CURRENCIES,
    matrix: buildFallbackMatrix(FALLBACK_CURRENCIES),
  });
  const [banks, setBanks] = useState<CentralBankEntry[]>(FALLBACK_BANKS);
  const [pairData, setPairData] = useState<PairResponse>({ pair: "EURUSD", current_rate: 0, candles: [] });
  const [ratesLoading, setRatesLoading] = useState(true);
  const [pairLoading, setPairLoading] = useState(false);
  const [fallbackMode, setFallbackMode] = useState(false);
  const [frameworkFilter, setFrameworkFilter] = useState("all");
  const [selectedCandidate, setSelectedCandidate] = useState<ForexCandidate | null>(null);
  const [signalAction, setSignalAction] = useState<string | null>(null);
  const selectedPair = normalizePair(searchParams.get("symbol") || searchParams.get("pair"));
  const selectedTimeframe = normalizeTimeframe(searchParams.get("timeframe"));
  const quotesQuery = useQuery({
    queryKey: ["forex-quotes"],
    queryFn: () => requestJson<{ quotes: ForexQuote[] }>("/forex/quotes"),
    staleTime: 30_000,
  });

  const updatePairParams = (pair: string, timeframe = selectedTimeframe) => {
    setSearchParams(new URLSearchParams({ symbol: normalizePair(pair), timeframe: normalizeTimeframe(timeframe) }), { replace: true });
  };

  useEffect(() => {
    let cancelled = false;
    setRatesLoading(true);
    void (async () => {
      const [ratesResult, banksResult] = await Promise.allSettled([
        requestJson<unknown>("/forex/cross-rates"),
        requestJson<unknown>("/forex/central-banks"),
      ]);
      if (cancelled) return;
      const nextCrossRates = ratesResult.status === "fulfilled"
        ? normalizeCrossRates(ratesResult.value)
        : { currencies: FALLBACK_CURRENCIES, matrix: buildFallbackMatrix(FALLBACK_CURRENCIES) };
      setCrossRates(nextCrossRates);
      setBanks(banksResult.status === "fulfilled" ? normalizeBanks(banksResult.value) : FALLBACK_BANKS);
      setFallbackMode(!(ratesResult.status === "fulfilled" && banksResult.status === "fulfilled"));
      setRatesLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (next.get("symbol") !== selectedPair || next.get("timeframe") !== selectedTimeframe || next.has("pair")) {
      next.delete("pair");
      next.set("symbol", selectedPair);
      next.set("timeframe", selectedTimeframe);
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, selectedPair, selectedTimeframe, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    setPairLoading(true);
    void (async () => {
      try {
        const payload = await requestJson<unknown>(`/forex/candles/${encodeURIComponent(selectedPair)}?interval=${encodeURIComponent(selectedTimeframe)}&range=3mo`);
        if (cancelled) return;
        setPairData(normalizePairResponse(payload, selectedPair, crossRates.currencies, crossRates.matrix));
      } catch {
        if (cancelled) return;
        setPairData(buildFallbackPair(selectedPair, crossRates.currencies, crossRates.matrix));
        setFallbackMode(true);
      } finally {
        if (!cancelled) setPairLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [crossRates.currencies, crossRates.matrix, selectedPair, selectedTimeframe]);

  const chartRows = useMemo(
    () =>
      pairData.candles.map((row) => ({
        time: new Date(row.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        close: row.c,
        high: row.h,
        low: row.l,
        volume: row.v,
      })),
    [pairData.candles],
  );
  const lastCandle = pairData.candles[pairData.candles.length - 1];
  const firstCandle = pairData.candles[0];
  const pairChangePct = firstCandle && lastCandle && firstCandle.c !== 0 ? ((lastCandle.c - firstCandle.c) / firstCandle.c) * 100 : 0;
  const strengthRows = useMemo(() => currencyStrengthRows(crossRates.currencies, crossRates.matrix), [crossRates.currencies, crossRates.matrix]);
  const intelligenceQuery = useQuery({
    queryKey: ["forex-intelligence", selectedPair, selectedTimeframe, pairData.candles.length, lastCandle?.t],
    queryFn: () => analyzeForexIntelligence({ symbol: selectedPair, timeframe: selectedTimeframe, bars: pairData.candles }),
    enabled: pairData.candles.length >= 30,
    staleTime: 30_000,
  });
  const intelligence = intelligenceQuery.data ?? null;
  const frameworksQuery = useQuery({
    queryKey: ["forex-frameworks", selectedPair, selectedTimeframe, lastCandle?.t],
    queryFn: () => analyzeForexFrameworks({ symbol: selectedPair, timeframe: selectedTimeframe }),
    enabled: pairData.candles.length >= 30,
    staleTime: 30_000,
  });
  const frameworkAnalysis = frameworksQuery.data ?? null;
  const signalSummaryQuery = useQuery({ queryKey: ["forex-signal-summary"], queryFn: getForexSignalSummary, staleTime: 15_000 });
  const candidatesQuery = useQuery({ queryKey: ["forex-candidates"], queryFn: listForexCandidates, staleTime: 15_000 });
  const strategiesQuery = useQuery({ queryKey: ["forex-strategies"], queryFn: listForexStrategies, staleTime: 60_000 });
  const activeTradesQuery = useQuery({ queryKey: ["forex-active-trades"], queryFn: listActiveForexTrades, staleTime: 15_000 });
  const ibkrStatusQuery = useQuery({ queryKey: ["ibkr-paper-status"], queryFn: getIbkrPaperStatus, staleTime: 15_000 });
  const ibkrContractsQuery = useQuery({ queryKey: ["ibkr-forex-contracts"], queryFn: listIbkrForexContracts, staleTime: 60_000 });
  const quoteRows = quotesQuery.data?.quotes?.length
    ? quotesQuery.data.quotes
    : MAJOR_PAIR_PRESETS.map((pair) => ({ symbol: pair, display_name: pairLabel(pair), price: buildFallbackPair(pair, crossRates.currencies, crossRates.matrix).current_rate, delay_status: "fallback", freshness: "seeded", quality_status: "fallback", price_precision: pairPrecision(pair), data_source_type: pair === "XAUUSD" ? "demo_gold" : "demo_fx", is_proxy: pair === "XAUUSD" }));
  const selectedQuote = quoteRows.find((quote) => quote.symbol === selectedPair);
  const filteredSignals = (frameworkAnalysis?.signals ?? []).filter((signal) => frameworkMatchesFilter(signal, frameworkFilter));
  const groupedSignals = filteredSignals.reduce<Record<string, FrameworkSignal[]>>((groups, signal) => {
    const group = FRAMEWORK_GROUPS[signal.framework_id] || "Experimental";
    groups[group] = [...(groups[group] || []), signal];
    return groups;
  }, {});
  const signalSummary = signalSummaryQuery.data;
  const candidates = candidatesQuery.data ?? [];
  const activeTrades = activeTradesQuery.data ?? [];
  const strategies = strategiesQuery.data?.items ?? [];
  const instrumentStatus = strategiesQuery.data?.instrument_status ?? {};
  const ibkrStatus = ibkrStatusQuery.data;
  const ibkrContracts = ibkrContractsQuery.data ?? [];
  const selectedInstrumentStatus = instrumentStatus[selectedPair] || (selectedPair === "XAUUSD" ? "CONTRACT_UNAVAILABLE" : "ANALYSIS_ONLY");
  const selectedPairCandidates = candidates.filter((candidate) => candidate.symbol === selectedPair).slice(0, 4);

  const refreshSignals = async () => {
    await Promise.all([signalSummaryQuery.refetch(), candidatesQuery.refetch(), activeTradesQuery.refetch(), strategiesQuery.refetch(), ibkrStatusQuery.refetch(), ibkrContractsQuery.refetch()]);
  };

  const handleGenerateCandidate = async () => {
    if (!lastCandle) return;
    setSignalAction("Generating candidate");
    try {
      const candidate = await generateForexCandidate({
        symbol: selectedPair,
        timeframe: selectedTimeframe,
        price: lastCandle.c,
        regime: intelligence?.regime.label || "trend",
        session: intelligence?.current_feature.session || "london",
        data_quality: frameworkAnalysis?.comparison.data_quality_score ?? 1,
        framework_agreement: frameworkAnalysis?.comparison.agreement_ratio ?? 0.66,
        framework_bias: frameworkAnalysis?.comparison.overall_framework_bias ?? intelligence?.confluence.direction_bias ?? "BULLISH",
        frameworks: frameworkAnalysis?.signals.map((signal) => signal.framework_id) ?? ["trend_following", "price_action", "support_resistance"],
        framework_signal_ids: frameworkAnalysis?.signals.map((signal) => signal.framework_id) ?? [],
        feature_vector_id: intelligence?.snapshot_id,
        source_dataset_id: selectedQuote?.data_source_type,
      });
      setSelectedCandidate(candidate);
      await refreshSignals();
    } finally {
      setSignalAction(null);
    }
  };

  const handleCandidateAction = async (candidate: ForexCandidate, action: "approve" | "reject" | "cancel") => {
    setSignalAction(`${action} candidate`);
    try {
      const updated =
        action === "approve"
          ? await approveForexCandidate(candidate.candidate_id)
          : action === "reject"
            ? await rejectForexCandidate(candidate.candidate_id)
            : await cancelForexCandidate(candidate.candidate_id);
      setSelectedCandidate(updated);
      await refreshSignals();
    } finally {
      setSignalAction(null);
    }
  };

  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel
        title="Forex & Metals Terminal"
        subtitle="Major FX, XAU/USD proxy status, pair detail, central banks, and relative strength"
        actions={
          <div className="flex items-center gap-2">
            <TerminalBadge variant={fallbackMode ? "warn" : "live"} dot>
              {fallbackMode ? "Seeded fallback" : "Backend live"}
            </TerminalBadge>
            <TerminalBadge variant="accent">FX</TerminalBadge>
          </div>
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {MAJOR_PAIR_PRESETS.map((pair) => (
            <button
              key={pair}
              type="button"
              onClick={() => updatePairParams(pair)}
              className={`rounded border px-2 py-1 text-[10px] uppercase tracking-[0.14em] ${
                selectedPair === pair
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                  : "border-terminal-border text-terminal-muted hover:text-terminal-text"
              }`}
            >
              {pairLabel(pair)}
            </button>
          ))}
        </div>
        <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          {quoteRows.map((quote) => (
            <button
              key={quote.symbol}
              type="button"
              onClick={() => updatePairParams(quote.symbol)}
              className={`rounded border px-3 py-2 text-left ${selectedPair === quote.symbol ? "border-terminal-accent bg-terminal-accent/15" : "border-terminal-border bg-terminal-panel/50 hover:border-terminal-muted"}`}
            >
              <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">{quote.display_name}</div>
              <div className="mt-1 ot-type-data text-sm text-terminal-text">{Number(quote.price || 0).toFixed(quote.price_precision || pairPrecision(quote.symbol))}</div>
              <div className="mt-1 flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-terminal-muted">
                <span>{quote.delay_status}</span>
                <span className={quote.quality_status === "ok" ? "text-terminal-pos" : "text-terminal-warn"}>{quote.quality_status}</span>
              </div>
              {quote.is_proxy ? (
                <div className="mt-1 text-[9px] uppercase tracking-[0.12em] text-terminal-warn">{quote.data_source_type || "proxy"}</div>
              ) : null}
            </button>
          ))}
        </div>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <div className="rounded border border-terminal-border bg-terminal-panel/50">
            <div className="flex items-center justify-between border-b border-terminal-border px-3 py-2">
              <div>
                <div className="text-xs uppercase tracking-[0.16em] text-terminal-muted">Cross Rates</div>
                <div className="mt-1 text-[11px] text-terminal-muted">Click a cell to open pair detail and rate context</div>
              </div>
              {ratesLoading ? <TerminalBadge variant="info" dot>Refreshing</TerminalBadge> : null}
            </div>
            <div className="p-2">
              <CrossRatesMatrix
                currencies={crossRates.currencies}
                matrix={crossRates.matrix}
                selectedPair={selectedPair}
                onSelectPair={(pair) => {
                  if (MAJOR_PAIR_PRESETS.includes(pair)) updatePairParams(pair);
                }}
              />
            </div>
          </div>

          <div className="grid gap-4">
            <TerminalPanel
              title={`${pairLabel(selectedPair)} Detail`}
              subtitle="Spot trend and intraday range"
              actions={
                <Link
                  to={`/forex/chart?ticker=${encodeURIComponent(selectedPair)}&symbol=${encodeURIComponent(selectedPair)}&market=FX&timeframe=${encodeURIComponent(selectedTimeframe)}`}
                  className="rounded border border-terminal-border px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-muted hover:text-terminal-text"
                >
                  Open Chart
                </Link>
              }
              bodyClassName="h-[280px]"
            >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartRows} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="fx-pair-fill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="var(--ot-color-accent-primary)" stopOpacity={0.28} />
                      <stop offset="100%" stopColor="var(--ot-color-accent-primary)" stopOpacity={0.04} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.14)" />
                  <XAxis dataKey="time" stroke="#94A3B8" tickLine={false} axisLine={false} fontSize={10} minTickGap={24} />
                  <YAxis stroke="#94A3B8" tickLine={false} axisLine={false} fontSize={10} domain={["dataMin", "dataMax"]} />
                  <Tooltip contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155", fontSize: "11px" }} />
                  <Area type="monotone" dataKey="close" stroke="var(--ot-color-accent-primary)" fill="url(#fx-pair-fill)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </TerminalPanel>
            {selectedQuote?.is_proxy ? (
              <div className="rounded border border-terminal-warn/40 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
                {pairLabel(selectedPair)} is currently labeled as {String(selectedQuote.data_source_type || "proxy data").replace(/_/g, " ")}. It is not silently treated as spot gold.
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded border border-terminal-border bg-terminal-panel/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Spot</div>
                <div className="mt-1 ot-type-data text-lg text-terminal-text">{pairData.current_rate.toFixed(pairPrecision(selectedPair))}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-panel/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Window Change</div>
                <div className={`mt-1 ot-type-data text-lg ${pairChangePct >= 0 ? "text-terminal-pos" : "text-terminal-neg"}`}>
                  {pairChangePct >= 0 ? "+" : ""}{pairChangePct.toFixed(2)}%
                </div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-panel/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Range</div>
                <div className="mt-1 ot-type-data text-lg text-terminal-text">
                  {pairData.candles.length
                    ? `${Math.min(...pairData.candles.map((row) => row.l)).toFixed(pairPrecision(selectedPair))} - ${Math.max(...pairData.candles.map((row) => row.h)).toFixed(pairPrecision(selectedPair))}`
                    : "--"}
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {SUPPORTED_TIMEFRAMES.map((timeframe) => (
                <button
                  key={timeframe}
                  type="button"
                  onClick={() => updatePairParams(selectedPair, timeframe)}
                  className={`rounded border px-2 py-1 text-[10px] uppercase tracking-[0.14em] ${selectedTimeframe === timeframe ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent" : "border-terminal-border text-terminal-muted hover:text-terminal-text"}`}
                >
                  {timeframe}
                </button>
              ))}
            </div>
          </div>
        </div>
      </TerminalPanel>

      <TerminalPanel
          title={`${pairLabel(selectedPair)} Intelligence`}
          subtitle="Read-only institutional context before strategy generation"
          actions={intelligenceQuery.isFetching ? <TerminalBadge variant="info" dot>Analyzing</TerminalBadge> : <TerminalBadge variant="accent">Read-only</TerminalBadge>}
        >
          {intelligenceQuery.isError ? (
            <div className="text-xs text-terminal-warn">Intelligence engine unavailable for this candle set.</div>
          ) : intelligence ? (
            <div className="grid gap-3 lg:grid-cols-[0.8fr_1.2fr]">
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Confluence</div>
                  <div className="mt-1 ot-type-data text-xl text-terminal-accent">{Math.round(intelligence.confluence.score * 100)}%</div>
                </div>
                <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Bias</div>
                  <div className="mt-1 text-sm uppercase text-terminal-text">{intelligence.confluence.direction_bias}</div>
                </div>
                <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Regime</div>
                  <div className="mt-1 text-sm uppercase text-terminal-text">{intelligence.regime.label}</div>
                </div>
                <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Session</div>
                  <div className="mt-1 text-sm uppercase text-terminal-text">{intelligence.current_feature.session.replace(/_/g, " ")}</div>
                </div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2 text-xs text-terminal-muted">
                <div className="text-terminal-text">{intelligence.trade_explanation.market_summary}</div>
                <div className="mt-2 grid gap-1 sm:grid-cols-2">
                  <span>Trend: {intelligence.current_feature.structure_trend}</span>
                  <span>Volatility: {intelligence.current_feature.volatility_state}</span>
                  <span>Premium/Discount: {intelligence.current_feature.premium_discount}</span>
                  <span>Expected probability: {(intelligence.trade_explanation.expected_probability * 100).toFixed(1)}%</span>
                </div>
                <div className="mt-2">Invalidation: {intelligence.trade_explanation.invalidation}</div>
              </div>
            </div>
          ) : (
            <div className="text-xs text-terminal-muted">Waiting for enough {pairLabel(selectedPair)} candles.</div>
          )}
        </TerminalPanel>

      <TerminalPanel
        title="Framework Analysis"
        subtitle="Read-only framework agreement, disagreement, and evidence"
        actions={frameworksQuery.isFetching ? <TerminalBadge variant="info" dot>Analyzing</TerminalBadge> : <TerminalBadge variant="accent">Analytical only</TerminalBadge>}
      >
        <div className="mb-3 flex flex-wrap gap-2">
          {FRAMEWORK_FILTERS.map((filter) => (
            <button
              key={filter}
              type="button"
              onClick={() => setFrameworkFilter(filter)}
              className={`rounded border px-2 py-1 text-[10px] uppercase tracking-[0.14em] ${frameworkFilter === filter ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent" : "border-terminal-border text-terminal-muted hover:text-terminal-text"}`}
            >
              {filter}
            </button>
          ))}
        </div>
        {frameworkAnalysis ? (
          <div className="space-y-3">
            <div className="grid gap-2 md:grid-cols-5">
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Overall Bias</div>
                <div className="mt-1 text-sm text-terminal-text">{frameworkAnalysis.comparison.overall_framework_bias.replace(/_/g, " ")}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Agreement</div>
                <div className="mt-1 ot-type-data text-sm text-terminal-pos">{Math.round(frameworkAnalysis.comparison.agreement_ratio * 100)}%</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Conflict</div>
                <div className="mt-1 ot-type-data text-sm text-terminal-warn">{Math.round(frameworkAnalysis.comparison.conflict_ratio * 100)}%</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Valid</div>
                <div className="mt-1 ot-type-data text-sm text-terminal-text">
                  {frameworkAnalysis.comparison.bullish_framework_count + frameworkAnalysis.comparison.bearish_framework_count + frameworkAnalysis.comparison.neutral_framework_count}
                </div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
                <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Unknown</div>
                <div className="mt-1 ot-type-data text-sm text-terminal-muted">{frameworkAnalysis.comparison.unknown_framework_count}</div>
              </div>
            </div>
            {Object.entries(groupedSignals).map(([group, signals]) => (
              <div key={group} className="rounded border border-terminal-border bg-terminal-panel/40 p-3">
                <div className="mb-2 text-xs uppercase tracking-[0.16em] text-terminal-accent">{group}</div>
                <div className="grid gap-2 lg:grid-cols-2">
                  {signals.map((signal) => (
                    <details key={signal.framework_id} className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2 text-xs">
                      <summary className="cursor-pointer list-none">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-terminal-text">{signal.framework_name}</span>
                          <span className="ot-type-data text-terminal-muted">{signal.bias.replace(/_/g, " ")} | {Math.round(signal.confidence * 100)}% | {signal.status.replace(/_/g, " ")}</span>
                        </div>
                      </summary>
                      <div className="mt-2 grid gap-2 text-terminal-muted">
                        <div>Signal: {signal.signal_type.replace(/_/g, " ")}</div>
                        <div>Version: {signal.framework_version}</div>
                        <div>Evidence: {(signal.supporting_evidence || []).slice(0, 3).join(" | ") || "none"}</div>
                        {signal.conflicting_evidence.length ? <div>Conflicts: {signal.conflicting_evidence.slice(0, 3).join(" | ")}</div> : null}
                        {signal.missing_evidence.length ? <div>Missing: {signal.missing_evidence.slice(0, 3).join(" | ")}</div> : null}
                        {signal.limitations.length ? <div>Limitations: {signal.limitations.slice(0, 2).join(" | ")}</div> : null}
                      </div>
                    </details>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : frameworksQuery.isError ? (
          <div className="text-xs text-terminal-warn">Framework analysis unavailable for this candle set.</div>
        ) : (
          <div className="text-xs text-terminal-muted">Waiting for framework evidence.</div>
        )}
      </TerminalPanel>

      <TerminalPanel
        title="Forex Signal Center"
        subtitle="Deterministic paper candidates with manual confirmation and OMS lineage"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <TerminalBadge variant={selectedInstrumentStatus === "PAPER_ELIGIBLE" ? "live" : selectedInstrumentStatus === "CONTRACT_UNAVAILABLE" ? "warn" : "info"}>
              {selectedInstrumentStatus.replace(/_/g, " ")}
            </TerminalBadge>
            <button
              type="button"
              onClick={handleGenerateCandidate}
              disabled={Boolean(signalAction) || !lastCandle || selectedPair === "XAUUSD"}
              className="rounded border border-terminal-accent px-3 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-accent disabled:cursor-not-allowed disabled:border-terminal-border disabled:text-terminal-muted"
            >
              Generate Candidate
            </button>
          </div>
        }
      >
        {selectedPair === "XAUUSD" ? (
          <div className="mb-3 rounded border border-terminal-warn/50 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
            Analysis available. Framework analysis available. Execution disabled until compatible spot or broker-backed contract is verified.
          </div>
        ) : null}
        {signalAction ? <div className="mb-3 text-xs uppercase tracking-[0.16em] text-terminal-accent">{signalAction}</div> : null}
        <div className="grid gap-2 md:grid-cols-4 xl:grid-cols-8">
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Active</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-text">{signalSummary?.active_candidate_count ?? 0}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Awaiting</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-accent">{signalSummary?.awaiting_confirmation_count ?? 0}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Approved</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-pos">{signalSummary?.approved_count ?? 0}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Rejected</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-neg">{signalSummary?.rejected_count ?? 0}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Expired</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-muted">{signalSummary?.expired_count ?? 0}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Trades</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-text">{signalSummary?.active_paper_trades ?? activeTrades.length}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Paper P/L</div>
            <div className="mt-1 ot-type-data text-lg text-terminal-text">{(signalSummary?.current_paper_pnl ?? 0).toFixed(2)}</div>
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">Emergency</div>
            <div className="mt-1 text-xs uppercase text-terminal-muted">{signalSummary?.emergency_disable_state || "Account scoped"}</div>
          </div>
        </div>

        <div className="mt-3 grid gap-3 xl:grid-cols-[1.2fr_0.8fr]">
          <div className="space-y-2">
            {(selectedPairCandidates.length ? selectedPairCandidates : candidates.slice(0, 3)).map((candidate) => (
              <div key={candidate.candidate_id} className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm text-terminal-text">{pairLabel(candidate.symbol)} | {candidate.strategy_id.replace(/_/g, " ")} | {candidate.direction}</div>
                  <TerminalBadge variant={candidate.status === "FILLED" ? "live" : candidate.status === "RISK_REJECTED" || candidate.status === "REJECTED" ? "warn" : "accent"}>
                    {candidate.status.replace(/_/g, " ")}
                  </TerminalBadge>
                </div>
                <div className="mt-2 grid gap-2 sm:grid-cols-4">
                  <span>Entry {candidate.candidate_entry}</span>
                  <span>Stop {candidate.stop_price}</span>
                  <span>Target {candidate.target_prices[0] ?? "-"}</span>
                  <span>R/R {candidate.risk_reward.toFixed(2)}</span>
                  <span>Size {candidate.risk_decision?.position_size ?? 0}</span>
                  <span>Risk ${candidate.risk_decision?.estimated_risk ?? 0}</span>
                  <span>Confidence {Math.round(candidate.confidence * 100)}%</span>
                  <span>Agreement {Math.round(candidate.framework_agreement * 100)}%</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" onClick={() => setSelectedCandidate(candidate)} className="rounded border border-terminal-border px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-text">Review</button>
                  {candidate.status === "AWAITING_CONFIRMATION" ? (
                    <button type="button" onClick={() => handleCandidateAction(candidate, "approve")} className="rounded border border-terminal-accent px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-accent">Approve</button>
                  ) : null}
                  {["CREATED", "AWAITING_CONFIRMATION", "APPROVED"].includes(candidate.status) ? (
                    <>
                      <button type="button" onClick={() => handleCandidateAction(candidate, "reject")} className="rounded border border-terminal-warn px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-warn">Reject</button>
                      <button type="button" onClick={() => handleCandidateAction(candidate, "cancel")} className="rounded border border-terminal-border px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-muted">Cancel</button>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
            {!candidates.length ? <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-3 text-xs text-terminal-muted">No forex candidates yet.</div> : null}
          </div>
          <div className="rounded border border-terminal-border bg-terminal-bg/40 px-3 py-3 text-xs text-terminal-muted">
            <div className="mb-3 rounded border border-terminal-border bg-terminal-panel/40 px-3 py-2">
              <div className="mb-2 text-xs uppercase tracking-[0.16em] text-terminal-accent">IBKR Paper Status</div>
              <div className="grid gap-1">
                <span>Broker: {ibkrStatus?.broker || "IBKR"} | {ibkrStatus?.environment || "PAPER"}</span>
                <span>Adapter: {ibkrStatus?.adapter_mode || (ibkrStatus?.simulated_adapter ? "FIXTURE_IBKR" : "UNKNOWN")}</span>
                <span>Source: {ibkrStatus?.state_source || "LEGACY_FIXTURE_FILE"}</span>
                <span>Connection: {ibkrStatus?.connection_state || "DISCONNECTED"}</span>
                <span>Account: {ibkrStatus?.account_verified ? "Verified" : "Unverified"} {ibkrStatus?.masked_account_id || ""}</span>
                <span>Market data: {ibkrStatus?.market_data_state || "UNAVAILABLE"}</span>
                <span>Reconciliation: {ibkrStatus?.reconciliation_status || "UNKNOWN"}</span>
                <span>Recovery: {ibkrStatus?.recovery?.status || "IDLE"}</span>
                {ibkrStatus?.fixture_mode || ibkrStatus?.simulated_adapter ? <span className="text-terminal-warn">Fixture IBKR, not real paper connectivity</span> : null}
                {ibkrStatus?.real_submission_enabled ? <span className="text-terminal-pos">Real paper submission gate enabled</span> : <span className="text-terminal-warn">Real paper submission blocked</span>}
              </div>
            </div>
            <div className="mb-3 rounded border border-terminal-border bg-terminal-panel/40 px-3 py-2">
              <div className="mb-2 text-xs uppercase tracking-[0.16em] text-terminal-accent">Contract Status</div>
              <div className="space-y-1">
                {ibkrContracts.slice(0, 4).map((contract) => (
                  <div key={contract.canonical_symbol} className="flex items-center justify-between gap-2">
                    <span>{contract.canonical_symbol} | con_id {contract.con_id || "-"} | {contract.verification_source || "FIXTURE"}</span>
                    <span className={contract.usable_for_real_submission ? "text-terminal-pos" : "text-terminal-warn"}>{contract.verified ? `tick ${contract.minimum_tick} | ${contract.usable_for_real_submission ? "execution allowed" : "execution blocked"}` : contract.rejection_reasons.join("|")}</span>
                  </div>
                ))}
                {!ibkrContracts.length ? <div>Contracts not verified.</div> : null}
              </div>
            </div>
            <div className="mb-2 text-xs uppercase tracking-[0.16em] text-terminal-accent">Strategy Controls</div>
            <div className="space-y-2">
              {strategies.slice(0, 5).map((strategy) => (
                <div key={strategy.strategy_id} className="flex items-center justify-between gap-2 border-b border-terminal-border/60 pb-2">
                  <div>
                    <div className="text-terminal-text">{strategy.strategy_name}</div>
                    <div>{strategy.status} | {strategy.execution_mode}</div>
                  </div>
                  <div className={strategy.enabled ? "text-terminal-pos" : "text-terminal-muted"}>{strategy.enabled ? "Enabled" : "Off"}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 text-xs uppercase tracking-[0.16em] text-terminal-accent">Active Paper Trades</div>
            <div className="mt-2 space-y-2">
              {activeTrades.map((trade) => (
                <div key={trade.trade_id} className="rounded border border-terminal-border px-2 py-2">
                  <div className="text-terminal-text">{pairLabel(trade.symbol)} | {trade.direction} | {trade.quantity}</div>
                  <div>Entry {trade.entry_price} | P/L {trade.unrealized_pnl.toFixed(2)} | {trade.reconciliation_state}</div>
                </div>
              ))}
              {!activeTrades.length ? <div>No active paper trades.</div> : null}
            </div>
          </div>
        </div>

        {selectedCandidate ? (
          <div className="mt-3 rounded border border-terminal-accent/50 bg-terminal-panel/60 px-3 py-3 text-xs">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="text-sm text-terminal-text">Candidate Review | {selectedCandidate.candidate_id}</div>
              <button type="button" onClick={() => setSelectedCandidate(null)} className="rounded border border-terminal-border px-2 py-1 text-[10px] uppercase tracking-[0.14em] text-terminal-muted">Close</button>
            </div>
            <div className="grid gap-2 md:grid-cols-3 text-terminal-muted">
              <span>Strategy: {selectedCandidate.strategy_id}</span>
              <span>Status: {selectedCandidate.status}</span>
              <span>Expiration: {new Date(selectedCandidate.expiration).toLocaleString()}</span>
              <span>Risk approved: {selectedCandidate.risk_decision?.approved ? "yes" : "no"}</span>
              <span>Margin: {selectedCandidate.risk_decision?.estimated_margin ?? 0}</span>
              <span>OMS order: {selectedCandidate.oms_order_id || "-"}</span>
              <span>Regime: {selectedCandidate.regime}</span>
              <span>Session: {selectedCandidate.session}</span>
              <span>Data quality: {Math.round(selectedCandidate.data_quality * 100)}%</span>
            </div>
            {selectedCandidate.risk_decision?.rejection_reasons?.length ? (
              <div className="mt-2 text-terminal-warn">Risk rejection: {selectedCandidate.risk_decision.rejection_reasons.join(" | ")}</div>
            ) : null}
            <div className="mt-2 text-terminal-muted">
              Evidence: {Array.isArray(selectedCandidate.explanation?.supporting_frameworks) ? selectedCandidate.explanation?.supporting_frameworks.join(" | ") : "stored deterministic facts"}
            </div>
          </div>
        ) : null}
      </TerminalPanel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <TerminalPanel title="Majors Heatmap" subtitle="Relative strength vs USD basket proxy">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {strengthRows.map((row) => (
              <div key={row.currency} className={`rounded border border-terminal-border px-3 py-3 ${strengthTone(row.score)}`}>
                <div className="text-[10px] uppercase tracking-[0.16em]">{row.currency}</div>
                <div className="mt-1 ot-type-data text-lg">{row.usdValue.toFixed(4)}</div>
                <div className="mt-1 text-[11px]">
                  Score {row.score >= 0 ? "+" : ""}{row.score.toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        </TerminalPanel>

        <TerminalPanel
          title="Central Bank Monitor"
          subtitle="Policy rates, recent decisions, and upcoming meetings"
          actions={pairLoading ? <TerminalBadge variant="info" dot>Pair updating</TerminalBadge> : null}
        >
          <CentralBankMonitor banks={banks} loading={ratesLoading} />
        </TerminalPanel>
      </div>

      {fallbackMode ? (
        <div className="rounded border border-terminal-warn/40 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
          Forex backend routes are implemented in the workspace, but global router registration is still outside this packet scope, so the page can fall back to seeded data.
        </div>
      ) : null}
    </div>
  );
}
