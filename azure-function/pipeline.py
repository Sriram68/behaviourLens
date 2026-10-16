"""Runs the pipeline from BehaviorLens Live: copies today's new event files into
the Databricks Volume, starts the "BehaviorLens Pipeline" Job and reports its tasks.

It uses its own Databricks identity (`behaviorlens-runner`, set up by
databricks/setup_pipeline_runner.py) that can only run that one Job and write
that one Volume, so Ask's identity stays read-only. The Live page is public, so
every pipeline call needs the presenter key (PIPELINE_RUN_KEY), compared in
constant time.
"""

import hmac
import os
from concurrent.futures import ThreadPoolExecutor

import requests

VOLUME_PATH = os.environ.get("DATABRICKS_VOLUME_PATH", "/Volumes/workspace/default/raw_events_v2")
TASK_ORDER = ["ingestion", "aggregations", "data_quality", "ai_insights"]
ALERT_MODES = ("once", "always", "never")


def _host():
    return os.environ.get("DATABRICKS_HOST", "").rstrip("/")


def job_id():
    return os.environ.get("PIPELINE_JOB_ID", "")


def is_configured():
    return bool(_host() and os.environ.get("RUNNER_DATABRICKS_TOKEN") and job_id() and os.environ.get("PIPELINE_RUN_KEY"))


def key_matches(supplied):
    expected = os.environ.get("PIPELINE_RUN_KEY", "")
    return bool(expected and supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def session():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {os.environ.get('RUNNER_DATABRICKS_TOKEN', '')}"
    return s


def _call(s, method, path, **kwargs):
    kwargs.setdefault("timeout", 30)
    resp = s.request(method, _host() + path, **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _volume_files(s, prefix):
    """Names (YYYY/MM/DD/uuid.ndjson) already in the Volume under `prefix`."""
    found, page_token = set(), None
    while True:
        params = {"page_token": page_token} if page_token else {}
        resp = s.get(f"{_host()}/api/2.0/fs/directories{VOLUME_PATH}/{prefix}", params=params, timeout=30)
        if resp.status_code == 404:
            return found
        resp.raise_for_status()
        data = resp.json()
        found.update(f"{prefix}/{e['name']}" for e in data.get("contents", []) if not e.get("is_directory"))
        page_token = data.get("next_page_token")
        if not page_token:
            return found


def sync_today(s, container, now):
    """Uploads today's blobs (UTC) that aren't in the Volume yet. Blobs are
    write-once, so a second click only uploads what arrived in between."""
    prefix = f"{now:%Y/%m/%d}"
    blobs = [b.name for b in container.list_blobs(name_starts_with=prefix + "/") if b.name.endswith(".ndjson")]
    todo = sorted(set(blobs) - _volume_files(s, prefix))

    def upload(name):
        data = container.download_blob(name).readall()
        resp = s.put(f"{_host()}/api/2.0/fs/files{VOLUME_PATH}/{name}", params={"overwrite": "false"}, data=data, timeout=60)
        return resp.status_code in (200, 204, 409)  # 409: uploaded by a concurrent sync

    with ThreadPoolExecutor(max_workers=16) as pool:
        ok = sum(pool.map(upload, todo))
    return {"uploaded": ok, "failed": len(todo) - ok, "alreadySynced": len(blobs) - len(todo)}


def active_run(s):
    runs = _call(s, "GET", "/api/2.1/jobs/runs/list", params={"job_id": job_id(), "active_only": "true"}).get("runs", [])
    return runs[0]["run_id"] if runs else None


def start_run(s, alert_mode=None):
    body = {"job_id": int(job_id())}
    if alert_mode:
        body["notebook_params"] = {"alert_mode": alert_mode}
    return _call(s, "POST", "/api/2.1/jobs/run-now", json=body)["run_id"]


def run_status(s, run_id):
    data = _call(s, "GET", "/api/2.1/jobs/runs/get", params={"run_id": run_id})
    if str(data.get("job_id")) != str(job_id()):
        raise LookupError("not a BehaviorLens pipeline run")
    states = {t["task_key"]: t.get("state", {}) for t in data.get("tasks", [])}
    tasks = []
    for key in TASK_ORDER:
        state = states.get(key, {})
        tasks.append({"key": key, "state": state.get("result_state") or state.get("life_cycle_state") or "PENDING"})
    return {
        "runId": run_id,
        "state": data["state"].get("life_cycle_state"),
        "result": data["state"].get("result_state"),
        "tasks": tasks,
        "startTime": data.get("start_time"),
        "endTime": data.get("end_time") or None,
    }
