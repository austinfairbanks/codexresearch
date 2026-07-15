from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from soleresearch.errors import ProjectError
from soleresearch.schemas import SCHEMA_VERSION
from soleresearch.storage import atomic_write_bytes, atomic_write_json, confined_project_path, read_json

JOURNAL = ".soleresearch/transaction.json"


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _fsync_directory(path: Path) -> None:
    """Best-effort directory metadata durability on platforms that support it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _validated_entries(journal: dict[str, Any]) -> list[dict[str, Any]]:
    entries = journal.get("entries")
    expected = {"path", "before", "before_exists", "before_hash", "after_hash"}
    if not isinstance(entries, list) or not entries:
        raise ProjectError("invalid canonical transaction journal entries")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected:
            raise ProjectError("invalid canonical transaction journal entry")
        if not isinstance(entry["path"], str) or entry["path"] in seen:
            raise ProjectError("invalid or duplicate canonical transaction target")
        seen.add(entry["path"])
        try:
            before = base64.b64decode(entry["before"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ProjectError("invalid transaction rollback content") from exc
        if not isinstance(entry["before_exists"], bool) or _digest(before) != entry["before_hash"]:
            raise ProjectError("transaction journal before hash does not match rollback content")
        for field in ("before_hash", "after_hash"):
            if not isinstance(entry[field], str) or not entry[field].startswith("sha256:"):
                raise ProjectError("invalid transaction journal content hash")
    return entries


def _mark_recovery_conflict(journal_path: Path, journal: dict[str, Any], conflicts: list[dict[str, str]]) -> None:
    journal["state"] = "recovery_conflict"
    journal["recovery_conflicts"] = conflicts
    atomic_write_json(journal_path, journal)
    _fsync_directory(journal_path.parent)


def recover_transaction(project_path: Path) -> bool:
    """Recover only journal-proven writes and never overwrite third-state edits."""
    root = project_path.resolve()
    journal_path = confined_project_path(root, JOURNAL)
    if not journal_path.exists():
        return False
    journal = read_json(journal_path)
    if not isinstance(journal, dict) or journal.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("invalid canonical transaction journal")
    state = journal.get("state")
    if state == "recovery_conflict":
        raise ProjectError(f"canonical transaction recovery conflict remains visible at {journal_path}")
    if state not in {"prepared", "committed"}:
        raise ProjectError("invalid canonical transaction journal state")
    entries = _validated_entries(journal)
    classifications: list[tuple[dict[str, Any], Path, bool, bool]] = []
    for entry in entries:
        target = confined_project_path(root, entry["path"])
        current_exists = target.exists()
        current_hash = _digest(target.read_bytes() if current_exists else b"")
        before_matches = current_hash == entry["before_hash"] and current_exists == entry["before_exists"]
        after_matches = current_exists and current_hash == entry["after_hash"]
        classifications.append((entry, target, before_matches, after_matches))

    if state == "committed":
        conflicts = [
            {"path": entry["path"], "observed": "before" if before_matches else "third_state", "reason": "committed target does not match after hash"}
            for entry, _target, before_matches, after_matches in classifications
            if not after_matches
        ]
        if conflicts:
            _mark_recovery_conflict(journal_path, journal, conflicts)
            raise ProjectError("committed canonical transaction has divergent targets; journal retained")
    else:
        third_states = [
            {"path": entry["path"], "observed": "third_state", "reason": "target matches neither before nor after hash"}
            for entry, _target, before_matches, after_matches in classifications
            if not before_matches and not after_matches
        ]
        for entry, target, before_matches, after_matches in classifications:
            # Both-match means the transaction was a byte-for-byte no-op.
            if not after_matches or before_matches:
                continue
            if entry["before_exists"]:
                atomic_write_bytes(target, base64.b64decode(entry["before"], validate=True))
                _fsync_directory(target.parent)
            else:
                target.unlink(missing_ok=True)
                _fsync_directory(target.parent)
        if third_states:
            _mark_recovery_conflict(journal_path, journal, third_states)
            raise ProjectError("canonical transaction encountered third-state human edits; journal retained")
    journal_path.unlink()
    _fsync_directory(journal_path.parent)
    return True


def transactional_write(
    project_path: Path,
    updates: dict[str, bytes],
    *,
    after_replace: Callable[[int, str], None] | None = None,
) -> None:
    """Durably replace a canonical file set with hash-aware crash recovery."""
    root = project_path.resolve()
    recover_transaction(root)
    if not updates:
        return
    entries: list[dict[str, Any]] = []
    for relative in sorted(updates):
        target = confined_project_path(root, relative)
        before_exists = target.exists()
        before = target.read_bytes() if before_exists else b""
        entries.append(
            {
                "path": relative,
                "before": base64.b64encode(before).decode("ascii"),
                "before_exists": before_exists,
                "before_hash": _digest(before),
                "after_hash": _digest(updates[relative]),
            }
        )
    journal_path = confined_project_path(root, JOURNAL)
    journal = {"schema_version": SCHEMA_VERSION, "state": "prepared", "entries": entries}
    atomic_write_json(journal_path, journal)
    _fsync_directory(journal_path.parent)
    try:
        for index, relative in enumerate(sorted(updates), start=1):
            target = confined_project_path(root, relative)
            atomic_write_bytes(target, updates[relative])
            _fsync_directory(target.parent)
            if after_replace is not None:
                after_replace(index, relative)
        journal["state"] = "committed"
        atomic_write_json(journal_path, journal)
        _fsync_directory(journal_path.parent)
    except Exception:
        recover_transaction(root)
        raise
    journal_path.unlink()
    _fsync_directory(journal_path.parent)
