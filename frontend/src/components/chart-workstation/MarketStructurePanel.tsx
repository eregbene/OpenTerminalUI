import type { MarketStructureSnapshot } from "../../api/marketStructure";

type ToggleKey = "swings" | "bos" | "choch" | "mss" | "liquidity" | "sweeps" | "fvg" | "orderBlocks" | "ranges" | "sessions";

const TOGGLES: Array<{ key: ToggleKey; label: string }> = [
  { key: "swings", label: "Swings" },
  { key: "bos", label: "BOS" },
  { key: "choch", label: "CHoCH" },
  { key: "mss", label: "MSS" },
  { key: "liquidity", label: "Liquidity" },
  { key: "sweeps", label: "Sweeps" },
  { key: "fvg", label: "FVG" },
  { key: "orderBlocks", label: "OB" },
  { key: "ranges", label: "Range" },
  { key: "sessions", label: "Sessions" },
];

export type MarketStructureToggles = Record<ToggleKey, boolean>;

export const DEFAULT_MARKET_STRUCTURE_TOGGLES: MarketStructureToggles = {
  swings: true,
  bos: true,
  choch: true,
  mss: true,
  liquidity: false,
  sweeps: true,
  fvg: true,
  orderBlocks: true,
  ranges: false,
  sessions: false,
};

function overlayEnabled(role: string, toggles: MarketStructureToggles): boolean {
  if (role.includes("swing")) return toggles.swings;
  if (role === "bos") return toggles.bos;
  if (role === "choch") return toggles.choch;
  if (role === "mss") return toggles.mss;
  if (role.includes("liquidity_sweep")) return toggles.sweeps;
  if (role.includes("buy_side") || role.includes("sell_side")) return toggles.liquidity;
  if (role === "fvg") return toggles.fvg;
  if (role === "order_block") return toggles.orderBlocks;
  if (role === "dealing_range") return toggles.ranges;
  if (role === "session_level") return toggles.sessions;
  return true;
}

export function MarketStructurePanel({
  snapshot,
  loading,
  error,
  toggles,
  onToggle,
}: {
  snapshot: MarketStructureSnapshot | null;
  loading: boolean;
  error: string | null;
  toggles: MarketStructureToggles;
  onToggle: (key: ToggleKey) => void;
}) {
  const visibleOverlays = snapshot?.overlays.filter((overlay) => overlayEnabled(overlay.style_role, toggles)).slice(0, 10) ?? [];
  const latestBreak = snapshot?.breaks.length ? snapshot.breaks[snapshot.breaks.length - 1] : null;
  const latestRange = snapshot?.dealing_ranges.length ? snapshot.dealing_ranges[snapshot.dealing_ranges.length - 1] : null;
  return (
    <div
      className="absolute left-2 top-10 z-20 w-80 max-w-[calc(100%-1rem)] rounded border border-terminal-border bg-terminal-panel/95 p-3 text-[11px] text-terminal-text shadow-xl"
      data-testid="market-structure-panel"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-2 border-b border-terminal-border pb-2">
        <div>
          <div className="font-semibold uppercase tracking-[0.18em] text-terminal-accent">Market Structure</div>
          <div className="text-[10px] text-terminal-muted">
            {snapshot ? `v${snapshot.configuration_version} / ${snapshot.configuration_hash}` : "bensim-smc"}
          </div>
        </div>
        {snapshot?.score ? <span className="rounded border border-terminal-border px-2 py-0.5">{Math.round(snapshot.score.total_score * 100)}%</span> : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {TOGGLES.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`rounded border px-1.5 py-0.5 ${toggles[item.key] ? "border-terminal-accent text-terminal-accent" : "border-terminal-border text-terminal-muted"}`}
            onClick={() => onToggle(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {loading ? <div className="mt-3 text-terminal-muted">Analyzing completed bars...</div> : null}
      {error ? <div className="mt-3 text-terminal-neg">{error}</div> : null}
      {snapshot ? (
        <>
          <div className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <span className="text-terminal-muted">Trend</span>
            <span>{snapshot.trend?.state ?? "unknown"}</span>
            <span className="text-terminal-muted">Last break</span>
            <span>{latestBreak?.break_kind ?? "-"}</span>
            <span className="text-terminal-muted">Swings</span>
            <span>{snapshot.swings.length}</span>
            <span className="text-terminal-muted">FVG</span>
            <span>{snapshot.imbalances.length}</span>
            <span className="text-terminal-muted">OB</span>
            <span>{snapshot.order_blocks.length}</span>
            <span className="text-terminal-muted">Range pos</span>
            <span>{latestRange?.normalized_current_position?.toFixed(2) ?? "-"}</span>
          </div>
          <div className="mt-3 border-t border-terminal-border pt-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-terminal-muted">Visible overlays</div>
            <div className="space-y-1">
              {visibleOverlays.length ? visibleOverlays.map((overlay) => (
                <div key={overlay.overlay_id} className="flex items-center justify-between gap-2 rounded border border-terminal-border/70 bg-terminal-bg/40 px-2 py-1" title={overlay.tooltip_evidence.join(" | ")}>
                  <span className="truncate">{overlay.label}</span>
                  <span className="text-terminal-muted">{overlay.status}</span>
                </div>
              )) : <div className="text-terminal-muted">No overlays enabled.</div>}
            </div>
          </div>
          {snapshot.explanations[0] ? <div className="mt-3 text-terminal-muted">{snapshot.explanations[0]}</div> : null}
        </>
      ) : null}
    </div>
  );
}
