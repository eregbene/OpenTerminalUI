import { useState } from "react";
import { useAgentStore } from "../agent/agentStore";
import { runStrategyResearchWorkflow, type ResearchWorkflowResult } from "../api/strategyResearch";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

const STRATEGIES = [
  ["ema_trend_continuation_v1", "EMA Trend Continuation"],
  ["rsi_mean_reversion_v1", "RSI Mean Reversion"],
  ["donchian_breakout_v1", "Donchian Breakout"],
];

export function ResearchWorkflowPage() {
  const [strategyId, setStrategyId] = useState("ema_trend_continuation_v1");
  const [result, setResult] = useState<ResearchWorkflowResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const explainEntity = useAgentStore((state) => state.explainEntity);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      setResult(await runStrategyResearchWorkflow(strategyId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Research workflow failed");
    } finally {
      setLoading(false);
    }
  }

  const metrics = result?.backtest.result?.metrics;
  return (
    <div className="min-h-screen bg-terminal-bg p-4 text-terminal-text">
      <div className="mx-auto max-w-7xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="ot-type-panel-title text-terminal-accent">Research Lab</div>
            <h1 className="text-xl font-semibold">Strategy Validation Workflow</h1>
          </div>
          <div className="flex items-center gap-2">
            <select className="rounded border border-terminal-border bg-terminal-panel px-2 py-1 text-xs" value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
              {STRATEGIES.map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
            <button type="button" className="rounded border border-terminal-accent bg-terminal-accent/10 px-3 py-1 text-xs text-terminal-accent" onClick={run} disabled={loading}>
              {loading ? "Running..." : "Run Workflow"}
            </button>
          </div>
        </div>

        <div className="rounded border border-terminal-warn/60 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
          Research qualification does not activate trading.
        </div>
        {error ? <div className="rounded border border-terminal-neg/60 bg-terminal-neg/10 px-3 py-2 text-xs text-terminal-neg">{error}</div> : null}

        <div className="grid gap-3 lg:grid-cols-4">
          <TerminalPanel title="Backtest" subtitle="Conservative next-bar simulation">
            <div className="space-y-2 text-xs">
              <div className="flex justify-between"><span>Total Return</span><span>{metrics ? `${(metrics.total_return * 100).toFixed(2)}%` : "-"}</span></div>
              <div className="flex justify-between"><span>Sharpe</span><span>{metrics?.sharpe_ratio?.toFixed(2) ?? "-"}</span></div>
              <div className="flex justify-between"><span>Max Drawdown</span><span>{metrics ? `${(metrics.maximum_drawdown * 100).toFixed(2)}%` : "-"}</span></div>
              <div className="flex justify-between"><span>Trades</span><span>{metrics?.trade_count ?? "-"}</span></div>
            </div>
          </TerminalPanel>
          <TerminalPanel title="Optimization" subtitle="All trials retained">
            <div className="space-y-1 text-xs">
              <div>Trials: {result?.optimization.trials.length ?? "-"}</div>
              {result?.optimization.trials.slice(0, 4).map((trial) => (
                <div key={trial.trial_id} className="flex justify-between gap-2 border-t border-terminal-border/60 pt-1">
                  <span>{JSON.stringify(trial.parameter_set)}</span>
                  <span>{trial.objective_value?.toFixed(2) ?? "n/a"}</span>
                </div>
              ))}
            </div>
          </TerminalPanel>
          <TerminalPanel title="Validation" subtitle="Walk-forward + robustness">
            <div className="space-y-1 text-xs">
              <div>Folds: {result?.validation.folds.length ?? "-"}</div>
              <div>Robustness: {result ? `${result.validation.robustness.filter((row) => row.passed).length}/${result.validation.robustness.length}` : "-"}</div>
              {result?.validation.folds.map((fold) => (
                <div key={fold.fold_id} className="flex justify-between border-t border-terminal-border/60 pt-1">
                  <span>{fold.fold_id.slice(0, 12)}</span>
                  <span>{fold.validation_metrics ? `${(fold.validation_metrics.total_return * 100).toFixed(2)}%` : "n/a"}</span>
                </div>
              ))}
            </div>
          </TerminalPanel>
          <TerminalPanel title="Scorecard" subtitle="Auditable gates">
            <div className="space-y-1 text-xs">
              <div className="flex justify-between"><span>Score</span><span>{result ? result.scorecard.total_score.toFixed(2) : "-"}</span></div>
              <div className="flex justify-between"><span>Status</span><span>{result?.candidate.promotion_status ?? "-"}</span></div>
              {result ? (
                <div className="flex flex-wrap gap-2 border-t border-terminal-border/60 pt-2">
                  <button
                    type="button"
                    className="rounded border border-terminal-accent/70 px-2 py-1 text-[11px] text-terminal-accent"
                    onClick={() => void explainEntity("research", result.scorecard.scorecard_id, "scorecard")}
                  >
                    AI Explain Scorecard
                  </button>
                  <button
                    type="button"
                    className="rounded border border-terminal-accent/70 px-2 py-1 text-[11px] text-terminal-accent"
                    onClick={() => void explainEntity("research", result.candidate.candidate_id, "candidate")}
                  >
                    AI Explain Candidate
                  </button>
                </div>
              ) : null}
              {result ? Object.entries(result.scorecard.gates).map(([gate, passed]) => (
                <div key={gate} className="flex justify-between border-t border-terminal-border/60 pt-1">
                  <span>{gate}</span>
                  <span className={passed ? "text-terminal-accent" : "text-terminal-neg"}>{passed ? "PASS" : "FAIL"}</span>
                </div>
              )) : null}
            </div>
          </TerminalPanel>
        </div>

        {result ? (
          <TerminalPanel title="Lineage" subtitle="Reproducibility manifest">
            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              <button
                type="button"
                className="rounded border border-terminal-accent/70 px-2 py-1 text-[11px] text-terminal-accent"
                onClick={() => void explainEntity("research", result.experiment.experiment_id, "research run")}
              >
                AI Explain Research Run
              </button>
              <button
                type="button"
                className="rounded border border-terminal-accent/70 px-2 py-1 text-[11px] text-terminal-accent"
                onClick={() => void explainEntity("strategy", strategyId, "strategy")}
              >
                AI Explain Strategy
              </button>
            </div>
            <pre className="max-h-80 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(result.manifest, null, 2)}</pre>
          </TerminalPanel>
        ) : null}
      </div>
    </div>
  );
}
