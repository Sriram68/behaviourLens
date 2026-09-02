# Power BI Report Configuration Guide

`BehaviorLens.pbip` (repo root) — a Power BI Project, opened directly in Power BI Desktop with the [PBIR preview feature](https://learn.microsoft.com/power-bi/create-reports/projects-report) enabled (`File → Options and settings → Options → Preview features → Store reports using enhanced metadata format (PBIR)`).

## Data source

Import mode against the Databricks SQL Warehouse (`Serverless Starter Warehouse`, `/sql/1.0/warehouses/b785449970d46af7`) on `dbc-6051f46c-210c.cloud.databricks.com`, authenticated via a personal access token entered directly in Desktop's connector dialog (never stored in the project). Reload the model after a Databricks pipeline run via **Home → Refresh**.

## Semantic model (`BehaviorLens.SemanticModel/`)

Source tables (Import mode, one partition each from the corresponding Delta table):

| Table | Grain | Key columns |
|---|---|---|
| `session_summary` | 1 row per session | `sessionId`, `userId`, `session_start_ts`, `session_duration_seconds`, `event_count`, `distinct_event_types` |
| `events_enriched` | 1 row per event | `eventName`, `sessionId`, `event_timestamp`, `flow` *(calc.)*, `flow_step` *(calc.)* |
| `feature_adoption_daily` | 1 row per day × feature event | `event_date`, `eventName`, `event_count`, `distinct_users` |
| `error_summary_daily` | 1 row per day × error event | `event_date`, `eventName`, `event_count`, `distinct_sessions` |

Plus a calculated `DimDate` table (full-year calendar, `Date`/`Year`/`Month`/`DayOfWeek`, `Month` and `DayOfWeek` both `sortByColumn`-ordered chronologically rather than alphabetically) — the single shared date dimension all 4 fact tables relate to (`Many` → `DimDate[Date]` `One`, all one-directional).

Calculated columns on `events_enriched`:
- `flow` — buckets each of the 10 event types into one of the 3 proposal flows (`Login & Onboarding` / `Feature Usage` / `Error & Recovery`) via `SWITCH(TRUE(), ...)`.
- `flow_step` — the event's ordinal position within its flow, used as the funnel category (numeric, so it sorts correctly without needing a separate sort column).
- `event_date` — date-truncated `event_timestamp`, for the `DimDate` relationship.

Key measures (session_summary/events_enriched/feature_adoption_daily/error_summary_daily): `Total Sessions`, `Active Users`, `Avg Session Duration (min)`, `Avg Events per Session`, `Total Events`, `Sessions per Step`, `Feature Events`, `Feature Adoption Users`, `Total Errors`, `Affected Sessions`, `Error Rank`.

**Relationship note:** `events_enriched` → `session_summary` (on `sessionId`) is deliberately **inactive** — an active version creates an ambiguous relationship path to `DimDate` (via `session_summary` *and* directly via `events_enriched.event_date`). Activate it with `USERELATIONSHIP` in a specific measure if session-level attributes are ever needed alongside event-level date filtering.

## Report pages (`BehaviorLens.Report/`)

All 4 pages share a top row (KPI card + a synced `Date Range` between-slicer, `DateSync` sync group) and the `BehaviorLensExecutive` custom theme.

1. **Usage Trends** — Active Users and Avg Session Duration trend lines (categorical date axis — see gotcha below), Sessions by Day of Week, a sortable table of longest sessions, and Sessions by Engagement Depth.
2. **User Journeys** — one funnel per flow (`Category` = `flow_step`, pre-filtered to that flow), plus Event Volume by Flow and Type and a Session Share by Flow donut.
3. **Feature Adoption** — a compact eventName × Month heatmap (`pivotTable` with a `linearGradient2` conditional-format fill), Feature Events and Feature Adoption Users by month, and 3 more breakdowns (by eventName, by date, by day of week).
4. **Error Patterns** — ranked bar chart of errors by type, an error-share donut, and 3 more breakdowns (trend, affected sessions, day of week).

### Gotchas hit while building this (documented so they don't recur)

- **`spark.read.json` doesn't recurse into subdirectories by default** — caused a silent 0-row ingestion bug when the Files-API sync preserved the Function's `YYYY/MM/DD/` blob structure. Fixed in `01_ingestion.py` with `recursiveFileLookup`. See `qa-report.md`.
- **Model edits made live over the XMLA connection are not persisted until Desktop does an explicit Save.** Closing without saving (needed at one point to avoid Desktop clobbering file-based report edits with its stale in-memory state) also silently discarded relationship work. Always **Save** in Desktop after a live model edit, before closing.
- **Continuous date axes span the full model date range, not just the dates with data.** With only ~8 clustered days of real data inside a full-year `DimDate`, the default continuous axis rendered as a visually flat line. Fixed by setting `categoryAxis.axisType = 'Categorical'` on trend line charts.
- **`tableEx`/`pivotTable` `columnAdjustment: 'growToFit'` redistributes width among existing columns — it does not stretch a narrow table to fill an oversized container.** Right-size the visual to its content instead of relying on `growToFit` to fill unused space.

## Databricks pipeline (for reference)

The pipeline behind this model is registered as a real Databricks Job (**Workflows → Jobs → "BehaviorLens Pipeline"**, 3 tasks: `ingestion` → `aggregations` → `data_quality`), not just ad-hoc API runs — triggerable from the UI (**Run now**) after any new sync of raw events into the `raw_events` Volume (`databricks/sync_raw_events.py`).

## Known caveat

`Avg Session Duration` reads unrealistically high in places (thousands of minutes) — an artifact of `qa/synthetic_event_generator.py` picking each event's timestamp uniformly at random within the lookback window rather than sequentially within a session, not a modeling bug. Directionally still useful for demonstrating the trend visuals; not a real duration if taken literally.
