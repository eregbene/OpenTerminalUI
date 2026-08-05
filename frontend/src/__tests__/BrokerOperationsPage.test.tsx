import { render, screen, waitFor } from "@testing-library/react";

import { BrokerOperationsPage } from "../pages/BrokerOperationsPage";

vi.mock("../api/brokers", () => ({
  fetchBrokerHealth: vi.fn(async () => ({ connection_state: "CONNECTED", environment: "PAPER", account_verification_status: "PAPER_VERIFIED", order_submission_status: "ENABLED", client_id: 111 })),
  fetchBrokerCapabilities: vi.fn(async () => ({ order_types: ["MARKET"], live_trading_enabled: false })),
  fetchIbkrAccounts: vi.fn(async () => [{ account_id: "DU1234567", alias: "DU***67", paper_verified: true }]),
  fetchIbkrSnapshot: vi.fn(async () => ({ account_id: "DU1234567", net_liquidation: "100000" })),
  fetchIbkrPositions: vi.fn(async () => []),
  fetchIbkrOrders: vi.fn(async () => []),
  fetchIbkrExecutions: vi.fn(async () => []),
  connectBroker: vi.fn(async () => ({})),
  disconnectBroker: vi.fn(async () => ({})),
  resolveIbkrContract: vi.fn(async () => ({ instrument_id: "FX:EURUSD" })),
  fetchIbkrQuote: vi.fn(async () => ({ mode: "DELAYED" })),
  fetchIbkrHistoricalBars: vi.fn(async () => []),
  reconcileIbkrAccount: vi.fn(async () => ({ status: "MATCHED" })),
}));

describe("BrokerOperationsPage", () => {
  it("shows IBKR paper account safety and broker status", async () => {
    render(<BrokerOperationsPage />);
    await waitFor(() => expect(screen.getByText("IBKR PAPER ACCOUNT")).toBeInTheDocument());
    expect(screen.getByText(/Live trading is disabled/i)).toBeInTheDocument();
    expect(screen.getByText(/Paper verification: PAPER_VERIFIED/i)).toBeInTheDocument();
  });
});
