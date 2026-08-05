import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { fetchPhase12Alerts, fetchPhase12OperationsHealth, fetchPhase12PortfolioDashboard, type Phase12PortfolioDashboard } from "../api/phase12";
import { TerminalBadge } from "../components/terminal/TerminalBadge";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

function money(value?: number, currency = "USD") {
  return typeof value === "number" ? value.toLocaleString("en-US", { style: "currency", currency, maximumFractionDigits: 0 }) : "--";
}

function pct(value?: number) {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "--";
}

export function PortfolioOperationsCenter() {
  const [dashboard, setDashboard] = useState<Phase12PortfolioDashboard | null>(null);
  const [health, setHealth] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchPhase12PortfolioDashboard(), fetchPhase12OperationsHealth(), fetchPhase12Alerts()])
      .then(([dash, ops, alertRows]) => {
        if (cancelled) return;
        setDashboard(dash);
        setHealth(ops.components || []);
        setAlerts(alertRows.items || []);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Phase 12 dashboard unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const currency = dashboard?.base_currency || "USD";
  const empty = dashboard?.status === "EMPTY";

  return (
    <div className="space-y-4 px-3 py-3">
      <TerminalPanel
        title="Portfolio Operations"
        subtitle="Paper-only portfolio management, valuation, risk, execution quality, alerts, and reports"
        actions={<TerminalBadge variant={dashboard?.paper_only === false ? "danger" : "live"}>Paper only</TerminalBadge>}
      >
        {error ? <div className="text-sm text-terminal-danger">{error}</div> : null}
        {empty ? <div className="text-sm text-terminal-muted">{dashboard?.message}</div> : null}
        <div className="grid gap-3 md:grid-cols-4">
          {[
            ["Equity", money(dashboard?.total_equity, currency)],
            ["Daily P&L", money(dashboard?.daily_pnl, currency)],
            ["Return", pct(dashboard?.total_return_pct)],
            ["Drawdown", pct(dashboard?.drawdown_pct)],
            ["Gross Exposure", money(dashboard?.gross_exposure, currency)],
            ["Net Exposure", money(dashboard?.net_exposure, currency)],
            ["Cash", money(dashboard?.cash, currency)],
            ["Strategies", String(dashboard?.active_strategies ?? "--")],
          ].map(([label, value]) => (
            <div key={label} className="rounded border border-terminal-border bg-terminal-panel/60 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.16em] text-terminal-muted">{label}</div>
              <div className="mt-1 ot-type-data text-lg text-terminal-text">{value}</div>
            </div>
          ))}
        </div>
      </TerminalPanel>

      <div className="grid gap-4 xl:grid-cols-3">
        <TerminalPanel title="Workflows" subtitle="Detailed Phase 12 views">
          <div className="grid gap-2 text-xs">
            {[
              ["Management", "/equity/portfolio/manage"],
              ["Performance", "/equity/portfolio/performance"],
              ["Risk", "/equity/portfolio/risk"],
              ["Execution Analytics", "/equity/portfolio/execution"],
              ["Strategy Leaderboard", "/equity/portfolio/strategies"],
              ["Replay", "/equity/portfolio/replay"],
              ["Reports", "/equity/portfolio/reports"],
              ["Operations Center", "/equity/operations"],
            ].map(([label, to]) => (
              <Link key={to} to={to} className="rounded border border-terminal-border px-3 py-2 text-terminal-muted hover:text-terminal-text">
                {label}
              </Link>
            ))}
          </div>
        </TerminalPanel>

        <TerminalPanel title="System Health" subtitle="Critical operating components">
          <div className="space-y-2">
            {health.map((row) => (
              <div key={row.component} className="flex items-center justify-between rounded border border-terminal-border px-2 py-1 text-xs">
                <span>{row.component}</span>
                <TerminalBadge variant={row.status === "OK" ? "live" : row.status === "SIMULATED" ? "info" : "warn"}>{row.status}</TerminalBadge>
              </div>
            ))}
          </div>
        </TerminalPanel>

        <TerminalPanel title="Alerts" subtitle="Deduplicated operational alerts">
          <div className="space-y-2">
            {alerts.length === 0 ? <div className="text-xs text-terminal-muted">No active Phase 12 alerts.</div> : null}
            {alerts.slice(0, 8).map((row) => (
              <div key={row.alert_id} className="rounded border border-terminal-border px-2 py-1 text-xs">
                <div className="flex items-center justify-between">
                  <span>{row.message || row.alert_id}</span>
                  <TerminalBadge variant={row.severity === "CRITICAL" ? "danger" : "warn"}>{row.status}</TerminalBadge>
                </div>
              </div>
            ))}
          </div>
        </TerminalPanel>
      </div>
    </div>
  );
}
