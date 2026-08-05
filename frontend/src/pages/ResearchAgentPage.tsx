import { useEffect, useMemo, useState } from "react";

import {
  fetchAITradingDecisions,
  fetchAITradingStatus,
  fetchAIInstitutionalStatus,
  emergencyDisableAITrading,
  runAIShadowAnalysis,
  type AIShadowDecision,
  type AITradingStatus,
} from "../api/aiTrading";
import {
  activateResearchAgentPolicy,
  approveResearchAgentPlan,
  cancelResearchAgentPlan,
  createResearchAgentPlan,
  createResearchAgentPolicy,
  fetchAIProviderBudgets,
  fetchAIProviderHealth,
  fetchResearchAgentLineage,
  fetchResearchAgentPlans,
  fetchResearchAgentPolicies,
  fetchResearchAgentReports,
  fetchResearchAgentStatus,
  generateResearchHypotheses,
  pauseResearchAgentPlan,
  rejectResearchAgentPlan,
  runAIProviderHealthCheck,
  startResearchAgentPlan,
  type ResearchAgentStatus,
  type ResearchPlan,
  type ResearchPolicy,
} from "../api/researchAgent";
import {
  fetchPropFirmLeaderboard,
  fetchPropFirmProfiles,
  fetchPropFirmSimulations,
  runPropFirmSimulation,
  type PropFirmProfile,
  type PropFirmSimulation,
} from "../api/propFirms";
import { TerminalPanel } from "../components/terminal/TerminalPanel";

export function ResearchAgentPage() {
  const [status, setStatus] = useState<ResearchAgentStatus | null>(null);
  const [policies, setPolicies] = useState<ResearchPolicy[]>([]);
  const [plans, setPlans] = useState<ResearchPlan[]>([]);
  const [providerHealth, setProviderHealth] = useState<Array<Record<string, unknown>>>([]);
  const [aiTradingStatus, setAITradingStatus] = useState<AITradingStatus | null>(null);
  const [latestShadowDecision, setLatestShadowDecision] = useState<AIShadowDecision | Record<string, unknown> | null>(null);
  const [institutionalStatus, setInstitutionalStatus] = useState<Record<string, unknown> | null>(null);
  const [budgets, setBudgets] = useState<Record<string, unknown> | null>(null);
  const [objective, setObjective] = useState("Validate baseline strategy robustness for TEST");
  const [hypotheses, setHypotheses] = useState<Array<Record<string, unknown>>>([]);
  const [reports, setReports] = useState<Array<Record<string, unknown>>>([]);
  const [lineage, setLineage] = useState<Record<string, unknown> | null>(null);
  const [propProfiles, setPropProfiles] = useState<PropFirmProfile[]>([]);
  const [propProfileId, setPropProfileId] = useState("FTMO_2_STEP_PHASE_1");
  const [propRisk, setPropRisk] = useState("0.25");
  const [propTradeCap, setPropTradeCap] = useState("1");
  const [propExecution, setPropExecution] = useState("NORMAL");
  const [propSimulation, setPropSimulation] = useState<PropFirmSimulation | null>(null);
  const [propLeaderboard, setPropLeaderboard] = useState<Array<Record<string, unknown>>>([]);
  const [message, setMessage] = useState("Ready");

  async function load() {
    const [s, p, pl, h, b, ats, institutional, decisions, propProfilesResp, propSims, propLeaders] = await Promise.all([
      fetchResearchAgentStatus(),
      fetchResearchAgentPolicies(),
      fetchResearchAgentPlans(),
      fetchAIProviderHealth(),
      fetchAIProviderBudgets(),
      fetchAITradingStatus(),
      fetchAIInstitutionalStatus(),
      fetchAITradingDecisions(),
      fetchPropFirmProfiles(),
      fetchPropFirmSimulations(),
      fetchPropFirmLeaderboard(),
    ]);
    setStatus(s);
    setPolicies(p);
    setPlans(pl);
    setProviderHealth(h);
    setBudgets(b);
    setAITradingStatus(ats);
    setInstitutionalStatus(institutional);
    setLatestShadowDecision(decisions[0] ?? null);
    setPropProfiles(propProfilesResp);
    setPropSimulation(propSims[0] ?? null);
    setPropLeaderboard(propLeaders);
  }

  useEffect(() => {
    void load();
  }, []);

  const activePlan = useMemo(() => plans[0] ?? null, [plans]);
  const latestDecisionDisplay = useMemo(() => {
    const row = latestShadowDecision as Record<string, unknown> | null;
    const nested = (row?.decision && typeof row.decision === "object" ? row.decision : null) as Record<string, unknown> | null;
    const raw = (row?.raw_decision && typeof row.raw_decision === "object" ? row.raw_decision : null) as Record<string, unknown> | null;
    const consensus = (raw?.strategy_consensus && typeof raw.strategy_consensus === "object" ? raw.strategy_consensus : null) as Record<string, unknown> | null;
    const marketContext = (raw?.market_context && typeof raw.market_context === "object" ? raw.market_context : null) as Record<string, unknown> | null;
    const market = (marketContext?.market && typeof marketContext.market === "object" ? marketContext.market : null) as Record<string, unknown> | null;
    return {
      symbol: String(row?.symbol ?? "-"),
      timeframe: String(row?.timeframe ?? ""),
      decision: String(nested?.decision ?? row?.decision ?? "-"),
      riskStatus: String(row?.risk_status ?? "-"),
      confidence: String(nested?.confidence ?? row?.confidence ?? "-"),
      entry: String(nested?.proposed_entry ?? row?.proposed_entry ?? "-"),
      stopLoss: String(nested?.stop_loss ?? row?.stop_loss ?? "-"),
      takeProfit: String(nested?.take_profit ?? row?.take_profit ?? "-"),
      dataTimestamp: String(nested?.data_timestamp ?? row?.data_timestamp ?? "-"),
      decisionTimestamp: String(nested?.decision_timestamp ?? row?.decision_timestamp ?? "-"),
      reasoningSummary: String(nested?.reasoning_summary ?? row?.reasoning_summary ?? ""),
      regime: String(marketContext?.market_regime ?? row?.market_regime ?? "-"),
      session: String(market?.session ?? "-"),
      agreement: String(consensus?.agreement_score ?? "-"),
      bullScore: String(consensus?.bull_score ?? "-"),
      bearScore: String(consensus?.bear_score ?? "-"),
      recommendedDirection: String(consensus?.recommended_direction ?? "-"),
    };
  }, [latestShadowDecision]);
  const institutionalDisplay = useMemo(() => {
    const memory = (institutionalStatus?.trade_memory as Array<Record<string, unknown>> | undefined) ?? [];
    const performance = (institutionalStatus?.performance && typeof institutionalStatus.performance === "object" ? institutionalStatus.performance : {}) as Record<string, unknown>;
    const reviews = (institutionalStatus?.recent_reviews as Array<Record<string, unknown>> | undefined) ?? [];
    return { memory, performance, reviews };
  }, [institutionalStatus]);

  async function runAction(action: () => Promise<unknown>, label: string) {
    try {
      await action();
      await load();
      setMessage(label);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Action failed");
    }
  }

  async function runPropLab() {
    const result = await runPropFirmSimulation({
      profile_ids: [propProfileId],
      account_size: 100000,
      risk_per_trade_percents: [Number(propRisk)],
      trade_frequency_caps: [Number(propTradeCap)],
      execution_scenarios: [propExecution],
      monte_carlo_runs: 1000,
    });
    setPropSimulation(result);
    setPropLeaderboard(await fetchPropFirmLeaderboard());
  }

  return (
    <div className="min-h-screen bg-terminal-bg p-4 text-terminal-text">
      <div className="mx-auto max-w-7xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="ot-type-panel-title text-terminal-accent">Research Agent</div>
            <h1 className="text-xl font-semibold">Autonomous Research Operations</h1>
          </div>
          <div className="text-xs text-terminal-muted">{message}</div>
        </div>

        <div className="rounded border border-terminal-warn/60 bg-terminal-warn/10 px-3 py-2 text-xs text-terminal-warn">
          Research-only system. Candidate promotion, deployment activation, risk approval, paper orders, broker actions, and portfolio mutation remain forbidden.
        </div>

        <div className="grid gap-3 lg:grid-cols-4">
          <TerminalPanel title="Agent Status" subtitle="Mode and kill switches">
            <div className="space-y-1 text-xs">
              <div>Health: {status?.health ?? "-"}</div>
              <div>Mode: {status?.mode ?? "-"}</div>
              <div>Autonomous default: {String(status?.autonomous_default ?? false)}</div>
              <div>Kill switches: {status?.kill_switches?.length ?? 0}</div>
              <div>Scheduler queued: {String(status?.scheduler?.queued ?? "-")}</div>
            </div>
          </TerminalPanel>
          <TerminalPanel title="Provider Health" subtitle="No secrets displayed">
            <div className="space-y-1 text-xs">
              {providerHealth.map((row) => (
                <div key={String(row.provider)} className="flex items-center justify-between gap-2 rounded border border-terminal-border bg-terminal-bg px-2 py-1">
                  <span>{String(row.provider)} | {String(row.state)} | configured {String(row.configured)}</span>
                  <button className="rounded border border-terminal-border px-2 py-0.5 text-terminal-muted" onClick={() => void runAction(() => runAIProviderHealthCheck(String(row.provider)), "Provider health check completed")}>
                    Check
                  </button>
                </div>
              ))}
            </div>
          </TerminalPanel>
          <TerminalPanel title="Budget" subtitle="Provider and research limits">
            <pre className="max-h-36 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(budgets, null, 2)}</pre>
          </TerminalPanel>
          <TerminalPanel title="Policy" subtitle="Human controlled">
            <div className="space-y-2 text-xs">
              <div>Policies: {policies.length}</div>
              <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void runAction(() => createResearchAgentPolicy({ workspace_id: "default" }), "Policy created")}>
                Create Policy
              </button>
              {policies[0] ? (
                <button className="ml-2 rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void runAction(() => activateResearchAgentPolicy(policies[0].policy_id), "Policy activated")}>
                  Activate
                </button>
              ) : null}
            </div>
          </TerminalPanel>
        </div>

        <TerminalPanel title="AI Trading" subtitle="Guarded shadow and limited paper automation">
          <div className="grid gap-3 text-xs lg:grid-cols-[16rem_1fr_auto]">
            <div className="space-y-1">
              <div>Provider: {aiTradingStatus?.provider_configured ? "openai" : "-"}</div>
              <div>Model: {aiTradingStatus?.model ?? "-"}</div>
              <div>Mode: {aiTradingStatus?.ai_mode ?? "-"}</div>
              <div>Live trading: {aiTradingStatus?.live_trading_enabled ? "ENABLED" : "DISABLED"}</div>
              <div>Automatic submission: {aiTradingStatus?.order_submission_enabled ? "ENABLED" : "DISABLED"}</div>
              <div>Scheduler: {String(aiTradingStatus?.scheduler?.["enabled"] ?? false)} / owner {String(aiTradingStatus?.scheduler?.["owner"] ?? "-")}</div>
              <div>Profile: {String(aiTradingStatus?.scheduler?.["active_profile"] ?? "-")}</div>
              <div>Daily entries: {String(aiTradingStatus?.scheduler?.["entries_used"] ?? 0)} / {String(aiTradingStatus?.scheduler?.["daily_entry_cap"] ?? "-")}</div>
              <div>Daily lock: {String(aiTradingStatus?.scheduler?.["daily_lock_active"] ?? false)} {String(aiTradingStatus?.scheduler?.["daily_lock_reason"] ?? "")}</div>
              <div>Cooldown: {String(aiTradingStatus?.scheduler?.["cooldown_active"] ?? false)} until {String(aiTradingStatus?.scheduler?.["cooldown_expiry"] ?? "-")}</div>
              <div>Daily P&L: {String(aiTradingStatus?.scheduler?.["daily_net_pnl"] ?? "0")} | DD {String(aiTradingStatus?.scheduler?.["daily_drawdown"] ?? "0")}</div>
              <div>Consecutive losses: {String(aiTradingStatus?.scheduler?.["consecutive_losses"] ?? 0)}</div>
              <div>OpenAI today: {String(aiTradingStatus?.usage?.["requests_today"] ?? 0)} calls</div>
              <div>Cost today: ${String(aiTradingStatus?.usage?.["estimated_cost_today"] ?? "0")}</div>
              <div>Next analysis: {String(aiTradingStatus?.usage?.["next_eligible_analysis_timestamp"] ?? "-")}</div>
              <div>Emergency: {aiTradingStatus?.emergency_disabled ? "DISABLED" : "READY"}</div>
            </div>
            <div className="rounded border border-terminal-border bg-terminal-bg p-2">
              <div className="mb-1 text-terminal-accent">Latest Decision</div>
              <div>{latestDecisionDisplay.symbol} {latestDecisionDisplay.timeframe} | {latestDecisionDisplay.decision} | {latestDecisionDisplay.riskStatus}</div>
              <div>Confidence: {latestDecisionDisplay.confidence}</div>
              <div>Entry: {latestDecisionDisplay.entry} | SL {latestDecisionDisplay.stopLoss} | TP {latestDecisionDisplay.takeProfit}</div>
              <div>Data: {latestDecisionDisplay.dataTimestamp}</div>
              <div>Decision: {latestDecisionDisplay.decisionTimestamp}</div>
              <div className="mt-1 text-terminal-muted">{latestDecisionDisplay.reasoningSummary}</div>
            </div>
            <button
              className="h-9 rounded border border-terminal-accent px-3 text-terminal-accent"
              onClick={() => void runAction(async () => setLatestShadowDecision(await runAIShadowAnalysis("EURUSD", "15m")), "Shadow analysis completed")}
            >
              Run Shadow Analysis
            </button>
            <button
              className="h-9 rounded border border-terminal-danger px-3 text-terminal-danger"
              onClick={() => void runAction(async () => { await emergencyDisableAITrading(); await load(); }, "AI trading emergency disabled")}
            >
              Emergency Disable
            </button>
          </div>
        </TerminalPanel>

        <div className="grid gap-3 lg:grid-cols-3">
          <TerminalPanel title="Market Regime" subtitle="Latest institutional context">
            <div className="space-y-1 text-xs">
              <div>Regime: {latestDecisionDisplay.regime}</div>
              <div>Session: {latestDecisionDisplay.session}</div>
              <div>Direction: {latestDecisionDisplay.recommendedDirection}</div>
              <div>Consensus: {latestDecisionDisplay.agreement}</div>
            </div>
          </TerminalPanel>
          <TerminalPanel title="Strategy Consensus" subtitle="Bull and bear score">
            <div className="space-y-1 text-xs">
              <div>Bull score: {latestDecisionDisplay.bullScore}</div>
              <div>Bear score: {latestDecisionDisplay.bearScore}</div>
              <div>OpenAI today: {String(aiTradingStatus?.usage?.["requests_today"] ?? 0)}</div>
              <div>Cost today: ${String(aiTradingStatus?.usage?.["estimated_cost_today"] ?? "0")}</div>
            </div>
          </TerminalPanel>
          <TerminalPanel title="Multi-Timeframe Bias" subtitle="Macro and execution context">
            <pre className="max-h-36 overflow-auto text-[11px] text-terminal-muted">
              {JSON.stringify(((latestShadowDecision as Record<string, unknown> | null)?.raw_decision as Record<string, unknown> | undefined)?.multi_timeframe_context ?? {}, null, 2)}
            </pre>
          </TerminalPanel>
        </div>

        <div className="grid gap-3 lg:grid-cols-3">
          <TerminalPanel title="Trade Memory" subtitle="Accepted AI paper/shadow records">
            <pre className="max-h-48 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(institutionalDisplay.memory.slice(0, 3), null, 2)}</pre>
          </TerminalPanel>
          <TerminalPanel title="Performance" subtitle="Institutional metrics">
            <pre className="max-h-48 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(institutionalDisplay.performance, null, 2)}</pre>
          </TerminalPanel>
          <TerminalPanel title="Recent AI Reviews" subtitle="Post-trade lessons">
            <pre className="max-h-48 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(institutionalDisplay.reviews.slice(0, 3), null, 2)}</pre>
          </TerminalPanel>
        </div>

        <TerminalPanel title="Prop-Firm Laboratory" subtitle="RESEARCH SIMULATION - NOT A GUARANTEE">
          <div className="grid gap-3 text-xs lg:grid-cols-[18rem_1fr]">
            <div className="space-y-2">
              <select className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={propProfileId} onChange={(event) => setPropProfileId(event.target.value)}>
                {propProfiles.map((profile) => (
                  <option key={profile.profile_id} value={profile.profile_id}>
                    {profile.provider_name} {profile.phase}
                  </option>
                ))}
              </select>
              <select className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={propRisk} onChange={(event) => setPropRisk(event.target.value)}>
                {["0.10", "0.20", "0.25", "0.30", "0.40", "0.50"].map((risk) => <option key={risk} value={risk}>{risk}% risk</option>)}
              </select>
              <select className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={propTradeCap} onChange={(event) => setPropTradeCap(event.target.value)}>
                <option value="1">1 trade per day</option>
                <option value="2">2 trades per day</option>
              </select>
              <select className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={propExecution} onChange={(event) => setPropExecution(event.target.value)}>
                <option value="NORMAL">NORMAL execution</option>
                <option value="STRESSED">STRESSED execution</option>
                <option value="SEVERE">SEVERE execution</option>
              </select>
              <button className="w-full rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void runAction(runPropLab, "Prop-firm simulation completed")}>
                Run Read-Only Simulation
              </button>
            </div>
            <div className="grid gap-3 lg:grid-cols-3">
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Pass probability</div>
                <div className="text-lg">{String((((propSimulation?.report?.profiles as Array<Record<string, unknown>> | undefined)?.[0]?.summary as Record<string, unknown> | undefined)?.pass_rate ?? "-"))}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Breach probability</div>
                <div className="text-lg">{String((((propSimulation?.report?.profiles as Array<Record<string, unknown>> | undefined)?.[0]?.summary as Record<string, unknown> | undefined)?.breach_rate ?? "-"))}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Sample</div>
                <div className="text-lg">{String(propSimulation?.status ?? "NOT_RUN")}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Median days</div>
                <div>{String((((propSimulation?.report?.profiles as Array<Record<string, unknown>> | undefined)?.[0]?.summary as Record<string, unknown> | undefined)?.median_days_to_pass ?? "-"))}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Median trades</div>
                <div>{String((((propSimulation?.report?.profiles as Array<Record<string, unknown>> | undefined)?.[0]?.summary as Record<string, unknown> | undefined)?.median_trades_to_pass ?? "-"))}</div>
              </div>
              <div className="rounded border border-terminal-border bg-terminal-bg p-2">
                <div className="text-terminal-accent">Max drawdown</div>
                <div>{String((((propSimulation?.report?.profiles as Array<Record<string, unknown>> | undefined)?.[0]?.summary as Record<string, unknown> | undefined)?.maximum_drawdown_before_passing ?? "-"))}</div>
              </div>
            </div>
          </div>
          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <pre className="max-h-56 overflow-auto rounded border border-terminal-border bg-terminal-bg p-2 text-[11px] text-terminal-muted">{JSON.stringify(propSimulation?.report?.risk_analysis ?? {}, null, 2)}</pre>
            <pre className="max-h-56 overflow-auto rounded border border-terminal-border bg-terminal-bg p-2 text-[11px] text-terminal-muted">{JSON.stringify(propLeaderboard, null, 2)}</pre>
          </div>
        </TerminalPanel>

        <TerminalPanel title="Plan Builder" subtitle="Objective, hypotheses, task graph, approvals">
          <div className="grid gap-2 text-xs md:grid-cols-[1fr_auto_auto]">
            <input className="rounded border border-terminal-border bg-terminal-bg px-2 py-1" value={objective} onChange={(event) => setObjective(event.target.value)} />
            <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void runAction(async () => setHypotheses(await generateResearchHypotheses({ objective, instrument: "TEST" })), "Hypotheses generated")}>
              Generate Hypotheses
            </button>
            <button className="rounded border border-terminal-accent px-2 py-1 text-terminal-accent" onClick={() => void runAction(() => createResearchAgentPlan({ objective, template: "baseline_validation", hypotheses }), "Plan created")}>
              Create Plan
            </button>
          </div>
          <div className="mt-2 grid gap-2 lg:grid-cols-2">
            <pre className="max-h-48 overflow-auto rounded border border-terminal-border bg-terminal-bg p-2 text-[11px] text-terminal-muted">{JSON.stringify(hypotheses, null, 2)}</pre>
            <div className="space-y-1 text-xs">
              {plans.map((plan) => (
                <div key={plan.plan_id} className="rounded border border-terminal-border bg-terminal-bg p-2">
                  <div className="font-semibold text-terminal-accent">{plan.title}</div>
                  <div>{plan.status} | approval {plan.approval_state} | tasks {plan.tasks.length}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(() => approveResearchAgentPlan(plan.plan_id), "Plan approved")}>Approve</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(() => rejectResearchAgentPlan(plan.plan_id), "Plan rejected")}>Reject</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(() => startResearchAgentPlan(plan.plan_id), "Plan started")}>Start</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(() => pauseResearchAgentPlan(plan.plan_id), "Plan paused")}>Pause</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(() => cancelResearchAgentPlan(plan.plan_id), "Plan cancelled")}>Cancel</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(async () => setReports(await fetchResearchAgentReports(plan.plan_id)), "Reports loaded")}>Reports</button>
                    <button className="rounded border border-terminal-border px-2 py-1 text-terminal-muted" onClick={() => void runAction(async () => setLineage(await fetchResearchAgentLineage(plan.plan_id)), "Lineage loaded")}>Lineage</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </TerminalPanel>

        <div className="grid gap-3 lg:grid-cols-2">
          <TerminalPanel title="Lineage Viewer" subtitle="Objective to reports">
            <pre className="max-h-80 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(lineage ?? activePlan?.dependencies ?? {}, null, 2)}</pre>
          </TerminalPanel>
          <TerminalPanel title="Reports and Recommendations" subtitle="Evidence-grounded outputs">
            <pre className="max-h-80 overflow-auto text-[11px] text-terminal-muted">{JSON.stringify(reports, null, 2)}</pre>
          </TerminalPanel>
        </div>
      </div>
    </div>
  );
}
