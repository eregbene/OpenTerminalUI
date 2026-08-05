import { api } from "./base";

export type ResearchAgentStatus = {
  mode: string;
  autonomous_default: boolean;
  active_policy?: ResearchPolicy | null;
  kill_switches: Array<Record<string, unknown>>;
  scheduler: Record<string, unknown>;
  health: string;
};

export type ResearchPolicy = {
  policy_id: string;
  owner_user_id: string;
  workspace_id: string;
  status: string;
  allowed_instruments: string[];
  allowed_research_templates: string[];
  maximum_jobs_per_day: number;
  maximum_provider_cost: string;
  approval_required: boolean;
};

export type ResearchPlan = {
  plan_id: string;
  title: string;
  objective: string;
  status: string;
  approval_state: string;
  agent_mode: string;
  policy_id?: string | null;
  instrument_scope: string[];
  dataset_scope: string[];
  strategy_scope: string[];
  tasks: Array<{ task_id: string; type: string; status: string; dependencies: string[]; policy_decision: string }>;
  dependencies?: Record<string, string[]>;
  budget: Record<string, unknown>;
};

export const fetchResearchAgentStatus = async () => (await api.get<ResearchAgentStatus>("/research-agent/status")).data;
export const fetchResearchAgentPolicies = async () => (await api.get<{ items: ResearchPolicy[] }>("/research-agent/policies")).data.items;
export const createResearchAgentPolicy = async (payload: Record<string, unknown> = {}) => (await api.post<ResearchPolicy>("/research-agent/policies", payload)).data;
export const activateResearchAgentPolicy = async (policyId: string) => (await api.post<ResearchPolicy>(`/research-agent/policies/${policyId}/activate`)).data;
export const fetchResearchAgentPlans = async () => (await api.get<{ items: ResearchPlan[] }>("/research-agent/plans")).data.items;
export const createResearchAgentPlan = async (payload: Record<string, unknown>) => (await api.post<ResearchPlan>("/research-agent/plans", payload)).data;
export const approveResearchAgentPlan = async (planId: string) => (await api.post<ResearchPlan>(`/research-agent/plans/${planId}/approve`)).data;
export const rejectResearchAgentPlan = async (planId: string) => (await api.post<ResearchPlan>(`/research-agent/plans/${planId}/reject`)).data;
export const startResearchAgentPlan = async (planId: string) => (await api.post<ResearchPlan>(`/research-agent/plans/${planId}/start`)).data;
export const pauseResearchAgentPlan = async (planId: string) => (await api.post<ResearchPlan>(`/research-agent/plans/${planId}/pause`)).data;
export const cancelResearchAgentPlan = async (planId: string) => (await api.post<ResearchPlan>(`/research-agent/plans/${planId}/cancel`)).data;
export const fetchResearchAgentReports = async (planId: string) => (await api.get<{ items: Array<Record<string, unknown>> }>(`/research-agent/plans/${planId}/reports`)).data.items;
export const fetchResearchAgentLineage = async (planId: string) => (await api.get<Record<string, unknown>>(`/research-agent/plans/${planId}/lineage`)).data;
export const generateResearchHypotheses = async (payload: Record<string, unknown>) => (await api.post<{ items: Array<Record<string, unknown>> }>("/research-agent/hypotheses", payload)).data.items;
export const fetchAIProviderHealth = async () => (await api.get<{ items: Array<Record<string, unknown>> }>("/ai/providers/health")).data.items;
export const fetchAIProviderBudgets = async () => (await api.get<Record<string, unknown>>("/ai/providers/budgets")).data;
export const runAIProviderHealthCheck = async (provider: string) => (await api.post<Record<string, unknown>>(`/ai/providers/${provider}/health-check`)).data;
