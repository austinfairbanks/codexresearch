from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from soleresearch.controller import CONFIG_HOME_ENV, capability_paths
from soleresearch.cli import main
from soleresearch.errors import MigrationRequired, ProjectError
from soleresearch.exporting import export_project
from soleresearch.graph import GraphRepository
from soleresearch.indexing import rebuild_index
from soleresearch.migrations import migrate_project
from soleresearch.outline import render_outline
from soleresearch.project import load_project, project_status

PHASE1_OUTLINE = """# Research Outline

<!-- soleresearch:anchor root -->

## Research question

- Add the primary question here.

## Working map

- Add concepts, evidence links, disagreements, gaps, and candidate conclusions here.
"""


def _phase1(root: Path, *, graph_record: dict | None = None, meta_version: object = 1) -> None:
    root.mkdir()
    project = {
        "schema_version": 1,
        "project_id": "prj_" + "1" * 32,
        "name": "Phase One",
        "created_at": "2026-07-10T12:00:00Z",
        "data_policy": "public_only",
    }
    (root / "project.json").write_text(json.dumps(project) + "\n", encoding="utf-8")
    (root / "outline.md").write_text(PHASE1_OUTLINE, encoding="utf-8")
    (root / "outline.meta.json").write_text(
        json.dumps({"schema_version": meta_version, "revision": 0, "anchors": {"root": "Research Outline"}}) + "\n",
        encoding="utf-8",
    )
    for relative in (
        "graph/nodes.jsonl", "graph/edges.jsonl", "sources/sources.jsonl",
        "evidence/evidence.jsonl", "decisions/decisions.jsonl",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(graph_record) + "\n" if relative == "graph/nodes.jsonl" and graph_record else "",
            encoding="utf-8",
        )
    (root / "references").mkdir()
    (root / "references/references.bib").write_text("", encoding="utf-8")
    (root / "references/references.csl.json").write_text("[]\n", encoding="utf-8")


def _bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _token(project_id: str, kind: str) -> str:
    return json.loads(capability_paths(project_id)[kind].read_text(encoding="utf-8"))["token"]


def test_v1_load_requires_explicit_migration_without_mutating_bytes(tmp_path: Path) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root)
    before = _bytes(root)
    with pytest.raises(MigrationRequired, match="sole-research migrate"):
        load_project(root)
    assert _bytes(root) == before


def test_empty_phase1_migration_is_deterministic_idempotent_and_complete(tmp_path: Path) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root)
    original_outline = (root / "outline.md").read_bytes()
    result = migrate_project(root)
    assert result["migrated"] is True and result["graph_contract"] == "strict_v1"
    assert (root / "outline.md").read_bytes() == original_outline
    meta = json.loads((root / "outline.meta.json").read_text())
    assert (meta["schema_version"], meta["graph_contract"]) == (2, "strict_v1")
    event = json.loads((root / "events/migrations.jsonl").read_text())
    assert event["before_hash"].startswith("sha256:") and event["after_hash"].startswith("sha256:")
    assert load_project(root)["project_id"] == "prj_" + "1" * 32
    assert project_status(root)["outline_dirty"] is True
    assert rebuild_index(root)["records"] == 1  # migration event
    with pytest.raises(ProjectError, match="unreconciled"):
        export_project(root, tmp_path / "blocked-export")

    repository = GraphRepository(root)
    conflict_result = repository.reconcile(
        controller_token=_token(repository.project["project_id"], "human")
    )
    assert conflict_result["conflicts"]
    (root / "outline.md").write_text(render_outline([]), encoding="utf-8")
    repository.reconcile(controller_token=_token(repository.project["project_id"], "human"))
    export_project(root, tmp_path / "export")
    assert (tmp_path / "export/events/migrations.jsonl").is_file()
    again = migrate_project(root)
    assert again["migrated"] is False
    assert len((root / "events/migrations.jsonl").read_text().splitlines()) == 1


def test_nonempty_untyped_phase1_graph_requires_manual_conversion(tmp_path: Path) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root, graph_record={"schema_version": 1, "node_id": "node_1", "type": "question"})
    before = _bytes(root)
    with pytest.raises(ProjectError, match="manual graph conversion"):
        migrate_project(root)
    assert _bytes(root) == before


@pytest.mark.parametrize("version", [3, 9, True, None, "1"])
def test_migration_unknown_versions_fail_without_mutation(tmp_path: Path, version: object) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root, meta_version=version)
    before = _bytes(root)
    with pytest.raises(ProjectError, match="unsupported"):
        migrate_project(root)
    assert _bytes(root) == before


def test_interrupted_migration_recovers_then_completes(tmp_path: Path) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root)

    class Crash(BaseException):
        pass

    def crash(index: int, _path: str) -> None:
        if index == 2:
            raise Crash()

    with pytest.raises(Crash):
        migrate_project(root, after_replace=crash)
    assert (root / ".soleresearch/transaction.json").is_file()
    completed = migrate_project(root)
    assert completed["migrated"] is True
    assert load_project(root)["project_id"] == "prj_" + "1" * 32


def test_cli_requires_then_runs_explicit_migration(tmp_path: Path, capsys) -> None:
    os.environ[CONFIG_HOME_ENV] = str(tmp_path / "config")
    root = tmp_path / "project"
    _phase1(root)
    before = _bytes(root)
    assert main(["status", str(root)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "MigrationRequired" not in error["error"]  # actionable message, not an internal type dump
    assert "sole-research migrate" in error["error"]
    assert _bytes(root) == before
    assert main(["migrate", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["migrated"] is True and result["to_version"] == 2
    assert main(["status", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["graph_state"]["migration_events"] == 1
