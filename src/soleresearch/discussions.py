from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from soleresearch.errors import ProjectError, SchemaError
from soleresearch.graph import opaque_id
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.storage import confined_project_path, read_jsonl, write_jsonl
from soleresearch.writing import guarded_mutation


class DiscussionRepository:
    """Durable raw turns and explicitly promoted takeaways scoped to one entity."""

    def __init__(self, project_path: Path) -> None:
        self.root = project_path.resolve()
        load_project(self.root)
        self.directory = confined_project_path(self.root, "discussions")
        self.directory.mkdir(exist_ok=True)

    def _entity_exists(self, entity_type: str, entity_id: str) -> bool:
        ledgers = {
            "node": ("graph/nodes.jsonl", "node_id"),
            "edge": ("graph/edges.jsonl", "edge_id"),
            "source": ("sources/sources.jsonl", "source_id"),
            "evidence": ("evidence/evidence.jsonl", "evidence_id"),
        }
        if entity_type not in ledgers:
            return False
        path, key = ledgers[entity_type]
        return any(item.get(key) == entity_id for item in read_jsonl(self.root / path))

    def _path(self, discussion_id: str) -> Path:
        if not isinstance(discussion_id, str) or not discussion_id.startswith("dsc_") or len(discussion_id) != 36:
            raise ProjectError("invalid discussion_id")
        return confined_project_path(self.root, f"discussions/{discussion_id}.jsonl")

    def all(self, discussion_id: str) -> list[dict[str, Any]]:
        path = self._path(discussion_id)
        records = read_jsonl(path)
        seen: set[str] = set()
        scope: tuple[str, str] | None = None
        for line_number, record in enumerate(records, start=1):
            try:
                validate_document("discussion", record)
            except SchemaError as exc:
                raise ProjectError(f"invalid discussion {discussion_id}:{line_number}: {exc}") from exc
            if record["discussion_id"] != discussion_id:
                raise ProjectError(f"discussion record identity does not match filename: {discussion_id}")
            current_scope = (record["entity_type"], record["entity_id"])
            if scope is None:
                scope = current_scope
            elif current_scope != scope:
                raise ProjectError(f"discussion changes entity scope: {discussion_id}")
            if record["entry_id"] in seen:
                raise ProjectError(f"duplicate discussion entry_id: {record['entry_id']}")
            seen.add(record["entry_id"])
        return records

    def list(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("dsc_*.jsonl")):
            records = self.all(path.stem)
            if records:
                summaries.append(
                    {
                        "discussion_id": path.stem,
                        "entity_type": records[0]["entity_type"],
                        "entity_id": records[0]["entity_id"],
                        "entries": len(records),
                        "promoted_takeaways": sum(item["entry_type"] == "promoted_takeaway" for item in records),
                    }
                )
        return summaries

    @guarded_mutation
    def add(
        self,
        *,
        entity_type: str,
        entity_id: str,
        content: str,
        actor_type: str,
        actor_id: str,
        discussion_id: str | None = None,
        entry_type: str = "turn",
        promoted_node_id: str | None = None,
        now: Callable[[], str] = utc_now,
    ) -> dict[str, Any]:
        if not self._entity_exists(entity_type, entity_id):
            raise ProjectError(f"discussion entity does not exist: {entity_type} {entity_id}")
        identity = discussion_id or opaque_id("dsc")
        path = self._path(identity)
        records = read_jsonl(path) if path.exists() else []
        if records and (records[0]["entity_type"], records[0]["entity_id"]) != (entity_type, entity_id):
            raise ProjectError("discussion_id cannot change entity scope")
        if entry_type == "turn" and promoted_node_id is not None:
            raise ProjectError("raw discussion turns cannot promote graph state")
        if entry_type == "promoted_takeaway" and promoted_node_id is not None:
            if not self._entity_exists("node", promoted_node_id):
                raise ProjectError("promoted_node_id must reference a node accepted through an explicit graph diff")
        record = {
            "schema_version": SCHEMA_VERSION,
            "discussion_id": identity,
            "entry_id": opaque_id("ent"),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entry_type": entry_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "content": content.strip(),
            "promoted_node_id": promoted_node_id,
            "created_at": now(),
        }
        validate_document("discussion", record)
        records.append(record)
        write_jsonl(path, records)
        return record
