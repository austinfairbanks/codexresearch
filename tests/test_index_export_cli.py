from __future__ import annotations

import json
from pathlib import Path

import pytest

from soleresearch.cli import main
from soleresearch.errors import ProjectError
from soleresearch.exporting import export_project
from soleresearch.indexing import read_index_snapshot, rebuild_index
from soleresearch.project import initialize_project
from soleresearch.storage import write_jsonl


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_rebuild_index_is_identical_and_never_changes_canonical_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    record = {"schema_version": 1, "decision_id": "decision_1", "value": "question"}
    write_jsonl(root / "decisions/decisions.jsonl", [record])
    before = _tree_bytes(root)

    first = rebuild_index(root)
    first_snapshot = read_index_snapshot(root)
    second = rebuild_index(root)

    assert first["records"] == second["records"] == 1
    assert read_index_snapshot(root) == first_snapshot
    after = {key: value for key, value in _tree_bytes(root).items() if not key.startswith(".soleresearch/")}
    assert after == before


def test_exports_are_deterministic_and_exclude_generated_private_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "runs/run_1/transcripts").mkdir(parents=True)
    (root / "runs/run_1/transcripts/raw.txt").write_text("private", encoding="utf-8")
    (root / "runs/run_1/events.jsonl").write_text("", encoding="utf-8")
    rebuild_index(root)

    first = tmp_path / "export-a"
    second = tmp_path / "export-b"
    export_project(root, first)
    export_project(root, second)

    assert _tree_bytes(first) == _tree_bytes(second)
    assert not (first / ".soleresearch").exists()
    assert not (first / "runs/run_1/transcripts").exists()
    assert (first / "outline.html").is_file()
    assert json.loads((first / "manifest.json").read_text())["schema_version"] == 1


def test_cli_bounded_empty_project_smoke(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    output = tmp_path / "export"

    assert main(["init", str(root), "--name", "CLI Project"]) == 0
    assert json.loads(capsys.readouterr().out)["project_id"].startswith("prj_")
    assert main(["doctor", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["status", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["sources"] == 0
    assert main(["rebuild-index", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 0
    assert main(["export", str(root), str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["canonical_files"] > 0


def test_cli_errors_are_structured(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing"
    assert main(["status", str(missing)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["command"] == "status"
    assert "missing required file" in error["error"]


@pytest.mark.parametrize("command", ["doctor", "status", "export"])
def test_read_commands_fail_closed_on_unknown_ledger_version(
    command: str, tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "sources/sources.jsonl").write_text(
        '{"schema_version":9,"source_id":"source_1"}\n', encoding="utf-8"
    )
    argv = [command, str(root)]
    if command == "export":
        argv.append(str(tmp_path / "export"))

    assert main(argv) == 2
    error = json.loads(capsys.readouterr().err)
    assert "sources/sources.jsonl:1" in error["error"]


def test_duplicate_index_key_is_structured_and_preserves_prior_index(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    write_jsonl(
        root / "decisions/decisions.jsonl",
        [{"schema_version": 1, "decision_id": "decision_1", "value": "question"}],
    )
    rebuild_index(root)
    index = root / ".soleresearch/index.sqlite3"
    before = index.read_bytes()
    write_jsonl(
        root / "decisions/decisions.jsonl",
        [
            {"schema_version": 1, "decision_id": "decision_1", "value": "question"},
            {"schema_version": 1, "decision_id": "decision_1", "value": "concept"},
        ],
    )

    assert main(["rebuild-index", str(root)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "duplicate decision_id in decisions/decisions.jsonl:2" in error["error"]
    assert index.read_bytes() == before


def test_malformed_utf8_is_a_structured_cli_error(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "decisions/decisions.jsonl").write_bytes(b"\xff\n")

    assert main(["status", str(root)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "invalid UTF-8" in error["error"]
    assert "decisions/decisions.jsonl" in error["error"]


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_export_rejects_symlinks_and_outside_resolution(kind: str, tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "file":
        target = outside / "outline.md"
        target.write_text("outside", encoding="utf-8")
        link = root / "outline.md"
        link.unlink()
        link.symlink_to(target)
    else:
        link = root / "discussions"
        link.rmdir()
        link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError, match="symbolic link"):
        export_project(root, tmp_path / "export")
