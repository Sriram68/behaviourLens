import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest

import ask
import function_app


def make_request(body, headers=None, method="POST", url="/api/events", params=None):
    return func.HttpRequest(
        method=method,
        url=url,
        body=json.dumps(body).encode("utf-8") if body is not None else b"",
        headers={"Content-Type": "application/json", **(headers or {})},
        params=params or {},
    )


SAMPLE_EVENT = {
    "eventName": "login_attempt",
    "userId": "u1",
    "sessionId": "s1",
    "timestamp": "2026-07-13T00:00:00Z",
    "pageUrl": "/login",
    "properties": {},
}


@pytest.fixture(autouse=True)
def fresh_limiters(monkeypatch):
    monkeypatch.setattr(function_app, "ingest_limiter", function_app.RateLimiter(300))
    monkeypatch.setattr(function_app, "ask_limiter", function_app.RateLimiter(10))
    function_app._stats_cache.clear()
    function_app._blob_cache.clear()


# --- Ingestion ---------------------------------------------------------------


@patch("function_app._get_container_client")
def test_ingest_valid_batch_returns_200_and_writes_blob(mock_get_container):
    mock_container = MagicMock()
    mock_get_container.return_value = mock_container

    events = [SAMPLE_EVENT, {**SAMPLE_EVENT, "eventName": "login_success"}]
    resp = function_app.ingest_events(make_request({"events": events}))

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert body["status"] == "ok"
    assert body["count"] == 2
    assert body["rejected"] == 0

    mock_container.upload_blob.assert_called_once()
    _, kwargs = mock_container.upload_blob.call_args
    written_lines = kwargs["data"].split("\n")
    assert len(written_lines) == 2
    assert json.loads(written_lines[0])["eventName"] == "login_attempt"
    assert kwargs["overwrite"] is False


@patch("function_app._get_container_client")
def test_ingest_accepts_bare_list_payload(mock_get_container):
    mock_get_container.return_value = MagicMock()
    resp = function_app.ingest_events(make_request([SAMPLE_EVENT]))
    assert resp.status_code == 200


def test_ingest_rejects_invalid_json():
    req = func.HttpRequest(method="POST", url="/api/events", body=b"not-json", headers={})
    resp = function_app.ingest_events(req)
    assert resp.status_code == 400


def test_ingest_rejects_empty_events_list():
    resp = function_app.ingest_events(make_request({"events": []}))
    assert resp.status_code == 400


def test_ingest_rejects_missing_events_key():
    resp = function_app.ingest_events(make_request({}))
    assert resp.status_code == 400


def test_ingest_rejects_non_object_payload():
    resp = function_app.ingest_events(make_request("just a string"))
    assert resp.status_code == 400


@patch("function_app._get_container_client")
def test_blob_name_is_date_partitioned(mock_get_container):
    mock_get_container.return_value = MagicMock()
    resp = function_app.ingest_events(make_request({"events": [SAMPLE_EVENT]}))
    blob_name = json.loads(resp.get_body())["blob"]
    assert blob_name.count("/") == 3  # YYYY/MM/DD/<uuid>.ndjson
    assert blob_name.endswith(".ndjson")


@patch("function_app._get_container_client")
def test_ingest_drops_invalid_events_and_reports_them(mock_get_container):
    mock_container = MagicMock()
    mock_get_container.return_value = mock_container
    events = [SAMPLE_EVENT, {**SAMPLE_EVENT, "eventName": "DROP TABLE"}, {"eventName": "x"}]
    resp = function_app.ingest_events(make_request({"events": events}))

    body = json.loads(resp.get_body())
    assert resp.status_code == 200
    assert (body["count"], body["rejected"]) == (1, 2)
    assert len(mock_container.upload_blob.call_args.kwargs["data"].split("\n")) == 1


@patch("function_app._get_container_client")
def test_ingest_rejects_a_batch_with_no_valid_events(mock_get_container):
    resp = function_app.ingest_events(make_request({"events": [{"eventName": "x"}]}))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["rejected"] == 1
    mock_get_container.assert_not_called()


def test_ingest_rejects_oversized_batches():
    resp = function_app.ingest_events(make_request({"events": [SAMPLE_EVENT] * 201}))
    assert resp.status_code == 413


def test_ingest_rejects_oversized_bodies():
    big = {**SAMPLE_EVENT, "properties": {"blob": "x" * (300 * 1024)}}
    resp = function_app.ingest_events(make_request({"events": [big]}))
    assert resp.status_code == 413


@patch("function_app._get_container_client")
def test_ingest_rate_limits_per_client_ip(mock_get_container, monkeypatch):
    mock_get_container.return_value = MagicMock()
    monkeypatch.setattr(function_app, "ingest_limiter", function_app.RateLimiter(2))
    headers = {"X-Forwarded-For": "203.0.113.7:51234"}
    statuses = [
        function_app.ingest_events(make_request({"events": [SAMPLE_EVENT]}, headers)).status_code for _ in range(3)
    ]
    assert statuses == [200, 200, 429]
    other_ip = make_request({"events": [SAMPLE_EVENT]}, {"X-Forwarded-For": "198.51.100.1"})
    assert function_app.ingest_events(other_ip).status_code == 200


# --- Validation & scrubbing ------------------------------------------------


def test_clean_event_masks_emails_and_keeps_app_version():
    event = {
        **SAMPLE_EVENT,
        "pageUrl": "https://app/?invite=jane@psiog.com",
        "properties": {"email": "jane.doe@psiog.com", "nested": {"to": ["a@b.io"]}},
        "appVersion": "2.4.0",
        "unexpected": "dropped",
    }
    cleaned = function_app.clean_event(event)
    assert cleaned["pageUrl"] == "https://app/?invite=[redacted]@psiog.com"
    assert cleaned["properties"] == {"email": "[redacted]@psiog.com", "nested": {"to": ["[redacted]@b.io"]}}
    assert cleaned["appVersion"] == "2.4.0"
    assert "unexpected" not in cleaned


@pytest.mark.parametrize(
    "override",
    [
        {"eventName": "Login Attempt"},
        {"eventName": ""},
        {"userId": 42},
        {"timestamp": "yesterday"},
        {"properties": ["not", "a", "dict"]},
        {"pageUrl": "x" * 3000},
        {"properties": {key: "y" * 2000 for key in "abcde"}},  # each field fits, the event doesn't
    ],
)
def test_clean_event_rejects_invalid_events(override):
    assert function_app.clean_event({**SAMPLE_EVENT, **override}) is None


def test_clean_event_rejects_non_dicts():
    assert function_app.clean_event("event") is None


def test_parse_timestamp_assumes_utc_for_naive_values():
    parsed = function_app._parse_timestamp("2026-10-12T10:00:00")
    assert parsed.tzinfo == datetime.timezone.utc


def test_rate_limiter_window_slides():
    limiter = function_app.RateLimiter(2, window_seconds=60)
    assert limiter.allow("ip", now=0)
    assert limiter.allow("ip", now=1)
    assert not limiter.allow("ip", now=2)
    assert limiter.allow("ip", now=61)


# --- Live stats --------------------------------------------------------------

NOW = datetime.datetime(2026, 10, 12, 10, 30, tzinfo=datetime.timezone.utc)


def _event(name, minutes_ago, session="s1", user="u1", **props):
    ts = NOW - datetime.timedelta(minutes=minutes_ago)
    return {
        "eventName": name,
        "userId": user,
        "sessionId": session,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "pageUrl": "https://sriram68.github.io/behaviourLens/demo-app/index.html#/reports",
        "properties": props,
    }


def test_compute_stats_counts_only_events_inside_the_window():
    events = [
        _event("page_view", 1),
        _event("export_triggered", 2, session="s2", user="u2", format="csv"),
        _event("api_error", 2, session="s2", user="u2", code=502, endpoint="/api/export"),
        _event("retry_attempt", 2, session="s2", user="u2", originalEvent="api_error", endpoint="/api/export"),
        _event("api_error", 3, code=500, endpoint="/api/reports"),
        _event("search_query", 60 * 24 * 30),  # backfilled history: excluded
    ]
    stats = function_app.compute_stats(events, NOW, 30)

    assert stats["totals"] == {"events": 5, "sessions": 2, "users": 2, "errors": 3}
    assert stats["byFlow"]["Error & Recovery"] == 3
    assert stats["exports"] == {"attempts": 1, "retries": 1, "failures": 1}
    assert len(stats["perMinute"]) == 31
    assert sum(point["count"] for point in stats["perMinute"]) == 5
    assert stats["recent"][0]["eventName"] == "page_view"  # newest first
    assert stats["recent"][0]["path"] == "/behaviourLens/demo-app/index.html#/reports"
    assert {"code": 502, "endpoint": "/api/export"} in [r["detail"] for r in stats["recent"]]


def test_compute_stats_ignores_unparseable_timestamps():
    stats = function_app.compute_stats([{"eventName": "x", "timestamp": None}], NOW, 5)
    assert stats["totals"]["events"] == 0


def _blob(name, minutes_ago):
    return SimpleNamespace(name=name, last_modified=NOW - datetime.timedelta(minutes=minutes_ago))


@patch("function_app._get_container_client")
def test_live_stats_reads_recent_blobs_once_and_caches_them(mock_get_container):
    container = MagicMock()
    container.list_blobs.return_value = [_blob("2026/10/12/a.ndjson", 1), _blob("2026/10/12/old.ndjson", 300)]
    payload = json.dumps(_event("feature_click", 1, feature="dashboard")) + "\n\nnot-json\n"
    container.download_blob.return_value.readall.return_value = payload.encode()
    mock_get_container.return_value = container

    with patch("function_app._utcnow", return_value=NOW):
        resp = function_app.live_stats(make_request(None, method="GET", url="/api/stats", params={"minutes": "30"}))
        function_app._stats_cache.clear()
        function_app.live_stats(make_request(None, method="GET", url="/api/stats", params={"minutes": "30"}))

    body = json.loads(resp.get_body())
    assert resp.status_code == 200
    assert body["totals"]["events"] == 1
    assert body["byEvent"] == {"feature_click": 1}
    container.download_blob.assert_called_once_with("2026/10/12/a.ndjson")


@patch("function_app._get_container_client")
def test_live_stats_clamps_bad_window_and_serves_from_cache(mock_get_container):
    container = MagicMock()
    container.list_blobs.return_value = []
    mock_get_container.return_value = container
    first = function_app.live_stats(make_request(None, method="GET", url="/api/stats", params={"minutes": "abc"}))
    second = function_app.live_stats(make_request(None, method="GET", url="/api/stats", params={"minutes": "abc"}))
    assert json.loads(first.get_body())["windowMinutes"] == 30
    assert first.get_body() == second.get_body()
    assert container.list_blobs.call_count <= 2  # one listing per date prefix, then cached


# --- Health ------------------------------------------------------------------


@patch("function_app._get_container_client")
def test_health_ok_when_storage_reachable(mock_get_container):
    mock_get_container.return_value.exists.return_value = True
    resp = function_app.health(make_request(None, method="GET", url="/api/health"))
    assert resp.status_code == 200
    assert json.loads(resp.get_body())["checks"]["storage"] is True


@patch("function_app._get_container_client", side_effect=RuntimeError("no storage"))
def test_health_degraded_when_storage_fails(_):
    resp = function_app.health(make_request(None, method="GET", url="/api/health"))
    assert resp.status_code == 503
    assert json.loads(resp.get_body())["status"] == "degraded"


# --- Ask endpoint ---------------------------------------------------------------


def _ask(question, headers=None):
    return function_app.ask_question(make_request({"question": question}, headers, url="/api/ask"))


def test_ask_validates_the_question():
    assert _ask("hi").status_code == 400
    assert _ask("x" * 301).status_code == 400
    bad = func.HttpRequest(method="POST", url="/api/ask", body=b"nope", headers={})
    assert function_app.ask_question(bad).status_code == 400


def test_ask_returns_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(ask, "is_configured", lambda: False)
    assert _ask("Which feature is least used?").status_code == 503


def test_ask_success_and_error_mapping(monkeypatch):
    monkeypatch.setattr(ask, "is_configured", lambda: True)
    monkeypatch.setattr(ask, "answer_question", lambda q: {"question": q, "answer": "ok"})
    assert json.loads(_ask("Which feature is least used?").get_body())["answer"] == "ok"

    def unsafe(_):
        raise ask.UnsafeQuery("Only SELECT queries are allowed", "DROP TABLE x")

    monkeypatch.setattr(ask, "answer_question", unsafe)
    resp = _ask("Delete everything please")
    assert resp.status_code == 422
    assert json.loads(resp.get_body())["sql"] == "DROP TABLE x"

    def failing(_):
        raise ask.QueryError("warehouse down")

    monkeypatch.setattr(ask, "answer_question", failing)
    assert _ask("How many sessions?").status_code == 502

    def upstream(_):
        raise ValueError("bad json from model")

    monkeypatch.setattr(ask, "answer_question", upstream)
    assert _ask("How many sessions?").status_code == 502


def test_ask_is_rate_limited(monkeypatch):
    monkeypatch.setattr(function_app, "ask_limiter", function_app.RateLimiter(1))
    monkeypatch.setattr(ask, "is_configured", lambda: False)
    assert _ask("first question").status_code == 503
    assert _ask("second question").status_code == 429
