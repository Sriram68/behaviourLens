"""Demo-morning preflight: checks every moving part of BehaviorLens and warms
up what's slow to start (the Function and the SQL warehouse).

  python scripts/preflight.py            # everything, including one real Ask
  python scripts/preflight.py --no-ask   # skip the Gemini/warehouse round trip

Exit code is non-zero if anything the live demo depends on is down. The
Databricks checks run only when credentials are available (see
databricks/bl_client.py); everything else uses the standard library.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://func-behaviorlens47209.azurewebsites.net/api"
PAGES = "https://sriram68.github.io/behaviourLens/demo-app"
ORIGINS = ["https://sriram68.github.io", "http://localhost:5500"]

results = []


def check(name, fn, required=True):
    started = time.monotonic()
    try:
        detail = fn() or ""
        ok = True
    except Exception as err:  # noqa: BLE001 - report any failure as a failed check
        detail, ok = f"{type(err).__name__}: {err}", False
    elapsed = time.monotonic() - started
    mark = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"[{mark}] {name} ({elapsed:.1f}s) {detail}")
    results.append((ok, required))


def http(url, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read()


def function_health():
    status, _, body = http(f"{API}/health", timeout=90)  # first call may cold-start
    data = json.loads(body)
    if not data["checks"]["storage"]:
        raise RuntimeError("storage unreachable")
    return f"storage ok, ask configured: {data['checks']['askConfigured']}"


def cors(origin):
    def run():
        _, headers, _ = http(
            f"{API}/events", method="OPTIONS",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
        )
        allowed = {k.lower(): v for k, v in headers.items()}.get("access-control-allow-origin")
        if allowed not in (origin, "*"):
            raise RuntimeError(f"origin not allowed (got {allowed!r})")
        return "allowed"
    return run


def live_stats():
    _, _, body = http(f"{API}/stats?minutes=30")
    totals = json.loads(body)["totals"]
    return f"{totals['events']} events in the last 30 min"


def page(url):
    def run():
        status, _, _ = http(url)
        return f"HTTP {status}"
    return run


def ask():
    status, _, body = http(f"{API}/ask", method="POST", body={"question": "How many sessions were there in total?"}, timeout=180)
    data = json.loads(body)
    check_ = data.get("factCheck", {})
    return f"\"{data.get('answer', '')[:80]}\" ({check_.get('verified')}/{check_.get('total')} verified)"


def databricks_checks():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "databricks"))
    try:
        import bl_client

        ws = bl_client.Workspace()
    except (ImportError, SystemExit) as err:
        print(f"[SKIP] Databricks checks: {err}")
        return

    def last_run():
        job = ws.find_job()
        runs = ws.call("GET", "/api/2.1/jobs/runs/list", params={"job_id": job["job_id"], "limit": 1}).get("runs", [])
        if not runs:
            raise RuntimeError("job has never run")
        state = runs[0]["state"]
        if state.get("result_state") != "SUCCESS":
            raise RuntimeError(f"last run {state.get('result_state') or state.get('life_cycle_state')}")
        ended = time.strftime("%Y-%m-%d %H:%M", time.localtime(runs[0].get("end_time", 0) / 1000))
        return f"last run SUCCESS at {ended}"

    def warehouse():
        info = ws.call("GET", f"/api/2.0/sql/warehouses/{bl_client.WAREHOUSE_ID}")
        if info["state"] in ("STOPPED", "STOPPING"):
            ws.call("POST", f"/api/2.0/sql/warehouses/{bl_client.WAREHOUSE_ID}/start")
            return "was stopped, starting it now (Power BI refresh and Ask will be fast)"
        return f"state {info['state']}"

    check("Databricks job", last_run)
    check("SQL warehouse warm", warehouse, required=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ask", action="store_true")
    args = parser.parse_args()

    check("Function health", function_health)
    for origin in ORIGINS:
        check(f"CORS for {origin}", cors(origin))
    check("Live stats endpoint", live_stats)
    check("ReportHub on GitHub Pages", page(f"{PAGES}/index.html"))
    check("Live view on GitHub Pages", page(f"{PAGES}/live.html"))
    databricks_checks()
    if not args.no_ask:
        check("Ask BehaviorLens round trip", ask, required=False)

    failed = [r for r in results if not r[0] and r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (" - NOT demo-ready" if failed else " - demo-ready"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
