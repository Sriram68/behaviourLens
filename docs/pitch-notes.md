# Pitch Notes

Talking points for the final demonstration. Pair with `docs/demo-script.md`.

## The one-liner

**BehaviorLens shows which features people use, where they get stuck, and what just
broke, and it explains each finding in plain English with every number checked. One
drop-in SDK, ₹0 to run.**

## The story arc

1. **Problem:** Psiog teams ship apps but can't see how they're used. Feature decisions
   are opinions; UX failures surface only when someone complains.
2. **Proof it works:** eight weeks of ReportHub traffic with three planted problems.
   BehaviorLens finds all three without being told where to look. The audience adds
   live traffic and walks into the same incident.
3. **Trust:** numbers come from code, words from the model, and a fact check stands
   between them. Unsafe questions get blocked.
4. **Cost and effort:** free tiers, two script tags to instrument an app.

## Numbers to remember

Read the exact values off the dashboards on the day: the history is regenerated, so
they vary a little. Typical values from the seeded run:

| Finding | Typical value |
|---|---|
| Export failure rate before vs. since release 2.4.0 | ~2% → ~40% (about 20×) |
| Sessions that gave up after a failed export | ~50% |
| New users lost at onboarding step 3 | ~35% of those who reach it |
| Scheduled Reports share of feature clicks | ~2% |

| Engineering fact | Value |
|---|---|
| SDK size, minified + gzipped | 2,038 bytes (2 KB budget enforced in CI) |
| Automated tests | 29 SDK + 69 Function + 7 pipeline + 7 browser |
| Data quality checks per pipeline run | 22 (incl. "zero unmasked emails") |
| Simulated history | ~33k events, ~2.9k sessions, ~270 users, 8 weeks |
| Spend | ₹0 of the ₹2,500 budget |

## What makes it different

- **Verified AI, not vibes.** `insights.py` computes every number; Gemini only narrates;
  `factcheck.py` rejects any narrative containing a number, date or version that isn't
  in the data. One self-correction attempt, then it's held as `needs_review`. Tests
  prove the AI layer finds the planted incident, drop-off and dead feature.
- **Privacy by design.** No names or emails are collected. The SDK masks emails anyway,
  the Function masks them again, and a data-quality check fails the pipeline if one
  ever lands. Users can opt out. All IDs are anonymous.
- **Real time and analytics.** The live screen reads the ingestion layer directly
  (seconds); Databricks does the heavy analytics (minutes).
- **Safe natural-language questions.** "Ask BehaviorLens" writes SQL, but a parser only
  allows one read-only SELECT on six allowlisted tables, blocks functions like
  `secret()` and `read_files()`, caps rows, and runs as a service principal that can
  read nothing else. The SQL is shown to the user.
- **Production habits.** Retries with backoff, offline persistence, server-side
  validation, rate limiting, CI on every push, a preflight script for the demo itself.

## Likely questions

**Why not Google Analytics, Mixpanel or Application Insights?**
Data stays inside Psiog's own Azure tenant, which matters for client work and India's
DPDP Act. There's no per-event or per-user pricing. One event schema works across every
Psiog app. And the AI layer is built around our questions. Application Insights is
great for request and exception telemetry; BehaviorLens is about product behaviour
(funnels, adoption, journeys). They complement each other.

**Is this real data?**
The history is simulated with problems planted on purpose: a known-answer test, like a
fire drill. That's how we prove detection works. The audience's clicks are real. The
next step is a pilot on a real Psiog application.

**How do you know the AI isn't making things up?**
It never computes anything: every number comes from pandas code, and a deterministic
checker verifies every number, date and version in the text against that data. Unit
tests confirm the facts layer finds the planted problems. A second LLM call scores
relevance, but only as a soft signal; the hard gate is the fact check.

**Couldn't someone use "Ask" to read or delete data?**
The query is parsed and must be a single SELECT over six analytics tables; writes,
other schemas and risky functions are rejected before anything runs. Even past the
parser, the service principal only has SELECT on those six tables. Requests are
rate-limited per IP.

**Does it scale?**
Azure Functions scales out automatically and the SDK batches events. Databricks is
Spark, so the same notebooks handle millions of rows. Today the pipeline rewrites
tables each run; for production scale, switch to incremental ingestion (Auto Loader +
MERGE) and a schedule. Tested end to end at ~33k events here and 12k in the final-term
scale test.

**What does it cost beyond the free tier?**
Ingestion and storage are very cheap at Psiog's volumes (Functions bills per execution
and the SDK batches events). The main cost driver at scale would be Databricks compute
per run. Check current Azure and Databricks pricing before quoting figures.

**How long to add BehaviorLens to an app?**
Two script tags and `BehaviorLens.init({ endpoint, autoCapture: true })`. Page views
and JavaScript errors are captured automatically; any button can be tracked with a
`data-bl-event` attribute and no code.

**What if the ingestion endpoint goes down?**
The SDK queues events, retries with exponential backoff (capped at 30 s), and keeps
unsent events in localStorage across reloads, so short outages lose nothing.

**What's next?**
A pilot on one real Psiog app; incremental, scheduled ingestion; per-app tenancy
(an `appId` on every event); alert routing per team; publishing to the Power BI service
with row-level security.

## Limitations to volunteer (they build credibility)

- Rate limiting is in-memory per Function instance: a first line of defence, not a quota.
- The live view scans the last 30 minutes of blobs: fine for hundreds of events a
  minute, not for millions (that's what the Databricks path is for).
- Tables are fully rewritten each run (simple and fast at this size; see "Does it scale?").
- The Power BI report runs in Desktop, not yet published to the Power BI service.
- Gemini 2.5 Flash (proposal) is unavailable to new keys, so the build uses
  `gemini-3.6-flash`.
