# BehaviorLens

Lightweight behavioral analytics platform: JS event SDK → Azure Functions ingestion → Blob Storage → Databricks pipeline (incl. fact-checked Gemini insights) → Power BI dashboards, plus a real-time live view and natural-language "Ask BehaviorLens".

See [S3-C-01_BehaviorLens_Proposal.md](S3-C-01_BehaviorLens_Proposal.md) for the full proposal.

**Live demo:** [ReportHub](https://sriram68.github.io/behaviourLens/demo-app/index.html) (the instrumented app) · [BehaviorLens Live](https://sriram68.github.io/behaviourLens/demo-app/live.html) (real-time view, QR code, Ask)

## Docs

- [docs/demo-script.md](docs/demo-script.md) — the 10-minute live demo, runbook and fallbacks
- [docs/pitch-notes.md](docs/pitch-notes.md) — talking points and likely questions
- [docs/architecture.md](docs/architecture.md) — full data flow and deliberate substitutions
- [docs/event-schema.md](docs/event-schema.md) — event schema reference, all 12 tracked events
- [docs/qa-report.md](docs/qa-report.md) — test results, coverage, data quality checks, scale tests, bugs found and fixed
- [docs/powerbi-guide.md](docs/powerbi-guide.md) — Power BI semantic model and report configuration
- [docs/ai-prompt-log.md](docs/ai-prompt-log.md) — Gemini prompt versions and the fact-check method

## Structure

- `sdk/` — framework-agnostic JS SDK, 2 KB gzipped: batching, retries with backoff, offline persistence, PII masking, opt-out, autocapture (page views, `data-bl-event` clicks, JS errors)
- `demo-app/` — **ReportHub** (`index.html`), a product-like app instrumented with the SDK, and **BehaviorLens Live** (`live.html`), the presenter screen
- `azure-function/` — Python Function App: `POST /api/events` (validated, rate-limited, PII-scrubbed ingestion to Blob), `GET /api/stats` (live view), `GET /api/health`, `POST /api/ask` (guarded text-to-SQL with fact-checked answers)
- `databricks/notebooks/` — ingestion, aggregations, data quality (22 checks), AI insights; `insights.py` (facts packs) and `factcheck.py` are imported by the AI notebook
- `databricks/*.py` — `deploy.py`, `run_pipeline.py` (sync + run + wait), incremental `sync_raw_events.py`, `setup_ask_access.py` (read-only identity for Ask)
- `qa/synthetic_event_generator.py` — 8 weeks of realistic ReportHub journeys with three planted problems
- `e2e/` — Playwright: the three user flows against the live Function, plus the live view against a mocked API
- `scripts/preflight.py` — demo-morning health check and warm-up
- `BehaviorLens.pbip` — Power BI project (5 pages incl. AI Insights); see `docs/powerbi-guide.md`

## Hosting

- **Function:** `https://func-behaviorlens47209.azurewebsites.net/api/` (personal subscription; the original `pSiddhi` subscription's resource group restricted `Microsoft.Web/*` writes, see [docs/architecture.md](docs/architecture.md)). It can also run locally with Azure Functions Core Tools (`func start`) against the same storage account.
- **Demo app and live view:** GitHub Pages, deployed by `.github/workflows/pages.yml` (only `demo-app/` and the SDK are published).

## Running locally

```bash
# Azure Function (ingestion + live stats + ask)
cd azure-function
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
func start

# Tests
cd sdk && npm install && npm test                    # SDK (Jest)
cd azure-function && pytest tests/                   # Function (pytest)
cd databricks && pytest tests/                       # insight layer (needs pandas)
cd e2e && npm install && npx playwright install chromium && npm test
# Point the E2E suite at a local Function instead of the deployed one:
BEHAVIORLENS_API=http://localhost:7071/api/events npm test

# Demo app and live view (served statically so fetch isn't blocked by file:// CORS)
python -m http.server 5500
# http://localhost:5500/demo-app/index.html
# http://localhost:5500/demo-app/live.html?api=http://localhost:7071/api
```

## Running the pipeline

Scripts read credentials from the environment (see `databricks/bl_client.py`): a Databricks token or a `databricks auth login` session, and the storage connection string (or `azure-function/local.settings.json`).

```bash
pip install -r databricks/requirements-scripts.txt
python databricks/deploy.py                                   # push notebooks/modules, create the Volume
python qa/synthetic_event_generator.py                        # simulated history (see --dry-run)
python databricks/run_pipeline.py --reset --since 2026/10/12  # sync + run the job + wait
python databricks/setup_ask_access.py                         # once: read-only identity + Function settings for Ask
python scripts/preflight.py                                   # before every demo
```
