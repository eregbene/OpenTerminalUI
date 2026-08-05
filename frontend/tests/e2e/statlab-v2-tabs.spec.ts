import { test, expect } from "@playwright/test";

const NEW_TABS = ["Factor / CAPM", "Autocorrelation", "Causality", "Regimes"] as const;
const corsHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,OPTIONS",
  "access-control-allow-headers": "*",
};

test("Statistical Lab shows the 4 new statsmodels tabs", async ({ page }) => {
  await page.goto("/equity/stat-lab", { waitUntil: "domcontentloaded" });

  // Header renders
  await expect(page.getByRole("heading", { name: "Statistical Lab" })).toBeVisible({ timeout: 30_000 });

  // All four new tab buttons are present and clickable, each revealing a config panel.
  for (const label of NEW_TABS) {
    const tab = page.getByRole("button", { name: label, exact: true });
    await expect(tab).toBeVisible();
    await tab.click();
    // Each tab shows its run button + the empty-state placeholder before running.
    await expect(page.getByText(/Run analysis to see results/i).first()).toBeVisible({ timeout: 10_000 });
    await page.screenshot({ path: `test-results/statlab-${label.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.png`, fullPage: true });
  }
});

test("Factor / CAPM tab renders deterministic regression results", async ({ page }) => {
  await page.route("**/api/statlab/methods", async (route) => {
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      json: { forecast_methods: [{ id: "arima", label: "ARIMA" }] },
    });
  });
  await page.route("**/api/statlab/regression", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      json: {
        ticker: "RELIANCE",
        benchmark_ticker: "^NSEI",
        asset: "RELIANCE",
        benchmark: "^NSEI",
        alpha_daily: 0.0004,
        alpha_annual: 0.101,
        beta: 1.123,
        r_squared: 0.672,
        correlation: 0.82,
        tracking_error: 0.148,
        information_ratio: 0.68,
        alpha_tstat: 2.1,
        alpha_pvalue: 0.037,
        beta_tstat: 11.4,
        beta_pvalue: 0.001,
        n_obs: 252,
        rolling_window: 63,
        rolling_beta: [
          { date: "2025-01-02", beta: 1.02 },
          { date: "2025-02-03", beta: 1.11 },
          { date: "2025-03-03", beta: 1.16 },
        ],
        scatter: [
          { x: -1.2, y: -1.4 },
          { x: 0.4, y: 0.7 },
          { x: 1.1, y: 1.3 },
        ],
        fit_line: [
          { x: -1.5, y: -1.6 },
          { x: 1.5, y: 1.8 },
        ],
        interpretation: "Deterministic CAPM fixture for E2E validation.",
      },
    });
  });

  await page.goto("/equity/stat-lab", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Statistical Lab" })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "Factor / CAPM", exact: true }).click();
  // Click the tab's run button (its label contains Run / Regress / CAPM).
  const runBtn = page.getByRole("button", { name: /run|regress|capm|analyze/i }).last();
  await runBtn.click();

  // Beta stat should appear once the regression completes.
  await expect(page.getByText(/Beta/i).first()).toBeVisible({ timeout: 45_000 });
  await page.screenshot({ path: "test-results/statlab-capm-result.png", fullPage: true });
});
