from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest

from soleresearch.cli import _parser
from soleresearch.mcp_server import _argv, _leaf_tools, select_workspace_root, selected_workspace_root, serve_mcp
from soleresearch.project import initialize_project
from soleresearch.errors import ProjectError
from soleresearch.projection import build_dashboard_projection, configure_site, publication_status, publish_dashboard_projection, site_configuration
from soleresearch.schemas import tool_catalog


def test_projection_is_bounded_valid_and_contains_read_only_outline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    record = initialize_project(project, name="Transition test")
    projection = build_dashboard_projection(
        project,
        published_revision=4,
        thread_id="thread-test",
        now=lambda: "2026-07-18T12:00:00Z",
    )
    assert projection["projection_schema_version"] == "1.0.0"
    assert projection["project_id"] == record["project_id"]
    assert projection["published_revision"] == 4
    assert projection["outline"]["content"] == (project / "outline.md").read_text(encoding="utf-8")
    assert len(projection["project_revision"]) == 64
    assert len(projection["content_sha256"]) == 64
    assert projection["collections"]["nodes"] == {"total": 0, "included": 0, "truncated": False, "cursor": None}
    assert projection["truncated"] is False
    assert "controller" not in json.dumps(projection).casefold()


def test_truncated_projection_descriptor_binds_cursor_to_project_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    record = initialize_project(project)
    monkeypatch.setattr(
        "soleresearch.projection.ui_state",
        lambda _root, now: {
            "schema_version": 1,
            "project": {"project_id": record["project_id"], "name": "Bounded", "data_policy": "public_only"},
            "outline": {"hash": "sha256:" + "0" * 64, "dirty": False, "reconciliation_required": False},
            "views": {"nodes": {"items": [{}] * 500, "total": 501, "truncated": True}},
            "staleness": {},
            "errors": [],
        },
    )
    projection = build_dashboard_projection(project, published_revision=9)
    assert projection["truncated"] is True
    assert projection["collections"]["nodes"]["cursor"] == {
        "project_id": record["project_id"],
        "collection": "nodes",
        "published_revision": 9,
        "position": 0,
        "limit": 200,
    }


def test_publish_is_revisioned_and_records_success_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    record = initialize_project(project)
    token = tmp_path / "publisher.token"
    token.write_text("a" * 64, encoding="utf-8")
    token.chmod(0o600)
    sites_token = tmp_path / "sites.token"
    sites_token.write_text("b" * 64, encoding="utf-8")
    sites_token.chmod(0o600)
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/projects/{record['project_id']}/snapshots"
        assert request.headers["authorization"] == "Bearer " + "a" * 64
        assert request.headers["oai-sites-authorization"] == "Bearer " + "b" * 64
        payload = json.loads(request.content)
        observed.append(payload)
        return httpx.Response(201, json={"published_revision": payload["published_revision"], "idempotent": False})

    result = publish_dashboard_projection(
        project,
        site_url="http://127.0.0.1:3000",
        publisher_token_file=token,
        sites_auth_token_file=sites_token,
        transport=httpx.MockTransport(handler),
    )
    assert result["published_revision"] == 1
    assert observed[0]["published_revision"] == 1
    state = json.loads((project / ".soleresearch/sites-publication.json").read_text(encoding="utf-8"))
    assert state["published_revision"] == 1
    assert publication_status(project)["retry_pending"] is False


def test_failed_publish_keeps_exact_outbox_for_bounded_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    token = tmp_path / "publisher.token"
    token.write_text("a" * 64, encoding="utf-8")
    token.chmod(0o600)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ProjectError, match="without changing local research"):
        publish_dashboard_projection(
            project,
            site_url="http://127.0.0.1:3000",
            publisher_token_file=token,
            transport=httpx.MockTransport(handler),
            sleeper=lambda _delay: None,
        )
    status = publication_status(project)
    assert attempts == 3
    assert status["published_revision"] == 0
    assert status["pending_revision"] == 1
    assert status["retry_pending"] is True


def test_site_configuration_persists_only_external_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    publisher = tmp_path / "publisher.token"
    sites_auth = tmp_path / "sites.token"
    publisher.write_text("a" * 64, encoding="utf-8")
    sites_auth.write_text("b" * 64, encoding="utf-8")
    publisher.chmod(0o600)
    sites_auth.chmod(0o600)
    result = configure_site(
        site_url="https://example.chatgpt.site/",
        publisher_token_file=publisher,
        sites_auth_token_file=sites_auth,
    )
    assert result["site_url"] == "https://example.chatgpt.site"
    stored = site_configuration()
    assert stored["credential_files"] == "configured and validated"
    assert "publisher_token_file" not in stored and "sites_auth_token_file" not in stored
    assert "a" * 64 not in json.dumps(stored)


def test_mcp_has_one_named_tool_per_cli_leaf_and_preserves_nested_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_WORKSPACE_ROOT", str(tmp_path))
    tools = _leaf_tools(_parser())
    by_name = {tool.name: tool for tool in tools}
    assert "soleresearch_source_read" in by_name
    assert "soleresearch_publish" in by_name
    project = tmp_path / "project"
    project.mkdir()
    assert _argv(by_name["soleresearch_source_read"], {
        "project": str(project),
        "source_id": "src_" + "0" * 32,
        "state": "queued",
    }) == ["source", str(project), "read", "src_" + "0" * 32, "--state", "queued"]


def test_workspace_selection_is_explicit_external_and_persistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "research"
    workspace.mkdir()
    result = select_workspace_root(workspace)
    assert result["workspace_root"] == str(workspace)
    assert selected_workspace_root() == workspace
    assert not (workspace / "workspace.json").exists()


def test_project_tool_fails_closed_before_workspace_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOLERESEARCH_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    status_tool = {tool.name: tool for tool in _leaf_tools(_parser())}["soleresearch_status"]
    with pytest.raises(ProjectError, match="workspace selected"):
        _argv(status_tool, {"project": str(tmp_path / "project")})


def test_mcp_initialize_and_tool_listing_are_protocol_clean() -> None:
    incoming = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    )
    outgoing = io.StringIO()
    assert serve_mcp(_parser, input_stream=incoming, output_stream=outgoing) == 0
    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert messages[0]["result"]["protocolVersion"] == "2025-06-18"
    assert any(tool["name"] == "soleresearch_status" for tool in messages[1]["result"]["tools"])


def test_versioned_mcp_mapping_covers_the_catalog() -> None:
    mapping_path = Path(__file__).parents[1] / "src/soleresearch/contracts/v1/mcp_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    catalog_ids = {item["id"] for item in tool_catalog()["tools"]}
    assert set(mapping["catalog_tools"]) == catalog_ids
    actual_tools = {tool.name for tool in _leaf_tools(_parser())}
    mapped_tools = {name for names in mapping["catalog_tools"].values() for name in names}
    assert mapped_tools <= actual_tools
    assert mapping["catalog_tools"]["core.serve"] == []
