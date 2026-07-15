from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit

from soleresearch.controller import ControllerCapability, require_controller
from soleresearch.discussions import DiscussionRepository
from soleresearch.errors import ProjectError, SchemaError
from soleresearch.outline import ROOT_ANCHOR, parse_outline, text_hash
from soleresearch.orchestration import RunRepository
from soleresearch.project import load_project, project_status, utc_now
from soleresearch.refresh import read_refresh_signal
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.storage import canonical_json, confined_project_path, read_json, read_jsonl
from soleresearch.transactions import transactional_write
from soleresearch.writing import ProjectWriteLock, canonical_write_guard, run_write_scope

MAX_REQUEST_BYTES = 1_048_576
MAX_VIEW_RECORDS = 500
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
SOURCE_TITLE_FILE_SUFFIXES = {".doc", ".docx", ".dvi", ".eps", ".pdf", ".ps"}


@dataclass(frozen=True)
class UIConfig:
    project: Path
    workspace_root: Path | None
    host: str
    port: int
    edit_capability: ControllerCapability | None
    unsafe_non_loopback: bool
    allowed_hosts: tuple[str, ...]
    csrf_token: str


def _asset(name: str) -> bytes:
    return files("soleresearch").joinpath("ui", name).read_bytes()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return ("" if not records else "\n".join(canonical_json(item) for item in records) + "\n").encode("utf-8")


def _recorded_source_title_is_readable(value: object) -> bool:
    title = str(value or "").strip()
    lowered = title.casefold()
    if not title or lowered.startswith(("doi:", "http://", "https://", "src_", "microsoft word -")):
        return False
    return Path(title).suffix.casefold() not in SOURCE_TITLE_FILE_SUFFIXES


def _extracted_source_title(project_path: Path, source_id: str) -> str | None:
    extraction_dir = confined_project_path(project_path, Path(".soleresearch/extractions") / source_id)
    if not extraction_dir.is_dir():
        return None
    extraction_paths = sorted(
        (path for path in extraction_dir.glob("*.json") if path.is_file() and not path.is_symlink()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for extraction_path in extraction_paths:
        try:
            text = read_json(extraction_path).get("text", "")
        except (AttributeError, OSError, ProjectError):
            continue
        for line in str(text).splitlines():
            title = " ".join(line.split())
            if 4 <= len(title) <= 240 and any(character.isalpha() for character in title):
                return title
    return None


def _source_display_title(project_path: Path, source: dict[str, Any]) -> str:
    recorded = str(source.get("title") or "").strip()
    if _recorded_source_title_is_readable(recorded):
        return recorded
    return _extracted_source_title(project_path, source["source_id"]) or recorded or "Untitled source"


def workspace_projects(config: UIConfig) -> list[tuple[Path, dict[str, Any]]]:
    """Discover only immediate, non-symlinked projects under an explicit root."""
    if config.workspace_root is None:
        return [(config.project, load_project(config.project))]
    root = config.workspace_root
    if root.is_symlink() or not root.is_dir():
        raise ProjectError("workspace root must remain a real directory")
    projects: list[tuple[Path, dict[str, Any]]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        project_file = child / "project.json"
        if not project_file.exists():
            continue
        if child.is_symlink() or project_file.is_symlink() or not child.is_dir() or not project_file.is_file():
            raise ProjectError(f"workspace project entry is not a confined regular directory: {child.name}")
        projects.append((child.resolve(), load_project(child)))
    if not any(path == config.project for path, _project in projects):
        raise ProjectError("primary project is no longer present in the configured workspace root")
    identities = [project["project_id"] for _path, project in projects]
    if len(identities) != len(set(identities)):
        raise ProjectError("workspace project IDs must be unique")
    return projects


def workspace_state(config: UIConfig) -> dict[str, Any]:
    projects = []
    for path, project in workspace_projects(config):
        nodes = read_jsonl(path / "graph/nodes.jsonl")
        active_nodes = [item for item in nodes if not item.get("retired", False)]
        try:
            ui_state(path)
            available = True
            availability_error = None
        except (ProjectError, SchemaError, OSError, UnicodeDecodeError) as exc:
            available = False
            availability_error = str(exc)
        questions = [
            item["title"]
            for item in nodes
            if item.get("node_type") == "question" and item.get("parent_id") is None and not item.get("retired", False)
        ]
        projects.append(
            {
                "project_id": project["project_id"],
                "name": project["name"],
                "directory": path.name,
                "questions": questions,
                "node_count": sum(not item.get("retired", False) for item in nodes),
                "source_count": len(read_jsonl(path / "sources/sources.jsonl")),
                "evidence_count": len(read_jsonl(path / "evidence/evidence.jsonl")),
                "editable": path == config.project and config.edit_capability is not None,
                "available": available,
                "availability_error": availability_error,
                "preview": {
                    "nodes": [
                        {
                            "node_id": item["node_id"],
                            "parent_id": item.get("parent_id"),
                            "node_type": item["node_type"],
                            "title": item["title"],
                            "evidence_count": len(item.get("evidence_ids", [])),
                        }
                        for item in active_nodes[:MAX_VIEW_RECORDS]
                    ],
                    "total": len(active_nodes),
                    "truncated": len(active_nodes) > MAX_VIEW_RECORDS,
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "default_project_id": load_project(config.project)["project_id"],
        "workspace_name": config.workspace_root.name if config.workspace_root is not None else load_project(config.project)["name"],
        "projects": projects,
    }


def _bounded(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": records[:MAX_VIEW_RECORDS],
        "total": len(records),
        "truncated": len(records) > MAX_VIEW_RECORDS,
    }


def _discussion_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    directory = confined_project_path(root, "discussions")
    if directory.is_dir():
        for path in sorted(directory.glob("*.jsonl")):
            confined = confined_project_path(root, path.relative_to(root))
            if not confined.is_file():
                raise ProjectError(f"discussion ledger is not a regular file: {confined.relative_to(root)}")
            records.extend(read_jsonl(confined))
    return records


def _annotation_threads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project human turns into lifecycle threads without guessing agent intent."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["discussion_id"], []).append(record)
    threads: list[dict[str, Any]] = []
    for discussion_id, entries in grouped.items():
        # JSONL order is authoritative when multiple entries share one-second clocks.
        ordered = entries
        for index, entry in enumerate(ordered):
            if entry["entry_type"] != "turn" or entry["actor_type"] != "human":
                continue
            following: list[dict[str, Any]] = []
            for later in ordered[index + 1:]:
                if later["entry_type"] == "turn" and later["actor_type"] == "human":
                    break
                following.append(later)
            agent_entries = [item for item in following if item["actor_type"] in {"agent", "orchestrator"}]
            promoted = [item for item in following if item["entry_type"] == "promoted_takeaway"]
            if promoted:
                status = "takeaway_proposed"
                response = promoted[-1]
            elif agent_entries:
                status = "agent_responded"
                response = agent_entries[-1]
            else:
                status = "awaiting_agent"
                response = None
            threads.append(
                {
                    "discussion_id": discussion_id,
                    "annotation_entry_id": entry["entry_id"],
                    "entity_type": entry["entity_type"],
                    "entity_id": entry["entity_id"],
                    "content": entry["content"],
                    "status": status,
                    "response": None if response is None else response["content"],
                    "created_at": entry["created_at"],
                    "updated_at": entry["created_at"] if response is None else response["created_at"],
                }
            )
    return sorted(threads, key=lambda item: (item["updated_at"], item["annotation_entry_id"]), reverse=True)


def _coverage_nodes(
    nodes: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    conflicted = {item["node_id"] for item in conflicts if item["status"] == "queued" and item["node_id"]}
    projected: list[dict[str, Any]] = []
    for node in nodes:
        records = [evidence_by_id[item] for item in node.get("evidence_ids", []) if item in evidence_by_id]
        stances = [item["attestations"][-1]["stance"] for item in records if item.get("attestations")]
        if node.get("node_type") == "gap":
            state = "open_gap"
        elif node.get("node_id") in conflicted:
            state = "graph_conflict"
        elif "contradicts" in stances:
            state = "conflicting_evidence"
        elif not records:
            state = "unlinked"
        elif "supports" in stances:
            state = "linked_support"
        else:
            state = "linked_context"
        projected.append(
            {
                **node,
                "coverage": {
                    "state": state,
                    "evidence_count": len(records),
                    "source_count": len({item["source_id"] for item in records}),
                    "support_count": stances.count("supports"),
                    "contradiction_count": stances.count("contradicts"),
                    "qualification_count": stances.count("qualifies"),
                },
            }
        )
    return projected


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ProjectError(f"invalid UI observation clock: {value}") from exc
    if parsed.tzinfo != UTC:
        raise ProjectError("UI observation clock must be UTC")
    return parsed


def _run_records(root: Path, observed_at: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    runs = confined_project_path(root, "runs")
    if not runs.is_dir():
        return records
    for directory in sorted(runs.glob("run_*")):
        directory = confined_project_path(root, directory.relative_to(root))
        if not directory.is_dir() or not confined_project_path(root, (directory / "manifest.json").relative_to(root)).is_file():
            continue
        # Full immutable/relational validation, deliberately without status(),
        # elapsed persistence, cap pausing, or Git-intent recovery.
        RunRepository.open_read_only(root, directory.name)
        manifest = validate_document("run_manifest", read_json(confined_project_path(root, (directory / "manifest.json").relative_to(root))))
        state = validate_document("run_state", read_json(confined_project_path(root, (directory / "state.json").relative_to(root))))
        gate = validate_document("run_gate", read_json(confined_project_path(root, (directory / "gate.json").relative_to(root))))
        budget = validate_document("run_budget", read_json(confined_project_path(root, (directory / "budget.json").relative_to(root))))
        events = read_jsonl(confined_project_path(root, (directory / "events.jsonl").relative_to(root)))
        for event in events:
            validate_document("run_event", event)
        results_directory = confined_project_path(root, (directory / "results/v1").relative_to(root))
        tasks_directory = confined_project_path(root, (directory / "tasks/v1").relative_to(root))
        results_by_task: dict[str, dict[str, Any]] = {}
        for path in sorted(results_directory.glob("*.json")):
            confined = confined_project_path(root, path.relative_to(root))
            if not confined.is_file():
                raise ProjectError(f"run result is not a regular file: {confined.relative_to(root)}")
            result = validate_document("result", read_json(confined))
            results_by_task[result["task_id"]] = result
        result_task_ids = set(results_by_task)
        terminal_task_ids: set[str] = set()
        terminal_task_events: dict[str, str] = {}
        for event in events:
            if event["event_type"] in {"task_lease_expired", "task_cancelled", "task_requeued"} and isinstance(event["data"].get("task_id"), str):
                terminal_task_ids.add(event["data"]["task_id"])
                terminal_task_events[event["data"]["task_id"]] = event["event_type"]
        tasks = [
            validate_document("task", read_json(confined_project_path(root, path.relative_to(root))))
            for path in sorted(tasks_directory.glob("*.json"))
        ]
        terminal_task_ids.update(task["requeue_of_task_id"] for task in tasks if task["requeue_of_task_id"])
        pending_task_ids = [task["task_id"] for task in tasks if task["task_id"] not in result_task_ids and task["task_id"] not in terminal_task_ids]
        task_summaries = []
        for task in tasks:
            result = results_by_task.get(task["task_id"])
            if result is not None:
                task_status = "completed"
            elif task["task_id"] in terminal_task_events:
                task_status = terminal_task_events[task["task_id"]].removeprefix("task_")
            elif task["requeue_of_task_id"]:
                task_status = "requeued"
            else:
                task_status = "pending"
            task_summaries.append(
                {
                    "task_id": task["task_id"],
                    "worker_id": task["worker_id"],
                    "role": task["role"],
                    "depth": task["depth"],
                    "cycle": task["cycle"],
                    "subquestion": task["subquestion"],
                    "evidence_strategy": task["evidence_strategy"],
                    "allowed_capabilities": task["allowed_capabilities"],
                    "status": task_status,
                    "created_at": task["created_at"],
                    "result": None if result is None else {
                        "completed_at": result["completed_at"],
                        "used_capabilities": result["used_capabilities"],
                        "accessed_source_count": len(result["accessed_sources"]),
                        "evidence_count": len(result["evidence"]),
                        "rationale": result["rationale"],
                        "usage": result["usage"],
                        "errors": result["errors"],
                    },
                }
            )
        observed_elapsed_seconds = budget["elapsed_seconds"]
        if budget["active_since"] is not None:
            delta = (_parse_utc(observed_at) - _parse_utc(budget["active_since"])).total_seconds()
            if delta < 0:
                raise ProjectError("UI observation clock predates run active_since")
            observed_elapsed_seconds += delta
        remaining = {
            "cycles": max(0, budget["limits"]["cycles"] - budget["consumed"]["cycles"]),
            "tasks": max(0, budget["limits"]["tasks"] - budget["consumed"]["tasks"]),
            "deep_sources": max(0, budget["limits"]["deep_sources"] - budget["consumed"]["deep_sources"]),
            "agents": max(0, budget["limits"]["agents"] - 1 - len(pending_task_ids)),
            "max_depth": budget["limits"]["max_depth"],
            "minutes": max(0.0, budget["limits"]["minutes"] - observed_elapsed_seconds / 60.0),
            "provider_usage": {
                "unit": budget["limits"]["provider_usage"]["unit"],
                "remaining": max(0.0, budget["limits"]["provider_usage"]["ceiling"] - budget["consumed"]["provider_usage"]),
            },
        }
        records.append(
            {
                "run_id": manifest["run_id"],
                "created_at": manifest["created_at"],
                "controller": gate["controller"],
                "status": state["status"],
                "pause_reason": state["pause_reason"],
                "current_cycle": state["current_cycle"],
                "gate": gate,
                "budget": budget,
                "remaining": remaining,
                "pending_task_ids": pending_task_ids,
                "tasks": task_summaries,
                "events": events,
                "telemetry": {
                    "event_history_recorded": True,
                    "declared_capabilities_recorded": True,
                    "used_capabilities_recorded": True,
                    "individual_tool_calls_recorded": False,
                },
                "observed_at": observed_at,
                "observed_elapsed_seconds": observed_elapsed_seconds,
            }
        )
    return records


def _audit_timeline(
    human_edits: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
    migrations: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    graph_diffs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for item in human_edits:
        timeline.append({"at": item["created_at"], "type": "human_outline_edit", "id": item["event_id"], "actor": item["actor"]["subject"], "summary": item["semantic_summary"]})
    for item in reconciliations:
        timeline.append({"at": item["created_at"], "type": "outline_reconciliation", "id": item["event_id"], "actor": item["actor_id"], "summary": {"conflict_ids": item["conflict_ids"]}})
    for item in migrations:
        timeline.append({"at": item["recorded_at"], "type": "contract_migration", "id": item["migration_id"], "actor": "system", "summary": {"from_version": item["from_version"], "to_version": item["to_version"]}})
    for item in decisions:
        identity = item.get("decision_id") or item.get("id") or "decision"
        at = item.get("created_at") or item.get("recorded_at") or "0000-01-01T00:00:00Z"
        timeline.append({"at": at, "type": "decision", "id": identity, "actor": item.get("actor_id") or item.get("actor") or "unknown", "summary": item})
    for run in runs:
        for item in run["events"]:
            timeline.append({"at": item["created_at"], "type": "run:" + item["event_type"], "id": item["event_id"], "actor": item["actor"], "summary": {"run_id": item["run_id"], "reason": item["reason"], "data": item["data"]}})
    for item in graph_diffs:
        timeline.append({"at": item["created_at"], "type": "graph_diff:" + item["status"], "id": item["diff_id"], "actor": item["actor_id"], "summary": {"actor_type": item["actor_type"], "base_revision": item["base_revision"], "applied_revision": item["applied_revision"], "operations": len(item["operations"]), "reason": item["status_reason"]}})
    return sorted(timeline, key=lambda item: (item["at"], item["type"], item["id"]))


def ui_state(project_path: Path, *, now: Callable[[], str] = utc_now) -> dict[str, Any]:
    """Build a read-only view from authoritative files, never SQLite."""
    root = project_path.resolve()
    project = load_project(root)
    status = project_status(root)
    nodes = read_jsonl(root / "graph/nodes.jsonl")
    edges = read_jsonl(root / "graph/edges.jsonl")
    sources = read_jsonl(root / "sources/sources.jsonl")
    source_titles = {item["source_id"]: _source_display_title(root, item) for item in sources}
    source_urls = {item["source_id"]: item.get("canonical_url") for item in sources}
    evidence = [
        {
            **item,
            "source_title": source_titles.get(item["source_id"], item["source_id"]),
            "source_url": source_urls.get(item["source_id"]),
        }
        for item in read_jsonl(root / "evidence/evidence.jsonl")
    ]
    discussions = _discussion_records(root)
    conflicts = read_jsonl(root / "graph/conflicts.jsonl")
    projected_nodes = _coverage_nodes(nodes, evidence, conflicts)
    annotation_threads = _annotation_threads(discussions)
    gaps = [item for item in nodes if item.get("node_type") == "gap" and not item.get("retired", False)]
    human_edit_path = confined_project_path(root, "events/human-edits.jsonl")
    human_edits = read_jsonl(human_edit_path) if human_edit_path.is_file() else []
    for record in human_edits:
        validate_document("human_edit_event", record)
    reconciliations = read_jsonl(root / "events/reconciliations.jsonl")
    migrations = read_jsonl(root / "events/migrations.jsonl")
    decisions = read_jsonl(root / "decisions/decisions.jsonl")
    observed_at = now()
    _parse_utc(observed_at)
    runs = _run_records(root, observed_at)
    graph_diffs = read_jsonl(root / "graph/diffs.jsonl")
    audit = {
        "human_edits": _bounded(human_edits),
        "reconciliations": _bounded(reconciliations),
        "migrations": _bounded(migrations),
        "decisions": _bounded(decisions),
        "graph_diffs": _bounded(graph_diffs),
        "timeline": _bounded(_audit_timeline(human_edits, reconciliations, migrations, decisions, runs, graph_diffs)),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {"project_id": project["project_id"], "name": project["name"], "data_policy": project["data_policy"]},
        "outline": {
            "hash": text_hash((root / "outline.md").read_text(encoding="utf-8")),
            "dirty": status["outline_dirty"],
            "reconciliation_required": status["reconciliation_required"],
        },
        "views": {
            "nodes": _bounded(projected_nodes),
            "edges": _bounded(edges),
            "sources": _bounded(sources),
            "evidence": _bounded(evidence),
            "discussions": _bounded(discussions),
            "annotations": _bounded(annotation_threads),
            "proposals": _bounded([item for item in graph_diffs if item["status"] == "proposed"]),
            "gaps": _bounded(gaps),
            "conflicts": _bounded(conflicts),
            "runs": _bounded(runs),
            "audit": audit,
        },
        "staleness": {
            "outline_dirty": status["outline_dirty"],
            "index_present": status["index_present"],
            "index_required": False,
        },
        "errors": [],
    }


def _validate_outline_edit(root: Path, content: str) -> dict[str, dict[str, Any]]:
    if len(content.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ProjectError(f"outline exceeds {MAX_REQUEST_BYTES} UTF-8 bytes")
    if not content.endswith("\n"):
        raise ProjectError("outline must end with a newline")
    lines = content.splitlines()
    if lines.count("# Research Outline") != 1 or lines.count(ROOT_ANCHOR) != 1:
        raise ProjectError("outline must contain exactly one Research Outline heading and root anchor")
    nodes = read_jsonl(root / "graph/nodes.jsonl")
    active_ids = {item["node_id"] for item in nodes if not item.get("retired", False)}
    parsed = parse_outline(content, active_ids)
    if parsed.issues:
        details = "; ".join(item["details"] for item in parsed.issues[:5])
        raise ProjectError(f"outline anchors are invalid: {details}")
    if set(parsed.mappings) != active_ids:
        missing = sorted(active_ids - set(parsed.mappings))
        raise ProjectError("outline must preserve every active graph anchor: " + ", ".join(missing))
    return parsed.mappings


def _semantic_summary(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields = ("title", "body", "parent_id", "position")
    changed = sorted(
        identity for identity in set(before) | set(after)
        if before.get(identity) != after.get(identity)
    )
    counts = {
        field: sum(1 for identity in changed if before.get(identity, {}).get(field) != after.get(identity, {}).get(field))
        for field in fields
    }
    return {
        "changed_node_ids": changed,
        "title_changes": counts["title"],
        "body_changes": counts["body"],
        "parent_changes": counts["parent_id"],
        "position_changes": counts["position"],
    }


def _unfinished_run(root: Path) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    runs = confined_project_path(root, "runs")
    if not runs.is_dir():
        return None
    active: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for path in sorted(runs.glob("run_*/state.json")):
        path = confined_project_path(root, path.relative_to(root))
        state = validate_document("run_state", read_json(path))
        if state["run_id"] != path.parent.name:
            raise ProjectError("unfinished run state identity does not match confined path")
        if state["status"] != "finished":
            gate_path = confined_project_path(root, (path.parent / "gate.json").relative_to(root))
            gate = validate_document("run_gate", read_json(gate_path))
            if gate["run_id"] != state["run_id"]:
                raise ProjectError("unfinished run gate identity does not match state")
            active.append((state["run_id"], state, gate))
    if len(active) > 1:
        raise ProjectError("multiple unfinished runs prevent a safe UI edit")
    return active[0] if active else None


@contextmanager
def _human_edit_scope(root: Path) -> Iterator[None]:
    """Respect the run broker; allow edits only at a clean human gate."""
    with ProjectWriteLock(root):
        active = _unfinished_run(root)
        if active is None:
            with canonical_write_guard(root):
                yield
            return
        run_id, state, gate = active
        if state["status"] != "active" or gate["status"] != "clean" or gate["controller"] != "human":
            raise ProjectError(
                f"UI edit blocked by run {run_id}: status={state['status']}, gate={gate['status']}, "
                f"controller={gate['controller']}; reach a clean human-controlled gate first"
            )
        with run_write_scope(root, run_id):
            with canonical_write_guard(root):
                yield


def save_outline(
    project_path: Path,
    *,
    content: str,
    base_hash: str,
    capability: ControllerCapability,
) -> dict[str, Any]:
    root = project_path.resolve()
    project = load_project(root)
    if capability.authority != "human_accepted":
        raise ProjectError("UI outline editing requires the external human capability")
    with _human_edit_scope(root):
        current = (root / "outline.md").read_text(encoding="utf-8")
        current_hash = text_hash(current)
        if base_hash != current_hash:
            raise ProjectError("outline changed since it was loaded; reload before saving")
        before = _validate_outline_edit(root, current)
        after = _validate_outline_edit(root, content)
        after_hash = text_hash(content)
        if after_hash == current_hash:
            return {"schema_version": SCHEMA_VERSION, "saved": False, "outline_hash": current_hash, "event_id": None}
        event = validate_document(
            "human_edit_event",
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": "hed_" + uuid.uuid4().hex,
                "event_type": "human_outline_edit",
                "project_id": project["project_id"],
                "before_hash": current_hash,
                "after_hash": after_hash,
                "semantic_summary": _semantic_summary(before, after),
                "actor": {
                    "subject": capability.subject,
                    "authority": capability.authority,
                    "capability_id": capability.capability_id,
                },
                "created_at": utc_now(),
            },
        )
        event_path = root / "events/human-edits.jsonl"
        events = read_jsonl(event_path) if event_path.is_file() else []
        transactional_write(
            root,
            {
                "events/human-edits.jsonl": _jsonl_bytes([*events, event]),
                "outline.md": content.encode("utf-8"),
            },
        )
        return {"schema_version": SCHEMA_VERSION, "saved": True, "outline_hash": after_hash, "event_id": event["event_id"]}


def add_draft_annotation(
    project_path: Path,
    *,
    node_id: str,
    content: str,
    capability: ControllerCapability,
) -> dict[str, Any]:
    """Record human intent without allowing the browser to author canonical prose."""
    root = project_path.resolve()
    if capability.authority != "human_accepted":
        raise ProjectError("UI annotation requires the external human capability")
    note = content.strip()
    if not note:
        raise ProjectError("annotation content cannot be empty")
    if len(note) > 4_000:
        raise ProjectError("annotation content exceeds 4000 characters")
    with _human_edit_scope(root):
        discussions = DiscussionRepository(root)
        record = discussions.add(
            entity_type="node",
            entity_id=node_id,
            content=note,
            actor_type="human",
            actor_id=capability.subject,
            discussion_id=None,
        )
    return {"schema_version": SCHEMA_VERSION, "annotation": record}


class _Server(HTTPServer):
    config: UIConfig


class UIRequestHandler(BaseHTTPRequestHandler):
    server_version = "SoleResearchUI"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Request bodies, headers, and query values are deliberately never logged.
        print(f"ui {self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else '-'}")

    @property
    def config(self) -> UIConfig:
        return self.server.config  # type: ignore[attr-defined]

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send(self, status: int, body: bytes, content_type: str, *, etag: str | None = None) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if etag is not None:
            self.send_header("ETag", f'"{etag}"')
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, _json_bytes({"schema_version": SCHEMA_VERSION, "error": message}), "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        if len(self.headers.get_all("Host", [])) != 1:
            return False
        raw = self.headers.get("Host", "")
        if not raw or any(character.isspace() for character in raw) or "/" in raw or "\\" in raw:
            return False
        try:
            hostname = urlsplit("//" + raw).hostname
        except ValueError:
            return False
        if not hostname:
            return False
        return hostname.lower() in self.config.allowed_hosts

    def _selected_project(self) -> Path:
        values = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        unexpected = set(values) - {"project"}
        if unexpected:
            raise ProjectError(f"unsupported UI query parameter: {sorted(unexpected)[0]}")
        requested = values.get("project")
        if requested is None:
            return self.config.project
        if len(requested) != 1 or not requested[0]:
            raise ProjectError("project query must contain one project ID")
        matches = [path for path, project in workspace_projects(self.config) if project["project_id"] == requested[0]]
        if len(matches) != 1:
            raise ProjectError("requested project is not registered in this workspace")
        return matches[0]

    def _dispatch(self, method: str) -> None:
        if not self._valid_host():
            self._error(HTTPStatus.BAD_REQUEST, "invalid Host header for local UI")
            return
        path = urlsplit(self.path).path
        if method == "GET" and path == "/":
            bootstrap = json.dumps(
                {"schema_version": SCHEMA_VERSION, "annotation_enabled": self.config.edit_capability is not None, "csrf_token": self.config.csrf_token if self.config.edit_capability else None},
                ensure_ascii=True,
                separators=(",", ":"),
            ).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            html = _asset("index.html").replace(
                b"__SOLERESEARCH_BOOTSTRAP__",
                bootstrap.encode("ascii"),
            )
            self._send(HTTPStatus.OK, html, "text/html; charset=utf-8")
            return
        if method == "GET" and path in {"/assets/app.css", "/assets/layout.js", "/assets/app.js"}:
            content_type = "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"
            self._send(HTTPStatus.OK, _asset(path.rsplit("/", 1)[1]), content_type)
            return
        if method == "GET" and path == "/api/v1/workspace":
            self._send(HTTPStatus.OK, _json_bytes(workspace_state(self.config)), "application/json; charset=utf-8")
            return
        if method == "GET" and path == "/api/v1/state":
            self._send(HTTPStatus.OK, _json_bytes(ui_state(self._selected_project())), "application/json; charset=utf-8")
            return
        if method == "GET" and path == "/api/v1/completion":
            self._send(HTTPStatus.OK, _json_bytes(read_refresh_signal()), "application/json; charset=utf-8")
            return
        if method == "GET" and path == "/api/v1/outline":
            content = (self._selected_project() / "outline.md").read_text(encoding="utf-8")
            digest = text_hash(content)
            self._send(HTTPStatus.OK, _json_bytes({"schema_version": SCHEMA_VERSION, "content": content, "outline_hash": digest}), "application/json; charset=utf-8", etag=digest)
            return
        if method == "PUT" and path == "/api/v1/outline":
            self._put_outline()
            return
        if method == "POST" and path == "/api/v1/annotations":
            self._post_annotation()
            return
        if path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "unknown API endpoint")
        elif method != "GET":
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_GET(self) -> None:
        try:
            self._dispatch("GET")
        except (ProjectError, SchemaError, OSError, UnicodeDecodeError) as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))

    def do_PUT(self) -> None:
        try:
            self._dispatch("PUT")
        except (ProjectError, SchemaError) as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (OSError, UnicodeDecodeError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            self._dispatch("POST")
        except (ProjectError, SchemaError) as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (OSError, UnicodeDecodeError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def do_TRACE(self) -> None:
        self._dispatch("TRACE")

    def _put_outline(self) -> None:
        capability = self.config.edit_capability
        if capability is None:
            self._error(HTTPStatus.FORBIDDEN, "UI is read-only")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.config.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid CSRF token")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            self._error(HTTPStatus.LENGTH_REQUIRED, "valid Content-Length required")
            return
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            return
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "request must be valid UTF-8 JSON")
            return
        if not isinstance(value, dict) or set(value) != {"schema_version", "content", "base_hash"}:
            self._error(HTTPStatus.BAD_REQUEST, "outline request fields must be schema_version, content, and base_hash")
            return
        if value["schema_version"] != SCHEMA_VERSION or not isinstance(value["content"], str) or not isinstance(value["base_hash"], str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid outline request contract")
            return
        expected_etag = f'"{value["base_hash"]}"'
        if self.headers.get("If-Match") != expected_etag:
            self._error(HTTPStatus.PRECONDITION_FAILED, "If-Match must equal the quoted base_hash")
            return
        result = save_outline(
            self.config.project,
            content=value["content"],
            base_hash=value["base_hash"],
            capability=capability,
        )
        self._send(HTTPStatus.OK, _json_bytes(result), "application/json; charset=utf-8", etag=result["outline_hash"])

    def _post_annotation(self) -> None:
        capability = self.config.edit_capability
        if capability is None:
            self._error(HTTPStatus.FORBIDDEN, "UI annotations are disabled")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.config.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid CSRF token")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            self._error(HTTPStatus.LENGTH_REQUIRED, "valid Content-Length required")
            return
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            return
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "request must be valid UTF-8 JSON")
            return
        if not isinstance(value, dict) or set(value) != {"schema_version", "node_id", "content"}:
            self._error(HTTPStatus.BAD_REQUEST, "annotation request fields must be schema_version, node_id, and content")
            return
        if value["schema_version"] != SCHEMA_VERSION or not isinstance(value["node_id"], str) or not isinstance(value["content"], str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid annotation request contract")
            return
        project = self._selected_project()
        if project != self.config.project:
            self._error(HTTPStatus.FORBIDDEN, "selected workspace project is read-only in this server session")
            return
        result = add_draft_annotation(
            project,
            node_id=value["node_id"],
            content=value["content"],
            capability=capability,
        )
        self._send(HTTPStatus.CREATED, _json_bytes(result), "application/json; charset=utf-8")


def create_config(
    project_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    controller_token: str | None = None,
    edit: bool = False,
    unsafe_non_loopback: bool = False,
    allowed_hosts: tuple[str, ...] = (),
    workspace_root: Path | None = None,
) -> UIConfig:
    root = project_path.resolve()
    project = load_project(root)
    resolved_workspace: Path | None = None
    if workspace_root is not None:
        resolved_workspace = workspace_root.resolve()
        if workspace_root.is_symlink() or not resolved_workspace.is_dir():
            raise ProjectError("workspace root must be a real directory")
        if root.parent != resolved_workspace:
            raise ProjectError("primary project must be an immediate child of the workspace root")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ProjectError("UI port must be between 0 and 65535")
    is_loopback = host.lower() in LOOPBACK_HOSTS
    if not is_loopback and not unsafe_non_loopback:
        raise ProjectError("non-loopback UI binding requires --unsafe-non-loopback")
    if edit and not is_loopback:
        raise ProjectError("UI edit mode is disabled on non-loopback bindings in v1")
    normalized_hosts = set(LOOPBACK_HOSTS)
    for allowed in allowed_hosts:
        candidate = allowed.strip().lower()
        if not candidate or any(character.isspace() for character in candidate) or "/" in candidate or "\\" in candidate or ":" in candidate:
            raise ProjectError(f"invalid explicitly allowed UI Host: {allowed!r}")
        normalized_hosts.add(candidate)
    capability: ControllerCapability | None = None
    if edit:
        capability = require_controller(project["project_id"], controller_token)
        if capability.authority != "human_accepted":
            raise ProjectError("UI edit mode requires the project-bound external human capability")
    return UIConfig(
        project=root,
        workspace_root=resolved_workspace,
        host=host.lower(),
        port=port,
        edit_capability=capability,
        unsafe_non_loopback=unsafe_non_loopback,
        allowed_hosts=tuple(sorted(normalized_hosts)),
        csrf_token=secrets.token_urlsafe(32),
    )


def create_server(
    project_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    controller_token: str | None = None,
    edit: bool = False,
    unsafe_non_loopback: bool = False,
    allowed_hosts: tuple[str, ...] = (),
    workspace_root: Path | None = None,
) -> HTTPServer:
    config = create_config(
        project_path,
        host=host,
        port=port,
        controller_token=controller_token,
        edit=edit,
        unsafe_non_loopback=unsafe_non_loopback,
        allowed_hosts=allowed_hosts,
        workspace_root=workspace_root,
    )
    server = _Server((host, port), UIRequestHandler)
    server.config = UIConfig(**{**config.__dict__, "port": server.server_port})
    return server


def serve(
    project_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    controller_token: str | None = None,
    edit: bool = False,
    unsafe_non_loopback: bool = False,
    allowed_hosts: tuple[str, ...] = (),
    workspace_root: Path | None = None,
) -> None:
    server = create_server(
        project_path,
        host=host,
        port=port,
        controller_token=controller_token,
        edit=edit,
        unsafe_non_loopback=unsafe_non_loopback,
        allowed_hosts=allowed_hosts,
        workspace_root=workspace_root,
    )
    print(json.dumps({"schema_version": SCHEMA_VERSION, "listening": f"http://{host}:{server.server_port}", "edit_enabled": edit}, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is an expected operator shutdown, not an application failure.
        pass
    finally:
        server.server_close()
