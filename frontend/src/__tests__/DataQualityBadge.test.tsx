import { render, screen } from "@testing-library/react";

import { DataQualityBadge } from "../components/market/DataQualityBadge";

describe("DataQualityBadge", () => {
  it("renders provenance tooltip without claiming fallback is realtime", () => {
    render(<DataQualityBadge status="fallback" provider="internal-demo" timestamp="2026-01-01T12:00:00Z" />);
    const badge = screen.getByTestId("data-quality-badge");
    expect(badge).toHaveTextContent("Fallback");
    expect(badge).toHaveAttribute("data-status", "fallback");
    expect(badge.getAttribute("title")).toContain("internal-demo");
  });
});
