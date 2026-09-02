# Final Demo Script

A walkthrough script for the Week 17 recording, mapped directly to the proposal's
"Live Demonstrations" checklist (`S3-C-01_BehaviorLens_Proposal.md`, Deliverables
section) so every promised item is visibly covered. Target: 8–10 minutes.

## 0. Setup (before recording, not on camera)

- Confirm the demo app is servable: `python -m http.server 5500` from the repo root.
- Confirm the deployed Function is reachable: a POST to
  `https://func-behaviorlens47209.azurewebsites.net/api/events` returns 200.
- Have three tabs ready: Azure Portal (Blob container), Databricks workspace
  (Workflows → Jobs and Catalog), Power BI Desktop with `BehaviorLens.pbip` open.
- Optionally run `qa/synthetic_event_generator.py` once beforehand so Power BI has
  fresh data to refresh into during the recording, rather than refreshing stale data.

## 1. Real-time event capture (~90s)

Proposal item: *"Real-time event capture: 10+ events across 3 flows visible in
Azure Blob Storage."*

1. Open the demo app (`http://localhost:5500/demo-app/index.html`).
2. Narrate the 3 flows on screen: Login & Onboarding, Core Feature Usage, Error &
   Recovery — click through each, pointing at the live event-log panel as events fire.
3. Switch to Azure Portal → Storage account `stbehaviorlens47209` → `raw-events`
   container. Show a freshly-timestamped blob under today's `YYYY/MM/DD/` path,
   open it, show the NDJSON events matching what was just clicked.

## 2. Databricks pipeline (~90s)

Proposal item: *"Databricks pipeline: raw → enriched → aggregated tables, all
queryable."*

1. Databricks workspace → Workflows → Jobs → **BehaviorLens Pipeline**. Point out
   the 4 tasks (`ingestion` → `aggregations` → `data_quality` → `ai_insights`) and
   click **Run now** live if time allows, or show the most recent successful run.
2. Catalog → `workspace.default` → run a quick `SELECT COUNT(*)` against
   `events_raw`, `events_enriched`, `session_summary`, `feature_adoption_daily`,
   `error_summary_daily`, `qa_data_quality_results`, `ai_insights_weekly` to show
   real row counts, not empty tables.

## 3. Interactive Power BI dashboard (~2 min)

Proposal item: *"Interactive Power BI dashboard: all 4 analytics views live and
responsive."*

1. **Usage Trends** — hover the trend lines, use the Date Range slicer, point out
   Active Users / Avg Session Duration responding live.
2. **User Journeys** — click through a funnel stage, show cross-filtering into the
   Session Share by Flow donut.
3. **Feature Adoption** — hover the eventName × Month heatmap, point out the
   conditional-format gradient.
4. **Error Patterns** — click a bar in the ranked error chart, show it filtering the
   error-share donut and trend visuals.
5. Click **Home → Refresh** once to show a live reload against Databricks (matches
   the "responsive" claim rather than a static screenshot).

## 4. AI insight narratives (~90s)

Proposal item: *"AI insight narratives: 3 distinct usage scenarios from Gemini."*

1. Switch to the **AI Insights** page. Read the top KPI row (Avg/Min Relevance
   Score, Scenarios Generated, Last Generated) aloud.
2. Scroll the narrative table — point out the 3 distinct scenarios (weekly
   narrative, anomaly detection, cohort segmentation) and that each is real
   generated text, not a placeholder.

## 5. Anomaly detection demo (~60s)

Proposal item: *"Anomaly detection demo: simulated error spike with AI alert
narrative."*

1. On the AI Insights page, find the `anomaly_detection` row's narrative — it
   references the real Aug 29–Sep 1 error spike in the scale-test data (338
   errors/day vs. a ~10/day baseline), flagged as a genuine statistical outlier
   via z-score.
2. Optionally cross-reference: flip to **Error Patterns**, filter the date slicer
   to Aug 29–Sep 1, show the visible spike the narrative is describing.

## 6. Scale validation (~45s)

Proposal item: *"Scale validation: 5,000+ synthetic events processed within SLA."*

1. State the number on camera: 12,001 events (exceeds the 5,000+ target), full
   pipeline (ingestion → aggregation → data quality → AI insights) in ~2 minutes
   wall clock on serverless compute.
2. Show `docs/qa-report.md`'s scale-test section as supporting evidence if a
   document view is convenient.

## 7. Cohort segmentation (~45s)

Proposal item: *"Cohort segmentation: high-engagement vs low-engagement user
identification."*

1. Back on the AI Insights page, read the `cohort_segmentation` narrative row —
   states the median-event-count split (high vs. low engagement) and the
   suggested action.

## 8. Close (~30s)

- One sentence on QA rigor: full regression suite (SDK, Function, E2E, Great
  Expectations) green, plus real bugs found and fixed during testing (name 1–2
  from `docs/qa-report.md`'s bug list to show QA wasn't just happy-path).
- One sentence on budget: entire build stayed at ₹0 actual spend against a ₹2,500
  ceiling, everything on free tiers even at scale.

## Reference docs to have open/linked in the recording description

- `docs/architecture.md` — full data flow
- `docs/qa-report.md` — test results and bugs found/fixed
- `docs/powerbi-guide.md` — report/model configuration
- `docs/ai-prompt-log.md` — Gemini prompts and relevance scores
