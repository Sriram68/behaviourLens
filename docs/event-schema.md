# Event Schema Reference

Every event emitted by the BehaviorLens SDK follows this shape:

```json
{
  "eventName": "string",
  "userId": "string",
  "sessionId": "string",
  "timestamp": "ISO 8601 string",
  "pageUrl": "string",
  "properties": { "...": "event-specific key/value pairs" }
}
```

- `userId` and `sessionId` are generated client-side (localStorage / sessionStorage) and reused across events in the same browser session — see `sdk/src/behaviorlens.js`.
- All five top-level fields are required; the SDK's `validateEvent()` drops any event missing one, and the Databricks ingestion notebook re-validates on the server side.
- `properties` is free-form per event and becomes a widening struct column in `events_raw`/`events_enriched` as new event types are ingested (Delta writes use `overwriteSchema=true` to accommodate this — see [qa-report.md](qa-report.md)).

## Tracked events (10, across 3 flows)

### Login & Onboarding flow
| Event | Properties |
|---|---|
| `login_attempt` | `email` |
| `login_success` | `email` |
| `onboarding_step_complete` | `step` (1–3) |

### Core Feature Usage flow
| Event | Properties |
|---|---|
| `feature_click` | `feature` |
| `search_query` | `query` |
| `filter_applied` | `filter` |
| `export_triggered` | `format` |

### Error & Recovery flow
| Event | Properties |
|---|---|
| `api_error` | `code`, `endpoint` |
| `page_not_found` | `path` |
| `retry_attempt` | `originalEvent` |

## Example event

```json
{
  "eventName": "api_error",
  "userId": "6e88b328-3b4c-4d9e-a631-bc8bb4a66f55",
  "sessionId": "8b05eb4d-f512-4d73-ac33-9e8648c4a301",
  "timestamp": "2026-07-13T16:17:16.781Z",
  "pageUrl": "http://localhost:5500/demo-app/index.html",
  "properties": { "code": 500, "endpoint": "/api/reports" }
}
```

Captured verbatim from a real Blob Storage NDJSON file during demo app testing.
