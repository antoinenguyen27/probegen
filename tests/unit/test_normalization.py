from __future__ import annotations

from probegen.models.eval_case import normalize_input


def test_normalize_input_string() -> None:
    assert normalize_input("hello") == "hello"


def test_normalize_input_dict_uses_priority_keys() -> None:
    payload = {"other": "ignored", "query": "What changed?"}
    assert normalize_input(payload) == "What changed?"


def test_normalize_input_conversation_list() -> None:
    payload = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "What changed?"},
    ]
    assert normalize_input(payload) == "USER: Hi\nASSISTANT: Hello\nUSER: What changed?"


def test_normalize_input_falls_back_to_json() -> None:
    payload = {"foo": "bar", "count": 2}
    assert normalize_input(payload) == '{"count": 2, "foo": "bar"}'
