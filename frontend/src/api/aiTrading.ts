import { api } from "./base";

export type AITradingStatus = {
  provider_configured: boolean;
  model_configured: boolean;
  model: string;
  api_key_configured: boolean;
  ai_mode: string;
  analysis_enabled: boolean;
  order_submission_enabled: boolean;
  broker_mode: string;
  live_trading_enabled: boolean;
  last_successful_request?: string | null;
  last_provider_error?: string | null;
  usage?: Record<string, unknown>;
  scheduler?: Record<string, unknown>;
  emergency_disabled?: boolean;
};

export type AIShadowDecision = {
  decision_id: string;
  shadow_trade_id?: string | null;
  symbol: string;
  timeframe: string;
  status: string;
  validation_status: string;
  risk_status: string;
  rejection_reasons: string[];
  decision?: {
    decision: string;
    confidence: number;
    proposed_entry?: number | null;
    stop_loss?: number | null;
    take_profit?: number | null;
    reasoning_summary: string;
    data_timestamp: string;
    decision_timestamp: string;
  } | null;
  provider?: Record<string, unknown> | null;
  shadow_trade_created: boolean;
};

export const fetchAITradingStatus = async () => (await api.get<AITradingStatus>("/ai-trading/status")).data;
export const runAIShadowAnalysis = async (symbol: string, timeframe = "15m") => (await api.post<AIShadowDecision>("/ai-trading/shadow/analyze", { symbol, timeframe })).data;
export const fetchAITradingDecisions = async () => (await api.get<{ items: Array<Record<string, unknown>> }>("/ai-trading/decisions")).data.items;
export const emergencyDisableAITrading = async () => (await api.post<Record<string, unknown>>("/ai-trading/emergency-disable")).data;
export const fetchAITradingUsage = async () => (await api.get<Record<string, unknown>>("/ai-trading/usage")).data;
export const fetchAIInstitutionalStatus = async () => (await api.get<Record<string, unknown>>("/ai-trading/institutional/status")).data;
