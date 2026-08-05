/** @vitest-environment jsdom */
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/base", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path === "/brokers/ibkr/status") {
        return { data: { broker: "IBKR", environment: "PAPER", connection_state: "ACCOUNT_VERIFIED", account_verified: true, market_data_state: "IBKR_DELAYED", reconciliation_status: "MATCHED", emergency_disable: "ACCOUNT_SCOPED", recovery: { status: "RECOVERY_COMPLETE", blocking: false } } };
      }
      if (path === "/brokers/ibkr/contracts") {
        return { data: { items: [{ canonical_symbol: "EURUSD", verified: true, con_id: 1001, minimum_tick: "0.00001", order_types: ["MARKET"], rejection_reasons: [] }] } };
      }
      return { data: {} };
    }),
  },
}));

import { getIbkrPaperStatus, listIbkrForexContracts } from "../api/forexSignals";

describe("IBKR paper acceptance API client", () => {
  it("loads status and contract verification without secrets", async () => {
    await expect(getIbkrPaperStatus()).resolves.toMatchObject({ broker: "IBKR", account_verified: true, connection_state: "ACCOUNT_VERIFIED" });
    await expect(listIbkrForexContracts()).resolves.toEqual([
      expect.objectContaining({ canonical_symbol: "EURUSD", verified: true, con_id: 1001 }),
    ]);
  });
});
