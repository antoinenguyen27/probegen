from __future__ import annotations

from parity.stages._common import simplify_schema


def test_strips_unsupported_keywords() -> None:
    """additionalProperties, title, default, format are not in _SUPPORTED_KEYS and must be removed."""
    schema = {
        "type": "object",
        "title": "MyModel",
        "additionalProperties": False,
        "properties": {
            "name": {
                "type": "string",
                "title": "Name",
                "default": "fallback",
                "format": "uri",
            }
        },
        "required": ["name"],
    }

    result = simplify_schema(schema)

    assert "title" not in result
    assert "additionalProperties" not in result
    prop = result["properties"]["name"]
    assert "title" not in prop
    assert "default" not in prop
    assert "format" not in prop
    assert prop["type"] == "string"
    assert result["required"] == ["name"]


def test_resolves_single_ref() -> None:
    """A $ref pointing into $defs is inlined; $defs is consumed and not present in output."""
    schema = {
        "type": "object",
        "properties": {
            "child": {"$ref": "#/$defs/Child"},
        },
        "$defs": {
            "Child": {
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                },
            }
        },
    }

    result = simplify_schema(schema)

    assert "$defs" not in result
    child = result["properties"]["child"]
    assert child["type"] == "object"
    assert child["properties"]["value"]["type"] == "integer"


def test_resolves_transitive_refs() -> None:
    """A ref whose target itself contains another ref is resolved recursively."""
    schema = {
        "type": "object",
        "properties": {
            "outer": {"$ref": "#/$defs/Outer"},
        },
        "$defs": {
            "Outer": {
                "type": "object",
                "properties": {
                    "inner": {"$ref": "#/$defs/Inner"},
                },
            },
            "Inner": {"type": "string"},
        },
    }

    result = simplify_schema(schema)

    outer = result["properties"]["outer"]
    assert outer["type"] == "object"
    assert outer["properties"]["inner"] == {"type": "string"}


def test_collapses_nullable_anyof_to_inner_type() -> None:
    """anyOf: [X, {type: null}] is reduced to X (nullable optional field)."""
    schema = {
        "type": "object",
        "properties": {
            "count": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    }

    result = simplify_schema(schema)

    assert result["properties"]["count"] == {"type": "integer"}


def test_collapses_multi_type_anyof_to_unconstrained() -> None:
    """anyOf with multiple non-null variants collapses to {} (Agent SDK cannot express union types)."""
    schema = {
        "type": "object",
        "properties": {
            "value": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }

    result = simplify_schema(schema)

    assert result["properties"]["value"] == {}


def test_removes_inject_keys_from_properties_and_required() -> None:
    """Inject keys are stripped from both the properties dict and the required list."""
    schema = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "timestamp": {"type": "string"},
            "probes": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["run_id", "timestamp", "probes"],
    }

    result = simplify_schema(schema, remove_keys={"run_id", "timestamp"})

    assert "run_id" not in result["properties"]
    assert "timestamp" not in result["properties"]
    assert "probes" in result["properties"]
    assert result["required"] == ["probes"]


def test_processes_array_items_recursively() -> None:
    """Unsupported keywords nested inside array item schemas are also stripped."""
    schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "title": "ItemTitle",
                    "additionalProperties": False,
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "title": "FieldName"},
                    },
                },
            }
        },
    }

    result = simplify_schema(schema)

    items = result["properties"]["items_list"]["items"]
    assert "title" not in items
    assert "additionalProperties" not in items
    assert items["type"] == "object"
    assert "title" not in items["properties"]["name"]
    assert items["properties"]["name"]["type"] == "string"


def test_remove_keys_without_required_does_not_raise() -> None:
    """A schema with no 'required' field and remove_keys set processes cleanly."""
    schema = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "data": {"type": "string"},
        },
        # no "required" key
    }

    result = simplify_schema(schema, remove_keys={"run_id"})

    assert "run_id" not in result["properties"]
    assert "data" in result["properties"]
    assert "required" not in result


def test_smoke_behavior_change_manifest_schema() -> None:
    """Real Pydantic schema for BehaviorChangeManifest survives simplification with inject keys removed."""
    from parity.models import BehaviorChangeManifest
    from parity.stages.stage1 import _STAGE1_INJECT_KEYS

    raw_schema = BehaviorChangeManifest.model_json_schema()
    result = simplify_schema(raw_schema, remove_keys=_STAGE1_INJECT_KEYS)

    assert isinstance(result.get("properties"), dict)
    for key in _STAGE1_INJECT_KEYS:
        assert key not in result["properties"], f"inject key {key!r} was not removed from schema"
    # Core semantic fields must survive
    assert "changes" in result["properties"]
    assert "overall_risk" in result["properties"]
    assert "has_changes" in result["properties"]
