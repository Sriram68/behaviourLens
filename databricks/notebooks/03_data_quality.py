# Databricks notebook source
# MAGIC %md
# MAGIC # BehaviorLens — Data Quality (Great Expectations)
# MAGIC Validates `events_raw`, `events_enriched`, `session_summary`, `feature_adoption_daily`,
# MAGIC and `error_summary_daily` after each pipeline run. Run `01_ingestion` and `02_aggregations` first.

# COMMAND ----------

# MAGIC %pip install great_expectations==0.15.50 -q

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
spark.sql(f"USE CATALOG {catalog}")
spark.sql(f"USE SCHEMA {schema}")

# COMMAND ----------

from great_expectations.dataset import SparkDFDataset

KNOWN_EVENT_NAMES = [
    "login_attempt", "login_success", "onboarding_step_complete",
    "feature_click", "search_query", "filter_applied", "export_triggered",
    "api_error", "page_not_found", "retry_attempt",
]

results = {}


def validate(table_name, expectations_fn):
    df = spark.table(table_name)
    ge_df = SparkDFDataset(df)
    results[table_name] = expectations_fn(ge_df)

# COMMAND ----------

validate("events_raw", lambda ge_df: [
    ge_df.expect_column_values_to_not_be_null("eventName"),
    ge_df.expect_column_values_to_not_be_null("userId"),
    ge_df.expect_column_values_to_not_be_null("sessionId"),
    ge_df.expect_column_values_to_not_be_null("event_timestamp"),
    ge_df.expect_column_values_to_be_in_set("eventName", KNOWN_EVENT_NAMES),
])

# COMMAND ----------

validate("events_enriched", lambda ge_df: [
    ge_df.expect_column_values_to_not_be_null("session_event_seq"),
    ge_df.expect_column_values_to_not_be_null("session_start_ts"),
    ge_df.expect_column_values_to_be_between("session_duration_seconds", min_value=0, max_value=None),
])

# COMMAND ----------

validate("session_summary", lambda ge_df: [
    ge_df.expect_column_values_to_not_be_null("sessionId"),
    ge_df.expect_column_values_to_be_unique("sessionId"),
    ge_df.expect_column_values_to_be_between("event_count", min_value=1, max_value=None),
])

# COMMAND ----------

validate("feature_adoption_daily", lambda ge_df: [
    ge_df.expect_column_values_to_be_between("event_count", min_value=0, max_value=None),
    ge_df.expect_column_values_to_be_in_set("eventName", KNOWN_EVENT_NAMES),
])

# COMMAND ----------

validate("error_summary_daily", lambda ge_df: [
    ge_df.expect_column_values_to_be_between("event_count", min_value=0, max_value=None),
    ge_df.expect_column_values_to_be_in_set("eventName", KNOWN_EVENT_NAMES),
])

# COMMAND ----------

summary_rows = []
all_passed = True
for table_name, result_list in results.items():
    for res in result_list:
        success = bool(res["success"])
        all_passed = all_passed and success
        summary_rows.append({
            "table": table_name,
            "expectation": res["expectation_config"]["expectation_type"],
            "column": res["expectation_config"]["kwargs"].get("column"),
            "success": success,
        })

summary_df = spark.createDataFrame(summary_rows)
summary_df.write.format("delta").mode("overwrite").saveAsTable("qa_data_quality_results")
display(summary_df)

print(f"{sum(r['success'] for r in summary_rows)}/{len(summary_rows)} data quality checks passed")
assert all_passed, "One or more Great Expectations checks failed — see qa_data_quality_results"
