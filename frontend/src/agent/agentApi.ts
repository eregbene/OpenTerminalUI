import { api, getAccessToken } from "../api/base";
import { parseSSEBuffer } from "./sse";
import type { AgentEvent, RunRequest } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string) || "/api";

/** Create a run and return its id. Uses the shared axios client (auth handled by interceptor). */
export async function createRun(req: RunRequest): Promise<string> {
  const { data } = await api.post<{ run_id: string }>("/agent/runs", req);
  return data.run_id;
}

/**
 * Open the SSE stream for a run and invoke `onEvent` per agent event.
 * Resolves when the stream ends. Never throws for transport/HTTP errors —
 * it emits a synthetic `error` event instead so the UI degrades gracefully.
 */
export async function streamRun(
  runId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken();
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/agent/runs/${runId}/stream`, { headers, signal });
  } catch (err) {
    // A user-initiated abort is not an error to surface in the UI.
    if (signal?.aborted) return;
    onEvent({ type: "error", message: (err as Error).message || "network error" });
    return;
  }
  if (!resp.ok || !resp.body) {
    onEvent({ type: "error", message: `stream failed (HTTP ${resp.status})` });
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSSEBuffer(buffer);
    buffer = rest;
    for (const event of events) onEvent(event);
  }
}

export interface AIChatRequest {
  message: string;
  conversation_id?: string;
  evidence_bundle_id?: string;
  domain?: string;
  entity_id?: string;
}

export async function chatStream(
  req: AIChatRequest,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken();
  const headers: Record<string, string> = { Accept: "text/event-stream", "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/ai/chat/stream`, { method: "POST", headers, body: JSON.stringify(req), signal });
  } catch (err) {
    if (signal?.aborted) return;
    onEvent({ type: "error", message: (err as Error).message || "network error" });
    return;
  }
  if (!resp.ok || !resp.body) {
    onEvent({ type: "error", message: `AI chat failed (HTTP ${resp.status})` });
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSSEBuffer(buffer);
    buffer = rest;
    for (const event of events) onEvent(event);
  }
}

export async function listAIConversations(): Promise<{ items: unknown[] }> {
  const { data } = await api.get<{ items: unknown[] }>("/ai/conversations");
  return data;
}
