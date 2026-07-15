from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soleresearch.errors import ProjectError
from soleresearch.storage import atomic_write_json

CONTROLLER_ENV = "SOLERESEARCH_CONTROLLER_TOKEN"
CONFIG_HOME_ENV = "SOLERESEARCH_CONFIG_HOME"


@dataclass(frozen=True)
class ControllerCapability:
    subject: str
    authority: str
    capability_id: str


def config_home() -> Path:
    explicit = os.environ.get(CONFIG_HOME_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "soleresearch").resolve()
    # Sandboxed/headless installations may not expose a writable home config.
    # The CLI reports this external path so operators can deliberately override it.
    uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return (Path(tempfile.gettempdir()) / f"soleresearch-config-{uid}").resolve()


def capability_paths(project_id: str) -> dict[str, Path]:
    base = config_home() / "projects" / project_id
    return {
        "agent": base / "agent.capability.json",
        "human": base / "human.capability.json",
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record(project_id: str, authority: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability_id": "cap_" + uuid.uuid4().hex,
        "project_id": project_id,
        "subject": "human-controller" if authority == "human_accepted" else "agent-controller",
        "authority": authority,
        "token": "ctl_" + secrets.token_hex(32),
        "created_at": _now(),
    }


def issue_controller_capabilities(project_id: str) -> dict[str, Path]:
    paths = capability_paths(project_id)
    for name, authority in (("agent", "agent_accepted"), ("human", "human_accepted")):
        path = paths[name]
        if path.exists():
            raise ProjectError(f"refusing to overwrite external controller capability: {path}")
        atomic_write_json(path, _record(project_id, authority))
        path.chmod(0o600)
    return paths


def ensure_controller_capabilities(project_id: str) -> dict[str, Path]:
    paths = capability_paths(project_id)
    present = {name: path.exists() for name, path in paths.items()}
    if all(present.values()):
        _load_records(project_id)
        return paths
    if any(present.values()):
        raise ProjectError("external controller capability set is incomplete; repair it manually")
    return issue_controller_capabilities(project_id)


def remove_controller_capabilities(project_id: str) -> None:
    paths = capability_paths(project_id)
    for path in paths.values():
        path.unlink(missing_ok=True)
    directory = next(iter(paths.values())).parent
    try:
        directory.rmdir()
    except OSError:
        pass


def read_controller_capability(token_file: Path | None = None) -> str:
    if token_file is not None:
        try:
            value = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectError(f"cannot read external controller capability: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("token"), str):
            raise ProjectError("external controller capability record is invalid")
        return value["token"]
    value = os.environ.get(CONTROLLER_ENV, "").strip()
    if not value:
        raise ProjectError(f"controller capability required via --controller-token-file or {CONTROLLER_ENV}")
    return value


def _load_records(project_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    required = {"schema_version", "capability_id", "project_id", "subject", "authority", "token", "created_at"}
    for path in capability_paths(project_id).values():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectError(f"cannot load external controller capability {path}: {exc}") from exc
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value.get("schema_version") != 1
            or value.get("project_id") != project_id
            or value.get("authority") not in {"agent_accepted", "human_accepted"}
            or not all(isinstance(value.get(field), str) and value[field] for field in ("capability_id", "subject", "token", "created_at"))
        ):
            raise ProjectError(f"invalid external controller capability record: {path}")
        records.append(value)
    return records


def require_controller(project_id: str, supplied: str | None) -> ControllerCapability:
    if not supplied:
        raise ProjectError("external controller capability is required for canonical graph mutation")
    supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
    for record in _load_records(project_id):
        expected_digest = hashlib.sha256(record["token"].encode("utf-8")).digest()
        if hmac.compare_digest(expected_digest, supplied_digest):
            return ControllerCapability(
                subject=record["subject"],
                authority=record["authority"],
                capability_id=record["capability_id"],
            )
    raise ProjectError("invalid external controller capability")
