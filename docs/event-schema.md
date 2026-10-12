# Event Schema Reference

Every event emitted by the BehaviorLens SDK follows this shape:

```json
{
  "eventName": "string",
  "userId": "string",
  "sessionId": "string",
  "timestamp": "ISO 8601 string",
  "pageUrl": "string",
  "appVersion": "string (optional, SDK v2)",
  "properties": { "...": "event-specific key/value pairs" }
}
```

- `userId` and `sessionId` are anonymous random IDs generated client-side (localStorage / sessionStorage) and reused across events in the same browser session — see `sdk/src/behaviorlens.js`.
- The five fields `eventName`, `userId`, `sessionId`, `timestamp`, `pageUrl` are required. The SDK's `validateEvent()` drops any event missing one; the Azure Function re-validates (event name must match `^[a-z][a-z0-9_]{1,63}$`, timestamp must parse, `properties` must be an object, at most 8 KB per event and 200 events per batch); the Databricks ingestion notebook validates a third time.
- `appVersion` is stamped on every event when the app passes it to `BehaviorLens.init({ appVersion })`. It's what lets the pipeline tie an error spike to a release.
- `properties` is free-form per event and becomes a widening struct column in `events_raw`/`events_enriched` (Delta writes use `overwriteSchema=true` — see [qa-report.md](qa-report.md)). The most-used properties are also flattened into typed columns in `events_enriched`: `feature`, `onboarding_step`, `error_code`, `endpoint`.

## Privacy rules

- No names or email addresses are collected. ReportHub tracks the sign-in **method**, not the email.
- Defence in depth: the SDK masks any email address in `properties` and `pageUrl` (`jane@psiog.com` → `[redacted]@psiog.com`), the Function masks again server-side, and the data-quality notebook fails the pipeline if an unmasked email ever reaches `events_raw`.
- `BehaviorLens.optOut()` stops tracking on that device and clears the queue (ReportHub exposes it in Settings).

## Tracked events (12)

### Captured automatically (SDK `autoCapture: true`)
| Event | Properties |
|---|---|
| `page_view` | `path` (pathname + hash), on load and on every route change |
| `js_error` | `message`, `source`, `line`: uncaught errors and unhandled promise rejections |

Any element with `data-bl-event="<name>"` is also tracked on click, with each `data-bl-prop-<key>="<value>"` attribute as a property, without writing code.

### Login & Onboarding flow
| Event | Properties |
|---|---|
| `login_attempt` | `method` (`password` / `sso`) |
| `login_success` | `method` |
| `onboarding_step_complete` | `step` (1 profile, 2 team, 3 connect a data source) |

### Core Feature Usage flow
| Event | Properties |
|---|---|
| `feature_click` | `feature` (`dashboard`, `reports`, `saved_views`, `share_link`, `scheduled_reports`) |
| `search_query` | `query`, `results` (count; 0 = content gap) |
| `filter_applied` | `filter` |
| `export_triggered` | `format` (`csv` / `pdf`) |

### Error & Recovery flow
| Event | Properties |
|---|---|
| `api_error` | `code`, `endpoint` |
| `page_not_found` | `path` |
| `retry_attempt` | `originalEvent`, `endpoint` |
| `js_error` | see above (captured automatically) |

## Example event

```json
{
  "eventName": "api_error",
  "userId": "6e88b328-3b4c-4d9e-a631-bc8bb4a66f55",
  "sessionId": "8b05eb4d-f512-4d73-ac33-9e8648c4a301",
  "timestamp": "2026-10-12T10:17:16.781Z",
  "pageUrl": "https://sriram68.github.io/behaviourLens/demo-app/index.html#/reports",
  "appVersion": "2.4.0",
  "properties": { "code": 502, "endpoint": "/api/export" }
}
```

## Schema history

- **v1 (mid-term and final-term):** 10 events; `login_*` carried the user's `email`. That data stays in the `raw_events` Volume.
- **v2 (current):** adds `page_view`, `js_error` and `appVersion`; replaces `email` with `method`; masks PII at three layers. Data lands in the `raw_events_v2` Volume.
