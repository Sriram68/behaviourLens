"""Resilient Gemini calls: retry transient failures, then fall back to the next model.

Free-tier Gemini returns 503 ("model overloaded") or 429 at busy times. A demo
can't depend on one model being up, so each call retries with exponential
backoff (honouring Retry-After) and, if a model stays unavailable, moves on to
the next in MODELS. The caller learns which model actually answered.

Shared by the Azure Function (/api/ask) and the Databricks AI insights
notebook. databricks/notebooks/gemini.py must stay byte-identical to this file;
tests/test_gemini.py enforces that.
"""

import time

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Primary first. The aliases track Google's current stable Flash models.
MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
RETRYABLE = {429, 500, 502, 503, 504}


class GeminiUnavailable(Exception):
    """Every model failed with a transient error."""


def _text(response_json):
    parts = response_json["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts if not part.get("thought")).strip()


def generate(prompt, api_key, temperature=0.3, json_mode=False, models=None, attempts_per_model=3,
             timeout=90, sleep=time.sleep, thinking_level=None):
    """Returns (text, model_used). Raises requests.HTTPError for non-transient
    errors (bad request, bad key) and GeminiUnavailable if every model is down.

    thinking_level ("low", "high") trades reasoning for latency; it is sent only
    to explicitly versioned Gemini 3 models, which accept it."""
    failures = []
    for model in models or MODELS:
        config = {"temperature": temperature}
        if json_mode:
            config["responseMimeType"] = "application/json"
        if thinking_level and model.startswith("gemini-3"):
            config["thinkingConfig"] = {"thinkingLevel": thinking_level}
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
        for attempt in range(attempts_per_model):
            try:
                resp = requests.post(URL.format(model=model), headers={"x-goog-api-key": api_key}, json=body, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as err:
                failures.append(f"{model}: {type(err).__name__}")
                status = None
            else:
                if resp.status_code < 400:
                    return _text(resp.json()), model
                if resp.status_code not in RETRYABLE:
                    resp.raise_for_status()
                failures.append(f"{model}: HTTP {resp.status_code}")
                status = resp.status_code
            if attempt + 1 < attempts_per_model:
                retry_after = resp.headers.get("Retry-After") if status else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1)
                sleep(min(delay, 30))
    raise GeminiUnavailable("; ".join(failures))
