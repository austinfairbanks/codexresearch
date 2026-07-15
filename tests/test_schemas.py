from __future__ import annotations

import pytest

from soleresearch.errors import SchemaError
from soleresearch.schemas import (
    capability_spec,
    schema_spec,
    validate_capability_document,
    validate_document,
)


def test_task_and_result_contracts_round_trip() -> None:
    task = {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "parent_task_id": None,
        "requeue_of_task_id": None,
        "worker_id": "wrk_" + "1" * 32,
        "role": "reader",
        "depth": 0,
        "base_revision": 0,
        "cycle": 1,
        "subquestion": "What evidence exists?",
        "evidence_strategy": "Inspect primary sources.",
        "allowed_capabilities": ["local_files"],
        "allowed_domains": [],
        "artifact_references": [],
        "selected_context": {},
        "inherited_remaining_budget": {"deep_sources": 5},
        "reservation": {"deep_sources": 1, "provider_usage": 0, "lease_expires_at": "2026-07-10T12:30:00Z"},
        "required_result_operations": ["propose_graph_diff"],
        "created_at": "2026-07-10T12:00:00Z",
    }
    result = {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "worker_id": "wrk_" + "1" * 32,
        "base_revision": 0,
        "used_capabilities": [],
        "used_artifact_references": [],
        "accessed_sources": [],
        "evidence": [],
        "proposed_graph_operations": [],
        "outline_suggestions": [],
        "disagreements": [],
        "gaps": [],
        "rationale": "No sources were provided.",
        "usage": {"unit": "tokens", "amount": 0},
        "completed_operations": ["propose_graph_diff"],
        "errors": [{"code": "no_result:propose_graph_diff", "message": "No graph change."}],
        "completed_at": "2026-07-10T12:01:00Z",
    }

    assert validate_document("task", task) is task
    assert validate_document("result", result) is result


def _standalone_result() -> dict:
    return {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "worker_id": "wrk_" + "1" * 32,
        "base_revision": 0,
        "used_capabilities": [],
        "used_artifact_references": [],
        "accessed_sources": [],
        "evidence": [],
        "proposed_graph_operations": [],
        "outline_suggestions": [],
        "disagreements": [],
        "gaps": [],
        "rationale": "Bounded worker result.",
        "usage": {"unit": "tokens", "amount": 0},
        "completed_operations": [],
        "errors": [],
        "completed_at": "2026-07-10T12:01:00Z",
    }


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "invent", "target": "node", "target_id": "nod_" + "1" * 32},
        {"op": "add", "entity": "node", "node": {"node_type": "question", "title": "Bad keys"}},
        {"op": "add", "target": "edge", "record": {"node_type": "question", "title": "Bad target"}},
        {"op": "add", "target": "node", "record": {"title": "Missing node type"}},
        {"op": "add", "target": "node", "record": {"node_type": "question", "title": "Okay", "mystery": 1}},
        {"op": "link", "record": {"edge_type": "supports", "source_node_id": "nod_a"}},
        {"op": "update", "target": "node", "target_id": "nod_a", "changes": {}},
        {"op": "update", "target": "edge", "target_id": "edg_a", "changes": {"title": "No"}},
        {"op": "merge", "source_id": "nod_same", "target_id": "nod_same"},
        {"op": "move", "target_id": "nod_a", "parent_id": None, "position": -1},
        {"op": "unlink", "target_id": "edg_a", "extra": True},
        {"op": "retire", "target": "shoe", "target_id": "nod_a"},
    ],
)
def test_result_contract_rejects_malformed_graph_operations(operation: dict) -> None:
    result = _standalone_result()
    result["proposed_graph_operations"] = [operation]
    result["completed_operations"] = ["propose_graph_diff"]
    with pytest.raises(SchemaError):
        validate_document("result", result)


def test_result_contract_accepts_all_graph_operations_and_optional_operation_id() -> None:
    result = _standalone_result()
    result["proposed_graph_operations"] = [
        {"operation_id": "op_" + "1" * 32, "op": "add", "target": "node", "record": {"node_type": "question", "title": "Question"}},
        {"op": "update", "target": "node", "target_id": "nod_" + "1" * 32, "changes": {"title": "Updated"}},
        {"op": "merge", "source_id": "nod_" + "2" * 32, "target_id": "nod_" + "1" * 32},
        {"op": "move", "target_id": "nod_" + "1" * 32, "parent_id": None, "position": 0},
        {"op": "link", "record": {"edge_type": "supports", "source_node_id": "nod_" + "1" * 32, "target_node_id": "nod_" + "2" * 32}},
        {"op": "unlink", "target_id": "edg_" + "1" * 32},
        {"op": "retire", "target": "node", "target_id": "nod_" + "1" * 32},
        {"op": "restore", "target": "edge", "target_id": "edg_" + "1" * 32},
    ]
    result["completed_operations"] = ["propose_graph_diff"]
    assert validate_document("result", result) is result


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(completed_operations=["unknown"]), "enum"),
        (lambda value: value.update(completed_operations=["gaps", "gaps"]), "unique"),
        (lambda value: value.update(gaps=[{"question": "Open"}]), "lacks completed"),
        (lambda value: value.update(completed_operations=["gaps"]), "requires output"),
    ],
)
def test_result_contract_rejects_completed_operation_output_mismatches(mutate, message: str) -> None:
    result = _standalone_result()
    mutate(result)
    with pytest.raises(SchemaError, match=message):
        validate_document("result", result)


def test_result_contract_accepts_explicit_no_result_for_every_output_operation() -> None:
    result = _standalone_result()
    operations = [
        "propose_graph_diff", "exact_locator_evidence", "source_provenance",
        "outline_suggestions", "disagreements", "gaps",
    ]
    result["completed_operations"] = operations
    result["errors"] = [
        {"code": f"no_result:{operation}", "message": f"No output for {operation}."}
        for operation in operations
    ]
    assert validate_document("result", result) is result


def test_published_result_schema_has_exact_operation_branches_and_output_conditionals() -> None:
    spec = schema_spec("result")
    definitions = spec["$defs"]
    assert {"partialNodeRecord", "fullNodeRecord", "partialEdgeRecord", "fullEdgeRecord"} <= set(definitions)
    branches = definitions["graphOperation"]["oneOf"]
    assert len(branches) == 8
    assert {branch["properties"]["op"]["const"] for branch in branches} == {
        "add", "update", "merge", "move", "link", "unlink", "retire", "restore",
    }
    assert spec["properties"]["proposed_graph_operations"]["items"] == {"$ref": "#/$defs/graphOperation"}
    assert len(spec["allOf"]) == 6
    for coupling in spec["allOf"]:
        assert len(coupling["allOf"]) == 2
        assert {"if", "then"} <= set(coupling["allOf"][0])
        assert {"if", "then"} <= set(coupling["allOf"][1])
        assert "anyOf" in coupling["allOf"][1]["then"]


@pytest.mark.parametrize("kind", ["project", "task", "result"])
@pytest.mark.parametrize("version", [2, True, 1.0, None, "1"])
def test_unknown_versions_fail_closed(kind: str, version: object) -> None:
    with pytest.raises(SchemaError, match="unsupported"):
        validate_document(kind, {"schema_version": version})


def test_unknown_fields_fail_closed() -> None:
    document = {
        "schema_version": 1,
        "project_id": "prj_1",
        "name": "Example",
        "created_at": "2026-07-10T00:00:00Z",
        "data_policy": "public_only",
        "surprise": True,
    }
    with pytest.raises(SchemaError, match="unknown fields"):
        validate_document("project", document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "   "),
        ("name", "\t"),
        ("created_at", "not-a-dateZ"),
        ("created_at", "2026-07-10Z"),
        ("created_at", "2026-07-10T00:00:00+00:00"),
    ],
)
def test_project_contract_rejects_blank_identity_and_invalid_timestamps(
    field: str, value: str
) -> None:
    document = {
        "schema_version": 1,
        "project_id": "prj_1",
        "name": "Example",
        "created_at": "2026-07-10T00:00:00Z",
        "data_policy": "public_only",
    }
    document[field] = value
    with pytest.raises(SchemaError, match="invalid project"):
        validate_document("project", document)


def test_packaged_specs_are_runtime_source_of_truth() -> None:
    for kind in (
        "project", "task", "result", "source", "evidence", "node", "edge",
        "discussion", "graph_diff", "conflict", "outline_meta", "reconciliation_event", "migration_event",
        "human_edit_event",
        "tool_catalog", "adapter_manifest", "adapter_approval", "zotero_bundle_manifest",
    ):
        spec = schema_spec(kind)
        expected = 2 if kind == "outline_meta" else 1
        assert spec["properties"]["schema_version"] == {"type": "integer", "const": expected}
        assert spec["additionalProperties"] is False
        assert set(spec["required"]) == set(spec["properties"])

    capabilities = capability_spec()
    assert capabilities["namespace"] == "soleresearch"
    assert capabilities["executable"] == "sole-research"
    assert set(capabilities["commands"]) == {
        "init",
        "doctor",
            "status",
            "serve",
        "export",
        "rebuild-index",
        "migrate",
        "import",
        "source",
        "evidence",
        "scaffold",
        "discuss",
        "diff",
        "reconcile",
        "run",
        "resume",
        "budget",
        "gate",
        "tools",
        "adapter",
        "zotero-bundle",
    }
    historical_outline = schema_spec("outline_meta", 1)
    assert historical_outline["properties"]["schema_version"] == {"type": "integer", "const": 1}
    assert set(historical_outline["required"]) == {"schema_version", "revision", "anchors"}


@pytest.mark.parametrize("version", [True, 1.0, None, "1"])
def test_capability_version_is_bool_safe(version: object) -> None:
    with pytest.raises(SchemaError, match="unsupported"):
        validate_capability_document({"schema_version": version})


def test_optional_parent_id_cannot_be_blank() -> None:
    task = {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "parent_task_id": " ",
        "requeue_of_task_id": None,
        "worker_id": "wrk_" + "1" * 32,
        "role": "reader",
        "depth": 1,
        "base_revision": 0,
        "cycle": 1,
        "subquestion": "Question",
        "evidence_strategy": "Inspect sources",
        "allowed_capabilities": [],
        "allowed_domains": [],
        "artifact_references": [],
        "selected_context": {},
        "inherited_remaining_budget": {},
        "reservation": {"deep_sources": 0, "provider_usage": 0, "lease_expires_at": "2026-07-10T12:30:00Z"},
        "required_result_operations": [],
        "created_at": "2026-07-10T12:00:00Z",
    }
    with pytest.raises(SchemaError, match="parent_task_id"):
        validate_document("task", task)


def test_worker_task_packets_reject_controller_capabilities() -> None:
    task = {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "parent_task_id": None,
        "requeue_of_task_id": None,
        "worker_id": "wrk_" + "1" * 32,
        "role": "reader",
        "depth": 0,
        "base_revision": 0,
        "cycle": 1,
        "subquestion": "Question",
        "evidence_strategy": "Inspect sources",
        "allowed_capabilities": [],
        "allowed_domains": [],
        "artifact_references": [],
        "selected_context": {"controller_token": "ctl_secret"},
        "inherited_remaining_budget": {},
        "reservation": {"deep_sources": 0, "provider_usage": 0, "lease_expires_at": "2026-07-10T12:30:00Z"},
        "required_result_operations": [],
        "created_at": "2026-07-10T12:00:00Z",
    }
    with pytest.raises(SchemaError, match="must not contain controller"):
        validate_document("task", task)
