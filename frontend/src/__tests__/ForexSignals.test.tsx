/** @vitest-environment jsdom */
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/base", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path === "/forex-signals/summary") return { data: { active_candidate_count: 1 } };
      if (path === "/forex-signals/candidates") return { data: { items: [{ candidate_id: "fxcand_test" }] } };
      if (path === "/forex-strategies") return { data: { items: [{ strategy_id: "trend_pullback_v1" }], instrument_status: { XAUUSD: "CONTRACT_UNAVAILABLE" } } };
      if (path === "/forex-execution/active") return { data: { items: [{ trade_id: "fxtrade_test" }] } };
      return { data: {} };
    }),
    post: vi.fn(async (_path: string, payload?: unknown) => ({ data: { candidate_id: "fxcand_test", payload } })),
  },
}));

import {
  generateForexCandidate,
  getForexSignalSummary,
  listActiveForexTrades,
  listForexCandidates,
  listForexStrategies,
} from "../api/forexSignals";

describe("forexSignals API", () => {
  it("maps FX-4 signal endpoints", async () => {
    await expect(getForexSignalSummary()).resolves.toMatchObject({ active_candidate_count: 1 });
    await expect(listForexCandidates()).resolves.toEqual([{ candidate_id: "fxcand_test" }]);
    await expect(listForexStrategies()).resolves.toMatchObject({ instrument_status: { XAUUSD: "CONTRACT_UNAVAILABLE" } });
    await expect(listActiveForexTrades()).resolves.toEqual([{ trade_id: "fxtrade_test" }]);
    await expect(generateForexCandidate({ symbol: "EURUSD", timeframe: "1h", price: 1.085 })).resolves.toMatchObject({ candidate_id: "fxcand_test" });
  });
});
