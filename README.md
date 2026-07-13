# BehaviorLens

Lightweight behavioral analytics platform: JS event SDK → Azure Functions ingestion → Blob Storage → Databricks pipeline → (Power BI + AI insights, phase 2).

See [S3-C-01_BehaviorLens_Proposal.md](S3-C-01_BehaviorLens_Proposal.md) for the full proposal.

## Structure

- `sdk/` — framework-agnostic JS event tracking SDK (batching, session/user IDs, schema validation)
- `demo-app/` — minimal demo app firing 10+ events across 3 flows (login/onboarding, feature usage, error/recovery)
- `azure-function/` — Python HTTP-triggered Function that receives batched events and writes NDJSON to Blob Storage
- `databricks/notebooks/` — ingestion, enrichment, and aggregation notebooks
- `qa/` — Great Expectations data validation suite
- `docs/` — architecture, event schema reference, QA reports

## Note on Azure Function hosting

The target subscription's resource group restricts `Microsoft.Web/*` write permissions, blocking Function App creation in the portal. The ingestion Function runs locally via Azure Functions Core Tools (`func start`) against the real Azure Storage account/container — same code, deployable to Azure with `func azure functionapp publish` once elevated permissions are available.

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
```
