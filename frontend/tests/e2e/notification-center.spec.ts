import { expect, test } from "@playwright/test";

function makeJwt(payload: Record<string, unknown>): string {
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `x.${encoded}.y`;
}

test("notification center opens from the top bar", async ({ page, request }) => {
  const notificationTitle = `Alert: AAPL price_above ${Date.now()}`;
  const accessToken = makeJwt({
    sub: "e2e-user",
    email: "e2e@example.com",
    role: "trader",
    exp: Math.floor(Date.now() / 1000) + 3600,
  });

  const createResponse = await request.post(`http://127.0.0.1:${process.env.E2E_BACKEND_PORT || "8010"}/api/notifications`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    data: {
      type: "alert",
      title: notificationTitle,
      body: "AAPL crossed the configured threshold",
      ticker: "AAPL",
      action_url: "/equity/alerts",
      priority: "high",
    },
  });
  expect(createResponse.status(), await createResponse.text()).toBe(201);

  await page.goto("/", { waitUntil: "domcontentloaded" });

  const bell = page.getByRole("button", { name: "Notifications" });
  await expect(bell).toBeVisible();
  await bell.click();

  const panel = page.getByTestId("notification-panel");
  const notificationItem = panel.getByRole("button", { name: new RegExp(notificationTitle) }).first();
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("button", { name: "All", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Alerts" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "News" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "System" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Trades" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Mark all read" })).toBeVisible();
  await expect(panel.getByText(notificationTitle, { exact: true })).toBeVisible();
  await expect(notificationItem).toContainText(/ago|about/i);

  await page.mouse.click(20, 20);
  await expect(panel).toBeHidden();
});
