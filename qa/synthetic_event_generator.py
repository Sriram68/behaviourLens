"""Generates synthetic BehaviorLens events and sends them through the real
ingestion endpoint, exercising the SDK->Function->Blob path at higher volume
than manual UI clicks can produce. Used for the E2E scale test."""

import json
import random
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

ENDPOINT = "http://localhost:7071/api/events"
TOTAL_EVENTS = 800
BATCH_SIZE = 20
NUM_USERS = 60
DAYS_BACK = 3

FLOW_EVENTS = {
    "login": ["login_attempt", "login_success", "onboarding_step_complete"],
    "feature": ["feature_click", "search_query", "filter_applied", "export_triggered"],
    "error": ["api_error", "page_not_found", "retry_attempt"],
}
FLOW_WEIGHTS = {"login": 0.25, "feature": 0.55, "error": 0.20}

EVENT_PROPERTIES = {
    "login_attempt": lambda: {"email": f"user{random.randint(1, 999)}@psiog.com"},
    "login_success": lambda: {"email": f"user{random.randint(1, 999)}@psiog.com"},
    "onboarding_step_complete": lambda: {"step": random.randint(1, 3)},
    "feature_click": lambda: {"feature": random.choice(["export_report", "dashboard", "settings"])},
    "search_query": lambda: {"query": random.choice(["revenue", "active users", "errors", "churn"])},
    "filter_applied": lambda: {"filter": random.choice(["last_7_days", "active_users", "errors_only"])},
    "export_triggered": lambda: {"format": random.choice(["csv", "pdf"])},
    "api_error": lambda: {"code": random.choice([500, 502, 503]), "endpoint": "/api/reports"},
    "page_not_found": lambda: {"path": f"/missing-page-{random.randint(1, 50)}"},
    "retry_attempt": lambda: {"originalEvent": "api_error"},
}


def random_timestamp():
    delta_seconds = random.randint(0, DAYS_BACK * 24 * 3600)
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_event(user_id, session_id):
    flow = random.choices(list(FLOW_WEIGHTS.keys()), weights=list(FLOW_WEIGHTS.values()))[0]
    event_name = random.choice(FLOW_EVENTS[flow])
    return {
        "eventName": event_name,
        "userId": user_id,
        "sessionId": session_id,
        "timestamp": random_timestamp(),
        "pageUrl": "/synthetic-load-test",
        "properties": EVENT_PROPERTIES[event_name](),
    }


def send_batch(events):
    body = json.dumps({"events": events}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def main():
    users = [str(uuid.uuid4()) for _ in range(NUM_USERS)]
    sessions = {u: str(uuid.uuid4()) for u in users}

    sent = 0
    batch = []
    for _ in range(TOTAL_EVENTS):
        user_id = random.choice(users)
        # 10% chance to start a new session for this user
        if random.random() < 0.1:
            sessions[user_id] = str(uuid.uuid4())
        batch.append(build_event(user_id, sessions[user_id]))

        if len(batch) >= BATCH_SIZE:
            status, resp = send_batch(batch)
            sent += len(batch)
            print(f"sent {sent}/{TOTAL_EVENTS} (status {status}, blob {resp.get('blob')})")
            batch = []

    if batch:
        status, resp = send_batch(batch)
        sent += len(batch)
        print(f"sent {sent}/{TOTAL_EVENTS} (status {status}, blob {resp.get('blob')})")

    print(f"Done. Sent {sent} synthetic events across {NUM_USERS} users.")


if __name__ == "__main__":
    main()
