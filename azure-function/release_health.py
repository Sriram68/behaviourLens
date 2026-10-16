"""Release health for BehaviorLens Live: which release hurt real users, how many
gave up, and what to fix first.

Everything shown comes from the pipeline's anomaly facts pack (computed in code,
the same numbers its fact-checked narrative was verified against). The verdict
is a fixed rule here, never the model's opinion.
"""

import json
import time

import ask

SQL = (
    "SELECT facts_json, narrative, status, facts_verified, facts_total, generated_at "
    "FROM ai_insights_weekly WHERE scenario = 'anomaly_detection' LIMIT 1"
)
CACHE_SECONDS = 120
# Roll back when the spike is current, the release made exports at least this
# many times worse, and most of the spike's errors come from that release.
ROLLBACK_MIN_RATIO = 3
ROLLBACK_MIN_SHARE_PCT = 50

_cache = {"at": 0.0, "value": None}


def verdict(facts):
    """(level, headline): rollback, investigate or healthy."""
    version = (facts.get("latest_release") or {}).get("version")
    ratio = facts.get("export_failure_rate_ratio") or 0
    share = (facts.get("error_share_by_app_version_pct") or {}).get(version, 0) if version else 0
    if facts.get("alert") and version and ratio >= ROLLBACK_MIN_RATIO and share >= ROLLBACK_MIN_SHARE_PCT:
        return "rollback", f"Roll back or hotfix {version}"
    if facts.get("is_anomaly"):
        return "investigate", "Investigate the error spike"
    return "healthy", "No release problem detected"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def summarize(row):
    facts = json.loads(row.get("facts_json") or "{}")
    level, headline = verdict(facts)
    release = facts.get("latest_release") or {}
    top = (facts.get("top_causes") or [{}])[0]
    return {
        "level": level,
        "headline": headline,
        "release": release,
        "firstFlaggedDay": facts.get("first_flagged_day"),
        "exportFailureBeforePct": facts.get("export_failure_rate_before_release_pct"),
        "exportFailureSincePct": facts.get("export_failure_rate_since_release_pct"),
        "exportFailureRatio": facts.get("export_failure_rate_ratio"),
        "sessionsHit": facts.get("sessions_hit_by_export_failure_since_release"),
        "sessionsAbandoned": facts.get("sessions_abandoned_after_export_failure"),
        "abandonedPct": facts.get("abandoned_after_export_failure_pct"),
        "errorSharePct": (facts.get("error_share_by_app_version_pct") or {}).get(release.get("version")),
        "topCause": {k: top.get(k) for k in ("event", "code", "endpoint", "share_pct")} if top else None,
        "narrative": row.get("narrative") or "",
        "status": row.get("status"),
        "factsVerified": _int(row.get("facts_verified")),
        "factsTotal": _int(row.get("facts_total")),
        "generatedAt": row.get("generated_at"),
    }


def latest(max_wait_seconds=40):
    """The current release health, cached briefly; None if Ask's Databricks access isn't configured
    or the pipeline hasn't produced an anomaly row yet."""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    if not ask.is_configured():
        return None
    columns, rows = ask.run_sql(SQL, max_wait_seconds=max_wait_seconds)
    if not rows:
        return None
    value = summarize(dict(zip(columns, rows[0])))
    _cache.update(at=now, value=value)
    return value
