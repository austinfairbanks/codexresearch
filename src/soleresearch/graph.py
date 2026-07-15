from __future__ import annotations

import json
import re
import uuid
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from soleresearch.errors import ProjectError, SchemaError
from soleresearch.controller import require_controller
from soleresearch.markdown import protected_markdown_lines
from soleresearch.outline import MAX_OUTLINE_DEPTH, build_outline_meta, parse_outline, render_outline, text_hash
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.storage import canonical_json, confined_project_path, read_json, read_jsonl, write_jsonl
from soleresearch.transactions import recover_transaction, transactional_write
from soleresearch.writing import guarded_mutation

NODE_TYPES = ("question", "concept", "evidence", "interpretation", "conclusion", "gap", "outline")
MATURITY_STATES = ("exploratory", "developing", "supported", "contested", "gap", "retired")
AUTHORITY_STATES = ("proposed", "agent_accepted", "human_accepted", "rejected", "stale")
EDGE_TYPES = ("supports", "contradicts", "qualifies", "relates_to", "answers", "contains", "depends_on")
OPERATIONS = ("add", "update", "merge", "move", "link", "unlink", "retire", "restore")


def opaque_id(prefix: str) -> str:
    """Generate an opaque identity that cannot change with content or time."""
    return f"{prefix}_{uuid.uuid4().hex}"


def new_node(
    node_type: str,
    title: str,
    *,
    body: str = "",
    tags: list[str] | None = None,
    maturity: str = "exploratory",
    authority: str = "proposed",
    parent_id: str | None = None,
    position: int = 0,
    evidence_ids: list[str] | None = None,
    node_id: str | None = None,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    instant = now()
    record = {
        "schema_version": SCHEMA_VERSION,
        "node_id": node_id or opaque_id("nod"),
        "node_type": node_type,
        "title": title.strip(),
        "body": body.strip("\n"),
        "tags": sorted(set(tags or [])),
        "maturity": maturity,
        "prior_maturity": None,
        "authority": authority,
        "parent_id": parent_id,
        "position": position,
        "evidence_ids": sorted(set(evidence_ids or [])),
        "retired": False,
        "created_at": instant,
        "updated_at": instant,
    }
    return validate_document("node", record)


def new_edge(
    edge_type: str,
    source_node_id: str,
    target_node_id: str,
    *,
    evidence_ids: list[str] | None = None,
    authority: str = "proposed",
    edge_id: str | None = None,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    instant = now()
    record = {
        "schema_version": SCHEMA_VERSION,
        "edge_id": edge_id or opaque_id("edg"),
        "edge_type": edge_type,
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "evidence_ids": sorted(set(evidence_ids or [])),
        "authority": authority,
        "retired": False,
        "created_at": instant,
        "updated_at": instant,
    }
    return validate_document("edge", record)


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return ("" if not records else "\n".join(canonical_json(item) for item in records) + "\n").encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _strict_keys(operation: dict[str, Any], expected: set[str]) -> None:
    if set(operation) != expected:
        missing = sorted(expected - set(operation))
        extra = sorted(set(operation) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ProjectError("invalid graph operation: " + "; ".join(details))


def _derived_id(prefix: str, diff_id: str, index: int, purpose: str) -> str:
    digest = hashlib.sha256(f"{diff_id}:{index}:{purpose}".encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _normalize_operation(
    operation: dict[str, Any], *, now: Callable[[], str], diff_id: str, index: int
) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise ProjectError("graph operation must be an object")
    value = deepcopy(operation)
    value.setdefault("operation_id", _derived_id("op", diff_id, index, "operation"))
    op = value.get("op")
    if op not in OPERATIONS:
        raise ProjectError(f"unsupported graph operation: {op}")
    if op == "add":
        _strict_keys(value, {"operation_id", "op", "target", "record"})
        if value["target"] != "node":
            raise ProjectError("add targets nodes; use link for edges")
        raw = value["record"]
        if not isinstance(raw, dict):
            raise ProjectError("add operation record must be an object")
        if "node_id" not in raw:
            value["record"] = new_node(**raw, node_id=_derived_id("nod", diff_id, index, "node"), now=now)
        else:
            value["record"] = validate_document("node", raw)
    elif op == "link":
        _strict_keys(value, {"operation_id", "op", "record"})
        raw = value["record"]
        if not isinstance(raw, dict):
            raise ProjectError("link operation record must be an object")
        if "edge_id" not in raw:
            value["record"] = new_edge(**raw, edge_id=_derived_id("edg", diff_id, index, "edge"), now=now)
        else:
            value["record"] = validate_document("edge", raw)
    elif op == "update":
        _strict_keys(value, {"operation_id", "op", "target", "target_id", "changes"})
        if value["target"] not in {"node", "edge"} or not isinstance(value["changes"], dict) or not value["changes"]:
            raise ProjectError("update requires node/edge target and non-empty changes")
        immutable = {"schema_version", "node_id", "edge_id", "created_at"}
        if immutable.intersection(value["changes"]):
            raise ProjectError("update cannot change graph identity or creation metadata")
    elif op == "merge":
        _strict_keys(value, {"operation_id", "op", "source_id", "target_id"})
        if value["source_id"] == value["target_id"]:
            raise ProjectError("merge source and target must differ")
    elif op == "move":
        _strict_keys(value, {"operation_id", "op", "target_id", "parent_id", "position"})
        if not isinstance(value["position"], int) or isinstance(value["position"], bool) or value["position"] < 0:
            raise ProjectError("move position must be a non-negative integer")
    elif op in {"unlink"}:
        _strict_keys(value, {"operation_id", "op", "target_id"})
    else:
        _strict_keys(value, {"operation_id", "op", "target", "target_id"})
        if value["target"] not in {"node", "edge"}:
            raise ProjectError(f"{op} target must be node or edge")
    if not isinstance(value["operation_id"], str) or re.fullmatch(r"op_[0-9a-f]{32}", value["operation_id"]) is None:
        raise ProjectError("operation_id must be an opaque op_ identity")
    return value


def _validate_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], evidence_ids: set[str]) -> None:
    node_map: dict[str, dict[str, Any]] = {}
    for record in nodes:
        try:
            validate_document("node", record)
        except SchemaError as exc:
            raise ProjectError(str(exc)) from exc
        if record["node_id"] in node_map:
            raise ProjectError(f"duplicate node_id: {record['node_id']}")
        node_map[record["node_id"]] = record
        if record["retired"] != (record["maturity"] == "retired"):
            raise ProjectError(f"node retired flag and maturity disagree: {record['node_id']}")
        if record["retired"] and record["prior_maturity"] is None:
            raise ProjectError(f"retired node lacks prior maturity: {record['node_id']}")
        if not record["retired"] and record["prior_maturity"] is not None:
            raise ProjectError(f"active node retains stale prior maturity: {record['node_id']}")
        for line, protected in protected_markdown_lines(record["body"].splitlines()):
            if not protected and (re.match(r"^#{1,6}\s+", line) or "soleresearch:node" in line):
                raise ProjectError(f"node body contains reserved outline structure: {record['node_id']}")
        missing = sorted(set(record["evidence_ids"]) - evidence_ids)
        if missing:
            raise ProjectError(f"node {record['node_id']} references unknown evidence: {', '.join(missing)}")
        if record["node_type"] in {"evidence", "interpretation", "conclusion"} and not record["retired"] and not record["evidence_ids"]:
            raise ProjectError(f"active {record['node_type']} node requires exact-locator evidence: {record['node_id']}")
    structural: dict[str, set[str]] = {identity: set() for identity in node_map}
    for node in nodes:
        parent = node["parent_id"]
        if parent is not None and parent not in node_map:
            raise ProjectError(f"node {node['node_id']} references unknown parent: {parent}")
        if parent is not None:
            structural[parent].add(node["node_id"])
        seen = {node["node_id"]}
        depth = 1
        while parent is not None:
            if parent in seen:
                raise ProjectError(f"outline parent cycle at node: {node['node_id']}")
            seen.add(parent)
            parent = node_map[parent]["parent_id"]
            depth += 1
        if depth > MAX_OUTLINE_DEPTH:
            raise ProjectError(f"outline graph exceeds maximum depth {MAX_OUTLINE_DEPTH}: {node['node_id']}")
    edge_ids: set[str] = set()
    for record in edges:
        try:
            validate_document("edge", record)
        except SchemaError as exc:
            raise ProjectError(str(exc)) from exc
        if record["edge_id"] in edge_ids:
            raise ProjectError(f"duplicate edge_id: {record['edge_id']}")
        edge_ids.add(record["edge_id"])
        if record["source_node_id"] == record["target_node_id"] and not record["retired"]:
            raise ProjectError(f"active graph edge cannot be self-referential: {record['edge_id']}")
        for endpoint in (record["source_node_id"], record["target_node_id"]):
            if endpoint not in node_map:
                raise ProjectError(f"edge {record['edge_id']} references unknown node: {endpoint}")
        missing = sorted(set(record["evidence_ids"]) - evidence_ids)
        if missing:
            raise ProjectError(f"edge {record['edge_id']} references unknown evidence: {', '.join(missing)}")
        if record["edge_type"] in {"supports", "contradicts", "qualifies"} and not record["retired"] and not record["evidence_ids"]:
            raise ProjectError(f"{record['edge_type']} edge requires exact-locator evidence: {record['edge_id']}")
        if record["edge_type"] == "contains" and not record["retired"]:
            structural[record["source_node_id"]].add(record["target_node_id"])

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identity: str) -> None:
        if identity in visiting:
            raise ProjectError(f"structural graph cycle at node: {identity}")
        if identity in visited:
            return
        visiting.add(identity)
        for child in structural[identity]:
            visit(child)
        visiting.remove(identity)
        visited.add(identity)

    for identity in structural:
        visit(identity)


def _resequence(nodes: list[dict[str, Any]]) -> None:
    parents = {item["parent_id"] for item in nodes}
    for parent in parents:
        siblings = sorted(
            (item for item in nodes if item["parent_id"] == parent),
            key=lambda item: (item["position"], item["node_id"]),
        )
        for position, node in enumerate(siblings):
            node["position"] = position


def _apply_operations(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], operations: list[dict[str, Any]], *, authority: str, now: Callable[[], str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = deepcopy(nodes)
    edges = deepcopy(edges)
    instant = now()

    def mark_mutated(record: dict[str, Any]) -> None:
        if authority == "agent_accepted" and record.get("authority") == "human_accepted":
            record["authority"] = "agent_accepted"
        elif authority == "human_accepted":
            record["authority"] = "human_accepted"
        record["updated_at"] = instant

    for operation in operations:
        node_map = {item["node_id"]: item for item in nodes}
        edge_map = {item["edge_id"]: item for item in edges}
        op = operation["op"]
        if op == "add":
            record = deepcopy(operation["record"])
            if record["node_id"] in node_map:
                raise ProjectError(f"add target already exists: {record['node_id']}")
            if record["authority"] == "proposed":
                record["authority"] = authority
            nodes.append(record)
        elif op == "link":
            record = deepcopy(operation["record"])
            if record["edge_id"] in edge_map:
                raise ProjectError(f"link target already exists: {record['edge_id']}")
            if record["authority"] == "proposed":
                record["authority"] = authority
            edges.append(record)
        elif op == "update":
            mapping = node_map if operation["target"] == "node" else edge_map
            record = mapping.get(operation["target_id"])
            if record is None:
                raise ProjectError(f"update target does not exist: {operation['target_id']}")
            allowed = ({"title", "body", "tags", "maturity", "authority", "evidence_ids"} if operation["target"] == "node" else {"edge_type", "source_node_id", "target_node_id", "evidence_ids", "authority"})
            unknown = set(operation["changes"]) - allowed
            if unknown:
                raise ProjectError("update contains unsupported fields: " + ", ".join(sorted(unknown)))
            record.update(deepcopy(operation["changes"]))
            mark_mutated(record)
        elif op == "merge":
            source = node_map.get(operation["source_id"])
            target = node_map.get(operation["target_id"])
            if source is None or target is None:
                raise ProjectError("merge source and target must both exist")
            for node in nodes:
                if node["parent_id"] == source["node_id"]:
                    node["parent_id"] = target["node_id"]
                    mark_mutated(node)
            for edge in edges:
                if edge["source_node_id"] == source["node_id"]:
                    edge["source_node_id"] = target["node_id"]
                    mark_mutated(edge)
                if edge["target_node_id"] == source["node_id"]:
                    edge["target_node_id"] = target["node_id"]
                    mark_mutated(edge)
            target["evidence_ids"] = sorted(set(target["evidence_ids"] + source["evidence_ids"]))
            target["tags"] = sorted(set(target["tags"] + source["tags"]))
            mark_mutated(target)
            source["retired"] = True
            source["prior_maturity"] = source["maturity"]
            source["maturity"] = "retired"
            mark_mutated(source)
            # Endpoint rewrites may produce self-edges or duplicate semantic links.
            seen_edges: dict[tuple[Any, ...], dict[str, Any]] = {}
            for edge in sorted(edges, key=lambda item: item["edge_id"]):
                if edge["retired"]:
                    continue
                if edge["source_node_id"] == edge["target_node_id"]:
                    edge["retired"] = True
                    mark_mutated(edge)
                    continue
                signature = (
                    edge["edge_type"], edge["source_node_id"], edge["target_node_id"], tuple(edge["evidence_ids"])
                )
                if signature in seen_edges:
                    edge["retired"] = True
                    mark_mutated(edge)
                else:
                    seen_edges[signature] = edge
        elif op == "move":
            record = node_map.get(operation["target_id"])
            if record is None:
                raise ProjectError(f"move target does not exist: {operation['target_id']}")
            if operation["parent_id"] is not None and operation["parent_id"] not in node_map:
                raise ProjectError(f"move parent does not exist: {operation['parent_id']}")
            record["parent_id"] = operation["parent_id"]
            siblings = sorted(
                (item for item in nodes if item["parent_id"] == operation["parent_id"] and item["node_id"] != record["node_id"]),
                key=lambda item: (item["position"], item["node_id"]),
            )
            siblings.insert(min(operation["position"], len(siblings)), record)
            for position, sibling in enumerate(siblings):
                sibling["position"] = position
            mark_mutated(record)
        elif op == "unlink":
            record = edge_map.get(operation["target_id"])
            if record is None:
                raise ProjectError(f"unlink target does not exist: {operation['target_id']}")
            record["retired"] = True
            mark_mutated(record)
        else:
            mapping = node_map if operation["target"] == "node" else edge_map
            record = mapping.get(operation["target_id"])
            if record is None:
                raise ProjectError(f"{op} target does not exist: {operation['target_id']}")
            retiring = op == "retire"
            if record["retired"] == retiring:
                continue
            record["retired"] = retiring
            if operation["target"] == "node":
                if retiring:
                    record["prior_maturity"] = record["maturity"]
                    record["maturity"] = "retired"
                else:
                    if record["prior_maturity"] is None:
                        raise ProjectError(f"retired node lacks prior maturity: {record['node_id']}")
                    record["maturity"] = record["prior_maturity"]
                    record["prior_maturity"] = None
            mark_mutated(record)
    _resequence(nodes)
    return nodes, edges


class GraphRepository:
    def __init__(self, project_path: Path) -> None:
        self.root = project_path.resolve()
        recover_transaction(self.root)
        self.project = load_project(self.root)
        self.nodes_path = confined_project_path(self.root, "graph/nodes.jsonl")
        self.edges_path = confined_project_path(self.root, "graph/edges.jsonl")
        self.diffs_path = confined_project_path(self.root, "graph/diffs.jsonl")
        self.conflicts_path = confined_project_path(self.root, "graph/conflicts.jsonl")
        self.events_path = confined_project_path(self.root, "events/reconciliations.jsonl")

    def nodes(self) -> list[dict[str, Any]]:
        nodes = read_jsonl(self.nodes_path)
        _validate_graph(nodes, read_jsonl(self.edges_path), self._evidence_ids())
        return nodes

    def edges(self) -> list[dict[str, Any]]:
        edges = read_jsonl(self.edges_path)
        _validate_graph(read_jsonl(self.nodes_path), edges, self._evidence_ids())
        return edges

    def diffs(self) -> list[dict[str, Any]]:
        records = read_jsonl(self.diffs_path)
        for record in records:
            normalized = [
                _normalize_operation(item, now=utc_now, diff_id=record["diff_id"], index=index)
                for index, item in enumerate(record["operations"])
            ]
            if normalized != record["operations"]:
                raise ProjectError(f"graph diff contains a non-canonical operation: {record['diff_id']}")
            if len({item["operation_id"] for item in normalized}) != len(normalized):
                raise ProjectError(f"graph diff has duplicate operation_id: {record['diff_id']}")
        return records

    def conflicts(self) -> list[dict[str, Any]]:
        return read_jsonl(self.conflicts_path)

    def reconciliation_events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.events_path)

    @property
    def revision(self) -> int:
        meta = validate_document("outline_meta", read_json(self.root / "outline.meta.json"))
        return meta["graph_revision"]

    def _evidence_ids(self) -> set[str]:
        return {item["evidence_id"] for item in read_jsonl(self.root / "evidence/evidence.jsonl")}

    @guarded_mutation
    def propose(
        self,
        operations: list[dict[str, Any]],
        *,
        base_revision: int | None = None,
        actor_type: str = "agent",
        actor_id: str = "agent",
        diff_id: str | None = None,
        now: Callable[[], str] = utc_now,
    ) -> dict[str, Any]:
        if actor_type not in {"human", "agent", "orchestrator"}:
            raise ProjectError(f"unsupported diff actor_type: {actor_type}")
        identity = diff_id or opaque_id("dif")
        if re.fullmatch(r"dif_[0-9a-f]{32}", identity) is None:
            raise ProjectError("diff_id must be an opaque dif_ identity")
        existing = self.diffs()
        match = next((item for item in existing if item["diff_id"] == identity), None)
        instant = match["created_at"] if match is not None else now()
        fixed_now = lambda: instant
        normalized = [
            _normalize_operation(item, now=fixed_now, diff_id=identity, index=index)
            for index, item in enumerate(operations)
        ]
        if len({item["operation_id"] for item in normalized}) != len(normalized):
            raise ProjectError("graph diff has duplicate operation_id")
        if actor_type != "human":
            for operation in normalized:
                record = operation.get("record")
                changes = operation.get("changes", {})
                claimed = record.get("authority") if isinstance(record, dict) else changes.get("authority")
                if claimed == "human_accepted":
                    raise ProjectError("only a human-authored diff may claim human_accepted authority")
        base = self.revision if base_revision is None else base_revision
        if not isinstance(base, int) or isinstance(base, bool) or base < 0:
            raise ProjectError("base_revision must be a non-negative integer")
        document = {
            "schema_version": SCHEMA_VERSION,
            "diff_id": identity,
            "base_revision": base,
            "status": "proposed",
            "actor_type": actor_type,
            "actor_id": actor_id,
            "operations": normalized,
            "created_at": instant,
            "applied_revision": None,
            "applied_by": None,
            "applied_authority": None,
            "status_reason": None,
        }
        validate_document("graph_diff", document)
        if match is not None:
            comparable = ("base_revision", "actor_type", "actor_id", "operations", "created_at")
            if any(match[field] != document[field] for field in comparable):
                raise ProjectError(f"diff_id already exists with different content: {identity}")
            return match
        # Validate proposal semantics against its claimed base only when it is current.
        if base == self.revision:
            authority = "human_accepted" if actor_type == "human" else "agent_accepted"
            nodes, edges = _apply_operations(self.nodes(), self.edges(), normalized, authority=authority, now=fixed_now)
            _validate_graph(nodes, edges, self._evidence_ids())
        existing.append(document)
        write_jsonl(self.diffs_path, existing)
        return document

    @guarded_mutation
    def apply(
        self,
        diff_id: str,
        *,
        controller_token: str | None,
        now: Callable[[], str] = utc_now,
        after_replace: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        capability = require_controller(self.project["project_id"], controller_token)
        diffs = self.diffs()
        diff = next((item for item in diffs if item["diff_id"] == diff_id), None)
        if diff is None:
            raise ProjectError(f"unknown graph diff: {diff_id}")
        if diff["status"] == "applied":
            return diff
        if diff["status"] != "proposed":
            raise ProjectError(f"graph diff is not applicable: {diff_id} ({diff['status']})")
        current_revision = self.revision
        if diff["base_revision"] != current_revision:
            diff["status"] = "stale"
            diff["status_reason"] = f"base revision {diff['base_revision']} does not match current {current_revision}"
            write_jsonl(self.diffs_path, diffs)
            raise ProjectError(diff["status_reason"])
        meta = validate_document("outline_meta", read_json(self.root / "outline.meta.json"))
        current_outline = (self.root / "outline.md").read_text(encoding="utf-8")
        if text_hash(current_outline) != meta["outline_hash"]:
            raise ProjectError("outline has human edits; reconcile before applying graph proposals")
        authority = capability.authority
        if authority != "human_accepted":
            for operation in diff["operations"]:
                record = operation.get("record")
                changes = operation.get("changes", {})
                claimed = record.get("authority") if isinstance(record, dict) else changes.get("authority")
                if claimed == "human_accepted":
                    raise ProjectError("agent controller capability cannot apply human_accepted authority")
        nodes, edges = _apply_operations(self.nodes(), self.edges(), diff["operations"], authority=authority, now=now)
        _validate_graph(nodes, edges, self._evidence_ids())
        revision = current_revision + 1
        outline = render_outline(nodes)
        next_meta = build_outline_meta(nodes, edges, outline, graph_revision=revision, revision=meta["revision"] + 1)
        diff["status"] = "applied"
        diff["applied_revision"] = revision
        diff["applied_by"] = capability.subject
        diff["applied_authority"] = capability.authority
        diff["status_reason"] = None
        transactional_write(
            self.root,
            {
                "graph/nodes.jsonl": _jsonl_bytes(nodes),
                "graph/edges.jsonl": _jsonl_bytes(edges),
                "graph/diffs.jsonl": _jsonl_bytes(diffs),
                "outline.md": outline.encode("utf-8"),
                "outline.meta.json": _json_bytes(next_meta),
            },
            after_replace=after_replace,
        )
        return diff

    def _upsert_conflict(
        self,
        conflicts: list[dict[str, Any]],
        *,
        conflict_type: str,
        node_id: str | None,
        anchor: str | None,
        details: str,
        instant: str,
    ) -> dict[str, Any]:
        fingerprint = "sha256:" + hashlib.sha256(
            canonical_json({"type": conflict_type, "node_id": node_id, "anchor": anchor, "details": details}).encode("utf-8")
        ).hexdigest()
        identity = "cnf_" + fingerprint.removeprefix("sha256:")[:32]
        for prior in conflicts:
            if (
                prior["status"] == "queued"
                and prior["conflict_type"] == conflict_type
                and prior["node_id"] == node_id
                and prior["anchor"] == anchor
                and prior["conflict_id"] != identity
            ):
                prior.update(
                    {
                        "status": "superseded",
                        "resolution": f"superseded_by:{identity}",
                        "resolved_at": instant,
                        "resolution_actor": "reconciliation-system",
                        "resolution_revision": self.revision,
                    }
                )
        existing = next((item for item in conflicts if item["conflict_id"] == identity), None)
        if existing is not None:
            existing.update(
                {"status": "queued", "resolution": None, "resolved_at": None, "resolution_actor": None, "resolution_revision": None}
            )
            return existing
        record = {
            "schema_version": SCHEMA_VERSION,
            "conflict_id": identity,
            "fingerprint": fingerprint,
            "conflict_type": conflict_type,
            "node_id": node_id,
            "anchor": anchor,
            "details": details,
            "status": "queued",
            "resolution": None,
            "created_at": instant,
            "resolved_at": None,
            "resolution_actor": None,
            "resolution_revision": None,
        }
        validate_document("conflict", record)
        conflicts.append(record)
        return record

    def _event(
        self,
        *,
        actor_id: str,
        base_hash: str,
        current_hash: str,
        result_hash: str,
        base_revision: int,
        result_revision: int,
        changes: list[dict[str, Any]],
        affected: list[dict[str, Any]],
        conflict_ids: list[str],
        instant: str,
    ) -> dict[str, Any]:
        record = {
            "schema_version": SCHEMA_VERSION,
            "event_id": opaque_id("rec"),
            "actor_id": actor_id,
            "base_outline_hash": base_hash,
            "current_outline_hash": current_hash,
            "result_outline_hash": result_hash,
            "base_graph_revision": base_revision,
            "result_graph_revision": result_revision,
            "semantic_changes": changes,
            "affected_proposals": affected,
            "conflict_ids": sorted(set(conflict_ids)),
            "created_at": instant,
        }
        return validate_document("reconciliation_event", record)

    @guarded_mutation
    def reconcile(
        self,
        *,
        controller_token: str | None,
        now: Callable[[], str] = utc_now,
    ) -> dict[str, Any]:
        capability = require_controller(self.project["project_id"], controller_token)
        actor_id = capability.subject
        nodes = self.nodes()
        edges = self.edges()
        node_ids = {item["node_id"] for item in nodes if not item["retired"]}
        current_text = (self.root / "outline.md").read_text(encoding="utf-8")
        meta = validate_document("outline_meta", read_json(self.root / "outline.meta.json"))
        if text_hash(current_text) == meta["outline_hash"]:
            conflicts = self.conflicts()
            queued = [item for item in conflicts if item["status"] == "queued"]
            if not queued:
                return {"schema_version": SCHEMA_VERSION, "changed": False, "revision": self.revision, "conflicts": []}
            instant = now()
            for conflict in queued:
                conflict.update(
                    {"status": "resolved", "resolution": "outline_repaired", "resolved_at": instant, "resolution_actor": actor_id, "resolution_revision": self.revision}
                )
            events = self.reconciliation_events()
            event = self._event(
                actor_id=actor_id, base_hash=meta["base_outline_hash"], current_hash=meta["outline_hash"], result_hash=meta["outline_hash"],
                base_revision=self.revision, result_revision=self.revision, changes=[], affected=[],
                conflict_ids=[item["conflict_id"] for item in queued], instant=instant,
            )
            events.append(event)
            transactional_write(self.root, {"graph/conflicts.jsonl": _jsonl_bytes(conflicts), "events/reconciliations.jsonl": _jsonl_bytes(events)})
            return {"schema_version": SCHEMA_VERSION, "changed": False, "revision": self.revision, "conflicts": queued, "event_id": event["event_id"]}
        parsed = parse_outline(current_text, node_ids)
        conflicts = self.conflicts()
        surfaced_conflicts: list[dict[str, Any]] = []
        instant = now()
        issues = list(parsed.issues)
        if not any(item["type"] == "missing_anchor" for item in issues):
            for missing_node_id in sorted(node_ids - set(parsed.mappings)):
                issues.append(
                    {
                        "type": "missing_anchor",
                        "node_id": missing_node_id,
                        "anchor": missing_node_id,
                        "details": f"canonical node is missing from outline: {missing_node_id}",
                    }
                )
        if issues:
            for issue in issues:
                conflict = self._upsert_conflict(
                    conflicts,
                    conflict_type=issue["type"],
                    node_id=issue["node_id"],
                    anchor=issue["anchor"],
                    details=issue["details"],
                    instant=instant,
                )
                if conflict not in surfaced_conflicts:
                    surfaced_conflicts.append(conflict)
            events = self.reconciliation_events()
            event = self._event(
                actor_id=actor_id,
                base_hash=meta["base_outline_hash"],
                current_hash=text_hash(current_text),
                result_hash=text_hash(current_text),
                base_revision=self.revision,
                result_revision=self.revision,
                changes=[],
                affected=[],
                conflict_ids=[item["conflict_id"] for item in surfaced_conflicts],
                instant=instant,
            )
            events.append(event)
            transactional_write(
                self.root,
                {
                    "graph/conflicts.jsonl": _jsonl_bytes(conflicts),
                    "events/reconciliations.jsonl": _jsonl_bytes(events),
                },
            )
            return {"schema_version": SCHEMA_VERSION, "changed": False, "revision": self.revision, "conflicts": surfaced_conflicts, "event_id": event["event_id"]}
        base = meta["mappings"]
        changed_nodes: set[str] = set()
        node_map = {item["node_id"]: item for item in nodes}
        semantic_changes: list[dict[str, Any]] = []
        for node_id, human in parsed.mappings.items():
            previous = base.get(node_id)
            if not isinstance(previous, dict):
                conflict = self._upsert_conflict(
                    conflicts,
                    conflict_type="identity_conflict",
                    node_id=node_id,
                    anchor=node_id,
                    details=f"anchor {node_id} is absent from projection metadata",
                    instant=instant,
                )
                surfaced_conflicts.append(conflict)
                continue
            changes = {field: human[field] for field in ("title", "body", "parent_id", "position") if human[field] != previous.get(field)}
            if changes:
                semantic_changes.append(
                    {
                        "node_id": node_id,
                        "before": {field: previous.get(field) for field in ("title", "body", "parent_id", "position")},
                        "after": {field: human[field] for field in ("title", "body", "parent_id", "position")},
                    }
                )
                node_map[node_id].update(changes)
                node_map[node_id]["updated_at"] = instant
                changed_nodes.add(node_id)
        if surfaced_conflicts:
            events = self.reconciliation_events()
            event = self._event(
                actor_id=actor_id,
                base_hash=meta["base_outline_hash"], current_hash=text_hash(current_text), result_hash=text_hash(current_text),
                base_revision=self.revision, result_revision=self.revision, changes=[], affected=[],
                conflict_ids=[item["conflict_id"] for item in surfaced_conflicts], instant=instant,
            )
            events.append(event)
            transactional_write(self.root, {"graph/conflicts.jsonl": _jsonl_bytes(conflicts), "events/reconciliations.jsonl": _jsonl_bytes(events)})
            return {"schema_version": SCHEMA_VERSION, "changed": False, "revision": self.revision, "conflicts": surfaced_conflicts, "event_id": event["event_id"]}
        _resequence(nodes)
        _validate_graph(nodes, edges, self._evidence_ids())
        diffs = self.diffs()
        affected_proposals: list[dict[str, Any]] = []
        for diff in diffs:
            if diff["status"] != "proposed":
                continue
            touched = set()
            for operation in diff["operations"]:
                touched.update(
                    value for key, value in operation.items()
                    if key in {"target_id", "source_id", "parent_id"} and isinstance(value, str) and value.startswith("nod_")
                )
                record = operation.get("record")
                if isinstance(record, dict) and isinstance(record.get("node_id"), str):
                    touched.add(record["node_id"])
            overlap = sorted(touched & changed_nodes)
            if overlap:
                diff["status"] = "stale"
                diff["status_reason"] = "human outline edit won for: " + ", ".join(overlap)
                affected_proposals.append({"diff_id": diff["diff_id"], "before_status": "proposed", "after_status": "stale"})
                conflict = self._upsert_conflict(
                    conflicts,
                    conflict_type="concurrent_edit",
                    node_id=overlap[0],
                    anchor=overlap[0],
                    details=f"pending diff {diff['diff_id']} conflicted with human outline edits",
                    instant=instant,
                )
                conflict.update(
                    {"status": "resolved", "resolution": "human_wins", "resolved_at": instant, "resolution_actor": actor_id, "resolution_revision": self.revision + 1}
                )
                surfaced_conflicts.append(conflict)
        revision = self.revision + (1 if changed_nodes else 0)
        canonical_outline = render_outline(nodes)
        next_meta = build_outline_meta(nodes, edges, canonical_outline, graph_revision=revision, revision=meta["revision"] + 1)
        # A repaired valid outline resolves earlier open anchor/identity conflicts.
        for conflict in conflicts:
            if conflict["status"] == "queued":
                conflict.update(
                    {"status": "resolved", "resolution": "outline_repaired", "resolved_at": instant, "resolution_actor": actor_id, "resolution_revision": revision}
                )
        events = self.reconciliation_events()
        event = self._event(
            actor_id=actor_id,
            base_hash=meta["base_outline_hash"],
            current_hash=text_hash(current_text),
            result_hash=text_hash(canonical_outline),
            base_revision=self.revision,
            result_revision=revision,
            changes=semantic_changes,
            affected=affected_proposals,
            conflict_ids=[item["conflict_id"] for item in surfaced_conflicts],
            instant=instant,
        )
        events.append(event)
        transactional_write(
            self.root,
            {
                "graph/nodes.jsonl": _jsonl_bytes(nodes),
                "graph/diffs.jsonl": _jsonl_bytes(diffs),
                "graph/conflicts.jsonl": _jsonl_bytes(conflicts),
                "events/reconciliations.jsonl": _jsonl_bytes(events),
                "outline.md": canonical_outline.encode("utf-8"),
                "outline.meta.json": _json_bytes(next_meta),
            },
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "changed": bool(changed_nodes),
            "revision": revision,
            "changed_node_ids": sorted(changed_nodes),
            "conflicts": surfaced_conflicts,
            "event_id": event["event_id"],
        }
