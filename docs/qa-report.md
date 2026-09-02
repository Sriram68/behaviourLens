# QA Report — Wednesday Checkpoint

## Summary

| Layer | Tool | Result |
|---|---|---|
| SDK unit tests | Jest | 12/12 passing, 85.4% statement coverage |
| Azure Function unit tests | Pytest | 6/6 passing, 86% coverage |
| CI | GitHub Actions | Green on every push — [run 29266759706](https://github.com/Sriram68/behaviourLens/actions/runs/29266759706) |
| Data quality | Great Expectations | 15/15 checks passing across 5 Delta tables |
| Scale test | Synthetic load generator | 12,001 events spread across ~8 months, full pipeline in ~2min wall clock |
| E2E | Playwright | 3/3 flow scenarios passing against the live Azure Function |
| Power BI | 4 required views | Usage Trends, User Journeys, Feature Adoption, Error Patterns — see [powerbi-guide.md](powerbi-guide.md) |
| AI output quality | 3 Gemini scenarios | 100/100 average relevance score (target: >85%) — see [ai-prompt-log.md](ai-prompt-log.md) |

## SDK test suite (`sdk/tests/behaviorlens.test.js`)

12 tests covering: `init` validation, `track` guardrails (throws before init / without eventName), schema-compliant event construction, session/user ID persistence across calls, batching (auto-flush at `batchSize`, manual `flush()`, interval-based flush via fake timers), and network-failure re-queueing.

```
Tests:       12 passed, 12 total
Coverage:    85.36% stmts | 80% branch | 95.23% funcs | 90.78% lines
```

## Azure Function test suite (`azure-function/tests/test_function_app.py`)

6 tests covering: valid batch → 200 + correct blob write, bare-list payload handling, invalid JSON → 400, empty events → 400, missing `events` key → 400, and date-partitioned blob naming.

```
Tests:       6 passed, 6 total
Coverage:    86% (function_app.py)
```

## Data quality (Great Expectations)

`databricks/notebooks/03_data_quality.py` runs 15 expectations across `events_raw`, `events_enriched`, `session_summary`, `feature_adoption_daily`, and `error_summary_daily` — null checks, uniqueness (`sessionId` in `session_summary`), range checks (non-negative counts, non-negative session duration), and set-membership checks (`eventName` against the 10 known event types). Results are written to `qa_data_quality_results` for auditability. All 15 passed both in the initial small-scale run and the 811-event scale test.

## Scale test

`qa/synthetic_event_generator.py` generates realistic synthetic events (weighted across the 3 flows, 60 synthetic users, timestamps spread over 3 days) and sends them through the real SDK-equivalent path (batched POSTs to the ingestion endpoint) — not inserted directly into Blob Storage, so the full path is exercised. For Phase 2 the target was raised to 5,000+ events and pointed at the now-deployed Azure Function (`func-behaviorlens47209`, see [architecture.md](architecture.md)).

- Phase 2 initial run: 5,200 events attempted, 5,180 delivered (99.6%; 1 transient batch failure) in 305.6s wall clock; combined with 811 pre-existing events, the pipeline processed 5,992 events.
- A second run added `--days-back 240` (a parametrization added specifically for this) to spread 6,000 more events across ~8 months instead of a single 3-day burst, needed to give the Power BI trend visuals real day-to-day variation instead of a flat line.
- Final state: **12,001 events → 1,343 sessions, 930 feature-adoption day/event buckets, 593 error-summary day/event buckets**, spanning ~8 distinct clusters of days from January through September 2026.
- Full pipeline (ingestion → aggregation → data quality) completes in **~2 minutes wall clock** on Databricks serverless compute at this volume.
- All 15 Great Expectations checks still pass at this volume.
- The pipeline is now also registered as a persistent Databricks Job ("BehaviorLens Pipeline", 3 tasks) under Workflows → Jobs, rather than only existing as ad-hoc API-submitted runs — re-runnable and inspectable from the Databricks UI.

## E2E test suite (`e2e/tests/`)

3 Playwright scenarios, one per proposal flow, run against the demo app served locally and the **live deployed Azure Function** (not a mock) — covering the full browser → SDK → ingestion path:

- `login-onboarding.spec.js` — login + onboarding steps, asserts DOM event log and a real `200` from `/api/events`
- `feature-usage.spec.js` — feature click, search, filter, export
- `error-recovery.spec.js` — API error, 404, retry

```
3 passed (11.3s)
```

Wired into CI as `e2e-tests` in `.github/workflows/ci.yml`, marked `continue-on-error: true` since it depends on live network access to Azure rather than being fully local/mocked like `sdk-tests` and `function-tests` — will promote to merge-blocking once proven stable over more runs.

## Bugs found and fixed during this QA pass

Real defects surfaced and were fixed before each scale test passed — left in this report because catching them is the point of embedding QA into every stage rather than validating happy-path-only:

1. **Great Expectations `SparkDFDataset` incompatible with serverless compute.** It issues an internal `CACHE TABLE`/`PERSIST TABLE` statement to speed up repeated expectation checks, which Databricks serverless rejects (`NOT_SUPPORTED_WITH_SERVERLESS`). Fixed by validating via `PandasDataset` instead — the tables in this POC are small enough for driver-side validation without losing coverage.
2. **Delta schema mismatch on overwrite.** The `properties` column is a struct inferred from whatever event types are present in a given ingestion run; a narrower first run (7 events) created a table schema that a later, more diverse run (811 events, more event types) didn't match, and Delta's `overwrite` mode rejects schema changes by default. Fixed by adding `.option("overwriteSchema", "true")` to all five table writes.
3. **`01_ingestion.py` silently dropped events under nested date folders.** `spark.read.json(raw_path)` does not recurse into subdirectories by default. The Azure Function names blobs `YYYY/MM/DD/uuid.ndjson` (date-partitioned), and once that structure was preserved when syncing into the Unity Catalog Volume for the Phase 2 scale test, ingestion silently read 0 new rows from any dated subfolder — the run "succeeded" but produced numbers identical to a stale prior run, with no error raised. Fixed by adding `.option("recursiveFileLookup", "true")` to the read. This is the more dangerous class of bug precisely because it fails silently rather than loudly — caught only because the post-run row counts were sanity-checked against the expected event volume instead of just checking the job succeeded.
4. **No CORS configured on the deployed Function App.** The SDK's `fetch()` call sends `Content-Type: application/json`, which isn't CORS-safelisted and triggers a browser preflight `OPTIONS` request. The freshly deployed Function had no CORS policy, so any real browser would have silently failed to deliver events — this had never surfaced before because local dev via `func start` enables permissive CORS by default, masking the gap. Caught before writing the E2E suite by checking `az functionapp cors show` returned empty; fixed with `az functionapp cors add --allowed-origins http://localhost:5500`, verified via a manual preflight request before trusting Playwright's results.
5. **Unpinned `azure-functions` dependency broke unit-testability.** `requirements.txt` listed `azure-functions` with no version. A fresh `pip install` for the Phase 6 regression pass pulled 1.14.0, where `@app.route(...)` returns a `FunctionBuilder` instead of the decorated function — `function_app.ingest_events` silently became non-callable in tests (`AttributeError: 'NoneType' object has no attribute 'status_code'` on all 6 tests), with no change to the actual deployed behavior. Fixed by registering the route via `app.route(...)(ingest_events)` after the function definition instead of as a decorator, keeping `ingest_events` a plain callable for tests, and pinning `azure-functions==1.14.0`.

## Power BI dashboards

4/4 required analytics views built against the Databricks semantic model (Import mode): Usage Trends, User Journeys, Feature Adoption, Error Patterns. Full configuration, model structure, and gotchas hit while building it (a silent relationship-persistence bug, a continuous-axis rendering trap, a `growToFit` misunderstanding) are documented in [powerbi-guide.md](powerbi-guide.md).

## AI insights (`databricks/notebooks/04_ai_insights.py`)

3 scenarios per the proposal's AI layer, run as the 4th task in the "BehaviorLens Pipeline" Databricks Job, against `gemini-3.6-flash` (see [ai-prompt-log.md](ai-prompt-log.md) for the model-availability deviation from the proposal's Gemini 2.5 Flash):

- **Weekly narrative** — plain-language feature-adoption summary with a concrete recommendation.
- **Anomaly detection** — z-score outlier flagging on daily error counts, narrated in plain language. Correctly caught the real Aug 29–Sep 1 spike from the scale-test data (338 errors/day vs. a ~10/day baseline) as a genuine statistical outlier.
- **Cohort segmentation** — high/low engagement session split (median event count) with a suggested action.

Each narrative is scored for relevance against its own source data by a second Gemini call. Result: **100/100 average relevance** across all 3 scenarios, against the proposal's >85% target. Results persist to `ai_insights_weekly` (Delta) for auditability and are surfaced on a 5th Power BI page ("AI Insights").

Databricks secret `behaviorlens/gemini_api_key` holds the API key — never in notebook source or git.

## Known gaps (documented, not hidden)

- Load test target in the original proposal was 5,000+ events; exceeded (12,001 total processed end-to-end, spread across ~8 months rather than one burst).
- Function App and Databricks-to-Blob mount substitutions are documented in [architecture.md](architecture.md) — both are real, working equivalents, not simulated stand-ins.
- Kong Gateway OSS (proposal's optional API rate-limiting layer) is intentionally dropped — not in the proposal's minimum compliance requirements, and not worth the added infra this late in the timeline.
- Ollama/local dev-LLM (proposal's zero-cost dev-phase fallback) skipped — Gemini's free tier (1,500 req/day) comfortably covers the ~6 calls/run this project makes, so the extra local setup wasn't worth the time.
