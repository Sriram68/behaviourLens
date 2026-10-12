const { test, expect } = require("@playwright/test");
const { waitForIngest, signedIn, appUrl } = require("./helpers");

test("core feature usage flow reaches the live ingestion endpoint", async ({ page }) => {
  await signedIn(page);
  await page.goto(appUrl("/demo-app/index.html?exportFailRate=0#/reports"));

  const ingestPromise = waitForIngest(page);

  await page.fill("#search-input", "revenue");
  await page.click("#search-btn");
  await expect(page.locator("#search-summary")).toContainText("1 report(s)");
  await page.selectOption("#filter-select", "last_7_days");
  await page.click("#export-btn");
  await expect(page.locator("#toast")).toContainText("exported as CSV");
  // Declarative tracking: no JS handler, just data-bl-event attributes.
  await page.click("text=Open report builder");

  const log = page.locator("#log");
  await expect(log).toContainText("page_view");
  await expect(log).toContainText('search_query {"query":"revenue","results":1}');
  await expect(log).toContainText("filter_applied");
  await expect(log).toContainText("export_triggered");
  await expect(log).toContainText('feature_click {"feature":"reports"}');

  const response = await ingestPromise;
  expect(response.status()).toBe(200);
});
