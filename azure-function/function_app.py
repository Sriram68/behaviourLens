import datetime
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import azure.functions as func
import requests
from azure.storage.blob import BlobServiceClient

import ask

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
# The storage SDK logs every HTTP request at INFO; keep the Function logs readable.
logging.getLogger("azure").setLevel(logging.WARNING)

REQUIRED_FIELDS = ("eventName", "userId", "sessionId", "timestamp", "pageUrl")
EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
EMAIL_RE = re.compile(r"[\w.%+-]+@([\w.-]+\.[a-z]{2,})", re.IGNORECASE)
MAX_BODY_BYTES = 256 * 1024
MAX_EVENTS_PER_BATCH = 200
MAX_EVENT_BYTES = 8 * 1024
MAX_FIELD_CHARS = 2048

FLOWS = {
    "login_attempt": "Login & Onboarding",
    "login_success": "Login & Onboarding",
    "onboarding_step_complete": "Login & Onboarding",
    "feature_click": "Feature Usage",
    "search_query": "Feature Usage",
    "filter_applied": "Feature Usage",
    "export_triggered": "Feature Usage",
    "api_error": "Error & Recovery",
    "page_not_found": "Error & Recovery",
    "retry_attempt": "Error & Recovery",
    "js_error": "Error & Recovery",
    "page_view": "Navigation",
}
ERROR_EVENTS = {name for name, flow in FLOWS.items() if flow == "Error & Recovery"}
DETAIL_KEYS = ("feature", "step", "query", "filter", "format", "code", "endpoint", "path", "message")


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _get_container_client():
    conn_str = os.environ["AzureWebJobsStorage"]
    container_name = os.environ.get("RAW_EVENTS_CONTAINER", "raw-events")
    blob_service = BlobServiceClient.from_connection_string(conn_str)
    return blob_service.get_container_client(container_name)


def _json_response(payload, status_code=200, headers=None):
    return func.HttpResponse(json.dumps(payload), status_code=status_code, mimetype="application/json", headers=headers)


class RateLimiter:
    """Sliding-window limit per client IP. In-memory, so per Function instance:
    a cheap first line of defence for an anonymous endpoint, not a quota system."""

    def __init__(self, limit, window_seconds=60):
        self.limit = limit
        self.window = window_seconds
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            hits = self.hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


ingest_limiter = RateLimiter(int(os.environ.get("INGEST_RATE_LIMIT_PER_MIN", "300")))
ask_limiter = RateLimiter(int(os.environ.get("ASK_RATE_LIMIT_PER_MIN", "10")))


def _client_ip(req):
    forwarded = req.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded.count(":") == 1:  # IPv4 with port, e.g. 1.2.3.4:5678
        forwarded = forwarded.split(":")[0]
    return forwarded or "unknown"


def _too_many_requests():
    return _json_response({"error": "Too many requests, slow down"}, 429, {"Retry-After": "60"})


def _scrub(value):
    if isinstance(value, str):
        return EMAIL_RE.sub(r"[redacted]@\1", value)[:MAX_FIELD_CHARS]
    if isinstance(value, dict):
        return {str(k)[:64]: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _parse_timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def clean_event(event):
    """Returns a validated, PII-scrubbed copy of `event`, or None if it is invalid.

    Server-side twin of the SDK's validation: a client that skips the SDK
    (or a modified one) can't push malformed or personal data into the lake.
    """
    if not isinstance(event, dict):
        return None
    for field in REQUIRED_FIELDS:
        value = event.get(field)
        if not isinstance(value, str) or not value or len(value) > MAX_FIELD_CHARS:
            return None
    if not EVENT_NAME_RE.match(event["eventName"]) or _parse_timestamp(event["timestamp"]) is None:
        return None
    properties = event.get("properties", {})
    if not isinstance(properties, dict):
        return None

    cleaned = {field: event[field] for field in REQUIRED_FIELDS}
    cleaned["pageUrl"] = _scrub(cleaned["pageUrl"])
    cleaned["properties"] = _scrub(properties)
    if isinstance(event.get("appVersion"), str):
        cleaned["appVersion"] = event["appVersion"][:32]
    if len(json.dumps(cleaned)) > MAX_EVENT_BYTES:
        return None
    return cleaned


def ingest_events(req: func.HttpRequest) -> func.HttpResponse:
    if not ingest_limiter.allow(_client_ip(req)):
        return _too_many_requests()
    if len(req.get_body() or b"") > MAX_BODY_BYTES:
        return func.HttpResponse("Payload too large", status_code=413)

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    events = body if isinstance(body, list) else (body.get("events") if isinstance(body, dict) else None)
    if not events or not isinstance(events, list):
        return func.HttpResponse("No events provided", status_code=400)
    if len(events) > MAX_EVENTS_PER_BATCH:
        return func.HttpResponse(f"At most {MAX_EVENTS_PER_BATCH} events per batch", status_code=413)

    accepted = [e for e in (clean_event(event) for event in events) if e is not None]
    rejected = len(events) - len(accepted)
    if not accepted:
        return _json_response({"status": "rejected", "count": 0, "rejected": rejected}, 400)

    ndjson = "\n".join(json.dumps(e) for e in accepted)
    blob_name = f"{_utcnow():%Y/%m/%d}/{uuid.uuid4()}.ndjson"

    container_client = _get_container_client()
    container_client.upload_blob(name=blob_name, data=ndjson, overwrite=False)

    logging.info("Wrote %d events to %s (%d rejected)", len(accepted), blob_name, rejected)
    return _json_response({"status": "ok", "count": len(accepted), "rejected": rejected, "blob": blob_name})


# --- Live stats ------------------------------------------------------------
# Blobs are write-once (overwrite=False), so each one is downloaded at most
# once per instance and cached; every poll only fetches what is new.
_blob_cache = {}
_blob_cache_lock = threading.Lock()
_stats_cache = {}
STATS_CACHE_SECONDS = 2


def _parse_ndjson(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _recent_blob_events(container, now, minutes):
    since = now - datetime.timedelta(minutes=minutes)
    prefixes = sorted({f"{since:%Y/%m/%d}/", f"{now:%Y/%m/%d}/"})
    recent = []
    for prefix in prefixes:
        for blob in container.list_blobs(name_starts_with=prefix):
            if blob.last_modified and blob.last_modified >= since:
                recent.append(blob.name)

    def fetch(name):
        return name, _parse_ndjson(container.download_blob(name).readall().decode("utf-8"))

    missing = [name for name in recent if name not in _blob_cache]
    if missing:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for name, events in pool.map(fetch, missing):
                with _blob_cache_lock:
                    _blob_cache[name] = events

    recent_names = set(recent)
    with _blob_cache_lock:
        for name in [n for n in _blob_cache if n not in recent_names]:
            del _blob_cache[name]
        return [event for name in recent for event in _blob_cache.get(name, [])]


def _event_path(page_url):
    parsed = urlparse(page_url or "")
    return (parsed.path or "/") + (f"#{parsed.fragment}" if parsed.fragment else "")


def compute_stats(events, now, minutes):
    """Aggregates events whose own timestamp is inside the window. Backfilled
    synthetic history (old timestamps) is excluded even if it landed just now."""
    since = now - datetime.timedelta(minutes=minutes)
    window = []
    for event in events:
        ts = _parse_timestamp(event.get("timestamp"))
        if ts and since <= ts <= now + datetime.timedelta(minutes=1):
            window.append((ts, event))
    window.sort(key=lambda pair: pair[0])

    by_event = Counter(event.get("eventName") for _, event in window)
    by_flow = Counter(FLOWS.get(event.get("eventName"), "Other") for _, event in window)
    per_minute = Counter(ts.replace(second=0, microsecond=0) for ts, _ in window)
    start_minute = since.replace(second=0, microsecond=0)
    series = []
    for i in range(minutes + 1):
        minute = start_minute + datetime.timedelta(minutes=i)
        series.append({"minute": minute.isoformat(), "count": per_minute.get(minute, 0)})

    def export_calls(name):
        return sum(
            1
            for _, event in window
            if event.get("eventName") == name and (event.get("properties") or {}).get("endpoint") == "/api/export"
        )

    recent = []
    for ts, event in reversed(window[-25:]):
        props = event.get("properties") or {}
        detail = {key: props[key] for key in DETAIL_KEYS if key in props}
        recent.append(
            {
                "eventName": event.get("eventName"),
                "timestamp": ts.isoformat(),
                "path": _event_path(event.get("pageUrl")),
                "detail": detail,
                "session": str(event.get("sessionId", ""))[:8],
            }
        )

    return {
        "generatedAt": now.isoformat(),
        "windowMinutes": minutes,
        "totals": {
            "events": len(window),
            "sessions": len({event.get("sessionId") for _, event in window}),
            "users": len({event.get("userId") for _, event in window}),
            "errors": sum(count for name, count in by_event.items() if name in ERROR_EVENTS),
        },
        "byEvent": dict(by_event.most_common()),
        "byFlow": dict(by_flow.most_common()),
        "perMinute": series,
        # Retries call /api/export again, so they count as calls too.
        "exports": {
            "attempts": by_event.get("export_triggered", 0),
            "retries": export_calls("retry_attempt"),
            "failures": export_calls("api_error"),
        },
        "recent": recent,
    }


def live_stats(req: func.HttpRequest) -> func.HttpResponse:
    try:
        minutes = max(1, min(int(req.params.get("minutes", "30")), 180))
    except ValueError:
        minutes = 30

    cached = _stats_cache.get(minutes)
    if cached and time.monotonic() - cached[0] < STATS_CACHE_SECONDS:
        return _json_response(cached[1])

    now = _utcnow()
    events = _recent_blob_events(_get_container_client(), now, minutes)
    stats = compute_stats(events, now, minutes)
    _stats_cache[minutes] = (time.monotonic(), stats)
    return _json_response(stats)


# --- Health & Ask ------------------------------------------------------------


def health(req: func.HttpRequest) -> func.HttpResponse:
    checks = {"storage": False, "askConfigured": ask.is_configured()}
    try:
        checks["storage"] = bool(_get_container_client().exists())
    except Exception:  # noqa: BLE001 - any storage failure means unhealthy
        logging.exception("Health check: storage unreachable")
    status = "ok" if checks["storage"] else "degraded"
    return _json_response({"status": status, "checks": checks}, 200 if checks["storage"] else 503)


def ask_question(req: func.HttpRequest) -> func.HttpResponse:
    if not ask_limiter.allow(_client_ip(req)):
        return _too_many_requests()
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Invalid JSON body"}, 400)
    question = str((body or {}).get("question", "")).strip() if isinstance(body, dict) else ""
    if not 3 <= len(question) <= 300:
        return _json_response({"error": "Ask a question between 3 and 300 characters"}, 400)
    if not ask.is_configured():
        return _json_response({"error": "Ask BehaviorLens is not configured on this deployment"}, 503)

    try:
        return _json_response(ask.answer_question(question))
    except ask.UnsafeQuery as err:
        logging.warning("Blocked generated SQL for %r: %s", question, err)
        return _json_response({"error": f"Blocked by SQL guardrail: {err}", "sql": err.sql}, 422)
    except ask.QueryError as err:
        return _json_response({"error": f"Query failed: {err}"}, 502)
    except (requests.RequestException, KeyError, ValueError) as err:
        logging.exception("Ask failed")
        return _json_response({"error": f"Upstream error: {type(err).__name__}"}, 502)


app.route(route="events", methods=["POST"])(ingest_events)
app.route(route="stats", methods=["GET"])(live_stats)
app.route(route="health", methods=["GET"])(health)
app.route(route="ask", methods=["POST"])(ask_question)
