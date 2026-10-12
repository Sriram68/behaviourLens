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

/** Starts the page already signed in and onboarded, so a test can go
 * straight to the feature it covers. */
async function signedIn(page) {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("rh_logged_in", "1");
    window.localStorage.setItem("rh_onboarded_step", "3");
  });
}

/** Demo-app URL for a test. Set BEHAVIORLENS_API to aim the app at another
 * ingestion endpoint (e.g. a local `func start`) instead of the deployed one. */
function appUrl(path) {
  const api = process.env.BEHAVIORLENS_API;
  if (!api) return path;
  const [base, hash] = path.split("#");
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}api=${encodeURIComponent(api)}${hash ? `#${hash}` : ""}`;
}

module.exports = { waitForIngest, signedIn, appUrl };
