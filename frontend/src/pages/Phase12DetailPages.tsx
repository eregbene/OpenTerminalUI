import { useEffect, useMemo, useState } from "react";

import {
  evaluatePhase12Execution,
  fetchPhase12Incidents,
  fetchPhase12OperationsHealth,
  fetchPhase12PortfolioDashboard,
  fetchPhase12PortfolioManagement,
  fetchPhase12Reports,
  fetchPhase12Risk,
  generatePhase12Report,
} from "../api/phase12";
import { TerminalBadge } from "../components/terminal/TerminalBadge";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="max-h-[420px] overflow-auto rounded border border-terminal-border bg-terminal-bg p-3 text-[11px] text-terminal-muted">{JSON.stringify(value, null, 2)}</pre>;
}

export function PortfolioManagementPage() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetchPhase12PortfolioManagement().then(setData).catch((error) => setData({ error: String(error) }));
  }, []);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Portfolio Management" subtitle="Create portfolios, attach paper accounts, manage approved strategy allocations">
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}

export function PortfolioPerformancePage() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetchPhase12PortfolioDashboard()
      .then((dash) => (dash.portfolio_id ? fetchPhase12Risk(dash.portfolio_id).then((risk) => ({ dash, risk })) : { dash }))
      .then(setData)
      .catch((error) => setData({ error: String(error) }));
  }, []);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Performance Analytics" subtitle="Equity curves, return statistics, drawdown, attribution, and data-quality status">
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}

export function PortfolioRiskPage() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetchPhase12PortfolioDashboard()
      .then((dash) => (dash.portfolio_id ? fetchPhase12Risk(dash.portfolio_id) : dash))
      .then(setData)
      .catch((error) => setData({ error: String(error) }));
  }, []);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Portfolio Risk" subtitle="Exposure, leverage, concentration, VaR/CVaR status, stress, and limits">
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}

export function ExecutionAnalyticsPage() {
  const [data, setData] = useState<any>(null);
  const sample = useMemo(
    () => ({
      order: { side: "BUY", quantity: 100, decision_price: 100, timestamp: new Date().toISOString() },
      fills: [{ quantity: 100, price: 100.05, commission: 1 }],
      timestamps: {},
    }),
    [],
  );
  useEffect(() => {
    evaluatePhase12Execution(sample).then(setData).catch((error) => setData({ error: String(error) }));
  }, [sample]);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Execution Analytics" subtitle="Latency, slippage, benchmarks, fill quality, and anomalies">
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}

export function StrategyLeaderboardPage() {
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Strategy Leaderboard" subtitle="Transparent ranking with sample sufficiency and component details">
        <div className="text-sm text-terminal-muted">No ranked Phase 12 strategy samples yet. Rankings remain unavailable until allocations and performance snapshots exist.</div>
      </TerminalPanel>
    </div>
  );
}

export function PortfolioReplayPage() {
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Portfolio Replay" subtitle="Read-only trading-day replay timeline">
        <div className="flex items-center gap-2 text-sm text-terminal-muted">
          <TerminalBadge variant="info">Replay mode</TerminalBadge>
          Replay controls are read-only and do not mutate current trading state.
        </div>
      </TerminalPanel>
    </div>
  );
}

export function OperationsCenterPage() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    Promise.all([fetchPhase12OperationsHealth(), fetchPhase12Incidents()])
      .then(([health, incidents]) => setData({ health, incidents }))
      .catch((error) => setData({ error: String(error) }));
  }, []);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel title="Operations Center" subtitle="Component health, alerts, incidents, broker state, data freshness, and timelines">
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}

export function ReportsCenterPage() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    fetchPhase12Reports().then(setData).catch((error) => setData({ error: String(error) }));
  }, []);
  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel
        title="Reports Center"
        subtitle="Daily, weekly, monthly, execution, risk, and operations reports"
        actions={<button className="rounded border border-terminal-border px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text" onClick={() => generatePhase12Report({ report_type: "DAILY" }).then(setData)}>Generate</button>}
      >
        <JsonBlock value={data || { loading: true }} />
      </TerminalPanel>
    </div>
  );
}
