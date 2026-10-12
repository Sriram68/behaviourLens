# AI Prompt Version Log

Prompts live in `databricks/notebooks/insights.py` (`PROMPT`, `SCENARIOS`) and are
called from `04_ai_insights.py`. Any prompt-text change gets a new `PROMPT_VERSION`
and a new section below — same principle as the mid-term's "AI prompt version log
with per-version quality scores" QA deliverable. Every run is also appended to the
`ai_insights_history` Delta table, so versions can be compared on real output.

## Model

`gemini-3.6-flash` via the Generative Language REST API, called with a
Databricks secret (`behaviorlens/gemini_api_key`) rather than a hardcoded key.

**Deviation from the proposal:** the proposal specifies Gemini 2.5 Flash. As of
this build, `gemini-2.5-flash` returns `404 NOT_FOUND` for newly-created API
keys — the API itself redirects new callers to `gemini-3.6-flash` ("This model
... is no longer available to new users"). Functionally equivalent choice
(same tier, same use case), substituted only because the originally-proposed
model is no longer reachable with a fresh key.

## v2 — computed facts, verified narratives (current)

**Why v1 wasn't enough:** v1 asked Gemini to narrate raw aggregates and then asked
Gemini to grade its own narrative. A 100/100 self-score says little: the same model
judges its own work, and nothing stops it computing (or inventing) a wrong number.
v1's "weekly" narrative also used all-time totals, not a week.

**What v2 changes:**

1. **Numbers are computed in code.** `insights.py` builds a facts pack per scenario
   from the pipeline tables. The model gets only that pack and is told to use only
   its numbers, write dates as YYYY-MM-DD, and compute nothing.
2. **Every narrative is fact-checked.** `factcheck.verify()` extracts each number,
   ISO date and version string and requires it to appear in the pack (rounding to
   the precision written is allowed; a 0-1 ratio may be written as a percentage).
3. **One self-correction.** A failing narrative is regenerated once, with the
   offending numbers named in the prompt. If it still fails it is stored as
   `needs_review` and never presented as verified.
4. **The LLM score is demoted** to a soft relevance signal (`relevance_score`).
   The hard gate is the fact check: the job fails if fewer than 3 of 4 narratives
   verify.

| Scenario | Facts pack (`insights.py`) |
|---|---|
| `weekly_narrative` | This week vs last week: active users, sessions, avg session minutes, feature uses, exports, export failure rate; feature clicks by feature; most/least used feature |
| `anomaly_detection` | Daily errors for the last 7 days with z-scores against the 21 days before (flag at z > 3); root cause of flagged days by event/code/endpoint; error share by app version; latest release and export failure rate before/since it; sessions abandoned after a failed export; `alert` if a flagged day is in the last 3 days |
| `cohort_segmentation` | Users split at the median sessions per user (28 days): sessions, session length, feature adoption %, and the feature with the biggest adoption gap |
| `funnel_dropoff` | Onboarding funnel (started → step 1 → 2 → 3) with conversions and losses; top three drop-off points ranked (onboarding, abandoned exports, failed sign-in) |

Scores for v2 are recorded per run in `ai_insights_history`
(`status`, `facts_verified`/`facts_total`, `attempts`, `relevance_score`).

### Teams alert

When `anomaly_detection.alert` is true and its narrative verified, the notebook
posts an adaptive card (narrative, release, export failure rate before/since,
fact-check line, link to the live view) to the webhook in the optional secret
`behaviorlens/teams_webhook_url`. The outcome is stored in `alert_status`.

### Ask BehaviorLens (`azure-function/ask.py`)

Same rule on a different path: Gemini writes one SQL query (`SQL_PROMPT`), told to
do all arithmetic in SQL; the guardrail validates it; Gemini narrates the result
(`ANSWER_PROMPT`); `factcheck.verify()` checks the answer against the result rows,
with one self-correction pass.

## v1 — initial three scenarios

| Scenario | Input | Relevance score |
|---|---|---|
| `weekly_narrative` | Feature-adoption totals by event type | 100/100 |
| `anomaly_detection` | Daily error counts + z-score outlier flags | 100/100 |
| `cohort_segmentation` | Session counts/avg events by engagement cohort | 100/100 |

**Average: 100/100** against the proposal's >85% relevance target.

Sample output (run against the 12,001-event dataset, see `qa-report.md` for
the underlying scale-test numbers):

- **Weekly narrative** correctly identified `feature_click`/`search_query` as
  the most-adopted features (~1,142 distinct users) and `export_triggered` as
  least-adopted (1,062 users), with a concrete recommendation (surface export
  CTAs inside search/filter views).
- **Anomaly detection** correctly flagged the real Aug 29–Sep 1 spike in the
  synthetic dataset (peak 338 errors/day vs. a ~10/day baseline) as a
  statistical outlier warranting investigation — this is a genuine artifact of
  how the scale-test data was generated in two disjoint bursts, not a
  fabricated anomaly.
- **Cohort segmentation** split sessions at the median event count (730
  high-engagement vs. 613 low-engagement) and proposed investigating
  drop-off after the second event.

### Scoring methodology

A second Gemini call rates each narrative 0-100 for factual accuracy/relevance
against the exact aggregated data it was built from (not raw events) —
`score_relevance()` in the notebook. This is the same data the narrative
prompt saw, so the score measures whether the narrative faithfully represents
its own inputs, not independent fact-checking against ground truth.

## Known gap

Gemini 3.6 Flash's "thinking" behavior (visible in raw API responses as
`thoughtSignature`/`thoughtsTokenCount`) adds latency and token overhead
per call versus the proposal's assumption of a lighter Flash-tier model. Not a
functional issue at this call volume (6 calls per pipeline run, comfortably
inside the free tier's 1,500 req/day), but worth knowing if scaling to true
weekly-scheduled runs across many more scenarios.
