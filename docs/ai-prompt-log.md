# AI Prompt Version Log

Prompts live in `databricks/notebooks/04_ai_insights.py`. Any prompt-text
change gets a new `PROMPT_VERSION` and a new row below — same principle as the
mid-term's "AI prompt version log with per-version quality scores" QA
deliverable.

## Model

`gemini-3.6-flash` via the Generative Language REST API, called with a
Databricks secret (`behaviorlens/gemini_api_key`) rather than a hardcoded key.

**Deviation from the proposal:** the proposal specifies Gemini 2.5 Flash. As of
this build, `gemini-2.5-flash` returns `404 NOT_FOUND` for newly-created API
keys — the API itself redirects new callers to `gemini-3.6-flash` ("This model
... is no longer available to new users"). Functionally equivalent choice
(same tier, same use case), substituted only because the originally-proposed
model is no longer reachable with a fresh key.

## v1 — initial three scenarios (current)

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
