"""Incrementally syncs NDJSON event files from the `raw-events` Azure Blob
container into the Unity Catalog Volume that 01_ingestion.py reads from.

Needed because Databricks Free Edition runs on Databricks-managed infra, not
inside the Azure subscription, so there's no direct Blob mount (see
docs/architecture.md). Blobs are write-once, so only names missing from the
Volume are uploaded: a re-run after a demo takes seconds, not minutes.

  python databricks/sync_raw_events.py --since 2026/10/12   # only blobs from that date on
  python databricks/sync_raw_events.py --dry-run
  python databricks/sync_raw_events.py --reset --since 2026/10/12
      # empty the Volume first (e.g. after regenerating the demo history).
      # Safe: Blob Storage stays the source of truth.

Credentials: see bl_client.py.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from azure.storage.blob import BlobServiceClient

import bl_client


def existing_files(ws, prefixes):
    """Relative names (YYYY/MM/DD/uuid.ndjson) already in the Volume."""
    found = set()
    for prefix in prefixes:
        page_token = None
        while True:
            params = {"page_token": page_token} if page_token else {}
            resp = ws.request("GET", f"/api/2.0/fs/directories{bl_client.VOLUME_PATH}/{prefix}", params=params)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
            for entry in data.get("contents", []):
                if not entry.get("is_directory"):
                    found.add(f"{prefix}/{entry['name']}")
            page_token = data.get("next_page_token")
            if not page_token:
                break
    return found


def clear_volume(ws, workers=8):
    """Deletes every file and folder in the Volume (not the Volume itself)."""
    files, folders = [], []

    def walk(path):
        page_token = None
        while True:
            params = {"page_token": page_token} if page_token else {}
            resp = ws.request("GET", f"/api/2.0/fs/directories{path}", params=params)
            if resp.status_code == 404:
                return
            resp.raise_for_status()
            data = resp.json()
            for entry in data.get("contents", []):
                if entry.get("is_directory"):
                    folders.append(entry["path"])
                    walk(entry["path"])
                else:
                    files.append(entry["path"])
            page_token = data.get("next_page_token")
            if not page_token:
                return

    walk(bl_client.VOLUME_PATH)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda f: ws.request("DELETE", f"/api/2.0/fs/files{f}"), files))
    for folder in sorted(folders, key=len, reverse=True):
        ws.request("DELETE", f"/api/2.0/fs/directories{folder}")
    print(f"Cleared {len(files)} files from {bl_client.VOLUME_PATH}")


def sync(since, dry_run=False, workers=8, reset=False):
    ws = bl_client.Workspace()
    if reset and not dry_run:
        clear_volume(ws, workers)
    container = BlobServiceClient.from_connection_string(
        bl_client.storage_connection_string()
    ).get_container_client(bl_client.CONTAINER)

    blobs = [b.name for b in container.list_blobs() if b.name.endswith(".ndjson") and b.name[:10] >= since]
    prefixes = sorted({name[:10] for name in blobs})
    todo = sorted(set(blobs) - existing_files(ws, prefixes))
    print(f"{len(blobs)} blobs since {since}; {len(blobs) - len(todo)} already in {bl_client.VOLUME_PATH}; {len(todo)} to upload")
    if dry_run or not todo:
        return len(todo), 0

    def upload(name):
        data = container.download_blob(name).readall()
        resp = ws.request(
            "PUT", f"/api/2.0/fs/files{bl_client.VOLUME_PATH}/{name}", params={"overwrite": "false"}, data=data
        )
        return name, resp.status_code in (200, 204, 409)  # 409: uploaded by a concurrent run

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (name, ok) in enumerate(pool.map(upload, todo), start=1):
            if not ok:
                failed += 1
                print(f"FAILED {name}")
            if i % 100 == 0:
                print(f"  uploaded {i}/{len(todo)}")
    print(f"Synced {len(todo) - failed} blobs ({failed} failed).")
    return len(todo), failed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2000/01/01", help="only blobs written on/after this date (YYYY/MM/DD)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true", help="empty the Volume before syncing")
    args = parser.parse_args()
    _, failed = sync(args.since, args.dry_run, reset=args.reset)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
