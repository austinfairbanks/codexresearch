from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any

from soleresearch.errors import SchemaError

SCHEMA_VERSION = 1
SCHEMA_KINDS = (
    "project",
    "task",
    "result",
    "source",
    "evidence",
    "node",
    "edge",
    "discussion",
    "graph_diff",
    "conflict",
    "outline_meta",
    "reconciliation_event",
    "migration_event",
    "human_edit_event",
    "run_manifest",
    "run_event",
    "run_gate",
    "run_budget",
    "budget_extension",
    "worker",
    "worker_bundle",
    "run_state",
    "tool_catalog",
    "adapter_manifest",
    "adapter_approval",
    "zotero_bundle_manifest",
    "dashboard_projection",
)

CURRENT_SCHEMA_VERSIONS = {kind: (2 if kind == "outline_meta" else 1) for kind in SCHEMA_KINDS}

RESULT_OPERATIONS = {
    "propose_graph_diff": "proposed_graph_operations",
    "exact_locator_evidence": "evidence",
    "source_provenance": "accessed_sources",
    "outline_suggestions": "outline_suggestions",
    "disagreements": "disagreements",
    "gaps": "gaps",
}
GRAPH_OPERATIONS = {"add", "update", "merge", "move", "link", "unlink", "retire", "restore"}


def is_schema_version(value: Any) -> bool:
    """Accept only the integer schema version, never bool or equal-valued float."""
    return isinstance(value, int) and not isinstance(value, bool) and value == SCHEMA_VERSION


@lru_cache(maxsize=None)
def schema_spec(kind: str, version: int | None = None) -> dict[str, Any]:
    """Load the packaged contract that is also the runtime source of truth."""
    if kind not in SCHEMA_KINDS:
        raise SchemaError(f"unknown schema kind: {kind}")
    selected_version = CURRENT_SCHEMA_VERSIONS[kind] if version is None else version
    if not isinstance(selected_version, int) or isinstance(selected_version, bool) or selected_version < 1:
        raise SchemaError(f"invalid schema version for {kind}: {selected_version!r}")
    resource = files("soleresearch").joinpath("contracts", f"v{selected_version}", f"{kind}.schema.json")
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"cannot load packaged {kind} schema: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaError(f"packaged {kind} schema must be an object")
    return value


def validate_capability_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not is_schema_version(value.get("schema_version")):
        raise SchemaError("unsupported packaged capability specification")
    return value


def capability_spec() -> dict[str, Any]:
    resource = files("soleresearch").joinpath("contracts", "v1", "capabilities.json")
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"cannot load packaged capability specification: {exc}") from exc
    return validate_capability_document(value)


def tool_catalog() -> dict[str, Any]:
    """Return the strict, harness-neutral command catalog."""
    resource = files("soleresearch").joinpath("contracts", "v1", "tool_catalog.json")
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"cannot load packaged tool catalog: {exc}") from exc
    contracts_resource = files("soleresearch").joinpath("contracts", "v1", "tool_action_arguments.json")
    try:
        contracts_document = json.loads(contracts_resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"cannot load packaged tool action arguments: {exc}") from exc
    if (
        not isinstance(contracts_document, dict)
        or set(contracts_document) != {"schema_version", "contracts"}
        or not is_schema_version(contracts_document.get("schema_version"))
        or not isinstance(contracts_document.get("contracts"), dict)
    ):
        raise SchemaError("unsupported packaged tool action arguments")
    expected_contracts: set[str] = set()
    for tool in value.get("tools", []) if isinstance(value, dict) else []:
        for action in tool.get("actions", []) if isinstance(tool, dict) else []:
            identity = f"{tool.get('id')}.{action.get('name')}"
            expected_contracts.add(identity)
            contract = contracts_document["contracts"].get(identity)
            if (
                not isinstance(contract, dict)
                or set(contract) != {"parser_signature", "constraints"}
                or not isinstance(contract["parser_signature"], list)
                or not isinstance(contract["constraints"], list)
                or not all(isinstance(item, str) and item for item in contract["parser_signature"] + contract["constraints"])
            ):
                raise SchemaError(f"invalid packaged tool action argument contract: {identity}")
            action.update(contract)
            parsed_inputs: list[dict[str, Any]] = []
            for signature in contract["parser_signature"]:
                parts = signature.split("|", 6)
                if len(parts) != 7:
                    raise SchemaError(f"invalid parser signature: {identity}")
                name, value_type, kind, required, repeatability, default_json, choices_text = parts
                try:
                    default = json.loads(default_json)
                except json.JSONDecodeError as exc:
                    raise SchemaError(f"invalid parser signature default: {identity}.{name}") from exc
                parsed_inputs.append(
                    {
                        "name": name,
                        "required": required == "required",
                        "type": value_type,
                        "secret": name == "controller_token_file",
                        "positional": kind == "positional",
                        "repeatable": repeatability == "repeatable",
                        "choices": [] if choices_text == "-" else choices_text.split(","),
                        "default": default,
                        "option_strings": [] if kind == "positional" else ["--" + name.replace("_", "-")],
                    }
                )
            action["inputs"] = parsed_inputs
    if set(contracts_document["contracts"]) != expected_contracts:
        raise SchemaError("packaged tool action argument set does not match tool catalog actions")
    catalog = validate_document("tool_catalog", value)
    identities = [item["id"] for item in catalog["tools"]]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise SchemaError("packaged tool catalog IDs must be unique and sorted")
    for tool in catalog["tools"]:
        grouped = "|" in tool["command"]
        actions = tool.get("actions", [])
        if grouped and not actions:
            raise SchemaError(f"grouped tool requires action contracts: {tool['id']}")
        names = [action["name"] for action in actions]
        if len(names) != len(set(names)):
            raise SchemaError(f"tool action names must be unique: {tool['id']}")
        if actions and tool["authority"] != "action_dependent":
            raise SchemaError(f"tool with action contracts must use action_dependent authority: {tool['id']}")
    return catalog


def _is_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaError(f"unsupported type in packaged schema: {expected}")


def _valid_datetime(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == UTC


def _validate_value(field: str, value: Any, rule: dict[str, Any]) -> None:
    expected = rule.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(_is_type(value, item) for item in expected_types):
        raise SchemaError(f"document has invalid field type: {field}")
    if "const" in rule and value != rule["const"]:
        raise SchemaError(f"document has invalid constant: {field}")
    if "enum" in rule and value not in rule["enum"]:
        raise SchemaError(f"document has invalid enum value: {field}")
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            raise SchemaError(f"document has too-short field: {field}")
        pattern = rule.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value, flags=re.DOTALL) is None:
            raise SchemaError(f"document has invalid field pattern: {field}")
        if rule.get("format") == "date-time" and not _valid_datetime(value):
            raise SchemaError(f"document has invalid UTC timestamp: {field}")
    if isinstance(value, int) and not isinstance(value, bool) and value < rule.get("minimum", value):
        raise SchemaError(f"document has field below minimum: {field}")
    if isinstance(value, list) and isinstance(rule.get("items"), dict):
        if len(value) < rule.get("minItems", 0):
            raise SchemaError(f"document has too-few items: {field}")
        for index, item in enumerate(value):
            _validate_value(f"{field}[{index}]", item, rule["items"])
    if isinstance(value, dict):
        required = set(rule.get("required", []))
        properties = rule.get("properties", {})
        missing = sorted(required - set(value))
        extra = (
            sorted(set(value) - set(properties))
            if rule.get("additionalProperties") is False
            else []
        )
        if missing:
            raise SchemaError(f"document field {field} missing fields: {', '.join(missing)}")
        if extra:
            raise SchemaError(f"document field {field} has unknown fields: {', '.join(extra)}")
        for nested_field, nested_value in value.items():
            if nested_field in properties:
                _validate_value(f"{field}.{nested_field}", nested_value, properties[nested_field])


def _validate_partial_record(
    kind: str,
    record: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str],
) -> None:
    missing = sorted(required - set(record))
    extra = sorted(set(record) - allowed)
    if missing:
        raise SchemaError(f"graph {kind} record missing fields: {', '.join(missing)}")
    if extra:
        raise SchemaError(f"graph {kind} record has unknown fields: {', '.join(extra)}")
    properties = schema_spec(kind)["properties"]
    for field, value in record.items():
        _validate_value(f"proposed_graph_operations.record.{field}", value, properties[field])


def _validate_graph_operation(operation: dict[str, Any]) -> None:
    op = operation.get("op")
    if op not in GRAPH_OPERATIONS:
        raise SchemaError(f"unsupported result graph operation: {op!r}")
    common = {"operation_id", "op"}
    expected: set[str]
    if op == "add":
        expected = common | {"target", "record"}
    elif op == "link":
        expected = common | {"record"}
    elif op == "update":
        expected = common | {"target", "target_id", "changes"}
    elif op == "merge":
        expected = common | {"source_id", "target_id"}
    elif op == "move":
        expected = common | {"target_id", "parent_id", "position"}
    elif op == "unlink":
        expected = common | {"target_id"}
    else:
        expected = common | {"target", "target_id"}
    required = expected - {"operation_id"}
    missing = sorted(required - set(operation))
    extra = sorted(set(operation) - expected)
    if missing or extra:
        details = (["missing " + ", ".join(missing)] if missing else []) + (
            ["unknown " + ", ".join(extra)] if extra else []
        )
        raise SchemaError("invalid result graph operation: " + "; ".join(details))
    operation_id = operation.get("operation_id")
    if operation_id is not None and re.fullmatch(r"op_[0-9a-f]{32}", operation_id) is None:
        raise SchemaError("result graph operation_id must be an opaque op_ identity")

    def require_id(value: Any, prefix: str, label: str) -> None:
        if not isinstance(value, str) or re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", value) is None:
            raise SchemaError(f"result graph operation {label} must be an opaque {prefix}_ identity")

    if op == "add":
        if operation["target"] != "node":
            raise SchemaError("result add operation must target a node")
        record = operation["record"]
        if "node_id" in record:
            validate_document("node", record)
        else:
            _validate_partial_record(
                "node",
                record,
                required={"node_type", "title"},
                allowed={
                    "node_type", "title", "body", "tags", "maturity", "authority",
                    "parent_id", "position", "evidence_ids",
                },
            )
            parent_id = record.get("parent_id")
            if parent_id is not None:
                require_id(parent_id, "nod", "record.parent_id")
    elif op == "link":
        record = operation["record"]
        if "edge_id" in record:
            validate_document("edge", record)
        else:
            _validate_partial_record(
                "edge",
                record,
                required={"edge_type", "source_node_id", "target_node_id"},
                allowed={
                    "edge_type", "source_node_id", "target_node_id", "evidence_ids", "authority",
                },
            )
            require_id(record["source_node_id"], "nod", "record.source_node_id")
            require_id(record["target_node_id"], "nod", "record.target_node_id")
    elif op == "update":
        changes = operation["changes"]
        if not changes:
            raise SchemaError("result update operation requires non-empty changes")
        allowed = (
            {"title", "body", "tags", "maturity", "authority", "evidence_ids"}
            if operation["target"] == "node"
            else {"edge_type", "source_node_id", "target_node_id", "evidence_ids", "authority"}
        )
        extra = sorted(set(changes) - allowed)
        if extra:
            raise SchemaError("result update operation has unsupported changes: " + ", ".join(extra))
        properties = schema_spec(operation["target"])["properties"]
        for field, value in changes.items():
            _validate_value(f"proposed_graph_operations.changes.{field}", value, properties[field])
        require_id(operation["target_id"], "nod" if operation["target"] == "node" else "edg", "target_id")
        for field in ("source_node_id", "target_node_id"):
            if field in changes:
                require_id(changes[field], "nod", f"changes.{field}")
    elif op == "merge" and operation["source_id"] == operation["target_id"]:
        raise SchemaError("result merge source and target must differ")
    elif op == "merge":
        require_id(operation["source_id"], "nod", "source_id")
        require_id(operation["target_id"], "nod", "target_id")
    elif op == "move":
        require_id(operation["target_id"], "nod", "target_id")
        if operation["parent_id"] is not None:
            require_id(operation["parent_id"], "nod", "parent_id")
    elif op == "unlink":
        require_id(operation["target_id"], "edg", "target_id")
    else:
        require_id(operation["target_id"], "nod" if operation["target"] == "node" else "edg", "target_id")


def _validate_result_semantics(document: dict[str, Any]) -> None:
    completed = document["completed_operations"]
    if len(completed) != len(set(completed)):
        raise SchemaError("result completed_operations must be unique")
    unknown = sorted(set(completed) - set(RESULT_OPERATIONS))
    if unknown:
        raise SchemaError("result completed_operations contains unsupported operations: " + ", ".join(unknown))
    completed_set = set(completed)
    error_codes = {item["code"] for item in document["errors"]}
    for operation, field in RESULT_OPERATIONS.items():
        output = document[field]
        if output and operation not in completed_set:
            raise SchemaError(f"result nonempty {field} lacks completed operation {operation}")
        if operation in completed_set and not output and f"no_result:{operation}" not in error_codes:
            raise SchemaError(f"result completed operation {operation} requires output or explicit no-result error")

    operation_ids: list[str] = []
    for operation in document["proposed_graph_operations"]:
        _validate_graph_operation(operation)
        operation_id = operation.get("operation_id")
        if operation_id is not None:
            operation_ids.append(operation_id)
    if len(operation_ids) != len(set(operation_ids)):
        raise SchemaError("result graph operation_id values must be unique")


def validate_document(kind: str, document: Any, *, version: int | None = None) -> dict[str, Any]:
    """Validate a strict core document against its packaged current/selected contract."""
    expected_version = CURRENT_SCHEMA_VERSIONS.get(kind) if version is None else version
    spec = schema_spec(kind, expected_version)
    if not isinstance(document, dict):
        raise SchemaError(f"{kind} document must be an object")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != expected_version:
        raise SchemaError(f"unsupported {kind} schema_version: {version!r}; expected {expected_version}")
    required = set(spec.get("required", []))
    properties = spec.get("properties", {})
    missing = sorted(required - set(document))
    extra = sorted(set(document) - set(properties)) if spec.get("additionalProperties") is False else []
    if missing:
        raise SchemaError(f"{kind} document missing fields: {', '.join(missing)}")
    if extra:
        raise SchemaError(f"{kind} document has unknown fields: {', '.join(extra)}")
    try:
        for field, value in document.items():
            if field in properties:
                _validate_value(field, value, properties[field])
    except SchemaError as exc:
        raise SchemaError(f"invalid {kind} {exc}") from exc
    if kind == "task":
        def contains_capability(value: Any) -> bool:
            if isinstance(value, dict):
                forbidden = {"controller_token", "controller_capability", "controller_token_file"}
                return bool(forbidden.intersection(value)) or any(contains_capability(item) for item in value.values())
            if isinstance(value, list):
                return any(contains_capability(item) for item in value)
            return isinstance(value, str) and value.startswith("ctl_")

        if contains_capability(document):
            raise SchemaError("task packets must not contain controller capabilities")
    elif kind == "result":
        _validate_result_semantics(document)
    return document
