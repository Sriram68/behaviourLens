# Databricks notebook source
# MAGIC %md
# MAGIC # BehaviorLens — Aggregations
# MAGIC Computes `session_summary`, `feature_adoption_daily`, and `error_summary_daily`
# MAGIC from `events_enriched`. Run `01_ingestion` first.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")

# COMMAND ----------

from pyspark.sql import functions as F

events_enriched = spark.table("events_enriched")

FEATURE_EVENTS = ["feature_click", "search_query", "filter_applied", "export_triggered"]
ERROR_EVENTS = ["api_error", "page_not_found", "retry_attempt"]

# COMMAND ----------

# MAGIC %md ## session_summary

# COMMAND ----------

session_summary = (
    events_enriched.groupBy("sessionId", "userId")
    .agg(
        F.min("event_timestamp").alias("session_start_ts"),
        F.max("event_timestamp").alias("session_end_ts"),
        F.first("session_duration_seconds").alias("session_duration_seconds"),
        F.count("*").alias("event_count"),
        F.countDistinct("eventName").alias("distinct_event_types"),
    )
)

session_summary.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("session_summary")
print(f"session_summary row count: {session_summary.count()}")

# COMMAND ----------

# MAGIC %md ## feature_adoption_daily

# COMMAND ----------

feature_adoption_daily = (
    events_enriched
    .filter(F.col("eventName").isin(FEATURE_EVENTS))
    .withColumn("event_date", F.to_date("event_timestamp"))
    .groupBy("event_date", "eventName")
    .agg(
        F.count("*").alias("event_count"),
        F.countDistinct("userId").alias("distinct_users"),
    )
    .orderBy("event_date", "eventName")
)

feature_adoption_daily.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("feature_adoption_daily")
print(f"feature_adoption_daily row count: {feature_adoption_daily.count()}")

# COMMAND ----------

# MAGIC %md ## error_summary_daily

# COMMAND ----------

error_summary_daily = (
    events_enriched
    .filter(F.col("eventName").isin(ERROR_EVENTS))
    .withColumn("event_date", F.to_date("event_timestamp"))
    .groupBy("event_date", "eventName")
    .agg(
        F.count("*").alias("event_count"),
        F.countDistinct("userId").alias("distinct_users"),
        F.countDistinct("sessionId").alias("distinct_sessions"),
    )
    .orderBy(F.desc("event_count"))
)

error_summary_daily.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("error_summary_daily")
print(f"error_summary_daily row count: {error_summary_daily.count()}")
display(error_summary_daily)
