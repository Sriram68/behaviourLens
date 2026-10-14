# Databricks notebook source
# MAGIC %md
# MAGIC # BehaviorLens — AI Insights (Gemini)
# MAGIC Four scenarios: a week-over-week usage narrative, error-spike anomaly detection with
# MAGIC root cause, engagement cohorts, and drop-off analysis.
# MAGIC
# MAGIC **Numbers come from code, words come from the model.** `insights.py` computes a facts pack
# MAGIC per scenario; Gemini narrates it; `factcheck.py` verifies that every number, date and version
# MAGIC in the narrative appears in the pack. A narrative that fails gets one rewrite with the
# MAGIC offending numbers named; if it still fails it is stored as `needs_review` and never shown as
# MAGIC verified. A second Gemini call still scores relevance (0-100) as a softer signal.
# MAGIC
# MAGIC When the anomaly is current, a verified alert goes to Teams if the secret
# MAGIC `behaviorlens/teams_webhook_url` exists: once per incident (release + first flagged day),
# MAGIC tracked in `ai_alert_log`. The `alert_mode` widget overrides that: `never` (e.g. while
# MAGIC regenerating demo data) or `always` (re-send). Requires `behaviorlens/gemini_api_key`.
# MAGIC Run `01_ingestion`, `02_aggregations` and `03_data_quality` first.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
dbutils.widgets.text("live_url", "https://sriram68.github.io/behaviourLens/demo-app/live.html", "Live dashboard URL")
dbutils.widgets.dropdown("alert_mode", "once", ["once", "always", "never"], "Teams alert mode")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
live_url = dbutils.widgets.get("live_url")
alert_mode = dbutils.widgets.get("alert_mode")
spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

import json
from datetime import datetime, timezone

import requests
from pyspark.sql import functions as F

# Workspace files next to this notebook, byte-identical to the Azure Function's
# copies (enforced by azure-function/tests/test_factcheck.py and test_gemini.py).
import factcheck
import gemini
import insights

API_KEY = dbutils.secrets.get(scope="behaviorlens", key="gemini_api_key")
# gemini-2.5-flash (per the original proposal) returns 404 for new API keys; see
# docs/ai-prompt-log.md. gemini.MODELS lists the primary model and its fallbacks.
PROMPT_VERSION = "v2"


def call_gemini(prompt, temperature=0.3):
    """Returns (text, model_used): retries overloaded models, then falls back."""
    return gemini.generate(prompt, API_KEY, temperature=temperature)

# COMMAND ----------

# MAGIC %md ## Facts packs (computed, not generated)

# COMMAND ----------

enriched = spark.table("events_enriched")
events_pdf = enriched.select(
    "eventName", "userId", "sessionId", "event_timestamp", "appVersion", "feature", "endpoint",
    F.col("error_code").alias("code"),
    F.col("onboarding_step").alias("step"),
    F.col("properties.path").alias("path") if "path" in enriched.schema["properties"].dataType.fieldNames()
    else F.lit(None).cast("string").alias("path"),
).toPandas()
sessions_pdf = spark.table("session_summary").select(
    "sessionId", "userId", "session_start_ts", "session_duration_seconds", "event_count"
).toPandas()

packs = insights.all_facts(events_pdf, sessions_pdf)
print(json.dumps(packs["anomaly_detection"], indent=1)[:3000])

# COMMAND ----------

# MAGIC %md ## Narrate, verify, self-correct once

# COMMAND ----------


def score_relevance(facts, narrative):
    raw, _ = call_gemini(
        f"Source facts:\n{json.dumps(facts)}\n\nNarrative under review:\n{narrative}\n\n"
        "Rate this narrative's relevance and usefulness to a product stakeholder, given the facts, "
        "on a 0-100 scale. Respond with ONLY the integer score.",
        temperature=0.0,
    )
    digits = "".join(c for c in raw if c.isdigit())
    return min(int(digits), 100) if digits else None


results = []
for scenario, facts in packs.items():
    unverified = None
    try:
        for attempt in (1, 2):
            narrative, model_used = call_gemini(insights.build_prompt(scenario, facts, unverified))
            check = factcheck.verify(narrative, facts)
            if not check["unverified"]:
                break
            unverified = check["unverified"]
            print(f"{scenario}: attempt {attempt} used unverified numbers {unverified}")
        status, score = ("verified" if not check["unverified"] else "needs_review"), score_relevance(facts, narrative)
    except gemini.GeminiUnavailable as err:
        # Every model stayed overloaded: record it and keep the other scenarios.
        print(f"{scenario}: Gemini unavailable ({err})")
        narrative, model_used, status, score, attempt = "", None, "generation_failed", None, 0
        check = {"total": 0, "verified": 0, "unverified": []}
    results.append(
        {
            "scenario": scenario,
            "model": model_used,
            "narrative": narrative,
            "status": status,
            "facts_total": check["total"],
            "facts_verified": check["verified"],
            "unverified_facts": ", ".join(check["unverified"]),
            "attempts": attempt,
            "relevance_score": score,
            "facts_json": json.dumps(facts),
        }
    )
    print(f"\n[{scenario}] {results[-1]['status']} {check['verified']}/{check['total']}\n{narrative}")

# COMMAND ----------

# MAGIC %md ## Teams alert when the anomaly is happening now

# COMMAND ----------


def teams_card(narrative, facts, check_line):
    rows = []
    if facts.get("first_flagged_day"):
        rows.append({"title": "Started", "value": facts["first_flagged_day"]})
    if facts.get("latest_release"):
        release = facts["latest_release"]
        rows.append({"title": "Latest release", "value": f"{release['version']} ({release['date']})"})
    if facts.get("export_failure_rate_since_release_pct") is not None:
        rows.append({
            "title": "Export failure rate",
            "value": f"{facts['export_failure_rate_before_release_pct']}% before, "
                     f"{facts['export_failure_rate_since_release_pct']}% since",
        })
    if facts.get("sessions_abandoned_after_export_failure"):
        rows.append({
            "title": "Sessions abandoned",
            "value": f"{facts['sessions_abandoned_after_export_failure']} after a failed export",
        })
    rows.append({"title": "Fact check", "value": check_line})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True,
                     "text": "BehaviorLens alert: error spike in ReportHub"},
                    {"type": "TextBlock", "wrap": True, "text": narrative},
                    {"type": "FactSet", "facts": rows},
                ],
                "actions": [{"type": "Action.OpenUrl", "title": "Open BehaviorLens Live", "url": live_url}],
            },
        }],
    }


anomaly = packs["anomaly_detection"]
anomaly_row = next(r for r in results if r["scenario"] == "anomaly_detection")
release = anomaly.get("latest_release") or {}
# One incident = the release it lines up with + the day it started; alert once per incident.
incident_key = f"{release.get('version', 'n/a')}|{anomaly.get('first_flagged_day', 'n/a')}"
spark.sql("CREATE TABLE IF NOT EXISTS ai_alert_log (incident_key STRING, sent_at TIMESTAMP)")
already_sent = spark.sql(
    "SELECT max(sent_at) AS sent_at FROM ai_alert_log WHERE incident_key = :key", args={"key": incident_key}
).first()["sent_at"]

alert_status = "not needed: no current anomaly"
if anomaly["alert"]:
    if anomaly_row["status"] != "verified":
        alert_status = "held: narrative failed fact check"
    elif alert_mode == "never":
        alert_status = "suppressed: alert_mode=never"
    elif alert_mode == "once" and already_sent is not None:
        alert_status = f"skipped: already alerted for {incident_key} at {already_sent:%Y-%m-%d %H:%M} UTC"
    else:
        try:
            webhook = dbutils.secrets.get(scope="behaviorlens", key="teams_webhook_url")
        except Exception:  # noqa: BLE001 - secret is optional
            webhook = None
        if not webhook:
            alert_status = "skipped: no teams_webhook_url secret"
        else:
            check_line = f"{anomaly_row['facts_verified']}/{anomaly_row['facts_total']} numbers verified"
            try:
                resp = requests.post(webhook, json=teams_card(anomaly_row["narrative"], anomaly, check_line), timeout=30)
                alert_status = "sent" if resp.status_code < 300 else f"failed: HTTP {resp.status_code}"
            except requests.RequestException as err:
                alert_status = f"failed: {type(err).__name__}"
            if alert_status == "sent":
                spark.sql(
                    "INSERT INTO ai_alert_log VALUES (:key, current_timestamp())", args={"key": incident_key}
                )
print(f"Teams alert ({alert_mode}, incident {incident_key}): {alert_status}")

# COMMAND ----------

# MAGIC %md ## Persist for auditability + Power BI

# COMMAND ----------

run_ts = datetime.now(timezone.utc)
for row in results:
    row.update({
        "prompt_version": PROMPT_VERSION,
        "generated_at": run_ts,
        "alert_status": alert_status if row["scenario"] == "anomaly_detection" else None,
    })

columns = [
    "scenario", "prompt_version", "model", "narrative", "status", "facts_total", "facts_verified",
    "unverified_facts", "attempts", "relevance_score", "alert_status", "facts_json", "generated_at",
]
schema_ddl = (
    "scenario string, prompt_version string, model string, narrative string, status string, "
    "facts_total int, facts_verified int, unverified_facts string, attempts int, relevance_score int, "
    "alert_status string, facts_json string, generated_at timestamp"
)
insights_df = spark.createDataFrame([tuple(r[c] for c in columns) for r in results], schema_ddl)
insights_df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("ai_insights_weekly")
# Every run is kept, so prompt changes can be compared over time (the prompt version log).
insights_df.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable("ai_insights_history")
display(insights_df.drop("facts_json"))

verified = sum(r["status"] == "verified" for r in results)
print(f"{verified}/{len(results)} narratives fully fact-checked")
# Fail loudly if AI quality collapses, rather than publishing unverified insights.
assert verified >= 3, f"Only {verified}/{len(results)} narratives passed the fact check"
