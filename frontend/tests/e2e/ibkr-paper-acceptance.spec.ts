import { expect, test } from "@playwright/test";

test("forex page labels fixture IBKR as non-real paper connectivity", async ({ page }) => {
  const email = `ibkr-e2e-${Date.now()}@example.com`;
  const password = "StrongPass123!";
  await page.request.post("/api/auth/register", {
    data: { email, password, role: "trader" },
  });
  const login = await page.request.post("/api/auth/login", {
    data: { email, password },
  });
  expect(login.ok()).toBeTruthy();
  const tokens = (await login.json()) as { access_token: string; refresh_token: string };

  await page.addInitScript(
    ([at, rt]) => {
      localStorage.setItem("ot-access-token", at);
      localStorage.setItem("ot-refresh-token", rt);
    },
    [tokens.access_token, tokens.refresh_token],
  );

  await page.goto("/equity/forex?symbol=EURUSD", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("IBKR Paper Status")).toBeVisible();
  await expect(page.getByText("Adapter: FIXTURE_IBKR")).toBeVisible();
  await expect(page.getByText(/Real paper submission blocked/i)).toBeVisible();
  await expect(page.getByText("Contract Status")).toBeVisible();
});
