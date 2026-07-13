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
});
