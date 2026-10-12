"""Shared configuration and auth for the BehaviorLens Databricks scripts.

Credentials, in order of preference (never passed on the command line):
  Databricks: DATABRICKS_TOKEN, else an OAuth token from the Databricks CLI
              (`databricks auth login --host <host>` once, in a browser).
  Storage:    AZURE_STORAGE_CONNECTION_STRING, else AzureWebJobsStorage from
              azure-function/local.settings.json (gitignored).
"""

import json
import os
import pathlib
import shutil
import subprocess
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_HOST = "https://dbc-6051f46c-210c.cloud.databricks.com"
JOB_NAME = os.environ.get("BEHAVIORLENS_JOB_NAME", "BehaviorLens Pipeline")
CATALOG = "workspace"
SCHEMA = "default"
VOLUME = os.environ.get("BEHAVIORLENS_VOLUME", "raw_events_v2")
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "b785449970d46af7")
CONTAINER = os.environ.get("RAW_EVENTS_CONTAINER", "raw-events")


def host():
    value = os.environ.get("DATABRICKS_HOST", DEFAULT_HOST).rstrip("/")
    return value if value.startswith("http") else f"https://{value}"


def token():
    if os.environ.get("DATABRICKS_TOKEN"):
        return os.environ["DATABRICKS_TOKEN"]
    cli = shutil.which("databricks")
    if cli:
        out = subprocess.run([cli, "auth", "token", "--host", host()], capture_output=True, text=True, check=False)
        if out.returncode == 0:
            return json.loads(out.stdout)["access_token"]
    raise SystemExit(
        f"No Databricks credentials. Set DATABRICKS_TOKEN, or run once:\n  databricks auth login --host {host()}"
    )


def storage_connection_string():
    if os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        return os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    settings = ROOT / "azure-function" / "local.settings.json"
    if settings.exists():
        value = json.loads(settings.read_text(encoding="utf-8")).get("Values", {}).get("AzureWebJobsStorage")
        if value and "UseDevelopmentStorage" not in value:
            return value
    raise SystemExit("Set AZURE_STORAGE_CONNECTION_STRING (or AzureWebJobsStorage in azure-function/local.settings.json)")


class Workspace:
    def __init__(self):
        self.base = host()
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token()}"

    def request(self, method, path, **kwargs):
        kwargs.setdefault("timeout", 60)
        return self.session.request(method, self.base + path, **kwargs)

    def call(self, method, path, **kwargs):
        resp = self.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def find_job(self, name=JOB_NAME):
        jobs = self.call("GET", "/api/2.1/jobs/list", params={"name": name}).get("jobs", [])
        if not jobs:
            raise SystemExit(f"No Databricks job named '{name}'")
        return self.call("GET", "/api/2.1/jobs/get", params={"job_id": jobs[0]["job_id"]})

    def sql(self, statement):
        """Runs one statement on the SQL warehouse and waits for it."""
        data = self.call(
            "POST",
            "/api/2.0/sql/statements",
            json={"warehouse_id": WAREHOUSE_ID, "statement": statement, "wait_timeout": "50s",
                  "on_wait_timeout": "CONTINUE", "catalog": CATALOG, "schema": SCHEMA},
        )
        while data["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(2)
            data = self.call("GET", f"/api/2.0/sql/statements/{data['statement_id']}")
        if data["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(f"SQL failed: {data['status'].get('error', {}).get('message')}")
        return data.get("result", {}).get("data_array", [])
