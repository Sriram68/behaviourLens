# BehaviorLens — Architecture (Wednesday Checkpoint Scope)

Scope for this checkpoint: instrumentation, ingestion, and the Databricks pipeline.
Power BI dashboards and Gemini AI insights are deferred to Phase 2 (see the [proposal](../S3-C-01_BehaviorLens_Proposal.md)).

## Data flow

```
Demo app (browser)
   │  BehaviorLens JS SDK: batches events, validates schema
   ▼
Azure Function — POST /api/events
   │  writes NDJSON batches
   ▼
Azure Blob Storage (raw-events container)
   │  synced to a Unity Catalog Volume
   ▼
Databricks Free Edition
   │  01_ingestion.py   → events_raw, events_enriched (Delta)
   │  02_aggregations.py → session_summary, feature_adoption_daily, error_summary_daily (Delta)
   │  03_data_quality.py → Great Expectations checks → qa_data_quality_results (Delta)
   ▼
(Phase 2: Power BI dashboards + Gemini AI insight narratives)
```

## One remaining deliberate substitution, and one now resolved

### Resolved: Azure Function is deployed to Azure

At the Week 10 checkpoint, the target Azure subscription's resource group (`rg-pSiddhi3.0-2026-01-sem3-Sriram`) restricted `Microsoft.Web/ServerFarms/write` and `Microsoft.Web/Sites/write`, blocking Function App creation, so the Function ran locally via Core Tools against real Azure Storage.

For Phase 2, the ingestion Function (`azure-function/function_app.py`, unchanged code) is deployed for real on a personal Azure subscription (`rg-behaviorlens`, `func-behaviorlens47209`, Python 3.11 on Linux Consumption) since the restricted `pSiddhi` subscription was never unblocked. Live endpoint: `https://func-behaviorlens47209.azurewebsites.net/api/events`, writing to the `raw-events` container on `stbehaviorlens47209`.

### Databricks reads from a Unity Catalog Volume, not directly from Blob Storage

Databricks Free Edition runs on Databricks-managed infrastructure (confirmed via the Unity Catalog metastore: `metastore_aws_us_east_2`), not inside the Azure subscription. Mounting an external Azure Blob container would require configuring Unity Catalog storage credentials and external locations across clouds — a genuine setup path, but not one to gamble a 2-day deadline on.

Instead, NDJSON files are synced from the `raw-events` Blob container into a Unity Catalog Volume (`workspace.default.raw_events`) via the Databricks Files REST API, and `01_ingestion.py` reads from that Volume path. The transformation logic (schema validation, deduplication, sessionization, aggregation) is identical to what would run against a direct Blob mount.

## Notebooks

| Notebook | Produces | Notes |
|---|---|---|
| `01_ingestion.py` | `events_raw`, `events_enriched` | Schema validation, dedup on natural key, sessionization via window functions |
| `02_aggregations.py` | `session_summary`, `feature_adoption_daily`, `error_summary_daily` | Matches the proposal's aggregation table list exactly |
| `03_data_quality.py` | `qa_data_quality_results` | Great Expectations checks (via Pandas — see QA report for why) |

## Validated at scale

811 synthetic events (60 users, 3 flows, timestamps spread across 3 days) processed end-to-end — ingestion → aggregation → data quality — in 120s wall clock on serverless compute, including cold-start. See [qa-report.md](qa-report.md) for full results.
