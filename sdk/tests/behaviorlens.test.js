const BehaviorLens = require("../src/behaviorlens");

function flushPromises() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  BehaviorLens.reset();
  window.sessionStorage.clear();
  window.localStorage.clear();
  global.fetch = jest.fn(() => Promise.resolve({ ok: true }));
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});

describe("init", () => {
  test("throws if endpoint is missing", () => {
    expect(() => BehaviorLens.init({})).toThrow("endpoint");
  });

  test("succeeds with a valid endpoint", () => {
    expect(() => BehaviorLens.init({ endpoint: "http://localhost/api/events" })).not.toThrow();
  });
});

describe("track", () => {
  test("throws if called before init", () => {
    expect(() => BehaviorLens.track("login_attempt")).toThrow("init()");
  });

  test("throws if eventName is missing", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events" });
    expect(() => BehaviorLens.track()).toThrow("eventName");
  });

  test("builds a schema-compliant event and queues it", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("feature_click", { button: "export" });
    const queue = BehaviorLens._internal.getState().queue;
    expect(queue).toHaveLength(1);
    expect(queue[0]).toMatchObject({
      eventName: "feature_click",
      properties: { button: "export" },
    });
    expect(queue[0].sessionId).toBeTruthy();
    expect(queue[0].userId).toBeTruthy();
    expect(queue[0].timestamp).toBeTruthy();
    expect(queue[0].pageUrl).toBeTruthy();
  });

  test("reuses the same sessionId and userId across multiple track calls", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("login_attempt");
    BehaviorLens.track("login_success");
    const [first, second] = BehaviorLens._internal.getState().queue;
    expect(first.sessionId).toBe(second.sessionId);
    expect(first.userId).toBe(second.userId);
  });
});

describe("validateEvent", () => {
  test("rejects events missing required fields", () => {
    expect(BehaviorLens._internal.validateEvent({ eventName: "x" })).toBe(false);
  });

  test("accepts a fully-formed event", () => {
    const event = BehaviorLens._internal.buildEvent("search_query", { q: "test" });
    expect(BehaviorLens._internal.validateEvent(event)).toBe(true);
  });
});

describe("batching", () => {
  test("auto-flushes once batchSize is reached", async () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 2 });
    BehaviorLens.track("login_attempt");
    expect(global.fetch).not.toHaveBeenCalled();
    BehaviorLens.track("login_success");
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(BehaviorLens._internal.getState().queue).toHaveLength(0);

    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.events).toHaveLength(2);
  });

  test("manual flush sends whatever is queued", async () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("api_error", { code: 500 });
    BehaviorLens.flush();
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  test("flushes automatically on the flush interval", () => {
    jest.useFakeTimers();
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10, flushIntervalMs: 2000 });
    BehaviorLens.track("page_not_found");
    expect(global.fetch).not.toHaveBeenCalled();
    jest.advanceTimersByTime(2000);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  test("re-queues events if the network request fails", async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error("network down")));
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("retry_attempt");
    BehaviorLens.flush();
    await flushPromises();
    expect(BehaviorLens._internal.getState().queue).toHaveLength(1);
  });

  test("re-queues events when the server answers with an error status", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 503 }));
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("export_triggered");
    BehaviorLens.flush();
    await flushPromises();
    expect(BehaviorLens._internal.getState().queue).toHaveLength(1);
  });
});

describe("retry and persistence", () => {
  test("backs off after a failure instead of retrying every interval", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 500 }));
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10, flushIntervalMs: 1000 });
    BehaviorLens.track("api_error");
    BehaviorLens.flush();
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledTimes(1);

    BehaviorLens.flush();
    expect(global.fetch).toHaveBeenCalledTimes(1);

    const state = BehaviorLens._internal.getState();
    expect(state.retryDelayMs).toBe(1000);
    state.nextAttemptAt = 0;
    global.fetch = jest.fn(() => Promise.resolve({ ok: true }));
    BehaviorLens.flush();
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(state.retryDelayMs).toBe(0);
    expect(state.queue).toHaveLength(0);
  });

  test("backoff doubles and is capped at 30s", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 500 }));
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10, flushIntervalMs: 20000 });
    BehaviorLens.track("api_error");
    const state = BehaviorLens._internal.getState();
    for (let i = 0; i < 3; i++) {
      state.nextAttemptAt = 0;
      BehaviorLens.flush();
      await flushPromises();
    }
    expect(state.retryDelayMs).toBe(30000);
  });

  test("drops the oldest events once the queue is full", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 100, maxQueueSize: 3 });
    ["a", "b", "c", "d", "e"].forEach((name) => BehaviorLens.track(name));
    const state = BehaviorLens._internal.getState();
    expect(state.queue.map((e) => e.eventName)).toEqual(["c", "d", "e"]);
    expect(state.dropped).toBe(2);
  });

  test("persists unsent events and restores them on the next init", async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error("offline")));
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("search_query", { query: "revenue" });
    BehaviorLens.flush();
    await flushPromises();
    expect(JSON.parse(window.localStorage.getItem("bl_queue"))).toHaveLength(1);

    BehaviorLens.reset();
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    expect(BehaviorLens._internal.getState().queue[0].eventName).toBe("search_query");
    expect(window.localStorage.getItem("bl_queue")).toBeNull();
  });

  test("sends via sendBeacon on pagehide and keeps events if the beacon is refused", () => {
    navigator.sendBeacon = jest.fn(() => false);
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("feature_click");
    window.dispatchEvent(new Event("pagehide"));
    expect(navigator.sendBeacon).toHaveBeenCalledTimes(1);
    expect(BehaviorLens._internal.getState().queue).toHaveLength(1);

    navigator.sendBeacon = jest.fn(() => true);
    window.dispatchEvent(new Event("pagehide"));
    expect(BehaviorLens._internal.getState().queue).toHaveLength(0);
    delete navigator.sendBeacon;
  });
});

describe("privacy", () => {
  test("masks email addresses in properties but keeps the domain", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("login_attempt", { email: "jane.doe@psiog.com", nested: { note: "ping a@b.io" } });
    const [event] = BehaviorLens._internal.getState().queue;
    expect(event.properties.email).toBe("[redacted]@psiog.com");
    expect(event.properties.nested.note).toBe("ping [redacted]@b.io");
  });

  test("masks emails inside the page URL without eating the rest of it", () => {
    window.history.replaceState({}, "", "/invite?to=jane.doe@psiog.com&ref=mail");
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("page_view");
    expect(BehaviorLens._internal.getState().queue[0].pageUrl).toBe(
      "http://localhost/invite?to=[redacted]@psiog.com&ref=mail"
    );
    window.history.replaceState({}, "", "/");
  });

  test("masking can be turned off explicitly", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10, maskPII: false });
    BehaviorLens.track("login_attempt", { email: "jane.doe@psiog.com" });
    expect(BehaviorLens._internal.getState().queue[0].properties.email).toBe("jane.doe@psiog.com");
  });

  test("scrub leaves non-string values untouched", () => {
    expect(BehaviorLens._internal.scrub({ n: 5, ok: true, list: ["x@y.com"] })).toEqual({
      n: 5,
      ok: true,
      list: ["[redacted]@y.com"],
    });
  });

  test("optOut stops tracking and clears the queue; optIn resumes", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 10 });
    BehaviorLens.track("feature_click");
    BehaviorLens.optOut();
    expect(BehaviorLens._internal.getState().queue).toHaveLength(0);
    expect(BehaviorLens.track("feature_click")).toBe(false);
    BehaviorLens.optIn();
    expect(BehaviorLens.track("feature_click")).toBe(true);
  });

  test("attaches appVersion when configured", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", appVersion: "2.4.0" });
    BehaviorLens.track("feature_click");
    expect(BehaviorLens._internal.getState().queue[0].appVersion).toBe("2.4.0");
  });
});

describe("autocapture", () => {
  const names = () => BehaviorLens._internal.getState().queue.map((e) => e.eventName);

  test("is off by default", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 100 });
    window.dispatchEvent(new Event("hashchange"));
    expect(names()).toEqual([]);
  });

  test("tracks a page view on init and on navigation", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 100, autoCapture: true });
    window.dispatchEvent(new Event("hashchange"));
    window.history.pushState({}, "", "/reports");
    expect(names()).toEqual(["page_view", "page_view", "page_view"]);
    expect(BehaviorLens._internal.getState().queue[2].properties.path).toBe("/reports");
  });

  test("tracks clicks on elements carrying data-bl-event, with data-bl-prop-* properties", () => {
    document.body.innerHTML =
      '<button data-bl-event="export_triggered" data-bl-prop-format="csv" data-bl-prop-report-id="r1"><span id="inner">Export</span></button><button id="plain">No tracking</button>';
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 100, autoCapture: { clicks: true } });
    document.getElementById("inner").click();
    document.getElementById("plain").click();
    const queue = BehaviorLens._internal.getState().queue;
    expect(queue).toHaveLength(1);
    expect(queue[0]).toMatchObject({ eventName: "export_triggered", properties: { format: "csv", reportId: "r1" } });
  });

  test("captures uncaught errors and unhandled promise rejections as js_error", () => {
    BehaviorLens.init({ endpoint: "http://localhost/api/events", batchSize: 100, autoCapture: { errors: true } });
    window.dispatchEvent(
      new ErrorEvent("error", { message: "x is undefined", filename: "https://app/share.js?v=2", lineno: 42 })
    );
    const rejection = new Event("unhandledrejection");
    rejection.reason = new Error("fetch failed");
    window.dispatchEvent(rejection);

    const queue = BehaviorLens._internal.getState().queue;
    expect(queue[0]).toMatchObject({
      eventName: "js_error",
      properties: { message: "x is undefined", source: "https://app/share.js", line: 42 },
    });
    expect(queue[1].properties).toMatchObject({ message: "fetch failed", source: "unhandledrejection" });
  });
});
