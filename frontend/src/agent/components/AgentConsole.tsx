import { useEffect, useState } from "react";

import "../agentConsole.css";
import { useStockStore } from "../../store/stockStore";
import { useAgentStore } from "../agentStore";
import { buildScreenContext } from "../screenContext";
import { ArtifactCanvas } from "./ArtifactCanvas";
import { ChatThread } from "./ChatThread";

export function AgentConsole() {
  const open = useAgentStore((s) => s.open);
  const running = useAgentStore((s) => s.running);
  const debate = useAgentStore((s) => s.debate);
  const strategy = useAgentStore((s) => s.strategy);
  const screener = useAgentStore((s) => s.screener);
  const messages = useAgentStore((s) => s.messages);
  const artifacts = useAgentStore((s) => s.artifacts);
  const toggleOpen = useAgentStore((s) => s.toggleOpen);
  const setOpen = useAgentStore((s) => s.setOpen);
  const toggleDebate = useAgentStore((s) => s.toggleDebate);
  const toggleStrategy = useAgentStore((s) => s.toggleStrategy);
  const toggleScreener = useAgentStore((s) => s.toggleScreener);
  const startRun = useAgentStore((s) => s.startRun);
  const retry = useAgentStore((s) => s.retry);
  const stop = useAgentStore((s) => s.stop);
  const clear = useAgentStore((s) => s.clear);
  const exportConversation = useAgentStore((s) => s.exportConversation);
  // Subscribe to the active ticker so the context chip re-renders on symbol change.
  useStockStore((s) => s.ticker);
  const contextSymbol = buildScreenContext().symbol;
  const [draft, setDraft] = useState("");
  const activeModel = [...messages].reverse().find((message) => message.role === "assistant")?.model;
  const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
  const modelLabel = activeModel?.replace(/^.*\//, "").replace(/:free$/, "");

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "j") {
        ev.preventDefault();
        toggleOpen();
      } else if (ev.key === "Escape" && useAgentStore.getState().open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleOpen, setOpen]);

  const submit = () => {
    const text = draft.trim();
    if (!text || running) return;
    setDraft("");
    void startRun(text);
  };

  const copyLatest = async () => {
    const text = latestAssistant?.content ?? "";
    if (text) await navigator.clipboard?.writeText(text);
  };

  const exportLatest = async () => {
    await navigator.clipboard?.writeText(exportConversation());
  };

  const openResearchAgent = () => {
    window.location.assign("/equity/research-agent");
  };

  return (
    <aside
      className={`ot-agent-panel${open ? "" : " ot-agent-panel--closed"}`}
      role="dialog"
      aria-label="Agent Console"
      aria-hidden={!open}
    >
      <header
        style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "var(--ot-space-2) var(--ot-space-3)",
          borderBottom: "1px solid var(--ot-color-border-default)",
          fontWeight: "var(--ot-font-weight-semibold)", color: "var(--ot-color-text-primary)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--ot-space-2)" }}>
          <span>Agent</span>
          <button
            type="button"
            onClick={toggleDebate}
            aria-pressed={debate}
            aria-label="Toggle multi-agent debate mode"
            title="Multi-agent debate: analyst team → bull vs bear → portfolio-manager decision"
            className={`rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide transition-colors ${
              debate
                ? "border-terminal-accent bg-terminal-accent text-terminal-bg"
                : "border-terminal-border text-terminal-muted hover:border-terminal-accent hover:text-terminal-accent"
            }`}
          >
            Debate
          </button>
          <button
            type="button"
            onClick={toggleStrategy}
            aria-pressed={strategy}
            aria-label="Toggle strategy lab mode"
            title="Strategy Lab: bounded, read-only backtest iteration with out-of-sample validation"
            className={`rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide transition-colors ${
              strategy
                ? "border-terminal-accent bg-terminal-accent text-terminal-bg"
                : "border-terminal-border text-terminal-muted hover:border-terminal-accent hover:text-terminal-accent"
            }`}
          >
            Strategy Lab
          </button>
          <button
            type="button"
            onClick={toggleScreener}
            aria-pressed={screener}
            aria-label="Toggle screener mode"
            title="Screen membership: which built-in screens this stock qualifies under"
            className={`rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide transition-colors ${
              screener
                ? "border-terminal-accent bg-terminal-accent text-terminal-bg"
                : "border-terminal-border text-terminal-muted hover:border-terminal-accent hover:text-terminal-accent"
            }`}
          >
            Screener
          </button>
          {contextSymbol ? (
            <span
              title={`Default subject: ${contextSymbol} (the stock you have open)`}
              className="rounded border border-terminal-accent/50 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-terminal-accent"
            >
              ▦ {contextSymbol}
            </span>
          ) : null}
          {modelLabel ? (
            <span
              title="Model handling this request"
              className="rounded border border-terminal-border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-terminal-muted"
            >
              {modelLabel}
            </span>
          ) : null}
          <span className="rounded border border-terminal-border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-terminal-muted">
            {latestAssistant?.grounding?.validated === false ? "Grounding: limited" : "Grounding: verified"}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close agent console"
          style={{ background: "transparent", border: "none", color: "var(--ot-color-text-muted)", cursor: "pointer", fontSize: 16 }}
        >
          ✕
        </button>
      </header>

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        <ChatThread messages={messages} />
        <ArtifactCanvas artifacts={artifacts} />
        {latestAssistant ? (
          <section className="mx-3 mb-3 grid gap-2 rounded border border-terminal-border bg-terminal-panel/70 p-2 text-[11px] text-terminal-muted">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold uppercase tracking-wide text-terminal-text">Evidence</span>
              <span>{latestAssistant.evidenceBundleId ?? "No bundle"}</span>
              {latestAssistant.latencyMs != null ? <span>{Math.round(latestAssistant.latencyMs)}ms</span> : null}
            </div>
            {latestAssistant.citations?.length ? (
              <div className="flex flex-wrap gap-1">
                {latestAssistant.citations.map((citation) => (
                  <span key={`${citation.bundle_id}-${citation.marker}`} className="rounded border border-terminal-accent/40 px-1.5 py-0.5 text-terminal-accent">
                    {citation.marker}
                  </span>
                ))}
              </div>
            ) : null}
            {latestAssistant.tokenUsage ? (
              <div className="font-mono">
                Tokens: {String(latestAssistant.tokenUsage.prompt_tokens ?? 0)} prompt / {String(latestAssistant.tokenUsage.completion_tokens ?? 0)} completion
              </div>
            ) : null}
            {latestAssistant.grounding?.warnings?.length ? (
              <div>Limitations: {latestAssistant.grounding.warnings.join(", ")}</div>
            ) : null}
          </section>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2 border-t border-terminal-border px-2 py-2">
        <button type="button" onClick={copyLatest} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Copy</button>
        <button type="button" onClick={exportLatest} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Export</button>
        <button type="button" onClick={retry} disabled={running} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Retry</button>
        <button type="button" onClick={stop} disabled={!running} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Stop</button>
        <button type="button" onClick={clear} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Clear</button>
        <button type="button" onClick={openResearchAgent} className="rounded border border-terminal-border px-2 py-1 text-[11px] uppercase text-terminal-muted hover:text-terminal-accent">Research Agent</button>
        <span className="ml-auto text-[11px] uppercase text-terminal-muted">Suggested: explain risk, summarize order, compare evidence</span>
      </div>

      <div style={{ display: "flex", gap: "var(--ot-space-2)", padding: "var(--ot-space-2)", borderTop: "1px solid var(--ot-color-border-default)" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          placeholder={
            debate
              ? `Enter a ticker for multi-agent debate${contextSymbol ? ` (default ${contextSymbol})` : ""}…`
              : strategy
                ? `Enter a ticker for Strategy Lab${contextSymbol ? ` (default ${contextSymbol})` : ""}…`
              : contextSymbol
                ? `Ask about ${contextSymbol} or any stock…`
                : "Ask the agent to find or analyze stocks…"
          }
          aria-label="Agent prompt"
          style={{
            flex: 1, background: "var(--ot-color-canvas-elevated)",
            border: "1px solid var(--ot-color-border-default)", borderRadius: "var(--ot-radius-sm)",
            color: "var(--ot-color-text-primary)", fontFamily: "var(--ot-font-ui)",
            padding: "var(--ot-space-2)",
          }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={running}
          style={{
            background: "var(--ot-color-accent-primary)", color: "var(--ot-color-text-inverse)",
            border: "none", borderRadius: "var(--ot-radius-sm)", padding: "0 var(--ot-space-3)",
            cursor: running ? "default" : "pointer", opacity: running ? 0.6 : 1,
            fontWeight: "var(--ot-font-weight-semibold)",
          }}
        >
          {running ? "…" : "Send"}
        </button>
      </div>
    </aside>
  );
}
