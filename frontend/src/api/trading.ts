import { api } from "./base";

export type PaperAccount = {
  account_id: string;
  name: string;
  base_currency: string;
  status: string;
  equity: string;
  buying_power: string;
  cash_balance: string;
};

export type StrategyDeployment = {
  deployment_id: string;
  account_id: string;
  candidate_id: string;
  strategy_id: string;
  strategy_version: string;
  status: string;
  stale: boolean;
  stale_reasons: string[];
};

export type CanonicalPaperOrder = {
  order_id: string;
  account_id: string;
  deployment_id: string;
  proposal_id: string;
  status: string;
  quantity: string;
  filled_quantity: string;
  average_fill_price?: string | null;
};

export type RiskEvaluation = {
  evaluation_id: string;
  decision: string;
  approved_quantity: string;
  approved_notional: string;
  blocking_reasons: string[];
  resizing_reasons: string[];
  rules_evaluated: Array<{ rule_id: string; status: string; message: string }>;
};

export async function createCanonicalPaperAccount(payload = { name: "SYNTHETIC PAPER SCALE TEST", initial_cash: "1000000", base_currency: "USD" }) {
  const { data } = await api.post<PaperAccount>("/trading/paper/accounts", payload);
  return data;
}

export async function createCanonicalDeployment(accountId: string) {
  const { data } = await api.post<StrategyDeployment>("/trading/paper/deployments", {
    account_id: accountId,
    candidate_id: "candidate_phase8_demo",
    strategy_id: "smc_breakout_v1",
    strategy_version: "1.0.0",
    instrument: {
      instrument_id: "NSE:RELIANCE",
      symbol: "RELIANCE",
      asset_class: "EQUITY",
      quote_currency: "USD",
      minimum_tick: "0.01",
      lot_size: "1",
      contract_multiplier: "1",
      shortable: false,
      market_open: true,
    },
    selected_parameters: { source: "phase8-ui" },
  });
  return data;
}

export async function approveCanonicalDeployment(deploymentId: string) {
  const { data } = await api.post<StrategyDeployment>(`/trading/paper/deployments/${deploymentId}/approve`, {
    approver: "human",
    notes: "UI verification approval for internal paper trading only",
    strategy_hash: "strategy_hash_phase8_demo",
    candidate_hash: "candidate_hash_phase8_demo",
  });
  return data;
}

export async function submitCanonicalIntent(accountId: string, deploymentId: string) {
  const payload = {
    intent: {
      account_id: accountId,
      strategy_id: "smc_breakout_v1",
      strategy_version: "1.0.0",
      deployment_id: deploymentId,
      proposal_id: "proposal_phase8_demo",
      instrument: {
        instrument_id: "NSE:RELIANCE",
        symbol: "RELIANCE",
        asset_class: "EQUITY",
        quote_currency: "USD",
        minimum_tick: "0.01",
        lot_size: "1",
        contract_multiplier: "1",
        shortable: false,
        market_open: true,
      },
      side: "BUY",
      order_type: "MARKET",
      requested_quantity: "10",
      sizing_intent: { type: "fixed_units", quantity: "10" },
      reference_price: "100",
      invalidation_price: "95",
      time_in_force: "DAY",
      idempotency_key: "phase8-demo-intent",
    },
    market: {
      price: "100",
      provider: "ui-fixture",
      quality_score: "1",
      is_stale: false,
      is_simulated: true,
      conversion_rate: "1",
    },
  };
  const { data } = await api.post<{ risk_evaluation: RiskEvaluation; order: CanonicalPaperOrder | null }>("/trading/paper/intents", payload);
  return data;
}

export async function simulateCanonicalOrder(orderId: string) {
  const { data } = await api.post<{ order: CanonicalPaperOrder; fill: unknown }>(`/trading/paper/orders/${orderId}/simulate`, {
    market: {
      price: "101",
      provider: "ui-fixture",
      quality_score: "1",
      is_stale: false,
      is_simulated: true,
      conversion_rate: "1",
    },
  });
  return data;
}

export async function setCanonicalEmergencyDisable(accountId: string, enabled: boolean) {
  const { data } = await api.post(`/trading/paper/accounts/${accountId}/emergency-disable`, {
    enabled,
    reason: enabled ? "manual emergency disable" : "manual re-enable",
    updated_by: "human",
  });
  return data;
}
