from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from soleresearch.controller import CONFIG_HOME_ENV, capability_paths
from soleresearch.errors import ProjectError
from soleresearch.exporting import export_project
from soleresearch.graph import GraphRepository, new_edge, new_node
from soleresearch.outline import parse_outline, render_outline
from soleresearch.project import initialize_project, load_project, project_status
from soleresearch.storage import atomic_write_json, write_jsonl
from soleresearch.transactions import recover_transaction

NOW = lambda: "2026-07-10T12:00:00Z"
LATER = lambda: "2026-07-10T13:00:00Z"


def _root(tmp_path: Path) -> Path:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "controller-config")
    root = tmp_path / "project"
    initialize_project(root)
    return root


def _token(root: Path, authority: str = "agent") -> str:
    project_id = json.loads((root / "project.json").read_text())["project_id"]
    return json.loads(capability_paths(project_id)[authority].read_text(encoding="utf-8"))["token"]


def _apply(root: Path, operations: list[dict], *, actor_type: str = "agent", actor_id: str = "actor") -> dict:
    repository = GraphRepository(root)
    diff = repository.propose(operations, actor_type=actor_type, actor_id=actor_id, now=NOW)
    authority = "human" if actor_type == "human" else "agent"
    return repository.apply(diff["diff_id"], controller_token=_token(root, authority), now=NOW)


def test_controller_capability_replaces_forgeable_role(tmp_path: Path) -> None:
    root = _root(tmp_path)
    repository = GraphRepository(root)
    diff = repository.propose([{"op": "add", "target": "node", "record": new_node("question", "Q", now=NOW)}], now=NOW)
    with pytest.raises(ProjectError, match="capability"):
        repository.apply(diff["diff_id"], controller_token=None)
    with pytest.raises(ProjectError, match="invalid external controller"):
        repository.apply(diff["diff_id"], controller_token="ctl_forged")
    repository.apply(diff["diff_id"], controller_token=_token(root), now=NOW)
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in capability_paths(repository.project["project_id"]).values())
    assert not any("controller" in path.name for path in root.iterdir())
    assert all(root not in path.parents for path in capability_paths(repository.project["project_id"]).values())

    forged = repository.propose(
        [{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Forged human"}}],
        actor_type="human",
        actor_id="untrusted-packet",
        now=NOW,
    )
    applied = repository.apply(forged["diff_id"], controller_token=_token(root, "agent"), now=NOW)
    forged_node = next(item for item in repository.nodes() if item["title"] == "Forged human")
    assert forged_node["authority"] == "agent_accepted"
    assert (applied["applied_by"], applied["applied_authority"]) == ("agent-controller", "agent_accepted")


def test_human_acceptance_and_agent_semantic_mutations_downgrade(tmp_path: Path) -> None:
    root = _root(tmp_path)
    a = new_node("question", "A", maturity="supported", now=NOW)
    b = new_node("concept", "B", maturity="developing", position=1, now=NOW)
    c = new_node("concept", "C", position=2, now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": a}, {"op": "add", "target": "node", "record": b}, {"op": "add", "target": "node", "record": c}], actor_type="human")
    repository = GraphRepository(root)
    assert {item["authority"] for item in repository.nodes()} == {"human_accepted"}

    _apply(root, [{"op": "update", "target": "node", "target_id": a["node_id"], "changes": {"body": "agent edit"}}])
    assert next(item for item in GraphRepository(root).nodes() if item["node_id"] == a["node_id"])["authority"] == "agent_accepted"
    _apply(root, [{"op": "update", "target": "node", "target_id": a["node_id"], "changes": {"authority": "human_accepted"}}], actor_type="human")
    _apply(root, [{"op": "move", "target_id": a["node_id"], "parent_id": b["node_id"], "position": 0}])
    assert next(item for item in GraphRepository(root).nodes() if item["node_id"] == a["node_id"])["authority"] == "agent_accepted"

    edge = new_edge("relates_to", b["node_id"], c["node_id"], now=NOW)
    _apply(root, [{"op": "link", "record": edge}], actor_type="human")
    _apply(root, [{"op": "update", "target": "edge", "target_id": edge["edge_id"], "changes": {"target_node_id": a["node_id"]}}])
    assert GraphRepository(root).edges()[0]["authority"] == "agent_accepted"

    _apply(root, [{"op": "retire", "target": "node", "target_id": b["node_id"]}])
    retired = next(item for item in GraphRepository(root).nodes() if item["node_id"] == b["node_id"])
    assert (retired["maturity"], retired["prior_maturity"], retired["authority"]) == ("retired", "developing", "agent_accepted")
    _apply(root, [{"op": "restore", "target": "node", "target_id": b["node_id"]}])
    restored = next(item for item in GraphRepository(root).nodes() if item["node_id"] == b["node_id"])
    assert (restored["maturity"], restored["prior_maturity"]) == ("developing", None)


def test_reconcile_preserves_authority_and_records_atomic_event(tmp_path: Path) -> None:
    root = _root(tmp_path)
    human = new_node("question", "Human accepted", now=NOW)
    agent = new_node("concept", "Agent accepted", position=1, now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": human}], actor_type="human")
    _apply(root, [{"op": "add", "target": "node", "record": agent}])
    outline = (root / "outline.md").read_text(encoding="utf-8")
    outline = outline.replace("## Human accepted", "## Human edit").replace("## Agent accepted", "## Agent edit")
    (root / "outline.md").write_text(outline, encoding="utf-8")
    result = GraphRepository(root).reconcile(controller_token=_token(root, "human"), now=LATER)
    authorities = {item["title"]: item["authority"] for item in GraphRepository(root).nodes()}
    assert authorities == {"Human edit": "human_accepted", "Agent edit": "agent_accepted"}
    event = GraphRepository(root).reconciliation_events()[-1]
    assert event["event_id"] == result["event_id"]
    assert event["actor_id"] == "human-controller"
    assert {item["node_id"] for item in event["semantic_changes"]} == {human["node_id"], agent["node_id"]}
    assert event["base_graph_revision"] + 1 == event["result_graph_revision"]


def test_shorthand_retry_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    repository = GraphRepository(root)
    identity = "dif_" + "a" * 32
    operations = [{"op": "add", "target": "node", "record": {"node_type": "question", "title": "Q"}}]
    first = repository.propose(operations, diff_id=identity, now=NOW)
    second = repository.propose(operations, diff_id=identity, now=LATER)
    assert second == first
    assert first["operations"][0]["operation_id"] == "op_" + hashlib.sha256(f"{identity}:0:operation".encode()).hexdigest()[:32]
    assert first["operations"][0]["record"]["node_id"] == "nod_" + hashlib.sha256(f"{identity}:0:node".encode()).hexdigest()[:32]


def test_max_depth_and_reserved_body_structure_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    records = []
    parent = None
    for index in range(6):
        node = new_node("concept", f"N{index}", parent_id=parent, now=NOW)
        records.append({"op": "add", "target": "node", "record": node})
        parent = node["node_id"]
    with pytest.raises(ProjectError, match="maximum depth"):
        GraphRepository(root).propose(records, now=NOW)
    with pytest.raises(ProjectError, match="reserved outline structure"):
        GraphRepository(root).propose([{"op": "add", "target": "node", "record": new_node("concept", "Bad", body="## Nested heading", now=NOW)}], now=NOW)


def test_fenced_heading_and_anchor_like_text_remain_body(tmp_path: Path) -> None:
    node = new_node("concept", "Code", body="```md\n## Not a node\n<!-- soleresearch:node nod_00000000000000000000000000000000 -->\n```", now=NOW)
    text = render_outline([node])
    parsed = parse_outline(text, {node["node_id"]})
    assert parsed.issues == []
    assert parsed.mappings[node["node_id"]]["body"] == node["body"]


@pytest.mark.parametrize(
    "body",
    [
        "````md\n```\n## Still code\n<!-- soleresearch:node nod_00000000000000000000000000000000 -->\n````",
        "~~~~\n~~~\n## Still tilde code\n~~~~",
        "   ```\n## Three-space fenced\n   ```",
        "    ## Indented code heading",
    ],
)
def test_commonmark_fence_and_indentation_boundaries(body: str) -> None:
    node = new_node("concept", "Protected", body=body, now=NOW)
    parsed = parse_outline(render_outline([node]), {node["node_id"]})
    assert parsed.issues == []
    assert parsed.mappings[node["node_id"]]["body"] == body


def test_four_space_fence_is_indented_code_not_an_opener(tmp_path: Path) -> None:
    root = _root(tmp_path)
    body = "    ```\n## This is outside indented code\n    ```"
    with pytest.raises(ProjectError, match="reserved outline structure"):
        GraphRepository(root).propose(
            [{"op": "add", "target": "node", "record": new_node("concept", "Boundary", body=body, now=NOW)}],
            now=NOW,
        )


def test_conflict_fingerprint_dedupes_and_repair_resolves(tmp_path: Path) -> None:
    root = _root(tmp_path)
    node = new_node("question", "Q", now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": node}])
    clean = (root / "outline.md").read_text(encoding="utf-8")
    broken = clean.replace(f"<!-- soleresearch:node {node['node_id']} -->", "")
    (root / "outline.md").write_text(broken, encoding="utf-8")
    repository = GraphRepository(root)
    repository.reconcile(controller_token=_token(root, "human"), now=NOW)
    repository.reconcile(controller_token=_token(root, "human"), now=LATER)
    conflicts = repository.conflicts()
    assert len(conflicts) == 1 and conflicts[0]["status"] == "queued"
    (root / "outline.md").write_text(clean, encoding="utf-8")
    repository.reconcile(controller_token=_token(root, "human"), now=LATER)
    repaired = repository.conflicts()[0]
    assert repaired["status"] == "resolved"
    assert repaired["resolution"] == "outline_repaired"


def test_dirty_outline_load_status_index_and_export_policy(tmp_path: Path) -> None:
    root = _root(tmp_path)
    node = new_node("question", "Q", now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": node}])
    (root / "outline.md").write_text((root / "outline.md").read_text().replace("## Q", "## Edited"), encoding="utf-8")
    assert load_project(root)["project_id"].startswith("prj_")
    status = project_status(root)
    assert status["outline_dirty"] is True and status["reconciliation_required"] is True
    with pytest.raises(ProjectError, match="unreconciled"):
        export_project(root, tmp_path / "export")


def test_dirty_outline_cannot_launder_out_of_band_graph_mutation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    node = new_node("question", "Q", now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": node}])
    (root / "outline.md").write_text((root / "outline.md").read_text().replace("## Q", "## Human dirty"), encoding="utf-8")
    nodes = GraphRepository(root).nodes()
    nodes[0]["title"] = "Out-of-band graph edit"
    write_jsonl(root / "graph/nodes.jsonl", nodes)
    with pytest.raises(ProjectError, match="graph hash"):
        load_project(root)
    with pytest.raises(ProjectError, match="graph hash"):
        GraphRepository(root)


def test_truncated_graph_and_empty_outline_cannot_launder_deletion(tmp_path: Path) -> None:
    root = _root(tmp_path)
    node = new_node("question", "Persist me", now=NOW)
    _apply(root, [{"op": "add", "target": "node", "record": node}])
    events_before = (root / "events/reconciliations.jsonl").read_bytes()
    write_jsonl(root / "graph/nodes.jsonl", [])
    write_jsonl(root / "graph/edges.jsonl", [])
    (root / "outline.md").write_text(render_outline([]), encoding="utf-8")
    with pytest.raises(ProjectError, match="graph hash"):
        load_project(root)
    with pytest.raises(ProjectError, match="graph hash"):
        GraphRepository(root)
    assert (root / "events/reconciliations.jsonl").read_bytes() == events_before


def test_central_read_integrity_rejects_self_edge_and_invalid_retired_state(tmp_path: Path) -> None:
    root = _root(tmp_path)
    node = new_node("concept", "A", now=NOW)
    write_jsonl(root / "graph/nodes.jsonl", [node])
    write_jsonl(root / "graph/edges.jsonl", [new_edge("relates_to", node["node_id"], node["node_id"], now=NOW)])
    with pytest.raises(ProjectError, match="self-referential"):
        load_project(root)
    write_jsonl(root / "graph/edges.jsonl", [])
    node["retired"] = True
    write_jsonl(root / "graph/nodes.jsonl", [node])
    with pytest.raises(ProjectError, match="retired flag"):
        load_project(root)


def test_merge_retires_self_edges_and_deduplicates_links(tmp_path: Path) -> None:
    root = _root(tmp_path)
    a, b, c = (new_node("concept", value, position=index, now=NOW) for index, value in enumerate(("A", "B", "C")))
    _apply(root, [{"op": "add", "target": "node", "record": item} for item in (a, b, c)])
    edges = [
        new_edge("relates_to", a["node_id"], b["node_id"], now=NOW),
        new_edge("relates_to", a["node_id"], c["node_id"], now=NOW),
        new_edge("relates_to", b["node_id"], c["node_id"], now=NOW),
    ]
    _apply(root, [{"op": "link", "record": item} for item in edges])
    _apply(root, [{"op": "merge", "source_id": a["node_id"], "target_id": b["node_id"]}])
    active = [item for item in GraphRepository(root).edges() if not item["retired"]]
    assert len(active) == 1
    assert (active[0]["source_node_id"], active[0]["target_node_id"]) == (b["node_id"], c["node_id"])


def test_abrupt_process_recovery_and_third_state_preservation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    repository = GraphRepository(root)
    diff = repository.propose([{"op": "add", "target": "node", "record": new_node("question", "Q", now=NOW)}], now=NOW)
    script = """
import os, sys
from pathlib import Path
from soleresearch.graph import GraphRepository
root=Path(sys.argv[1]); token=sys.argv[2]
def crash(index, path):
    if index == 2: os._exit(91)
GraphRepository(root).apply(sys.argv[3], controller_token=token, after_replace=crash)
"""
    completed = subprocess.run([sys.executable, "-c", script, str(root), _token(root), diff["diff_id"]], check=False)
    assert completed.returncode == 91
    assert (root / ".soleresearch/transaction.json").is_file()
    GraphRepository(root)
    assert not (root / ".soleresearch/transaction.json").exists()
    assert GraphRepository(root).nodes() == []

    # Repeat, then alter an applied target into a third state before recovery.
    diff2 = GraphRepository(root).propose([{"op": "add", "target": "node", "record": new_node("question", "Q2", now=NOW)}], now=NOW)
    completed = subprocess.run([sys.executable, "-c", script, str(root), _token(root), diff2["diff_id"]], check=False)
    assert completed.returncode == 91
    target = root / "graph/diffs.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + "human-third-state\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="third-state"):
        recover_transaction(root)
    assert target.read_text(encoding="utf-8").endswith("human-third-state\n")
    journal = json.loads((root / ".soleresearch/transaction.json").read_text())
    assert journal["state"] == "recovery_conflict"


def test_corrupt_and_divergent_committed_journals_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    journal = root / ".soleresearch/transaction.json"
    journal.parent.mkdir(exist_ok=True)
    journal.write_text("{bad", encoding="utf-8")
    with pytest.raises(ProjectError, match="invalid JSON"):
        recover_transaction(root)
    journal.unlink()
    target = root / "outline.md"
    before = target.read_bytes()
    after = b"after\n"
    entry = {
        "path": "outline.md",
        "before": base64.b64encode(before).decode("ascii"),
        "before_exists": True,
        "before_hash": "sha256:" + hashlib.sha256(before).hexdigest(),
        "after_hash": "sha256:" + hashlib.sha256(after).hexdigest(),
    }
    atomic_write_json(journal, {"schema_version": 1, "state": "committed", "entries": [entry]})
    with pytest.raises(ProjectError, match="divergent"):
        recover_transaction(root)
    assert json.loads(journal.read_text())["state"] == "recovery_conflict"


@pytest.mark.parametrize("state", ["prepared", "committed"])
def test_equal_before_after_hash_is_safe_for_recovery(tmp_path: Path, state: str) -> None:
    root = _root(tmp_path)
    target = root / "outline.md"
    content = target.read_bytes()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    journal = root / ".soleresearch/transaction.json"
    journal.parent.mkdir(exist_ok=True)
    atomic_write_json(
        journal,
        {
            "schema_version": 1,
            "state": state,
            "entries": [
                {
                    "path": "outline.md",
                    "before": base64.b64encode(content).decode("ascii"),
                    "before_exists": True,
                    "before_hash": digest,
                    "after_hash": digest,
                }
            ],
        },
    )
    assert recover_transaction(root) is True
    assert target.read_bytes() == content
    assert not journal.exists()
