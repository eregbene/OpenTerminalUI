import { type ReactNode, useEffect, useState } from "react";

import {
  getResearchCalibration,
  getResearchEquity,
  getResearchPerformance,
  getResearchPerformanceAnalysis,
  getStrategyLeaderboard,
  type StrategyPerformanceSnapshot,
} from "../api/research";

export function StrategyPerformanceDashboard() {
  const [leaderboard, setLeaderboard] = useState<StrategyPerformanceSnapshot | null>(null);
  const [performance, setPerformance] = useState<Record<string, any> | null>(null);
  const [equity, setEquity] = useState<Record<string, any> | null>(null);
  const [calibration, setCalibration] = useState<Record<string, any> | null>(null);
  const [analysis, setAnalysis] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([
      getStrategyLeaderboard(),
      getResearchPerformance(),
      getResearchEquity(),
      getResearchCalibration(),
      getResearchPerformanceAnalysis(),
    ]).then(([leaderboardData, performanceData, equityData, calibrationData, analysisData]) => {
      if (!mounted) return;
      setLeaderboard(leaderboardData);
      setPerformance(performanceData);
      setEquity(equityData);
      setCalibration(calibrationData);
      setAnalysis(analysisData);
    });
    return () => {
      mounted = false;
    };
  }, []);

  const items = leaderboard?.items ?? [];
  const metrics = performance?.lifetime ?? {};
  const weights = leaderboard?.adaptive_weights ?? {};
  const regimes = analysis?.best_market_regime ? [analysis.best_market_regime, analysis.worst_regime].filter(Boolean) : [];

  return (
    <div className="min-h-screen bg-terminal-bg p-4 text-terminal-text">
      <div className="mb-4 flex items-center justify-between border-b border-terminal-border pb-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.24em] text-terminal-muted">Research Intelligence</div>
          <h1 className="text-xl font-semibold text-terminal-accent">Strategy Performance Portfolio</h1>
        </div>
        <div className="text-right text-xs text-terminal-muted">
          <div>Adaptive weighting: research only</div>
          <div>{leaderboard?.updated_at ?? "loading"}</div>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="Trades" value={metrics.trades ?? 0} />
        <Metric label="Win Rate" value={`${(((metrics.win_rate ?? 0) as number) * 100).toFixed(1)}%`} />
        <Metric label="PF" value={(metrics.profit_factor ?? 0).toFixed?.(2) ?? "0.00"} />
        <Metric label="Sharpe" value={(metrics.sharpe ?? 0).toFixed?.(2) ?? "0.00"} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <section className="border border-terminal-border bg-terminal-panel p-3">
          <div className="mb-2 text-sm font-semibold text-terminal-accent">Leaderboard</div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-terminal-muted">
                <tr>
                  <th className="py-2">Rank</th>
                  <th>Strategy</th>
                  <th>Health</th>
                  <th>Trades</th>
                  <th>Win</th>
                  <th>PF</th>
                  <th>Expectancy</th>
                  <th>Weight</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.strategy_id} className="border-t border-terminal-border/70">
                    <td className="py-2">{row.rank ?? "-"}</td>
                    <td className="text-terminal-text">{row.name}</td>
                    <td className="text-terminal-accent">{row.health}</td>
                    <td>{row.metrics?.trades ?? 0}</td>
                    <td>{(((row.metrics?.win_rate ?? 0) as number) * 100).toFixed(1)}%</td>
                    <td>{Number(row.metrics?.profit_factor ?? 0).toFixed(2)}</td>
                    <td>{Number(row.metrics?.expectancy ?? 0).toFixed(2)}</td>
                    <td>{Number(weights[row.strategy_id] ?? 1).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid gap-4">
          <Panel title="Calibration">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Metric label="ECE" value={Number(calibration?.expected_calibration_error ?? 0).toFixed(3)} compact />
              <Metric label="Brier" value={Number(calibration?.brier_score ?? 0).toFixed(3)} compact />
            </div>
            <div className="mt-2 text-xs text-terminal-muted">{calibration?.status ?? "loading"}</div>
          </Panel>
          <Panel title="Evidence Gate">
            <div className="text-xs text-terminal-muted">
              {leaderboard?.sample_size_protection?.effect ?? "Waiting for strategy evidence."}
            </div>
          </Panel>
          <Panel title="Equity">
            <div className="text-xs text-terminal-muted">
              Equity points: {(equity?.equity_curve ?? []).length} | Drawdown points: {(equity?.drawdown_curve ?? []).length}
            </div>
          </Panel>
        </section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Regime Analysis">
          {regimes.length ? regimes.map((row: any) => <div key={row.name} className="text-xs">{row.name}: PF {Number(row.metrics?.profit_factor ?? 0).toFixed(2)}</div>) : <div className="text-xs text-terminal-muted">No completed trade regimes yet.</div>}
        </Panel>
        <Panel title="Recommendations">
          {(analysis?.research_recommendations ?? ["Collect more completed paper trades."]).map((item: string) => (
            <div key={item} className="text-xs text-terminal-muted">{item}</div>
          ))}
        </Panel>
      </div>
    </div>
  );
}

function Metric({ label, value, compact = false }: { label: string; value: any; compact?: boolean }) {
  return (
    <div className={`border border-terminal-border bg-terminal-panel ${compact ? "p-2" : "p-3"}`}>
      <div className="text-[10px] uppercase text-terminal-muted">{label}</div>
      <div className="mt-1 text-lg font-semibold text-terminal-accent">{String(value)}</div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-terminal-border bg-terminal-panel p-3">
      <div className="mb-2 text-sm font-semibold text-terminal-accent">{title}</div>
      {children}
    </section>
  );
}

export default StrategyPerformanceDashboard;
