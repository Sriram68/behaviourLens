"""Ask BehaviorLens: a plain-English question becomes guarded SQL, runs on the
Databricks SQL warehouse, and comes back as a short answer whose every number
is fact-checked against the query result.

Design rule: numbers are computed in SQL, never by the model. The model only
writes the query and then narrates its result, so factcheck.verify() can hold
the narrative to the rows.
"""

import json
import os
import time

import requests
import sqlglot
from sqlglot import exp

import factcheck

DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CATALOG = os.environ.get("DATABRICKS_CATALOG", "workspace")
SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "default")
MAX_ROWS = 100

# Tables (and their columns) the pipeline produces. Doubles as the prompt's
# schema description and the guardrail's allowlist.
TABLES = {
    "events_enriched": (
        "one row per event: eventName, userId, sessionId, event_timestamp, pageUrl, appVersion, "
        "properties STRUCT<feature, query, results, filter, format, code, endpoint, path, step, "
        "originalEvent, message>, session_event_seq, session_start_ts, session_end_ts, "
        "session_duration_seconds"
    ),
    "session_summary": (
        "one row per session: sessionId, userId, session_start_ts, session_end_ts, "
        "session_duration_seconds, event_count, distinct_event_types"
    ),
    "feature_adoption_daily": "per day and feature event: event_date, eventName, event_count, distinct_users",
    "feature_usage_daily": "per day and product feature: event_date, feature, event_count, distinct_users",
    "error_summary_daily": (
        "per day and error event: event_date, eventName, event_count, distinct_users, distinct_sessions"
    ),
    "error_detail_daily": (
        "per day and error cause: event_date, eventName, code, endpoint, appVersion, event_count, "
        "distinct_users, distinct_sessions"
    ),
}

EVENT_NAMES = (
    "page_view, login_attempt, login_success, onboarding_step_complete (properties.step 1-3), "
    "feature_click (properties.feature), search_query (properties.query, properties.results), "
    "filter_applied, export_triggered (properties.format), api_error (properties.code, "
    "properties.endpoint), page_not_found (properties.path), retry_attempt, js_error (properties.message)"
)

# Functions that could read secrets, files or the network, call other models,
# or reveal identity. Checked against every function node in the parsed query.
BLOCKED_FUNCTIONS = {
    "secret", "try_secret", "list_secrets", "read_files", "http_request", "java_method",
    "reflect", "try_reflect", "current_user", "session_user", "user", "is_member",
    "is_account_group_member", "input_file_name", "vector_search", "url_decode",
}
BLOCKED_PREFIXES = ("ai_", "read_", "http")
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Into)

SQL_PROMPT = """You translate product-analytics questions into a single Databricks SQL query.

Tables (catalog {catalog}, schema {schema}; refer to them by bare name):
{tables}

Event names in events_enriched.eventName: {events}

Rules:
- One SELECT statement only. No DDL/DML. Only the tables above.
- Do all arithmetic in SQL: if the answer needs a percentage, ratio, difference or
  average, compute it as a named, ROUNDed column. The narrator may not compute numbers.
- Return at most {max_rows} rows, ordered meaningfully.
- "This week" means the last 7 days up to the latest event_date/event_timestamp in the data,
  not the wall clock (the data may be historical).

Respond with JSON only: {{"sql": "<query>"}}, or {{"sql": null, "reason": "<why>"}} if the
question cannot be answered from these tables.

Question: {question}"""

ANSWER_PROMPT = """You are BehaviorLens, a product-analytics assistant. Answer the question in
1-3 plain sentences for a non-technical stakeholder, using ONLY the query result below.

Strict rules:
- Every number you write must appear in the result (you may round it to fewer decimals).
- Do not add, subtract, average or otherwise compute new numbers.
- Write dates as YYYY-MM-DD. No preamble, no markdown.
{feedback}
Question: {question}
Columns: {columns}
Rows (JSON): {rows}"""


class UnsafeQuery(Exception):
    def __init__(self, message, sql=None):
        super().__init__(message)
        self.sql = sql


class QueryError(Exception):
    pass


def is_configured():
    return all(
        os.environ.get(key)
        for key in ("GEMINI_API_KEY", "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_WAREHOUSE_ID")
    )


def _function_name(node):
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def guard_sql(sql):
    """Parses model-written SQL and returns a safe, row-limited version of it.

    Raises UnsafeQuery unless it is exactly one read-only SELECT over the
    allowlisted tables with no blocked functions. The returned SQL is
    regenerated from the validated syntax tree, so what runs is what was checked.
    """
    if not sql or not isinstance(sql, str):
        raise UnsafeQuery("No SQL was generated", sql)
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="databricks") if s is not None]
    except sqlglot.errors.ParseError as err:
        raise UnsafeQuery(f"SQL did not parse: {err}", sql) from err
    if len(statements) != 1:
        raise UnsafeQuery("Exactly one statement is allowed", sql)

    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        raise UnsafeQuery("Only SELECT queries are allowed", sql)

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeQuery("Write operations are not allowed", sql)
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_PREFIXES):
                raise UnsafeQuery(f"Function {name}() is not allowed", sql)
        if isinstance(node, exp.Table):
            table = node.name.lower()
            if table in cte_names and not node.db:
                continue
            if table not in TABLES:
                raise UnsafeQuery(f"Table '{node.name or node.sql()}' is not in the allowlist", sql)
            if node.db and node.db.lower() != SCHEMA:
                raise UnsafeQuery(f"Schema '{node.db}' is not allowed", sql)
            if node.catalog and node.catalog.lower() != CATALOG:
                raise UnsafeQuery(f"Catalog '{node.catalog}' is not allowed", sql)

    if isinstance(root, exp.Select):
        limit = root.args.get("limit")
        current = limit.expression if limit is not None else None
        if not (isinstance(current, exp.Literal) and current.is_int and int(current.this) <= MAX_ROWS):
            root = root.limit(MAX_ROWS)
    else:
        root = exp.select("*").from_(root.subquery("q")).limit(MAX_ROWS)
    return root.sql(dialect="databricks")


def call_gemini(prompt, json_mode=False, temperature=0.2):
    config = {"temperature": temperature}
    if json_mode:
        config["responseMimeType"] = "application/json"
    resp = requests.post(
        GEMINI_URL.format(model=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)),
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
        timeout=60,
    )
    resp.raise_for_status()
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts if not part.get("thought")).strip()


def run_sql(sql):
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    if not host.startswith("http"):
        host = "https://" + host
    headers = {"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}"}
    resp = requests.post(
        f"{host}/api/2.0/sql/statements",
        headers=headers,
        json={
            "warehouse_id": os.environ["DATABRICKS_WAREHOUSE_ID"],
            "statement": sql,
            "catalog": CATALOG,
            "schema": SCHEMA,
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
            "row_limit": MAX_ROWS,
        },
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    # A cold serverless warehouse can take a little while to start.
    deadline = time.monotonic() + 90
    while data["status"]["state"] in ("PENDING", "RUNNING") and time.monotonic() < deadline:
        time.sleep(2)
        poll = requests.get(f"{host}/api/2.0/sql/statements/{data['statement_id']}", headers=headers, timeout=30)
        poll.raise_for_status()
        data = poll.json()

    state = data["status"]["state"]
    if state != "SUCCEEDED":
        raise QueryError(data["status"].get("error", {}).get("message", f"Query ended in state {state}"))
    columns = [col["name"] for col in data["manifest"]["schema"]["columns"]]
    rows = (data.get("result") or {}).get("data_array") or []
    return columns, rows


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.index("{"):]
    return json.loads(text)


def answer_question(question):
    tables = "\n".join(f"- {name}: {desc}" for name, desc in TABLES.items())
    plan = _parse_json(
        call_gemini(
            SQL_PROMPT.format(
                catalog=CATALOG, schema=SCHEMA, tables=tables, events=EVENT_NAMES,
                max_rows=MAX_ROWS, question=question,
            ),
            json_mode=True,
            temperature=0,
        )
    )
    if not plan.get("sql"):
        return {
            "question": question,
            "sql": None,
            "columns": [],
            "rows": [],
            "answer": plan.get("reason") or "That question can't be answered from the BehaviorLens tables.",
            "factCheck": {"total": 0, "verified": 0, "unverified": []},
        }

    safe_sql = guard_sql(plan["sql"])
    columns, rows = run_sql(safe_sql)

    question_numbers = [float(n.replace(",", "")) for n in factcheck.extract_claims(question)[0]]
    facts = {"rows": rows, "row_count": len(rows), "question_numbers": question_numbers}
    feedback = ""
    for _ in range(2):
        answer = call_gemini(
            ANSWER_PROMPT.format(
                feedback=feedback, question=question, columns=json.dumps(columns), rows=json.dumps(rows[:50])
            )
        )
        check = factcheck.verify(answer, facts)
        if not check["unverified"]:
            break
        # One self-correction pass: tell the model exactly which numbers didn't check out.
        feedback = (
            f"\nYour previous answer used numbers not present in the result: {', '.join(check['unverified'])}. "
            "Rewrite it using only numbers from the result.\n"
        )

    return {
        "question": question,
        "sql": safe_sql,
        "columns": columns,
        "rows": rows,
        "answer": answer,
        "factCheck": check,
    }
