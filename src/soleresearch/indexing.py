from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from soleresearch.errors import ProjectError
from soleresearch.project import CANONICAL_JSONL, load_project
from soleresearch.schemas import SCHEMA_VERSION
from soleresearch.storage import canonical_json, read_jsonl
from soleresearch.storage import read_json


def _record_key(record: dict[str, Any]) -> str:
    for field in (
        "node_id",
        "edge_id",
        "source_id",
        "evidence_id",
        "decision_id",
        "id",
    ):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return "sha256:" + hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def rebuild_index(project_path: Path) -> dict[str, Any]:
    """Replace the disposable index using only authoritative project files."""
    root = project_path.resolve()
    load_project(root)
    index_dir = root / ".soleresearch"
    index_dir.mkdir(parents=True, exist_ok=True)
    destination = index_dir / "index.sqlite3"
    descriptor, temp_name = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=index_dir)
    os.close(descriptor)
    temporary = Path(temp_name)
    counts: dict[str, int] = {}
    try:
        try:
            connection = sqlite3.connect(temporary)
            try:
                connection.execute(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
                )
                connection.execute(
                    "CREATE TABLE records (ledger TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, "
                    "PRIMARY KEY (ledger, record_key)) WITHOUT ROWID"
                )
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
                indexed_ledgers = [*CANONICAL_JSONL]
                if (root / "events/human-edits.jsonl").is_file():
                    indexed_ledgers.append("events/human-edits.jsonl")
                for relative in indexed_ledgers:
                    records = read_jsonl(root / relative)
                    counts[relative] = len(records)
                    seen: set[str] = set()
                    for line_number, record in enumerate(records, start=1):
                        key = _record_key(record)
                        if key in seen:
                            raise ProjectError(
                                f"duplicate index key in {relative}:{line_number}: {key}"
                            )
                        seen.add(key)
                        connection.execute(
                            "INSERT INTO records(ledger, record_key, payload) VALUES (?, ?, ?)",
                            (relative, key, canonical_json(record)),
                        )
                runs_root = root / "runs"
                if runs_root.exists():
                    for run_dir in sorted(path for path in runs_root.glob("run_*") if path.is_dir()):
                        # Historical/imported run folders may contain only
                        # transcripts or generic events. They remain exportable
                        # but are not Phase-4 orchestration runs without a manifest.
                        if not (run_dir / "manifest.json").is_file():
                            continue
                        from soleresearch.orchestration import RunRepository

                        RunRepository(root, run_dir.name)
                        for path in sorted(run_dir.rglob("*")):
                            if not path.is_file() or path.name.endswith(".tmp") or "transcripts" in path.parts:
                                continue
                            relative = path.relative_to(root).as_posix()
                            if path.suffix == ".json":
                                records = [read_json(path)]
                            elif path.suffix == ".jsonl":
                                records = read_jsonl(path)
                            else:
                                continue
                            counts[relative] = len(records)
                            for index, record in enumerate(records):
                                key = _record_key(record)
                                if len(records) > 1 and not any(field in record for field in ("event_id", "extension_id", "id")):
                                    key = f"{index:08d}:{key}"
                                connection.execute(
                                    "INSERT INTO records(ledger, record_key, payload) VALUES (?, ?, ?)",
                                    (relative, key, canonical_json(record)),
                                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ProjectError(f"SQLite index rebuild failed for {root}: {exc}") from exc
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "schema_version": SCHEMA_VERSION,
        "index": str(destination),
        "records": sum(counts.values()),
        "ledgers": counts,
    }


def read_index_snapshot(project_path: Path) -> list[tuple[str, str, str]]:
    destination = project_path.resolve() / ".soleresearch/index.sqlite3"
    if not destination.is_file():
        raise ProjectError(f"index does not exist: {destination}")
    connection = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        return list(
            connection.execute(
                "SELECT ledger, record_key, payload FROM records ORDER BY ledger, record_key"
            )
        )
    finally:
        connection.close()
