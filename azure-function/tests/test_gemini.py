from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import gemini


def _resp(status, text="ok", headers=None, thought=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": f" {text} "}]
    resp.json.return_value = {"candidates": [{"content": {"parts": parts}}]}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return resp


@patch("gemini.requests.post")
def test_returns_text_and_model_skipping_thought_parts(mock_post):
    mock_post.return_value = _resp(200, "answer", thought="thinking...")
    text, model = gemini.generate("hi", "key", json_mode=True, sleep=lambda s: None)
    assert (text, model) == ("answer", gemini.MODELS[0])
    _, kwargs = mock_post.call_args
    assert kwargs["headers"] == {"x-goog-api-key": "key"}
    assert kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


@patch("gemini.requests.post")
def test_retries_transient_errors_with_backoff(mock_post):
    mock_post.side_effect = [_resp(503), _resp(429, headers={"Retry-After": "5"}), _resp(200, "fine")]
    waits = []
    text, model = gemini.generate("hi", "key", sleep=waits.append)
    assert (text, model) == ("fine", gemini.MODELS[0])
    assert waits == [2, 5.0]  # exponential first, then the server's Retry-After


@patch("gemini.requests.post")
def test_falls_back_to_the_next_model_when_one_stays_down(mock_post):
    mock_post.side_effect = [_resp(503), _resp(503), requests.ConnectionError(), _resp(200, "fallback")]
    text, model = gemini.generate("hi", "key", models=["primary", "backup"], attempts_per_model=2, sleep=lambda s: None)
    assert (text, model) == ("fallback", "backup")
    urls = [call.args[0] for call in mock_post.call_args_list]
    assert ["primary" in u for u in urls] == [True, True, False, False]


@patch("gemini.requests.post")
def test_raises_when_every_model_is_unavailable(mock_post):
    mock_post.return_value = _resp(503)
    with pytest.raises(gemini.GeminiUnavailable, match="primary: HTTP 503; primary: HTTP 503; backup: HTTP 503"):
        gemini.generate("hi", "key", models=["primary", "backup"], attempts_per_model=2, sleep=lambda s: None)


@patch("gemini.requests.post")
def test_does_not_retry_permanent_errors(mock_post):
    mock_post.return_value = _resp(400)
    with pytest.raises(requests.HTTPError):
        gemini.generate("hi", "key", sleep=lambda s: None)
    assert mock_post.call_count == 1


def test_databricks_copy_is_identical():
    here = Path(__file__).resolve().parents[1] / "gemini.py"
    notebook_copy = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "gemini.py"
    assert notebook_copy.read_bytes() == here.read_bytes()
