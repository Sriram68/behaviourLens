"""The insight layer must find what the simulated history planted: the 2.4.0
export incident, the onboarding step-3 drop-off, and the barely-used
Scheduled Reports feature. The generator is the ground truth."""

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

import factcheck
import insights
from synthetic_event_generator import simulate

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
PROPS = ["feature", "code", "endpoint", "step", "path"]


def to_frames(raw_events):
    """Mirrors 01_ingestion (typed timestamp, properties) and 02_aggregations
    (session_summary) closely enough for the facts functions."""
    events = pd.DataFrame(
        {
            "eventName": [e["eventName"] for e in raw_events],
            "userId": [e["userId"] for e in raw_events],
            "sessionId": [e["sessionId"] for e in raw_events],
            "event_timestamp": pd.to_datetime([e["timestamp"] for e in raw_events], utc=True).tz_localize(None),
            "appVersion": [e.get("appVersion") for e in raw_events],
            **{p: [e["properties"].get(p) for e in raw_events] for p in PROPS},
        }
    )
    events["step"] = pd.to_numeric(events["step"])
    events["code"] = pd.to_numeric(events["code"])
    grouped = events.groupby("sessionId")
    sessions = pd.DataFrame(
        {
            "userId": grouped["userId"].first(),
            "session_start_ts": grouped["event_timestamp"].min(),
            "session_end_ts": grouped["event_timestamp"].max(),
            "event_count": grouped.size(),
        }
    ).reset_index()
    sessions["session_duration_seconds"] = (sessions["session_end_ts"] - sessions["session_start_ts"]).dt.total_seconds()
    return events, sessions


@pytest.fixture(scope="module")
def history():
    raw, incident_start = simulate(days=56, seed=42, incident_days_ago=5, now=NOW)
    events, sessions = to_frames(raw)
    return events, sessions, incident_start


def test_anomaly_detects_the_export_incident_and_its_cause(history):
    events, _, incident_start = history
    facts = insights.anomaly_facts(events)

    assert facts["is_anomaly"] and facts["alert"]
    assert facts["first_flagged_day"] == incident_start.strftime("%Y-%m-%d")
    top = facts["top_causes"][0]
    assert (top["event"], top["code"], top["endpoint"]) == ("api_error", 502, "/api/export")
    assert facts["error_share_by_app_version_pct"]["2.4.0"] > 90
    assert facts["latest_release"] == {"version": "2.4.0", "date": incident_start.strftime("%Y-%m-%d")}
    assert facts["export_failure_rate_before_release_pct"] < 5
    assert 35 < facts["export_failure_rate_since_release_pct"] < 65
    assert facts["export_failure_rate_ratio"] > 10
    assert facts["sessions_abandoned_after_export_failure"] > 0


def test_no_anomaly_when_error_volume_is_steady():
    # Incident happened long before the 28-day lookback: errors are high but flat.
    raw, _ = simulate(days=56, seed=7, incident_days_ago=50, now=NOW)
    events, _ = to_frames(raw)
    facts = insights.anomaly_facts(events)
    assert not facts["is_anomaly"]
    assert not facts["alert"]
    assert "top_causes" not in facts


def test_funnel_finds_the_step_3_drop_off(history):
    events, _, _ = history
    facts = insights.funnel_facts(events)

    onboarding = facts["onboarding"]
    assert onboarding["started"] >= onboarding["step_1"] >= onboarding["step_2"] >= onboarding["step_3"]
    assert facts["biggest_drop"]["step"] == 3
    assert facts["step_conversion_pct"]["step_3"] < facts["step_conversion_pct"]["step_2"]
    assert [p["rank"] for p in facts["top_dropoff_points"]] == [1, 2, 3]
    assert facts["top_dropoff_points"][0]["lost"] >= facts["top_dropoff_points"][-1]["lost"]


def test_weekly_usage_flags_scheduled_reports_as_least_used(history):
    events, sessions, _ = history
    facts = insights.weekly_usage_facts(events, sessions)

    assert facts["least_used_feature"] == "scheduled_reports"
    assert facts["most_used_feature"] == "dashboard"
    assert facts["this_week"].endswith("to 2026-10-12")
    this, last = facts["this_week_metrics"], facts["last_week_metrics"]
    assert this["export_failure_rate_pct"] > last["export_failure_rate_pct"]
    assert facts["change_points"]["export_failure_rate_pct"] > 10
    assert "export_failure_rate_pct" not in facts["change_pct"]
    assert 2 < this["avg_session_minutes"] < 30  # realistic sessions, not multi-day ones


def test_cohorts_show_power_users_live_in_saved_views(history):
    events, sessions, _ = history
    facts = insights.cohort_facts(events, sessions)

    high, low = facts["cohorts"]["high"], facts["cohorts"]["low"]
    assert high["avg_sessions_per_user"] > low["avg_sessions_per_user"]
    assert facts["biggest_adoption_gap_feature"] == "saved_views"


def test_facts_are_json_serialisable_and_prompts_embed_them(history):
    events, sessions, _ = history
    packs = insights.all_facts(events, sessions)
    assert set(packs) == set(insights.SCENARIOS)
    for scenario, facts in packs.items():
        json.dumps(facts)
        prompt = insights.build_prompt(scenario, facts, unverified=["1,500"])
        assert json.dumps(facts, indent=1) in prompt
        assert "numbers not in FACTS: 1,500" in prompt


def test_a_narrative_built_from_the_facts_passes_verification(history):
    events, _, _ = history
    facts = insights.anomaly_facts(events)
    top = facts["top_causes"][0]
    narrative = (
        f"Errors spiked from {facts['first_flagged_day']}, peaking at {facts['peak_day_errors']} on "
        f"{facts['peak_day']} against a baseline of {facts['baseline_errors_per_day']} per day. "
        f"Most are {top['code']} errors on {top['endpoint']} since release {facts['latest_release']['version']}: "
        f"export failures rose from {facts['export_failure_rate_before_release_pct']}% to "
        f"{facts['export_failure_rate_since_release_pct']}%."
    )
    check = factcheck.verify(narrative, facts)
    assert check["unverified"] == [], check
    assert check["total"] >= 8
