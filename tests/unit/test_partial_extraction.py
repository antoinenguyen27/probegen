from __future__ import annotations

from parity.stages._common import attempt_partial_extraction


def test_returns_none_for_empty_string() -> None:
    assert attempt_partial_extraction("") is None


def test_returns_none_for_none() -> None:
    assert attempt_partial_extraction(None) is None


def test_parses_valid_json_directly() -> None:
    result = attempt_partial_extraction('{"key": "value"}')
    assert result == {"key": "value"}


def test_extracts_json_from_surrounding_text() -> None:
    raw = 'Here is the result:\n{"status": "ok", "count": 3}\nThat is all.'
    result = attempt_partial_extraction(raw)
    assert result == {"status": "ok", "count": 3}


def test_returns_none_when_braces_present_but_invalid() -> None:
    # Braces exist but the substring between them is not valid JSON
    result = attempt_partial_extraction("{ this is not json }")
    assert result is None


def test_returns_none_when_no_braces() -> None:
    assert attempt_partial_extraction("no json at all") is None


def test_parses_nested_json() -> None:
    raw = 'prefix {"outer": {"inner": [1, 2, 3]}} suffix'
    result = attempt_partial_extraction(raw)
    assert result == {"outer": {"inner": [1, 2, 3]}}
