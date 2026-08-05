import { expect, test } from "@playwright/test";

test("research agent operations page loads with safety boundary", async ({ page }) => {
  await page.goto("/equity/research-agent", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "Autonomous Research Operations" })).toBeVisible();
  await expect(page.getByText(/Research-only system/i)).toBeVisible();
  await expect(page.getByText("Provider Health")).toBeVisible();
});
