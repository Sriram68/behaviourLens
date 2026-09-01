const { test, expect } = require("@playwright/test");
const { waitForIngest } = require("./helpers");

test("core feature usage flow reaches the live ingestion endpoint", async ({ page }) => {
  await page.goto("/demo-app/index.html");

  const ingestPromise = waitForIngest(page);

  await page.click("#feature-click");
  await page.fill("#search-input", "active users");
  await page.click("#search-btn");
  await page.selectOption("#filter-select", "last_7_days");
  await page.click("#export-btn");

  const log = page.locator("#log");
  await expect(log).toContainText("feature_click");
  await expect(log).toContainText("search_query");
  await expect(log).toContainText("filter_applied");
  await expect(log).toContainText("export_triggered");

  // 4 events fired, batchSize is 3, so a flush has already happened by now.
  const response = await ingestPromise;
  expect(response.status()).toBe(200);
});
