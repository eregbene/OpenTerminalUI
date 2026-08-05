import { api } from "./base";

export type ResearchItem = {
  id: string;
  source: string;
  external_id: string;
  title: string;
  authors: string[];
  abstract: string;
  url: string;
  categories: string[];
  published_at: string;
  text_chars?: number;
  score?: number;
};

export async function ingestResearch(
  query: string,
  max_results = 25,
): Promise<{ ingested: number; fetched: number; query: string }> {
  const { data } = await api.post<{ ingested: number; fetched: number; query: string }>("/research/ingest", {
    query,
    max_results,
  });
  return data;
}

export async function ingestUrlResearch(
  url: string,
): Promise<{ ingested: number; url: string; title: string; text_chars: number }> {
  const { data } = await api.post<{ ingested: number; url: string; title: string; text_chars: number }>(
    "/research/ingest_url",
    { url },
  );
  return data;
}

export async function searchResearch(q: string, k = 10): Promise<{ query: string; results: ResearchItem[] }> {
  const { data } = await api.get<{ query: string; results: ResearchItem[] }>("/research/search", {
    params: { q, k },
  });
  return data;
}

export async function listResearch(limit = 50): Promise<{ items: ResearchItem[] }> {
  const { data } = await api.get<{ items: ResearchItem[] }>("/research/items", {
    params: { limit },
  });
  return data;
}

export type StrategyPerformanceSnapshot = {
  items?: Array<Record<string, any>>;
  adaptive_weights?: Record<string, number>;
  health_counts?: Record<string, number>;
  sample_size_protection?: Record<string, any>;
  updated_at?: string;
};

export async function getStrategyLeaderboard(): Promise<StrategyPerformanceSnapshot> {
  const { data } = await api.get<StrategyPerformanceSnapshot>("/research/leaderboard");
  return data;
}

export async function getResearchPerformance(): Promise<Record<string, any>> {
  const { data } = await api.get<Record<string, any>>("/research/performance");
  return data;
}

export async function getResearchEquity(): Promise<Record<string, any>> {
  const { data } = await api.get<Record<string, any>>("/research/equity");
  return data;
}

export async function getResearchCalibration(): Promise<Record<string, any>> {
  const { data } = await api.get<Record<string, any>>("/research/calibration");
  return data;
}

export async function getResearchPerformanceAnalysis(): Promise<Record<string, any>> {
  const { data } = await api.get<Record<string, any>>("/research/performance/analysis");
  return data;
}
