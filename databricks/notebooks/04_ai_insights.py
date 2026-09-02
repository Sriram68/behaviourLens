# Databricks notebook source
# MAGIC %md
# MAGIC # BehaviorLens — AI Insights (Gemini 2.5 Flash)
# MAGIC Three scenarios per the proposal's AI layer: a plain-language weekly
# MAGIC narrative, anomaly detection on error spikes, and engagement cohort
# MAGIC segmentation. A second Gemini call scores each narrative's relevance
# MAGIC against the source data. Run `01_ingestion`, `02_aggregations`, and
# MAGIC `03_data_quality` first. Requires a Databricks secret
# MAGIC `behaviorlens/gemini_api_key` (see docs/powerbi-guide.md's sibling,
# MAGIC docs/ai-prompt-log.md, for how it's provisioned).

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")

# COMMAND ----------

import json
import requests
from datetime import datetime, timezone

API_KEY = dbutils.secrets.get(scope="behaviorlens", key="gemini_api_key")
# gemini-2.5-flash (per the original proposal) returns 404 for new API keys as of
# this build -- the API redirects new callers to gemini-3.6-flash. Documented as a
# deviation in docs/ai-prompt-log.md.
MODEL = "gemini-3.6-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

PROMPT_VERSION = "v1"


def call_gemini(prompt, temperature=0.3):
    resp = requests.post(
        f"{GEMINI_URL}?key={API_KEY}",
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# COMMAND ----------

# MAGIC %md ## Pull aggregates (small, already-aggregated — not raw events)

# COMMAND ----------

feature_pdf = spark.table("feature_adoption_daily").groupBy("eventName").sum("event_count", "distinct_users").toPandas()
feature_pdf.columns = ["eventName", "total_events", "total_distinct_users"]

error_pdf = spark.table("error_summary_daily").toPandas()

session_pdf = spark.table("session_summary").toPandas()

feature_summary_text = feature_pdf.to_string(index=False)
error_daily_text = error_pdf.groupby(["event_date", "eventName"])["event_count"].sum().reset_index().to_string(index=False)

print(feature_summary_text)

# COMMAND ----------

# MAGIC %md ## Scenario 1 — Weekly usage narrative

# COMMAND ----------

narrative_prompt = f"""You are a product analyst. Below is aggregated feature-usage data
for a behavioral analytics platform called BehaviorLens, covering 4 feature events
(feature_click, search_query, filter_applied, export_triggered) across several months
of synthetic usage data.

Feature usage totals:
{feature_summary_text}

Write a 3-4 sentence plain-language narrative for a non-technical stakeholder:
which features are most/least adopted, and one concrete recommendation. No preamble,
just the narrative."""

narrative_text = call_gemini(narrative_prompt)
print(narrative_text)

# COMMAND ----------

# MAGIC %md ## Scenario 2 — Anomaly detection on error spikes

# COMMAND ----------

import pandas as pd

error_by_day = error_pdf.groupby("event_date")["event_count"].sum().reset_index()
mean_errors = error_by_day["event_count"].mean()
std_errors = error_by_day["event_count"].std()
error_by_day["z_score"] = (error_by_day["event_count"] - mean_errors) / std_errors if std_errors > 0 else 0
anomalies = error_by_day[error_by_day["z_score"].abs() > 1.5].sort_values("z_score", ascending=False)

anomaly_prompt = f"""You are a product analyst reviewing daily error-event counts for
BehaviorLens. Baseline: mean {mean_errors:.1f} errors/day, std dev {std_errors:.1f}.

Days flagged as statistical outliers (|z-score| > 1.5):
{anomalies.to_string(index=False) if not anomalies.empty else "None flagged."}

Write a 2-3 sentence anomaly commentary a non-technical stakeholder would understand:
what happened and whether it warrants investigation. No preamble."""

anomaly_text = call_gemini(anomaly_prompt)
print(anomaly_text)

# COMMAND ----------

# MAGIC %md ## Scenario 3 — Engagement cohort segmentation

# COMMAND ----------

median_events = session_pdf["event_count"].median()
session_pdf["cohort"] = session_pdf["event_count"].apply(lambda x: "High engagement" if x >= median_events else "Low engagement")
cohort_summary = session_pdf.groupby("cohort").agg(
    sessions=("sessionId", "count"),
    avg_events=("event_count", "mean"),
    distinct_users=("userId", "nunique"),
).reset_index()

cohort_prompt = f"""You are a product analyst segmenting BehaviorLens sessions into two
cohorts by engagement (split at the median event count per session):

{cohort_summary.to_string(index=False)}

Write a 3-4 sentence cohort narrative for a non-technical stakeholder: how the cohorts
differ and one suggested action to move low-engagement sessions toward high-engagement
behavior. No preamble."""

cohort_text = call_gemini(cohort_prompt)
print(cohort_text)

# COMMAND ----------

# MAGIC %md ## Quality scoring — a second Gemini call rates each narrative's relevance

# COMMAND ----------

def score_relevance(source_data, narrative):
    scoring_prompt = f"""Source data:
{source_data}

Narrative under review:
{narrative}

Rate this narrative's factual accuracy and relevance to the source data on a 0-100
scale. Respond with ONLY the integer score, nothing else."""
    raw = call_gemini(scoring_prompt, temperature=0.0)
    digits = "".join(c for c in raw if c.isdigit())
    return int(digits) if digits else None


narrative_score = score_relevance(feature_summary_text, narrative_text)
anomaly_score = score_relevance(error_daily_text, anomaly_text)
cohort_score = score_relevance(cohort_summary.to_string(index=False), cohort_text)

print(f"Narrative relevance: {narrative_score}")
print(f"Anomaly relevance:   {anomaly_score}")
print(f"Cohort relevance:    {cohort_score}")

# COMMAND ----------

# MAGIC %md ## Persist for auditability + Power BI

# COMMAND ----------

run_ts = datetime.now(timezone.utc)
rows = [
    ("weekly_narrative", PROMPT_VERSION, narrative_text, narrative_score, run_ts),
    ("anomaly_detection", PROMPT_VERSION, anomaly_text, anomaly_score, run_ts),
    ("cohort_segmentation", PROMPT_VERSION, cohort_text, cohort_score, run_ts),
]
insights_df = spark.createDataFrame(rows, ["scenario", "prompt_version", "narrative", "relevance_score", "generated_at"])
insights_df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("ai_insights_weekly")
display(insights_df)

avg_score = sum(s for s in [narrative_score, anomaly_score, cohort_score] if s is not None) / 3
print(f"Average relevance score: {avg_score:.1f}/100")
assert avg_score >= 85, f"Average relevance score {avg_score:.1f} below the 85 target"
