from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from soleresearch.errors import ProjectError
from soleresearch.markdown import protected_markdown_lines
from soleresearch.schemas import SCHEMA_VERSION, validate_document

ANCHOR_RE = re.compile(r"^<!--\s*soleresearch:node\s+(nod_[0-9a-f]{32})\s*-->\s*$")
ROOT_ANCHOR = "<!-- soleresearch:anchor root -->"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MAX_OUTLINE_DEPTH = 5  # root nodes use H2; depth five uses H6.


def text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def graph_hash(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    import json

    payload = json.dumps(
        {"nodes": sorted(nodes, key=lambda item: item["node_id"]), "edges": sorted(edges, key=lambda item: item["edge_id"])},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParsedOutline:
    mappings: dict[str, dict[str, Any]]
    issues: list[dict[str, Any]]


def _clean_body(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def parse_outline(text: str, known_node_ids: set[str] | None = None) -> ParsedOutline:
    """Parse semantic heading/body/order state without inventing identities."""
    lines = text.splitlines()
    headings: list[dict[str, Any]] = []
    loose_anchors: list[tuple[int, str]] = []
    for index, (line, protected) in enumerate(protected_markdown_lines(lines)):
        if protected:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            headings.append({"line": index, "level": len(heading.group(1)), "title": heading.group(2).strip()})
        anchor = ANCHOR_RE.match(line)
        if anchor:
            loose_anchors.append((index, anchor.group(1)))
    mappings: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    anchored_lines: set[int] = set()
    sibling_counts: dict[str | None, int] = {}
    stack: list[tuple[int, str]] = []
    for heading_index, heading in enumerate(headings):
        if heading["level"] == 1 and heading["title"] == "Research Outline":
            continue
        next_heading_line = headings[heading_index + 1]["line"] if heading_index + 1 < len(headings) else len(lines)
        anchor_matches = [
            (line_number, node_id)
            for line_number, node_id in loose_anchors
            if heading["line"] < line_number < next_heading_line
        ]
        if not anchor_matches:
            issues.append({"type": "missing_anchor", "node_id": None, "anchor": None, "details": f"heading lacks stable anchor: {heading['title']}"})
            continue
        anchor_line, node_id = anchor_matches[0]
        anchored_lines.add(anchor_line)
        if len(anchor_matches) > 1:
            for extra_line, extra_id in anchor_matches[1:]:
                issues.append({"type": "orphan_anchor", "node_id": extra_id, "anchor": extra_id, "details": f"multiple anchors under heading {heading['title']} at line {extra_line + 1}"})
                anchored_lines.add(extra_line)
        if node_id in mappings:
            issues.append({"type": "duplicate_anchor", "node_id": node_id, "anchor": node_id, "details": f"duplicate anchor {node_id}"})
            continue
        if known_node_ids is not None and node_id not in known_node_ids:
            issues.append({"type": "orphan_anchor", "node_id": node_id, "anchor": node_id, "details": f"anchor references unknown node {node_id}"})
            continue
        while stack and stack[-1][0] >= heading["level"]:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        position = sibling_counts.get(parent_id, 0)
        sibling_counts[parent_id] = position + 1
        body_lines = lines[anchor_line + 1 : next_heading_line]
        mappings[node_id] = {
            "title": heading["title"],
            "body": _clean_body(body_lines),
            "parent_id": parent_id,
            "position": position,
        }
        stack.append((heading["level"], node_id))
    for line_number, node_id in loose_anchors:
        if line_number not in anchored_lines:
            issues.append({"type": "orphan_anchor", "node_id": node_id, "anchor": node_id, "details": f"anchor at line {line_number + 1} has no heading"})
    return ParsedOutline(mappings=mappings, issues=issues)


def render_outline(nodes: list[dict[str, Any]]) -> str:
    active = {item["node_id"]: item for item in nodes if not item["retired"]}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for node in active.values():
        parent = node["parent_id"] if node["parent_id"] in active else None
        children.setdefault(parent, []).append(node)
    for values in children.values():
        values.sort(key=lambda item: (item["position"], item["node_id"]))
    lines = ["# Research Outline", "", ROOT_ANCHOR, ""]

    def visit(node: dict[str, Any], level: int) -> None:
        if level > 6:
            raise ProjectError(f"outline graph exceeds maximum depth {MAX_OUTLINE_DEPTH}")
        lines.extend([f"{'#' * level} {node['title']}", "", f"<!-- soleresearch:node {node['node_id']} -->"])
        if node["body"]:
            lines.extend(["", node["body"]])
        lines.append("")
        for child in children.get(node["node_id"], []):
            visit(child, level + 1)

    for root in children.get(None, []):
        visit(root, 2)
    return "\n".join(lines).rstrip() + "\n"


def build_outline_meta(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], outline: str, *, graph_revision: int, revision: int
) -> dict[str, Any]:
    parsed = parse_outline(outline, {item["node_id"] for item in nodes if not item["retired"]})
    if parsed.issues:
        raise ProjectError("renderer produced an invalid anchored outline projection")
    digest = text_hash(outline)
    document = {
        "schema_version": 2,
        "graph_contract": "strict_v1",
        "revision": revision,
        "graph_revision": graph_revision,
        "graph_hash": graph_hash(nodes, edges),
        "outline_hash": digest,
        "base_outline_hash": digest,
        "mappings": parsed.mappings,
    }
    return validate_document("outline_meta", document)
