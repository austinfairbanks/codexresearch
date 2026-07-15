from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from soleresearch.controller import CONFIG_HOME_ENV, capability_paths
from soleresearch.discussions import DiscussionRepository
from soleresearch.errors import ProjectError, SchemaError
from soleresearch.evidence import EvidenceRepository, locate_excerpt, locator
from soleresearch.graph import GraphRepository, new_node
from soleresearch.indexing import read_index_snapshot, rebuild_index
from soleresearch.orchestration import (
    DEFAULT_ENVELOPE,
    MAX_WORKER_SOURCE_CHARACTERS,
    ProjectRunLock,
    RunRepository,
    validate_result_with_bundle,
)
from soleresearch.project import initialize_project
from soleresearch.schemas import validate_document
from soleresearch.sources import SourceRepository, import_local_document, import_url, load_extraction
from soleresearch.storage import canonical_json, write_jsonl
from soleresearch.writing import ProjectWriteLock, run_write_scope

NOW = lambda: "2026-07-10T12:00:00Z"
LATER = lambda: "2026-07-10T12:01:00Z"


def _project(tmp_path: Path) -> tuple[Path, str, str]:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "controller-config")
    root = tmp_path / "project"
    project = initialize_project(root)
    os.environ["PHASE4_PROJECT_ROOT"] = str(root)
    for index in range(3):
        note = tmp_path / f"source-{index}.md"
        note.write_text(f"# Source {index}\n\nExact inspected content {index}.\n")
        import_local_document(root, note, kind="markdown")
    paths = capability_paths(project["project_id"])
    agent = json.loads(paths["agent"].read_text())["token"]
    human = json.loads(paths["human"].read_text())["token"]
    return root, agent, human


def _task(run: RunRepository, token: str, **overrides: object) -> dict:
    source_refs = [f"source:{item['source_id']}" for item in SourceRepository(run.canonical_root).all()]
    values = {
        "role": "reader", "subquestion": "What does the source show?",
        "evidence_strategy": "inspect exact passages", "selected_context": {"node_ids": ["nod_context"]},
        "allowed_capabilities": ["read_source"], "artifact_references": source_refs,
        "reserve_deep_sources": 3, "reserve_provider_usage": 0.5,
        "controller_token": token, "now": NOW,
    }
    values.update(overrides)
    return run.dispatch(**values)


def _result(task: dict, *, sources: int = 0, usage: float = 0.1, operations: list[dict] | None = None) -> dict:
    root = Path(os.environ["PHASE4_PROJECT_ROOT"])
    records = SourceRepository(root).all()
    proposed = operations or []
    errors = [] if proposed or "propose_graph_diff" not in task["required_result_operations"] else [{"code": "no_result:propose_graph_diff", "message": "No graph change proposed."}]
    return {
        "schema_version": 1, "run_id": task["run_id"], "task_id": task["task_id"],
        "worker_id": task["worker_id"], "base_revision": task["base_revision"],
        "used_capabilities": [], "used_artifact_references": [f"source:{item['source_id']}" for item in records[:sources]],
        "accessed_sources": [
            {"source_id": item["source_id"], "source_hash": item["content_hash"], "source_version": item["source_version"], "deeply_processed": True, "access_method": "local_inspection", "access_url": None}
            for item in records[:sources]
        ],
        "evidence": [], "proposed_graph_operations": proposed, "outline_suggestions": [],
        "disagreements": [], "gaps": [], "rationale": "bounded result",
        "usage": {"unit": "dollars", "amount": usage}, "errors": errors,
        "completed_operations": sorted(set(task["required_result_operations"]) | ({"source_provenance"} if sources else set())),
        "completed_at": "2026-07-10T12:01:00Z",
    }


def test_default_manifest_is_immutable_and_exactly_balanced(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    assert run.manifest["config"] == DEFAULT_ENVELOPE
    assert run.manifest["git"] == {
        "enabled": False, "repository": None, "baseline_revision": None,
        "branch": None, "worktree": None, "baseline_dirty": None,
    }
    before = (run.run_dir / "manifest.json").read_bytes()
    _task(run, agent)
    assert (run.run_dir / "manifest.json").read_bytes() == before
    with pytest.raises(ProjectError, match="writable run"):
        RunRepository.create(root, controller="human", now=NOW)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"agents": 5}, "at most 4"),
        ({"max_depth": 2}, "only root worker"),
        ({"tasks": 0}, "tasks"),
        ({"provider_usage": {"unit": "dollars", "ceiling": 0}}, "ceiling"),
    ],
)
def test_envelope_cap_matrix_fails_closed(tmp_path: Path, change: dict, message: str) -> None:
    root, _, _ = _project(tmp_path)
    envelope = {**DEFAULT_ENVELOPE, **change}
    with pytest.raises(ProjectError, match=message):
        RunRepository.create(root, controller="sol", envelope=envelope, now=NOW)


def test_dispatch_context_is_selected_and_worker_has_no_writer_authority(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    assert task["selected_context"] == {"node_ids": ["nod_context"]}
    assert task["inherited_remaining_budget"]["tasks"] == 10
    worker = json.loads((run.run_dir / "workers" / f"{task['worker_id']}.json").read_text())
    assert worker["role"] == "reader" and worker["capabilities"] == ["read_source"]
    serialized = json.dumps(task)
    assert "ctl_" not in serialized and str(root) not in serialized
    with pytest.raises(ProjectError, match="allowlisted"):
        _task(run, agent, allowed_capabilities=["canonical_writer"])
    with pytest.raises(ProjectError, match="logical artifact"):
        _task(run, agent, artifact_references=[str(root / "outline.md")])


def test_shared_depth_concurrency_and_task_caps_cannot_reset(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    envelope = {**DEFAULT_ENVELOPE, "tasks": 4}
    run = RunRepository.create(root, controller="sol", envelope=envelope, now=NOW)
    parent = _task(run, agent)
    nested = _task(run, agent, parent_task_id=parent["task_id"], role="critic")
    assert nested["depth"] == 1
    with pytest.raises(ProjectError, match="depth cap"):
        _task(run, agent, parent_task_id=nested["task_id"])
    _task(run, agent, role="scout")
    with pytest.raises(ProjectError, match="concurrent agents"):
        _task(run, agent, role="verifier")
    assert nested["inherited_remaining_budget"]["tasks"] == 3


def test_sol_result_import_auto_applies_and_is_idempotent(tmp_path: Path) -> None:
    root, agent, human = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    result = _result(task, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Worker question"}}])
    first = run.import_result(result, controller_token=agent, now=LATER)
    second = run.import_result(result, controller_token=agent, now=LATER)
    assert first["graph_diff"]["status"] == "applied"
    assert first["graph_diff"]["applied_authority"] == "agent_accepted"
    assert second == {"schema_version": 1, "result_id": first["result_id"], "status": "accepted", "idempotent": True}
    state = json.loads((run.run_dir / "state.json").read_text())
    assert first["graph_diff"]["diff_id"] in state["ratification_diff_ids"]
    run.switch_controller("human", controller_token=agent, now=LATER)
    ratified = run.ratify(first["graph_diff"]["diff_id"], controller_token=human, now=LATER)
    assert ratified["ratification_diff"]["applied_authority"] == "human_accepted"
    assert GraphRepository(root).nodes()[0]["authority"] == "human_accepted"


def test_human_result_waits_at_gate_and_controller_handoff_is_clean_only(tmp_path: Path) -> None:
    root, agent, human = _project(tmp_path)
    run = RunRepository.create(root, controller="human", now=NOW)
    task = _task(run, human)
    with pytest.raises(ProjectError, match="clean gate"):
        run.switch_controller("sol", controller_token=human, now=NOW)
    result = _result(task, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Human-reviewed"}}])
    imported = run.import_result(result, controller_token=human, now=LATER)
    assert imported["graph_diff"]["status"] == "proposed"
    assert run.status(now=LATER)["gate"]["status"] == "human_review"
    applied = run.resolve_result(imported["result_id"], accept=True, controller_token=human, now=LATER)
    assert applied["graph_diff"]["applied_authority"] == "human_accepted"
    switched = run.switch_controller("sol", controller_token=human, now=LATER)
    assert switched["controller"] == "sol"
    with pytest.raises(ProjectError, match="sol controller"):
        run.pause("wrong capability", controller_token=human, now=LATER)
    run.pause("operator pause", controller_token=agent, now=LATER)


def test_stale_result_remains_reviewable_and_cannot_apply(tmp_path: Path) -> None:
    root, _, human = _project(tmp_path)
    run = RunRepository.create(root, controller="human", now=NOW)
    task1 = _task(run, human)
    task2 = _task(run, human, role="critic")
    op1 = [{"op": "add", "target": "node", "record": {"node_type": "question", "title": "First"}}]
    op2 = [{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Second"}}]
    first = run.import_result(_result(task1, operations=op1), controller_token=human, now=LATER)
    run.resolve_result(first["result_id"], accept=True, controller_token=human, now=LATER)
    second = run.import_result(_result(task2, operations=op2), controller_token=human, now=LATER)
    assert second["stale"] is True and second["graph_diff"]["status"] == "proposed"
    with pytest.raises(ProjectError, match="stale result"):
        run.resolve_result(second["result_id"], accept=True, controller_token=human, now=LATER)


def test_hard_cap_is_durable_then_extension_allows_resume(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    envelope = {**DEFAULT_ENVELOPE, "deep_sources": 1, "provider_usage": {"unit": "dollars", "ceiling": 1.0}}
    run = RunRepository.create(root, controller="sol", envelope=envelope, now=NOW)
    task = _task(run, agent, reserve_deep_sources=1)
    result = _result(task, sources=1, usage=0.5)
    accepted = run.import_result(result, controller_token=agent, now=LATER)
    assert accepted["status"] == "accepted"
    assert run.status(now=LATER)["state"]["pause_reason"] == "hard_cap:deep_sources"
    with pytest.raises(ProjectError, match="extension"):
        run.resume(controller_token=agent, now=LATER)
    extension = run.extend({"deep_sources": 1}, actor="sol", reason="one bounded additional source", controller_token=agent, now=LATER)
    assert extension["delta"] == {"deep_sources": 1}
    run.resume(controller_token=agent, now=LATER)
    assert run.status(now=LATER)["budget"]["consumed"]["deep_sources"] == 1


def test_exact_provider_usage_ceiling_result_applies_then_pauses(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    envelope = {**DEFAULT_ENVELOPE, "provider_usage": {"unit": "dollars", "ceiling": 1.0}}
    run = RunRepository.create(root, controller="sol", envelope=envelope, now=NOW)
    task = _task(run, agent, reserve_provider_usage=1.0)
    result = _result(task, usage=1.0, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "At ceiling"}}])
    assert run.import_result(result, controller_token=agent, now=LATER)["graph_diff"]["status"] == "applied"
    assert run.status(now=LATER)["state"]["pause_reason"] == "hard_cap:provider_usage"


def test_worker_bundle_is_secret_free_bounded_and_drives_task_aware_preflight(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    large = tmp_path / "large.md"
    large.write_text("# Large\n\n" + "bounded source material " * 4_000)
    source, _ = import_local_document(root, large, kind="markdown")
    extraction = load_extraction(root, source["source_id"], source["content_hash"], source["source_version"])
    excerpt = "# Large"
    evidence, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=locate_excerpt(extraction, excerpt), excerpt=excerpt,
        paraphrase="Bounded source.", stance="context", actor_type="human",
        actor_id="tester", method="manual", now=NOW,
    )
    run = RunRepository.create(root, controller="sol", now=NOW)
    reference = f"source:{source['source_id']}"
    task = _task(
        run,
        agent,
        artifact_references=[reference, f"evidence:{evidence['evidence_id']}"],
        reserve_deep_sources=1,
        reserve_provider_usage=0.5,
    )
    bundle = run.worker_bundle(task["task_id"], controller_token=agent, now=NOW)
    serialized = canonical_json(bundle)
    assert "ctl_" not in serialized and str(root) not in serialized
    assert bundle["task"] == task
    assert bundle["sources"][0]["material_truncated"] is True
    assert bundle["sources"][0]["included_characters"] == MAX_WORKER_SOURCE_CHARACTERS
    assert set(bundle["evidence"][0]["record"]) == {
        "evidence_id", "source_id", "source_hash", "source_version",
        "locator", "excerpt", "retrieved_at",
    }
    assert "attestations" not in bundle["evidence"][0]["record"]

    validate_result_with_bundle(bundle["result_template"], bundle, receipt_time=NOW())
    result = _result(task, usage=0.0)
    validate_result_with_bundle(result, bundle, receipt_time=LATER())
    future = {**result, "completed_at": "2026-07-10T12:02:00Z"}
    with pytest.raises(ProjectError, match="later than receipt"):
        validate_result_with_bundle(future, bundle, receipt_time=LATER())
    outside = {**result, "used_artifact_references": ["source:not-assigned"]}
    with pytest.raises(ProjectError, match="artifact references outside"):
        validate_result_with_bundle(outside, bundle, receipt_time=LATER())

    oversized_context = _task(
        run, agent, role="large-context", selected_context={"text": "x" * 60_000},
        artifact_references=[reference], reserve_deep_sources=1,
    )
    with pytest.raises(ProjectError, match="serialized bytes"):
        run.worker_bundle(oversized_context["task_id"], controller_token=agent, now=NOW)
    oversized_evidence = _task(
        run, agent, role="too-many-evidence",
        artifact_references=[f"evidence:ev_{index:024x}" for index in range(65)],
    )
    with pytest.raises(ProjectError, match="64 evidence"):
        run.worker_bundle(oversized_evidence["task_id"], controller_token=agent, now=NOW)


def test_task_result_time_order_is_closed_on_both_sides(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    bundle = run.worker_bundle(task["task_id"], controller_token=agent, now=NOW)
    before_task = _result(task, usage=0.0)
    before_task["completed_at"] = "2026-07-10T11:59:59Z"
    with pytest.raises(ProjectError, match="earlier than task creation"):
        validate_result_with_bundle(before_task, bundle, receipt_time=LATER())
    after_receipt = _result(task, usage=0.0)
    after_receipt["completed_at"] = "2026-07-10T12:02:00Z"
    with pytest.raises(ProjectError, match="later than receipt"):
        validate_result_with_bundle(after_receipt, bundle, receipt_time=LATER())


def test_worker_bundle_strips_query_credentials_from_provenance_urls(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    source, _ = import_url(root, "https://example.org/paper?access_token=secret-value")
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(
        run, agent, artifact_references=[f"source:{source['source_id']}"],
        allowed_capabilities=["web_search"], allowed_domains=["example.org"],
    )
    bundle = run.worker_bundle(task["task_id"], controller_token=agent, now=NOW)
    serialized = canonical_json(bundle)
    assert "access_token" not in serialized and "secret-value" not in serialized
    assert bundle["sources"][0]["provenance_urls"] == ["https://example.org/paper"]


def test_sol_semantically_rebases_three_independent_parallel_node_adds(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    tasks = [_task(run, agent, role=f"branch-{index}") for index in range(3)]
    imports = []
    for index, task in enumerate(tasks):
        operation = [{
            "op": "add", "target": "node",
            "record": {"node_type": "question", "title": f"Branch {index}", "position": index},
        }]
        imports.append(run.import_result(_result(task, operations=operation), controller_token=agent, now=LATER))
    assert [item["graph_diff"]["status"] for item in imports] == ["applied", "applied", "applied"]
    assert [item["rebased_from_revision"] for item in imports] == [None, 0, 0]
    assert [item["graph_diff"]["base_revision"] for item in imports] == [0, 1, 2]
    assert [item["title"] for item in GraphRepository(root).nodes()] == ["Branch 0", "Branch 1", "Branch 2"]


def test_applied_semantic_rebase_recovers_after_run_commit_crash(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    first_task = _task(run, agent, role="first")
    second_task = _task(run, agent, role="second")
    first_result = _result(first_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "First", "position": 0},
    }])
    second_result = _result(second_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Recovered", "position": 1},
    }])
    run.import_result(first_result, controller_token=agent, now=LATER)

    def crash_after_apply() -> None:
        raise RuntimeError("injected after graph apply")

    with pytest.raises(RuntimeError, match="injected after graph apply"):
        run.import_result(
            second_result, controller_token=agent, now=LATER,
            after_graph_apply=crash_after_apply,
        )
    assert [item["title"] for item in GraphRepository(root).nodes()] == ["First", "Recovered"]
    assert len(RunRepository(root, run.run_id)._state()["accepted_result_ids"]) == 1

    recovered = RunRepository(root, run.run_id)
    retried = recovered.import_result(second_result, controller_token=agent, now=LATER)
    assert retried["graph_diff"]["status"] == "applied"
    assert retried["rebased_from_revision"] == second_task["base_revision"]
    assert [item["title"] for item in GraphRepository(root).nodes()].count("Recovered") == 1
    status = recovered.status(now=LATER)
    assert len(status["state"]["accepted_result_ids"]) == 2
    assert len(status["budget"]["accounted_result_ids"]) == 2


def test_applied_rebase_recovery_overrides_later_lease_expiry(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    first_task = _task(run, agent, role="first")
    second_task = _task(run, agent, role="second", lease_minutes=1)
    first = _result(first_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "First", "position": 0},
    }])
    second = _result(second_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Expired recovery", "position": 1},
    }])
    run.import_result(first, controller_token=agent, now=LATER)
    with pytest.raises(RuntimeError):
        run.import_result(
            second, controller_token=agent, now=LATER,
            after_graph_apply=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
        )
    late = lambda: "2026-07-10T13:00:00Z"
    assert run.expire_leases(controller_token=agent, now=late)["expired_task_ids"] == [second_task["task_id"]]
    recovered = run.import_result(second, controller_token=agent, now=late)
    assert recovered["recovered_applied"] is True
    status = RunRepository(root, run.run_id).status(now=late)
    assert recovered["graph_diff"]["status"] == "applied"
    assert len(status["state"]["accepted_result_ids"]) == 2
    assert len(status["budget"]["accounted_result_ids"]) == 2
    assert recovered["graph_diff"]["diff_id"] in status["state"]["ratification_diff_ids"]
    assert [item["title"] for item in GraphRepository(root).nodes()].count("Expired recovery") == 1
    events = [json.loads(line)["event_type"] for line in (run.run_dir / "events.jsonl").read_text().splitlines()]
    assert events[-2:] == ["task_lease_expired", "result_recovered_applied"]


def test_applied_rebase_recovery_accounts_before_current_minutes_pause(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(
        root, controller="sol", envelope={**DEFAULT_ENVELOPE, "minutes": 1}, now=NOW
    )
    first_task = _task(run, agent, role="first")
    second_task = _task(run, agent, role="second")
    receipt = lambda: "2026-07-10T12:00:30Z"
    first = _result(first_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "First", "position": 0},
    }])
    second = _result(second_task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Minutes recovery", "position": 1},
    }])
    first["completed_at"] = receipt()
    second["completed_at"] = receipt()
    run.import_result(first, controller_token=agent, now=receipt)
    with pytest.raises(RuntimeError):
        run.import_result(
            second, controller_token=agent, now=receipt,
            after_graph_apply=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
        )
    late = lambda: "2026-07-10T12:02:00Z"
    recovered = run.import_result(second, controller_token=agent, now=late)
    status = RunRepository(root, run.run_id).status(now=late)
    assert recovered["recovered_applied"] is True
    assert status["state"]["status"] == status["gate"]["status"] == "paused"
    assert status["state"]["pause_reason"] == "hard_cap:minutes"
    assert len(status["state"]["accepted_result_ids"]) == 2
    assert len(status["budget"]["accounted_result_ids"]) == 2
    assert recovered["graph_diff"]["diff_id"] in status["state"]["ratification_diff_ids"]
    assert [item["title"] for item in GraphRepository(root).nodes()].count("Minutes recovery") == 1
    events = [json.loads(line)["event_type"] for line in (run.run_dir / "events.jsonl").read_text().splitlines()]
    assert events[-2:] == ["result_recovered_applied", "run_paused"]


def test_result_diff_uses_single_captured_receipt_when_clock_crosses_lease(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, lease_minutes=1)
    receipt = "2026-07-10T12:00:59Z"
    result = _result(task, operations=[{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Captured receipt", "position": 0},
    }])
    result["completed_at"] = receipt
    instants = iter((receipt, "2026-07-10T12:01:01Z"))
    imported = run.import_result(result, controller_token=agent, now=lambda: next(instants))
    assert imported["graph_diff"]["created_at"] == receipt
    assert imported["graph_diff"]["status"] == "applied"
    assert GraphRepository(root).nodes()[0]["created_at"] == receipt


def test_applied_prior_diff_stamped_after_lease_fails_recovery(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, lease_minutes=1)
    operations = [{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Late durable diff", "position": 0},
    }]
    result = _result(task, operations=operations)
    result_identity = "res_" + hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()[:32]
    diff_id = "dif_" + hashlib.sha256(
        f"{run.run_id}:{task['task_id']}:{result_identity}".encode("utf-8")
    ).hexdigest()[:32]
    late = lambda: "2026-07-10T12:02:00Z"
    graph = GraphRepository(root)
    with run_write_scope(root, run.run_id, canonical_root=root):
        graph.propose(
            operations, base_revision=task["base_revision"], actor_type="agent",
            actor_id=task["worker_id"], diff_id=diff_id, now=late,
        )
        graph.apply(diff_id, controller_token=agent, now=late)
    with pytest.raises(ProjectError, match="receipt occurred after"):
        run.import_result(result, controller_token=agent, now=late)


def test_stale_position_collision_pauses_at_cap_and_reject_keeps_gate_paused(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(
        root, controller="sol", envelope={**DEFAULT_ENVELOPE, "tasks": 2}, now=NOW
    )
    first_task = _task(run, agent, role="first")
    second_task = _task(run, agent, role="second")
    collision = [{
        "op": "add", "target": "node",
        "record": {"node_type": "question", "title": "Same position", "position": 0},
    }]
    run.import_result(_result(first_task, operations=collision), controller_token=agent, now=LATER)
    second = run.import_result(_result(second_task, operations=collision), controller_token=agent, now=LATER)
    assert second["stale"] is True and second["pending_decision"]["stale"] is True
    status = run.status(now=LATER)
    assert status["state"]["status"] == "paused"
    assert status["gate"]["status"] == "paused"
    run.resolve_result(second["result_id"], accept=False, controller_token=agent, now=LATER)
    reopened = RunRepository(root, run.run_id).status(now=LATER)
    assert reopened["state"]["status"] == reopened["gate"]["status"] == "paused"
    assert reopened["gate"]["pending_decisions"] == []


def test_stale_non_add_operation_is_never_semantically_rebased(tmp_path: Path) -> None:
    root, agent, human = _project(tmp_path)
    seed = new_node("question", "Seed", authority="human_accepted", now=NOW)
    diff = GraphRepository(root).propose(
        [{"op": "add", "target": "node", "record": seed}],
        actor_type="human", actor_id="human", now=NOW,
    )
    GraphRepository(root).apply(diff["diff_id"], controller_token=human, now=NOW)
    run = RunRepository.create(root, controller="sol", now=NOW)
    add_task = _task(run, agent, role="add")
    update_task = _task(run, agent, role="update")
    run.import_result(
        _result(add_task, operations=[{
            "op": "add", "target": "node",
            "record": {"node_type": "question", "title": "Independent", "position": 1},
        }]),
        controller_token=agent, now=LATER,
    )
    stale = run.import_result(
        _result(update_task, operations=[{
            "op": "update", "target": "node", "target_id": seed["node_id"],
            "changes": {"title": "Stale edit"},
        }]),
        controller_token=agent, now=LATER,
    )
    assert stale["stale"] is True and stale["rebased_from_revision"] is None
    assert stale["graph_diff"]["base_revision"] == update_task["base_revision"]


def test_malformed_mismatched_and_duplicate_results_fail_closed(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    malformed = _result(task)
    malformed["unknown"] = True
    with pytest.raises(ProjectError, match="unknown fields"):
        run.import_result(malformed, controller_token=agent, now=LATER)
    mismatch = _result(task)
    mismatch["worker_id"] = "wrk_" + "0" * 32
    with pytest.raises(ProjectError, match="worker_id"):
        run.import_result(mismatch, controller_token=agent, now=LATER)
    valid = _result(task)
    run.import_result(valid, controller_token=agent, now=LATER)
    changed = {**valid, "rationale": "different"}
    with pytest.raises(ProjectError, match="different result"):
        run.import_result(changed, controller_token=agent, now=LATER)


def test_interrupt_resume_events_and_lock_contention(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    run.pause("interrupt", controller_token=agent, now=LATER)
    resumed = run.resume(controller_token=agent, now=LATER)
    assert resumed["status"] == "active"
    events = [item["event_type"] for item in map(json.loads, (run.run_dir / "events.jsonl").read_text().splitlines())]
    assert events == ["run_created", "run_paused", "run_resumed"]
    script = """
import sys, time
from pathlib import Path
from soleresearch.writing import ProjectWriteLock
with ProjectWriteLock(Path(sys.argv[1])):
    print('READY', flush=True)
    time.sleep(5)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(root)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "READY"
        with pytest.raises(ProjectError, match="writer lock"):
            run.status(now=LATER)
    finally:
        process.terminate()
        process.wait(timeout=5)


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def test_optional_git_worktree_checkpoint_never_merges_or_pushes(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    _git(root, "init")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    run = RunRepository.create(
        root, controller="sol", git_repository=root, worktree_parent=tmp_path / "worktrees",
        baseline_revision=baseline, now=NOW,
    )
    policy = run.manifest["git"]
    assert policy["branch"] == f"soleresearch/run/{run.run_id}"
    worktree = Path(policy["worktree"])
    assert (worktree / ".git").is_file()
    with pytest.raises(ProjectError, match="canonical paths"):
        _task(run, agent, selected_context={"worktree": str(worktree)})
    task = _task(run, agent)
    result = _result(task, usage=0.0, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Worktree-only"}}])
    run.import_result(result, controller_token=agent, now=LATER)
    assert GraphRepository(root).nodes() == []
    assert [item["title"] for item in GraphRepository(worktree).nodes()] == ["Worktree-only"]
    with pytest.raises(ProjectError, match="active run controller broker"):
        GraphRepository(worktree).propose(
            [{"op": "add", "target": "node", "record": new_node("question", "Bypass", now=NOW)}], now=NOW
        )
    (worktree / "note.md").write_text("checkpoint\n")
    checkpoint = run.checkpoint(["note.md"], "research checkpoint", controller_token=agent, now=LATER)
    assert _git(worktree, "rev-parse", "HEAD") == checkpoint["revision"]
    assert _git(root, "rev-parse", "HEAD") == baseline
    assert _git(root, "remote") == ""
    (worktree / "rogue.md").write_text("rogue\n")
    _git(worktree, "add", "rogue.md")
    (worktree / "note.md").write_text("next\n")
    with pytest.raises(ProjectError, match="pre-staged"):
        run.checkpoint(["note.md"], "must reject", controller_token=agent, now=LATER)


def test_checkpoint_verifies_binding_and_cleans_index_after_commit_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import soleresearch.git_isolation as git_isolation

    root, agent, _ = _project(tmp_path)
    _git(root, "init")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    run = RunRepository.create(root, controller="sol", git_repository=root, worktree_parent=tmp_path / "wt", baseline_revision=baseline, now=NOW)
    worktree = Path(run.manifest["git"]["worktree"])
    (worktree / "note.md").write_text("content\n")
    original = git_isolation._git
    def fail_commit(repository: Path, arguments: list[str]) -> str:
        if "commit" in arguments:
            raise ProjectError("injected commit failure")
        return original(repository, arguments)
    monkeypatch.setattr(git_isolation, "_git", fail_commit)
    with pytest.raises(ProjectError, match="injected"):
        run.checkpoint(["note.md"], "checkpoint", controller_token=agent, now=LATER)
    monkeypatch.setattr(git_isolation, "_git", original)
    assert _git(worktree, "diff", "--cached", "--name-only") == ""
    run.manifest["git"]["branch"] = "soleresearch/run/wrong"
    with pytest.raises(ProjectError, match="branch"):
        run.checkpoint(["note.md"], "wrong binding", controller_token=agent, now=LATER)
    assert _git(worktree, "diff", "--cached", "--name-only") == ""


def test_abrupt_git_create_intent_recovers_orphan_and_allows_same_id_retry(tmp_path: Path) -> None:
    root, _, _ = _project(tmp_path)
    _git(root, "init")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    identity = "run_" + "c" * 32
    script = """
import os, sys
from pathlib import Path
from soleresearch.orchestration import RunRepository
def crash(index, path):
    if index == 2: os._exit(78)
RunRepository.create(Path(sys.argv[1]), controller='sol', git_repository=Path(sys.argv[1]),
    worktree_parent=Path(sys.argv[2]), baseline_revision=sys.argv[3], run_id=sys.argv[4],
    now=lambda:'2026-07-10T12:00:00Z', after_replace=crash)
"""
    completed = subprocess.run([sys.executable, "-c", script, str(root), str(tmp_path / "worktrees"), baseline, identity], check=False)
    assert completed.returncode == 78
    recovered = RunRepository.create(
        root, controller="sol", git_repository=root, worktree_parent=tmp_path / "worktrees",
        baseline_revision=baseline, run_id=identity, now=NOW,
    )
    assert recovered.run_id == identity
    assert not (root / ".soleresearch/git-run-intent.json").exists()
    assert _git(root, "branch", "--list", f"soleresearch/run/{identity}").strip()


def test_lifecycle_child_decision_and_ratification_tamper_fail_on_reopen(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    parent = _task(run, agent)
    child = _task(run, agent, parent_task_id=parent["task_id"])
    child_path = run.run_dir / "tasks/v1" / f"{child['task_id']}.json"
    tampered = json.loads(child_path.read_text())
    tampered["depth"] = 0
    child_path.write_text(json.dumps(tampered) + "\n")
    with pytest.raises(ProjectError, match="task/worker relational|child task depth"):
        RunRepository(root, run.run_id)


def test_state_gate_pending_decision_and_ratification_links_fail_closed(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    gate_path = run.run_dir / "gate.json"
    gate = json.loads(gate_path.read_text())
    gate["status"] = "paused"
    gate_path.write_text(json.dumps(gate) + "\n")
    with pytest.raises(ProjectError, match="lifecycle|active run gate"):
        RunRepository(root, run.run_id)

    human_dir = tmp_path / "human"
    human_dir.mkdir()
    human_root, _, human = _project(human_dir)
    human_run = RunRepository.create(human_root, controller="human", now=NOW)
    task = _task(human_run, human)
    imported = human_run.import_result(
        _result(task, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Review"}}]),
        controller_token=human, now=LATER,
    )
    gate_path = human_run.run_dir / "gate.json"
    gate = json.loads(gate_path.read_text())
    gate["pending_decisions"][0]["diff_id"] = "dif_" + "f" * 32
    gate_path.write_text(json.dumps(gate) + "\n")
    with pytest.raises(ProjectError, match="pending decision"):
        RunRepository(human_root, human_run.run_id)

    sol_dir = tmp_path / "sol"
    sol_dir.mkdir()
    sol_root, sol, _ = _project(sol_dir)
    sol_run = RunRepository.create(sol_root, controller="sol", now=NOW)
    task = _task(sol_run, sol)
    sol_run.import_result(
        _result(task, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Agent"}}]),
        controller_token=sol, now=LATER,
    )
    state_path = sol_run.run_dir / "state.json"
    state = json.loads(state_path.read_text())
    state["ratification_diff_ids"] = ["dif_" + "e" * 32]
    state_path.write_text(json.dumps(state) + "\n")
    with pytest.raises(ProjectError, match="ratification ledger"):
        RunRepository(sol_root, sol_run.run_id)


def test_git_isolation_refuses_dirty_or_internal_worktree_parent(tmp_path: Path) -> None:
    root, _, _ = _project(tmp_path)
    _git(root, "init")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    (root / "dirty.txt").write_text("dirty")
    with pytest.raises(ProjectError, match="dirty baseline"):
        RunRepository.create(root, controller="sol", git_repository=root, worktree_parent=tmp_path / "wt", baseline_revision=baseline, now=NOW)
    (root / "dirty.txt").unlink()
    with pytest.raises(ProjectError, match="outside"):
        RunRepository.create(root, controller="sol", git_repository=root, worktree_parent=root / "wt", baseline_revision=baseline, now=NOW)


def test_packaged_run_schemas_fail_closed() -> None:
    with pytest.raises(SchemaError, match="expected 1"):
        validate_document("run_state", {"schema_version": 2})


def test_task_cap_is_unconditional_while_prior_task_is_pending(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", envelope={**DEFAULT_ENVELOPE, "tasks": 1}, now=NOW)
    _task(run, agent)
    with pytest.raises(ProjectError, match="hard_cap:tasks"):
        _task(run, agent, role="second")
    assert run.status(now=NOW)["state"]["pause_reason"] == "hard_cap:tasks"


def test_abrupt_dispatch_recovers_entire_run_transaction(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    script = """
import os, sys
from pathlib import Path
from soleresearch.orchestration import RunRepository
def crash(index, path):
    if index == 2: os._exit(77)
RunRepository(Path(sys.argv[1]), sys.argv[2]).dispatch(
    role='reader', subquestion='q', evidence_strategy='read', selected_context={},
    allowed_capabilities=['read_source'], controller_token=sys.argv[3], now=lambda:'2026-07-10T12:00:00Z',
    after_replace=crash,
)
"""
    completed = subprocess.run([sys.executable, "-c", script, str(root), run.run_id, agent], check=False)
    assert completed.returncode == 77
    recovered = RunRepository(root, run.run_id)
    status = recovered.status(now=NOW)
    assert status["budget"]["consumed"]["tasks"] == 0
    assert status["pending_tasks"] == []
    assert [json.loads(line)["event_type"] for line in (run.run_dir / "events.jsonl").read_text().splitlines()] == ["run_created"]
    assert not (root / ".soleresearch/transaction.json").exists()


def test_create_transaction_rolls_back_all_run_files_on_injected_failure(tmp_path: Path) -> None:
    root, _, _ = _project(tmp_path)
    identity = "run_" + "a" * 32
    def fail(index: int, path: str) -> None:
        if index == 3:
            raise RuntimeError(path)
    with pytest.raises(RuntimeError):
        RunRepository.create(root, controller="sol", run_id=identity, now=NOW, after_replace=fail)
    assert not (root / "runs" / identity / "manifest.json").exists()
    assert not (root / ".soleresearch/transaction.json").exists()


def test_active_run_brokers_all_direct_canonical_writes(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    with pytest.raises(ProjectError, match="active run controller broker"):
        GraphRepository(root).propose([{"op": "add", "target": "node", "record": new_node("question", "Bypass", now=NOW)}], now=NOW)
    source_id = SourceRepository(root).all()[0]["source_id"]
    with pytest.raises(ProjectError, match="active run controller broker"):
        SourceRepository(root).set_reading_state(source_id, "read")
    with pytest.raises(ProjectError, match="active run controller broker"):
        EvidenceRepository(root).add(
            source_id=source_id, locator=locator("page", page=1), excerpt="x", paraphrase="x",
            stance="context", actor_type="agent", actor_id="bypass", method="manual",
        )
    with pytest.raises(ProjectError, match="active run controller broker"):
        DiscussionRepository(root).add(
            entity_type="source", entity_id=source_id, content="bypass", actor_type="agent", actor_id="bypass",
        )
    with pytest.raises(ProjectError, match="active run controller broker"):
        GraphRepository(root).reconcile(controller_token=agent, now=NOW)
    assert run.status(now=NOW)["state"]["status"] == "active"


def test_context_allowlist_size_secrets_worker_identity_and_required_ops(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    with pytest.raises(ProjectError, match="secrets or capabilities"):
        _task(run, agent, selected_context={"api_token": "secret"})
    with pytest.raises(ProjectError, match="65536"):
        _task(run, agent, selected_context={"text": "x" * 70_000})
    worker_id = "wrk_" + "b" * 32
    task = _task(run, agent, worker_id=worker_id)
    result = _result(task)
    result["completed_operations"] = []
    with pytest.raises(ProjectError, match="required operations"):
        run.import_result(result, controller_token=agent, now=LATER)
    with pytest.raises(ProjectError, match="immutable"):
        _task(run, agent, worker_id=worker_id, role="critic")


def test_result_source_provenance_and_relational_tamper_fail_closed(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, reserve_deep_sources=1)
    result = _result(task, sources=1)
    result["accessed_sources"][0]["source_hash"] = "sha256:forged"
    with pytest.raises(ProjectError, match="immutable version"):
        run.import_result(result, controller_token=agent, now=LATER)
    task_path = run.run_dir / "tasks/v1" / f"{task['task_id']}.json"
    tampered = json.loads(task_path.read_text())
    tampered["run_id"] = "run_" + "f" * 32
    task_path.write_text(json.dumps(tampered) + "\n")
    with pytest.raises(ProjectError, match="task ledger identity"):
        RunRepository(root, run.run_id)


def test_repeated_extensions_cannot_recurse_past_initial_envelope(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    run.extend({"tasks": 6}, actor="sol", reason="bounded first extension", controller_token=agent, now=NOW)
    with pytest.raises(ProjectError, match="cumulative tasks"):
        run.extend({"tasks": 5}, actor="sol", reason="would recurse", controller_token=agent, now=NOW)


def test_lock_path_symlink_and_invalid_run_id_fail_before_external_git(tmp_path: Path) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "cfg")
    root = tmp_path / "fresh"
    initialize_project(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".soleresearch").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectError, match="symbolic link"):
        with ProjectWriteLock(root):
            pass
    (root / ".soleresearch").unlink()
    with pytest.raises(ProjectError, match="run_id"):
        RunRepository.create(
            root, controller="sol", run_id="bad", git_repository=root,
            worktree_parent=tmp_path / "worktrees", baseline_revision="HEAD", now=NOW,
        )
    assert not (tmp_path / "worktrees").exists()


def test_run_artifacts_rebuild_into_identical_index_and_cli_surface() -> None:
    from soleresearch.cli import _parser

    help_text = _parser().format_help()
    for command in ("run", "resume", "budget", "gate", "status"):
        assert command in help_text


def test_run_artifacts_rebuild_into_identical_index(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    _task(run, agent)
    rebuild_index(root)
    first = read_index_snapshot(root)
    rebuild_index(root)
    assert read_index_snapshot(root) == first
    assert any(ledger.startswith(f"runs/{run.run_id}/") for ledger, _, _ in first)


def test_paused_run_holds_result_without_graph_apply_until_resume(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    result = _result(task, operations=[{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Held"}}])
    run.pause("manual review", controller_token=agent, now=NOW)
    held = run.import_result(result, controller_token=agent, now=LATER)
    assert held["status"] == "held_paused" and GraphRepository(root).nodes() == []
    run.resume(controller_token=agent, now=LATER)
    after_lease = lambda: "2026-07-10T13:00:00Z"
    assert run.import_result(result, controller_token=agent, now=after_lease)["graph_diff"]["status"] == "applied"


def test_result_scope_and_structural_completed_operations_fail_closed(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent)
    result = _result(task)
    result["used_capabilities"] = ["web_search"]
    with pytest.raises(ProjectError, match="capabilities outside"):
        run.import_result(result, controller_token=agent, now=LATER)
    result = _result(task)
    result["used_artifact_references"] = ["source:not-assigned"]
    with pytest.raises(ProjectError, match="artifact references"):
        run.import_result(result, controller_token=agent, now=LATER)
    result = _result(task, sources=1)
    result["accessed_sources"][0]["access_url"] = "https://unapproved.example/paper"
    with pytest.raises(ProjectError, match="domain outside"):
        run.import_result(result, controller_token=agent, now=LATER)
    result = _result(task)
    result["errors"] = []
    with pytest.raises(ProjectError, match="explicit no-result"):
        run.import_result(result, controller_token=agent, now=LATER)
    unbound = _task(run, agent, role="unbound", artifact_references=[], reserve_deep_sources=1)
    result = _result(unbound, sources=1)
    result["used_artifact_references"] = []
    with pytest.raises(ProjectError, match="local accessed source"):
        run.import_result(result, controller_token=agent, now=LATER)
    web = _task(
        run, agent, role="web", allowed_capabilities=["web_search"], allowed_domains=["example.org"],
        artifact_references=[], reserve_deep_sources=1,
    )
    result = _result(web, sources=1)
    result["used_artifact_references"] = []
    result["used_capabilities"] = ["web_search"]
    result["accessed_sources"][0]["access_url"] = "https://example.org/not-this-source"
    with pytest.raises(ProjectError, match="URL provenance"):
        run.import_result(result, controller_token=agent, now=LATER)


def test_result_evidence_exact_locator_excerpt_and_canonical_id(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, required_result_operations=["exact_locator_evidence", "source_provenance"], reserve_deep_sources=1)
    result = _result(task, sources=1, usage=0.0)
    source = SourceRepository(root).get(result["accessed_sources"][0]["source_id"])
    extraction = load_extraction(root, source["source_id"], source["content_hash"], source["source_version"])
    excerpt = next(line for line in extraction["text"].splitlines() if line.startswith("Exact inspected"))
    exact = locate_excerpt(extraction, excerpt)
    identity = canonical_json({"source_hash": source["content_hash"], "source_version": source["source_version"], "locator": exact, "excerpt": excerpt})
    evidence = {
        "evidence_id": "ev_" + __import__("hashlib").sha256(identity.encode()).hexdigest()[:24],
        "source_id": source["source_id"], "source_hash": source["content_hash"], "source_version": source["source_version"],
        "locator": exact, "excerpt": excerpt, "paraphrase": "Inspected statement", "stance": "context",
    }
    result["evidence"] = [evidence]
    result["completed_operations"] = ["exact_locator_evidence", "source_provenance"]
    assert run.import_result(result, controller_token=agent, now=LATER)["status"] == "accepted"


def test_result_rejects_source_aliases_in_access_and_evidence(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    records = SourceRepository(root).all()
    alias = "src_" + "a" * 24
    records[0]["aliases"].append(alias)
    write_jsonl(root / "sources/sources.jsonl", records)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, reserve_deep_sources=1)
    result = _result(task, sources=1)
    result["accessed_sources"][0]["source_id"] = alias
    with pytest.raises(ProjectError, match="canonical source IDs"):
        run.import_result(result, controller_token=agent, now=LATER)


def test_receipt_time_lease_expiry_cancel_and_requeue_are_durable(tmp_path: Path) -> None:
    root, agent, _ = _project(tmp_path)
    run = RunRepository.create(root, controller="sol", now=NOW)
    task = _task(run, agent, lease_minutes=1)
    result = _result(task)
    result["completed_at"] = NOW()
    late = lambda: "2026-07-10T12:02:00Z"
    with pytest.raises(ProjectError, match="receipt occurred after"):
        run.import_result(result, controller_token=agent, now=late)
    assert run.expire_leases(controller_token=agent, now=late)["expired_task_ids"] == [task["task_id"]]
    replacement = run.requeue_task(task["task_id"], controller_token=agent, now=late)
    assert replacement["requeue_of_task_id"] == task["task_id"]
    assert run.requeue_task(task["task_id"], controller_token=agent, now=late)["task_id"] == replacement["task_id"]
    with pytest.raises(ProjectError, match="terminal.*superseded"):
        run.import_result(result, controller_token=agent, now=late)
    run.cancel_task(replacement["task_id"], "superseded", controller_token=agent, now=late)
    replacement_result = _result(replacement)
    replacement_result["completed_at"] = late()
    with pytest.raises(ProjectError, match="terminal.*cancelled"):
        run.import_result(replacement_result, controller_token=agent, now=late)
    reopened = RunRepository(root, run.run_id)
    assert reopened.status(now=late)["pending_tasks"] == []
