"""BehaviorLens insight facts: the numbers each AI narrative is allowed to use.

Each function turns pipeline tables (as pandas DataFrames) into a small,
JSON-serialisable "facts pack". The model narrates a pack and factcheck.verify()
holds the narrative to it, so every number a stakeholder reads was computed
here, not by the model.

Pure pandas, so it is unit-tested locally against the simulated ReportHub
history (databricks/tests/) and imported unchanged by 04_ai_insights.

Expected inputs:
  events:   eventName, userId, sessionId, event_timestamp (naive UTC), appVersion,
            feature, code, endpoint, step, path   (property columns may be null)
  sessions: sessionId, userId, session_start_ts, session_duration_seconds, event_count
"""

import json

import pandas as pd

FEATURE_EVENTS = ["feature_click", "search_query", "filter_applied", "export_triggered"]
ERROR_EVENTS = ["api_error", "page_not_found", "retry_attempt", "js_error"]
EXPORT_ENDPOINT = "/api/export"
LOOKBACK_DAYS = 28
WINDOW_DAYS = 7
Z_THRESHOLD = 3


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def _change_pct(new, old):
    return round(100.0 * (new - old) / old, 1) if old else None


def _day(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def native(value):
    """Converts numpy/pandas scalars inside a facts pack to plain Python."""
    if isinstance(value, dict):
        return {str(k): native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return _day(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def windows(events):
    """(as_of, this_week_start, last_week_start): weeks are the 7 days up to and
    including the latest event date, not the wall clock."""
    as_of = events["event_timestamp"].max().normalize() + pd.Timedelta(days=1)
    this_start = as_of - pd.Timedelta(days=WINDOW_DAYS)
    return as_of, this_start, this_start - pd.Timedelta(days=WINDOW_DAYS)


def _export_calls(events):
    on_export = events["endpoint"] == EXPORT_ENDPOINT
    attempts = int((events["eventName"] == "export_triggered").sum())
    retries = int(((events["eventName"] == "retry_attempt") & on_export).sum())
    failures = int(((events["eventName"] == "api_error") & on_export).sum())
    return attempts + retries, failures


def _period(events, sessions, start, end):
    chunk = events[(events["event_timestamp"] >= start) & (events["event_timestamp"] < end)]
    sess = sessions[(sessions["session_start_ts"] >= start) & (sessions["session_start_ts"] < end)]
    features = chunk[chunk["eventName"] == "feature_click"]["feature"].value_counts()
    calls, failures = _export_calls(chunk)
    return chunk, {
        "active_users": int(chunk["userId"].nunique()),
        "sessions": int(len(sess)),
        "avg_session_minutes": round(float(sess["session_duration_seconds"].mean() / 60), 1) if len(sess) else 0.0,
        "feature_uses": int(chunk["eventName"].isin(FEATURE_EVENTS).sum()),
        "exports": int((chunk["eventName"] == "export_triggered").sum()),
        "export_failure_rate_pct": _pct(failures, calls),
    }, {str(k): int(v) for k, v in features.items()}


def weekly_usage_facts(events, sessions):
    as_of, this_start, last_start = windows(events)
    _, this_week, features_this = _period(events, sessions, this_start, as_of)
    _, last_week, features_last = _period(events, sessions, last_start, this_start)
    total_clicks = sum(features_this.values())
    ranked = sorted(features_this.items(), key=lambda kv: kv[1], reverse=True)
    return native(
        {
            "this_week": f"{_day(this_start)} to {_day(as_of - pd.Timedelta(days=1))}",
            "last_week": f"{_day(last_start)} to {_day(this_start - pd.Timedelta(days=1))}",
            "this_week_metrics": this_week,
            "last_week_metrics": last_week,
            # Counts change in %, rates change in percentage points.
            "change_pct": {k: _change_pct(v, last_week[k]) for k, v in this_week.items() if not k.endswith("_pct")},
            "change_points": {k: round(v - last_week[k], 1) for k, v in this_week.items() if k.endswith("_pct")},
            "feature_clicks_this_week": features_this,
            "feature_clicks_last_week": features_last,
            "most_used_feature": ranked[0][0] if ranked else None,
            "least_used_feature": ranked[-1][0] if ranked else None,
            "least_used_feature_share_pct": _pct(ranked[-1][1], total_clicks) if ranked else 0.0,
        }
    )


def _daily_errors(events, as_of):
    start = as_of - pd.Timedelta(days=LOOKBACK_DAYS)
    errors = events[events["eventName"].isin(ERROR_EVENTS) & (events["event_timestamp"] >= start)]
    days = pd.date_range(start, as_of - pd.Timedelta(days=1), freq="D")
    return errors, errors.groupby(errors["event_timestamp"].dt.normalize()).size().reindex(days, fill_value=0)


def _releases(events):
    versions = events.dropna(subset=["appVersion"]).groupby("appVersion")["event_timestamp"].min().sort_values()
    return [(str(v), ts) for v, ts in versions.items()]


def anomaly_facts(events):
    """Error-spike detection: z-scores of the last 7 days against the 21 days
    before them, then root cause (which error, endpoint and app version) for the
    flagged days and whether it lines up with the latest release."""
    as_of, this_start, _ = windows(events)
    errors, daily = _daily_errors(events, as_of)
    baseline = daily[daily.index < this_start]
    recent = daily[daily.index >= this_start]
    mean, std = float(baseline.mean()), float(baseline.std(ddof=1)) or 1.0
    z = (recent - mean) / std
    flagged_days = z[z > Z_THRESHOLD].index

    facts = {
        "lookback_days": LOOKBACK_DAYS,
        "window_days": WINDOW_DAYS,
        "z_threshold": Z_THRESHOLD,
        "baseline_errors_per_day": round(mean, 1),
        "baseline_std_dev": round(std, 1),
        "daily_errors_this_week": [
            {"date": _day(day), "errors": int(recent[day]), "z_score": round(float(z[day]), 1)} for day in recent.index
        ],
        "flagged_days": [_day(day) for day in flagged_days],
        "is_anomaly": bool(len(flagged_days)),
        # Alert only while it's still happening: a flagged day in the last 3 days.
        "alert": bool(len(flagged_days) and flagged_days.max() >= as_of - pd.Timedelta(days=3)),
    }

    if len(flagged_days):
        spike = errors[errors["event_timestamp"].dt.normalize().isin(flagged_days)]
        causes = (
            spike.fillna({"code": 0, "endpoint": "n/a"})
            .groupby(["eventName", "code", "endpoint"])
            .size()
            .sort_values(ascending=False)
            .head(3)
        )
        facts["first_flagged_day"] = _day(flagged_days.min())
        facts["peak_day"] = _day(recent[flagged_days].idxmax())
        facts["peak_day_errors"] = int(recent[flagged_days].max())
        facts["peak_vs_baseline_ratio"] = round(float(recent[flagged_days].max()) / mean, 1) if mean else None
        facts["top_causes"] = [
            {"event": e, "code": int(c), "endpoint": ep, "errors": int(n), "share_pct": _pct(n, len(spike))}
            for (e, c, ep), n in causes.items()
        ]
        facts["error_share_by_app_version_pct"] = {
            str(v): _pct(n, len(spike)) for v, n in spike["appVersion"].value_counts().items()
        }

    releases = _releases(events)
    if releases:
        version, released_at = releases[-1]
        before = events[events["event_timestamp"] < released_at]
        after = events[events["event_timestamp"] >= released_at]
        calls_before, failures_before = _export_calls(before)
        calls_after, failures_after = _export_calls(after)
        rate_before, rate_after = _pct(failures_before, calls_before), _pct(failures_after, calls_after)
        facts["latest_release"] = {"version": version, "date": _day(released_at)}
        facts["export_failure_rate_before_release_pct"] = rate_before
        facts["export_failure_rate_since_release_pct"] = rate_after
        facts["export_failure_rate_ratio"] = round(rate_after / rate_before, 1) if rate_before else None
        facts["export_failures_since_release"] = failures_after

        # Sessions whose last event is a failed export: the user gave up.
        last_events = after.sort_values("event_timestamp").groupby("sessionId").tail(1)
        failed_sessions = after[(after["eventName"] == "api_error") & (after["endpoint"] == EXPORT_ENDPOINT)]["sessionId"]
        abandoned = last_events[
            last_events["eventName"].isin(["api_error", "retry_attempt"]) & (last_events["endpoint"] == EXPORT_ENDPOINT)
        ]
        facts["sessions_hit_by_export_failure_since_release"] = int(failed_sessions.nunique())
        facts["sessions_abandoned_after_export_failure"] = int(len(abandoned))
        facts["abandoned_after_export_failure_pct"] = _pct(len(abandoned), failed_sessions.nunique())
    return native(facts)


def cohort_facts(events, sessions):
    """High vs low engagement users (split at the median sessions per user over the
    lookback window) and what the high-engagement users do differently."""
    as_of, _, _ = windows(events)
    start = as_of - pd.Timedelta(days=LOOKBACK_DAYS)
    sess = sessions[sessions["session_start_ts"] >= start]
    recent = events[events["event_timestamp"] >= start]
    per_user = sess.groupby("userId").agg(
        sessions=("sessionId", "count"), avg_minutes=("session_duration_seconds", lambda s: s.mean() / 60)
    )
    median = float(per_user["sessions"].median())
    per_user["cohort"] = (per_user["sessions"] >= max(median, 2)).map({True: "high", False: "low"})

    clicks = recent[recent["eventName"] == "feature_click"]
    users_by_feature = clicks.groupby("feature")["userId"].apply(set)
    exporters = set(recent[recent["eventName"] == "export_triggered"]["userId"])

    cohorts = {}
    for name, group in per_user.groupby("cohort"):
        ids = set(group.index)
        adoption = {f: _pct(len(ids & users), len(ids)) for f, users in users_by_feature.items()}
        cohorts[name] = {
            "users": int(len(ids)),
            "avg_sessions_per_user": round(float(group["sessions"].mean()), 1),
            "avg_session_minutes": round(float(group["avg_minutes"].mean()), 1),
            "feature_adoption_pct": adoption,
            "export_users_pct": _pct(len(ids & exporters), len(ids)),
        }

    gaps = {}
    if {"high", "low"} <= cohorts.keys():
        high, low = cohorts["high"]["feature_adoption_pct"], cohorts["low"]["feature_adoption_pct"]
        gaps = {f: round(high.get(f, 0.0) - low.get(f, 0.0), 1) for f in set(high) | set(low)}
    biggest = max(gaps, key=gaps.get) if gaps else None
    return native(
        {
            "lookback_days": LOOKBACK_DAYS,
            "median_sessions_per_user": median,
            "high_engagement_rule": f"at least {max(median, 2):g} sessions in {LOOKBACK_DAYS} days",
            "cohorts": cohorts,
            "adoption_gap_points": gaps,
            "biggest_adoption_gap_feature": biggest,
        }
    )


def funnel_facts(events):
    """Onboarding funnel for new users plus the top drop-off points across the journey."""
    onboarding_views = events[(events["eventName"] == "page_view") & events["path"].fillna("").str.endswith("#/onboarding")]
    started = set(onboarding_views["userId"])
    steps = events[events["eventName"] == "onboarding_step_complete"]
    reached = {n: set(steps[steps["step"] == n]["userId"]) & started for n in (1, 2, 3)}
    counts = {"started": len(started), **{f"step_{n}": len(reached[n]) for n in (1, 2, 3)}}

    previous = {"step_1": "started", "step_2": "step_1", "step_3": "step_2"}
    conversion = {k: _pct(counts[k], counts[p]) for k, p in previous.items()}
    losses = {k: counts[p] - counts[k] for k, p in previous.items()}
    worst = max(losses, key=losses.get)

    login_sessions = events[events["eventName"] == "login_attempt"]["sessionId"].unique()
    logged_in = set(events[events["eventName"] == "login_success"]["sessionId"])
    failed_logins = [s for s in login_sessions if s not in logged_in]

    export = anomaly_facts(events)
    dropoffs = [
        {
            "point": f"onboarding {worst.replace('_', ' ')}",
            "lost": losses[worst],
            "unit": "new users",
            "rate_pct": _pct(losses[worst], counts[previous[worst]]),
        },
        {
            "point": "gave up after a failed export (since latest release)",
            "lost": export.get("sessions_abandoned_after_export_failure", 0),
            "unit": "sessions",
            "rate_pct": export.get("abandoned_after_export_failure_pct"),
        },
        {
            "point": "sign-in never completed",
            "lost": len(failed_logins),
            "unit": "sessions",
            "rate_pct": _pct(len(failed_logins), len(login_sessions)),
        },
    ]
    dropoffs.sort(key=lambda d: d["lost"], reverse=True)
    for rank, point in enumerate(dropoffs, start=1):
        point["rank"] = rank
    return native(
        {
            "onboarding": counts,
            "step_conversion_pct": conversion,
            "users_lost_per_step": losses,
            "biggest_drop": {"step": int(worst[-1]), "users_lost": losses[worst], "conversion_pct": conversion[worst]},
            "onboarding_completion_pct": _pct(counts["step_3"], counts["started"]),
            "top_dropoff_points": dropoffs,
        }
    )


SCENARIOS = {
    "weekly_narrative": (
        "3-4 sentences",
        "Summarise how usage changed this week versus last week, name the most and least used feature, "
        "and give one concrete recommendation.",
    ),
    "anomaly_detection": (
        "2-4 sentences",
        "Say whether error volume is anomalous, when it started, the most likely cause (connect it to the "
        "latest release only if the facts support it), how it affects users, and what to investigate first.",
    ),
    "cohort_segmentation": (
        "3 sentences",
        "Describe how high- and low-engagement users differ, what high-engagement users do differently, "
        "and one action to move low-engagement users up.",
    ),
    "funnel_dropoff": (
        "3 sentences",
        "Rank the top drop-off points (biggest first), explain the onboarding drop, and suggest one fix.",
    ),
}

PROMPT = """You are a product analyst writing for non-technical stakeholders at Psiog about
ReportHub, an internal reporting app.

FACTS (JSON) are the only source of truth:
{facts}

Rules:
- Use only numbers that appear in FACTS, written exactly or rounded to fewer decimals.
  Never compute new numbers (no sums, differences or ratios that are not in FACTS).
- Write dates as YYYY-MM-DD and versions exactly as in FACTS (e.g. 2.4.0).
- Plain prose, {length}. No preamble, markdown or bullet points.
{feedback}
Task: {task}"""


def build_prompt(scenario, facts, unverified=None):
    length, task = SCENARIOS[scenario]
    feedback = ""
    if unverified:
        feedback = (
            f"\nA previous draft used numbers not in FACTS: {', '.join(unverified)}. "
            "Do not use them; rewrite using only FACTS.\n"
        )
    return PROMPT.format(facts=json.dumps(facts, indent=1), length=length, feedback=feedback, task=task)


def all_facts(events, sessions):
    return {
        "weekly_narrative": weekly_usage_facts(events, sessions),
        "anomaly_detection": anomaly_facts(events),
        "cohort_segmentation": cohort_facts(events, sessions),
        "funnel_dropoff": funnel_facts(events),
    }
