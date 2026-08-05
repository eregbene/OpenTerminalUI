import { expect, test } from "@playwright/test";

test("agent console opens from the app shell", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await page.keyboard.press("Control+J");

  const consolePanel = page.getByRole("dialog", { name: "Agent Console" });
  await expect(consolePanel).toBeVisible();
  await expect(consolePanel.getByPlaceholder(/Ask|Enter a ticker/i)).toBeVisible();
});
