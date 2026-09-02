import datetime
import json
import logging
import os
import uuid

import azure.functions as func
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _get_container_client():
    conn_str = os.environ["AzureWebJobsStorage"]
    container_name = os.environ.get("RAW_EVENTS_CONTAINER", "raw-events")
    blob_service = BlobServiceClient.from_connection_string(conn_str)
    return blob_service.get_container_client(container_name)


def ingest_events(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    events = body if isinstance(body, list) else body.get("events")
    if not events:
        return func.HttpResponse("No events provided", status_code=400)

    ndjson = "\n".join(json.dumps(e) for e in events)
    blob_name = f"{datetime.datetime.utcnow():%Y/%m/%d}/{uuid.uuid4()}.ndjson"

    container_client = _get_container_client()
    container_client.upload_blob(name=blob_name, data=ndjson, overwrite=False)

    logging.info("Wrote %d events to %s", len(events), blob_name)
    return func.HttpResponse(
        json.dumps({"status": "ok", "count": len(events), "blob": blob_name}),
        status_code=200,
        mimetype="application/json",
    )


app.route(route="events", methods=["POST"])(ingest_events)
