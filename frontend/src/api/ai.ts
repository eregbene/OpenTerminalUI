import { api } from "./base";
import type {
  AIResearchBrief,
  AIQueryResult,
} from "../types";

export async function aiQuery(query: string, context: Record<string, any>): Promise<AIQueryResult> {
  const { data } = await api.post<AIQueryResult>("/ai/query", { query, context });
  return data;
}

export async function createAIResearchBrief(payload: {
  symbol: string;
  horizon?: string;
  question?: string;
  context?: Record<string, unknown>;
}): Promise<AIResearchBrief> {
  const { data } = await api.post<AIResearchBrief>("/ai/research-brief", payload);
  return data;
}
