import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResearchWorkflowPage } from "../pages/ResearchWorkflowPage";

vi.mock("../api/strategyResearch", () => ({
  runStrategyResearchWorkflow: vi.fn(async () => ({
    experiment: { experiment_id: "exp_1", name: "EMA research" },
    backtest: {
      run_id: "bt_1",
      lineage: { strategy_hash: "abc" },
      result: {
        metrics: { total_return: 0.12, sharpe_ratio: 1.2, maximum_drawdown: -0.08, trade_count: 12, warnings: [] },
        trades: [],
        warnings: [],
      },
    },
    optimization: { job_id: "opt_1", trials: [{ trial_id: "t1", parameter_set: { "targets.0.value": 2 }, objective_value: 1.2, status: "completed" }] },
    validation: { job_id: "val_1", folds: [{ fold_id: "fold_1", validation_metrics: { total_return: 0.04 } }], robustness: [{ scenario_id: "cost", passed: true }] },
    scorecard: { scorecard_id: "score_1", total_score: 0.8, components: { risk: 0.9 }, gates: { minimum_trades: true, maximum_drawdown: false }, passed: false, warnings: [] },
    candidate: { candidate_id: "cand_1", promotion_status: "rejected", promotion_reasons: ["minimum_trades"], rejection_reasons: ["maximum_drawdown"] },
    manifest: { strategy_hash: "abc", dataset_hash: "def" },
  })),
}));

describe("ResearchWorkflowPage", () => {
  it("runs workflow and shows scorecard gates with research-only boundary", async () => {
    render(<ResearchWorkflowPage />);
    expect(screen.getByText("Research qualification does not activate trading.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    await waitFor(() => expect(screen.getByText("minimum_trades")).toBeInTheDocument());
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText(/dataset_hash/)).toBeInTheDocument();
  });
});
