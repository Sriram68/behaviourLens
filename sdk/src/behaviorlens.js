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

  var state = {
    endpoint: null,
    userId: null,
    batchSize: 10,
    flushIntervalMs: 2000,
    queue: [],
    timer: null,
    initialized: false,
  };

  function generateId() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function getOrCreate(storage, key) {
    try {
      var existing = storage.getItem(key);
      if (existing) return existing;
      var created = generateId();
      storage.setItem(key, created);
      return created;
    } catch (e) {
      return generateId();
    }
  }

  function getSessionId() {
    return getOrCreate(window.sessionStorage, SESSION_KEY);
  }

  function getUserId() {
    if (state.userId) return state.userId;
    return getOrCreate(window.localStorage, USER_KEY);
  }

  function validateEvent(event) {
    if (!event || typeof event !== "object") return false;
    return REQUIRED_FIELDS.every(function (field) {
      return event[field] !== undefined && event[field] !== null && event[field] !== "";
    });
  }

  function buildEvent(eventName, properties) {
    return {
      eventName: eventName,
      userId: getUserId(),
      sessionId: getSessionId(),
      timestamp: new Date().toISOString(),
      pageUrl: window.location.href,
      properties: properties || {},
    };
  }

  function send(events, useBeacon) {
    if (!events.length) return;
    var payload = JSON.stringify({ events: events });

    if (useBeacon && navigator.sendBeacon) {
      var blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon(state.endpoint, blob);
      return;
    }

    fetch(state.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    }).catch(function () {
      state.queue = events.concat(state.queue);
    });
  }

  function flush(useBeacon) {
    if (!state.queue.length) return;
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

  function init(config) {
    if (!config || !config.endpoint) {
      throw new Error("BehaviorLens.init requires an endpoint");
    }
    state.endpoint = config.endpoint;
    state.userId = config.userId || null;
    state.batchSize = config.batchSize || 10;
    state.flushIntervalMs = config.flushIntervalMs || 2000;
    state.queue = [];
    state.initialized = true;

    scheduleFlush();

    if (typeof window !== "undefined" && window.addEventListener) {
      window.addEventListener("pagehide", function () {
        flush(true);
      });
    }
  }

  function track(eventName, properties) {
    if (!state.initialized) {
      throw new Error("BehaviorLens.track called before init()");
    }
    if (!eventName) {
      throw new Error("BehaviorLens.track requires an eventName");
    }
    var event = buildEvent(eventName, properties);
    if (!validateEvent(event)) {
      return false;
    }
    state.queue.push(event);
    if (state.queue.length >= state.batchSize) {
      flush(false);
    }
    return true;
  }

  function reset() {
    state.endpoint = null;
    state.userId = null;
    state.batchSize = 10;
    state.flushIntervalMs = 2000;
    state.queue = [];
    state.initialized = false;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  return {
    init: init,
    track: track,
    flush: function () {
      flush(false);
    },
    reset: reset,
    _internal: {
      validateEvent: validateEvent,
      buildEvent: buildEvent,
      generateId: generateId,
      getState: function () {
        return state;
      },
    },
  };
});
