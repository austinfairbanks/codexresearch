from __future__ import annotations

import json
from pathlib import Path

import pytest

from soleresearch.discussions import DiscussionRepository
from soleresearch.controller import CONFIG_HOME_ENV, capability_paths
from soleresearch.errors import ProjectError
from soleresearch.evidence import EvidenceRepository, locator
from soleresearch.graph import GraphRepository, new_edge, new_node
from soleresearch.indexing import read_index_snapshot, rebuild_index
from soleresearch.outline import parse_outline, render_outline
from soleresearch.project import initialize_project, load_project
from soleresearch.sources import import_local_document

NOW = lambda: "2026-07-10T12:00:00Z"


def _project(tmp_path: Path) -> Path:
    import os

    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "controller-config")
    root = tmp_path / "project"
    initialize_project(root)
    return root


def _evidence(root: Path, tmp_path: Path) -> str:
    note = tmp_path / "note.md"
    note.write_text("# Result\n\nMeasured result.\n", encoding="utf-8")
    source, _ = import_local_document(root, note, kind="markdown", now=NOW)
    evidence, _ = EvidenceRepository(root).add(
        source_id=source["source_id"],
        locator=locator("section", section="Result"),
        excerpt="Measured result.",
        paraphrase="A result was measured.",
        stance="supports",
        actor_type="human",
        actor_id="reader",
        method="manual",
        now=NOW,
    )
    return evidence["evidence_id"]


def _apply(repository: GraphRepository, operations: list[dict]) -> dict:
    diff = repository.propose(operations, now=NOW)
    record = json.loads(capability_paths(repository.project["project_id"])["agent"].read_text())
    token = record["token"]
    return repository.apply(diff["diff_id"], controller_token=token, now=NOW)


def test_opaque_ids_do_not_derive_from_text_or_clock() -> None:
    first = new_node("question", "Same title", now=NOW)
    second = new_node("question", "Same title", now=NOW)
    assert first["node_id"] != second["node_id"]
    assert first["node_id"].startswith("nod_")


def test_add_update_move_link_unlink_retire_restore_merge(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    a = new_node("question", "Question", now=NOW)
    b = new_node("concept", "First", position=1, now=NOW)
    c = new_node("concept", "Second", position=2, now=NOW)
    _apply(repository, [
        {"op": "add", "target": "node", "record": a},
        {"op": "add", "target": "node", "record": b},
        {"op": "add", "target": "node", "record": c},
    ])
    edge = new_edge("relates_to", b["node_id"], a["node_id"], now=NOW)
    _apply(repository, [
        {"op": "update", "target": "node", "target_id": b["node_id"], "changes": {"body": "Body"}},
        {"op": "move", "target_id": c["node_id"], "parent_id": a["node_id"], "position": 0},
        {"op": "link", "record": edge},
    ])
    assert next(item for item in repository.nodes() if item["node_id"] == b["node_id"])["body"] == "Body"
    assert next(item for item in repository.nodes() if item["node_id"] == c["node_id"])["parent_id"] == a["node_id"]
    _apply(repository, [{"op": "unlink", "target_id": edge["edge_id"]}])
    assert repository.edges()[0]["retired"] is True
    _apply(repository, [{"op": "restore", "target": "edge", "target_id": edge["edge_id"]}])
    assert repository.edges()[0]["retired"] is False
    _apply(repository, [{"op": "retire", "target": "node", "target_id": b["node_id"]}])
    assert next(item for item in repository.nodes() if item["node_id"] == b["node_id"])["maturity"] == "retired"
    _apply(repository, [{"op": "restore", "target": "node", "target_id": b["node_id"]}])
    _apply(repository, [{"op": "merge", "source_id": b["node_id"], "target_id": c["node_id"]}])
    assert next(item for item in repository.nodes() if item["node_id"] == b["node_id"])["retired"] is True


def test_broken_refs_cycles_and_evidence_relations_fail_closed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    with pytest.raises(ProjectError, match="exact-locator evidence"):
        repository.propose([{"op": "add", "target": "node", "record": new_node("conclusion", "Unsupported", now=NOW)}], now=NOW)
    with pytest.raises(ProjectError, match="unknown node"):
        repository.propose([{"op": "link", "record": new_edge("relates_to", "nod_" + "0" * 32, "nod_" + "1" * 32, now=NOW)}], now=NOW)
    evidence_id = _evidence(root, tmp_path)
    conclusion = new_node("conclusion", "Supported", evidence_ids=[evidence_id], now=NOW)
    parent = new_node("concept", "Parent", now=NOW)
    _apply(repository, [{"op": "add", "target": "node", "record": parent}, {"op": "add", "target": "node", "record": conclusion}])
    with pytest.raises(ProjectError, match="parent cycle"):
        repository.propose([
            {"op": "move", "target_id": parent["node_id"], "parent_id": conclusion["node_id"], "position": 0},
            {"op": "move", "target_id": conclusion["node_id"], "parent_id": parent["node_id"], "position": 0},
        ], now=NOW)


def test_stale_base_rejected_and_apply_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    first = repository.propose([{"op": "add", "target": "node", "record": new_node("question", "One", now=NOW)}], base_revision=0, now=NOW)
    second = repository.propose([{"op": "add", "target": "node", "record": new_node("question", "Two", now=NOW)}], base_revision=0, now=NOW)
    token = json.loads(capability_paths(repository.project["project_id"])["agent"].read_text())["token"]
    applied = repository.apply(first["diff_id"], controller_token=token, now=NOW)
    assert repository.apply(first["diff_id"], controller_token=token, now=NOW) == applied
    with pytest.raises(ProjectError, match="does not match"):
        repository.apply(second["diff_id"], controller_token=token, now=NOW)
    assert next(item for item in repository.diffs() if item["diff_id"] == second["diff_id"])["status"] == "stale"
    with pytest.raises(ProjectError, match="controller capability"):
        GraphRepository(root).apply(first["diff_id"], controller_token=None)


def test_outline_round_trip_and_human_text_order_wins(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    first = new_node("question", "Original question", body="Original body", now=NOW)
    second = new_node("concept", "Concept", position=1, now=NOW)
    _apply(repository, [{"op": "add", "target": "node", "record": first}, {"op": "add", "target": "node", "record": second}])
    rendered = render_outline(repository.nodes())
    assert parse_outline(rendered, {first["node_id"], second["node_id"]}).issues == []
    pending = repository.propose([
        {"op": "update", "target": "node", "target_id": first["node_id"], "changes": {"title": "Agent title"}}
    ], now=NOW)
    edited = rendered.replace("## Original question", "## Human question").replace("Original body", "Human body")
    # Reorder the complete anchored sections.
    first_start = edited.index("## Human question")
    second_start = edited.index("## Concept")
    prefix = edited[:first_start]
    first_section = edited[first_start:second_start]
    second_section = edited[second_start:]
    (root / "outline.md").write_text(prefix + second_section.rstrip() + "\n\n" + first_section, encoding="utf-8")
    token = json.loads(capability_paths(repository.project["project_id"])["agent"].read_text())["token"]
    result = repository.reconcile(controller_token=token, now=NOW)
    assert result["changed"] is True
    nodes = repository.nodes()
    updated = next(item for item in nodes if item["node_id"] == first["node_id"])
    # Markdown authorship changes semantics but is not an acceptance gate.
    assert (updated["title"], updated["body"], updated["authority"]) == ("Human question", "Human body", "agent_accepted")
    assert next(item for item in repository.diffs() if item["diff_id"] == pending["diff_id"])["status"] == "stale"
    assert any(item["resolution"] == "human_wins" for item in repository.conflicts())


@pytest.mark.parametrize("edit", ["duplicate", "orphan", "missing"])
def test_anchor_identity_problems_queue_without_graph_mutation(tmp_path: Path, edit: str) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    node = new_node("question", "Question", now=NOW)
    _apply(repository, [{"op": "add", "target": "node", "record": node}])
    before = (root / "graph/nodes.jsonl").read_bytes()
    outline = (root / "outline.md").read_text(encoding="utf-8")
    anchor = f"<!-- soleresearch:node {node['node_id']} -->"
    if edit == "duplicate":
        outline += f"\n## Duplicate\n\n{anchor}\n"
    elif edit == "orphan":
        outline += f"\n<!-- soleresearch:node nod_{'f' * 32} -->\n"
    else:
        outline = outline.replace(anchor, "")
    (root / "outline.md").write_text(outline, encoding="utf-8")
    token = json.loads(capability_paths(repository.project["project_id"])["agent"].read_text())["token"]
    result = repository.reconcile(controller_token=token, now=NOW)
    assert result["changed"] is False
    assert result["conflicts"]
    assert (root / "graph/nodes.jsonl").read_bytes() == before


def test_discussion_turns_and_takeaways_never_accept_graph_state(tmp_path: Path) -> None:
    root = _project(tmp_path)
    graph = GraphRepository(root)
    node = new_node("question", "Question", now=NOW)
    _apply(graph, [{"op": "add", "target": "node", "record": node}])
    discussions = DiscussionRepository(root)
    turn = discussions.add(entity_type="node", entity_id=node["node_id"], content="Raw thought", actor_type="human", actor_id="reader", now=NOW)
    takeaway = discussions.add(entity_type="node", entity_id=node["node_id"], content="Promote this", actor_type="agent", actor_id="critic", discussion_id=turn["discussion_id"], entry_type="promoted_takeaway", now=NOW)
    assert takeaway["promoted_node_id"] is None
    assert graph.revision == 1
    assert len(discussions.all(turn["discussion_id"])) == 2
    with pytest.raises(ProjectError, match="does not exist"):
        discussions.add(entity_type="node", entity_id="nod_" + "0" * 32, content="No", actor_type="human", actor_id="reader", now=NOW)


def test_transaction_rolls_back_and_recovers_after_process_crash(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    node = new_node("question", "Question", now=NOW)
    diff = repository.propose([{"op": "add", "target": "node", "record": node}], now=NOW)
    before = {name: (root / name).read_bytes() for name in ("graph/nodes.jsonl", "graph/edges.jsonl", "graph/diffs.jsonl", "outline.md", "outline.meta.json")}

    class Crash(BaseException):
        pass

    def crash(index: int, _path: str) -> None:
        if index == 2:
            raise Crash()

    with pytest.raises(Crash):
        repository.apply(diff["diff_id"], controller_token=json.loads(capability_paths(repository.project["project_id"])["agent"].read_text())["token"], now=NOW, after_replace=crash)
    assert (root / ".soleresearch/transaction.json").is_file()
    GraphRepository(root)  # constructor recovers the prepared journal
    assert not (root / ".soleresearch/transaction.json").exists()
    for name, content in before.items():
        assert (root / name).read_bytes() == content


def test_graph_projection_index_is_deterministic(tmp_path: Path) -> None:
    root = _project(tmp_path)
    repository = GraphRepository(root)
    _apply(repository, [{"op": "add", "target": "node", "record": new_node("question", "Question", now=NOW)}])
    rebuild_index(root)
    first = read_index_snapshot(root)
    rebuild_index(root)
    assert read_index_snapshot(root) == first
    assert load_project(root)["project_id"].startswith("prj_")


def test_cli_scaffold_diff_discuss_and_reconcile_help_contract() -> None:
    from soleresearch.cli import _parser

    help_text = _parser().format_help()
    for command in ("scaffold", "discuss", "diff", "reconcile"):
        assert command in help_text
