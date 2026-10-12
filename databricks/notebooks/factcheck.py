"""Deterministic fact-checking for AI-generated text.

Every number, ISO date and version string in a narrative must trace back to a
value in the source data the model was given. This replaces "ask the model to
grade itself" with a check that cannot be talked into a pass.

This file is used both by the Azure Function (/api/ask) and by the Databricks
AI insights notebook. databricks/notebooks/factcheck.py must stay byte-identical
to this file; tests/test_factcheck.py enforces that.
"""

import re

ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
VERSION_RE = re.compile(r"\bv?\d+\.\d+\.\d+\b")
# A trailing "x" is allowed so ratios like "9x more errors" are checked too.
NUMBER_RE = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?=x\b|\W|$)")
NUMERIC_STRING_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _collect(value, numbers, strings):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        numbers.append(float(value))
    elif isinstance(value, str):
        strings.add(value)
        # SQL APIs often return numbers as strings ("1342").
        if NUMERIC_STRING_RE.match(value.strip()):
            numbers.append(float(value))
        for match in ISO_DATE_RE.findall(value):
            strings.add(match)
    elif isinstance(value, dict):
        for item in value.values():
            _collect(item, numbers, strings)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, numbers, strings)


def _decimals(token):
    return len(token.split(".")[1]) if "." in token else 0


def _matches(token, value):
    """True if `token` (as written in the text) is a faithful rendering of `value`.

    Rounding to the number of decimals written is allowed ("41.7" for 41.666),
    as is writing a 0-1 ratio as a percentage ("42%" for 0.4167).
    """
    written = float(token.replace(",", ""))
    tolerance = 0.5 * 10 ** (-_decimals(token)) + 1e-9
    candidates = [value]
    if -1 <= value <= 1:
        candidates.append(value * 100)
    return any(abs(written - candidate) <= tolerance for candidate in candidates)


def extract_claims(text):
    """Returns (numbers, dates, versions) as written in the text."""
    dates = ISO_DATE_RE.findall(text)
    stripped = ISO_DATE_RE.sub(" ", text)
    versions = VERSION_RE.findall(stripped)
    stripped = VERSION_RE.sub(" ", stripped)
    numbers = NUMBER_RE.findall(stripped)
    return numbers, dates, versions


def verify(text, facts):
    """Checks every number, ISO date and version in `text` against `facts`.

    `facts` is any JSON-like structure (dicts, lists, numbers, strings).
    Returns {"total": n, "verified": m, "unverified": [tokens...]}.
    """
    numbers, strings = [], set()
    _collect(facts, numbers, strings)
    bare_versions = {s.lstrip("v") for s in strings}

    unverified = []
    numeric_tokens, dates, versions = extract_claims(text)
    for token in numeric_tokens:
        if not any(_matches(token, value) for value in numbers):
            unverified.append(token)
    for date in dates:
        if date not in strings:
            unverified.append(date)
    for version in versions:
        if version.lstrip("v") not in bare_versions:
            unverified.append(version)

    total = len(numeric_tokens) + len(dates) + len(versions)
    return {"total": total, "verified": total - len(unverified), "unverified": unverified}
