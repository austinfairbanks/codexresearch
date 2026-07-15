from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soleresearch.errors import MigrationRequired, ProjectError, SchemaError
from soleresearch.schemas import SCHEMA_VERSION, is_schema_version, validate_document
from soleresearch.storage import atomic_write_json, atomic_write_text, confined_project_path, read_json, read_jsonl
from soleresearch.outline import build_outline_meta, graph_hash, parse_outline, render_outline, text_hash
from soleresearch.controller import issue_controller_capabilities, remove_controller_capabilities

CANONICAL_JSONL = (
    "graph/nodes.jsonl",
    "graph/edges.jsonl",
    "graph/diffs.jsonl",
    "graph/conflicts.jsonl",
    "events/reconciliations.jsonl",
    "events/migrations.jsonl",
    "sources/sources.jsonl",
    "evidence/evidence.jsonl",
    "decisions/decisions.jsonl",
)

PROJECT_IGNORE = """# Rebuildable or private Sole Research state
.soleresearch/
exports/
source-copies/
source-cache/
extraction-cache/
transcripts/
runs/*/transcripts/
*.tmp
*.sqlite3
"""

OUTLINE = render_outline([])


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _project_document(name: str, data_policy: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": f"prj_{uuid.uuid4().hex}",
        "name": name,
        "created_at": utc_now(),
        "data_policy": data_policy,
    }


def initialize_project(path: Path, *, name: str | None = None, data_policy: str = "public_only") -> dict[str, Any]:
    """Construct a complete project beside its destination, then rename once."""
    destination = path.resolve()
    if destination.exists():
        raise ProjectError(f"refusing to initialize existing path: {destination}")
    if data_policy not in {"public_only", "local_private"}:
        raise ProjectError(f"unsupported data policy: {data_policy}")
    project_name = (name or destination.name).strip()
    if not project_name:
        raise ProjectError("project name must not be empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.init-", dir=destination.parent))
    try:
        document = _project_document(project_name, data_policy)
        validate_document("project", document)
        atomic_write_json(temporary / "project.json", document)
        for relative in CANONICAL_JSONL:
            atomic_write_text(temporary / relative, "")
        atomic_write_text(temporary / "events/human-edits.jsonl", "")
        atomic_write_text(temporary / "outline.md", OUTLINE)
        atomic_write_json(
            temporary / "outline.meta.json",
            build_outline_meta([], [], OUTLINE, graph_revision=0, revision=0),
        )
        atomic_write_text(temporary / "sources/reading-queue.md", "# Reading Queue\n")
        (temporary / "discussions").mkdir(parents=True)
        (temporary / "runs").mkdir(parents=True)
        atomic_write_text(temporary / "references/references.bib", "")
        atomic_write_text(temporary / "references/references.csl.json", "[]\n")
        atomic_write_text(temporary / ".gitignore", PROJECT_IGNORE)
        os.replace(temporary, destination)
        try:
            issue_controller_capabilities(document["project_id"])
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            remove_controller_capabilities(document["project_id"])
            raise
        return document
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_project(path: Path) -> dict[str, Any]:
    root = path.resolve()
    from soleresearch.transactions import recover_transaction

    try:
        document = validate_document("project", read_json(root / "project.json"))
    except SchemaError as exc:
        raise ProjectError(str(exc)) from exc
    meta = read_json(root / "outline.meta.json")
    version = meta.get("schema_version") if isinstance(meta, dict) else None
    if version == 1:
        try:
            validate_document("outline_meta", meta, version=1)
        except SchemaError as exc:
            raise ProjectError(f"invalid historical outline.meta.json: {exc}") from exc
        raise MigrationRequired(
            f"outline.meta.json schema_version 1 requires explicit migration; run: sole-research migrate {root}"
        )
    if version != 2:
        raise ProjectError(f"unsupported outline.meta.json schema_version: {version!r}; expected 2")
    recover_transaction(root)
    # Recovery may have rolled an interrupted explicit migration back to v1.
    meta = read_json(root / "outline.meta.json")
    version = meta.get("schema_version") if isinstance(meta, dict) else None
    if version == 1:
        raise MigrationRequired(
            f"outline.meta.json schema_version 1 requires explicit migration; run: sole-research migrate {root}"
        )
    if version != 2:
        raise ProjectError(f"unsupported outline.meta.json schema_version after recovery: {version!r}; expected 2")
    validate_project_layout(root)
    return document


def validate_project_layout(root: Path) -> None:
    required = [
        "outline.md",
        "outline.meta.json",
        *CANONICAL_JSONL,
        "references/references.bib",
        "references/references.csl.json",
    ]
    paths = {relative: confined_project_path(root, relative) for relative in required}
    missing = [relative for relative, path in paths.items() if not path.is_file()]
    if missing:
        raise ProjectError(f"project is missing required files: {', '.join(missing)}")
    try:
        outline_meta = validate_document("outline_meta", read_json(root / "outline.meta.json"))
    except SchemaError as exc:
        raise ProjectError(f"invalid outline.meta.json: {exc}") from exc
    csl = read_json(root / "references/references.csl.json")
    if not isinstance(csl, list):
        raise ProjectError("references.csl.json must contain an array")
    ledgers: dict[str, list[dict[str, Any]]] = {}
    for relative in CANONICAL_JSONL:
        records = read_jsonl(root / relative)
        ledgers[relative] = records
        identity_field = {
            "graph/nodes.jsonl": "node_id",
            "graph/edges.jsonl": "edge_id",
            "graph/diffs.jsonl": "diff_id",
            "graph/conflicts.jsonl": "conflict_id",
            "events/reconciliations.jsonl": "event_id",
            "events/migrations.jsonl": "migration_id",
            "sources/sources.jsonl": "source_id",
            "evidence/evidence.jsonl": "evidence_id",
            "decisions/decisions.jsonl": "decision_id",
        }[relative]
        seen_ids: set[str] = set()
        for line_number, record in enumerate(records, start=1):
            version = record.get("schema_version")
            if not is_schema_version(version):
                raise ProjectError(
                    f"unsupported record schema_version in {relative}:{line_number}: "
                    f"{version!r}; expected {SCHEMA_VERSION}"
                )
            kind = {
                "graph/nodes.jsonl": "node",
                "graph/edges.jsonl": "edge",
                "graph/diffs.jsonl": "graph_diff",
                "graph/conflicts.jsonl": "conflict",
                "events/reconciliations.jsonl": "reconciliation_event",
                "events/migrations.jsonl": "migration_event",
                "sources/sources.jsonl": "source",
                "evidence/evidence.jsonl": "evidence",
            }.get(relative)
            if kind is not None:
                try:
                    # Untyped nodes are readable only under the explicit legacy
                    # graph contract; strict v2 metadata never infers legacy state.
                    legacy_node = (
                        outline_meta["graph_contract"] == "legacy_untyped"
                        and
                        relative == "graph/nodes.jsonl"
                        and set(record) == {"schema_version", "node_id", "type"}
                        and record.get("type") in {"question", "concept", "evidence", "interpretation", "conclusion", "gap", "outline"}
                    )
                    if not legacy_node:
                        validate_document(kind, record)
                except SchemaError as exc:
                    raise ProjectError(f"invalid {relative}:{line_number}: {exc}") from exc
            identity = record.get(identity_field)
            if isinstance(identity, str) and identity:
                if identity in seen_ids:
                    raise ProjectError(f"duplicate {identity_field} in {relative}:{line_number}: {identity}")
                seen_ids.add(identity)
    human_edits_path = confined_project_path(root, "events/human-edits.jsonl")
    if human_edits_path.is_file():
        seen_edit_ids: set[str] = set()
        for line_number, record in enumerate(read_jsonl(human_edits_path), start=1):
            try:
                validate_document("human_edit_event", record)
            except SchemaError as exc:
                raise ProjectError(f"invalid events/human-edits.jsonl:{line_number}: {exc}") from exc
            if record["event_id"] in seen_edit_ids:
                raise ProjectError(f"duplicate event_id in events/human-edits.jsonl:{line_number}: {record['event_id']}")
            seen_edit_ids.add(record["event_id"])
    sources = ledgers["sources/sources.jsonl"]
    identities: dict[str, str] = {}
    for source in sources:
        source_id = source["source_id"]
        for identity in [source_id, *source["aliases"]]:
            if identity in identities:
                raise ProjectError(f"duplicate source identity {identity}: {identities[identity]} and {source_id}")
            identities[identity] = source_id
        if source_id in source["aliases"]:
            raise ProjectError(f"source aliases include canonical ID: {source_id}")
        version_pairs = {(item["content_hash"], item["source_version"]) for item in source["versions"]}
        if len(version_pairs) != len(source["versions"]):
            raise ProjectError(f"duplicate source version in {source_id}")
        if source["state"] in {"content_inspected", "local_copy_retained"}:
            if not source["content_hash"] or (source["content_hash"], source["source_version"]) not in version_pairs:
                raise ProjectError(f"inspected source lacks current immutable version: {source_id}")
        if source["state"] == "local_copy_retained" and not source["local_copy_path"]:
            raise ProjectError(f"retained source lacks local_copy_path: {source_id}")
        if source["identifiers"]["doi_key"] and source["identifiers"]["doi_key"] not in source["identifiers"]["doi_keys"]:
            raise ProjectError(f"source primary DOI key is not retained: {source_id}")
        if source["identifiers"]["arxiv_base"] and source["identifiers"]["arxiv_base"] not in source["identifiers"]["arxiv_bases"]:
            raise ProjectError(f"source primary arXiv base is not retained: {source_id}")
    evidence_ids: set[str] = set()
    for evidence in ledgers["evidence/evidence.jsonl"]:
        evidence_id = evidence["evidence_id"]
        if evidence_id in evidence_ids:
            raise ProjectError(f"duplicate evidence_id: {evidence_id}")
        evidence_ids.add(evidence_id)
        canonical_source = identities.get(evidence["source_id"])
        if canonical_source is None:
            raise ProjectError(f"evidence references unknown source: {evidence_id}")
        source = next(item for item in sources if item["source_id"] == canonical_source)
        if (evidence["source_hash"], evidence["source_version"]) not in {
            (item["content_hash"], item["source_version"]) for item in source["versions"]
        }:
            raise ProjectError(f"evidence references unknown source version: {evidence_id}")
        attestation_ids = [item["attestation_id"] for item in evidence["attestations"]]
        if len(attestation_ids) != len(set(attestation_ids)):
            raise ProjectError(f"duplicate evidence attestation: {evidence_id}")
        locator = evidence["locator"]
        locator_type = locator["type"]
        if locator_type == "page" and (
            not isinstance(locator["page"], int)
            or isinstance(locator["page"], bool)
            or any(locator[key] is not None for key in ("section", "paragraph", "figure", "table", "timestamp", "start_char", "end_char"))
        ):
            raise ProjectError(f"invalid page locator semantics: {evidence_id}")
        if locator_type in {"figure", "table", "timestamp"}:
            typed_value = locator[locator_type]
            associated = [locator["page"] is not None, locator["section"] is not None]
            if not isinstance(typed_value, str) or not typed_value or sum(associated) != 1:
                raise ProjectError(f"invalid {locator_type} locator semantics: {evidence_id}")
            if not isinstance(locator["start_char"], int) or not isinstance(locator["end_char"], int):
                raise ProjectError(f"invalid {locator_type} locator offsets: {evidence_id}")
    strict_nodes = [item for item in ledgers["graph/nodes.jsonl"] if "node_type" in item]
    node_ids = {item["node_id"] for item in ledgers["graph/nodes.jsonl"]}
    for node in strict_nodes:
        if node["parent_id"] is not None and node["parent_id"] not in node_ids:
            raise ProjectError(f"node references unknown parent: {node['node_id']}")
        missing_evidence = sorted(set(node["evidence_ids"]) - evidence_ids)
        if missing_evidence:
            raise ProjectError(f"node references unknown evidence: {node['node_id']}")
    for edge in ledgers["graph/edges.jsonl"]:
        if edge["source_node_id"] not in node_ids or edge["target_node_id"] not in node_ids:
            raise ProjectError(f"edge references unknown node: {edge['edge_id']}")
        if set(edge["evidence_ids"]) - evidence_ids:
            raise ProjectError(f"edge references unknown evidence: {edge['edge_id']}")
    if outline_meta["graph_contract"] == "strict_v1":
        if len(strict_nodes) != len(ledgers["graph/nodes.jsonl"]):
            raise ProjectError("strict graph contract contains an untyped legacy node")
        from soleresearch.graph import _validate_graph

        _validate_graph(strict_nodes, ledgers["graph/edges.jsonl"], evidence_ids)
        outline = (root / "outline.md").read_text(encoding="utf-8")
        if outline_meta["graph_hash"] != graph_hash(strict_nodes, ledgers["graph/edges.jsonl"]):
            raise ProjectError("outline metadata graph hash does not match canonical graph")
        if text_hash(outline) == outline_meta["outline_hash"]:
            parsed = parse_outline(outline, {item["node_id"] for item in strict_nodes if not item["retired"]})
            if parsed.issues or parsed.mappings != outline_meta["mappings"]:
                raise ProjectError("clean outline projection does not match outline metadata")
    discussions = confined_project_path(root, "discussions")
    if discussions.exists():
        for path in sorted(discussions.glob("*.jsonl")):
            path = confined_project_path(root, path.relative_to(root))
            if not path.is_file():
                raise ProjectError(f"discussion ledger is not a regular file: {path.relative_to(root)}")
            for line_number, record in enumerate(read_jsonl(path), start=1):
                try:
                    validate_document("discussion", record)
                except SchemaError as exc:
                    raise ProjectError(f"invalid {path.relative_to(root)}:{line_number}: {exc}") from exc


def project_status(path: Path) -> dict[str, Any]:
    root = path.resolve()
    document = load_project(root)
    counts = {
        "nodes": len(read_jsonl(root / "graph/nodes.jsonl")),
        "edges": len(read_jsonl(root / "graph/edges.jsonl")),
        "sources": len(read_jsonl(root / "sources/sources.jsonl")),
        "evidence": len(read_jsonl(root / "evidence/evidence.jsonl")),
        "decisions": len(read_jsonl(root / "decisions/decisions.jsonl")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": document["project_id"],
        "name": document["name"],
        "counts": counts,
        "graph_state": {
            "revision": validate_document("outline_meta", read_json(root / "outline.meta.json"))["graph_revision"],
            "diffs": len(read_jsonl(root / "graph/diffs.jsonl")),
            "conflicts": len(read_jsonl(root / "graph/conflicts.jsonl")),
            "discussions": sum(1 for item in (root / "discussions").glob("*.jsonl") if item.is_file()),
            "reconciliation_events": len(read_jsonl(root / "events/reconciliations.jsonl")),
            "migration_events": len(read_jsonl(root / "events/migrations.jsonl")),
        },
        "outline_dirty": text_hash((root / "outline.md").read_text(encoding="utf-8"))
        != validate_document("outline_meta", read_json(root / "outline.meta.json"))["outline_hash"],
        "reconciliation_required": text_hash((root / "outline.md").read_text(encoding="utf-8"))
        != validate_document("outline_meta", read_json(root / "outline.meta.json"))["outline_hash"],
        "index_present": (root / ".soleresearch/index.sqlite3").is_file(),
        "valid": True,
    }
