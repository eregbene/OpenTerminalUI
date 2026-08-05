import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ResearchAgentPage } from "../pages/ResearchAgentPage";
import { runAIProviderHealthCheck } from "../api/researchAgent";

vi.mock("../api/researchAgent", () => ({
  fetchResearchAgentStatus: vi.fn(async () => ({
    mode: "ASSISTED",
    autonomous_default: false,
    active_policy: null,
    kill_switches: [],
    scheduler: { queued: 0 },
    health: "ok",
  })),
  fetchResearchAgentPolicies: vi.fn(async () => []),
  fetchResearchAgentPlans: vi.fn(async () => []),
  fetchAIProviderHealth: vi.fn(async () => [{ provider: "local", state: "CLOSED", configured: true }]),
  fetchAIProviderBudgets: vi.fn(async () => ({ daily_cost_limit: "25.00" })),
  createResearchAgentPolicy: vi.fn(async () => ({ policy_id: "policy_1" })),
  activateResearchAgentPolicy: vi.fn(async () => ({ policy_id: "policy_1" })),
  generateResearchHypotheses: vi.fn(async () => [{ hypothesis_id: "hyp_1", confidence: "UNASSESSED" }]),
  createResearchAgentPlan: vi.fn(async () => ({ plan_id: "plan_1" })),
  approveResearchAgentPlan: vi.fn(),
  rejectResearchAgentPlan: vi.fn(),
  startResearchAgentPlan: vi.fn(),
  pauseResearchAgentPlan: vi.fn(),
  cancelResearchAgentPlan: vi.fn(),
  fetchResearchAgentReports: vi.fn(async () => []),
  fetchResearchAgentLineage: vi.fn(async () => ({ nodes: [] })),
  runAIProviderHealthCheck: vi.fn(async () => ({ status: "ok" })),
}));

describe("ResearchAgentPage", () => {
  it("shows the research-only controls and can run a provider health check", async () => {
    render(<ResearchAgentPage />);

    await waitFor(() => expect(screen.getByText("Research Agent")).toBeInTheDocument());
    expect(screen.getByText(/Research-only system/i)).toBeInTheDocument();
    expect(screen.getByText(/local \| CLOSED \| configured true/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    expect(runAIProviderHealthCheck).toHaveBeenCalledWith("local");
  });
});
