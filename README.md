# BehaviorLens

Lightweight behavioral analytics platform: JS event SDK → Azure Functions ingestion → Blob Storage → Databricks pipeline → (Power BI + AI insights, phase 2).

See [S3-C-01_BehaviorLens_Proposal.md](S3-C-01_BehaviorLens_Proposal.md) for the full proposal.

## Docs

- [docs/architecture.md](docs/architecture.md) — full data flow, and the two deliberate substitutions made for this checkpoint
- [docs/event-schema.md](docs/event-schema.md) — event schema reference, all 10 tracked events
- [docs/qa-report.md](docs/qa-report.md) — test results, coverage, data quality checks, scale test results, bugs found and fixed

## Structure

- `sdk/` — framework-agnostic JS event tracking SDK (batching, session/user IDs, schema validation)
- `demo-app/` — minimal demo app firing 10+ events across 3 flows (login/onboarding, feature usage, error/recovery)
- `azure-function/` — Python HTTP-triggered Function that receives batched events and writes NDJSON to Blob Storage
- `databricks/notebooks/` — ingestion, enrichment, and aggregation notebooks
- `qa/` — Great Expectations data validation suite
- `e2e/` — Playwright E2E tests covering all 3 flows against the live Function
- `docs/` — architecture, event schema reference, QA reports

## Note on Azure Function hosting

The ingestion Function is deployed to Azure: `https://func-behaviorlens47209.azurewebsites.net/api/events` (personal subscription — the original `pSiddhi` subscription's resource group restricted `Microsoft.Web/*` write permissions, so it never hosted the deployed Function; see [docs/architecture.md](docs/architecture.md)). It can still be run locally via Azure Functions Core Tools (`func start`) against the same storage account for offline development.

## Running locally

```bash
# Azure Function (ingestion endpoint)
cd azure-function
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
func start

# SDK tests
cd sdk
npm install
npm test

# Demo app (serve statically so fetch isn't blocked by file:// CORS)
python -m http.server 5500
# open http://localhost:5500/demo-app/index.html

# E2E tests (spins up the same static server itself)
cd e2e
npm install
npx playwright install --with-deps chromium
npm test
```
