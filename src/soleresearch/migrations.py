from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from soleresearch.controller import capability_paths, ensure_controller_capabilities
from soleresearch.errors import ProjectError, SchemaError
from soleresearch.outline import build_outline_meta, render_outline
from soleresearch.project import load_project
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.storage import canonical_json, confined_project_path, read_json, read_jsonl
from soleresearch.transactions import recover_transaction, transactional_write

_PHASE1_REQUIRED = (
    "project.json",
    "outline.md",
    "outline.meta.json",
    "graph/nodes.jsonl",
    "graph/edges.jsonl",
    "sources/sources.jsonl",
    "evidence/evidence.jsonl",
    "decisions/decisions.jsonl",
    "references/references.bib",
    "references/references.csl.json",
)
_NEW_EMPTY_LEDGERS = (
    "graph/diffs.jsonl",
    "graph/conflicts.jsonl",
    "events/reconciliations.jsonl",
    "events/human-edits.jsonl",
)


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return ("" if not records else "\n".join(canonical_json(item) for item in records) + "\n").encode("utf-8")


def _migrate_project(
    project_path: Path,
    *,
    after_replace: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Explicitly migrate the historical empty Phase-1 graph to outline metadata v2."""
    root = project_path.resolve()
    recover_transaction(root)
    missing = [relative for relative in _PHASE1_REQUIRED if not confined_project_path(root, relative).is_file()]
    if missing:
        raise ProjectError("Phase-1 migration is missing required files: " + ", ".join(missing))
    try:
        project = validate_document("project", read_json(root / "project.json"))
    except SchemaError as exc:
        raise ProjectError(str(exc)) from exc
    meta_path = confined_project_path(root, "outline.meta.json")
    meta_raw = read_json(meta_path)
    version = meta_raw.get("schema_version") if isinstance(meta_raw, dict) else None
    if version == 2:
        load_project(root)
        return {
            "schema_version": SCHEMA_VERSION,
            "migrated": False,
            "from_version": 2,
            "to_version": 2,
            "controller_capability_paths": {name: str(path) for name, path in capability_paths(project["project_id"]).items()},
        }
    if version != 1:
        raise ProjectError(f"unsupported outline.meta.json schema_version for migration: {version!r}")
    try:
        validate_document("outline_meta", meta_raw, version=1)
    except SchemaError as exc:
        raise ProjectError(f"invalid historical outline.meta.json: {exc}") from exc
    nodes = read_jsonl(confined_project_path(root, "graph/nodes.jsonl"))
    edges = read_jsonl(confined_project_path(root, "graph/edges.jsonl"))
    if nodes or edges:
        raise ProjectError(
            "Phase-1 graph is nonempty and untyped; automatic migration is unsafe and requires manual graph conversion"
        )
    for relative in (*_NEW_EMPTY_LEDGERS, "events/migrations.jsonl"):
        path = confined_project_path(root, relative)
        if path.exists() and path.read_bytes():
            raise ProjectError(f"migration target already contains data: {relative}")

    before = meta_path.read_bytes()
    canonical_outline = render_outline([])
    meta_v2 = build_outline_meta([], [], canonical_outline, graph_revision=0, revision=meta_raw["revision"])
    meta_bytes = _json_bytes(meta_v2)
    before_hash = _digest(before)
    after_hash = _digest(meta_bytes)
    migration_id = "mig_" + hashlib.sha256(
        f"{project['project_id']}:{before_hash}:outline-meta-v1-v2".encode("utf-8")
    ).hexdigest()[:32]
    event = validate_document(
        "migration_event",
        {
            "schema_version": SCHEMA_VERSION,
            "migration_id": migration_id,
            "project_id": project["project_id"],
            "document": "outline.meta.json",
            "from_version": 1,
            "to_version": 2,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "graph_contract": "strict_v1",
            # Deterministic provenance: the immutable project creation instant.
            "recorded_at": project["created_at"],
        },
    )
    ensure_controller_capabilities(project["project_id"])
    updates = {relative: b"" for relative in _NEW_EMPTY_LEDGERS}
    updates.update(
        {
            "events/migrations.jsonl": _jsonl_bytes([event]),
            "outline.meta.json": meta_bytes,
        }
    )
    transactional_write(root, updates, after_replace=after_replace)
    load_project(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "migrated": True,
        "migration_id": migration_id,
        "from_version": 1,
        "to_version": 2,
        "graph_contract": "strict_v1",
        "outline_preserved": True,
        "controller_capability_paths": {name: str(path) for name, path in capability_paths(project["project_id"]).items()},
    }


def migrate_project(
    project_path: Path,
    *,
    after_replace: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    return _migrate_project(project_path, after_replace=after_replace)
