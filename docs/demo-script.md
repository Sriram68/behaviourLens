# Final Demo Script

A 10-minute live demonstration built around one story: **BehaviorLens catches a
real product problem before any user reports it**, with the audience generating
part of the data from their own phones.

The data is eight weeks of simulated ReportHub traffic with three problems
deliberately planted in it (see `qa/synthetic_event_generator.py`), plus whatever
the audience does live. Say so on stage: it's a known-answer test, the same way you'd
fire-drill an alarm. The point is that the pipeline and the AI find the planted
problems without being told where they are.

| Planted finding | Where it shows up |
|---|---|
| Release **2.4.0** breaks `/api/export`: ~45% of exports fail with a 502 (baseline 2%), users retry, then give up | Live screen export tile; Error Patterns "errors by app version"; AI anomaly narrative; Teams alert |
| Onboarding **step 3** ("connect a data source") loses about a third of new users | User Journeys onboarding funnel; AI drop-off narrative |
| **Scheduled Reports** is barely used; **Share Link** throws a JavaScript error | Feature Adoption "clicks by product feature"; `js_error` in the live feed |

## T-minus 1 day: regenerate the history

The history is generated relative to "now", so regenerate it the day before (or the
morning of) the demo so the incident sits in "this week":

```bash
python qa/synthetic_event_generator.py --dry-run     # check the story summary
python qa/synthetic_event_generator.py               # ~33k events through the live Function
python databricks/run_pipeline.py --reset --since <today as YYYY/MM/DD>
```

Then, **in this order**:

1. `python scripts/preflight.py --no-ask` — among other checks it starts the Databricks SQL
   warehouse. Power BI Desktop hangs on "Working on it" if it opens the project while the
   warehouse is asleep (it auto-stops after 10 idle minutes).
2. Open `BehaviorLens.pbip` in Power BI Desktop, click **Home → Refresh** (about 30 s), then **Save**.
3. Spot-check: Error Patterns shows the spike at the right of "Total Errors by Date" and
   "Export failure rate by app version" at ~40% for 2.4.0; AI Insights shows 4 narratives,
   all `verified`.

## T-minus 10 minutes: preflight

```bash
python scripts/preflight.py
```

Every line must say PASS. It also starts the SQL warehouse (it auto-stops when idle)
and asks the live screen's four suggested questions, so their answers are cached for
15 minutes and come back instantly on stage. Run it no earlier than ~10 minutes
before the Ask segment. A brand-new question takes ~5-10 s on a warm warehouse.
Then open:

1. **Projector:** `https://sriram68.github.io/behaviourLens/demo-app/live.html`
2. **Laptop tab 2:** Power BI Desktop with the refreshed report
3. **Laptop tab 3:** a terminal in the repo root
4. **Laptop tab 4:** Teams, on the channel that receives the alert
5. **Your phone:** ReportHub, signed in, as a backup "audience"

## 1. The problem (45s)

> "Psiog ships apps, but nobody can say which features people use, where they get
> stuck, or what broke last Tuesday until a user complains. Every team instruments
> differently or not at all. BehaviorLens is one drop-in SDK plus a pipeline that
> turns clicks into answers, and it costs nothing to run."

## 2. The audience joins (2 min)

1. Point at the QR code on the live screen: *"Scan this, sign in with the defaults,
   and use the app: search, export a report, try sharing a link."*
2. As events arrive, narrate the live screen: events per minute climbing, sessions
   count, the flow breakdown, the feed.
3. The **Export failure rate** tile turns red with "Incident". Say: *"Some of you just
   hit a broken export. Remember that, we'll come back to it."*
4. Someone taps **Copy share link**: a `js_error` appears in the feed. *"Nobody wrote
   tracking code for that. The SDK caught a real JavaScript exception on its own."*
5. Mention privacy: the app never sends names or emails; the SDK masks any that slip
   in and the server masks them again.

## 3. Start the pipeline (15s)

In the terminal:

```bash
python databricks/run_pipeline.py --since <today as YYYY/MM/DD>
```

*"This syncs the new events into Databricks and runs ingestion, aggregation, 22 data
quality checks and the AI layer. It takes about three minutes; let's look at what's
already there."*

## 4. Architecture (45s)

One sentence per hop: SDK (2 KB, batching, retries, privacy) → Azure Function
(validates, rate-limits, writes NDJSON) → Blob Storage → Databricks (Delta tables,
Great Expectations, Gemini) → Power BI, with the live screen reading the Function
directly for the real-time view.

## 5. Power BI: the story in the dashboards (2 min)

1. **Usage Trends:** eight weeks of steady usage, weekday rhythm.
2. **User Journeys → onboarding funnel:** *"Step 3, connect a data source, is where new
   users disappear."*
3. **Feature Adoption → clicks by product feature:** *"Scheduled Reports: almost
   nobody uses it. Promote it or retire it."*
4. **Error Patterns:** errors jump from the release date; **errors by app version**
   shows it's essentially all 2.4.0.

## 6. AI insights you can trust (1.5 min)

1. **AI Insights** page: four narratives (weekly usage, anomaly, cohorts, drop-off).
2. Point at **Facts Verified 100%**: *"Every number in these sentences was checked in
   code against the data the model was given. If the model invents a number, the
   pipeline rejects the narrative, asks once more naming the wrong number, and if it
   still fails it never shows up as verified."*
3. Switch to **Teams**: the alert card the pipeline sent when it found the spike,
   with the release and the before/after failure rates.

## 7. Ask BehaviorLens (1.5 min)

On the live screen:

1. Click **"What is the export failure rate by app version?"**. Open *SQL and
   result*: the generated SQL, the rows, and *"✓ All numbers verified"*.
2. Invite a judge to ask their own question.
3. Type **"Delete all the sessions"**: the guardrail blocks it and shows the SQL it
   refused to run. *"It only ever runs one read-only SELECT on six allowlisted tables,
   as a service principal that can't read or write anything else."*

## 8. Close the loop (45s)

By now `run_pipeline.py` has finished. In Power BI, **Home → Refresh**: today's bar
now includes the audience's sessions, and their export failures are in the error
counts.

> "From your tap to a verified insight in a few minutes, on free tiers. 29 SDK tests,
> 69 Function tests, 7 pipeline tests that prove the AI layer finds what we planted, 7
> browser tests, 22 data quality checks, all in CI."

## If something breaks

| Problem | Fallback |
|---|---|
| Venue Wi-Fi is down | Phone hotspot. If the Function itself is unreachable, run it locally (`cd azure-function && func start`) and open `live.html?api=http://localhost:7071/api` |
| Pipeline run is slow or fails | Skip section 8. The dashboards and AI page already hold the regenerated history |
| Gemini is down or rate-limited | AI Insights page shows the narratives stored by the last run; skip the live Ask |
| Ask is slow or says the model is busy | Use a suggested question (cached by preflight), or ask again: it falls back across three Gemini models |
| Nobody scans the QR code | Use your own phone: sign in, export twice, tap Share |
| Everything is down | Play the recorded walkthrough |

## Reference docs

- `docs/pitch-notes.md`: talking points and likely questions
- `docs/architecture.md`: full data flow
- `docs/qa-report.md`: test results and bugs found/fixed
- `docs/ai-prompt-log.md`: prompt versions and the fact-check method
