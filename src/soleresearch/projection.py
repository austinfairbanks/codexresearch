from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from soleresearch.errors import ProjectError
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import validate_document
from soleresearch.storage import atomic_write_json, canonical_json, read_json
from soleresearch.ui import ui_state

PROJECTION_SCHEMA_VERSION = "1.0.0"
MAX_PROJECTION_BYTES = 2 * 1024 * 1024
PUBLICATION_STATE = Path(".soleresearch/sites-publication.json")
PUBLICATION_OUTBOX = Path(".soleresearch/sites-publication-outbox.json")
MAX_PUBLISH_ATTEMPTS = 3


def _publication_state(root: Path) -> dict[str, Any]:
    path = root / PUBLICATION_STATE
    if not path.is_file():
        return {"published_revision": 0}
    value = read_json(path)
    revision = value.get("published_revision") if isinstance(value, dict) else None
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ProjectError("invalid generated Sites publication state")
    return value


def publication_status(project_path: Path) -> dict[str, Any]:
    root = project_path.resolve()
    project = load_project(root)
    state = _publication_state(root)
    outbox_path = root / PUBLICATION_OUTBOX
    pending = read_json(outbox_path) if outbox_path.is_file() else None
    return {
        "schema_version": 1,
        "project_id": project["project_id"],
        "published_revision": state.get("published_revision", 0),
        "project_revision": state.get("project_revision"),
        "published_at": state.get("published_at"),
        "site_url": state.get("site_url"),
        "pending_revision": pending.get("published_revision") if isinstance(pending, dict) else None,
        "retry_pending": pending is not None,
    }


def build_dashboard_projection(
    project_path: Path,
    *,
    published_revision: int,
    thread_id: str = "unknown",
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """Build the bounded, read-only Sites projection from authoritative files."""
    if not isinstance(published_revision, int) or isinstance(published_revision, bool) or published_revision < 1:
        raise ProjectError("published revision must be a positive integer")
    root = project_path.resolve()
    project = load_project(root)
    thread = thread_id.strip() or "unknown"
    if len(thread) > 200:
        raise ProjectError("thread identity exceeds 200 characters")
    state = ui_state(root, now=now)
    outline_content = (root / "outline.md").read_text(encoding="utf-8")
    views = state.get("views", {})
    collections = {
        name: {
            "total": value.get("total", 0),
            "included": len(value.get("items", [])),
            "truncated": bool(value.get("truncated", False)),
        }
        for name, value in views.items()
        if isinstance(value, dict) and isinstance(value.get("items"), list)
    }
    payload = {
        "schema_version": 1,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "project_id": project["project_id"],
        "thread_id": thread,
        "published_revision": published_revision,
        "produced_at": now(),
        "collections": collections,
        "truncated": any(item["truncated"] for item in collections.values()),
        "outline": {
            **state["outline"],
            "content": outline_content,
        },
        "state": state,
    }
    revision_input = {
        "schema_version": payload["schema_version"],
        "projection_schema_version": payload["projection_schema_version"],
        "project_id": payload["project_id"],
        "thread_id": payload["thread_id"],
        "outline": payload["outline"],
        "state": payload["state"],
    }
    payload["project_revision"] = hashlib.sha256(canonical_json(revision_input).encode("utf-8")).hexdigest()
    payload["content_sha256"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    validate_document("dashboard_projection", payload)
    size = len(canonical_json(payload).encode("utf-8"))
    if size > MAX_PROJECTION_BYTES:
        raise ProjectError(f"dashboard projection exceeds {MAX_PROJECTION_BYTES} UTF-8 bytes")
    return payload


def next_dashboard_projection(project_path: Path, *, thread_id: str = "unknown") -> dict[str, Any]:
    root = project_path.resolve()
    revision = _publication_state(root)["published_revision"] + 1
    return build_dashboard_projection(root, published_revision=revision, thread_id=thread_id)


def _publisher_token(path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ProjectError("publisher token file must be a regular file")
    if os.name != "nt" and stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise ProjectError("publisher token file must not be accessible by group or others")
    token = resolved.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ProjectError("publisher token must contain at least 32 characters")
    return token


def _sites_auth_token(path: Path | None) -> str | None:
    if path is None:
        configured = os.environ.get("SOLERESEARCH_SITES_AUTH_TOKEN_FILE", "").strip()
        path = Path(configured) if configured else None
    return None if path is None else _publisher_token(path)


def _publish_url(site_url: str) -> str:
    parsed = urlsplit(site_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProjectError("site URL must be an absolute HTTP(S) URL without credentials")
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not loopback:
        raise ProjectError("publisher requires HTTPS except for a loopback development Site")
    return site_url.rstrip("/") + "/api/v1/publish"


def publish_dashboard_projection(
    project_path: Path,
    *,
    site_url: str,
    publisher_token_file: Path,
    sites_auth_token_file: Path | None = None,
    thread_id: str = "unknown",
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Publish one immutable revision; local research remains committed on failure."""
    root = project_path.resolve()
    outbox_path = root / PUBLICATION_OUTBOX
    projection = read_json(outbox_path) if outbox_path.is_file() else next_dashboard_projection(root, thread_id=thread_id)
    validate_document("dashboard_projection", projection)
    if projection["project_id"] != load_project(root)["project_id"]:
        raise ProjectError("Sites publication outbox belongs to another project")
    if not outbox_path.is_file():
        atomic_write_json(outbox_path, projection)
    token = _publisher_token(publisher_token_file)
    sites_token = _sites_auth_token(sites_auth_token_file)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if sites_token is not None:
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_token}"
    response: httpx.Response | None = None
    last_error: httpx.HTTPError | None = None
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, transport=transport) as client:
        for attempt in range(MAX_PUBLISH_ATTEMPTS):
            try:
                response = client.post(
                    _publish_url(site_url),
                    headers=headers,
                    content=canonical_json(projection).encode("utf-8"),
                )
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt + 1 < MAX_PUBLISH_ATTEMPTS:
                sleeper(0.25 * (2**attempt))
    if response is None:
        raise ProjectError(f"Sites publication failed without changing local research: {last_error}") from last_error
    if response.status_code not in {200, 201}:
        detail = response.text[:1000].strip()
        raise ProjectError(
            f"Sites publication failed without changing local research: HTTP {response.status_code}"
            + (f" ({detail})" if detail else "")
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise ProjectError("Sites publication returned invalid JSON") from exc
    accepted = result.get("published_revision") if isinstance(result, dict) else None
    if accepted != projection["published_revision"]:
        raise ProjectError("Sites publication response did not confirm the requested revision")
    atomic_write_json(
        root / PUBLICATION_STATE,
        {
            "project_id": projection["project_id"],
            "published_revision": projection["published_revision"],
            "project_revision": projection["project_revision"],
            "published_at": projection["produced_at"],
            "site_url": site_url.rstrip("/"),
        },
    )
    outbox_path.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "project_id": projection["project_id"],
        "project_revision": projection["project_revision"],
        "published_revision": projection["published_revision"],
        "site_url": site_url.rstrip("/"),
        "idempotent": bool(result.get("idempotent", False)),
    }
