// BehaviorLens Live: the presenter screen. Polls the Function's /api/stats for
// what ReportHub users (the audience) are doing right now, and hosts the
// "Ask BehaviorLens" box backed by /api/ask.
// ?api=<base> points it at another Function (e.g. http://localhost:7071/api);
// ?app=<url> overrides the URL encoded in the QR code.

const params = new URLSearchParams(window.location.search);
const API = (params.get("api") || "https://func-behaviorlens-psiddhi.azurewebsites.net/api").replace(/\/$/, "");
const APP_URL = params.get("app") || new URL("index.html", window.location.href).href.split(/[?#]/)[0];
const MINUTES = 30;
const POLL_MS = 3000;
const FLOW_ORDER = ["Login & Onboarding", "Feature Usage", "Error & Recovery", "Navigation"];
const FEATURE_EVENTS = ["feature_click", "search_query", "filter_applied", "export_triggered"];
const ERROR_EVENTS = new Set(["api_error", "page_not_found", "retry_attempt", "js_error"]);
const SUGGESTIONS = [
  "Which feature is used least?",
  "What is the export failure rate by app version?",
  "Where do new users drop off in onboarding?",
  "How many errors happened each day this week?",
];

const $ = (id) => document.getElementById(id);
const tooltip = $("tooltip");
let lastStats = null;
const seenFeedKeys = new Set();

// --- Theme ---------------------------------------------------------------------

function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
  $("theme-toggle").textContent = `Theme: ${theme || "system"}`;
}
let theme = null;
try {
  theme = localStorage.getItem("bl_live_theme");
} catch (e) {
  theme = null;
}
applyTheme(theme);
$("theme-toggle").addEventListener("click", () => {
  theme = theme === null ? "light" : theme === "light" ? "dark" : null;
  applyTheme(theme);
  try {
    theme ? localStorage.setItem("bl_live_theme", theme) : localStorage.removeItem("bl_live_theme");
  } catch (e) {
    // Theme choice just won't persist.
  }
  if (lastStats) renderColumns($("per-minute"), lastStats.perMinute);
});

// --- QR code ---------------------------------------------------------------------

$("app-url").textContent = APP_URL;
if (window.QRCode) {
  // eslint-disable-next-line no-new
  new QRCode($("qr"), {
    text: APP_URL,
    width: 220,
    height: 220,
    colorDark: "#000000",
    colorLight: "#ffffff",
    correctLevel: QRCode.CorrectLevel.M,
  });
} else {
  $("qr").textContent = "QR library unavailable: share the link below.";
}

// --- Helpers ---------------------------------------------------------------------

const fmt = new Intl.NumberFormat();
const localTime = (iso, withSeconds) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: withSeconds ? "2-digit" : undefined });

function svg(tag, attrs, parent) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (parent) parent.appendChild(node);
  return node;
}

function niceMax(value) {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [2, 4, 6, 8, 10]) {
    if (step * magnitude >= value) return step * magnitude;
  }
  return 10 * magnitude;
}

function showTooltip(event, value, label) {
  tooltip.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = value;
  const line = document.createElement("span");
  const key = document.createElement("span");
  key.className = "key";
  line.append(key, document.createTextNode(label));
  tooltip.append(strong, line);
  tooltip.style.display = "block";
  const rect = tooltip.getBoundingClientRect();
  const x = Math.min(event.clientX + 12, window.innerWidth - rect.width - 8);
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${Math.max(8, event.clientY - rect.height - 12)}px`;
}

const hideTooltip = () => (tooltip.style.display = "none");

// --- Column chart: events per minute ---------------------------------------------

function renderColumns(container, series) {
  container.replaceChildren();
  const width = container.clientWidth;
  const height = container.clientHeight;
  const m = { top: 10, right: 6, bottom: 24, left: 34 };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const max = Math.max(0, ...series.map((d) => d.count));
  const top = niceMax(max);
  const y = (v) => m.top + h - (v / top) * h;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" }, container);
  const style = getComputedStyle(document.documentElement);

  [0, top / 2, top].forEach((tick) => {
    svg("line", {
      x1: m.left, x2: width - m.right, y1: y(tick), y2: y(tick),
      stroke: tick === 0 ? style.getPropertyValue("--axis") : style.getPropertyValue("--grid"), "stroke-width": 1,
    }, root);
    const label = svg("text", { x: m.left - 6, y: y(tick) + 4, "text-anchor": "end", class: "tick" }, root);
    label.textContent = fmt.format(tick);
  });

  const band = w / series.length;
  const barWidth = Math.min(24, band * 0.72);
  series.forEach((d, i) => {
    const cx = m.left + band * i + band / 2;
    if (d.count > 0) {
      const x0 = cx - barWidth / 2;
      const x1 = cx + barWidth / 2;
      const yTop = y(d.count);
      const base = y(0);
      const r = Math.min(4, barWidth / 2, base - yTop);
      // Rounded data-end, square at the baseline.
      svg("path", {
        d: `M${x0},${base} V${yTop + r} Q${x0},${yTop} ${x0 + r},${yTop} H${x1 - r} Q${x1},${yTop} ${x1},${yTop + r} V${base} Z`,
        fill: style.getPropertyValue("--series-1"),
        class: "bar",
      }, root);
    }
    if (i % 5 === 0 || i === series.length - 1) {
      const label = svg("text", { x: cx, y: height - 6, "text-anchor": "middle", class: "tick" }, root);
      label.textContent = localTime(d.minute);
    }
    // Hit target: the whole band, not just the painted bar.
    const hit = svg("rect", {
      x: m.left + band * i, y: m.top, width: band, height: h, fill: "transparent", tabindex: 0,
      "aria-label": `${localTime(d.minute)}: ${d.count} events`,
    }, root);
    const text = `${fmt.format(d.count)} event${d.count === 1 ? "" : "s"}`;
    hit.addEventListener("pointermove", (e) => showTooltip(e, text, localTime(d.minute)));
    hit.addEventListener("pointerleave", hideTooltip);
    hit.addEventListener("focus", () => {
      const box = hit.getBoundingClientRect();
      showTooltip({ clientX: box.left + box.width / 2, clientY: box.top + 20 }, text, localTime(d.minute));
    });
    hit.addEventListener("blur", hideTooltip);
  });

  if (max === 0) {
    const empty = svg("text", { x: m.left + w / 2, y: m.top + h / 2, "text-anchor": "middle", class: "empty" }, root);
    empty.textContent = "Waiting for events: scan the QR code to start";
  }

  const table = $("per-minute-table");
  table.replaceChildren();
  series.filter((d) => d.count > 0).forEach((d) => {
    const tr = document.createElement("tr");
    [localTime(d.minute), fmt.format(d.count)].forEach((v, i) => {
      const td = document.createElement("td");
      td.textContent = v;
      if (i === 1) td.className = "num";
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
}

// --- Bars: events by flow -------------------------------------------------------------

function renderFlows(byFlow) {
  const container = $("by-flow");
  container.replaceChildren();
  const max = Math.max(1, ...FLOW_ORDER.map((f) => byFlow[f] || 0));
  FLOW_ORDER.forEach((flow) => {
    const value = byFlow[flow] || 0;
    const row = document.createElement("div");
    row.className = "bar-row";
    row.title = `${flow}: ${value} events`;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = flow;
    const track = document.createElement("span");
    track.className = "bar-track";
    const fill = document.createElement("span");
    fill.className = "bar-fill";
    fill.style.width = `${(value / max) * 85}%`;
    const label = document.createElement("span");
    label.className = "bar-value";
    label.textContent = fmt.format(value);
    track.append(fill, label);
    row.append(name, track);
    container.appendChild(row);
  });
}

// --- Tiles and feed ---------------------------------------------------------------

function setNote(id, text, status) {
  const note = $(id);
  note.textContent = text;
  const tile = note.closest(".tile");
  tile.classList.remove("status-good", "status-critical");
  if (status) tile.classList.add(`status-${status}`);
}

function renderTiles(stats) {
  const t = stats.totals;
  $("t-events").textContent = fmt.format(t.events);
  $("t-sessions").textContent = fmt.format(t.sessions);
  $("t-errors").textContent = fmt.format(t.errors);
  const e = stats.byEvent;
  setNote("t-errors-note", `${e.api_error || 0} API · ${e.js_error || 0} JS · ${e.page_not_found || 0} not found`);
  $("t-features").textContent = fmt.format(FEATURE_EVENTS.reduce((sum, name) => sum + (e[name] || 0), 0));

  const calls = stats.exports.attempts + stats.exports.retries;
  if (!calls) {
    $("t-export").textContent = "–";
    setNote("t-export-note", "no exports yet");
    return;
  }
  const rate = stats.exports.failures / calls;
  $("t-export").textContent = `${Math.round(rate * 100)}%`;
  const detail = `${stats.exports.failures} of ${calls} export calls failed`;
  if (rate >= 0.2 && stats.exports.failures >= 2) setNote("t-export-note", `⚠ Incident: ${detail}`, "critical");
  else if (rate < 0.05) setNote("t-export-note", `✓ Healthy: ${detail}`, "good");
  else setNote("t-export-note", detail);
}

function describe(detail) {
  return Object.entries(detail)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");
}

function renderFeed(recent) {
  const body = $("feed");
  body.replaceChildren();
  recent.forEach((r) => {
    const key = `${r.timestamp}|${r.eventName}|${r.session}`;
    const tr = document.createElement("tr");
    if (seenFeedKeys.size && !seenFeedKeys.has(key)) tr.className = "new";
    const cells = [
      localTime(r.timestamp, true),
      r.eventName,
      r.path.split("#")[1] || r.path,
      r.eventName === "page_view" ? "" : describe(r.detail),
      r.session,
    ];
    cells.forEach((value, i) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (i === 1) td.className = ERROR_EVENTS.has(r.eventName) ? "event-name err" : "event-name";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  recent.forEach((r) => seenFeedKeys.add(`${r.timestamp}|${r.eventName}|${r.session}`));
  if (!recent.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "muted";
    td.textContent = "No events in the last 30 minutes yet.";
    tr.appendChild(td);
    body.appendChild(tr);
  }
}

// --- Polling -----------------------------------------------------------------------

async function poll() {
  try {
    const resp = await fetch(`${API}/stats?minutes=${MINUTES}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const stats = await resp.json();
    lastStats = stats;
    document.querySelector(".main").style.opacity = "1";
    renderTiles(stats);
    renderColumns($("per-minute"), stats.perMinute);
    renderFlows(stats.byFlow);
    renderFeed(stats.recent);
    $("live-chip").classList.remove("stale");
    $("live-text").textContent = `Live · updated ${localTime(stats.generatedAt, true)}`;
  } catch (err) {
    // Keep the last frame on screen, dimmed, rather than blanking it.
    document.querySelector(".main").style.opacity = "0.55";
    $("live-chip").classList.add("stale");
    $("live-text").textContent = `Connection lost (${err.message}), retrying…`;
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

window.addEventListener("resize", () => lastStats && renderColumns($("per-minute"), lastStats.perMinute));
poll();

// --- Ask BehaviorLens ------------------------------------------------------------------

const answerEl = $("answer");

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function resultTable(columns, rows) {
  const table = el("table");
  const head = el("tr");
  columns.forEach((c) => head.appendChild(el("th", c)));
  const thead = el("thead");
  thead.appendChild(head);
  const tbody = el("tbody");
  rows.slice(0, 20).forEach((row) => {
    const tr = el("tr");
    row.forEach((v) => tr.appendChild(el("td", v === null ? "–" : String(v))));
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
  return table;
}

function renderAnswer(data, status) {
  answerEl.replaceChildren();
  answerEl.classList.add("show");
  if (status === 422) {
    answerEl.appendChild(el("span", `⛔ ${data.error}`, "verdict bad"));
    if (data.sql) {
      const details = el("details");
      details.open = true;
      details.append(el("summary", "SQL the model proposed (not executed)"), el("pre", data.sql));
      answerEl.appendChild(details);
    }
    return;
  }
  if (status !== 200) {
    answerEl.appendChild(el("p", data.error || `Something went wrong (HTTP ${status}).`, "muted"));
    return;
  }
  answerEl.appendChild(el("p", data.answer));
  const check = data.factCheck || { total: 0, verified: 0, unverified: [] };
  if (!check.total) answerEl.appendChild(el("span", "No numbers to verify", "verdict warn"));
  else if (!check.unverified.length)
    answerEl.appendChild(el("span", `✓ All ${check.total} numbers verified against the query result`, "verdict good"));
  else
    answerEl.appendChild(
      el("span", `⚠ ${check.unverified.length} of ${check.total} numbers not found in the result: ${check.unverified.join(", ")}`, "verdict bad")
    );
  if (data.sql) {
    const details = el("details");
    details.append(el("summary", "SQL and result"), el("pre", data.sql), resultTable(data.columns, data.rows));
    answerEl.appendChild(details);
  }
}

async function ask(question) {
  const button = $("ask-btn");
  button.disabled = true;
  answerEl.replaceChildren(el("p", "Thinking… the first question can take ~20 seconds while the SQL warehouse wakes up.", "muted"));
  answerEl.classList.add("show");
  try {
    const resp = await fetch(`${API}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    renderAnswer(data, resp.status);
  } catch (err) {
    renderAnswer({ error: `Couldn't reach BehaviorLens (${err.message}).` }, 0);
  } finally {
    button.disabled = false;
  }
}

$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const question = $("ask-input").value.trim();
  if (question.length >= 3) ask(question);
});
SUGGESTIONS.forEach((q) => {
  const chip = el("button", q);
  chip.type = "button";
  chip.addEventListener("click", () => {
    $("ask-input").value = q;
    ask(q);
  });
  $("chips").appendChild(chip);
});
