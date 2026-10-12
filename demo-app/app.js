// ReportHub: a small internal reporting app instrumented with BehaviorLens.
// It reproduces the same behaviour as the simulated history, so live clicks
// extend the story: release 2.4.0 broke exports (~45% fail with a 502) and
// Share Link has a JavaScript bug. Query parameters make both deterministic
// for tests: ?exportFailRate=0..1 and ?shareBug=0..1.

const params = new URLSearchParams(window.location.search);
const APP_VERSION = "2.4.0";
const EXPORT_FAIL_RATE = params.has("exportFailRate") ? Number(params.get("exportFailRate")) : 0.45;
const SHARE_BUG_RATE = params.has("shareBug") ? Number(params.get("shareBug")) : 0.3;
const ENDPOINT = params.get("api") || "https://func-behaviorlens47209.azurewebsites.net/api/events";

const logEl = document.getElementById("log");
const debugEl = document.getElementById("debug");
const countEl = document.getElementById("debug-count");
let eventCount = 0;

BehaviorLens.init({
  endpoint: ENDPOINT,
  appVersion: APP_VERSION,
  batchSize: 5,
  flushIntervalMs: 2000,
  autoCapture: true, // page views, data-bl-event clicks, uncaught JS errors
  onEvent(event) {
    eventCount += 1;
    countEl.textContent = String(eventCount);
    const line = document.createElement("div");
    const time = new Date(event.timestamp).toLocaleTimeString();
    line.textContent = `[${time}] ${event.eventName} ${JSON.stringify(event.properties)}`;
    logEl.prepend(line);
  },
});

const track = (name, props) => BehaviorLens.track(name, props);

document.getElementById("debug-toggle").addEventListener("click", () => {
  const collapsed = debugEl.classList.toggle("collapsed");
  document.getElementById("debug-toggle").setAttribute("aria-expanded", String(!collapsed));
});
if (window.matchMedia("(max-width: 760px)").matches) debugEl.classList.add("collapsed");

// --- State --------------------------------------------------------------------

const store = {
  get loggedIn() {
    return sessionStorage.getItem("rh_logged_in") === "1";
  },
  set loggedIn(v) {
    v ? sessionStorage.setItem("rh_logged_in", "1") : sessionStorage.removeItem("rh_logged_in");
  },
  get onboardedStep() {
    return Number(localStorage.getItem("rh_onboarded_step") || 0);
  },
  set onboardedStep(v) {
    localStorage.setItem("rh_onboarded_step", String(v));
  },
};

const REPORTS = [
  { name: "Revenue by region", owner: "Finance", updated: "Today" },
  { name: "Active users (weekly)", owner: "Product", updated: "Yesterday" },
  { name: "Weekly errors", owner: "Engineering", updated: "Today" },
  { name: "Churn cohort analysis", owner: "Product", updated: "3 days ago" },
  { name: "Export history", owner: "Ops", updated: "Last week" },
  { name: "Support ticket volume", owner: "Support", updated: "Today" },
];

// --- Rendering helpers -------------------------------------------------------------

const root = document.getElementById("root");

function el(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content;
}

function shell(active, content) {
  const link = (route, label) =>
    `<a href="#/${route}" class="${active === route ? "active" : ""}">${label}</a>`;
  root.replaceChildren(
    el(`
    <header class="topbar">
      <span class="brand">ReportHub</span><span class="badge">v${APP_VERSION}</span>
      <span class="spacer"></span>
      <button id="logout" class="link">Sign out</button>
    </header>
    <div class="layout">
      <nav class="nav">
        ${link("dashboard", "Dashboard")}
        ${link("reports", "Reports")}
        ${link("saved-views", "Saved views")}
        ${link("scheduled-reports", "Scheduled reports")}
        ${link("settings", "Settings")}
        <a class="help" href="#/help/getting-started">Help center</a>
      </nav>
      <main id="view"></main>
    </div>`)
  );
  document.getElementById("view").replaceChildren(el(content));
  document.getElementById("logout").addEventListener("click", () => {
    store.loggedIn = false;
    location.hash = "#/login";
  });
}

let toastTimer;
function toast(text, { error = false, action, onAction } = {}) {
  const box = document.getElementById("toast");
  const button = document.getElementById("toast-action");
  document.getElementById("toast-text").textContent = text;
  box.className = `toast show${error ? " error" : ""}`;
  button.hidden = !action;
  button.onclick = () => {
    box.className = "toast";
    onAction();
  };
  if (action) button.textContent = action;
  clearTimeout(toastTimer);
  if (!action) toastTimer = setTimeout(() => (box.className = "toast"), 2600);
}

// --- Views -----------------------------------------------------------------

function loginView() {
  root.replaceChildren(
    el(`
    <div class="auth">
      <div class="card">
        <h1>Sign in to ReportHub</h1>
        <p class="muted">Psiog's internal reporting workspace</p>
        <form id="login-form">
          <input type="email" id="email" placeholder="Work email" value="demo@psiog.com" required autocomplete="off" />
          <input type="password" id="password" placeholder="Password" value="demopass" required />
          <button type="submit" class="primary">Sign in</button>
          <button type="button" id="sso-btn">Continue with SSO</button>
        </form>
        <p class="notice">Demo app instrumented with BehaviorLens. Your clicks are anonymous (no names or emails are sent) and appear on the live screen.</p>
      </div>
    </div>`)
  );
  const signIn = (method) => {
    // Only the sign-in method is tracked; the email address never leaves the page.
    track("login_attempt", { method });
    setTimeout(() => {
      track("login_success", { method });
      store.loggedIn = true;
      location.hash = store.onboardedStep >= 3 ? "#/dashboard" : "#/onboarding";
    }, 400);
  };
  document.getElementById("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    signIn("password");
  });
  document.getElementById("sso-btn").addEventListener("click", () => signIn("sso"));
}

const ONBOARDING = [
  { title: "Set up your profile", body: "Tell us your role so we can suggest the right reports.", cta: "Continue" },
  { title: "Pick your team", body: "We'll pin your team's shared reports to your dashboard.", cta: "Continue" },
  {
    title: "Connect a data source",
    body: "Paste a connection string for your warehouse, or upload a CSV to get started.",
    cta: "Connect data source",
  },
];

function onboardingView() {
  const step = Math.min(store.onboardedStep + 1, 3);
  const current = ONBOARDING[step - 1];
  const bars = [1, 2, 3].map((n) => `<span class="${n < step ? "done" : ""}"></span>`).join("");
  root.replaceChildren(
    el(`
    <div class="auth" id="onboarding">
      <div class="card">
        <p class="muted">Step ${step} of 3</p>
        <div class="steps">${bars}</div>
        <h1>${current.title}</h1>
        <p class="muted">${current.body}</p>
        ${step === 3 ? '<input placeholder="Server=...;Database=...;" style="margin-bottom:0.75rem" />' : ""}
        <div class="row">
          <button class="primary" data-step="${step}">${current.cta}</button>
          ${step === 3 ? '<button id="skip-onboarding" class="link">Skip for now</button>' : ""}
        </div>
      </div>
    </div>`)
  );
  root.querySelector(`[data-step="${step}"]`).addEventListener("click", () => {
    track("onboarding_step_complete", { step });
    store.onboardedStep = step;
    step === 3 ? (location.hash = "#/dashboard") : render();
  });
  const skip = document.getElementById("skip-onboarding");
  if (skip) skip.addEventListener("click", () => (location.hash = "#/dashboard"));
}

function dashboardView() {
  shell(
    "dashboard",
    `
    <h1>Good morning</h1>
    <p class="muted">Here's what your team looked at this week.</p>
    <div class="kpis">
      <div class="kpi"><div class="muted">Reports viewed</div><div class="value">128</div></div>
      <div class="kpi"><div class="muted">Exports</div><div class="value">42</div></div>
      <div class="kpi"><div class="muted">Saved views</div><div class="value">9</div></div>
    </div>
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <h2 style="margin:0">Pinned reports</h2>
        <button data-bl-event="feature_click" data-bl-prop-feature="dashboard">Customize dashboard</button>
      </div>
      <table><tbody>
        ${REPORTS.slice(0, 3).map((r) => `<tr><td>${r.name}</td><td class="muted">${r.updated}</td></tr>`).join("")}
      </tbody></table>
    </div>`
  );
}

function reportsView() {
  shell(
    "reports",
    `
    <h1>Reports</h1>
    <div class="card">
      <div class="row">
        <input type="search" id="search-input" placeholder="Search reports" />
        <button id="search-btn">Search</button>
        <select id="filter-select">
          <option value="">Filter…</option>
          <option value="last_7_days">Last 7 days</option>
          <option value="last_30_days">Last 30 days</option>
          <option value="my_team">My team</option>
          <option value="errors_only">Errors only</option>
        </select>
      </div>
      <p id="search-summary" class="muted"></p>
      <table>
        <thead><tr><th>Report</th><th>Owner</th><th>Updated</th></tr></thead>
        <tbody id="report-rows"></tbody>
      </table>
    </div>
    <div class="card">
      <h2>Share or export</h2>
      <div class="row">
        <button id="export-btn" class="primary">Export CSV</button>
        <button id="export-pdf-btn">Export PDF</button>
        <button id="share-btn" data-bl-event="feature_click" data-bl-prop-feature="share_link">Copy share link</button>
        <button data-bl-event="feature_click" data-bl-prop-feature="reports">Open report builder</button>
      </div>
    </div>`
  );

  const rows = document.getElementById("report-rows");
  const showReports = (list) => {
    rows.replaceChildren();
    list.forEach((r) => {
      const tr = document.createElement("tr");
      [r.name, r.owner, r.updated].forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      rows.appendChild(tr);
    });
  };
  showReports(REPORTS);

  const search = () => {
    const query = document.getElementById("search-input").value.trim();
    const matches = REPORTS.filter((r) => r.name.toLowerCase().includes(query.toLowerCase()));
    track("search_query", { query, results: matches.length });
    document.getElementById("search-summary").textContent = matches.length
      ? `${matches.length} report(s) match "${query}"`
      : `No reports match "${query}"`;
    showReports(matches);
  };
  document.getElementById("search-btn").addEventListener("click", search);
  document.getElementById("search-input").addEventListener("keydown", (e) => e.key === "Enter" && search());
  document.getElementById("filter-select").addEventListener("change", (e) => {
    if (e.target.value) track("filter_applied", { filter: e.target.value });
  });
  document.getElementById("export-btn").addEventListener("click", () => runExport("csv"));
  document.getElementById("export-pdf-btn").addEventListener("click", () => runExport("pdf"));
  document.getElementById("share-btn").addEventListener("click", () => {
    if (Math.random() < SHARE_BUG_RATE) {
      // The real bug behind the js_error spike: the share target is sometimes missing.
      const target = undefined;
      toast(`Link copied: ${target.url}`);
    }
    toast("Share link copied to clipboard");
  });
}

function runExport(format, isRetry = false) {
  if (!isRetry) track("export_triggered", { format });
  toast(`Preparing ${format.toUpperCase()} export…`);
  setTimeout(() => {
    if (Math.random() < EXPORT_FAIL_RATE) {
      track("api_error", { code: 502, endpoint: "/api/export" });
      toast("Export failed: 502 Bad Gateway", {
        error: true,
        action: "Retry",
        onAction: () => {
          track("retry_attempt", { originalEvent: "api_error", endpoint: "/api/export" });
          runExport(format, true);
        },
      });
    } else {
      toast(`Report exported as ${format.toUpperCase()}`);
    }
  }, 700);
}

function savedViewsView() {
  const views = ["Exec weekly summary", "EMEA pipeline", "My open incidents", "Q4 hiring"];
  shell(
    "saved-views",
    `
    <h1>Saved views</h1>
    <div class="grid-cards">
      ${views
        .map(
          (v) => `<div class="card"><h2>${v}</h2>
          <button data-bl-event="feature_click" data-bl-prop-feature="saved_views">Open view</button></div>`
        )
        .join("")}
    </div>`
  );
}

function scheduledReportsView() {
  shell(
    "scheduled-reports",
    `
    <h1>Scheduled reports</h1>
    <div class="card">
      <p class="muted">No schedules yet. Email any report to your team every Monday morning.</p>
      <button class="primary" data-bl-event="feature_click" data-bl-prop-feature="scheduled_reports">Create schedule</button>
    </div>`
  );
}

function settingsView() {
  const optedOut = localStorage.getItem("bl_opt_out") === "1";
  shell(
    "settings",
    `
    <h1>Settings</h1>
    <div class="card">
      <h2>Privacy</h2>
      <label class="row">
        <input type="checkbox" id="optout-toggle" style="width:auto;min-height:auto" ${optedOut ? "checked" : ""} />
        <span>Opt out of usage analytics on this device</span>
      </label>
      <p class="muted">BehaviorLens never collects names or email addresses, and masks any that slip into event data.</p>
    </div>`
  );
  document.getElementById("optout-toggle").addEventListener("change", (e) => {
    if (e.target.checked) {
      BehaviorLens.optOut();
      toast("Analytics turned off for this device");
    } else {
      BehaviorLens.optIn();
      toast("Analytics turned back on");
    }
  });
}

function notFoundView(path) {
  track("page_not_found", { path });
  shell(
    "",
    `
    <div class="card">
      <h1>Page not found</h1>
      <p class="muted">We couldn't find <code>${path.replace(/[<>&"]/g, "")}</code>.</p>
      <a href="#/dashboard">Back to dashboard</a>
    </div>`
  );
}

// --- Router ------------------------------------------------------------------

const ROUTES = {
  "/dashboard": dashboardView,
  "/reports": reportsView,
  "/saved-views": savedViewsView,
  "/scheduled-reports": scheduledReportsView,
  "/settings": settingsView,
};

function render() {
  const route = location.hash.replace(/^#/, "") || "/login";
  if (!store.loggedIn) return route === "/login" ? loginView() : (location.hash = "#/login");
  if (route === "/login") return (location.hash = store.onboardedStep >= 3 ? "#/dashboard" : "#/onboarding");
  if (route === "/onboarding") return onboardingView();
  if (ROUTES[route]) return ROUTES[route]();
  notFoundView(route);
}

window.addEventListener("hashchange", render);
render();
