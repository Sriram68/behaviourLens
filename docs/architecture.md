# BehaviorLens — Architecture

## Data flow

```
ReportHub (browser, demo-app/index.html)
   │  BehaviorLens JS SDK: autocapture, batching, retry/backoff, offline queue, PII masking
   ▼
Azure Function ── POST /api/events: validate, rate-limit, scrub PII, write NDJSON
   │                  │
   │                  └─ GET /api/stats ──► BehaviorLens Live (demo-app/live.html), seconds old
   ▼
Azure Blob Storage (raw-events container, YYYY/MM/DD/<uuid>.ndjson)
   │  databricks/sync_raw_events.py (incremental) → Unity Catalog Volume raw_events_v2
   ▼
Databricks Free Edition — "BehaviorLens Pipeline" Job (databricks/run_pipeline.py)
   │  01_ingestion.py    → events_raw, events_enriched (Delta; flattened feature/step/error columns)
   │  02_aggregations.py → session_summary, feature_adoption_daily, feature_usage_daily,
   │                       error_summary_daily, error_detail_daily
   │  03_data_quality.py → 22 Great Expectations checks → qa_data_quality_results
   │  04_ai_insights.py  → insights.py facts → Gemini → factcheck.py → ai_insights_weekly
   │                       (+ ai_insights_history, + Teams alert when an anomaly is current)
   ▼
Power BI (Import mode) — 5 pages: Usage Trends, User Journeys, Feature Adoption, Error Patterns, AI Insights

BehaviorLens Live ── POST /api/ask ──► Gemini writes SQL → sqlglot guardrail → SQL warehouse
                                       (read-only service principal) → Gemini narrates → factcheck.py
```

## Two speeds: real time and analytics

- **Real time (seconds):** `GET /api/stats` lists blobs written in the last N minutes, downloads
  each once (blobs are write-once, so they're cached per instance), and aggregates events whose own
  timestamp falls in the window. Backfilled history is excluded even if it was written a moment ago.
  This powers the live screen during the demo.
- **Analytics (minutes):** the Databricks job. Everything historical, every dashboard and every AI
  narrative comes from here.

## Trusting the AI layer

- **Numbers come from code.** `databricks/notebooks/insights.py` (pure pandas, unit-tested against the
  simulated history) computes a facts pack per scenario: week-over-week usage, anomaly detection
  (z-score of the last 7 days against the 21 before) with root cause and release correlation,
  engagement cohorts, drop-off points.
- **Words come from the model, checked.** `factcheck.py` extracts every number, ISO date and version
  string from a narrative and requires each to match the facts pack (rounding allowed, ratios may be
  written as percentages). A failing narrative gets one rewrite naming the bad numbers; if it still
  fails it's stored as `needs_review`. The job fails if fewer than 3 of 4 narratives verify.
- **Ask BehaviorLens** follows the same rule: SQL computes, the model narrates, `factcheck.py`
  verifies against the result rows. The identical `factcheck.py` lives in `azure-function/` and
  `databricks/notebooks/`; a test keeps them byte-identical.

## Security and privacy layers

| Layer | Control |
|---|---|
| SDK | Masks email addresses in properties and URLs (keeps the domain); `optOut()`; anonymous IDs only |
| Demo app | Tracks the sign-in *method*, never the email typed into the form (asserted by an E2E test) |
| Ingestion | Server-side validation (required fields, event-name pattern, timestamp, size caps), email scrubbing, per-IP rate limit (300/min), CORS limited to the demo origins |
| Pipeline | Data-quality check fails the run if any unmasked email reaches `events_raw` |
| Ask | sqlglot guardrail: one SELECT, six allowlisted tables, blocked functions (`secret`, `read_files`, `ai_*`, `http_request`, ...), row cap, SQL regenerated from the validated tree; per-IP limit (10/min); runs as the `behaviorlens-ask` service principal with SELECT on those six tables only |
| Secrets | Gemini key and Databricks token live in Databricks secrets / Function app settings, never in code |

## Datasets

- `raw_events` (Volume): the mid-term and final-term scale-test data (12,001 events, SDK v1, contains
  login emails). Kept as-is; point the ingestion `raw_path` widget at it to reproduce those runs.
- `raw_events_v2` (Volume): the ReportHub dataset (SDK v2, PII-free, `appVersion`): simulated history
  from `qa/synthetic_event_generator.py` plus live traffic. Regenerate it before a demo with
  `run_pipeline.py --reset --since <date>` (Blob Storage stays the source of truth).

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
| `01_ingestion.py` | `events_raw`, `events_enriched` | Schema validation, dedup on natural key, sessionization via window functions, flattened `feature`/`onboarding_step`/`error_code`/`endpoint`, `appVersion` |
| `02_aggregations.py` | `session_summary`, `feature_adoption_daily`, `feature_usage_daily`, `error_summary_daily`, `error_detail_daily` | The proposal's three tables plus per-feature usage and per-cause/per-version errors |
| `03_data_quality.py` | `qa_data_quality_results` | 22 Great Expectations checks (via Pandas — see QA report for why) |
| `04_ai_insights.py` | `ai_insights_weekly`, `ai_insights_history` | 4 fact-checked Gemini scenarios + Teams alert — see [ai-prompt-log.md](ai-prompt-log.md) |

## Validated at scale

12,001 events (60 synthetic users, 3 flows, spread across ~8 months rather than a single burst — see `qa/synthetic_event_generator.py --days-back`) processed end-to-end — ingestion → aggregation → data quality — in ~2 minutes wall clock on serverless compute, including cold-start. See [qa-report.md](qa-report.md) for full results.

The pipeline is registered as a persistent Databricks Job ("BehaviorLens Pipeline") under Workflows → Jobs, not just ad-hoc API-submitted runs, so it's visible and re-runnable directly from the Databricks UI.

## Power BI dashboards

`BehaviorLens.pbip` — 4 dashboards against the Databricks semantic model (Import mode, Databricks SQL Warehouse). Full model structure, page-by-page visual inventory, and gotchas hit while building it are in [powerbi-guide.md](powerbi-guide.md).
