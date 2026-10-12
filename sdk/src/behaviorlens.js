(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.BehaviorLens = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var REQUIRED_FIELDS = ["eventName", "userId", "sessionId", "timestamp", "pageUrl"];
  var SESSION_KEY = "bl_session_id";
  var USER_KEY = "bl_user_id";
  var QUEUE_KEY = "bl_queue";
  var OPT_OUT_KEY = "bl_opt_out";
  var MAX_BACKOFF_MS = 30000;
  // Keeps the domain (useful, org-level) but drops the mailbox (personal).
  var EMAIL_RE = /[\w.%+-]+@([\w.-]+\.[a-z]{2,})/gi;

  var state;
  var listenersAttached = false;

  function defaults() {
    return {
      endpoint: null,
      userId: null,
      appVersion: null,
      onEvent: null,
      batchSize: 10,
      flushIntervalMs: 2000,
      maxQueueSize: 500,
      maskPII: true,
      auto: {},
      queue: [],
      timer: null,
      retryDelayMs: 0,
      nextAttemptAt: 0,
      dropped: 0,
      initialized: false,
    };
  }
  state = defaults();

  function generateId() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function getOrCreate(kind, key) {
    try {
      var store = window[kind];
      var existing = store.getItem(key);
      if (existing) return existing;
      var created = generateId();
      store.setItem(key, created);
      return created;
    } catch (e) {
      return generateId();
    }
  }

  function getSessionId() {
    return getOrCreate("sessionStorage", SESSION_KEY);
  }

  function getUserId() {
    if (state.userId) return state.userId;
    return getOrCreate("localStorage", USER_KEY);
  }

  function isOptedOut() {
    try {
      return localStorage.getItem(OPT_OUT_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function scrub(value) {
    if (typeof value === "string") return value.replace(EMAIL_RE, "[redacted]@$1");
    if (value && typeof value === "object") {
      var out = Array.isArray(value) ? [] : {};
      for (var k in value) {
        if (Object.prototype.hasOwnProperty.call(value, k)) out[k] = scrub(value[k]);
      }
      return out;
    }
    return value;
  }

  function validateEvent(event) {
    if (!event || typeof event !== "object") return false;
    return REQUIRED_FIELDS.every(function (field) {
      return event[field] !== undefined && event[field] !== null && event[field] !== "";
    });
  }

  function buildEvent(eventName, properties) {
    var event = {
      eventName: eventName,
      userId: getUserId(),
      sessionId: getSessionId(),
      timestamp: new Date().toISOString(),
      pageUrl: window.location.href,
      properties: properties || {},
    };
    if (state.appVersion) event.appVersion = state.appVersion;
    if (state.maskPII) {
      event.pageUrl = scrub(event.pageUrl);
      event.properties = scrub(event.properties);
    }
    return event;
  }

  function persistQueue() {
    try {
      if (state.queue.length) {
        localStorage.setItem(QUEUE_KEY, JSON.stringify(state.queue));
      } else {
        localStorage.removeItem(QUEUE_KEY);
      }
    } catch (e) {
      // Storage full or unavailable: the in-memory queue still retries.
    }
  }

  function restoreQueue() {
    try {
      var saved = JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
      localStorage.removeItem(QUEUE_KEY);
      return Array.isArray(saved) ? saved : [];
    } catch (e) {
      return [];
    }
  }

  function capQueue() {
    var overflow = state.queue.length - state.maxQueueSize;
    if (overflow > 0) {
      state.queue.splice(0, overflow);
      state.dropped += overflow;
    }
  }

  function onSendSuccess() {
    state.retryDelayMs = 0;
    state.nextAttemptAt = 0;
    persistQueue();
  }

  function onSendFailure(events) {
    state.queue = events.concat(state.queue);
    capQueue();
    // Exponential backoff so a down endpoint isn't hammered every interval.
    state.retryDelayMs = Math.min(Math.max(state.retryDelayMs * 2, state.flushIntervalMs), MAX_BACKOFF_MS);
    state.nextAttemptAt = Date.now() + state.retryDelayMs;
    persistQueue();
  }

  function send(events, useBeacon) {
    if (!events.length) return;
    var payload = JSON.stringify({ events: events });

    if (useBeacon && navigator.sendBeacon) {
      var blob = new Blob([payload], { type: "application/json" });
      if (!navigator.sendBeacon(state.endpoint, blob)) {
        state.queue = events.concat(state.queue);
        persistQueue();
      }
      return;
    }

    fetch(state.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    })
      .then(function (res) {
        // fetch only rejects on network failure; a 4xx/5xx still resolves,
        // so check it explicitly or those events are silently lost.
        if (!res.ok) throw new Error(res.status);
        onSendSuccess();
      })
      .catch(function () {
        onSendFailure(events);
      });
  }

  function flush(useBeacon) {
    if (!state.queue.length) return;
    if (!useBeacon && Date.now() < state.nextAttemptAt) return;
    var events = state.queue;
    state.queue = [];
    send(events, useBeacon);
  }

  function scheduleFlush() {
    if (state.timer) return;
    state.timer = setInterval(function () {
      flush(false);
    }, state.flushIntervalMs);
  }

  function safeTrack(eventName, properties) {
    try {
      track(eventName, properties);
    } catch (e) {
      // Autocapture must never break the host page.
    }
  }

  function trackPageView() {
    if (!state.initialized || !state.auto.pageViews) return;
    safeTrack("page_view", { path: window.location.pathname + window.location.hash });
  }

  function propsFromDataset(el) {
    var props = {};
    for (var key in el.dataset) {
      // data-bl-prop-format="csv" -> dataset.blPropFormat -> { format: "csv" }
      if (key.indexOf("blProp") === 0 && key.length > 6) {
        props[key.charAt(6).toLowerCase() + key.slice(7)] = el.dataset[key];
      }
    }
    return props;
  }

  function attachListeners() {
    if (listenersAttached || typeof window === "undefined" || !window.addEventListener) return;
    listenersAttached = true;

    window.addEventListener("pagehide", function () {
      if (state.initialized) flush(true);
    });

    window.addEventListener("hashchange", trackPageView);
    window.addEventListener("popstate", trackPageView);
    if (window.history && window.history.pushState) {
      var pushState = window.history.pushState;
      window.history.pushState = function () {
        var result = pushState.apply(this, arguments);
        trackPageView();
        return result;
      };
    }

    document.addEventListener(
      "click",
      function (e) {
        if (!state.initialized || !state.auto.clicks) return;
        var el = e.target && e.target.closest && e.target.closest("[data-bl-event]");
        if (el) safeTrack(el.getAttribute("data-bl-event"), propsFromDataset(el));
      },
      true
    );

    // One handler for both uncaught errors and unhandled promise rejections.
    function onError(e) {
      if (!state.initialized || !state.auto.errors) return;
      var reason = e.reason;
      safeTrack("js_error", {
        message: String((reason && reason.message) || e.message || reason || "unknown").slice(0, 200),
        source: reason !== undefined ? "unhandledrejection" : String(e.filename || "").split("?")[0],
        line: e.lineno || 0,
      });
    }
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onError);
  }

  function init(config) {
    if (!config || !config.endpoint) {
      throw new Error("BehaviorLens: endpoint required");
    }
    if (state.timer) clearInterval(state.timer);
    state = defaults();
    ["endpoint", "userId", "appVersion", "batchSize", "flushIntervalMs", "maxQueueSize", "onEvent"].forEach(function (key) {
      if (config[key]) state[key] = config[key];
    });
    state.maskPII = config.maskPII !== false;
    var auto = config.autoCapture;
    state.auto = auto === true ? { pageViews: true, clicks: true, errors: true } : auto || {};
    // Events that failed to send on a previous page load get another chance.
    state.queue = restoreQueue();
    capQueue();
    state.initialized = true;

    scheduleFlush();
    attachListeners();
    trackPageView();
  }

  function track(eventName, properties) {
    if (!state.initialized) {
      throw new Error("BehaviorLens: call init() first");
    }
    if (!eventName) {
      throw new Error("BehaviorLens: eventName required");
    }
    if (isOptedOut()) return false;
    var event = buildEvent(eventName, properties);
    if (!validateEvent(event)) {
      return false;
    }
    state.queue.push(event);
    capQueue();
    if (state.onEvent) state.onEvent(event);
    if (state.queue.length >= state.batchSize) {
      flush(false);
    }
    return true;
  }

  function optOut() {
    try {
      localStorage.setItem(OPT_OUT_KEY, "1");
    } catch (e) {
      // Without storage the opt-out lasts for this page only.
    }
    state.queue = [];
    persistQueue();
  }

  function optIn() {
    try {
      localStorage.removeItem(OPT_OUT_KEY);
    } catch (e) {
      // Nothing persisted, nothing to clear.
    }
  }

  function reset() {
    if (state.timer) clearInterval(state.timer);
    state = defaults();
  }

  return {
    init: init,
    track: track,
    flush: function () {
      flush(false);
    },
    optOut: optOut,
    optIn: optIn,
    reset: reset,
    _internal: {
      validateEvent: validateEvent,
      buildEvent: buildEvent,
      generateId: generateId,
      scrub: scrub,
      getState: function () {
        return state;
      },
    },
  };
});
