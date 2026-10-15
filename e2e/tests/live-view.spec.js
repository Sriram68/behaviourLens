const { test, expect } = require("@playwright/test");

// The live screen is tested against a mocked API so it's deterministic and
// independent of the deployed Function; the ingestion specs cover the real path.
const NOW = new Date("2026-10-12T10:30:00Z");

function minuteSeries() {
  return Array.from({ length: 31 }, (_, i) => ({
    minute: new Date(NOW.getTime() - (30 - i) * 60000).toISOString(),
    count: i > 24 ? i - 22 : 0,
  }));
}

const STATS = {
  generatedAt: NOW.toISOString(),
  windowMinutes: 30,
  totals: { events: 42, sessions: 6, users: 6, errors: 9 },
  byEvent: { page_view: 14, feature_click: 8, export_triggered: 5, api_error: 4, js_error: 2, page_not_found: 1 },
  byFlow: { Navigation: 14, "Feature Usage": 15, "Error & Recovery": 9, "Login & Onboarding": 4 },
  perMinute: minuteSeries(),
  exports: { attempts: 5, retries: 3, failures: 4 },
  recent: [
    {
      eventName: "api_error",
      timestamp: NOW.toISOString(),
      path: "/behaviourLens/demo-app/index.html#/reports",
      detail: { code: 502, endpoint: "/api/export" },
      session: "abcd1234",
    },
    {
      eventName: "page_view",
      timestamp: NOW.toISOString(),
      path: "/behaviourLens/demo-app/index.html#/reports",
      detail: { path: "/behaviourLens/demo-app/index.html#/reports" },
      session: "abcd1234",
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/stats**", (route) => route.fulfill({ json: STATS }));
});

test("live view renders totals, the incident tile, charts and the feed", async ({ page }) => {
  await page.goto("/demo-app/live.html?api=http://mock.local/api");

  await expect(page.locator("#t-events")).toHaveText("42");
  await expect(page.locator("#t-sessions")).toHaveText("6");
  await expect(page.locator("#t-export")).toHaveText("50%");
  await expect(page.locator("#t-export-note")).toContainText("Incident: 4 of 8 export calls failed");
  await expect(page.locator("#export-tile")).toHaveClass(/status-critical/);
  await expect(page.locator("#t-features")).toHaveText("13");

  await expect(page.locator("#per-minute path.bar")).toHaveCount(6);
  await expect(page.locator("#by-flow .bar-row")).toHaveCount(4);
  await expect(page.locator("#by-flow .bar-row").first()).toContainText("Login & Onboarding");

  const firstRow = page.locator("#feed tr").first();
  await expect(firstRow).toContainText("api_error");
  await expect(firstRow).toContainText("code: 502 · endpoint: /api/export");
  await expect(page.locator("#live-text")).toContainText("Live");
  await expect(page.locator("#app-url")).toHaveText(/\/demo-app\/index\.html$/);
});

test("live view dims and flags a lost connection instead of blanking", async ({ page }) => {
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await expect(page.locator("#t-events")).toHaveText("42");
  await page.route("**/api/stats**", (route) => route.fulfill({ status: 503, body: "down" }));
  await expect(page.locator("#live-text")).toContainText("Connection lost", { timeout: 8000 });
  await expect(page.locator("#t-events")).toHaveText("42");
});

test("Ask shows a fact-checked answer with its SQL", async ({ page }) => {
  await page.route("**/api/ask", (route) =>
    route.fulfill({
      json: {
        question: "Which feature is used least?",
        sql: "SELECT feature, SUM(event_count) AS uses FROM feature_usage_daily GROUP BY feature ORDER BY uses LIMIT 100",
        columns: ["feature", "uses"],
        rows: [["scheduled_reports", "101"], ["share_link", "396"]],
        answer: "Scheduled reports is the least-used feature with 101 uses, against 396 for share links.",
        factCheck: { total: 2, verified: 2, unverified: [] },
      },
    })
  );
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.click("text=Which feature is used least?");

  await expect(page.locator("#answer")).toContainText("Scheduled reports is the least-used feature");
  await expect(page.locator("#answer .verdict")).toHaveText("✓ All 2 numbers verified against the query result");
  await page.click("text=SQL and result");
  await expect(page.locator("#answer pre")).toContainText("FROM feature_usage_daily");
  await expect(page.locator("#answer table")).toContainText("scheduled_reports");
});

test("Ask shows when the model declines and runs nothing", async ({ page }) => {
  await page.route("**/api/ask", (route) =>
    route.fulfill({
      json: {
        question: "Delete all the sessions",
        sql: null,
        columns: [],
        rows: [],
        answer: "Data modification statements like DELETE are not permitted.",
        declined: true,
        factCheck: { total: 0, verified: 0, unverified: [] },
      },
    })
  );
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.fill("#ask-input", "Delete all the sessions");
  await page.click("#ask-btn");

  await expect(page.locator("#answer")).toContainText("not permitted");
  await expect(page.locator("#answer .verdict")).toHaveText("⛔ Declined: no query was run");
  await expect(page.locator("#answer pre")).toHaveCount(0);
});

test("Ask surfaces the SQL guardrail when a question asks for something unsafe", async ({ page }) => {
  await page.route("**/api/ask", (route) =>
    route.fulfill({
      status: 422,
      json: { error: "Blocked by SQL guardrail: Only SELECT queries are allowed", sql: "DELETE FROM session_summary" },
    })
  );
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.fill("#ask-input", "Delete all the sessions");
  await page.click("#ask-btn");

  await expect(page.locator("#answer .verdict")).toContainText("Blocked by SQL guardrail");
  await expect(page.locator("#answer pre")).toHaveText("DELETE FROM session_summary");
});

test("Send report to Teams posts through the Function and confirms", async ({ page }) => {
  let posted = 0;
  await page.route("**/api/teams/report", (route) => {
    posted += 1;
    return route.fulfill({ json: { status: "sent", includedInsight: true, totals: { events: 42 } } });
  });
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.click("#teams-btn");
  await expect(page.locator("#teams-status")).toHaveText("✓ Sent to Teams (with AI insight)");
  await expect(page.locator("#teams-btn")).toBeEnabled();
  expect(posted).toBe(1);
});

test("Send report to Teams shows the cooldown message instead of failing silently", async ({ page }) => {
  await page.route("**/api/teams/report", (route) =>
    route.fulfill({ status: 429, json: { error: "A report was just sent. Try again in 12 s." } })
  );
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.click("#teams-btn");
  await expect(page.locator("#teams-status")).toHaveText("⚠ A report was just sent. Try again in 12 s.");
});
