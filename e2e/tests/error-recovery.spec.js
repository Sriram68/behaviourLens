const { test, expect } = require("@playwright/test");
const { waitForIngest } = require("./helpers");

test("error and recovery flow reaches the live ingestion endpoint", async ({ page }) => {
  await page.goto("/demo-app/index.html");

  const ingestPromise = waitForIngest(page);

  await page.click("#api-error-btn");
  await page.click("#not-found-btn");
  await page.click("#retry-btn");

  const log = page.locator("#log");
  await expect(log).toContainText("api_error");
  await expect(log).toContainText("page_not_found");
  await expect(log).toContainText("retry_attempt");

  // Exactly 3 events fired, matching batchSize, so a flush has already happened.
  const response = await ingestPromise;
  expect(response.status()).toBe(200);
});
