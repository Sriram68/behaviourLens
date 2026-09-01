const { test, expect } = require("@playwright/test");
const { waitForIngest } = require("./helpers");

test("login and onboarding flow reaches the live ingestion endpoint", async ({ page }) => {
  await page.goto("/demo-app/index.html");

  const ingestPromise = waitForIngest(page);

  await page.fill("#email", "e2e-test@psiog.com");
  await page.fill("#password", "e2e-test-pass");
  await page.click("#login-form button[type=submit]");

  const log = page.locator("#log");
  await expect(log).toContainText("login_attempt");
  await expect(log).toContainText("login_success", { timeout: 5000 });

  await page.click('#onboarding button[data-step="1"]');
  await page.click('#onboarding button[data-step="2"]');
  await page.click('#onboarding button[data-step="3"]');
  await expect(log).toContainText("onboarding_step_complete");

  // 5 events fired, batchSize is 3, so a flush has already happened by now.
  const response = await ingestPromise;
  expect(response.status()).toBe(200);
});
