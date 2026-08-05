import { api } from "./base";

export type ForexCandidate = {
  candidate_id: string;
  strategy_id: string;
  symbol: string;
  timeframe: string;
  direction: "BUY" | "SELL" | "NEUTRAL";
  candidate_entry: number;
  stop_price: number;
  target_prices: number[];
  risk_reward: number;
  confidence: number;
  framework_agreement: number;
  regime: string;
  session: string;
  spread: number;
  data_quality: number;
  expiration: string;
  status: string;
  explanation?: Record<string, unknown>;
  risk_decision?: {
    approved: boolean;
    rejection_reasons: string[];
    position_size: number;
    estimated_risk: number;
    estimated_margin: number;
  } | null;
  oms_order_id?: string | null;
  fill_ids?: string[];
};

export type ForexSignalSummary = {
  active_candidate_count: number;
  awaiting_confirmation_count: number;
  approved_count: number;
  rejected_count: number;
  expired_count: number;
  active_paper_trades: number;
  current_paper_pnl: number;
  emergency_disable_state: string;
  safe_defaults: Record<string, unknown>;
};

export type ForexStrategy = {
  strategy_id: string;
  strategy_name: string;
  status: string;
  supported_symbols: string[];
  supported_timeframes: string[];
  supported_regimes: string[];
  execution_mode: string;
  enabled: boolean;
  validation_scorecard_id?: string | null;
};

export type ActiveForexTrade = {
  trade_id: string;
  candidate_id: string;
  strategy_id: string;
  symbol: string;
  direction: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  stop_price: number;
  target_price: number;
  unrealized_pnl: number;
  r_multiple: number;
  status: string;
  broker_state: string;
  reconciliation_state: string;
};

export type IbkrPaperStatus = {
  broker: string;
  environment: string;
  adapter_mode?: string;
  state_source?: string;
  connection_state: string;
  account_verified: boolean;
  masked_account_id?: string | null;
  market_data_state: string;
  server_time?: string | null;
  latency_ms?: number | null;
  reconciliation_status: string;
  emergency_disable: string;
  recovery: { status: string; blocking: boolean; last_started_at?: string | null; last_completed_at?: string | null };
  fixture_mode?: boolean;
  simulated_adapter?: boolean;
  real_submission_enabled?: boolean;
};

export type IbkrContractStatus = {
  canonical_symbol: string;
  verified: boolean;
  con_id: number;
  minimum_tick: string;
  verification_source?: string;
  usable_for_real_submission?: boolean;
  order_types: string[];
  rejection_reasons: string[];
};

export async function getForexSignalSummary(): Promise<ForexSignalSummary> {
  const response = await api.get<ForexSignalSummary>("/forex-signals/summary");
  return response.data;
}

export async function listForexCandidates(): Promise<ForexCandidate[]> {
  const response = await api.get<{ items: ForexCandidate[] }>("/forex-signals/candidates");
  return response.data.items;
}

export async function generateForexCandidate(params: {
  symbol: string;
  timeframe: string;
  price: number;
  regime?: string;
  session?: string;
  data_quality?: number;
  framework_agreement?: number;
  framework_bias?: string;
  frameworks?: string[];
  framework_signal_ids?: string[];
  feature_vector_id?: string | null;
  source_dataset_id?: string | null;
}): Promise<ForexCandidate> {
  const response = await api.post<ForexCandidate>("/forex-signals/generate", params);
  return response.data;
}

export async function approveForexCandidate(candidateId: string): Promise<ForexCandidate> {
  const response = await api.post<ForexCandidate>(`/forex-signals/candidates/${candidateId}/approve`, {});
  return response.data;
}

export async function rejectForexCandidate(candidateId: string, reason = "user rejected"): Promise<ForexCandidate> {
  const response = await api.post<ForexCandidate>(`/forex-signals/candidates/${candidateId}/reject`, { reason });
  return response.data;
}

export async function cancelForexCandidate(candidateId: string, reason = "user cancelled"): Promise<ForexCandidate> {
  const response = await api.post<ForexCandidate>(`/forex-signals/candidates/${candidateId}/cancel`, { reason });
  return response.data;
}

export async function listForexStrategies(): Promise<{ items: ForexStrategy[]; instrument_status: Record<string, string> }> {
  const response = await api.get<{ items: ForexStrategy[]; instrument_status: Record<string, string> }>("/forex-strategies");
  return response.data;
}

export async function listActiveForexTrades(): Promise<ActiveForexTrade[]> {
  const response = await api.get<{ items: ActiveForexTrade[] }>("/forex-execution/active");
  return response.data.items;
}

export async function getIbkrPaperStatus(): Promise<IbkrPaperStatus> {
  const response = await api.get<IbkrPaperStatus>("/brokers/ibkr/status");
  return response.data;
}

export async function listIbkrForexContracts(): Promise<IbkrContractStatus[]> {
  const response = await api.get<{ items: IbkrContractStatus[] }>("/brokers/ibkr/contracts");
  return response.data.items;
}
