# IMPACT pSIDDHI - Application Data Analytics Integration
**S3-C-01 | Semester 3 Custom Track | QA Mandatory | AI Core**

**Budget:** ₹2,500 (Fixed Ceiling) | **Timeline:** Weeks 4-17 | **Participant:** Sriram Ganesan (ID: 414)

---

## Table of Contents
1. [Problem Statement](#problem-statement)
2. [Proposed Solution: BehaviorLens](#proposed-solution-behaviorlens)
3. [Architecture](#architecture)
4. [Tech Stack & Cost Breakdown](#tech-stack--cost-breakdown)
5. [Development Timeline](#development-timeline)
6. [QA Strategy](#qa-strategy)
7. [Deliverables](#deliverables)
8. [Risk Mitigation](#risk-mitigation)

---

## Problem Statement

Psiog builds and operates a range of internal and client-facing applications serving engineering teams at scale. However:

### Current Gaps
- **Invisible Users**: No visibility into how users navigate applications, which features get used, or where drops occur
- **Fragmented Silos**: Each application handles tracking independently with different schemas and tools
- **Reactive Decisions**: Feature build/retire decisions are opinion-driven, not data-driven

### Business Impact
- UX failures are invisible until users report them
- Engineering teams duplicate instrumentation efforts
- No way to prove feature adoption or engineering value

---

## Proposed Solution: BehaviorLens

**A lightweight, end-to-end behavioral analytics platform** that captures user interactions, processes them through a data pipeline, visualizes patterns in interactive dashboards, and generates AI-powered insights.

### Core Value Proposition
- Event instrumentation in target Psiog applications
- End-to-end processing via Databricks
- Interactive Power BI dashboards (4 views)
- Gemini-powered AI insight narratives
- **All within ₹2,500, all on free tiers**

---

## Architecture

### Layer 1: Instrumentation (Event Tracking SDK)

**JavaScript SDK embedded in target Psiog application**

- Framework-agnostic (React/Vue/plain JS compatible)
- Standardized event schema: `{ eventName, userId, sessionId, timestamp, pageUrl, properties }`
- Reusable across any Psiog application

#### Events Tracked (10+ distinct events across 3 flows)

**Login & Onboarding Flow:**
- `login_attempt`
- `login_success`
- `onboarding_step_complete`

**Core Feature Usage Flow:**
- `feature_click`
- `search_query`
- `filter_applied`
- `export_triggered`

**Error & Recovery Flow:**
- `api_error`
- `page_not_found`
- `retry_attempt`

#### Implementation Details
- Batched event sending to minimize network overhead
- Sends to Azure Functions endpoint
- <2 KB gzipped; negligible performance impact

---

### Layer 2: Data Processing (Databricks Pipeline)

**Data flow:** Raw events → Ingestion → Enrichment → Aggregation

#### Components
1. **Azure Functions (free tier)**
   - Receives raw events from JS SDK
   - Writes to Azure Blob Storage as NDJSON files

2. **Databricks Free Edition**
   - Ingestion notebook: picks up files from Blob Storage
   - Schema validation, deduplication, sessionization
   - Aggregation notebooks: compute daily metrics

#### Output Tables
- `events_raw` - Original event records
- `events_enriched` - Enriched with session context
- `session_summary` - User session aggregates
- `feature_adoption_daily` - Feature usage metrics
- `error_summary_daily` - Error patterns and frequencies

---

### Layer 3: Visualization & AI Insights

#### Power BI Dashboards (4 Views)
1. **Usage Trends** - Daily/weekly active users, session duration trends
2. **User Journeys** - Funnel analysis across key flows
3. **Feature Adoption** - Heatmap of feature usage intensity
4. **Error Patterns** - Ranked list of most common errors

#### AI Insight Generation (Gemini 2.5 Flash)
- Weekly analysis of aggregated event data
- Plain-language insight narratives
- Behavioral pattern summaries
- Anomaly commentary on usage spikes/drops
- Cohort segmentation recommendations
- Surfaced in dedicated "AI Insights" Power BI page

#### Development AI (Ollama + Llama 4 Scout)
- Local inference for offline testing
- Zero API cost during build phase
- Validates insight quality before publishing

---

## Tech Stack & Cost Breakdown

| Tool/Service | Purpose | Tier | Cost/Sem | Justification |
|---|---|---|---|---|
| **Azure Functions** | Event collection API endpoint | Free | ₹0 | 1M executions/month free; sufficient for event volume |
| **Azure Blob Storage** | Raw event file storage (NDJSON) | Free 5GB | ₹0 | Event files small; well within limit |
| **Databricks** | Ingestion, enrichment, aggregation pipeline | Free Edition | ₹0 | Community edition; scheduled notebooks, no cluster cost |
| **Power BI Desktop** | Interactive dashboards (4 views) | Free | ₹0 | Desktop version free; publish to free workspace |
| **Gemini 2.5 Flash** | AI insight narratives & anomaly detection | Free tier | ₹0 | Free tier: 1,500 req/day; weekly reports well within quota |
| **Ollama + Llama 4 Scout (8B)** | Local LLM for dev & offline AI testing | Free/Local | ₹0 | Runs on dev machine; zero API cost during build |
| **Kong Gateway OSS** | API rate limiting & ingestion security | Free OSS | ₹0 | Open-source; runs locally on Docker during dev |
| **GitHub (free tier)** | Version control + CI/CD (Actions) | Free | ₹0 | 2,000 Actions minutes/month free; sufficient for tests |
| **Pytest + Playwright** | QA test suite (unit, integration, E2E) | Free | ₹0 | Open-source; full test coverage, zero licensing cost |
| **Great Expectations (OSS)** | Data validation for Databricks pipeline | Free | ₹0 | Open-source; native Databricks support |
| **Gemini API (overage buffer)** | Reserve for insight generation if quota exceeded | Pay-per-use | ₹300 | ~14 weekly reports at scale; rarely needed |
| **Domain + SSL (optional)** | Custom URL for Power BI embed | Paid | ₹400 | Optional; Power BI default embed URL works for POC |
| **Contingency Buffer** | Unexpected overages or additional compute | - | ₹850 | Reserved; not expected to be needed |

### Budget Summary
- **Total Used:** ₹1,550
- **Budget Ceiling:** ₹2,500
- **Buffer:** ₹950 (38% reserve)
- **Efficiency:** 11 of 13 tools completely free

---

## Development Timeline

### Phase 1: Weeks 4-9 (Instrumentation + Databricks + Initial QA)

| Week | Task | Deliverable | QA Activity |
|---|---|---|---|
| **Wk 4** | Set up Azure Functions + Blob Storage. Design event schema. Build JS SDK skeleton. | Azure infra ready + event schema doc | Schema contract tests |
| **Wk 5** | Embed JS SDK in target Psiog app. Instrument Login & Onboarding flow (4 events). | 2 flows instrumented, events firing live | Unit tests for SDK batching |
| **Wk 6** | Instrument Core Feature & Error flows (6+ more events). Validate end-to-end event capture. | All 10+ events captured & in Blob Storage | Integration test: SDK → Functions → Blob |
| **Wk 7** | Build Databricks ingestion notebook. Raw events → enriched table. Add sessionization. | Databricks pipeline ingesting & enriching | Data quality checks: nulls, duplicates, schema drift |
| **Wk 8** | Aggregation notebooks: feature_adoption_daily, session_summary, error_summary_daily. Schedule jobs. | Full pipeline running on schedule; Power BI data ready | E2E pipeline test with 1,000 synthetic events |
| **Wk 9** | Bug fixes. Mid-term docs. Prepare Week 10 demo evidence. Upload to Moodle. | Mid-term review ready | All Phase 1 tests passing in GitHub Actions CI |

### Phase 2: Weeks 11-16 (Power BI + AI Insights + Full QA)

| Week | Task | Deliverable | QA Activity |
|---|---|---|---|
| **Wk 11** | Apply mid-term feedback. Build Power BI Usage Trends + User Journey (funnel) pages. | Dashboard v1: 2 pages live | Dashboard rendering & data refresh tests |
| **Wk 12** | Build Feature Adoption (heatmap) + Error Patterns dashboard pages. | All 4 Power BI dashboard pages live | Data accuracy validation vs Databricks tables |
| **Wk 13** | Integrate Gemini 2.5 Flash API. Build weekly insight narrative generator. | AI narratives generating for 3 scenarios | AI output relevance checks + prompt regression tests |
| **Wk 14** | Add anomaly detection logic (Gemini commentary on spikes/drops). Embed AI Insights page in dashboard. | Anomaly detection live; AI Insights page integrated | Anomaly trigger threshold tests + edge cases |
| **Wk 15** | Scale test with 5,000+ synthetic events. Optimize Databricks jobs and Power BI refresh latency. | System validated at scale | Load tests (Databricks throughput + Power BI refresh SLA) |
| **Wk 16** | Final documentation, demo video, presentation prep. Full regression run. Upload to Moodle. | Final review ready | Full QA regression suite + QA report published |

**Total Effort:** ~140 hours across 12 active weeks (~12 hrs/week)
**QA Integration:** Embedded in every development week, not bolted on at the end

---

## QA Strategy (Mandatory for Semester 3)

### Test Approach
Testing is embedded across every development week. Multi-layer strategy covers three testable boundaries:
1. JavaScript event tracking SDK
2. Databricks data processing pipeline
3. Gemini AI insight generation chain

All tests run automatically on every code push via GitHub Actions CI.

### Test Types & Coverage

| Test Type | What It Covers | Tool | Layer | Target Coverage |
|---|---|---|---|---|
| **Unit Tests** | SDK schema validation, batching logic, Azure Functions handler, Databricks transformation functions | Pytest + Jest | SDK + Pipeline | >80% code coverage |
| **Integration Tests** | SDK → Azure Functions → Blob Storage, Databricks notebook chaining, Power BI data refresh | Pytest + fixtures | All layers | All 3 layers tested |
| **E2E Tests** | Full path: user action → event fired → Databricks processed → Power BI updated → AI narrative generated | Playwright + Pytest | Full stack | 3 E2E user flow scenarios |
| **Data Validation** | Schema compliance, nulls, duplicate detection, type validation on all Databricks output tables | Great Expectations OSS | Pipeline output | Zero invalid records at merge |
| **AI Output Quality** | Gemini insight relevance, anomaly detection accuracy, cohort segmentation correctness on test datasets | Custom Pytest fixtures | AI layer | >85% relevance score on held-out scenarios |
| **Load Tests** | Databricks throughput at 5,000+ events; Power BI refresh latency | Locust (free) | Pipeline + BI | <60s pipeline, <10s BI refresh |
| **Regression Suite** | All above tests run on every push; branch merge blocked on failure | GitHub Actions CI | All layers | 100% pass required on merge |

### AI-Assisted QA Techniques

1. **Synthetic Test Data Generation**
   - Gemini generates diverse, realistic event streams
   - Covers edge cases: rapid re-clicks, session timeouts, concurrent sessions, malformed payloads
   - Eliminates wait for live user data during development

2. **Insight Quality Evaluation**
   - Second Gemini call evaluates narrative relevance
   - Factual accuracy validation against source data
   - Automated narrative quality scoring

3. **Prompt Regression Testing**
   - AI prompt version-controlled in GitHub
   - Any prompt change triggers full quality test suite re-run
   - Detects accuracy drift automatically

4. **Pipeline Anomaly Detection**
   - Statistical control charts on Databricks output metrics
   - Alerts when aggregated values deviate from rolling baseline
   - Catches pipeline bugs before dashboard

### QA Deliverables
- Pytest test suite with >80% code coverage
- GitHub Actions CI pipeline (merge blocking on failure)
- QA Report: test results, coverage metrics, AI quality scores, load benchmarks (Weeks 10 & 17)
- Great Expectations data quality results after each pipeline run
- AI prompt version log with per-version quality scores

---

## Deliverables

### By Week 17 Final Review

**Live Demonstrations:**
- ✅ Real-time event capture: 10+ events across 3 flows visible in Azure Blob Storage
- ✅ Databricks pipeline: raw → enriched → aggregated tables, all queryable
- ✅ Interactive Power BI dashboard: all 4 analytics views live and responsive
- ✅ AI insight narratives: 3 distinct usage scenarios from Gemini
- ✅ Anomaly detection demo: simulated error spike with AI alert narrative
- ✅ Scale validation: 5,000+ synthetic events processed within SLA
- ✅ Cohort segmentation: high-engagement vs low-engagement user identification

**Documentation:**
- Architecture diagram (full stack)
- Event schema reference
- Databricks notebook documentation
- Power BI report configuration guide
- AI prompt design notes
- QA test results and coverage report

**Code & Tests:**
- JavaScript SDK (production-ready)
- Databricks ingestion & aggregation notebooks
- Azure Functions event handler
- Pytest test suite (>80% coverage)
- GitHub Actions CI/CD pipeline
- Great Expectations validation suite

**All uploaded to Moodle at checkpoints (Week 10 & Week 17)**

---

## Risk Mitigation

| # | Risk | Impact | Mitigation | Fallback |
|---|---|---|---|---|
| 1 | Databricks free cluster timeout during long pipeline runs | High | Split aggregations into micro-batches; schedule outside peak hours | Pre-compute in Azure Functions; store in Blob |
| 2 | Azure Functions cold-start delays drop events | Medium | Enable warm-up pings every 5 min via GitHub Actions | Switch to Azure Container Apps (free tier, no cold-start) |
| 3 | Gemini free tier quota exceeded during testing | Low | Use Ollama + Llama 4 Scout for all dev-phase calls; reserve Gemini quota for demo | Gemini API budget (₹300) covers ~3,000 additional calls |
| 4 | Power BI Service free workspace publish limits hit | Low | Publish via Desktop embedded in Teams/SharePoint (free) | Embed Power BI report iframe in GitHub Pages static HTML |
| 5 | JS SDK causes performance regression in target app | Medium | Batch events with 2-sec debounce; SDK <2KB gzipped; Lighthouse CI testing | Disable SDK on low-end devices; capture server-side via Functions logs |

---

## Minimum Compliance Requirements (From RFP)

✅ **Target Application:** At least 1 Psiog application  
✅ **Tracked Events:** At least 10 distinct events  
✅ **User Flows:** At least 3 distinct flows instrumented  
✅ **Databricks:** Full event ingestion, transformation, aggregation pipeline  
✅ **Power BI:** At least 4 analytics views (Usage, Journeys, Adoption, Errors)  
✅ **AI Scenarios:** At least 3 distinct usage scenarios (pattern analysis, anomaly detection, segmentation)  
✅ **Budget:** Within ₹2,500 ceiling (using ₹1,550 = 62%)  
✅ **QA:** Comprehensive strategy embedded across all phases  
✅ **Timeline:** Realistic week-by-week plan with meaningful milestones  
✅ **Problem Understanding:** Genuine grasp of business context; evidence-based, not opinion-driven

---

## Success Metrics

- ✅ Detect top 3 user drop-off points
- ✅ Identify most-used and least-used features
- ✅ Reduce time-to-detect UX issues through proactive anomaly reporting
- ✅ Generate weekly AI insight summaries consumable by non-technical stakeholders
- ✅ Enable data-driven feature prioritization for product teams

---

## Semester 3 Alignment

**Why This Fits E2E Cross + AI Custom Track:**

- **End-to-End Cross-Stack:** Client JS → Serverless backend → Cloud data processing → BI visualization → AI insights
- **AI as Core Intelligence:** Without Gemini insights, the platform only delivers raw data. AI transforms data into decisions.
- **Genuine Business Impact:** Addresses real gap; enables product teams to answer questions about user behavior today
- **QA Embedded:** Applied and extended Sem 1/2 QA skills with multi-layer testing across SDK, pipeline, and AI
- **Budget Discipline:** ₹1,550 of ₹2,500, with 11 of 13 components at zero cost; 38% buffer for safety

---

## Summary

**BehaviorLens** delivers a production-grade behavioral analytics platform on a student budget. Full instrumentation, data processing, interactive dashboards, and AI-powered insights—all built on free tiers with comprehensive QA embedded from day one.

**Ready to build.** Let's begin.
