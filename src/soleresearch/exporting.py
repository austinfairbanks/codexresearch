from __future__ import annotations

import hashlib
import html
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from soleresearch.errors import ProjectError
from soleresearch.project import load_project, project_status
from soleresearch.schemas import SCHEMA_VERSION
from soleresearch.storage import atomic_write_json, atomic_write_text

ROOT_FILES = ("project.json", "outline.md", "outline.meta.json")
TREE_ROOTS = ("graph", "sources", "evidence", "discussions", "decisions", "events", "references", "runs")


def _assert_export_path(root: Path, path: Path) -> None:
    if path.is_symlink():
        raise ProjectError(f"export refuses symbolic link: {path.relative_to(root)}")
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ProjectError(f"export path resolves outside project: {path}") from exc


def _included_files(root: Path) -> list[Path]:
    root = root.resolve()
    included: list[Path] = []
    for name in ROOT_FILES:
        path = root / name
        _assert_export_path(root, path)
        included.append(path)
    for tree_name in TREE_ROOTS:
        tree = root / tree_name
        if not tree.exists():
            continue
        _assert_export_path(root, tree)
        for path in tree.rglob("*"):
            _assert_export_path(root, path)
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if "transcripts" in relative_parts or path.name.endswith(".tmp"):
                continue
            included.append(path)
    return sorted(included, key=lambda item: item.relative_to(root).as_posix())


def export_project(project_path: Path, output_path: Path) -> dict[str, Any]:
    root = project_path.resolve()
    destination = output_path.resolve()
    load_project(root)
    status = project_status(root)
    if status["outline_dirty"]:
        raise ProjectError("outline has unreconciled human edits; reconcile before export")
    if destination.exists():
        raise ProjectError(f"refusing to overwrite export path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.export-", dir=destination.parent))
    try:
        manifest_files: list[dict[str, Any]] = []
        for source in _included_files(root):
            relative = source.relative_to(root)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            target.write_bytes(content)
            manifest_files.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                }
            )
        audit = {
            "schema_version": SCHEMA_VERSION,
            "project_id": status["project_id"],
            "counts": status["counts"],
            "files_authoritative": True,
            "outline_dirty": False,
        }
        atomic_write_json(temporary / "audit-summary.json", audit)
        outline = (root / "outline.md").read_text(encoding="utf-8")
        atomic_write_text(
            temporary / "outline.html",
            "<!doctype html>\n<meta charset=\"utf-8\">\n<title>Research Outline</title>\n"
            f"<pre>{html.escape(outline)}</pre>\n",
        )
        atomic_write_json(
            temporary / "manifest.json",
            {"schema_version": SCHEMA_VERSION, "files": manifest_files},
        )
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "schema_version": SCHEMA_VERSION,
        "output": str(destination),
        "canonical_files": len(manifest_files),
    }
