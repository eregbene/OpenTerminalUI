/** @vitest-environment jsdom */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ForexPage } from "../pages/Forex";

vi.mock("../api/forexIntelligence", () => ({
  analyzeForexIntelligence: vi.fn(async ({ symbol, timeframe }: { symbol: string; timeframe: string }) => ({
    snapshot_id: `${symbol}-${timeframe}`,
    symbol,
    read_only: true,
    timeframe,
    bars_analyzed: 96,
    current_feature: { session: "london", structure_trend: "bullish", premium_discount: "discount", trend_score: 0.5, momentum_score: 0.4, volatility_state: "normal", confluence_score: 0.72, confidence: 0.61, evidence: ["trend=bullish"] },
    confluence: { score: 0.72, confidence: 0.61, direction_bias: "bullish", evidence: ["trend=bullish"] },
    regime: { label: "trend", volatility: "normal", news_risk: "unknown" },
    trade_explanation: { market_summary: `${symbol} context`, invalidation: "opposite swing", expected_probability: 0.58, missing_evidence: [] },
  })),
}));

vi.mock("../api/forexFrameworks", () => ({
  analyzeForexFrameworks: vi.fn(async ({ symbol, timeframe }: { symbol: string; timeframe: string }) => ({
    symbol,
    timeframe,
    provider_symbol: symbol === "XAUUSD" ? "GC=F" : "EURUSD=X",
    provider_metadata: { is_proxy: symbol === "XAUUSD", data_source_type: symbol === "XAUUSD" ? "gold_futures_proxy" : "spot_fx" },
    signals: [
      { framework_id: "trend_following", framework_name: "Trend Following", framework_version: "1.0.0", symbol, timeframe, bias: "BULLISH", signal_type: "trend_continuation", confidence: 0.72, quality: 0.8, status: "VALID", market_regime: "trend", supporting_evidence: ["trend_score=0.5"], conflicting_evidence: [], missing_evidence: [], limitations: [] },
      { framework_id: "ict", framework_name: "ICT Interpretation", framework_version: "1.0.0", symbol, timeframe, bias: "SLIGHTLY_BULLISH", signal_type: "liquidity_draw_interpretation", confidence: 0.44, quality: 0.7, status: "VALID", market_regime: "trend", supporting_evidence: ["session=london"], conflicting_evidence: [], missing_evidence: [], limitations: ["framework-specific interpretation"] },
      { framework_id: "vsa", framework_name: "Volume Spread Analysis", framework_version: "1.0.0", symbol, timeframe, bias: "UNKNOWN", signal_type: "limited_data", confidence: 0, quality: 0.25, status: "INSUFFICIENT_DATA", market_regime: "trend", supporting_evidence: [], conflicting_evidence: [], missing_evidence: ["centralized forex volume"], limitations: ["limited data"] },
    ],
    comparison: { overall_framework_bias: "BULLISH", agreement_ratio: 0.66, conflict_ratio: 0, data_quality_score: 0.58, weighted_bullish_score: 1.1, weighted_bearish_score: 0, bullish_framework_count: 2, bearish_framework_count: 0, neutral_framework_count: 0, unknown_framework_count: 1, conflicts: [], overlapping_evidence: ["SMC/ICT overlap"] },
    thesis: { directional_bias: "BULLISH", confidence: 0.5, label: "analytical_candidate", read_only: true, supporting_evidence: ["trend_score=0.5"], conflicting_evidence: [], missing_evidence: ["centralized forex volume"] },
  })),
}));

vi.mock("../api/forexSignals", () => ({
  getForexSignalSummary: vi.fn(async () => ({
    active_candidate_count: 1,
    awaiting_confirmation_count: 1,
    approved_count: 0,
    rejected_count: 0,
    expired_count: 0,
    active_paper_trades: 0,
    current_paper_pnl: 0,
    emergency_disable_state: "ACCOUNT_SCOPED",
    safe_defaults: {},
  })),
  listForexCandidates: vi.fn(async () => [
    {
      candidate_id: "fxcand_test",
      strategy_id: "trend_pullback_v1",
      symbol: "EURUSD",
      timeframe: "1h",
      direction: "BUY",
      candidate_entry: 1.085,
      stop_price: 1.083,
      target_prices: [1.089],
      risk_reward: 2,
      confidence: 0.62,
      framework_agreement: 0.66,
      regime: "trend",
      session: "london",
      spread: 0.00008,
      data_quality: 1,
      expiration: "2026-01-01T14:00:00Z",
      status: "AWAITING_CONFIRMATION",
      risk_decision: { approved: true, rejection_reasons: [], position_size: 50000, estimated_risk: 250, estimated_margin: 1800 },
      fill_ids: [],
    },
  ]),
  listForexStrategies: vi.fn(async () => ({
    instrument_status: { EURUSD: "PAPER_ELIGIBLE", XAUUSD: "CONTRACT_UNAVAILABLE" },
    items: [
      { strategy_id: "trend_pullback_v1", strategy_name: "Trend Pullback", status: "PAPER_CANDIDATE", supported_symbols: ["EURUSD"], supported_timeframes: ["1h"], supported_regimes: ["trend"], execution_mode: "MANUAL_CONFIRMATION", enabled: true, validation_scorecard_id: "fx4-fixture-scorecard" },
    ],
  })),
  listActiveForexTrades: vi.fn(async () => []),
  getIbkrPaperStatus: vi.fn(async () => ({
    broker: "IBKR",
    environment: "PAPER",
    connection_state: "ACCOUNT_VERIFIED",
    account_verified: true,
    masked_account_id: "DU***67",
    market_data_state: "IBKR_DELAYED",
    reconciliation_status: "MATCHED",
    emergency_disable: "ACCOUNT_SCOPED",
    recovery: { status: "RECOVERY_COMPLETE", blocking: false },
    simulated_adapter: true,
  })),
  listIbkrForexContracts: vi.fn(async () => [
    { canonical_symbol: "EURUSD", verified: true, con_id: 1001, minimum_tick: "0.00001", order_types: ["MARKET"], rejection_reasons: [] },
    { canonical_symbol: "XAUUSD", verified: false, con_id: 0, minimum_tick: "0.01", order_types: [], rejection_reasons: ["CONTRACT_UNAVAILABLE"] },
  ]),
  generateForexCandidate: vi.fn(async () => ({})),
  approveForexCandidate: vi.fn(async () => ({})),
  rejectForexCandidate: vi.fn(async () => ({})),
  cancelForexCandidate: vi.fn(async () => ({})),
}));

vi.mock("recharts", () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Stub,
    AreaChart: Stub,
    Area: Stub,
    CartesianGrid: Stub,
    Tooltip: Stub,
    XAxis: Stub,
    YAxis: Stub,
  };
});

const fetchMock = vi.fn();

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    statusText: "OK",
    text: async () => JSON.stringify(payload),
  };
}

describe("ForexPage", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the cross-rates workflow, pair detail, heatmap, and central bank monitor", async () => {
    fetchMock.mockImplementation(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/forex/cross-rates")) {
        return jsonResponse({
          currencies: ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"],
          matrix: [
            [1, 0.9231, 0.7852, 151.32, 0.8844, 1.5281, 1.3574, 1.6684],
            [1.0833, 1, 0.8505, 163.93, 0.9581, 1.6553, 1.4715, 1.8074],
            [1.2736, 1.1758, 1, 192.76, 1.1263, 1.946, 1.7307, 2.1247],
            [0.0066, 0.0061, 0.0052, 1, 0.0058, 0.0101, 0.009, 0.011],
            [1.1307, 1.0437, 0.8879, 171.08, 1, 1.7278, 1.5348, 1.8864],
            [0.6544, 0.6041, 0.5139, 98.97, 0.5788, 1, 0.8886, 1.092],
            [0.7367, 0.6796, 0.5778, 111.3, 0.6516, 1.1254, 1, 1.229],
            [0.5994, 0.5533, 0.4707, 90.7, 0.53, 0.9157, 0.8137, 1],
          ],
        });
      }
      if (url.endsWith("/api/forex/quotes")) {
        return jsonResponse({
          quotes: [
            { symbol: "EURUSD", display_name: "EUR/USD", price: 1.085, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "GBPUSD", display_name: "GBP/USD", price: 1.274, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "USDJPY", display_name: "USD/JPY", price: 151.32, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 3 },
            { symbol: "USDCHF", display_name: "USD/CHF", price: 0.8844, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "USDCAD", display_name: "USD/CAD", price: 1.3574, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "AUDUSD", display_name: "AUD/USD", price: 0.662, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "NZDUSD", display_name: "NZD/USD", price: 0.5994, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 5 },
            { symbol: "XAUUSD", display_name: "XAU/USD", price: 2353.2, delay_status: "delayed", freshness: "latest", quality_status: "ok", price_precision: 2, data_source_type: "gold_futures_proxy", is_proxy: true },
          ],
        });
      }
      if (url.endsWith("/api/forex/central-banks")) {
        return jsonResponse({
          banks: [
            {
              currency: "USD",
              bank: "Federal Reserve",
              policy_rate: 5.5,
              last_decision_date: "2026-01-28",
              next_decision_date: "2026-05-06",
              last_action: "held",
              last_change_bps: 0,
              days_since_last_decision: 51,
              days_until_next_decision: 47,
              decision_cycle: "scheduled",
            },
          ],
        });
      }
      if (url.includes("/api/forex/candles/EURUSD")) {
        return jsonResponse({
          pair: "EURUSD",
          current_rate: 1.085,
          candles: Array.from({ length: 96 }, (_, idx) => ({ t: idx + 1, o: 1.08 + idx * 0.0001, h: 1.09 + idx * 0.0001, l: 1.07 + idx * 0.0001, c: 1.085 + idx * 0.0001, v: 1000 + idx })),
        });
      }
      if (url.includes("/api/forex/candles/GBPUSD")) {
        return jsonResponse({
          pair: "GBPUSD",
          current_rate: 1.274,
          candles: Array.from({ length: 96 }, (_, idx) => ({ t: idx + 3, o: 1.27 + idx * 0.0001, h: 1.28 + idx * 0.0001, l: 1.269 + idx * 0.0001, c: 1.274 + idx * 0.0001, v: 1200 + idx })),
        });
      }
      if (url.includes("/api/forex/candles/XAUUSD")) {
        return jsonResponse({
          pair: "XAUUSD",
          current_rate: 2353.2,
          candles: Array.from({ length: 96 }, (_, idx) => ({ t: idx + 10, o: 2340 + idx, h: 2342 + idx, l: 2338 + idx, c: 2341 + idx, v: 1200 + idx })),
        });
      }
      throw new Error(`Unhandled fetch: ${url}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/equity/forex?symbol=EURUSD"]}>
          <Routes>
            <Route path="/equity/forex" element={<ForexPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText("Forex & Metals Terminal")).toBeInTheDocument();
    expect(await screen.findByText("Federal Reserve")).toBeInTheDocument();
    expect(screen.getByText("Majors Heatmap")).toBeInTheDocument();
    expect(screen.getByText("EUR/USD Detail")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /XAU\/USD/ }).length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByRole("button", { name: "GBP/USD" }));
    expect(await screen.findByText("Framework Analysis")).toBeInTheDocument();
    expect(await screen.findByText("Quantitative")).toBeInTheDocument();
    expect(await screen.findByText("Smart Money")).toBeInTheDocument();
    expect(await screen.findByText("Forex Signal Center")).toBeInTheDocument();
    expect(await screen.findByText(/trend pullback v1/i)).toBeInTheDocument();
    expect(await screen.findByText("IBKR Paper Status")).toBeInTheDocument();
    expect(await screen.findByText(/ACCOUNT_VERIFIED/)).toBeInTheDocument();
    expect(await screen.findByText(/EURUSD | con_id 1001/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: /XAU\/USD/ })[0]);
    expect(await screen.findByText(/gold futures proxy/i)).toBeInTheDocument();
    expect(await screen.findByText(/Execution disabled until compatible spot or broker-backed contract is verified/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/cross-rates"));
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/central-banks"));
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/quotes"));
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/candles/EURUSD"));
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/candles/GBPUSD"));
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/forex/candles/XAUUSD"));
    });
  });
});
