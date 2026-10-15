"""Gives the Azure Function what "Ask BehaviorLens" needs, with least privilege.

1. A Databricks service principal, `behaviorlens-ask`, that can only SELECT the
   six analytics tables the Ask guardrail allows and use the SQL warehouse. Even
   if a generated query slipped past the guardrail, this identity can't read
   anything else or write anything.
2. A token for it (on-behalf-of, 60 days).
3. The Function's app settings: that token, the warehouse, the Gemini key and,
   if present, the Teams webhook (read from the `behaviorlens` secret scope, never
   printed), written through a temporary file so no secret appears in the command
   line or console.

  python databricks/setup_ask_access.py
  python databricks/setup_ask_access.py --user-token   # fallback: your own PAT

Credentials: see bl_client.py, plus `az login` for the Function's subscription.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

import bl_client

SP_NAME = "behaviorlens-ask"
TABLES = [
    "events_enriched", "session_summary", "feature_adoption_daily",
    "feature_usage_daily", "error_summary_daily", "error_detail_daily",
]
# Read by the "Send report to Teams" endpoint, not reachable from Ask's SQL guardrail.
EXTRA_READ_TABLES = ["ai_insights_weekly"]
TOKEN_DAYS = 60
FUNCTION_APP = os.environ.get("BEHAVIORLENS_FUNCTION_APP", "func-behaviorlens-psiddhi")
RESOURCE_GROUP = os.environ.get("BEHAVIORLENS_RESOURCE_GROUP", "rg-pSiddhi3.0-2026-01-sem3-Sriram")
SUBSCRIPTION = os.environ.get("BEHAVIORLENS_SUBSCRIPTION", "pSiddhi")


def service_principal(ws):
    found = ws.call(
        "GET", "/api/2.0/preview/scim/v2/ServicePrincipals", params={"filter": f'displayName eq "{SP_NAME}"'}
    ).get("Resources", [])
    if found:
        return found[0]["applicationId"]
    created = ws.call(
        "POST",
        "/api/2.0/preview/scim/v2/ServicePrincipals",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"], "displayName": SP_NAME, "active": True},
    )
    print(f"Created service principal {SP_NAME}")
    return created["applicationId"]


def grant_read_only(ws, app_id):
    principal = f"`{app_id}`"
    ws.sql(f"GRANT USE CATALOG ON CATALOG {bl_client.CATALOG} TO {principal}")
    ws.sql(f"GRANT USE SCHEMA ON SCHEMA {bl_client.CATALOG}.{bl_client.SCHEMA} TO {principal}")
    for table in TABLES + EXTRA_READ_TABLES:
        ws.sql(f"GRANT SELECT ON TABLE {bl_client.CATALOG}.{bl_client.SCHEMA}.{table} TO {principal}")
    acl = {"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_USE"}]}
    ws.call("PATCH", f"/api/2.0/permissions/sql/warehouses/{bl_client.WAREHOUSE_ID}", json=acl)
    ws.call("PATCH", "/api/2.0/permissions/authorization/tokens", json=acl)
    print(f"Granted {SP_NAME}: SELECT on {len(TABLES + EXTRA_READ_TABLES)} tables, CAN_USE on warehouse")


def service_principal_token(ws, app_id):
    data = ws.call(
        "POST",
        "/api/2.0/token-management/on-behalf-of/tokens",
        json={"application_id": app_id, "lifetime_seconds": TOKEN_DAYS * 86400, "comment": "BehaviorLens Ask (read-only)"},
    )
    return data["token_value"]


def user_token(ws):
    data = ws.call(
        "POST", "/api/2.0/token/create",
        json={"lifetime_seconds": TOKEN_DAYS * 86400, "comment": "BehaviorLens Ask (user fallback)"},
    )
    return data["token_value"]


def secret(ws, key):
    resp = ws.request("GET", "/api/2.0/secrets/get", params={"scope": "behaviorlens", "key": key})
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return base64.b64decode(resp.json()["value"]).decode("utf-8")


def set_function_settings(settings):
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise SystemExit("Azure CLI not found")
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump([{"name": k, "value": v, "slotSetting": False} for k, v in settings.items()], fh)
        subprocess.run(
            [az, "functionapp", "config", "appsettings", "set", "--name", FUNCTION_APP,
             "--resource-group", RESOURCE_GROUP, "--subscription", SUBSCRIPTION,
             "--settings", f"@{path}", "-o", "none", "--only-show-errors"],
            check=True,
        )
    finally:
        os.remove(path)
    print(f"Set {', '.join(settings)} on {FUNCTION_APP}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-token", action="store_true", help="use your own PAT instead of a service principal")
    args = parser.parse_args()

    ws = bl_client.Workspace()
    if args.user_token:
        token = user_token(ws)
        print("WARNING: using your own PAT; Ask runs with your full permissions (the SQL guardrail still applies).")
    else:
        app_id = service_principal(ws)
        grant_read_only(ws, app_id)
        token = service_principal_token(ws, app_id)

    settings = {
        "GEMINI_API_KEY": secret(ws, "gemini_api_key"),
        "DATABRICKS_HOST": bl_client.host(),
        "DATABRICKS_TOKEN": token,
        "DATABRICKS_WAREHOUSE_ID": bl_client.WAREHOUSE_ID,
    }
    teams = secret(ws, "teams_webhook_url")
    if teams:  # powers the "Send report to Teams" button on BehaviorLens Live
        settings["TEAMS_WEBHOOK_URL"] = teams
    set_function_settings(settings)


if __name__ == "__main__":
    sys.exit(main())
