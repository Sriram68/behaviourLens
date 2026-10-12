from pathlib import Path

import factcheck

FACTS = {
    "export_failure_rate_this_week": 0.4167,
    "export_failures_this_week": 1342,
    "errors_vs_last_week_ratio": 9.3,
    "incident_start": "2026-10-07",
    "release": "2.4.0",
    "onboarding": [{"step": 2, "users": 410}, {"step": 3, "users": 226}],
}


def test_faithful_narrative_fully_verifies():
    text = (
        "Since release v2.4.0 on 2026-10-07, 1,342 exports failed (41.7% of attempts) and errors "
        "are running at 9.3x last week. Only 226 users finished step 3, down from 410 at step 2."
    )
    result = factcheck.verify(text, FACTS)
    assert result == {"total": 9, "verified": 9, "unverified": []}


def test_ratio_may_be_written_as_a_rounded_percentage():
    assert factcheck.verify("42% of exports failed", FACTS)["unverified"] == []


def test_invented_numbers_dates_and_versions_are_flagged():
    text = "1,500 exports failed after v2.5.0 shipped on 2026-10-08, a 12x jump."
    result = factcheck.verify(text, FACTS)
    assert result["total"] == 4
    assert result["verified"] == 0
    assert set(result["unverified"]) == {"1,500", "v2.5.0", "2026-10-08", "12"}


def test_rounding_must_match_the_precision_written():
    assert factcheck.verify("41.7%", FACTS)["unverified"] == []
    assert factcheck.verify("41.9%", FACTS)["unverified"] == ["41.9"]


def test_numbers_inside_words_are_not_claims():
    assert factcheck.verify("Q3 roadmap for p95 latency", FACTS)["total"] == 0


def test_numeric_strings_from_sql_results_count_as_numbers():
    rows = [["scheduled_reports", "37"], ["dashboard", "1204"]]
    result = factcheck.verify("Scheduled reports were used 37 times versus 1,204 for dashboards.", {"rows": rows})
    assert result["unverified"] == []


def test_booleans_are_not_treated_as_numbers():
    assert factcheck.verify("1 incident", {"flag": True})["unverified"] == ["1"]


def test_text_without_claims_has_nothing_to_verify():
    assert factcheck.verify("Exports are failing more often than usual.", FACTS) == {
        "total": 0,
        "verified": 0,
        "unverified": [],
    }


def test_databricks_copy_is_identical():
    """The AI insights notebook imports its own copy; keep the two in lockstep."""
    here = Path(__file__).resolve().parents[1] / "factcheck.py"
    notebook_copy = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "factcheck.py"
    assert notebook_copy.read_bytes() == here.read_bytes()
