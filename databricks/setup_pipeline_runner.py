"""Lets BehaviorLens Live start the pipeline, with least privilege.

1. A second Databricks service principal, `behaviorlens-runner`, that can only
   write the raw-events Volume and run the "BehaviorLens Pipeline" Job. It is
   separate from `behaviorlens-ask`, so Ask's identity stays read-only.
2. A token for it (on-behalf-of, 60 days).
3. A presenter key: the Live page is public, so starting a run needs it. It is
   stored in the `behaviorlens` secret scope (`pipeline_run_key`) and printed once.
4. The Function's app settings for both (see setup_ask_access.set_function_settings).

  python databricks/setup_pipeline_runner.py
  python databricks/setup_pipeline_runner.py --new-key   # rotate the presenter key

Credentials: see bl_client.py, plus `az login` for the Function's subscription.
"""

import argparse
import secrets
import sys

import bl_client
from setup_ask_access import secret, service_principal, service_principal_token, set_function_settings

SP_NAME = "behaviorlens-runner"
SECRET_KEY = "pipeline_run_key"


def grant_runner(ws, app_id, job_id):
    principal = f"`{app_id}`"
    ws.sql(f"GRANT USE CATALOG ON CATALOG {bl_client.CATALOG} TO {principal}")
    ws.sql(f"GRANT USE SCHEMA ON SCHEMA {bl_client.CATALOG}.{bl_client.SCHEMA} TO {principal}")
    ws.sql(f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {bl_client.CATALOG}.{bl_client.SCHEMA}.{bl_client.VOLUME} TO {principal}")
    ws.call(
        "PATCH", f"/api/2.0/permissions/jobs/{job_id}",
        json={"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_MANAGE_RUN"}]},
    )
    ws.call(
        "PATCH", "/api/2.0/permissions/authorization/tokens",
        json={"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_USE"}]},
    )
    print(f"Granted {SP_NAME}: read/write on Volume {bl_client.VOLUME}, CAN_MANAGE_RUN on job {job_id}")


def presenter_key(ws, rotate):
    key = None if rotate else secret(ws, SECRET_KEY)
    if not key:
        key = f"lens-{secrets.token_hex(4)}"  # short enough to type on stage; the endpoint is rate-limited
        ws.call("POST", "/api/2.0/secrets/put", json={"scope": "behaviorlens", "key": SECRET_KEY, "string_value": key})
        print(f"New presenter key (also in secret behaviorlens/{SECRET_KEY}): {key}")
    else:
        print(f"Presenter key unchanged (secret behaviorlens/{SECRET_KEY})")
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--new-key", action="store_true", help="rotate the presenter key")
    args = parser.parse_args()

    ws = bl_client.Workspace()
    job_id = ws.find_job()["job_id"]
    app_id = service_principal(ws, SP_NAME)
    grant_runner(ws, app_id, job_id)
    token = service_principal_token(ws, app_id, comment="BehaviorLens Live: run the pipeline")
    set_function_settings({
        "DATABRICKS_HOST": bl_client.host(),
        "RUNNER_DATABRICKS_TOKEN": token,
        "PIPELINE_JOB_ID": str(job_id),
        "PIPELINE_RUN_KEY": presenter_key(ws, args.new_key),
    })


if __name__ == "__main__":
    sys.exit(main())
