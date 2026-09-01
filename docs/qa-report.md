# QA Report — Wednesday Checkpoint

## Summary

| Layer | Tool | Result |
|---|---|---|
| SDK unit tests | Jest | 12/12 passing, 85.4% statement coverage |
| Azure Function unit tests | Pytest | 6/6 passing, 86% coverage |
| CI | GitHub Actions | Green on every push — [run 29266759706](https://github.com/Sriram68/behaviourLens/actions/runs/29266759706) |
| Data quality | Great Expectations | 15/15 checks passing across 5 Delta tables |
| Scale test | Synthetic load generator | 5,992 events (811 pre-existing + 5,180 new), full pipeline in 123s wall clock |
| E2E | Playwright | 3/3 flow scenarios passing against the live Azure Function |

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

- 5,200 events attempted, 5,180 delivered (99.6%; 1 transient batch failure) in 305.6s wall clock at the SDK/HTTP layer
- Combined with 811 pre-existing events already in the Databricks Volume from the mid-term run, the full pipeline processed **5,992 events → 688 sessions, 32 feature-adoption day/event buckets, 24 error-summary day/event buckets**
- Full pipeline (ingestion → aggregation → data quality) completed in **123s wall clock** on Databricks serverless compute — ingestion 43.5s, aggregations 22.5s, data quality 55.4s
- All 15 Great Expectations checks still passed at this volume

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

## Known gaps (documented, not hidden)

- Load test target in the original proposal was 5,000+ events; met in Phase 2 (5,180 new + 811 pre-existing = 5,992 total processed end-to-end).
- Function App and Databricks-to-Blob mount substitutions are documented in [architecture.md](architecture.md) — both are real, working equivalents, not simulated stand-ins.
