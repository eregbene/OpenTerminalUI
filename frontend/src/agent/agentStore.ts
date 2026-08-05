import { create } from "zustand";

import { chatStream, createRun, streamRun } from "./agentApi";
import { buildScreenContext } from "./screenContext";
import type { AgentArtifact, AgentEvent, AgentMessage } from "./types";

let seq = 0;
const nextId = () => `m${Date.now()}_${seq++}`;
let activeController: AbortController | null = null;

interface AgentState {
  open: boolean;
  running: boolean;
  debate: boolean;
  strategy: boolean;
  screener: boolean;
  messages: AgentMessage[];
  artifacts: AgentArtifact[];
  lastPrompt?: string;
  toggleOpen: () => void;
  setOpen: (open: boolean) => void;
  toggleDebate: () => void;
  toggleStrategy: () => void;
  toggleScreener: () => void;
  runScreenerFor: (ticker: string) => Promise<void>;
  appendUserAndPending: (prompt: string) => void;
  applyEvent: (event: AgentEvent) => void;
  startRun: (prompt: string) => Promise<void>;
  retry: () => Promise<void>;
  stop: () => void;
  clear: () => void;
  exportConversation: () => string;
  explainEntity: (domain: string, entityId: string, label?: string) => Promise<void>;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  open: false,
  running: false,
  debate: false,
  strategy: false,
  screener: false,
  messages: [],
  artifacts: [],
  lastPrompt: undefined,

  toggleOpen: () => set((s) => ({ open: !s.open })),
  setOpen: (open) => set({ open }),
  toggleDebate: () => set((s) => ({ debate: !s.debate, strategy: false, screener: false })),
  toggleStrategy: () => set((s) => ({ strategy: !s.strategy, debate: false, screener: false })),
  toggleScreener: () => set((s) => ({ screener: !s.screener, debate: false, strategy: false })),

  appendUserAndPending: (prompt) =>
    set((s) => ({
      messages: [
        ...s.messages,
        { id: nextId(), role: "user", content: prompt, steps: [], phases: [], roles: [], pending: false },
        { id: nextId(), role: "assistant", content: "", steps: [], phases: [], roles: [], pending: true, model: undefined },
      ],
    })),

  applyEvent: (event) => {
    if (event.type === "artifact") {
      set((s) => ({
        artifacts: [
          ...s.artifacts,
          { id: nextId(), kind: event.kind, name: event.name, data: event.data },
        ],
      }));
      return;
    }
    set((s) => {
      const messages = s.messages.slice();
      const idx = messages.length - 1;
      if (idx < 0 || messages[idx].role !== "assistant") return s;
      const msg = {
        ...messages[idx],
        steps: messages[idx].steps.slice(),
        phases: messages[idx].phases.slice(),
        roles: messages[idx].roles.slice(),
      };

      switch (event.type) {
        case "model":
          msg.model = event.name;
          break;
        case "status":
          msg.status = event.text;
          break;
        case "tool_call":
          // A concrete step supersedes the transient model-layer status line.
          msg.status = undefined;
          msg.steps.push({ id: event.id, name: event.name, isError: false });
          break;
        case "tool_result": {
          // Replace the step object (don't mutate the shared prior-state object).
          const si = msg.steps.findIndex((st) => st.id === event.id);
          if (si !== -1) msg.steps[si] = { ...msg.steps[si], isError: event.is_error };
          break;
        }
        case "phase":
          msg.phases.push({ key: event.key, label: event.label });
          break;
        case "role_message":
          msg.roles.push({ role: event.role, content: event.content });
          break;
        case "token":
          msg.content += event.text;
          break;
        case "final":
          msg.content = event.content ?? event.answer ?? msg.content;
          msg.evidenceBundleId = event.evidence_bundle_id;
          msg.conversationId = event.conversation_id;
          msg.citations = event.citations;
          msg.tokenUsage = event.token_usage;
          msg.grounding = event.grounding;
          msg.latencyMs = event.latency_ms;
          msg.status = undefined;
          msg.pending = false;
          break;
        case "error":
          msg.content = msg.content || `The agent hit an error: ${event.message}`;
          msg.status = undefined;
          msg.pending = false;
          break;
      }
      messages[idx] = msg;
      const running = event.type === "final" || event.type === "error" ? false : s.running;
      return { messages, running };
    });
  },

  startRun: async (prompt) => {
    const text = prompt.trim();
    if (!text || get().running) return;
    get().appendUserAndPending(text);
    set({ running: true, lastPrompt: text });
    activeController = new AbortController();
    try {
      const debate = get().debate;
      const strategy = get().strategy;
      const screener = get().screener;
      if (debate || strategy || screener) {
        const runId = await createRun({
          prompt: text,
          context: buildScreenContext(),
          ...(debate ? { mode: "debate" as const, ticker: text }
            : strategy ? { mode: "strategy" as const, ticker: text }
              : screener ? { mode: "screener" as const, ticker: text }
                : {}),
        });
        await streamRun(runId, (event) => get().applyEvent(event), activeController.signal);
      } else {
        const context = buildScreenContext();
        await chatStream(
          {
            message: text,
            ...(context.symbol ? { domain: "market_structure", entity_id: context.symbol } : {}),
          },
          (event) => get().applyEvent(event),
          activeController.signal,
        );
      }
    } catch (err) {
      get().applyEvent({ type: "error", message: (err as Error).message || "request failed" });
    } finally {
      activeController = null;
      if (get().running) set({ running: false });
    }
  },

  retry: async () => {
    const prompt = get().lastPrompt;
    if (prompt) await get().startRun(prompt);
  },

  stop: () => {
    activeController?.abort();
    activeController = null;
    set((s) => ({ running: false, messages: s.messages.map((m, i) => i === s.messages.length - 1 ? { ...m, pending: false, status: undefined } : m) }));
  },

  clear: () => set({ messages: [], artifacts: [], running: false }),

  exportConversation: () => JSON.stringify({ messages: get().messages, artifacts: get().artifacts }, null, 2),

  explainEntity: async (domain, entityId, label) => {
    const prompt = `Explain ${label ?? entityId}`;
    set({ open: true, debate: false, strategy: false, screener: false });
    get().appendUserAndPending(prompt);
    set({ running: true, lastPrompt: prompt });
    activeController = new AbortController();
    try {
      await chatStream(
        { message: prompt, domain, entity_id: entityId },
        (event) => get().applyEvent(event),
        activeController.signal,
      );
    } catch (err) {
      get().applyEvent({ type: "error", message: (err as Error).message || "request failed" });
    } finally {
      activeController = null;
      if (get().running) set({ running: false });
    }
  },

  runScreenerFor: async (ticker) => {
    const t = (ticker || "").trim().toUpperCase();
    if (!t) return;
    set({ screener: true, debate: false, strategy: false, open: true });
    await get().startRun(t);
  },
}));
