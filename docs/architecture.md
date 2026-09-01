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

For Phase 2, the ingestion Function (`azure-function/function_app.py`, unchanged code) is deployed for real on a personal Azure subscription (`rg-behaviorlens`, `func-behaviorlens47209`, Python 3.11 on Linux Consumption) since the restricted `pSiddhi` subscription was never unblocked. Live endpoint: `https://func-behaviorlens47209.azurewebsites.net/api/events`, writing to the `raw-events` container on `stbehaviorlens47209`. CORS is explicitly configured (`http://localhost:5500`) since a real deployment doesn't get the permissive default `func start` applies locally — see the QA report for how this was caught before it silently broke browser-based ingestion.

### Databricks reads from a Unity Catalog Volume, not directly from Blob Storage

Databricks Free Edition runs on Databricks-managed infrastructure (confirmed via the Unity Catalog metastore: `metastore_aws_us_east_2`), not inside the Azure subscription. Mounting an external Azure Blob container would require configuring Unity Catalog storage credentials and external locations across clouds — a genuine setup path, but not one to gamble a 2-day deadline on.

Instead, NDJSON files are synced from the `raw-events` Blob container into a Unity Catalog Volume (`workspace.default.raw_events`) via the Databricks Files REST API — now a committed, reusable script (`databricks/sync_raw_events.py`) rather than an undocumented manual step. The transformation logic (schema validation, deduplication, sessionization, aggregation) is identical to what would run against a direct Blob mount.

### Databricks workspace changed for Phase 2

The mid-term work was done in a Databricks Free Edition workspace under a different sign-in than the one available for Phase 2; that original workspace's login couldn't be located, so Phase 2 continues in a new Free Edition workspace (`dbc-6051f46c-210c.cloud.databricks.com`). No data is actually lost — the 3 notebooks are the source of truth and fully regenerate `events_raw` through `qa_data_quality_results` from raw events on any workspace. The scale-test numbers below reflect this new workspace, seeded with both the original 811 mid-term events (already present in its `raw_events` Volume) and the new Phase 2 batch.

## Notebooks

| Notebook | Produces | Notes |
|---|---|---|
| `01_ingestion.py` | `events_raw`, `events_enriched` | Schema validation, dedup on natural key, sessionization via window functions |
| `02_aggregations.py` | `session_summary`, `feature_adoption_daily`, `error_summary_daily` | Matches the proposal's aggregation table list exactly |
| `03_data_quality.py` | `qa_data_quality_results` | Great Expectations checks (via Pandas — see QA report for why) |

## Validated at scale

5,992 events (811 from the mid-term run + 5,180 from the Phase 2 scale test, 60 synthetic users, 3 flows) processed end-to-end — ingestion → aggregation → data quality — in 123s wall clock on serverless compute, including cold-start. See [qa-report.md](qa-report.md) for full results.
