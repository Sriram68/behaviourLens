# Power BI Report Configuration Guide

`BehaviorLens.pbip` (repo root) — a Power BI Project, opened directly in Power BI Desktop with the [PBIR preview feature](https://learn.microsoft.com/power-bi/create-reports/projects-report) enabled (`File → Options and settings → Options → Preview features → Store reports using enhanced metadata format (PBIR)`).

## Data source

Import mode against the Databricks SQL Warehouse (`Serverless Starter Warehouse`, `/sql/1.0/warehouses/b785449970d46af7`) on `dbc-6051f46c-210c.cloud.databricks.com`, authenticated via a personal access token entered directly in Desktop's connector dialog (never stored in the project). Reload the model after a Databricks pipeline run via **Home → Refresh**.

## Semantic model (`BehaviorLens.SemanticModel/`)

Source tables (Import mode, one partition each from the corresponding Delta table):

| Table | Grain | Key columns |
|---|---|---|
| `session_summary` | 1 row per session | `sessionId`, `userId`, `session_start_ts`, `session_duration_seconds`, `event_count`, `distinct_event_types` |
| `events_enriched` | 1 row per event | `eventName`, `userId`, `sessionId`, `event_timestamp`, `appVersion`, `feature`, `onboarding_step`, `error_code`, `endpoint`, `flow` *(calc.)*, `flow_step` *(calc.)* |
| `feature_adoption_daily` | 1 row per day × feature event | `event_date`, `eventName`, `event_count`, `distinct_users` |
| `error_summary_daily` | 1 row per day × error event | `event_date`, `eventName`, `event_count`, `distinct_sessions` |
| `ai_insights_weekly` | 1 row per Gemini scenario per run | `scenario`, `prompt_version`, `model`, `narrative`, `status` (`verified` / `needs_review`), `facts_verified`, `facts_total`, `unverified_facts`, `attempts`, `relevance_score`, `alert_status`, `generated_at` |

Plus a calculated `DimDate` table (full-year calendar, `Date`/`Year`/`Month`/`DayOfWeek`, `Month` and `DayOfWeek` both `sortByColumn`-ordered chronologically rather than alphabetically) — the single shared date dimension all 4 fact tables relate to (`Many` → `DimDate[Date]` `One`, all one-directional).

Calculated columns on `events_enriched`:
- `flow` — buckets each event type into one of the 3 proposal flows (`Login & Onboarding` / `Feature Usage` / `Error & Recovery`, which includes autocaptured `js_error`), with `page_view` as `Navigation`, via `SWITCH(TRUE(), ...)`.
- `flow_step` — the event's ordinal position within its flow, used as the funnel category (numeric, so it sorts correctly without needing a separate sort column).
- `event_date` — date-truncated `event_timestamp`, for the `DimDate` relationship.

Key measures (session_summary/events_enriched/feature_adoption_daily/error_summary_daily): `Total Sessions`, `Active Users`, `Avg Session Duration (min)`, `Avg Events per Session`, `Total Events`, `Users` (distinct users, used by the onboarding funnel), `Sessions per Step`, `Feature Events`, `Feature Adoption Users`, `Total Errors`, `Affected Sessions`, `Error Rank`.

Export reliability (`events_enriched`, display folder *Exports*): `Export Calls` (export_triggered plus retries of failed exports), `Failed Exports` (api_error on `/api/export`) and `Export Failure Rate`. The rate matters: raw error counts per app version are misleading because versions are live for different lengths of time (2.4.0 for days, 2.3.0 for weeks), so their bars look similar even though 2.4.0 fails ~20x as often.

AI quality (`ai_insights_weekly`): `Scenarios Generated`, `Narratives Verified`, `Facts Verified %`, `Avg Relevance Score`, `Last Generated`: explicit measures, so card labels read properly instead of "Count of scenario".

**Relationship note:** `events_enriched` → `session_summary` (on `sessionId`) is deliberately **inactive** — an active version creates an ambiguous relationship path to `DimDate` (via `session_summary` *and* directly via `events_enriched.event_date`). Activate it with `USERELATIONSHIP` in a specific measure if session-level attributes are ever needed alongside event-level date filtering.

## Report pages (`BehaviorLens.Report/`)

The 4 analytics pages share a top row (KPI card + a synced `Date Range` between-slicer, `DateSync` sync group) and the `BehaviorLensExecutive` custom theme; the AI Insights page carries its own KPI row against `ai_insights_weekly` instead (no date slicer — narratives are generated per pipeline run, not date-filterable).

1. **Usage Trends** — Active Users and Avg Session Duration trend lines (categorical date axis — see gotcha below), Sessions by Day of Week, a sortable table of longest sessions, and Sessions by Engagement Depth.
2. **User Journeys** — an **onboarding funnel** (distinct users per `onboarding_step`, filtered to `onboarding_step_complete`, which shows the step-3 drop-off), funnels for the Feature Usage and Error & Recovery flows (`Category` = `flow_step`), plus Event Volume by Flow and Type and a Session Share by Flow donut.
3. **Feature Adoption** — a compact eventName × Month heatmap (`pivotTable` with a `linearGradient2` conditional-format fill), Feature Events and Feature Adoption Users by month, and 3 more breakdowns (by eventName, by date, and **clicks by product feature**, which shows Scheduled Reports is barely used).
4. **Error Patterns** — ranked bar chart of errors by type, an error-share donut, and 3 more breakdowns (trend over time, affected sessions, and **export failure rate by app version**, which ties the incident to release 2.4.0: ~1.8% before, ~40% on 2.4.0).
5. **AI Insights** — a table of `ai_insights_weekly` narratives (scenario, status, facts verified / total, narrative, relevance score; verification columns first so they're visible without scrolling) plus a KPI row: Scenarios Generated, Narratives Verified, Facts Verified %.

### Gotchas hit while building this (documented so they don't recur)

- **`spark.read.json` doesn't recurse into subdirectories by default** — caused a silent 0-row ingestion bug when the Files-API sync preserved the Function's `YYYY/MM/DD/` blob structure. Fixed in `01_ingestion.py` with `recursiveFileLookup`. See `qa-report.md`.
- **Model edits made live over the XMLA connection are not persisted until Desktop does an explicit Save.** Closing without saving (needed at one point to avoid Desktop clobbering file-based report edits with its stale in-memory state) also silently discarded relationship work. Always **Save** in Desktop after a live model edit, before closing.
- **Continuous vs categorical date axes depend on the data's shape.** With only ~8 clustered days of data inside a full-year `DimDate`, a continuous axis rendered as a flat line, so the trend charts were set to `Categorical`. With the 8 contiguous weeks of ReportHub history, the categorical axis became a scrollbar that opened on mid-August and hid the October incident; the four date charts are now `Scalar` (continuous) again, showing the whole range with the spike at the right edge.
- **Open the PBIP only after the SQL warehouse is running.** Desktop reconciles new model columns against Databricks while opening; with the warehouse auto-stopped it sat on "Working on it" indefinitely. `scripts/preflight.py` starts the warehouse.
- **A refresh triggered outside Desktop's UI (XMLA) doesn't refresh its visuals.** Desktop kept showing cached results and "needs refresh" banners until its own **Home → Refresh** ran.
- **`tableEx`/`pivotTable` `columnAdjustment: 'growToFit'` redistributes width among existing columns — it does not stretch a narrow table to fill an oversized container.** Right-size the visual to its content instead of relying on `growToFit` to fill unused space.

## Databricks pipeline (for reference)

The pipeline behind this model is registered as a real Databricks Job (**Workflows → Jobs → "BehaviorLens Pipeline"**, 4 tasks: `ingestion` → `aggregations` → `data_quality` → `ai_insights`), not just ad-hoc API runs — triggerable from the UI (**Run now**) or with `python databricks/run_pipeline.py`, which first syncs new raw events into the `raw_events_v2` Volume.

## Known caveat (resolved)

Earlier builds showed `Avg Session Duration` in the thousands of minutes: the old load generator
picked each event's timestamp at random within the lookback window. The rewritten generator
simulates ordered journeys (sessions of a few minutes), and a data-quality check now fails the
pipeline if any session exceeds 24 hours.
