import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CanonicalPaperControls } from "../components/trading/CanonicalPaperControls";

vi.mock("../api/client", () => ({
  createCanonicalPaperAccount: vi.fn(async () => ({ account_id: "acct_1", name: "Paper", base_currency: "USD", status: "ACTIVE", equity: "100000", buying_power: "100000", cash_balance: "100000" })),
  createCanonicalDeployment: vi.fn(async () => ({ deployment_id: "dep_1", account_id: "acct_1", candidate_id: "cand_1", strategy_id: "s1", strategy_version: "1", status: "PENDING_APPROVAL", stale: false, stale_reasons: [] })),
  approveCanonicalDeployment: vi.fn(async () => ({ deployment_id: "dep_1", account_id: "acct_1", candidate_id: "cand_1", strategy_id: "s1", strategy_version: "1", status: "ENABLED", stale: false, stale_reasons: [] })),
  submitCanonicalIntent: vi.fn(async () => ({
    risk_evaluation: { evaluation_id: "risk_1", decision: "APPROVED", approved_quantity: "10", approved_notional: "1000", blocking_reasons: [], resizing_reasons: [], rules_evaluated: [{ rule_id: "deployment_enabled", status: "PASS", message: "ok" }] },
    order: { order_id: "order_1", account_id: "acct_1", deployment_id: "dep_1", proposal_id: "prop_1", status: "APPROVED", quantity: "10", filled_quantity: "0" },
  })),
  simulateCanonicalOrder: vi.fn(async () => ({ order: { order_id: "order_1", account_id: "acct_1", deployment_id: "dep_1", proposal_id: "prop_1", status: "FILLED", quantity: "10", filled_quantity: "10", average_fill_price: "101" }, fill: {} })),
  setCanonicalEmergencyDisable: vi.fn(async () => ({ status: "ENABLED" })),
}));

describe("CanonicalPaperControls", () => {
  it("runs the canonical paper workflow and shows risk/order state", async () => {
    render(<CanonicalPaperControls />);
    fireEvent.click(screen.getByText("Run Workflow"));
    await waitFor(() => expect(screen.getByText("Canonical paper workflow completed")).toBeInTheDocument());
    expect(screen.getByText("APPROVED")).toBeInTheDocument();
    expect(screen.getByText("FILLED")).toBeInTheDocument();
    expect(screen.getByText(/No broker connection/i)).toBeInTheDocument();
  });
});
