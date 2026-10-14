"""One command from "people clicked" to "dashboards are current":
incremental Blob -> Volume sync, then the "BehaviorLens Pipeline" Databricks Job
(ingestion -> aggregations -> data_quality -> ai_insights), waiting for it and
reporting every task. Afterwards, refresh Power BI (Home -> Refresh).

  python databricks/run_pipeline.py --since 2026/10/12
  python databricks/run_pipeline.py --skip-sync          # just re-run the job
  python databricks/run_pipeline.py --reset --since 2026/10/12 --alert never
      # after regenerating the demo history: empty the Volume, sync only today's blobs,
      # and keep the Teams alert for the on-stage run

--alert once|always|never sets the Teams alert mode (default: the notebook's
"once", i.e. one alert per incident).

Credentials: see bl_client.py.
"""

import argparse
import sys
import time

import bl_client
from sync_raw_events import sync


def run_job(ws, alert_mode=None):
    job = ws.find_job()
    body = {"job_id": job["job_id"]}
    if alert_mode:
        body["notebook_params"] = {"alert_mode": alert_mode}
    run = ws.call("POST", "/api/2.1/jobs/run-now", json=body)
    started = time.monotonic()
    print(f"Started '{bl_client.JOB_NAME}' (run {run['run_id']})")
    last_line = None
    while True:
        state = ws.call("GET", "/api/2.1/jobs/runs/get", params={"run_id": run["run_id"]})
        tasks = sorted(state.get("tasks", []), key=lambda t: t.get("start_time") or 0)
        line = "  " + " -> ".join(
            f"{t['task_key']}:{t['state'].get('result_state') or t['state'].get('life_cycle_state')}" for t in tasks
        )
        if line != last_line:
            print(f"{time.monotonic() - started:5.0f}s {line}")
            last_line = line
        if state["state"]["life_cycle_state"] in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        time.sleep(5)

    result = state["state"].get("result_state")
    print(f"Run {result} in {time.monotonic() - started:.0f}s: {state.get('run_page_url')}")
    for task in tasks:
        if task["state"].get("result_state") not in (None, "SUCCESS"):
            print(f"  {task['task_key']}: {task['state'].get('state_message', '')[:300]}")
    return result == "SUCCESS"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2000/01/01", help="sync blobs written on/after YYYY/MM/DD")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--reset", action="store_true", help="empty the Volume before syncing")
    parser.add_argument("--alert", choices=["once", "always", "never"], help="Teams alert mode for this run")
    args = parser.parse_args()

    if not args.skip_sync:
        _, failed = sync(args.since, reset=args.reset)
        if failed:
            sys.exit("Sync had failures; not starting the job.")
    ok = run_job(bl_client.Workspace(), args.alert)
    if ok:
        print("Done. Refresh Power BI (Home -> Refresh) to pull the new tables.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
