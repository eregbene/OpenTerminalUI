import { api } from "./base";

export type Phase12PortfolioDashboard = {
  status?: string;
  portfolio_id?: string;
  name?: string;
  base_currency?: string;
  total_equity?: number;
  cash?: number;
  daily_pnl?: number;
  total_return_pct?: number;
  drawdown_pct?: number;
  gross_exposure?: number;
  net_exposure?: number;
  margin_utilization?: number;
  active_strategies?: number;
  valuation_status?: string;
  paper_only?: boolean;
  alerts?: Array<{ alert_id: string; severity: string; message: string }>;
  message?: string;
};

export async function fetchPhase12PortfolioDashboard(): Promise<Phase12PortfolioDashboard> {
  const { data } = await api.get<Phase12PortfolioDashboard>("/portfolio/dashboard");
  return data;
}

export async function fetchPhase12PortfolioManagement(): Promise<{ items: any[]; permissions: string[] }> {
  const [{ data: portfolios }, { data: management }] = await Promise.all([api.get("/portfolios"), api.get("/portfolio/management")]);
  return { items: portfolios.items || management.items || [], permissions: management.permissions || [] };
}

export async function fetchPhase12Performance(portfolioId: string): Promise<any> {
  const { data } = await api.get(`/portfolios/${encodeURIComponent(portfolioId)}/performance`);
  return data;
}

export async function fetchPhase12Risk(portfolioId: string): Promise<any> {
  const { data } = await api.get(`/portfolios/${encodeURIComponent(portfolioId)}/risk`);
  return data;
}

export async function fetchPhase12OperationsHealth(): Promise<{ components: any[] }> {
  const { data } = await api.get("/operations/health");
  return data;
}

export async function fetchPhase12Alerts(): Promise<{ items: any[] }> {
  const { data } = await api.get("/operations/alerts");
  return data;
}

export async function fetchPhase12Incidents(): Promise<{ items: any[] }> {
  const { data } = await api.get("/operations/incidents");
  return data;
}

export async function fetchPhase12Reports(): Promise<{ items: any[] }> {
  const { data } = await api.get("/reports");
  return data;
}

export async function generatePhase12Report(payload: Record<string, unknown>): Promise<any> {
  const { data } = await api.post("/reports/generate", payload);
  return data;
}

export async function fetchPhase12ReportSchedules(): Promise<{ items: any[] }> {
  const { data } = await api.get("/reports/schedules");
  return data;
}

export async function evaluatePhase12Execution(payload: Record<string, unknown>): Promise<any> {
  const { data } = await api.post("/execution-analytics/evaluate", payload);
  return data;
}
