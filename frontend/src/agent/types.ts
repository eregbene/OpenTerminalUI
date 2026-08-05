export type AgentEvent =
  | { type: "token"; text: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; result: unknown; is_error: boolean }
  | { type: "artifact"; kind: string; name: string; data: unknown }
  | { type: "model"; name: string; phase: string }
  | { type: "status"; text: string }
  | { type: "phase"; key: string; label: string }
  | { type: "role_message"; role: string; content: string }
  | { type: "final"; content?: string; answer?: string; conversation_id?: string; evidence_bundle_id?: string; citations?: { marker: string; bundle_id: string; label: string }[]; token_usage?: Record<string, unknown>; grounding?: { validated: boolean; warnings: string[] }; provider?: string; latency_ms?: number }
  | { type: "error"; message: string };

export interface RunContext {
  route?: string;
  symbol?: string;
  market?: string;
}

export interface RunRequest {
  prompt: string;
  context?: RunContext;
  provider?: string;
  model?: string;
  mode?: "standard" | "deep" | "debate" | "strategy" | "screener";
  ticker?: string;
}

export interface AgentPhase {
  key: string;
  label: string;
}

export interface AgentRoleNote {
  role: string;
  content: string;
}

export interface AgentMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps: { id: string; name: string; isError: boolean }[];
  phases: AgentPhase[];
  roles: AgentRoleNote[];
  pending: boolean;
  model?: string;
  /** Transient live progress note for the active turn (e.g. "Contacting llama…",
   * "rate-limited; retrying in 15s…"). Not part of the persisted answer. */
  status?: string;
  evidenceBundleId?: string;
  conversationId?: string;
  citations?: { marker: string; bundle_id: string; label: string }[];
  tokenUsage?: Record<string, unknown>;
  grounding?: { validated: boolean; warnings: string[] };
  latencyMs?: number;
}

export interface AgentArtifact {
  id: string;
  kind: string;
  name: string;
  data: unknown;
}
