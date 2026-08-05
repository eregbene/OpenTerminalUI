import { expect, test } from "@playwright/test";

test("broker operations page labels IBKR paper mode", async ({ page }) => {
  await page.goto("/equity/brokers", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "IBKR PAPER ACCOUNT" })).toBeVisible();
  await expect(page.getByText(/Live trading is disabled/i)).toBeVisible();
  await expect(page.getByText("Connection")).toBeVisible();
});
