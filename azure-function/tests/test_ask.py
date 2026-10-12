import json
from unittest.mock import MagicMock, patch

import pytest

import ask


@pytest.fixture
def configured(monkeypatch):
    for key, value in {
        "GEMINI_API_KEY": "test-key",
        "DATABRICKS_HOST": "dbc-test.cloud.databricks.com",
        "DATABRICKS_TOKEN": "dapi-test",
        "DATABRICKS_WAREHOUSE_ID": "wh1",
    }.items():
        monkeypatch.setenv(key, value)


# --- SQL guardrail -------------------------------------------------------------


def test_allows_a_simple_select_and_adds_a_row_limit():
    sql = ask.guard_sql("SELECT feature, SUM(event_count) AS uses FROM feature_usage_daily GROUP BY feature")
    assert sql.endswith("LIMIT 100")


def test_keeps_a_smaller_limit_and_caps_a_larger_one():
    assert ask.guard_sql("SELECT * FROM session_summary LIMIT 5").endswith("LIMIT 5")
    assert ask.guard_sql("SELECT * FROM session_summary LIMIT 100000").endswith("LIMIT 100")


def test_allows_qualified_names_ctes_and_window_functions():
    sql = """
        WITH weekly AS (
          SELECT date_trunc('WEEK', event_date) AS wk, SUM(event_count) AS errors
          FROM workspace.default.error_summary_daily GROUP BY 1
        )
        SELECT wk, errors, LAG(errors) OVER (ORDER BY wk) AS prev FROM weekly ORDER BY wk DESC
    """
    assert "LIMIT 100" in ask.guard_sql(sql)


def test_wraps_set_operations_to_enforce_the_limit():
    sql = ask.guard_sql("SELECT eventName FROM error_summary_daily UNION SELECT eventName FROM feature_adoption_daily")
    assert sql.startswith("SELECT * FROM (")
    assert sql.endswith("LIMIT 100")


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("DROP TABLE events_enriched", "Only SELECT"),
        ("DELETE FROM session_summary", "Only SELECT"),
        ("SELECT 1; DROP TABLE events_enriched", "Exactly one"),
        ("SELECT * FROM events_raw", "allowlist"),
        ("SELECT * FROM system.access.audit", "allowlist"),
        ("SELECT * FROM other.default.session_summary", "Catalog"),
        ("SELECT * FROM workspace.secret_stuff.session_summary", "Schema"),
        ("SELECT secret('behaviorlens', 'gemini_api_key')", "secret"),
        ("SELECT ai_query('model', 'hi') FROM session_summary", "ai_query"),
        ("SELECT * FROM read_files('/Volumes/workspace/default/raw_events')", "(?i)read_files"),
        ("SELECT current_user()", "current_user"),
        ("SELECT * FROM (SELECT * FROM qa_data_quality_results) t", "allowlist"),
        ("", "No SQL"),
        ("SELEC nonsense FROM", "parse"),
    ],
)
def test_blocks_unsafe_queries(sql, reason):
    with pytest.raises(ask.UnsafeQuery, match=reason):
        ask.guard_sql(sql)


def test_cte_name_cannot_shadow_a_qualified_table():
    with pytest.raises(ask.UnsafeQuery):
        ask.guard_sql("WITH x AS (SELECT 1 AS a) SELECT * FROM other_schema.x")


# --- Gemini and Databricks calls --------------------------------------------------


@patch("ask.requests.post")
def test_call_gemini_sends_key_in_header_and_skips_thought_parts(mock_post, configured):
    mock_post.return_value.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": " answer "}]}}]
    }
    assert ask.call_gemini("hello", json_mode=True) == "answer"
    _, kwargs = mock_post.call_args
    assert kwargs["headers"] == {"x-goog-api-key": "test-key"}
    assert kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "key=" not in mock_post.call_args.args[0]


def _statement(state, statement_id="st1", error=None):
    data = {"statement_id": statement_id, "status": {"state": state}}
    if error:
        data["status"]["error"] = {"message": error}
    if state == "SUCCEEDED":
        data["manifest"] = {"schema": {"columns": [{"name": "feature"}, {"name": "uses"}]}}
        data["result"] = {"data_array": [["dashboard", "1204"]]}
    return data


@patch("ask.time.sleep")
@patch("ask.requests.get")
@patch("ask.requests.post")
def test_run_sql_polls_until_the_warehouse_finishes(mock_post, mock_get, _sleep, configured):
    mock_post.return_value.json.return_value = _statement("PENDING")
    mock_get.return_value.json.return_value = _statement("SUCCEEDED")
    columns, rows = ask.run_sql("SELECT 1")

    assert columns == ["feature", "uses"]
    assert rows == [["dashboard", "1204"]]
    assert mock_post.call_args.args[0] == "https://dbc-test.cloud.databricks.com/api/2.0/sql/statements"
    assert mock_post.call_args.kwargs["json"]["row_limit"] == ask.MAX_ROWS
    mock_get.assert_called_once()


@patch("ask.requests.post")
def test_run_sql_raises_on_failed_statements(mock_post, configured):
    mock_post.return_value.json.return_value = _statement("FAILED", error="TABLE_OR_VIEW_NOT_FOUND")
    with pytest.raises(ask.QueryError, match="TABLE_OR_VIEW_NOT_FOUND"):
        ask.run_sql("SELECT 1")


def test_is_configured_requires_every_setting(configured, monkeypatch):
    assert ask.is_configured()
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID")
    assert not ask.is_configured()


# --- End to end (model + warehouse mocked) -----------------------------------------


def test_answer_question_runs_guarded_sql_and_fact_checks_the_answer(monkeypatch):
    prompts = []

    def fake_gemini(prompt, json_mode=False, temperature=0.2):
        prompts.append(prompt)
        if json_mode:
            return "```json\n" + json.dumps({"sql": "SELECT feature, uses FROM feature_usage_daily"}) + "\n```"
        if len(prompts) == 2:
            return "Scheduled reports were used only 40 times."  # wrong: not in the rows
        return "Scheduled reports were used only 37 times, versus 1,204 for the dashboard."

    executed = []
    monkeypatch.setattr(ask, "call_gemini", fake_gemini)
    monkeypatch.setattr(
        ask,
        "run_sql",
        lambda sql: executed.append(sql) or (["feature", "uses"], [["dashboard", "1204"], ["scheduled_reports", "37"]]),
    )

    result = ask.answer_question("Which feature is least used?")

    assert executed == ["SELECT feature, uses FROM feature_usage_daily LIMIT 100"]
    assert result["factCheck"] == {"total": 2, "verified": 2, "unverified": []}
    assert "37 times" in result["answer"]
    assert "numbers not present in the result: 40" in prompts[2]


def test_answer_question_explains_when_the_question_is_out_of_scope(monkeypatch):
    monkeypatch.setattr(
        ask, "call_gemini", lambda *a, **k: json.dumps({"sql": None, "reason": "No revenue data is tracked."})
    )
    result = ask.answer_question("What was revenue last quarter?")
    assert result["sql"] is None
    assert result["answer"] == "No revenue data is tracked."


def test_answer_question_refuses_unsafe_generated_sql(monkeypatch):
    monkeypatch.setattr(ask, "call_gemini", lambda *a, **k: json.dumps({"sql": "DROP TABLE session_summary"}))
    monkeypatch.setattr(ask, "run_sql", MagicMock())
    with pytest.raises(ask.UnsafeQuery):
        ask.answer_question("Please clean up the sessions table")
    ask.run_sql.assert_not_called()


def test_read_only_grants_cover_exactly_the_allowlisted_tables():
    """databricks/setup_ask_access.py grants the Ask identity SELECT on its own
    table list; it must match the guardrail's allowlist, no more and no less."""
    import importlib.util
    import sys
    from pathlib import Path

    databricks_dir = Path(__file__).resolve().parents[2] / "databricks"
    sys.path.insert(0, str(databricks_dir))
    try:
        spec = importlib.util.spec_from_file_location("setup_ask_access", databricks_dir / "setup_ask_access.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(databricks_dir))
    assert sorted(module.TABLES) == sorted(ask.TABLES)
