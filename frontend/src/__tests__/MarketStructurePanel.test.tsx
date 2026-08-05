import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEFAULT_MARKET_STRUCTURE_TOGGLES,
  MarketStructurePanel,
  type MarketStructureToggles,
} from "../components/chart-workstation/MarketStructurePanel";
import type { MarketStructureSnapshot } from "../api/marketStructure";

function snapshot(): MarketStructureSnapshot {
  return {
    snapshot_id: "mssnap_1",
    symbol: "TEST",
    timeframe: "15m",
    configuration_version: "1.0",
    configuration_hash: "abc123",
    warnings: [],
    trend: { state: "bullish", evidence: ["higher high and higher low sequence"] },
    breaks: [{ id: "b1", break_kind: "bos", direction: "bullish", explanation: "Bullish BOS confirmed." }],
    swings: [{ id: "s1", swing_type: "high", price: 110, confirmation_time: "2026-01-01T00:00:00Z" }],
    liquidity_levels: [],
    liquidity_sweeps: [],
    imbalances: [],
    order_blocks: [],
    dealing_ranges: [{ id: "r1", price_low: 100, price_high: 120, normalized_current_position: 0.5 }],
    overlays: [
      {
        overlay_id: "ov1",
        type: "event_marker",
        direction: "bullish",
        label: "BOS",
        style_role: "bos",
        status: "confirmed",
        tooltip_evidence: ["Bullish BOS confirmed."],
      },
    ],
    score: { total_score: 0.75 },
    explanations: ["Trend is bullish because higher high and higher low sequence."],
  };
}

describe("MarketStructurePanel", () => {
  it("renders inspector fields and toggles overlays", async () => {
    const user = userEvent.setup();
    let toggles: MarketStructureToggles = { ...DEFAULT_MARKET_STRUCTURE_TOGGLES };
    const onToggle = (key: keyof MarketStructureToggles) => {
      toggles = { ...toggles, [key]: !toggles[key] };
    };
    render(<MarketStructurePanel snapshot={snapshot()} loading={false} error={null} toggles={toggles} onToggle={onToggle} />);
    expect(screen.getByText("Market Structure")).toBeInTheDocument();
    expect(screen.getByText("bullish")).toBeInTheDocument();
    expect(screen.getAllByText("BOS").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "BOS" }));
    expect(toggles.bos).toBe(false);
  });
});
