import { useEffect, useState } from "react";

import {
  connectBroker,
  disconnectBroker,
  fetchBrokerCapabilities,
  fetchBrokerHealth,
  fetchIbkrAccounts,
  fetchIbkrExecutions,
  fetchIbkrHistoricalBars,
  fetchIbkrOrders,
  fetchIbkrPositions,
  fetchIbkrQuote,
  fetchIbkrSnapshot,
  reconcileIbkrAccount,
  resolveIbkrContract,
} from "../api/brokers";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

export function BrokerOperationsPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [capabilities, setCapabilities] = useState<Record<string, unknown> | null>(null);
  const [accounts, setAccounts] = useState<Array<Record<string, unknown>>>([]);
  const [selectedAccount, setSelectedAccount] = useState("");
  const [snapshot, setSnapshot] = useState<Record<string, unknown> | null>(null);
  const [positions, setPositions] = useState<Array<Record<string, unknown>>>([]);
  const [orders, setOrders] = useState<Array<Record<string, unknown>>>([]);
  const [executions, setExecutions] = useState<Array<Record<string, unknown>>>([]);
  const [contract, setContract] = useState<Record<string, unknown> | null>(null);
  const [quote, setQuote] = useState<Record<string, unknown> | null>(null);
  const [bars, setBars] = useState<Array<Record<string, unknown>>>([]);
  const [reconciliation, setReconciliation] = useState<Record<string, unknown> | null>(null);
  const [instrument, setInstrument] = useState("FX:EURUSD");
  const [message, setMessage] = useState("Ready");

  async function load() {
    const [h, c, a] = await Promise.all([fetchBrokerHealth(), fetchBrokerCapabilities(), fetchIbkrAccounts()]);
    setHealth(h);
    setCapabilities(c);
    setAccounts(a);
    const accountId = selectedAccount || String(a[0]?.account_id || "");
    setSelectedAccount(accountId);
    if (accountId) {
      const [s, p, o, e] = await Promise.all([fetchIbkrSnapshot(accountId), fetchIbkrPositions(accountId), fetchIbkrOrders(accountId), fetchIbkrExecutions(accountId)]);
      setSnapshot(s);
      setPositions(p);
      setOrders(o);
      setExecutions(e);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function action(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
      setMessage(label);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Action failed");
    }
  }

  return (
    <div className="min-h-screen bg-terminal-bg p-4 text-terminal-text">
      <div className="mx-auto max-w-7xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="ot-type-panel-title text-terminal-accent">Broker Operations</div>
            <h1 className="text-xl font-semibold">IBKR PAPER ACCOUNT</h1>
          </div>
          <div className="text-xs text-terminal-muted">{message}</div>
        </div>

        <div className="rounded border border-terminal-warn/60 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
          Live trading is disabled. Broker mutations require canonical OMS orders, deterministic risk approval, allow-listed paper accounts, and user confirmation.
        </div>

        <div className="grid gap-3 lg:grid-cols-3">
          <TerminalPanel title="Connection" subtitle="TWS / IB Gateway paper">
            <div className="space-y-2 text-xs">
              <div>State: {String(health?.connection_state ?? "-")}</div>
              <div>Environment: {String(health?.environment ?? "-")}</div>
              <div>Paper verification: {String(health?.account_verification_status ?? "-")}</div>
              <div>Order submission: {String(health?.order_submission_status ?? "-")}</div>
              <div>Client ID: {String(health?.client_id ?? "-")}</div>
              <div>Market data: DELAYED</div>
              <div className="flex gap-2">
                <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void action("Broker connected", () => connectBroker())}>Connect</button>
                <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void action("Broker disconnected", () => disconnectBroker())}>Disconnect</button>
              </div>
            </div>
          </TerminalPanel>

          <TerminalPanel title="Paper Account" subtitle="No live styling for unverified accounts">
            <div className="space-y-2 text-xs">
              <select className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={selectedAccount} onChange={(event) => setSelectedAccount(event.target.value)}>
                {accounts.map((account) => <option key={String(account.account_id)} value={String(account.account_id)}>{String(account.alias)} | paper {String(account.paper_verified)}</option>)}
              </select>
              <pre className="max-h-48 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(snapshot, null, 2)}</pre>
            </div>
          </TerminalPanel>

          <TerminalPanel title="Capabilities" subtitle="Unsupported behavior fails explicitly">
            <pre className="max-h-60 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(capabilities, null, 2)}</pre>
          </TerminalPanel>
        </div>

        <TerminalPanel title="Contract and Market Data" subtitle="Mode and quality are explicit">
          <div className="grid gap-2 text-xs md:grid-cols-[1fr_auto_auto_auto]">
            <input className="rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={instrument} onChange={(event) => setInstrument(event.target.value)} />
            <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void action("Contract resolved", async () => setContract(await resolveIbkrContract(instrument)))}>Resolve</button>
            <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void action("Quote loaded", async () => setQuote(await fetchIbkrQuote(instrument)))}>Quote</button>
            <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void action("Historical bars loaded", async () => setBars(await fetchIbkrHistoricalBars(instrument)))}>Bars</button>
          </div>
          <div className="mt-2 grid gap-2 lg:grid-cols-3">
            <pre className="max-h-48 overflow-auto rounded border border-terminal-border p-2 text-[11px] text-terminal-muted">{JSON.stringify(contract, null, 2)}</pre>
            <pre className="max-h-48 overflow-auto rounded border border-terminal-border p-2 text-[11px] text-terminal-muted">{JSON.stringify(quote, null, 2)}</pre>
            <div className="rounded border border-terminal-border p-2 text-xs">Bars: {bars.length}</div>
          </div>
        </TerminalPanel>

        <div className="grid gap-3 lg:grid-cols-2">
          <TerminalPanel title="Orders and Executions" subtitle="Broker state is read-only here">
            <pre className="max-h-72 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify({ orders, executions }, null, 2)}</pre>
          </TerminalPanel>
          <TerminalPanel title="Reconciliation" subtitle="Inspect mismatches, no unsafe auto-fix">
            <button className="mb-2 rounded border border-terminal-accent px-2 py-1 text-xs text-terminal-accent" disabled={!selectedAccount} onClick={() => void action("Reconciliation complete", async () => setReconciliation(await reconcileIbkrAccount(selectedAccount)))}>
              Reconcile
            </button>
            <pre className="max-h-72 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify({ reconciliation, positions }, null, 2)}</pre>
          </TerminalPanel>
        </div>
      </div>
    </div>
  );
}
