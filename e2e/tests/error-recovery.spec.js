const { test, expect } = require("@playwright/test");
const { waitForIngest, signedIn, appUrl } = require("./helpers");

test("error and recovery flow reaches the live ingestion endpoint", async ({ page }) => {
  await signedIn(page);
  // Force the release-2.4.0 export failure and the share-link bug.
  await page.goto(appUrl("/demo-app/index.html?exportFailRate=1&shareBug=1#/reports"));

  const ingestPromise = waitForIngest(page);

  await page.click("#export-btn");
  await expect(page.locator("#toast")).toContainText("502 Bad Gateway");
  await page.click("#toast-action");
  await page.click("#share-btn");
  await page.click("text=Help center");
  await expect(page.locator("h1")).toContainText("Page not found");

  const log = page.locator("#log");
  await expect(log).toContainText('api_error {"code":502,"endpoint":"/api/export"}');
  await expect(log).toContainText("retry_attempt");
  // Captured automatically by the SDK from the uncaught TypeError.
  await expect(log).toContainText("js_error");
  await expect(log).toContainText("Cannot read properties of undefined");
  await expect(log).toContainText('page_not_found {"path":"/help/getting-started"}');

  const response = await ingestPromise;
  expect(response.status()).toBe(200);
});
