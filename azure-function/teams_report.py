"""The "Send report to Teams" button on BehaviorLens Live.

The Teams webhook URL is a credential (anyone holding it can post to the
channel), so it lives in the Function's settings (TEAMS_WEBHOOK_URL) and the
card is built here, never in the public page. A report combines the live
snapshot the presenter is looking at with the latest fact-checked AI insight
from the Databricks pipeline; the insight is best-effort so a sleeping SQL
warehouse can't block the send.
"""

import logging
import os

import ask

LIVE_URL = os.environ.get("LIVE_URL", "https://sriram68.github.io/behaviourLens/demo-app/live.html")
INSIGHT_SQL = (
    "SELECT narrative, status, facts_verified, facts_total, generated_at "
    "FROM ai_insights_weekly WHERE scenario = 'anomaly_detection' LIMIT 1"
)


def webhook_url():
    return os.environ.get("TEAMS_WEBHOOK_URL")


def latest_insight(max_wait_seconds=40):
    """The pipeline's latest anomaly narrative, or None if it can't be read quickly."""
    if not ask.is_configured():
        return None
    try:
        columns, rows = ask.run_sql(INSIGHT_SQL, max_wait_seconds=max_wait_seconds)
    except Exception:  # noqa: BLE001 - the live snapshot is still worth sending
        logging.exception("Teams report: could not read the latest AI insight")
        return None
    return dict(zip(columns, rows[0])) if rows else None


def _export_line(exports):
    calls = exports["attempts"] + exports["retries"]
    if not calls:
        return "no exports yet"
    rate = round(100 * exports["failures"] / calls)
    return f"{rate}% ({exports['failures']} of {calls} export calls failed)"


def build_card(stats, insight, sent_at):
    totals, by_event = stats["totals"], stats["byEvent"]
    errors = (
        f"{totals['errors']} ({by_event.get('api_error', 0)} API · {by_event.get('js_error', 0)} JS · "
        f"{by_event.get('page_not_found', 0)} not found · {by_event.get('retry_attempt', 0)} retries)"
    )
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True,
         "text": "BehaviorLens report: ReportHub"},
        {"type": "TextBlock", "isSubtle": True, "spacing": "None", "wrap": True,
         "text": f"Sent from BehaviorLens Live at {sent_at:%Y-%m-%d %H:%M} UTC"},
        {"type": "TextBlock", "weight": "Bolder", "wrap": True,
         "text": f"Live snapshot: last {stats['windowMinutes']} minutes"},
        {"type": "FactSet", "facts": [
            {"title": "Events", "value": str(totals["events"])},
            {"title": "Sessions", "value": str(totals["sessions"])},
            {"title": "Errors", "value": errors},
            {"title": "Export failure rate", "value": _export_line(stats["exports"])},
        ]},
    ]
    if insight and insight.get("narrative"):
        verified = (
            f"✓ {insight['facts_verified']}/{insight['facts_total']} numbers verified"
            if insight.get("status") == "verified"
            else f"⚠ {insight.get('status')}: not fact-checked"
        )
        body += [
            {"type": "TextBlock", "weight": "Bolder", "wrap": True,
             "text": f"Latest AI insight (pipeline run {str(insight.get('generated_at', ''))[:16].replace('T', ' ')} UTC)"},
            {"type": "TextBlock", "wrap": True, "text": insight["narrative"]},
            {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": verified},
        ]
    else:
        body.append({"type": "TextBlock", "isSubtle": True, "wrap": True,
                     "text": "Latest AI insight unavailable right now (pipeline data not reachable)."})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
                "actions": [{"type": "Action.OpenUrl", "title": "Open BehaviorLens Live", "url": LIVE_URL}],
            },
        }],
    }
