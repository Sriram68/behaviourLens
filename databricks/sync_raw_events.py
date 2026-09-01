"""Syncs NDJSON event files from the `raw-events` Azure Blob container into the
Databricks Unity Catalog Volume that 01_ingestion.py reads from
(/Volumes/workspace/default/raw_events by default). Needed because Databricks
Free Edition runs on Databricks-managed infra, not inside the Azure
subscription, so there's no direct Blob mount — see docs/architecture.md.

Requires three env vars (never pass these as CLI args or print them):
  AZURE_STORAGE_CONNECTION_STRING  - connection string for the storage account
  DATABRICKS_HOST                  - e.g. https://<workspace>.cloud.databricks.com
  DATABRICKS_TOKEN                 - a personal access token (User Settings > Developer)
"""

import os
import sys

import requests
from azure.storage.blob import BlobServiceClient

CONTAINER_NAME = os.environ.get("RAW_EVENTS_CONTAINER", "raw-events")
VOLUME_PATH = os.environ.get("DATABRICKS_VOLUME_PATH", "/Volumes/workspace/default/raw_events")


def main():
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    if not all([conn_str, host, token]):
        sys.exit(
            "Missing required env vars: AZURE_STORAGE_CONNECTION_STRING, "
            "DATABRICKS_HOST, DATABRICKS_TOKEN"
        )
    host = host.rstrip("/")

    blob_service = BlobServiceClient.from_connection_string(conn_str)
    container = blob_service.get_container_client(CONTAINER_NAME)

    synced = 0
    skipped = 0
    failed = 0
    for blob in container.list_blobs():
        dest_path = f"{VOLUME_PATH}/{blob.name}"
        url = f"{host}/api/2.0/fs/files{dest_path}"

        data = container.download_blob(blob.name).readall()
        resp = requests.put(
            url,
            params={"overwrite": "true"},
            headers={"Authorization": f"Bearer {token}"},
            data=data,
            timeout=30,
        )
        if resp.status_code in (200, 204):
            synced += 1
        else:
            failed += 1
            print(f"FAILED {blob.name}: {resp.status_code} {resp.text[:200]}")

        if (synced + failed) % 50 == 0:
            print(f"progress: {synced} synced, {failed} failed")

    print(f"Done. Synced {synced} blobs to {VOLUME_PATH} ({failed} failed).")


if __name__ == "__main__":
    main()
