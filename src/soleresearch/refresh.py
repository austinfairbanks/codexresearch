from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REFRESH_SIGNAL_ENV = "SOLERESEARCH_REFRESH_SIGNAL"
REFRESH_SIGNAL_SCHEMA_VERSION = 1
MAX_REFRESH_SIGNAL_BYTES = 4096
SIGNAL_ID_RE = re.compile(r"^sig_[0-9a-f]{32}$")


def refresh_signal_path() -> Path:
    explicit = os.environ.get(REFRESH_SIGNAL_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return (Path(tempfile.gettempdir()) / f"soleresearch-turn-complete-{uid}.json").resolve()


def empty_refresh_signal() -> dict[str, Any]:
    return {
        "schema_version": REFRESH_SIGNAL_SCHEMA_VERSION,
        "signal_id": None,
        "completed_at": None,
    }


def read_refresh_signal(path: Path | None = None) -> dict[str, Any]:
    """Read a best-effort completion signal without affecting UI availability."""
    target = (path or refresh_signal_path()).resolve()
    try:
        stat = target.lstat()
        if not target.is_file() or target.is_symlink() or stat.st_size > MAX_REFRESH_SIGNAL_BYTES:
            return empty_refresh_signal()
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"schema_version", "signal_id", "completed_at"}:
            return empty_refresh_signal()
        if value["schema_version"] != REFRESH_SIGNAL_SCHEMA_VERSION:
            return empty_refresh_signal()
        if not isinstance(value["signal_id"], str) or SIGNAL_ID_RE.fullmatch(value["signal_id"]) is None:
            return empty_refresh_signal()
        if not isinstance(value["completed_at"], str):
            return empty_refresh_signal()
        completed_at = datetime.fromisoformat(value["completed_at"].removesuffix("Z") + "+00:00")
        if completed_at.tzinfo != UTC:
            return empty_refresh_signal()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return empty_refresh_signal()
    return value
