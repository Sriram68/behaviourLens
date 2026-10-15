import datetime
import json
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest
import requests

import ask
import function_app
import teams_report

NOW = datetime.datetime(2026, 10, 16, 10, 30, tzinfo=datetime.timezone.utc)
STATS = {
    "windowMinutes": 30,
    "totals": {"events": 42, "sessions": 6, "users": 6, "errors": 9},
    "byEvent": {"api_error": 4, "js_error": 2, "page_not_found": 1, "retry_attempt": 2},
    "exports": {"attempts": 5, "retries": 3, "failures": 4},
}
INSIGHT = {
    "narrative": "Errors spiked from 2026-10-11 after release 2.4.0.",
    "status": "verified",
    "facts_verified": "12",
    "facts_total": "12",
    "generated_at": "2026-10-16T09:41:00.000Z",
}


def card_text(card):
    return json.dumps(card["attachments"][0]["content"], ensure_ascii=False)


def test_card_has_live_snapshot_and_verified_insight():
    card = teams_report.build_card(STATS, INSIGHT, NOW)
    text = card_text(card)
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert "Sent from BehaviorLens Live at 2026-10-16 10:30 UTC" in text
    assert "50% (4 of 8 export calls failed)" in text
    assert "9 (4 API · 2 JS · 1 not found · 2 retries)" in text
    assert "Errors spiked from 2026-10-11" in text
    assert "12/12 numbers verified" in text
    assert "pipeline run 2026-10-16 09:41 UTC" in text


def test_card_flags_an_unverified_insight_and_handles_no_exports():
    stats = {**STATS, "exports": {"attempts": 0, "retries": 0, "failures": 0}}
    text = card_text(teams_report.build_card(stats, {**INSIGHT, "status": "needs_review"}, NOW))
    assert "no exports yet" in text
    assert "needs_review: not fact-checked" in text


def test_card_without_insight_still_sends_the_snapshot():
    text = card_text(teams_report.build_card(STATS, None, NOW))
    assert "Latest AI insight unavailable" in text
    assert "Export failure rate" in text


def test_latest_insight_is_best_effort(monkeypatch):
    monkeypatch.setattr(ask, "is_configured", lambda: False)
    assert teams_report.latest_insight() is None

    monkeypatch.setattr(ask, "is_configured", lambda: True)
    monkeypatch.setattr(ask, "run_sql", MagicMock(side_effect=ask.QueryError("warehouse asleep")))
    assert teams_report.latest_insight() is None

    monkeypatch.setattr(ask, "run_sql", lambda sql, max_wait_seconds: (list(INSIGHT), [list(INSIGHT.values())]))
    assert teams_report.latest_insight() == INSIGHT


# --- Endpoint ---------------------------------------------------------------------


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(function_app, "report_limiter", function_app.RateLimiter(3))
    function_app._last_report_at[0] = 0.0
    monkeypatch.setattr(function_app, "_recent_blob_events", lambda container, now, minutes: [])
    monkeypatch.setattr(function_app, "_get_container_client", MagicMock())
    monkeypatch.setattr(teams_report, "latest_insight", lambda: INSIGHT)


def _send(headers=None):
    return function_app.send_report(func.HttpRequest(method="POST", url="/api/teams/report", body=b"", headers=headers or {}))


def test_report_requires_a_configured_webhook(endpoint, monkeypatch):
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    assert _send().status_code == 503


@patch("function_app.requests.post")
def test_report_is_sent_then_cooled_down(mock_post, endpoint, monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.test/hook")
    mock_post.return_value = MagicMock(status_code=202)

    first = _send()
    assert first.status_code == 200
    assert json.loads(first.get_body())["includedInsight"] is True
    assert mock_post.call_args.args[0] == "https://example.test/hook"
    assert "BehaviorLens report" in card_text(mock_post.call_args.kwargs["json"])

    second = _send()
    assert second.status_code == 429
    assert "just sent" in json.loads(second.get_body())["error"]
    assert mock_post.call_count == 1


@patch("function_app.requests.post")
def test_report_surfaces_teams_failures(mock_post, endpoint, monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.test/hook")
    mock_post.return_value = MagicMock(status_code=400)
    assert _send().status_code == 502
    mock_post.side_effect = requests.ConnectionError()
    assert _send().status_code == 502
    assert function_app._last_report_at[0] == 0.0  # failures don't start the cooldown


@patch("function_app.requests.post")
def test_report_is_rate_limited_per_ip(mock_post, endpoint, monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(function_app, "report_limiter", function_app.RateLimiter(1))
    mock_post.return_value = MagicMock(status_code=400)
    headers = {"X-Forwarded-For": "203.0.113.9"}
    assert _send(headers).status_code == 502
    assert _send(headers).status_code == 429
