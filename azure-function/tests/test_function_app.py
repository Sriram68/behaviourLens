import json
from unittest.mock import MagicMock, patch

import azure.functions as func

import function_app


def make_request(body):
    return func.HttpRequest(
        method="POST",
        url="/api/events",
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


SAMPLE_EVENT = {
    "eventName": "login_attempt",
    "userId": "u1",
    "sessionId": "s1",
    "timestamp": "2026-07-13T00:00:00Z",
    "pageUrl": "/login",
    "properties": {},
}


@patch("function_app._get_container_client")
def test_ingest_valid_batch_returns_200_and_writes_blob(mock_get_container):
    mock_container = MagicMock()
    mock_get_container.return_value = mock_container

    events = [SAMPLE_EVENT, {**SAMPLE_EVENT, "eventName": "login_success"}]
    resp = function_app.ingest_events(make_request({"events": events}))

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    assert body["status"] == "ok"
    assert body["count"] == 2

    mock_container.upload_blob.assert_called_once()
    _, kwargs = mock_container.upload_blob.call_args
    written_lines = kwargs["data"].split("\n")
    assert len(written_lines) == 2
    assert json.loads(written_lines[0])["eventName"] == "login_attempt"
    assert kwargs["overwrite"] is False


@patch("function_app._get_container_client")
def test_ingest_accepts_bare_list_payload(mock_get_container):
    mock_get_container.return_value = MagicMock()
    resp = function_app.ingest_events(make_request([SAMPLE_EVENT]))
    assert resp.status_code == 200


def test_ingest_rejects_invalid_json():
    req = func.HttpRequest(method="POST", url="/api/events", body=b"not-json", headers={})
    resp = function_app.ingest_events(req)
    assert resp.status_code == 400


def test_ingest_rejects_empty_events_list():
    resp = function_app.ingest_events(make_request({"events": []}))
    assert resp.status_code == 400


def test_ingest_rejects_missing_events_key():
    resp = function_app.ingest_events(make_request({}))
    assert resp.status_code == 400


@patch("function_app._get_container_client")
def test_blob_name_is_date_partitioned(mock_get_container):
    mock_get_container.return_value = MagicMock()
    resp = function_app.ingest_events(make_request({"events": [SAMPLE_EVENT]}))
    blob_name = json.loads(resp.get_body())["blob"]
    assert blob_name.count("/") == 3  # YYYY/MM/DD/<uuid>.ndjson
    assert blob_name.endswith(".ndjson")
