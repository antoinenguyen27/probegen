from __future__ import annotations

import pytest

from parity.models.eval_case import flatten_expected_output, is_conversation_input, normalize_input


# ---------------------------------------------------------------------------
# flatten_expected_output
# ---------------------------------------------------------------------------


class TestFlattenExpectedOutput:
    def test_none_returns_none(self) -> None:
        assert flatten_expected_output(None) is None

    def test_string_passthrough(self) -> None:
        assert flatten_expected_output("good answer") == "good answer"

    def test_dict_with_expected_behavior_key(self) -> None:
        result = flatten_expected_output({"expected_behavior": "cites sources"})
        assert result == "cites sources"

    def test_dict_with_answer_key(self) -> None:
        result = flatten_expected_output({"answer": "Paris"})
        assert result == "Paris"

    def test_dict_with_output_key(self) -> None:
        result = flatten_expected_output({"output": "The capital is Paris."})
        assert result == "The capital is Paris."

    def test_dict_priority_expected_behavior_before_answer(self) -> None:
        result = flatten_expected_output({"expected_behavior": "first", "answer": "second"})
        assert result == "first"

    def test_dict_without_known_keys_serialises_to_json(self) -> None:
        result = flatten_expected_output({"some_key": "value"})
        assert result is not None
        assert "some_key" in result

    def test_list_serialises_to_json(self) -> None:
        result = flatten_expected_output(["a", "b"])
        assert result is not None
        assert "a" in result

    def test_integer_converts_to_string(self) -> None:
        result = flatten_expected_output(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# is_conversation_input
# ---------------------------------------------------------------------------


class TestIsConversationInput:
    def test_valid_conversation(self) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        assert is_conversation_input(messages) is True

    def test_empty_list_returns_true(self) -> None:
        # vacuously true — all() of empty sequence is True
        assert is_conversation_input([]) is True

    def test_list_missing_role_key(self) -> None:
        assert is_conversation_input([{"content": "no role"}]) is False

    def test_list_missing_content_key(self) -> None:
        assert is_conversation_input([{"role": "user"}]) is False

    def test_plain_string_returns_false(self) -> None:
        assert is_conversation_input("hello") is False

    def test_dict_returns_false(self) -> None:
        assert is_conversation_input({"role": "user", "content": "hi"}) is False


# ---------------------------------------------------------------------------
# normalize_input
# ---------------------------------------------------------------------------


class TestNormalizeInput:
    def test_string_passthrough(self) -> None:
        assert normalize_input("hello") == "hello"

    def test_conversation_list_formats_with_roles(self) -> None:
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = normalize_input(messages)
        assert "USER: Hi" in result
        assert "ASSISTANT: Hello" in result

    def test_dict_with_query_key(self) -> None:
        result = normalize_input({"query": "what is 2+2?", "extra": "ignored"})
        assert result == "what is 2+2?"

    def test_dict_with_input_key(self) -> None:
        result = normalize_input({"input": "the value"})
        assert result == "the value"

    def test_dict_without_priority_keys_serialises(self) -> None:
        result = normalize_input({"other": "data"})
        assert "other" in result

    def test_integer_serialises(self) -> None:
        result = normalize_input(99)
        assert result == "99"
