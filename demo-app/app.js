BehaviorLens.init({
  endpoint: "https://func-behaviorlens47209.azurewebsites.net/api/events",
  batchSize: 3,
  flushIntervalMs: 3000,
});

const logEl = document.getElementById("log");

function fire(eventName, properties) {
  BehaviorLens.track(eventName, properties);
  const line = document.createElement("div");
  line.textContent = `[${new Date().toLocaleTimeString()}] ${eventName} ${JSON.stringify(properties || {})}`;
  logEl.prepend(line);
}

document.getElementById("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const email = document.getElementById("email").value;
  fire("login_attempt", { email });
  setTimeout(() => {
    fire("login_success", { email });
    document.getElementById("onboarding").style.display = "block";
  }, 400);
});

document.querySelectorAll("#onboarding button").forEach((btn) => {
  btn.addEventListener("click", () => {
    fire("onboarding_step_complete", { step: Number(btn.dataset.step) });
  });
});

document.getElementById("feature-click").addEventListener("click", () => {
  fire("feature_click", { feature: "export_report" });
});

document.getElementById("search-btn").addEventListener("click", () => {
  const query = document.getElementById("search-input").value;
  fire("search_query", { query });
});

document.getElementById("filter-select").addEventListener("change", (e) => {
  if (!e.target.value) return;
  fire("filter_applied", { filter: e.target.value });
});

document.getElementById("export-btn").addEventListener("click", () => {
  fire("export_triggered", { format: "csv" });
});

document.getElementById("api-error-btn").addEventListener("click", () => {
  fire("api_error", { code: 500, endpoint: "/api/reports" });
});

document.getElementById("not-found-btn").addEventListener("click", () => {
  fire("page_not_found", { path: "/nonexistent-page" });
});

document.getElementById("retry-btn").addEventListener("click", () => {
  fire("retry_attempt", { originalEvent: "api_error" });
});
