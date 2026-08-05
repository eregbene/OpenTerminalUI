import { expect, test } from "@playwright/test";

const backendPort = process.env.E2E_BACKEND_PORT || "8010";

test("Bensim Trading identity is visible on login and app shell", async ({ page }) => {
  await page.goto("/login", { waitUntil: "domcontentloaded" });

  await expect(page).toHaveTitle(/Bensim Trading/i);
  await expect(page.getByRole("heading", { name: /Bensim Trading/i })).toBeVisible();
  await expect(page.getByAltText(/Bensim Trading/i).first()).toBeVisible();

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByAltText(/Bensim Trading/i).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Bensim Trading Home", exact: true })).toBeVisible();
});

test("API documentation exposes Bensim Trading API title", async ({ page }) => {
  await page.goto(`http://127.0.0.1:${backendPort}/docs`, { waitUntil: "domcontentloaded" });

  await expect(page.getByText(/Bensim Trading API/i).first()).toBeVisible();
});
