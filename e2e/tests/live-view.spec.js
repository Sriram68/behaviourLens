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

test("Run pipeline asks for the presenter key, then shows each task's progress", async ({ page }) => {
  const keys = [];
  await page.route("**/api/pipeline/run", (route) => {
    keys.push(route.request().headers()["x-presenter-key"]);
    return route.fulfill({ json: { status: "started", runId: 7, synced: { uploaded: 3, failed: 0, alreadySynced: 9 } } });
  });
  let polls = 0;
  await page.route("**/api/pipeline/status**", (route) => {
    if (!route.request().url().includes("run_id=")) {
      return route.fulfill({ status: 400, json: { error: "run_id required" } }); // the key check
    }
    polls += 1;
    const running = polls === 1;
    return route.fulfill({
      json: {
        runId: 7,
        state: running ? "RUNNING" : "TERMINATED",
        result: running ? null : "SUCCESS",
        startTime: Date.now() - 65000,
        endTime: running ? null : Date.now(),
        tasks: [
          { key: "ingestion", state: "SUCCESS" },
          { key: "aggregations", state: running ? "RUNNING" : "SUCCESS" },
          { key: "data_quality", state: running ? "PENDING" : "SUCCESS" },
          { key: "ai_insights", state: running ? "PENDING" : "SUCCESS" },
        ],
      },
    });
  });
  await page.goto("/demo-app/live.html?api=http://mock.local/api");

  await expect(page.locator("#presenter-key")).toBeHidden();
  await page.click("#pipeline-btn");
  await expect(page.locator("#presenter-key")).toBeVisible();
  await page.fill("#presenter-key", "lens-test");
  await page.press("#presenter-key", "Enter");
  await expect(page.locator("#pipeline-msg")).toHaveText("✓ Presenter key saved for this tab");
  await expect(page.locator("#presenter-key")).toBeHidden();
  expect(keys).toEqual([]); // saving the key starts nothing

  await page.click("#pipeline-btn");
  await expect(page.locator("#pipeline-title")).toHaveText("Pipeline run 7");
  await expect(page.locator("#pipeline-note")).toHaveText("3 new event files synced to Databricks");
  await expect(page.locator('.step[data-task="ingestion"]')).toHaveClass(/done/);
  await expect(page.locator('.step[data-task="aggregations"]')).toHaveClass(/running/);
  await expect(page.locator("#pipeline-btn")).toBeDisabled();
  await expect(page.locator("#presenter-key")).toBeHidden();
  expect(keys).toEqual(["lens-test"]);

  await expect(page.locator('.step[data-task="ai_insights"]')).toHaveClass(/done/, { timeout: 12000 });
  await expect(page.locator("#pipeline-note")).toContainText("✓ Finished");
  await expect(page.locator("#pipeline-btn")).toBeEnabled();
});

test("Run pipeline says when a run is already in progress and shows that run", async ({ page }) => {
  await page.route("**/api/pipeline/status**", (route) =>
    route.request().url().includes("run_id=41")
      ? route.fulfill({ json: { runId: 41, state: "RUNNING", tasks: [{ key: "ingestion", state: "RUNNING" }] } })
      : route.fulfill({ status: 400, json: { error: "run_id required" } })
  );
  await page.route("**/api/pipeline/run", (route) => route.fulfill({ status: 409, json: { status: "running", runId: 41 } }));
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.click("#pipeline-btn");
  await page.fill("#presenter-key", "lens-test");
  await page.press("#presenter-key", "Enter");
  await page.click("#pipeline-btn");
  await expect(page.locator("#pipeline-msg")).toHaveText("⚠ The pipeline is already running");
  await expect(page.locator("#pipeline-title")).toHaveText("Pipeline run 41");
  await expect(page.locator('.step[data-task="ingestion"]')).toHaveClass(/running/);
  await expect(page.locator("#pipeline-btn")).toBeDisabled();
});

test("The theme button switches between light and dark with a sun/moon icon", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  const button = page.locator("#theme-toggle");
  const html = page.locator("html");
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect(button).toHaveAttribute("aria-label", "Switch to dark theme");
  await expect(button.locator("svg")).toHaveCount(1);
  await button.click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(button).toHaveAttribute("aria-label", "Switch to light theme");
  await button.click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await button.click();
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark"); // remembered over the light system setting
});

test("Release health shows the verdict, the user cost and the verified explanation", async ({ page }) => {
  await page.route("**/api/release/health", (route) =>
    route.fulfill({
      json: {
        level: "rollback",
        headline: "Roll back or hotfix 2.4.0",
        release: { version: "2.4.0", date: "2026-10-11" },
        firstFlaggedDay: "2026-10-12",
        exportFailureBeforePct: 1.8,
        exportFailureSincePct: 46.1,
        exportFailureRatio: 25.6,
        sessionsHit: 127,
        sessionsAbandoned: 77,
        abandonedPct: 60.6,
        errorSharePct: 100.0,
        topCause: { event: "api_error", code: 502, endpoint: "/api/export", share_pct: 58.3 },
        narrative: "Release 2.4.0 broke exports.",
        status: "verified",
        factsVerified: 11,
        factsTotal: 11,
        generatedAt: "2026-10-16T06:37:44.359Z",
      },
    })
  );
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await expect(page.locator("#release")).toBeVisible();
  await expect(page.locator("#rel-badge")).toHaveText("ROLL BACK");
  await expect(page.locator("#rel-title")).toHaveText("Roll back or hotfix 2.4.0 · release 2.4.0, shipped 2026-10-11");
  await expect(page.locator("#rel-exports")).toHaveText("1.8% → 46.1%");
  await expect(page.locator("#rel-abandoned")).toHaveText("77");
  await expect(page.locator("#rel-cause")).toHaveText("502 on /api/export");
  await expect(page.locator("#rel-verified")).toContainText("✓ 11/11 numbers verified");
  await expect(page.locator("#rel-narrative")).toContainText("Release 2.4.0 broke exports.");
});

test("Release health stays hidden until there is data", async ({ page }) => {
  await page.route("**/api/release/health", (route) => route.fulfill({ status: 503, json: { error: "not yet" } }));
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.waitForTimeout(500);
  await expect(page.locator("#release")).toBeHidden();
});

test("The ReportHub link under the QR code is clickable", async ({ page }) => {
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  const link = page.locator("#app-url");
  await expect(link).toHaveAttribute("href", /demo-app\/index\.html$/);
  await expect(link).toHaveAttribute("target", "_blank");
});

test("Run pipeline rejects a wrong presenter key and asks again", async ({ page }) => {
  let runs = 0;
  await page.route("**/api/pipeline/status**", (route) => route.fulfill({ status: 401, json: { error: "Presenter key required" } }));
  await page.route("**/api/pipeline/run", (route) => {
    runs += 1;
    return route.fulfill({ status: 401, json: { error: "Presenter key required" } });
  });
  await page.goto("/demo-app/live.html?api=http://mock.local/api");
  await page.click("#pipeline-btn");
  await page.fill("#presenter-key", "guess");
  await page.press("#presenter-key", "Enter");
  await expect(page.locator("#pipeline-msg")).toHaveText("⚠ Wrong presenter key");
  await expect(page.locator("#presenter-key")).toBeVisible();
  await expect(page.locator("#pipeline-wrap")).toBeHidden();
  expect(runs).toBe(0);
});
