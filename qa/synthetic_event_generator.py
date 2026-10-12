"""Simulates realistic BehaviorLens traffic for "ReportHub", an internal
reporting app, and sends it through the real ingestion endpoint.

Unlike a random event picker, every session is an ordered journey: login,
onboarding for new users, then a run of feature use with realistic gaps
between events. Three findings are planted for the pipeline and the AI layer
to discover, so the demo has a real story to tell:

1. Incident: release 2.4.0 (``--incident-days-ago`` days back, default 5)
   breaks /api/export. About 45% of exports fail with a 502 (baseline 2%);
   users retry, then abandon.
2. UX drop-off: onboarding step 3 ("connect a data source") loses about
   half of the new users who reach it.
3. Dead feature: Scheduled Reports is the least-used feature by far, and
   Share Link throws a JavaScript error 30% of the time.

All timestamps fall inside the last ``--days`` days and never after "now",
so the demo's live clicks land in the same week as the incident.

Usage:
  python qa/synthetic_event_generator.py --dry-run          # summary only
  python qa/synthetic_event_generator.py --out events.ndjson
  python qa/synthetic_event_generator.py                    # send to the endpoint
"""

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

ENDPOINT = os.environ.get("BEHAVIORLENS_ENDPOINT", "https://func-behaviorlens-psiddhi.azurewebsites.net/api/events")
APP_URL = "https://sriram68.github.io/behaviourLens/demo-app/index.html"
BATCH_SIZE = 200  # the Function's per-request maximum

EXPORT_FAIL_RATE_BASELINE = 0.02
EXPORT_FAIL_RATE_INCIDENT = 0.45
ONBOARDING_CONTINUE = {1: 0.93, 2: 0.80, 3: 0.50}  # P(complete step n | reached it)
CHURN_ON_STALL = {1: 0.3, 2: 0.5, 3: 0.75}  # P(never coming back | stalled at step n)
FEATURES = ["dashboard", "reports", "saved_views", "share_link", "scheduled_reports"]
SEARCHES = {
    "revenue by region": 14,
    "active users": 22,
    "weekly errors": 9,
    "churn": 6,
    "q3 forecast": 0,  # zero-result searches: content gap
    "export history": 3,
}
PAGES = ["/dashboard", "/reports", "/saved-views", "/scheduled-reports", "/settings"]
FILTERS = ["last_7_days", "last_30_days", "my_team", "errors_only"]


class User:
    def __init__(self, rng, joined, onboarded):
        self.id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        self.joined = joined
        # Skewed engagement: most users are casual, a few are power users.
        self.engagement = min(2.5, 0.4 + rng.expovariate(1.4))
        self.onboarding_done = 3 if onboarded else 0
        self.churned = False


def app_version(ts, incident_start):
    if ts >= incident_start:
        return "2.4.0"
    if ts >= incident_start - timedelta(days=21):
        return "2.3.1"
    return "2.3.0"


class Session:
    def __init__(self, rng, user, start, incident_start):
        self.rng = rng
        self.user = user
        self.id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        self.clock = start
        self.incident_start = incident_start
        self.events = []
        self.page = "/login"

    def emit(self, name, **props):
        self.clock += timedelta(seconds=max(2.0, self.rng.lognormvariate(2.6, 0.7)))
        self.events.append(
            {
                "eventName": name,
                "userId": self.user.id,
                "sessionId": self.id,
                "timestamp": self.clock.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.clock.microsecond // 1000:03d}Z",
                "pageUrl": f"{APP_URL}#{self.page}",
                "appVersion": app_version(self.clock, self.incident_start),
                "properties": props,
            }
        )

    def navigate(self, page):
        self.page = page
        self.emit("page_view", path=f"/behaviourLens/demo-app/index.html#{page}", title="ReportHub")

    def login(self):
        self.navigate("/login")
        method = "sso" if self.rng.random() < 0.6 else "password"
        self.emit("login_attempt", method=method)
        if self.rng.random() < 0.06:  # wrong password, try again
            self.emit("login_attempt", method=method)
            if self.rng.random() < 0.2:
                return False
        self.emit("login_success", method=method)
        return True

    def onboarding(self):
        self.navigate("/onboarding")
        for step in (1, 2, 3):
            if step <= self.user.onboarding_done:
                continue
            if self.rng.random() > ONBOARDING_CONTINUE[step]:
                self.user.churned = self.rng.random() < CHURN_ON_STALL[step]
                return False
            self.emit("onboarding_step_complete", step=step)
            self.user.onboarding_done = step
        return True

    def export(self):
        fail_rate = EXPORT_FAIL_RATE_INCIDENT if self.clock >= self.incident_start else EXPORT_FAIL_RATE_BASELINE
        self.emit("export_triggered", format="csv" if self.rng.random() < 0.7 else "pdf")
        for attempt in range(2):
            if self.rng.random() >= fail_rate:
                return True
            self.emit("api_error", code=502, endpoint="/api/export")
            if attempt == 1 or self.rng.random() > 0.7:
                return self.rng.random() > 0.6  # gave up; most abandon the session
            self.emit("retry_attempt", originalEvent="api_error", endpoint="/api/export")
        return True

    def action(self):
        rng = self.rng
        roll = rng.random()
        if rng.random() < 0.005:
            self.emit("api_error", code=500, endpoint="/api/reports")
        if roll < 0.22:
            self.navigate(rng.choices(PAGES, weights=[30, 30, 15, 4, 6])[0])
        elif roll < 0.48:
            saved_views = 8 + 10 * self.user.engagement  # power users live in saved views
            feature = rng.choices(FEATURES, weights=[34, 28, saved_views, 7, 2])[0]
            self.emit("feature_click", feature=feature)
            if feature == "share_link" and rng.random() < 0.3:
                self.emit(
                    "js_error",
                    message="Cannot read properties of undefined (reading 'url')",
                    source="share-link.js",
                    line=42,
                )
        elif roll < 0.66:
            query = rng.choice(list(SEARCHES))
            self.emit("search_query", query=query, results=SEARCHES[query])
        elif roll < 0.80:
            self.emit("filter_applied", filter=rng.choice(FILTERS))
        elif roll < 0.99:
            return self.export()
        else:
            self.page = "/help/getting-started"  # broken help link
            self.emit("page_not_found", path="/help/getting-started")
        return True

    def run(self):
        if not self.login():
            return self.events
        if self.user.onboarding_done < 3 and not self.onboarding():
            return self.events
        self.navigate("/dashboard")
        actions = min(30, 1 + int(self.rng.expovariate(1 / (5 * self.user.engagement))))
        for _ in range(actions):
            if not self.action():
                break
        return self.events


def session_start(rng, day):
    # Working hours in IST (UTC+5:30), peaking late morning.
    hour = min(14.5, max(3.0, rng.gauss(7.5, 2.4)))
    return day + timedelta(hours=hour, seconds=rng.randint(0, 59))


def simulate(days, seed, incident_days_ago, now=None):
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first_day = today - timedelta(days=days - 1)
    incident_start = today - timedelta(days=incident_days_ago) + timedelta(hours=4, minutes=30)  # 10:00 IST

    users = [User(rng, first_day, onboarded=True) for _ in range(120)]
    events = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        weekday = day.weekday() < 5
        growth = 1 + 0.006 * offset

        newcomers = [User(rng, day, onboarded=False) for _ in range(rng.randint(2, 6) if weekday else 0)]
        users.extend(newcomers)
        planned = [(user, session_start(rng, day)) for user in newcomers]

        count = int((55 if weekday else 12) * growth * rng.uniform(0.85, 1.15))
        weights = [0 if user.churned else user.engagement for user in users]
        planned += [(user, session_start(rng, day)) for user in rng.choices(users, weights=weights, k=count)]

        for user, start in sorted(planned, key=lambda pair: pair[1]):
            if start >= now:
                continue
            session_events = Session(rng, user, start, incident_start).run()
            events.extend(e for e in session_events if e["timestamp"] <= now.strftime("%Y-%m-%dT%H:%M:%S.999Z"))
    return events, incident_start


def summarize(events, incident_start):
    def ts(event):
        return datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))

    names = Counter(e["eventName"] for e in events)
    before = [e for e in events if ts(e) < incident_start]
    after = [e for e in events if ts(e) >= incident_start]

    def export_failure_rate(chunk):
        exports = sum(1 for e in chunk if e["eventName"] == "export_triggered")
        failures = sum(
            1 for e in chunk if e["eventName"] == "api_error" and e["properties"].get("endpoint") == "/api/export"
        )
        return failures, exports

    steps = Counter(e["properties"]["step"] for e in events if e["eventName"] == "onboarding_step_complete")
    features = Counter(e["properties"]["feature"] for e in events if e["eventName"] == "feature_click")
    fb, eb = export_failure_rate(before)
    fa, ea = export_failure_rate(after)
    print(f"events:   {len(events):,} across {len({e['sessionId'] for e in events}):,} sessions, "
          f"{len({e['userId'] for e in events}):,} users")
    print(f"range:    {min(e['timestamp'] for e in events)[:10]} .. {max(e['timestamp'] for e in events)[:10]}")
    print(f"incident: release 2.4.0 at {incident_start:%Y-%m-%d %H:%M} UTC")
    print(f"exports:  {fb}/{eb} failed before ({fb / max(eb, 1):.1%}), {fa}/{ea} failed after ({fa / max(ea, 1):.1%})")
    print(f"onboard:  step1 {steps[1]}, step2 {steps[2]}, step3 {steps[3]}")
    print(f"features: {dict(features.most_common())}")
    print(f"errors:   js_error {names['js_error']}, page_not_found {names['page_not_found']}, "
          f"api_error {names['api_error']}")


def send_batch(batch):
    body = json.dumps({"events": batch}).encode("utf-8")
    for attempt in range(6):
        req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(int(exc.headers.get("Retry-After", "10")))
                continue
            if exc.code < 500:
                raise
        except urllib.error.URLError:
            pass
        time.sleep(min(30, 2 ** attempt))
    raise RuntimeError("batch failed after retries")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", "--days-back", type=int, default=56, help="history length (default 56)")
    parser.add_argument("--incident-days-ago", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="print the story summary, send nothing")
    parser.add_argument("--out", help="write NDJSON to this file instead of sending")
    args = parser.parse_args()

    events, incident_start = simulate(args.days, args.seed, args.incident_days_ago)
    summarize(events, incident_start)
    if args.dry_run:
        return
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e) for e in events) + "\n")
        print(f"wrote {len(events):,} events to {args.out}")
        return

    start = time.monotonic()
    accepted = rejected = 0
    batches = math.ceil(len(events) / BATCH_SIZE)
    for i in range(batches):
        resp = send_batch(events[i * BATCH_SIZE:(i + 1) * BATCH_SIZE])
        accepted += resp.get("count", 0)
        rejected += resp.get("rejected", 0)
        if (i + 1) % 10 == 0 or i + 1 == batches:
            print(f"sent batch {i + 1}/{batches}: {accepted:,} accepted, {rejected} rejected")
    print(f"Done in {time.monotonic() - start:.1f}s: {accepted:,} accepted, {rejected} rejected.")
    if rejected:
        sys.exit(1)


if __name__ == "__main__":
    main()
