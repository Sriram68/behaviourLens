# Databricks notebook source
# MAGIC %md
# MAGIC # BehaviorLens — Ingestion & Enrichment
# MAGIC Reads raw NDJSON event files landed in a Unity Catalog Volume (synced from Azure Blob Storage),
# MAGIC deduplicates, validates schema, and produces `events_raw` and `events_enriched` Delta tables.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
dbutils.widgets.text("raw_path", "/Volumes/workspace/default/raw_events", "Raw NDJSON volume path")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
raw_path = dbutils.widgets.get("raw_path")

spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")

# COMMAND ----------

from pyspark.sql import functions as F

raw_df = spark.read.json(raw_path)

expected_columns = {"eventName", "userId", "sessionId", "timestamp", "pageUrl", "properties"}
missing = expected_columns - set(raw_df.columns)
assert not missing, f"Ingested data is missing required columns: {missing}"

# COMMAND ----------

# Schema validation: drop rows missing any required top-level field
validated_df = raw_df.filter(
    F.col("eventName").isNotNull()
    & F.col("userId").isNotNull()
    & F.col("sessionId").isNotNull()
    & F.col("timestamp").isNotNull()
    & F.col("pageUrl").isNotNull()
)

dropped_count = raw_df.count() - validated_df.count()
print(f"Dropped {dropped_count} rows failing schema validation")

# COMMAND ----------

# Deduplication on natural key + typed timestamp + ingestion metadata
events_raw = (
    validated_df
    .withColumn("event_timestamp", F.to_timestamp("timestamp"))
    .withColumn("ingestion_ts", F.current_timestamp())
    .dropDuplicates(["eventName", "userId", "sessionId", "timestamp"])
)

events_raw.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("events_raw")
print(f"events_raw row count: {events_raw.count()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sessionization
# MAGIC Enrich each event with session-level context: sequence number within the session,
# MAGIC session start/end timestamps, and session duration.

# COMMAND ----------

from pyspark.sql.window import Window

session_window = Window.partitionBy("sessionId").orderBy("event_timestamp")
session_bounds_window = Window.partitionBy("sessionId")

events_enriched = (
    events_raw
    .withColumn("session_event_seq", F.row_number().over(session_window))
    .withColumn("session_start_ts", F.min("event_timestamp").over(session_bounds_window))
    .withColumn("session_end_ts", F.max("event_timestamp").over(session_bounds_window))
    .withColumn(
        "session_duration_seconds",
        F.col("session_end_ts").cast("long") - F.col("session_start_ts").cast("long"),
    )
)

events_enriched.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("events_enriched")
print(f"events_enriched row count: {events_enriched.count()}")
display(events_enriched.limit(20))
