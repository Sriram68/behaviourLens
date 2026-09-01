/** Waits for a successful POST to the live ingestion endpoint, proving the
 * event(s) made it past the browser, through the SDK's batching, and were
 * accepted by the deployed Azure Function -- not just logged in the DOM. */
async function waitForIngest(page, { timeout = 10000 } = {}) {
  const response = await page.waitForResponse(
    (res) => res.url().includes("/api/events") && res.request().method() === "POST",
    { timeout }
  );
  return response;
}

module.exports = { waitForIngest };
