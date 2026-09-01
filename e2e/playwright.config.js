const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 30000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://localhost:5500",
    trace: "retain-on-failure",
  },
  webServer: {
    // Serves the whole repo root, matching the README's local-dev instructions,
    // so /demo-app/index.html resolves the same way here as it does for a human.
    command: "python -m http.server 5500 --directory ..",
    url: "http://localhost:5500/demo-app/index.html",
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
});
