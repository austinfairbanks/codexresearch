from __future__ import annotations

import json
from pathlib import Path

import pytest

from soleresearch.errors import ProjectError
from soleresearch.project import CANONICAL_JSONL, initialize_project, load_project, project_status


def test_init_creates_complete_files_first_project(tmp_path: Path) -> None:
    root = tmp_path / "wear-study"
    project = initialize_project(root, name="Wear Study")

    assert project["project_id"].startswith("prj_")
    assert load_project(root) == project
    assert "soleresearch:anchor root" in (root / "outline.md").read_text(encoding="utf-8")
    assert all((root / relative).read_bytes() == b"" for relative in CANONICAL_JSONL)
    assert json.loads((root / "references/references.csl.json").read_text()) == []
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for value in (".soleresearch/", "exports/", "source-cache/", "transcripts/"):
        assert value in ignored
    assert project_status(root)["counts"] == {
        "nodes": 0,
        "edges": 0,
        "sources": 0,
        "evidence": 0,
        "decisions": 0,
    }


def test_init_refuses_existing_path_without_modifying_it(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    marker = root / "human.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ProjectError, match="existing path"):
        initialize_project(root)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(root.iterdir()) == [marker]


def test_project_rejects_torn_jsonl_tail(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "sources/sources.jsonl").write_bytes(b'{"schema_version":1}')

    with pytest.raises(ProjectError, match="must end with newline"):
        load_project(root)


def test_project_rejects_unknown_project_schema(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    document = json.loads((root / "project.json").read_text(encoding="utf-8"))
    document["schema_version"] = 99
    (root / "project.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProjectError, match="unsupported"):
        load_project(root)


@pytest.mark.parametrize("version", [True, 1.0, None, "1"])
def test_project_version_is_bool_safe(tmp_path: Path, version: object) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    document = json.loads((root / "project.json").read_text(encoding="utf-8"))
    document["schema_version"] = version
    (root / "project.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ProjectError, match="unsupported"):
        load_project(root)


def test_project_load_rejects_unknown_canonical_record_schema(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "graph/nodes.jsonl").write_text(
        '{"schema_version":2,"node_id":"node_1"}\n', encoding="utf-8"
    )

    with pytest.raises(ProjectError, match=r"graph/nodes.jsonl:1"):
        load_project(root)


@pytest.mark.parametrize("version", [True, 1.0, None, "1"])
def test_canonical_record_version_is_bool_safe(tmp_path: Path, version: object) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "graph/nodes.jsonl").write_text(
        json.dumps({"schema_version": version, "node_id": "node_1"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match=r"graph/nodes.jsonl:1"):
        load_project(root)


@pytest.mark.parametrize("version", [True, 1.0, None, "1"])
def test_outline_meta_version_is_bool_safe(tmp_path: Path, version: object) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    metadata = json.loads((root / "outline.meta.json").read_text(encoding="utf-8"))
    metadata["schema_version"] = version
    (root / "outline.meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ProjectError, match="outline.meta.json"):
        load_project(root)


def test_project_load_wraps_malformed_utf8_with_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    ledger = root / "evidence/evidence.jsonl"
    ledger.write_bytes(b"\xff\n")

    with pytest.raises(ProjectError, match=r"invalid UTF-8.*evidence/evidence.jsonl"):
        load_project(root)
