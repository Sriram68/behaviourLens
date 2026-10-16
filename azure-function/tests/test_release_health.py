import json
from unittest.mock import MagicMock

import azure.functions as func
import pytest
import requests

import ask
import function_app
import release_health

FACTS = {
    "is_anomaly": True,
    "alert": True,
    "first_flagged_day": "2026-10-12",
    "latest_release": {"version": "2.4.0", "date": "2026-10-11"},
    "export_failure_rate_before_release_pct": 1.8,
    "export_failure_rate_since_release_pct": 46.1,
    "export_failure_rate_ratio": 25.6,
    "sessions_hit_by_export_failure_since_release": 127,
    "sessions_abandoned_after_export_failure": 77,
    "abandoned_after_export_failure_pct": 60.6,
    "error_share_by_app_version_pct": {"2.4.0": 100.0},
    "top_causes": [{"event": "api_error", "code": 502, "endpoint": "/api/export", "errors": 203, "share_pct": 58.3}],
}
ROW = {
    "facts_json": json.dumps(FACTS),
    "narrative": "Release 2.4.0 broke exports.",
    "status": "verified",
    "facts_verified": "11",
    "facts_total": "11",
    "generated_at": "2026-10-16T06:37:44.359Z",
}


@pytest.fixture(autouse=True)
def fresh_cache():
    release_health._cache.update(at=0.0, value=None)


def test_verdict_rolls_back_a_release_that_broke_exports():
    assert release_health.verdict(FACTS) == ("rollback", "Roll back or hotfix 2.4.0")


def test_verdict_only_investigates_when_the_release_is_not_clearly_to_blame():
    assert release_health.verdict({**FACTS, "export_failure_rate_ratio": 1.2})[0] == "investigate"
    assert release_health.verdict({**FACTS, "error_share_by_app_version_pct": {"2.4.0": 20.0}})[0] == "investigate"
    assert release_health.verdict({**FACTS, "alert": False})[0] == "investigate"  # spike is over


def test_verdict_is_healthy_without_an_anomaly():
    assert release_health.verdict({"is_anomaly": False, "alert": False}) == ("healthy", "No release problem detected")
    assert release_health.verdict({})[0] == "healthy"


def test_summary_carries_the_facts_and_the_verification():
    summary = release_health.summarize(ROW)
    assert summary["level"] == "rollback"
    assert summary["release"] == {"version": "2.4.0", "date": "2026-10-11"}
    assert summary["sessionsAbandoned"] == 77
    assert summary["errorSharePct"] == 100.0
    assert summary["topCause"] == {"event": "api_error", "code": 502, "endpoint": "/api/export", "share_pct": 58.3}
    assert (summary["factsVerified"], summary["factsTotal"]) == (11, 11)


def test_latest_reads_once_then_serves_the_cache(monkeypatch):
    monkeypatch.setattr(ask, "is_configured", lambda: True)
    run_sql = MagicMock(return_value=(list(ROW), [list(ROW.values())]))
    monkeypatch.setattr(ask, "run_sql", run_sql)
    assert release_health.latest()["headline"] == "Roll back or hotfix 2.4.0"
    assert release_health.latest()["level"] == "rollback"
    assert run_sql.call_count == 1


def test_latest_is_none_when_not_configured_or_empty(monkeypatch):
    monkeypatch.setattr(ask, "is_configured", lambda: False)
    assert release_health.latest() is None
    monkeypatch.setattr(ask, "is_configured", lambda: True)
    monkeypatch.setattr(ask, "run_sql", lambda sql, max_wait_seconds: (list(ROW), []))
    assert release_health.latest() is None


def _get():
    return function_app.release_health_view(func.HttpRequest(method="GET", url="/api/release/health", body=b""))


def test_endpoint_returns_the_summary(monkeypatch):
    monkeypatch.setattr(function_app, "release_limiter", function_app.RateLimiter(20))
    monkeypatch.setattr(release_health, "latest", lambda: {"level": "rollback"})
    resp = _get()
    assert resp.status_code == 200
    assert json.loads(resp.get_body()) == {"level": "rollback"}


def test_endpoint_reports_missing_or_unreachable_data(monkeypatch):
    monkeypatch.setattr(function_app, "release_limiter", function_app.RateLimiter(20))
    monkeypatch.setattr(release_health, "latest", lambda: None)
    assert _get().status_code == 503
    monkeypatch.setattr(release_health, "latest", MagicMock(side_effect=ask.QueryError("asleep")))
    assert _get().status_code == 502
    monkeypatch.setattr(release_health, "latest", MagicMock(side_effect=requests.Timeout()))
    assert _get().status_code == 502
