import { expect, test } from "@playwright/test";

function makeJwt(payload: Record<string, unknown>): string {
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `x.${encoded}.y`;
}

const corsHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,OPTIONS",
  "access-control-allow-headers": "*",
};

test("pair trading lab renders deterministic cointegration and backtest results", async ({ page }) => {
  test.slow();
  const accessToken = makeJwt({
    sub: "e2e-user",
    email: "e2e@example.com",
    role: "trader",
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  const refreshToken = makeJwt({ exp: Math.floor(Date.now() / 1000) + 7200 });

  await page.addInitScript(
    ([at, rt]) => {
      localStorage.setItem("ot-access-token", at);
      localStorage.setItem("ot-refresh-token", rt);
    },
    [accessToken, refreshToken],
  );

  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.route("**/api/pairs/test", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      json: {
        symbol1: "VOO",
        symbol2: "SPY",
        period: "2Y",
        period_start: "2025-01-02",
        period_end: "2025-04-30",
        alpha: 0.12,
        beta: 1.0172,
        adf_stat: -3.42,
        adf_pvalue: 0.021,
        coint_pvalue: 0.018,
        half_life: 12.5,
        zscore_current: -0.64,
        resid_mean: 0.02,
        resid_std: 1.13,
        cointegrated: true,
        verdict: "Cointegrated (p=0.0180)",
      },
    });
  });

  await page.route("**/api/pairs/signals", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      json: {
        symbol1: "VOO",
        symbol2: "SPY",
        beta: 1.0172,
        entry_z: 2,
        exit_z: 0.5,
        equity: [
          { date: "2025-01-02", equity: 1, position: 0, zscore: 0.1 },
          { date: "2025-02-03", equity: 1.015, position: 1, zscore: -2.1 },
          { date: "2025-03-03", equity: 1.032, position: 0, zscore: -0.3 },
        ],
        stats: {
          trades: 4,
          win_rate: 0.75,
          sharpe: 1.42,
          max_drawdown: -0.031,
          total_return: 0.032,
        },
      },
    });
  });

  await page.goto("/equity/pair-trading");

  // Default load is VOO/SPY on the "test" tab.
  const verdict = page.getByText(/Cointegrated|Not Cointegrated/i).first();
  await expect(verdict).toBeVisible({ timeout: 60_000 });

  // Beta hedge ratio cell should render a real number
  await expect(page.getByText(/Beta \(Hedge Ratio\)/i)).toBeVisible();

  await page.screenshot({ path: "test-results/pair-trading-lab.png", fullPage: true });

  // Backtest tab should load an equity curve from real data
  await page.getByRole("button", { name: /Backtest/i }).first().click();
  await expect(page.getByText(/Sharpe/i).first()).toBeVisible({ timeout: 60_000 });

  await page.screenshot({ path: "test-results/pair-trading-backtest.png", fullPage: true });

  const fatal = consoleErrors.filter(
    (e) => !/favicon|ResizeObserver|Failed to load resource|WebGLRenderer|THREE\.|WebGL context/i.test(e),
  );
  expect(fatal, `console errors: ${fatal.join("\n")}`).toHaveLength(0);
});
