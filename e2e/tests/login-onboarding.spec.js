const { test, expect } = require("@playwright/test");
const { waitForIngest, appUrl } = require("./helpers");

test("login and onboarding flow reaches the live ingestion endpoint", async ({ page }) => {
  await page.goto(appUrl("/demo-app/index.html"));

  const ingestPromise = waitForIngest(page);

  await page.fill("#email", "e2e-test@psiog.com");
  await page.fill("#password", "e2e-test-pass");
  await page.click("#login-form button[type=submit]");

  const log = page.locator("#log");
  await expect(log).toContainText("login_attempt");
  await expect(log).toContainText("login_success", { timeout: 5000 });

  for (const step of [1, 2, 3]) {
    await page.click(`#onboarding [data-step="${step}"]`);
  }
  await expect(log).toContainText("onboarding_step_complete");
  await expect(page).toHaveURL(/#\/dashboard$/);

  const response = await ingestPromise;
  expect(response.status()).toBe(200);

  // Privacy: the email typed into the form never leaves the browser.
  await expect(log).not.toContainText("e2e-test@psiog.com");
  expect(response.request().postData()).not.toContain("e2e-test@psiog.com");
});
