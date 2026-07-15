from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from soleresearch.errors import ProjectError


def confined_project_path(root: Path, relative: str | Path) -> Path:
    """Return a project-confined path after rejecting traversal and symlink components."""
    root = root.resolve()
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or ".." in candidate_relative.parts:
        raise ProjectError(f"project path must be relative and confined: {relative}")
    candidate = root / candidate_relative
    current = root
    for component in candidate_relative.parts:
        current = current / component
        if current.is_symlink():
            raise ProjectError(f"project path contains symbolic link: {candidate_relative}")
    existing = candidate
    while not existing.exists() and existing != root:
        existing = existing.parent
    try:
        existing.resolve().relative_to(root)
        candidate.parent.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"project path resolves outside project: {candidate_relative}") from exc
    return candidate


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write_text(path: Path, text: str) -> None:
    """Replace one file atomically after its complete content reaches disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace one binary file atomically after its complete content reaches disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(f"invalid JSON in {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ProjectError(f"invalid UTF-8 in {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read complete newline-terminated JSONL records; reject torn tails."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ProjectError(f"missing required file: {path}") from exc
    if raw and not raw.endswith(b"\n"):
        raise ProjectError(f"incomplete JSONL record in {path}: file must end with newline")
    records: list[dict[str, Any]] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError(f"invalid UTF-8 in {path}: {exc}") from exc
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProjectError(f"invalid JSONL in {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ProjectError(f"JSONL record in {path}:{line_number} must be an object")
        records.append(value)
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    lines = [canonical_json(record) for record in records]
    atomic_write_text(path, "" if not lines else "\n".join(lines) + "\n")
