import { useState } from "react";

import { useAgentStore } from "../../agent/agentStore";
import {
  approveCanonicalDeployment,
  createCanonicalDeployment,
  createCanonicalPaperAccount,
  setCanonicalEmergencyDisable,
  simulateCanonicalOrder,
  submitCanonicalIntent,
  type CanonicalPaperOrder,
  type PaperAccount,
  type RiskEvaluation,
  type StrategyDeployment,
} from "../../api/client";

export function CanonicalPaperControls() {
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [deployment, setDeployment] = useState<StrategyDeployment | null>(null);
  const [risk, setRisk] = useState<RiskEvaluation | null>(null);
  const [order, setOrder] = useState<CanonicalPaperOrder | null>(null);
  const [message, setMessage] = useState<string>("Ready");
  const [busy, setBusy] = useState(false);
  const explainEntity = useAgentStore((state) => state.explainEntity);

  async function runWorkflow() {
    setBusy(true);
    try {
      const createdAccount = await createCanonicalPaperAccount();
      const createdDeployment = await createCanonicalDeployment(createdAccount.account_id);
      const approvedDeployment = await approveCanonicalDeployment(createdDeployment.deployment_id);
      const submitted = await submitCanonicalIntent(createdAccount.account_id, approvedDeployment.deployment_id);
      const simulated = submitted.order ? await simulateCanonicalOrder(submitted.order.order_id) : null;
      setAccount(createdAccount);
      setDeployment(approvedDeployment);
      setRisk(submitted.risk_evaluation);
      setOrder(simulated?.order ?? submitted.order);
      setMessage("Canonical paper workflow completed");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workflow failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEmergency(enabled: boolean) {
    if (!account) return;
    setBusy(true);
    try {
      await setCanonicalEmergencyDisable(account.account_id, enabled);
      setMessage(enabled ? "Emergency disable enabled" : "Emergency disable cleared");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Emergency update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-terminal-border bg-terminal-panel p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-terminal-accent">Canonical Paper Controls</div>
          <div className="text-terminal-muted">Research candidate to human-approved internal simulator. No broker connection.</div>
        </div>
        <div className="flex gap-2">
          <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent disabled:opacity-50" disabled={busy} onClick={() => void runWorkflow()}>
            Run Workflow
          </button>
          <button className="rounded border border-terminal-neg px-2 py-1 text-terminal-neg disabled:opacity-50" disabled={busy || !account} onClick={() => void toggleEmergency(true)}>
            Emergency Disable
          </button>
          <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted disabled:opacity-50" disabled={busy || !account} onClick={() => void toggleEmergency(false)}>
            Clear
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div className="rounded border border-terminal-border bg-terminal-bg px-2 py-1">
          Account <span className="text-terminal-accent">{account?.account_id ?? "-"}</span>
          {account ? (
            <button type="button" className="ml-2 rounded border border-terminal-accent/60 px-1.5 py-0.5 text-[10px] text-terminal-accent" onClick={() => void explainEntity("position", account.account_id, "account snapshot")}>
              AI Explain
            </button>
          ) : null}
        </div>
        <div className="rounded border border-terminal-border bg-terminal-bg px-2 py-1">
          Deployment <span className="text-terminal-accent">{deployment?.status ?? "-"}</span>
          {deployment ? (
            <button type="button" className="ml-2 rounded border border-terminal-accent/60 px-1.5 py-0.5 text-[10px] text-terminal-accent" onClick={() => void explainEntity("strategy", deployment.deployment_id, "deployment")}>
              AI Explain
            </button>
          ) : null}
        </div>
        <div className="rounded border border-terminal-border bg-terminal-bg px-2 py-1">
          Risk <span className={risk?.decision?.includes("APPROVED") ? "text-terminal-pos" : "text-terminal-neg"}>{risk?.decision ?? "-"}</span>
          {risk ? (
            <button type="button" className="ml-2 rounded border border-terminal-accent/60 px-1.5 py-0.5 text-[10px] text-terminal-accent" onClick={() => void explainEntity("risk", risk.evaluation_id, "risk evaluation")}>
              AI Explain
            </button>
          ) : null}
        </div>
        <div className="rounded border border-terminal-border bg-terminal-bg px-2 py-1">
          Order <span className="text-terminal-accent">{order?.status ?? "-"}</span>
          {order ? (
            <button type="button" className="ml-2 rounded border border-terminal-accent/60 px-1.5 py-0.5 text-[10px] text-terminal-accent" onClick={() => void explainEntity("order", order.order_id, "paper order")}>
              AI Explain
            </button>
          ) : null}
        </div>
      </div>
      {risk && (
        <div className="mt-2 rounded border border-terminal-border bg-terminal-bg p-2">
          Approved qty {risk.approved_quantity} | notional {risk.approved_notional} | rules {risk.rules_evaluated.length}
          {risk.blocking_reasons.length > 0 && <div className="text-terminal-neg">{risk.blocking_reasons.join("; ")}</div>}
        </div>
      )}
      <div className="mt-2 text-terminal-muted">{message}</div>
    </div>
  );
}
