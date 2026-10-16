import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import azure.functions as func
import pytest
import requests

import function_app
import pipeline

NOW = datetime.datetime(2026, 10, 16, 9, 30, tzinfo=datetime.timezone.utc)
KEY = "presenter-key-123"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc.example.test/")
    monkeypatch.setenv("RUNNER_DATABRICKS_TOKEN", "runner-token")
    monkeypatch.setenv("PIPELINE_JOB_ID", "910")
    monkeypatch.setenv("PIPELINE_RUN_KEY", KEY)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.content = json.dumps(self._payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeContainer:
    def __init__(self, names):
        self.names = names

    def list_blobs(self, name_starts_with):
        return [SimpleNamespace(name=n) for n in self.names if n.startswith(name_starts_with)]

    def download_blob(self, name):
        return SimpleNamespace(readall=lambda: f"data of {name}".encode())


# --- Module ---------------------------------------------------------------------------


def test_configuration_and_key(configured, monkeypatch):
    assert pipeline.is_configured()
    assert pipeline.key_matches(KEY)
    assert not pipeline.key_matches("wrong")
    assert not pipeline.key_matches("")
    monkeypatch.delenv("PIPELINE_RUN_KEY")
    assert not pipeline.is_configured()
    assert not pipeline.key_matches("")  # an unset key never matches an empty one


def test_sync_uploads_only_todays_missing_files(configured):
    container = FakeContainer([
        "2026/10/15/old.ndjson",  # yesterday: never synced from Live
        "2026/10/16/a.ndjson",
        "2026/10/16/b.ndjson",
        "2026/10/16/notes.txt",
    ])
    s = MagicMock()
    s.get.return_value = FakeResponse(payload={"contents": [{"name": "a.ndjson"}, {"name": "sub", "is_directory": True}]})
    s.put.return_value = FakeResponse(status=204)

    result = pipeline.sync_today(s, container, NOW)

    assert result == {"uploaded": 1, "failed": 0, "alreadySynced": 1}
    assert s.get.call_args.args[0] == "https://dbc.example.test/api/2.0/fs/directories/Volumes/workspace/default/raw_events_v2/2026/10/16"
    url = s.put.call_args.args[0]
    assert url.endswith("/api/2.0/fs/files/Volumes/workspace/default/raw_events_v2/2026/10/16/b.ndjson")
    assert s.put.call_args.kwargs["params"] == {"overwrite": "false"}


def test_sync_handles_an_empty_volume_folder_and_counts_failures(configured):
    s = MagicMock()
    s.get.return_value = FakeResponse(status=404)
    s.put.side_effect = [FakeResponse(status=204), FakeResponse(status=500)]
    result = pipeline.sync_today(s, FakeContainer(["2026/10/16/a.ndjson", "2026/10/16/b.ndjson"]), NOW)
    assert result == {"uploaded": 1, "failed": 1, "alreadySynced": 0}


def test_start_run_passes_the_alert_mode(configured):
    s = MagicMock()
    s.request.return_value = FakeResponse(payload={"run_id": 77})
    assert pipeline.start_run(s, "never") == 77
    assert s.request.call_args.kwargs["json"] == {"job_id": 910, "notebook_params": {"alert_mode": "never"}}
    pipeline.start_run(s)
    assert s.request.call_args.kwargs["json"] == {"job_id": 910}


def test_run_status_lists_the_four_tasks_in_order(configured):
    s = MagicMock()
    s.request.return_value = FakeResponse(payload={
        "job_id": 910,
        "start_time": 1000,
        "end_time": 0,
        "state": {"life_cycle_state": "RUNNING"},
        "tasks": [
            {"task_key": "aggregations", "state": {"life_cycle_state": "RUNNING"}},
            {"task_key": "ingestion", "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}},
        ],
    })
    status = pipeline.run_status(s, 5)
    assert [t["key"] for t in status["tasks"]] == pipeline.TASK_ORDER
    assert [t["state"] for t in status["tasks"]] == ["SUCCESS", "RUNNING", "PENDING", "PENDING"]
    assert status["state"] == "RUNNING" and status["endTime"] is None


def test_run_status_refuses_other_jobs_runs(configured):
    s = MagicMock()
    s.request.return_value = FakeResponse(payload={"job_id": 1, "state": {}})
    with pytest.raises(LookupError):
        pipeline.run_status(s, 5)


# --- Endpoints ------------------------------------------------------------------------


@pytest.fixture
def endpoint(configured, monkeypatch):
    monkeypatch.setattr(function_app, "pipeline_run_limiter", function_app.RateLimiter(5))
    monkeypatch.setattr(function_app, "pipeline_status_limiter", function_app.RateLimiter(30))
    monkeypatch.setattr(function_app, "_get_container_client", MagicMock())
    monkeypatch.setattr(function_app, "_utcnow", lambda: NOW)
    monkeypatch.setattr(pipeline, "active_run", lambda s: None)
    monkeypatch.setattr(pipeline, "sync_today", lambda s, container, now: {"uploaded": 3, "failed": 0, "alreadySynced": 9})
    started = MagicMock(return_value=42)
    monkeypatch.setattr(pipeline, "start_run", started)
    return started


def _run(key=KEY, body=b"", ip="198.51.100.7"):
    headers = {"X-Forwarded-For": ip}
    if key is not None:
        headers["X-Presenter-Key"] = key
    return function_app.run_pipeline(func.HttpRequest(method="POST", url="/api/pipeline/run", body=body, headers=headers))


def _status(run_id="42", key=KEY):
    headers = {"X-Presenter-Key": key} if key else {}
    return function_app.pipeline_status(
        func.HttpRequest(method="GET", url="/api/pipeline/status", body=b"", headers=headers, params={"run_id": run_id})
    )


def test_run_requires_the_presenter_key(endpoint):
    assert _run(key=None).status_code == 401
    assert _run(key="guess").status_code == 401
    endpoint.assert_not_called()


def test_run_is_unavailable_when_not_configured(endpoint, monkeypatch):
    monkeypatch.delenv("RUNNER_DATABRICKS_TOKEN")
    assert _run().status_code == 503


def test_run_syncs_then_starts(endpoint):
    resp = _run(body=json.dumps({"alert": "never"}).encode())
    assert resp.status_code == 200
    assert json.loads(resp.get_body()) == {"status": "started", "runId": 42, "synced": {"uploaded": 3, "failed": 0, "alreadySynced": 9}}
    assert endpoint.call_args.args[1] == "never"


def test_run_rejects_an_unknown_alert_mode(endpoint):
    assert _run(body=b'{"alert": "loud"}').status_code == 400


def test_run_reports_the_run_already_in_progress(endpoint, monkeypatch):
    monkeypatch.setattr(pipeline, "active_run", lambda s: 41)
    resp = _run()
    assert resp.status_code == 409
    assert json.loads(resp.get_body()) == {"status": "running", "runId": 41}
    endpoint.assert_not_called()


def test_run_is_not_started_when_sync_fails(endpoint, monkeypatch):
    monkeypatch.setattr(pipeline, "sync_today", lambda s, container, now: {"uploaded": 1, "failed": 2, "alreadySynced": 0})
    assert _run().status_code == 502
    endpoint.assert_not_called()


def test_run_surfaces_databricks_errors(endpoint, monkeypatch):
    monkeypatch.setattr(pipeline, "active_run", MagicMock(side_effect=requests.ConnectionError()))
    assert _run().status_code == 502


def test_key_guesses_are_rate_limited_per_ip(endpoint, monkeypatch):
    monkeypatch.setattr(function_app, "pipeline_run_limiter", function_app.RateLimiter(2))
    assert _run(key="a").status_code == 401
    assert _run(key="b").status_code == 401
    assert _run(key=KEY).status_code == 429


def test_status_requires_the_key_and_a_run_id(endpoint, monkeypatch):
    monkeypatch.setattr(pipeline, "run_status", lambda s, run_id: {"runId": run_id, "state": "RUNNING"})
    assert _status(key=None).status_code == 401
    assert _status(run_id="abc").status_code == 400
    resp = _status()
    assert resp.status_code == 200
    assert json.loads(resp.get_body())["runId"] == 42


def test_status_hides_other_jobs_and_surfaces_errors(endpoint, monkeypatch):
    monkeypatch.setattr(pipeline, "run_status", MagicMock(side_effect=LookupError()))
    assert _status().status_code == 404
    monkeypatch.setattr(pipeline, "run_status", MagicMock(side_effect=requests.Timeout()))
    assert _status().status_code == 502


def test_health_reports_whether_the_pipeline_button_is_configured(configured, monkeypatch):
    container = MagicMock()
    container.exists.return_value = True
    monkeypatch.setattr(function_app, "_get_container_client", lambda: container)
    resp = function_app.health(func.HttpRequest(method="GET", url="/api/health", body=b""))
    assert json.loads(resp.get_body())["checks"]["pipelineConfigured"] is True
