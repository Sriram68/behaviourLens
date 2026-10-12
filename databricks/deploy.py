"""Deploys the pipeline to Databricks: uploads the four notebooks and the
modules they import (insights.py, factcheck.py) next to the notebooks the
"BehaviorLens Pipeline" Job already runs, creates the raw_events_v2 Volume, and
points the Job's ingestion task at it.

  python databricks/deploy.py

Credentials: see bl_client.py.
"""

import base64
import posixpath

import bl_client

NOTEBOOKS = ["01_ingestion", "02_aggregations", "03_data_quality", "04_ai_insights"]
MODULES = ["insights.py", "factcheck.py", "gemini.py"]
SOURCE_DIR = bl_client.ROOT / "databricks" / "notebooks"


def ensure_volume(ws):
    resp = ws.request(
        "POST",
        "/api/2.1/unity-catalog/volumes",
        json={"catalog_name": bl_client.CATALOG, "schema_name": bl_client.SCHEMA,
              "name": bl_client.VOLUME, "volume_type": "MANAGED"},
    )
    if resp.status_code == 200:
        print(f"Created volume {bl_client.VOLUME_PATH}")
    elif resp.status_code == 409 or "ALREADY_EXISTS" in resp.text:
        print(f"Volume {bl_client.VOLUME_PATH} already exists")
    else:
        raise RuntimeError(f"Creating volume failed: HTTP {resp.status_code} {resp.text[:300]}")


def notebook_dir(job):
    paths = [t["notebook_task"]["notebook_path"] for t in job["settings"]["tasks"] if "notebook_task" in t]
    dirs = {posixpath.dirname(p) for p in paths}
    if len(dirs) != 1:
        raise SystemExit(f"Expected the job's notebooks in one folder, found: {sorted(dirs)}")
    folder = dirs.pop()
    return folder[len("/Workspace"):] if folder.startswith("/Workspace/") else folder


def upload(ws, path, content, notebook):
    payload = {"path": path, "content": base64.b64encode(content).decode("ascii"), "overwrite": True}
    payload.update({"format": "SOURCE", "language": "PYTHON"} if notebook else {"format": "AUTO"})
    ws.call("POST", "/api/2.0/workspace/import", json=payload)
    print(f"Uploaded {path}")


def point_ingestion_at_volume(ws, job):
    tasks = job["settings"]["tasks"]
    changed = False
    for task in tasks:
        notebook = task.get("notebook_task")
        if notebook and notebook["notebook_path"].endswith("01_ingestion"):
            params = notebook.setdefault("base_parameters", {})
            if params.get("raw_path") != bl_client.VOLUME_PATH:
                params["raw_path"] = bl_client.VOLUME_PATH
                changed = True
    if changed:
        ws.call("POST", "/api/2.1/jobs/update", json={"job_id": job["job_id"], "new_settings": {"tasks": tasks}})
        print(f"Job ingestion task now reads {bl_client.VOLUME_PATH}")


def main():
    ws = bl_client.Workspace()
    job = ws.find_job()
    folder = notebook_dir(job)
    ensure_volume(ws)
    for name in NOTEBOOKS:
        upload(ws, f"{folder}/{name}", (SOURCE_DIR / f"{name}.py").read_bytes(), notebook=True)
    for name in MODULES:
        upload(ws, f"{folder}/{name}", (SOURCE_DIR / name).read_bytes(), notebook=False)
    point_ingestion_at_volume(ws, job)
    print(f"Deployed to {folder} for job {job['job_id']} ('{bl_client.JOB_NAME}').")


if __name__ == "__main__":
    main()
