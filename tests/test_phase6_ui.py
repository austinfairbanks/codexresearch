from __future__ import annotations

from io import BytesIO
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from html.parser import HTMLParser

import pytest

from soleresearch.controller import capability_paths, read_controller_capability, require_controller
from soleresearch.discussions import DiscussionRepository
from soleresearch.errors import ProjectError, SchemaError
from soleresearch.graph import GraphRepository, new_node
from soleresearch.evidence import EvidenceRepository, locate_excerpt
from soleresearch.indexing import rebuild_index
from soleresearch.orchestration import RunRepository
from soleresearch.outline import text_hash
from soleresearch.project import initialize_project
from soleresearch.refresh import read_refresh_signal
from soleresearch.storage import read_jsonl
from soleresearch.sources import import_local_document, load_extraction
from soleresearch.ui import UIRequestHandler, _source_display_title, create_config, save_outline, serve, ui_state
from soleresearch.schemas import tool_catalog


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, name: str = "UI Project") -> tuple[Path, dict[str, object], str, str]:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "project"
    project = initialize_project(root, name=name)
    paths = capability_paths(project["project_id"])
    human = read_controller_capability(paths["human"])
    agent = read_controller_capability(paths["agent"])
    graph = GraphRepository(root)
    node = new_node("question", "What should we understand?", authority="human_accepted")
    proposed = graph.propose([{"op": "add", "target": "node", "record": node}], actor_type="human", actor_id="fixture")
    graph.apply(proposed["diff_id"], controller_token=human)
    return root, project, human, agent


class _OpenBytesIO(BytesIO):
    def close(self) -> None:
        pass


class _FakeSocket:
    def __init__(self, request: bytes) -> None:
        self.input = _OpenBytesIO(request)
        self.output = _OpenBytesIO()

    def makefile(self, mode: str, _buffering: int = -1):
        return self.input if "r" in mode else self.output

    def sendall(self, content: bytes) -> None:
        self.output.write(content)


def _request(config: object, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    payload = body or b""
    actual_headers = {"Host": "127.0.0.1", **(headers or {})}
    if payload and "Content-Length" not in actual_headers:
        actual_headers["Content-Length"] = str(len(payload))
    request = f"{method} {path} HTTP/1.1\r\n".encode() + b"".join(
        f"{key}: {value}\r\n".encode() for key, value in actual_headers.items()
    ) + b"\r\n" + payload
    socket = _FakeSocket(request)
    server = type("FakeServer", (), {"config": config})()
    UIRequestHandler(socket, ("127.0.0.1", 12345), server)
    head, response_body = socket.output.getvalue().split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    status = int(lines[0].split()[1])
    response_headers = {line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip() for line in lines[1:]}
    return status, response_headers, response_body


def test_read_only_ui_is_static_safe_and_file_derived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch, name='<img src=x onerror="alert(1)">')
    rebuild_index(root)
    expected = ui_state(root)
    (root / ".soleresearch/index.sqlite3").unlink()
    assert ui_state(root)["views"] == expected["views"]
    config = create_config(root, port=0)

    status, headers, body = _request(config, "GET", "/")
    assert status == 200
    assert b"<img src=x" not in body
    assert b"cdn" not in body.lower()
    assert "default-src 'self'" in headers["content-security-policy"]
    status, _headers, body = _request(config, "GET", "/api/v1/state")
    state = json.loads(body)
    assert status == 200 and state["schema_version"] == 1
    assert state["project"]["name"].startswith("<img")
    assert state["staleness"]["index_required"] is False

    status, _headers, _body = _request(
        config,
        "PUT",
        "/api/v1/outline",
        body=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "2"},
    )
    assert status == 403
    assert not (root / "events/human-edits.jsonl").read_bytes()


def test_ui_completion_endpoint_is_read_only_and_fail_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    signal_path = tmp_path / "completion.json"
    monkeypatch.setenv("SOLERESEARCH_REFRESH_SIGNAL", str(signal_path))
    config = create_config(root, port=0)

    status, _headers, body = _request(config, "GET", "/api/v1/completion")
    assert status == 200
    assert json.loads(body) == read_refresh_signal(signal_path)

    signal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "signal_id": "sig_0123456789abcdef0123456789abcdef",
                "completed_at": "2026-07-13T22:30:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    status, _headers, body = _request(config, "GET", "/api/v1/completion")
    assert status == 200
    assert json.loads(body)["signal_id"] == "sig_0123456789abcdef0123456789abcdef"


def test_edit_ui_etag_csrf_anchor_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project, human, _agent = _project(tmp_path, monkeypatch)
    config = create_config(root, port=0, edit=True, controller_token=human)
    status, headers, body = _request(config, "GET", "/api/v1/outline")
    outline = json.loads(body)
    digest = outline["outline_hash"]
    changed = outline["content"].replace("What should we understand?", "What wear patterns matter?")
    payload = json.dumps({"schema_version": 1, "content": changed, "base_hash": digest}).encode()
    request_headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(payload)),
        "X-CSRF-Token": config.csrf_token,
        "If-Match": f'"{digest}"',
    }

    missing_csrf = {key: value for key, value in request_headers.items() if key != "X-CSRF-Token"}
    assert _request(config, "PUT", "/api/v1/outline", body=payload, headers=missing_csrf)[0] == 403
    bad_anchor = json.dumps({"schema_version": 1, "content": changed.replace("soleresearch:node", "wrong:node"), "base_hash": digest}).encode()
    assert _request(config, "PUT", "/api/v1/outline", body=bad_anchor, headers={**request_headers, "Content-Length": str(len(bad_anchor))})[0] == 409

    status, response_headers, response_body = _request(config, "PUT", "/api/v1/outline", body=payload, headers=request_headers)
    result = json.loads(response_body)
    assert status == 200 and result["saved"] is True
    assert response_headers["etag"] == f'"{result["outline_hash"]}"'
    assert (root / "outline.md").read_text(encoding="utf-8") == changed
    events = read_jsonl(root / "events/human-edits.jsonl")
    assert len(events) == 1
    assert events[0]["project_id"] == project["project_id"]
    assert events[0]["actor"]["capability_id"].startswith("cap_")
    assert "token" not in json.dumps(events[0]).lower()
    assert events[0]["semantic_summary"]["title_changes"] == 1
    assert _request(config, "PUT", "/api/v1/outline", body=payload, headers=request_headers)[0] == 409

    restarted = create_config(root, port=0, edit=True, controller_token=human)
    status, _headers, body = _request(restarted, "GET", "/api/v1/outline")
    assert status == 200 and json.loads(body)["content"] == changed


def test_ui_annotations_record_human_direction_without_editing_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, human, _agent = _project(tmp_path, monkeypatch)
    config = create_config(root, port=0, edit=True, controller_token=human)
    node_id = ui_state(root)["views"]["nodes"]["items"][0]["node_id"]
    before = (root / "outline.md").read_bytes()
    payload = json.dumps({"schema_version": 1, "node_id": node_id, "content": "Prioritize heel strike asymmetry."}).encode()
    headers = {"Content-Type": "application/json", "X-CSRF-Token": config.csrf_token}

    assert _request(config, "POST", "/api/v1/annotations", body=payload, headers={"Content-Type": "application/json"})[0] == 403
    status, _headers, body = _request(config, "POST", "/api/v1/annotations", body=payload, headers=headers)

    assert status == 201
    annotation = json.loads(body)["annotation"]
    assert annotation["entity_type"] == "node" and annotation["entity_id"] == node_id
    assert annotation["actor_type"] == "human"
    assert annotation["content"] == "Prioritize heel strike asymmetry."
    assert (root / "outline.md").read_bytes() == before
    assert ui_state(root)["views"]["discussions"]["items"][0]["content"] == annotation["content"]
    pending = ui_state(root)["views"]["annotations"]["items"][0]
    assert pending["status"] == "awaiting_agent"
    DiscussionRepository(root).add(
        entity_type="node",
        entity_id=node_id,
        content="I will prioritize the asymmetry evidence in the next proposal.",
        actor_type="orchestrator",
        actor_id="test-orchestrator",
        discussion_id=annotation["discussion_id"],
    )
    responded = ui_state(root)["views"]["annotations"]["items"][0]
    assert responded["status"] == "agent_responded"
    assert responded["response"].startswith("I will prioritize")


def test_ui_binding_capability_host_and_method_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, human, agent = _project(tmp_path, monkeypatch)
    with pytest.raises(ProjectError, match="human capability"):
        create_config(root, port=0, edit=True, controller_token=agent)
    with pytest.raises(ProjectError, match="unsafe-non-loopback"):
        create_config(root, host="0.0.0.0", port=0)
    with pytest.raises(ProjectError, match="disabled"):
        create_config(root, host="0.0.0.0", port=0, unsafe_non_loopback=True, edit=True, controller_token=human)
    config = create_config(root, port=0)
    assert _request(config, "POST", "/api/v1/state", body=b"")[0] == 404
    assert _request(config, "GET", "/", headers={"Host": "evil.example"})[0] == 400
    assert _request(config, "GET", "/../../project.json")[0] == 404
    container = create_config(root, host="0.0.0.0", port=0, unsafe_non_loopback=True)
    assert _request(container, "GET", "/")[0] == 200
    assert _request(container, "GET", "/", headers={"Host": "evil.example"})[0] == 400
    explicit = create_config(root, host="0.0.0.0", port=0, unsafe_non_loopback=True, allowed_hosts=("research.example",))
    assert _request(explicit, "GET", "/", headers={"Host": "research.example"})[0] == 200


def test_ui_workspace_discovers_independent_project_directories_and_scopes_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, human, _agent = _project(tmp_path, monkeypatch)
    other = tmp_path / "another-question"
    other_project = initialize_project(other, name="Independent outsole question")
    other_human = read_controller_capability(capability_paths(other_project["project_id"])["human"])
    graph = GraphRepository(other)
    question = new_node("question", "How does outsole geometry affect wear?", authority="human_accepted")
    proposal = graph.propose([{"op": "add", "target": "node", "record": question}], actor_type="human", actor_id="fixture")
    graph.apply(proposal["diff_id"], controller_token=other_human)
    config = create_config(root, port=0, edit=True, controller_token=human, workspace_root=tmp_path)

    status, _headers, body = _request(config, "GET", "/api/v1/workspace")
    workspace = json.loads(body)
    assert status == 200
    assert workspace["default_project_id"] != other_project["project_id"]
    assert {item["directory"] for item in workspace["projects"]} == {"project", "another-question"}
    other_summary = next(item for item in workspace["projects"] if item["project_id"] == other_project["project_id"])
    assert other_summary["questions"] == ["How does outsole geometry affect wear?"]
    assert other_summary["preview"]["total"] == 1
    assert other_summary["preview"]["nodes"][0]["node_id"] == question["node_id"]
    assert other_summary["editable"] is False
    assert other_summary["available"] is True and other_summary["availability_error"] is None

    query = f"?project={other_project['project_id']}"
    status, _headers, body = _request(config, "GET", "/api/v1/state" + query)
    assert status == 200 and json.loads(body)["project"]["name"] == "Independent outsole question"
    annotation = json.dumps({"schema_version": 1, "node_id": question["node_id"], "content": "Keep this isolated."}).encode()
    status, _headers, _body = _request(
        config,
        "POST",
        "/api/v1/annotations" + query,
        body=annotation,
        headers={"Content-Type": "application/json", "X-CSRF-Token": config.csrf_token},
    )
    assert status == 403
    assert _request(config, "GET", "/api/v1/state?project=prj_missing")[0] == 409


def test_ui_save_respects_active_run_controller_and_clean_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project, human, _agent = _project(tmp_path, monkeypatch)
    capability = require_controller(project["project_id"], human)
    before = (root / "outline.md").read_text(encoding="utf-8")
    changed = before.replace("What should we understand?", "Human clean-gate edit")

    sol_run = RunRepository.create(root, controller="sol")
    with pytest.raises(ProjectError, match="clean human-controlled gate"):
        save_outline(root, content=changed, base_hash=text_hash(before), capability=capability)
    # Finish the clean Sol run with its active controller, then start a human run.
    agent_token = read_controller_capability(capability_paths(project["project_id"])["agent"])
    sol_run.finish("test boundary", controller_token=agent_token)
    human_run = RunRepository.create(root, controller="human")
    result = save_outline(root, content=changed, base_hash=text_hash(before), capability=capability)
    assert result["saved"] is True
    assert human_run.status()["gate"]["status"] == "clean"


def test_wrappers_and_tmux_dry_run_preserve_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path / "path with spaces", monkeypatch)
    package = Path(__file__).parents[1]
    for script in (package / "scripts/sole-research-docker", package / "scripts/serve-ui-docker"):
        checked = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False)
        assert checked.returncode == 0, checked.stderr
    launched = subprocess.run(
        [sys.executable, str(package / "scripts/launch-tmux.py"), str(root), "--session", "sole-test", "--dry-run", "--", "codex", "--prompt", "two words"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert launched.returncode == 0
    assert "'two words'" in launched.stdout
    assert f"127.0.0.1:8765" in launched.stdout
    assert str(root) in launched.stdout


def test_serve_treats_keyboard_interrupt_as_clean_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)

    class InterruptedServer:
        server_port = 8765
        closed = False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed = True

    server = InterruptedServer()
    monkeypatch.setattr("soleresearch.ui.create_server", lambda *args, **kwargs: server)
    serve(root)
    assert server.closed is True


def test_ui_static_contract_has_accessible_tabs_and_transparent_fields() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    html = (package / "index.html").read_text(encoding="utf-8")
    javascript = (package / "app.js").read_text(encoding="utf-8")

    class Semantics(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.tabs: list[dict[str, str | None]] = []
            self.panels: list[dict[str, str | None]] = []
            self.statuses: list[dict[str, str | None]] = []

        def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
            value = dict(attrs)
            if value.get("role") == "tab": self.tabs.append(value)
            if value.get("role") == "tabpanel": self.panels.append(value)
            if value.get("role") == "status": self.statuses.append(value)

    parsed = Semantics()
    parsed.feed(html)
    assert len(parsed.tabs) == len(parsed.panels) == 7
    assert {item["aria-controls"] for item in parsed.tabs} == {item["id"] for item in parsed.panels}
    assert all(item.get("aria-selected") in {"true", "false"} for item in parsed.tabs)
    assert len(parsed.statuses) == 1 and parsed.statuses[0]["aria-live"] == "polite"
    for required in ("ArrowRight", "ArrowLeft", "Home", "End", "human_reading_state", "methodology_transparency", "start_char", "end_char", "source_title", "pending_decisions", "Run event history", "chronological audit events", "focusEvidence", "evidence-jump", "scrollIntoView"):
        assert required in javascript
    assert "source.reading_state" not in javascript
    assert "innerHTML" not in javascript
    assert "link.href = sourceUrl" in javascript
    serve_tool = next(item for item in tool_catalog()["tools"] if item["id"] == "core.serve")
    assert serve_tool["authority"] == "action_dependent"
    assert serve_tool["authority_modes"] == [
        {"condition": "read-only (default)", "authority": "none"},
        {"condition": "--edit", "authority": "human_controller"},
    ]


def test_ui_redesign_has_semantic_hierarchy_and_local_design_system() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    html = (package / "index.html").read_text(encoding="utf-8")
    css = (package / "app.css").read_text(encoding="utf-8")

    class Structure(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.tags: list[tuple[str, dict[str, str | None]]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.tags.append((tag, dict(attrs)))

    parsed = Structure()
    parsed.feed(html)
    assert any(tag == "a" and attrs.get("class") == "skip-link" and attrs.get("href") == "#map-canvas" for tag, attrs in parsed.tags)
    assert any(tag == "main" and attrs.get("class") == "workbench" for tag, attrs in parsed.tags)
    assert not any(tag == "footer" for tag, _attrs in parsed.tags)
    assert not any("app-header" in (attrs.get("class") or "").split() or "app-footer" in (attrs.get("class") or "").split() for _tag, attrs in parsed.tags)
    for hidden_status_id in ("project-name", "mode-badge", "save-state", "refresh-state"):
        assert any(attrs.get("id") == hidden_status_id for _tag, attrs in parsed.tags)
    assert any(tag == "section" and attrs.get("class") == "activity-rail" and attrs.get("aria-label") == "Live research activity" for tag, attrs in parsed.tags)
    for activity_id in ("activity-run-state", "activity-cycle", "activity-controller", "activity-progress", "activity-agent-preview", "activity-telemetry", "activity-agent-list"):
        assert any(attrs.get("id") == activity_id for _tag, attrs in parsed.tags)
    for removed_activity_id in ("activity-gate", "activity-agents", "activity-budget"):
        assert not any(attrs.get("id") == removed_activity_id for _tag, attrs in parsed.tags)
    assert any(tag == "details" and attrs.get("id") == "agent-activity" and "open" not in attrs for tag, attrs in parsed.tags)
    assert any(tag == "div" and attrs.get("id") == "activity-agent-list" and attrs.get("aria-label") == "Current agent assignments" for tag, attrs in parsed.tags)
    assert any(tag == "section" and attrs.get("class") == "map-pane" for tag, attrs in parsed.tags)
    assert any(tag == "section" and "draft-pane" in (attrs.get("class") or "").split() for tag, attrs in parsed.tags)
    assert any(tag == "div" and attrs.get("id") == "map-canvas" and attrs.get("role") == "region" for tag, attrs in parsed.tags)
    assert any(tag == "nav" and attrs.get("id") == "map-breadcrumb" and attrs.get("aria-label") == "Focused research path" for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "map-zoom-out" and attrs.get("aria-label") == "Zoom map out" for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "map-back" and "disabled" in attrs for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "map-fit" for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "map-zoom-in" and attrs.get("aria-label") == "Zoom map in" for tag, attrs in parsed.tags)
    assert any(tag == "aside" and attrs.get("id") == "research-inspector" and attrs.get("role") == "tabpanel" and attrs.get("aria-labelledby") == "context-tab-inspect" and "hidden" in attrs for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "context-tab-draft" and attrs.get("data-context-view") == "draft" for tag, attrs in parsed.tags)
    assert any(tag == "button" and attrs.get("id") == "context-tab-inspect" and attrs.get("data-context-view") == "inspect" for tag, attrs in parsed.tags)
    assert any(tag == "div" and attrs.get("id") == "outline-preview" and attrs.get("role") == "document" for tag, attrs in parsed.tags)
    assert any(tag == "details" and attrs.get("id") == "canonical-outline" for tag, attrs in parsed.tags)
    assert any(tag == "textarea" and attrs.get("id") == "outline-editor" and "readonly" in attrs for tag, attrs in parsed.tags)
    assert any(tag == "input" and attrs.get("id") == "research-search" and attrs.get("type") == "search" for tag, attrs in parsed.tags)
    assert any(tag == "select" and attrs.get("id") == "coverage-filter" for tag, attrs in parsed.tags)
    assert any(tag == "details" and attrs.get("class") == "map-find" for tag, attrs in parsed.tags)
    assert not any((attrs.get("id") or "").startswith("annotation-") for _tag, attrs in parsed.tags)
    assert any(
        tag == "div"
        and attrs.get("id") == "split-divider"
        and attrs.get("role") == "separator"
        and attrs.get("aria-orientation") == "vertical"
        and attrs.get("tabindex") == "0"
        for tag, attrs in parsed.tags
    )
    assert not any(attrs.get("id") == "save-button" for _tag, attrs in parsed.tags)
    assert all(attrs.get("tabindex") == "0" for tag, attrs in parsed.tags if attrs.get("role") == "tabpanel")
    assert html.index('class="map-pane"') < html.index('class="draft-pane context-pane"')
    assert html.count("<script") == 2
    assert html.index('src="/assets/layout.js"') < html.index('src="/assets/app.js"')
    assert "http://" not in html and "https://" not in html
    assert "product-mark" not in html
    assert "Authoritative files · exact-locator evidence · visible decisions" not in html
    assert "Research map" in html and "Research inspector" in html
    assert "Task progress" in html and "Agent assignments" in html
    assert "Human gate" not in html and "Assigned workers" not in html and "Budget left" not in html
    assert '>Overview</button>' in html and '>Evidence</button>' in html and '>Notes</button>' in html
    assert 'id="tab-sources"' not in html and 'id="panel-sources"' not in html
    assert 'id="inspector-more"' in html and '>Activity</button>' in html and '>History</button>' in html
    assert '>Focus</button>' not in html
    assert html.index('id="map-workspace"') < html.index('id="outline-preview"')
    assert 'id="map-summary"' not in html and 'id="map-label"' not in html
    assert html.index('class="map-navigation"') < html.index('id="map-canvas"')

    for token in (
        "--space-1: 4px",
        "--space-2: 8px",
        "--space-3: 12px",
        "--space-4: 16px",
        "--space-6: 24px",
        "--space-8: 32px",
        "--space-12: 48px",
        "--forest:",
        "--rust:",
        "--focus:",
    ):
        assert token in css
    assert "min-width: 44px" in css and "min-height: 44px" in css
    assert "outline: 3px solid var(--focus)" in css
    assert "@media (max-width: 1100px)" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 480px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "grid-template-columns: minmax(0, var(--split-position)) 12px minmax(0, 1fr)" in css
    assert ".split-divider" in css and "cursor: col-resize" in css
    assert ".inspector[hidden] { display: none; }" in css and "position: static" in css
    assert ".context-tabs" in css and ".context-tab.active" in css
    assert ".inspector-more" in css and ".inspector-secondary-tabs" in css
    assert ".evidence-mode-switch" in css and '.evidence-mode-button[aria-pressed="true"]' in css
    assert ".overview-metrics" in css and ".inspector-search" in css and ".show-more-button" in css
    assert ".note-direction" in css and ".past-runs" in css
    assert ".outline-node-section.draft-focus" in css
    assert '.outline-node-section[data-outline-depth="1"] { --draft-hierarchy-indent: 10px; }' in css
    assert '.outline-node-section[data-outline-depth="4"] { --draft-hierarchy-indent: 40px; }' in css
    assert "padding-inline-start: calc(var(--space-4) + var(--draft-hierarchy-indent))" in css
    assert '.outline-node-section[data-synthesis-detail="true"]' in css
    assert ".draft-section-kicker" in css and ".outline-question-section" in css
    assert ".outline-preview > h2:first-child" in css
    assert "padding-inline: clamp(var(--space-3), 2vw, 28px)" in css
    assert "width: min(100%, 92ch)" in css
    assert ".map-node-root" in css and ".map-edge-contains" in css and "border-radius: 50%" in css
    assert ".map-pane" in css and "padding: 0" in css
    assert ".map-find" in css and "position: absolute" in css
    assert "margin: 0" in css and ".map-find[open] > summary" in css
    assert ".evidence-satellite" in css and ".evidence-satellite[hidden]" in css
    assert ".map-hover-tooltip" in css and ".map-hover-tooltip[hidden]" in css
    assert ".evidence-source-link" in css
    assert ".inline-source-link" in css
    assert ".section-provenance" in css and ".annotation-composer" not in css
    assert ".map-node.map-filtered-out" in css
    assert ".workspace-question-root" in css and ".workspace-branch" in css
    assert "width: var(--workspace-root-size, 280px)" in css
    assert "width: var(--workspace-branch-size, 88px)" in css
    assert ".workspace-branch-small" in css and ".workspace-branch-large" in css
    assert ".map-canvas.map-zoom-far" in css and ".map-canvas.map-zoom-very-far" in css
    assert ".map-canvas.workspace-overview" in css
    assert ".map-hover-tooltip.below" in css
    assert ".outline-question-section" in css
    assert "text-wrap: balance" in css and "overflow-wrap: anywhere" in css
    assert "touch-action: none" in css and "cursor: grab" in css
    assert "font: 1.0625rem/1.75 var(--font-reading)" in css
    assert "gradient(" not in css and "backdrop-filter" not in css

    def relative_luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
        return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]

    def token(name: str) -> str:
        match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", css)
        assert match is not None
        return match.group(1)

    def contrast(foreground: str, background: str) -> float:
        foreground_luminance = relative_luminance(token(foreground))
        background_luminance = relative_luminance(token(background))
        return (max(foreground_luminance, background_luminance) + .05) / (min(foreground_luminance, background_luminance) + .05)

    text_pairs = (
        ("--ink", "--surface"),
        ("--ink-secondary", "--surface"),
        ("--ink-tertiary", "--canvas"),
        ("--surface", "--forest"),
        ("--rust-dark", "--rust-soft"),
    )
    for foreground, background in text_pairs:
        ratio = contrast(foreground, background)
        assert ratio >= 4.5, f"{foreground} against {background} is only {ratio:.2f}:1"
    for background in ("--surface", "--canvas"):
        ratio = contrast("--focus", background)
        assert ratio >= 3, f"focus against {background} is only {ratio:.2f}:1"

    root_block = css.split("}", 1)[0]
    body_css = css.split("}", 1)[1]
    assert re.findall(r"#[0-9a-fA-F]{6}", root_block)
    assert not re.findall(r"#[0-9a-fA-F]{6}", body_css), "component colors must use design tokens"


def test_ui_redesign_uses_progressive_disclosure_and_stable_refresh() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    javascript = (package / "app.js").read_text(encoding="utf-8")
    for required in (
        'element("blockquote", record.excerpt, "evidence-quote")',
        'element("details")',
        'element("progress")',
        'element("article", null, "timeline-item")',
        "renderedSignatures",
        "viewSnapshot(activeView)",
        "renderedSignatures[activeView] === signature",
        "content.scrollTop = scrollTop",
        "target.focus({preventScroll: true})",
        "function humanize(value)",
        "function safeWebUrl(value)",
        "function appendInlineLinks(item, text)",
        "function appendRichTextParagraphs(item, text, className)",
        "function proseClause(title)",
        "function nodeEvidenceSources(node)",
        "function appendSynthesisAttribution(item, node)",
        'element("a", match[1], "inline-source-link")',
        'link.rel = "noopener noreferrer"',
        'event.stopPropagation()',
        "function coverageLabel(state)",
        "function renderDraftProvenance(target, nodeId)",
        "function applyMapFilters(",
        'researchSearch.addEventListener("input"',
        'coverageFilter.addEventListener("change"',
        "mapFind.open = true",
        "mapFind.open = false",
        "function setSplitPosition(ratio",
        "function splitRatioFromPointer(clientX)",
        'splitDivider.addEventListener("pointerdown"',
        'splitDivider.addEventListener("keydown"',
        'splitDivider.addEventListener("dblclick"',
        'element("a", record.source_title, "evidence-source-link")',
        'link.href = sourceUrl',
        'details("Technical run details"',
        'placeholder: "Search research history"',
        "readableLines(entry.summary)",
        '`Research run · ${humanize(run.status)}`',
        "let initialLoadComplete = false",
        "function failInitialLoad(error)",
        "if (initialLoadComplete) return",
        "function pollCompletionSignal()",
        'fetch("/api/v1/completion", {cache: "no-store"})',
        "completionRefreshQueued = true",
        "pollCompletionSignal().catch(() => {})",
        "previewContent = null",
        "renderOutlinePreview(outline.content)",
        "function readerParagraphs(text)",
        "function appendEvidenceText(item, text)",
        'target.dataset.terminalSynthesis === "true"',
        'target.dataset.synthesisDetail === "true"',
        'destination.dataset.synthesisDetail === "true"',
        'section.dataset.terminalSynthesis = mappedNode && (mappedNode.tags || []).includes("terminal-synthesis") ? "true" : "false"',
        'section.dataset.synthesisDetail = synthesisDetail ? "true" : "false"',
        'organized in ${hiddenBranchCount} expandable ${hiddenBranchCount === 1 ? "branch" : "branches"}',
        'element("strong", `${displayKind} ${destination.dataset.claimNumber} — ${title}.`)',
        'labeledParagraph("Evidence"',
        'nodeType === "gap" || missingEvidence ? "Limitation"',
        'element("p", "Research question", "draft-section-kicker")',
        "outlinePreview.replaceChildren(fragment)",
        "soleresearch:anchor\\s+root",
        "AI draft · chat directed",
        "humanize(edge.edge_type)",
        'tab.scrollIntoView({block: "nearest", inline: "nearest"})',
            "function renderPrimaryMap({preserveCamera = false} = {})",
        'element("button", `Root {${rootCount}}`, "map-crumb")',
        'element("button", `Branch {${project.directory}}`, "map-crumb")',
        "function renderWorkspaceMap()",
        'mapCanvas.classList.add("workspace-overview")',
        'mapCanvas.classList.remove("workspace-overview")',
        " workspace-question-root",
        "workspace-branch${branchSizeClass}",
        "rootSizeUnit * (2 + normalized * 3)",
        "minimumBranchSize = Math.round(evidenceNodeSize * 1.5)",
        "maximumBranchSize = evidenceNodeSize * 2",
        "minimumBranchSize + normalized * (maximumBranchSize - minimumBranchSize)",
        'button.style.setProperty("--workspace-branch-size"',
        'mapCanvas.classList.toggle("map-zoom-far"',
        'mapCanvas.classList.toggle("map-zoom-very-far"',
        "async function switchWorkspaceProject(projectId, targetNodeId = null)",
        "function showWorkspaceOverview()",
        'fetch("/api/v1/workspace"',
        'crumb.setAttribute("aria-label", `${role}: ${node.title}`)',
        'crumb.title = node.title',
        '? "Branch"',
        ': `Sub-branch ${index}`',
        '? "Leaf"',
        "function renderActivity()",
        "function selectNode(nodeId",
        "Individual tool calls are not captured",
        "function selectedResearchContext()",
        'placeholder: activeEvidenceMode === "sources" ? "Search sources" : "Search evidence passages"',
        'viewHeader(scopedView, activeEvidenceMode === "sources" ? "Sources for selected context" : "Evidence for selected context")',
        'snapshot.views.annotations.items.filter((entry) => entry.entity_id === noteNode.node_id)',
        'viewHeader(view, noteNode && noteNode.node_id !== selectedNodeId ? "Notes from nearest parent context" : "Notes for selected context")',
        '`Review ${context.evidence.length} evidence ${context.evidence.length === 1 ? "passage" : "passages"} from ${context.sources.length}',
        "function moveCameraToNode(mapNodeId",
        "function followRouteToNode(nodeId)",
        "workspaceOverview = workspace.projects.length > 1",
        "const overviewReturn = workspaceOverview",
        "cameraHistory = overviewReturn ? [overviewReturn] : []",
        "const workspaceNodeId = `workspace:${projectId}:${targetNodeId}`",
        "selectedId: targetNodeId",
        "focus: true, selectedId: targetNodeId",
        "function updateMapCamera()",
        "function fitGraph(",
        "function cameraState()",
        "function reflectSelectedTopicInUrl()",
        "new URLSearchParams({project: activeProjectId, topic: selectedNodeId})",
        "window.history.replaceState",
        "focusRootId",
        "cameraHistory",
        "graphPositions",
        'stage.setAttribute("role", "tree")',
        'routeStage.setAttribute("role", "presentation")',
        'svgElement("line"',
        'svgElement("path"',
        'overview.setAttribute("aria-current", "page")',
        "function surfaceDraftNode(nodeId",
        "function updateEvidenceSatellites()",
        "function attachMapTooltip(target, text)",
        "function showMapTooltip(target, text)",
        "const maximumTop = Math.max(12, canvasRect.height - tooltipRect.height - 12)",
        "function selectDraftNode(nodeId)",
        'attachMapTooltip(button, node.title)',
        "attachMapTooltip(satellite, evidenceTitle)",
        'section.addEventListener("click", () => selectDraftNode(nodeAnchor[1]))',
        '[["passages", "Passages"], ["sources", "Sources"]]',
        'activeEvidenceMode = "passages"',
        "function renderSearchableCollection(",
        "function sourceCard(source)",
        'inspectorMore.open = Boolean(tab.closest(".inspector-secondary-tabs"))',
        'if (!groupTabs.some((item) => item.getAttribute("aria-selected") === "true") && groupTabs.length) groupTabs[0].tabIndex = 0',
        'if (!inspectorMore.open && (activeView === "runs" || activeView === "audit"))',
        "const groupTabs = tablist ? Array.from(tablist.querySelectorAll",
        "function appendEvidenceSatellites(",
        "const selectedMapNodeId = workspaceOverview ? focusRootId : selectedNodeId",
        "satellite.dataset.evidenceParentId = parentId",
        "cameraX = -satelliteX * cameraScale",
        "parentId: key",
        'element("span", `${displayKind} ${branchNumbers.get(`${project.project_id}:${node.node_id}`) || "—"}`',
        "satellite.dataset.tooltip = evidenceTitle",
        'activateContextTab("inspect")',
        "focusEvidence(evidenceId)",
        'event.target.closest("[data-node-id], [data-evidence-id], [data-destination-node-id]")',
        "function activateContextTab(view",
        "activeContextView",
        "contextPanels",
        'section.dataset.outlineNodeId = nodeAnchor[1]',
        "section.dataset.outlineDepth = String(Math.min(4, Math.max(0, mappedDepth)))",
        "let pendingHeading = null",
        "flushPendingHeading(section)",
        'target.scrollIntoView({behavior: "smooth", block: "start"})',
        'activeContextView === "draft"',
        'mapCanvas.addEventListener("pointerdown"',
        'mapCanvas.addEventListener("keydown"',
        'event.target.closest("[data-project-id][data-target-node-id]")',
        'mapCanvas.addEventListener("wheel"',
        'mapBack.addEventListener("click"',
    ):
        assert required in javascript


def test_ui_primary_map_uses_hierarchical_claims_and_depth_scaled_focus() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    javascript = (package / "app.js").read_text(encoding="utf-8")
    css = (package / "app.css").read_text(encoding="utf-8")

    for required in (
        "function hierarchicalClaimNumbers(nodes, byId, children)",
        "function nodeDisplayKind(node, depth, childCount)",
        "function initializeBranchExpansion({byId, children})",
        "const firstTier = children.get(root.node_id) || []",
        "firstTier.forEach((topic)",
        "function visibleGraph({nodes, byId, children})",
        "function collapseUnfocusedBranches(nodeId)",
        "function revealNodePath(nodeId)",
        'if (node.node_type === "gap") return "Open question"',
        'if (depth === 1) return "Topic"',
        'return "Section"',
        'return "Claim"',
        "function displayClaimTitle(title)",
        "function focusScaleForDepth(depth)",
        "const claimNumbers = hierarchicalClaimNumbers(allNodes, byId, children)",
        "const displayTitle = displayClaimTitle(node.title)",
        "map-node-depth-${Math.min(position.depth, 4)}",
        'element("span", `${displayKind} ${claimNumber}`, "node-type")',
        'button.dataset.claimNumber = claimNumber',
        "button.dataset.nodeKind = displayKind",
        "cameraScale = position.depth === 0",
        ": Math.max(cameraScale, focusScaleForDepth(position.depth))",
        'mapCanvas.classList.toggle("map-focus-active", Boolean(focusRootId))',
        "evidenceAngle: Math.atan2(",
        'parent.classList.toggle("evidence-expanded", expanded)',
        'mapCanvas.classList.toggle("map-evidence-focus", anyExpanded)',
        "depth >= 2 && childCount > 0",
        "position.depth >= 2 && childCount > 0",
        'button.setAttribute("aria-expanded", branchExpanded ? "true" : "false")',
        'element("span", branchExpanded ? "−" : "+", "branch-toggle-mark")',
        'expanded: true',
        "occupiedCircles",
        'liveStatus.textContent = closePreview ? "Evidence preview closed"',
        "renderPrimaryMap({preserveCamera: true})",
        'mapCanvas.querySelector(".map-stage")',
        "if (preserveCamera) updateMapCamera()",
    ):
        assert required in javascript

    assert ".map-node-depth-1" in css
    assert ".map-node-root { width: 330px; height: 330px" in css
    assert "if (depth <= 0) return 330" in javascript
    assert "const graphMargin = nodeSizeForDepth(0) / 2 + 24" in javascript
    assert "max-width: 228px" in css
    assert "width: 172px" in css and "height: 172px" in css
    assert ".map-node-depth-1 .map-node-title" in css
    assert css.count("transform: translate(-50%, -50%);") >= 2
    assert ".map-node.map-node-root:not(.workspace-question-root) > .node-type" in css
    assert ".map-node.map-node-root:not(.workspace-question-root) .map-node-title" in css
    assert ".map-node-depth-2" in css
    assert "width: 64px" in css and "height: 64px" in css
    assert ".map-node-depth-3" in css
    assert "width: 44px" in css and "height: 44px" in css
    assert ".map-node-depth-4" in css
    assert "width: 32px" in css and "height: 32px" in css
    assert ".map-node-depth-2::after" in css
    assert ".map-node.map-node-depth-2 > .node-type" in css
    assert ".map-node.map-node-depth-2 .map-node-title" in css
    assert "position: static" in css
    assert "width: 52px" in css and "max-height: 38px" in css
    assert "width: 34px" in css and "max-height: 25px" in css
    assert "width: 25px" in css and "max-height: 18px" in css
    assert "transform: none" in css
    assert ".map-label-right .map-node-title" not in css
    assert "claimLabelSide" not in javascript
    assert ".map-canvas.map-evidence-focus .map-node:not(.evidence-expanded)" in css
    assert ".map-canvas.map-evidence-focus .map-edge" in css
    assert ".branch-toggle-mark" in css
    assert "--evidence-node-size: 28px" in css
    assert ".map-canvas.map-focus-active" in css
    assert "transition: transform 300ms cubic-bezier(.22, 1, .36, 1)" in css
    assert "parentDepth = 0" in javascript
    assert "const showTitlePreviews = parentDepth >= 3 && evidenceIds.length <= 6" in javascript
    assert 'satellite.classList.toggle("evidence-satellite-preview", showTitlePreviews)' in javascript
    assert 'showTitlePreviews ? "evidence-satellite-title" : "evidence-satellite-mark"' in javascript
    assert "geometry.placeSatellites" in javascript
    assert "geometry.displayedRadius(visualSize, 1.06)" in javascript
    assert ".evidence-satellite-preview" in css
    assert "width: 60px" in css and "height: 60px" in css
    assert ".evidence-satellite-title" in css
    assert "cameraScale = Math.max(cameraScale, 4)" in javascript
    assert "geometry.layoutForest" in javascript
    assert "geometry.validateCircles" in javascript
    assert "geometry.boundsForCircles" in javascript
    assert 'fetch(projectApi("/api/v1/outline")' in javascript
    assert 'fetch(projectApi("/api/v1/state")' in javascript
    assert 'method: "PUT"' not in javascript
    assert 'method: "POST"' not in javascript and "X-CSRF-Token" not in javascript and "If-Match" not in javascript
    assert "window.setInterval" in javascript and "5000" in javascript
    assert "innerHTML" not in javascript and "insertAdjacentHTML" not in javascript
    assert "card(run.run_id" not in javascript
    assert 'element("h3", entry.type)' not in javascript
    assert 'typeof value === "string" ? humanize(value) : value' not in javascript
    assert 'return [`${prefix ? `${prefix}: ` : ""}${value}`]' in javascript
    assert 'event.key === "ArrowDown"' in javascript and 'event.key === "ArrowUp"' in javascript
    assert "let content = panels.map" not in javascript
    assert "editor.readOnly = false" not in javascript
    assert "editor.readOnly = true" in javascript
    assert "loadState({announce: true})" in javascript and ".catch(failInitialLoad)" in javascript
    assert "/api/v1/annotations" not in javascript
    failure_body = javascript.split("function failInitialLoad(error) {", 1)[1].split("\n}", 1)[0]
    assert failure_body.index("if (initialLoadComplete) return") < failure_body.index("editor.readOnly = true")
    assert failure_body.index("previewContent = null") < failure_body.index("outlinePreview.replaceChildren")


def test_ui_map_first_routes_and_prose_first_sources_are_present() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    javascript = (package / "app.js").read_text(encoding="utf-8")
    css = (package / "app.css").read_text(encoding="utf-8")
    html = (package / "index.html").read_text(encoding="utf-8")

    for required in (
        "function routeCue(node)",
        "function appendRouteControl(",
        "function appendRootPathGuide(",
        "function positionRootPathGuides()",
        "function scheduleRootPathGuideLayout()",
        "rootGuideLayoutFrame = window.requestAnimationFrame",
        'route.className = "map-route-control"',
        'route.dataset.destinationNodeId = destinationId',
        'route.setAttribute("aria-label", `Follow path to ${destinationTitle}`)',
        "geometry.isRouteActivation({type: event.type, key: event.key})",
        "if (!routePosition.persistent) return",
        'button.setAttribute("aria-label", `Follow path to ${destinationTitle}: ${cue}`)',
        "followRouteToNode(destination.node_id)",
        "followRouteToNode(destinationId)",
        "expandedBranchIds = new Set(navigation.expandedIds)",
        "renderPrimaryMap({preserveCamera: true})",
        "moveCameraToNode(nodeId, {record: false, focus: true})",
        "function appendSynthesisSources(item, node)",
        'element("strong", "Sources — ")',
    ):
        assert required in javascript
    assert 'document.createTextNode("Evidence from ")' not in javascript
    assert " shows that ${proseClause(node.title)}" not in javascript
    assert ".map-route-control" in css
    assert "min-width: 36px" in css and "min-height: 36px" in css
    assert ".map-route-cue-label" in css
    assert ".draft-sources" in css
    assert "pointer-events: auto" in css
    assert 'satellite.setAttribute("role", "treeitem")' not in javascript
    assert 'const evidenceStage = element("div", null, "map-evidence-stage")' in javascript
    assert 'evidenceStage.setAttribute("role", "presentation")' in javascript
    assert 'mapCanvas.replaceChildren(stage, evidenceStage, routeStage, ...rootGuides)' in javascript
    assert 'function collapseTreeNode(nodeId)' in javascript
    assert 'id="map-canvas" class="map-canvas" role="region"' in html
    assert "if (depth <= 0) return .82" in javascript
    assert ".map-root-path-guide" in css and ".map-root-path-item" in css
    assert ".map-canvas.map-focus-root .map-route-control.map-route-root" in css
    split_body = javascript.split("function setSplitPosition(ratio, {announce = false} = {}) {", 1)[1].split("\n}", 1)[0]
    assert split_body.index('splitWorkspace.style.setProperty("--split-position"') < split_body.index("scheduleRootPathGuideLayout()")


def test_ui_layout_module_guarantees_dense_collision_free_deterministic_geometry() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    layout = package / "layout.js"
    assert layout.is_file()
    script = r"""
const layout = require(process.argv[1]);
const nodes = [];
function add(id, parentId, depth, order, size) { nodes.push({id, parentId, depth, order, size}); }
add("root-a", null, 0, 0, 330);
for (let index = 0; index < 10; index += 1) {
  const topic = `topic-${index}`;
  add(topic, "root-a", 1, index, 147 + (index % 4) * 16);
  let parent = topic;
  for (let depth = 2; depth <= 5; depth += 1) {
    const child = `${topic}-depth-${depth}`;
    add(child, parent, depth, 0, [0, 0, 64, 44, 32, 32][depth]);
    parent = child;
  }
}
add("root-b", null, 0, 1, 440);
for (let index = 0; index < 8; index += 1) add(`other-${index}`, "root-b", 1, index, 172);

const first = layout.layoutForest(nodes, {clearance: 18, maximumScale: 1.2});
const second = layout.layoutForest(nodes, {clearance: 18, maximumScale: 1.2});
if (JSON.stringify(first) !== JSON.stringify(second)) throw new Error("layout is not deterministic");
const audit = layout.validateCircles(first.circles, {clearance: 18});
if (!audit.valid) throw new Error(JSON.stringify(audit.collisions));

const collapsedIds = new Set(nodes.filter((node) => node.depth <= 2).map((node) => node.id));
const collapsed = first.circles.filter((circle) => collapsedIds.has(circle.id));
const collapsedAudit = layout.validateCircles(collapsed, {clearance: 18});
if (!collapsedAudit.valid) throw new Error("collapsed geometry intersects");
for (const circle of collapsed) {
  const expandedCircle = second.circles.find((item) => item.id === circle.id);
  if (!expandedCircle || circle.x !== expandedCircle.x || circle.y !== expandedCircle.y) {
    throw new Error(`existing position moved: ${circle.id}`);
  }
}

const occupied = first.circles.slice();
const parent = occupied.find((item) => item.id === "topic-0-depth-5");
const satellites = layout.placeSatellites({
  parent,
  count: 12,
  radius: 30 * 1.06,
  occupied,
  clearance: 18,
  preferredAngle: 0,
});
const evidenceAudit = layout.validateCircles([...occupied, ...satellites], {clearance: 18});
if (!evidenceAudit.valid) throw new Error("evidence ring intersects");

const bounds = layout.boundsForCircles([...occupied, ...satellites], 18);
if (!(bounds.minX < bounds.maxX && bounds.minY < bounds.maxY)) throw new Error("invalid bounds");

const large = [{id: "large-root", parentId: null, order: 0, size: 330}];
for (let index = 0; index < 125; index += 1) {
  large.push({id: `large-${index}`, parentId: "large-root", order: index, size: 172});
}
const largeLayout = layout.layoutForest(large, {clearance: 18, maximumScale: 1.2});
const largeWidth = largeLayout.bounds.maxX - largeLayout.bounds.minX;
if (largeWidth > 10000) throw new Error(`fallback too wide: ${largeWidth}`);
const fit = layout.fitScaleForBounds(largeLayout.bounds, 648, 700);
if (fit >= .25) throw new Error("dense fit was incorrectly clamped");
if (largeWidth * fit > 648 - 72 + 1e-6) throw new Error("dense layout does not fit viewport");
"""
    completed = subprocess.run(
        ["node", "-e", script, str(layout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_ui_layout_module_places_routes_without_node_or_route_intersections() -> None:
    package = Path(__file__).parents[1] / "src/soleresearch/ui"
    layout = package / "layout.js"
    script = r"""
const geometry = require(process.argv[1]);
const nodes = [
  {id: "root", x: 0, y: 0, radius: 330 * 1.2 / 2, depth: 0},
  {id: "topic", x: 360, y: 0, radius: 172 * 1.2 / 2, depth: 1},
  {id: "depth2", x: 590, y: 0, radius: 64 * 1.2 / 2, depth: 2},
  {id: "depth3", x: 750, y: 0, radius: 44 * 1.2 / 2, depth: 3},
  {id: "interference", x: 475, y: 110, radius: 42, depth: 2},
];
const edges = [
  {id: "root-topic", sourceId: "root", destinationId: "topic", cue: "How it works", persistent: true},
  {id: "topic-depth2", sourceId: "topic", destinationId: "depth2", cue: "What evidence shows"},
  {id: "depth2-depth3", sourceId: "depth2", destinationId: "depth3", cue: "Tradeoffs and limits"},
];
const first = geometry.placeRoutes({edges, nodes, targetRadius: 18, clearance: 10});
const second = geometry.placeRoutes({edges, nodes, targetRadius: 18, clearance: 10});
if (JSON.stringify(first) !== JSON.stringify(second)) throw new Error("route placement is not deterministic");
if (first.length !== edges.length) throw new Error("route missing");
const footprints = geometry.routeFootprintCircles(first);
if (footprints.length !== first.filter((route) => route.persistent).length) throw new Error("hover cues reserved graph footprints");
for (const footprint of footprints) {
  const audit = geometry.validateCircles([...nodes, footprint], {clearance: 10});
  if (!audit.valid) throw new Error(JSON.stringify(audit.collisions));
}
const targetAudit = geometry.validateCircles([...nodes, ...geometry.routeTargetCircles(first)], {clearance: 10});
if (!targetAudit.valid) throw new Error(JSON.stringify(targetAudit.collisions));
const persistentFootprints = geometry.routeFootprintCircles(first.filter((route) => route.persistent));
const fullSceneAudit = geometry.validateCircles([
  ...geometry.routeTargetCircles(first),
  ...persistentFootprints,
], {clearance: 10});
if (!fullSceneAudit.valid) throw new Error(`full route scene: ${JSON.stringify(fullSceneAudit.collisions)}`);
for (const route of first.filter((item) => !item.persistent)) {
  if (geometry.routeFootprintCircles([route]).length) throw new Error(`hover route reserved a cue: ${route.id}`);
}
if (first.some((route) => route.targetRadius < 18)) throw new Error("target below 36 CSS pixels");
for (const route of first) {
  if (route.projection < 0 || route.projection > 1 || Math.abs(route.perpendicular) > 48) {
    throw new Error(`route control left its edge: ${route.id}`);
  }
  if (route.persistent && (route.cueProjection < 0 || route.cueProjection > 1 || Math.abs(route.cuePerpendicular) > 216)) {
    throw new Error(`route cue left its edge: ${route.id}`);
  }
}
if (!geometry.boundsAreDisjoint([
  {minX: 0, maxX: 100, minY: 0, maxY: 100},
  {minX: 140, maxX: 240, minY: 0, maxY: 100},
], 24)) throw new Error("disjoint project bounds rejected");

const phrases = [
  ["1. Image Filtering", "Smooth, denoise, and sharpen"],
  ["2. Morphological Operations", "Clean and reshape regions"],
  ["3. Geometric Transformations", "Align, warp, and measure"],
  ["4. Edge & Region Detection", "Find boundaries and segments"],
  ["5. Feature Detection & Matching", "Recognize and match landmarks"],
  ["6. Frequency Domain Methods", "Analyze patterns by frequency"],
  ["7. Shape & Geometry Analysis", "Measure contours and geometry"],
  ["8. Color & Intensity Processing", "Adjust contrast and color"],
  ["Methodology", "How it works"],
  ["History & Established Uses", "How the field evolved"],
  ["Practical Applications", "When to use it"],
  ["Limitations", "Tradeoffs and limits"],
];
for (const [title, expected] of phrases) {
  const actual = geometry.routeCue({title, body: ""});
  if (actual !== expected) throw new Error(`${title}: ${actual}`);
  const wordCount = actual.split(/\s+/).filter(Boolean).length;
  if (wordCount < 3 || wordCount > 7) throw new Error(`${title}: cue has ${wordCount} words`);
}

function assertGuide(viewport, expectedColumns, label) {
  const guide = geometry.rootGuideLayout({...viewport, count: 8});
  if (guide.columns !== expectedColumns || guide.rects.length !== 8) throw new Error(`${label}: wrong guide shape`);
  for (const rect of guide.rects) {
    if (rect.width < 1 || rect.height < 36) throw new Error(`${label}: unreadable guide target`);
    if (rect.x < 0 || rect.y < 0 || rect.x + rect.width > viewport.width || rect.y + rect.height > viewport.height) {
      throw new Error(`${label}: guide escaped viewport`);
    }
  }
  for (let leftIndex = 0; leftIndex < guide.rects.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < guide.rects.length; rightIndex += 1) {
      const left = guide.rects[leftIndex];
      const right = guide.rects[rightIndex];
      const separated = left.x + left.width <= right.x || right.x + right.width <= left.x
        || left.y + left.height <= right.y || right.y + right.height <= left.y;
      if (!separated) throw new Error(`${label}: guide rectangles overlap ${leftIndex}/${rightIndex}`);
    }
  }
}
assertGuide({width: 648, height: 700}, 2, "desktop");
assertGuide({width: 390, height: 700}, 1, "mobile");
assertGuide({width: 390, height: 420}, 1, "short mobile with room for one column");
assertGuide({width: 324, height: 350}, 1, "desktop at 200 percent");
assertGuide({width: 324, height: 210}, 2, "desktop at 200 percent with app chrome");

const navigation = geometry.routeNavigationState({
  destinationId: "depth2",
  hasChildren: true,
  expandedIds: [],
});
if (navigation.selectedNodeId !== "depth2" || !navigation.expandedIds.includes("depth2")) {
  throw new Error("route destination was not expanded");
}
if (!navigation.pushHistory || navigation.urlTopic !== "depth2" || navigation.draftNodeId !== "depth2" || navigation.inspectNodeId !== "depth2") {
  throw new Error("route navigation surfaces are not synchronized");
}
for (const event of [
  {type: "click"},
  {type: "keydown", key: "Enter"},
  {type: "keydown", key: " "},
]) {
  if (!geometry.isRouteActivation(event)) throw new Error(`activation rejected: ${JSON.stringify(event)}`);
}

const spokeRecords = [{id: "spoke-root", parentId: null, order: 0, size: 330}];
const spokeEdges = [];
for (let index = 0; index < 6; index += 1) {
  const topicId = `spoke-topic-${index}`;
  const claimId = `spoke-claim-${index}`;
  spokeRecords.push({id: topicId, parentId: "spoke-root", order: index, size: 172});
  spokeRecords.push({id: claimId, parentId: topicId, order: 0, size: 64});
  spokeEdges.push({id: `spoke-root-${index}`, sourceId: "spoke-root", destinationId: topicId, cue: `Path ${index} summary`, persistent: true});
  spokeEdges.push({id: `spoke-claim-${index}`, sourceId: topicId, destinationId: claimId, cue: `Evidence path ${index}`});
}
const spokeLayout = geometry.layoutForest(spokeRecords, {clearance: 18, maximumScale: 1.2});
const spokeRoutes = geometry.placeRoutes({edges: spokeEdges, nodes: spokeLayout.circles, targetRadius: 18, clearance: 10});
const spokePersistent = geometry.routeFootprintCircles(spokeRoutes.filter((route) => route.persistent));
const spokeSceneAudit = geometry.validateCircles([
  ...geometry.routeTargetCircles(spokeRoutes),
  ...spokePersistent,
], {clearance: 10});
if (!spokeSceneAudit.valid) throw new Error(`six-spoke scene: ${JSON.stringify(spokeSceneAudit.collisions)}`);
for (const route of spokeRoutes.filter((item) => !item.persistent)) {
  if (geometry.routeFootprintCircles([route]).length) throw new Error(`six-spoke hover route reserved a cue: ${route.id}`);
}
if (spokeRoutes.some((route) => Math.abs(route.perpendicular) > 48 || (route.persistent && Math.abs(route.cuePerpendicular) > 216))) {
  throw new Error("six-spoke path intent exceeded");
}

for (let topicCount = 1; topicCount <= 12; topicCount += 1) {
  const records = [{id: "integration-root", parentId: null, order: 0, size: 330}];
  const integrationEdges = [];
  for (let index = 0; index < topicCount; index += 1) {
    const topicId = `integration-topic-${index}`;
    const claimId = `integration-claim-${index}`;
    records.push({id: topicId, parentId: "integration-root", order: index, size: 172});
    records.push({id: claimId, parentId: topicId, order: 0, size: 64});
    integrationEdges.push({id: `integration-root-${index}`, sourceId: "integration-root", destinationId: topicId, cue: `Topic path ${index}`, persistent: true});
    integrationEdges.push({id: `integration-claim-${index}`, sourceId: topicId, destinationId: claimId, cue: `Claim path ${index}`});
  }
  const integrationLayout = geometry.layoutForest(records, {clearance: 18, maximumScale: 1.2});
  let integrationRoutes;
  try {
    integrationRoutes = geometry.placeRoutes({
      edges: integrationEdges,
      nodes: integrationLayout.circles,
      targetRadius: 18,
      clearance: 10,
    });
  } catch (error) {
    throw new Error(`integration placement ${topicCount}: ${error.message}`);
  }
  const integrationPersistent = geometry.routeFootprintCircles(integrationRoutes.filter((route) => route.persistent));
  const integrationScene = geometry.validateCircles([
    ...integrationLayout.circles,
    ...geometry.routeTargetCircles(integrationRoutes),
    ...integrationPersistent,
  ], {clearance: 10});
  if (!integrationScene.valid) throw new Error(`integration ${topicCount}: ${JSON.stringify(integrationScene.collisions)}`);
  for (const route of integrationRoutes.filter((item) => !item.persistent)) {
    if (geometry.routeFootprintCircles([route]).length) throw new Error(`integration hover route reserved a cue: ${route.id}`);
  }
}

const actualRootId = "nod_2c42650e71cb46d7ba173bacf26c165c";
const actualTopicIds = [
  "nod_59a9374f63356f18b50e09ffb91c50b4",
  "nod_acb5788c533bc6e60eb0557e77098faf",
  "nod_26c50032d07ff5953d22c37d249abab1",
  "nod_b87592aa04a49552571910f070ca59df",
  "nod_45c160665d51d5f24df86398f25be307",
  "nod_8635b4ef78a7ceecb4394774b8975721",
  "nod_ea7ccb5e63ce4e477ee1cb634f6122b0",
  "nod_815b9c801c75f6300f08c8e59d22eb24",
];
const sectionTitles = [
  "Simplified Description & Methodology",
  "Purpose & Best Uses",
  "Visual Examples",
  "History & Established Uses",
  "Comparisons & Tradeoffs",
  "Supporting Tools & Preprocessing",
];
const sectionLeafCounts = [
  [3, 1, 1, 1, 2, 1],
  [4, 2, 1, 1, 1, 1],
  [5, 1, 1, 0, 2, 1],
  [6, 1, 1, 1, 2, 1],
  [9, 1, 1, 1, 2, 1],
  [6, 2, 1, 1, 1, 1],
  [0, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 0],
];
const actualNodes = [{id: actualRootId, parentId: null, order: 0, size: 330}];
for (let topicIndex = 0; topicIndex < actualTopicIds.length; topicIndex += 1) {
  const topicId = actualTopicIds[topicIndex];
  actualNodes.push({id: topicId, parentId: actualRootId, order: topicIndex, size: 172});
  for (let sectionIndex = 0; sectionIndex < 6; sectionIndex += 1) {
    const sectionId = topicIndex === 5 && sectionIndex === 2
      ? "nod_9fa84bbf43454dbaa36051ff9f89b5e7"
      : `actual-section-${topicIndex}-${sectionIndex}`;
    actualNodes.push({id: sectionId, parentId: topicId, order: sectionIndex, size: 64, title: sectionTitles[sectionIndex]});
    for (let leafIndex = 0; leafIndex < sectionLeafCounts[topicIndex][sectionIndex]; leafIndex += 1) {
      actualNodes.push({id: `actual-leaf-${topicIndex}-${sectionIndex}-${leafIndex}`, parentId: sectionId, order: leafIndex, size: 44});
    }
  }
}
if (actualNodes.length !== 125) throw new Error(`actual topology count changed: ${actualNodes.length}`);
const actualLayout = geometry.layoutForest(actualNodes, {clearance: 18, maximumScale: 1.2});
if (actualLayout.mode !== "spaced") throw new Error(`actual topology did not exercise fallback: ${actualLayout.mode}`);
const actualVisible = actualLayout.circles.filter((circle) => circle.depth <= 2);
if (actualVisible.length !== 57) throw new Error(`actual visible count changed: ${actualVisible.length}`);
const actualEdges = actualNodes.filter((node) => node.parentId && actualVisible.some((circle) => circle.id === node.id)).map((node) => ({
  id: `${node.parentId}:${node.id}`,
  sourceId: node.parentId,
  destinationId: node.id,
  cue: geometry.routeCue({title: node.title || `Topic ${node.order + 1}`, body: ""}),
  persistent: actualLayout.circles.find((circle) => circle.id === node.parentId).depth === 0,
}));
if (actualEdges.length !== 56) throw new Error(`actual route count changed: ${actualEdges.length}`);
const actualRoutes = geometry.placeRoutes({edges: actualEdges, nodes: actualVisible, targetRadius: 18, clearance: 10});
const formerlyFailing = actualRoutes.find((route) => route.id === "nod_8635b4ef78a7ceecb4394774b8975721:nod_9fa84bbf43454dbaa36051ff9f89b5e7");
if (!formerlyFailing || Number.isFinite(formerlyFailing.cueX)) throw new Error("actual Visual Examples hover route reserved a cue footprint");
const actualBounds = geometry.boundsForCircles([
  ...actualVisible,
  ...geometry.routeTargetCircles(actualRoutes),
  ...geometry.routeFootprintCircles(actualRoutes),
], 18);
if (actualBounds.maxX - actualBounds.minX > 10000) throw new Error("actual visible route scene is not compact");

for (const topicCount of [1, 8, 48, 69]) {
  const records = [{id: "scale-root", parentId: null, order: 0, size: 330}];
  const scaleEdges = [];
  for (let index = 0; index < topicCount; index += 1) {
    const topicId = `scale-topic-${index}`;
    const claimId = `scale-claim-${index}`;
    records.push({id: topicId, parentId: "scale-root", order: index, size: 172});
    records.push({id: claimId, parentId: topicId, order: 0, size: 64});
    scaleEdges.push({id: `scale-root-${index}`, sourceId: "scale-root", destinationId: topicId, cue: `Path ${index}`, persistent: true});
    scaleEdges.push({id: `scale-claim-${index}`, sourceId: topicId, destinationId: claimId, cue: `Claim ${index}`});
  }
  const compactLayout = geometry.layoutForest(records, {clearance: 18, maximumScale: 1.2});
  const compactRoutes = geometry.placeRoutes({edges: scaleEdges, nodes: compactLayout.circles, targetRadius: 18, clearance: 10});
  const scene = [
    ...compactLayout.circles,
    ...geometry.routeTargetCircles(compactRoutes),
    ...geometry.routeFootprintCircles(compactRoutes),
  ];
  const sceneBounds = geometry.boundsForCircles(scene, 18);
  const sceneWidth = sceneBounds.maxX - sceneBounds.minX;
  if (sceneWidth > 10000) throw new Error(`compact ${topicCount} too wide: ${sceneWidth}`);
  const overviewScale = geometry.fitScaleForBounds(sceneBounds, 648, 700);
  if (sceneWidth * overviewScale > 648 - 72 + 1e-6) throw new Error(`compact ${topicCount} does not fit overview`);

  const root = compactLayout.circles.find((circle) => circle.id === "scale-root");
  const firstTierIds = new Set(records.filter((record) => record.parentId === "scale-root").map((record) => record.id));
  const firstTierRoutes = compactRoutes.filter((route) => route.sourceId === "scale-root");
  const neighborhood = [
    root,
    ...compactLayout.circles.filter((circle) => firstTierIds.has(circle.id)),
    ...geometry.routeTargetCircles(firstTierRoutes),
    ...geometry.routeFootprintCircles(firstTierRoutes),
  ];
  const neighborhoodBounds = geometry.boundsForCircles(neighborhood, 18);
  const rootScale = geometry.fitScaleAroundPoint(neighborhoodBounds, root, 648, 700, {padding: 72, maximum: .9});
  const horizontalExtent = Math.max(root.x - neighborhoodBounds.minX, neighborhoodBounds.maxX - root.x);
  const verticalExtent = Math.max(root.y - neighborhoodBounds.minY, neighborhoodBounds.maxY - root.y);
  if (horizontalExtent * rootScale > (648 - 72) / 2 + 1e-6 || verticalExtent * rootScale > (700 - 72) / 2 + 1e-6) {
    throw new Error(`root neighborhood ${topicCount} does not fit focused viewport`);
  }
  if (topicCount <= 8 && rootScale < .5) throw new Error(`root neighborhood ${topicCount} is not readable: ${rootScale}`);
}
"""
    completed = subprocess.run(
        ["node", "-e", script, str(layout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_ui_state_exposes_run_history_budget_and_unified_graph_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, human, _agent = _project(tmp_path, monkeypatch)
    run = RunRepository.create(root, controller="human")
    task = run.dispatch(
        role="reader",
        subquestion="Which tread pattern is supported?",
        evidence_strategy="Inspect exact source passages",
        selected_context={"node_ids": []},
        allowed_capabilities=["read_source", "web_search"],
        controller_token=human,
    )
    state = ui_state(root)
    observed = state["views"]["runs"]["items"][0]
    assert observed["run_id"] == run.run_id
    assert observed["events"][0]["event_type"] == "run_created"
    assert set(observed["remaining"]) == {"cycles", "tasks", "deep_sources", "agents", "max_depth", "minutes", "provider_usage"}
    assert observed["tasks"] == [{
        "task_id": task["task_id"],
        "worker_id": task["worker_id"],
        "role": "reader",
        "depth": 0,
        "cycle": 1,
        "subquestion": "Which tread pattern is supported?",
        "evidence_strategy": "Inspect exact source passages",
        "allowed_capabilities": ["read_source", "web_search"],
        "status": "pending",
        "created_at": task["created_at"],
        "result": None,
    }]
    assert observed["telemetry"] == {
        "event_history_recorded": True,
        "declared_capabilities_recorded": True,
        "used_capabilities_recorded": True,
        "individual_tool_calls_recorded": False,
    }
    types = {item["type"] for item in state["views"]["audit"]["timeline"]["items"]}
    assert "run:run_created" in types
    assert any(value.startswith("graph_diff:") for value in types)


def test_map_evidence_reference_resolves_source_title_for_safe_cross_view_focus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, human, _agent = _project(tmp_path, monkeypatch)
    note = tmp_path / "source.md"
    note.write_text("# Tread Wear Study\n\nExact inspected wear passage.\n", encoding="utf-8")
    source, _created = import_local_document(root, note, kind="markdown")
    excerpt = "Exact inspected wear passage."
    evidence, _created = EvidenceRepository(root).add(
        source_id=source["source_id"],
        locator=locate_excerpt(load_extraction(root, source["source_id"]), excerpt),
        excerpt=excerpt,
        paraphrase="The inspected source contains the wear passage.",
        stance="context",
        actor_type="human",
        actor_id="fixture",
        method="manual",
    )
    graph = GraphRepository(root)
    node = new_node("interpretation", "Evidence-linked interpretation", evidence_ids=[evidence["evidence_id"]], authority="human_accepted")
    diff = graph.propose([{"op": "add", "target": "node", "record": node}], actor_type="human", actor_id="fixture")
    graph.apply(diff["diff_id"], controller_token=human)
    state = ui_state(root)
    rendered_evidence = next(item for item in state["views"]["evidence"]["items"] if item["evidence_id"] == evidence["evidence_id"])
    rendered_node = next(item for item in state["views"]["nodes"]["items"] if item["node_id"] == node["node_id"])
    assert rendered_evidence["source_title"] == "Tread Wear Study"
    assert rendered_evidence["source_url"] == source["canonical_url"]
    assert rendered_node["coverage"]["state"] == "linked_context"
    assert rendered_node["coverage"]["evidence_count"] == 1
    assert rendered_node["evidence_ids"] == [evidence["evidence_id"]]


def test_source_display_title_replaces_file_metadata_with_extracted_document_title(tmp_path: Path) -> None:
    source_id = "src_placeholder"
    extraction_dir = tmp_path / ".soleresearch/extractions" / source_id
    extraction_dir.mkdir(parents=True)
    (extraction_dir / "version.json").write_text(
        json.dumps({"text": "ORB: An Efficient Alternative to SIFT or SURF\nAuthors\nAbstract"}),
        encoding="utf-8",
    )

    assert _source_display_title(tmp_path, {"source_id": source_id, "title": "matching_noise.eps"}) == (
        "ORB: An Efficient Alternative to SIFT or SURF"
    )
    assert _source_display_title(tmp_path, {"source_id": source_id, "title": "Curated source title"}) == (
        "Curated source title"
    )


def test_ui_projects_reviewable_graph_proposals_without_applying_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    graph = GraphRepository(root)
    proposed_node = new_node("gap", "Which outsole patterns remain unresolved?", authority="agent_accepted")
    proposal = graph.propose(
        [{"op": "add", "target": "node", "record": proposed_node}],
        actor_type="agent",
        actor_id="research-agent",
    )

    state = ui_state(root)

    assert state["views"]["proposals"]["total"] == 1
    assert state["views"]["proposals"]["items"][0]["diff_id"] == proposal["diff_id"]
    assert all(item["node_id"] != proposed_node["node_id"] for item in state["views"]["nodes"]["items"])


@pytest.mark.parametrize("relative", [
    "results/v1/tsk_00000000000000000000000000000000.json",
    "workers/wrk_00000000000000000000000000000000.json",
    "extensions.jsonl",
])
def test_ui_rejects_malformed_complete_run_relations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    run = RunRepository.create(root, controller="human")
    path = root / "runs" / run.run_id / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises((ProjectError, SchemaError)):
        ui_state(root)


def test_ui_observes_active_elapsed_time_without_mutating_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    run = RunRepository.create(root, controller="human", now=lambda: "2026-07-11T10:00:00Z")
    budget_path = root / "runs" / run.run_id / "budget.json"
    before = budget_path.read_bytes()
    at_thirty = ui_state(root, now=lambda: "2026-07-11T10:30:00Z")["views"]["runs"]["items"][0]
    at_forty = ui_state(root, now=lambda: "2026-07-11T10:40:00Z")["views"]["runs"]["items"][0]
    assert at_thirty["observed_elapsed_seconds"] == 1800
    assert at_thirty["remaining"]["minutes"] == 60
    assert at_forty["observed_elapsed_seconds"] == 2400
    assert at_forty["remaining"]["minutes"] == 50
    assert at_forty["observed_at"] == "2026-07-11T10:40:00Z"
    assert budget_path.read_bytes() == before


def test_edit_broker_rejects_symlinked_unfinished_state_before_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, project, human, _agent = _project(tmp_path, monkeypatch)
    capability = require_controller(project["project_id"], human)
    run = RunRepository.create(root, controller="human")
    state_path = root / "runs" / run.run_id / "state.json"
    outside = tmp_path / "outside-state.json"
    outside.write_bytes(state_path.read_bytes())
    state_path.unlink()
    state_path.symlink_to(outside)
    before = (root / "outline.md").read_text(encoding="utf-8")
    changed = before.replace("What should we understand?", "Must not write through symlinked broker")
    with pytest.raises(ProjectError, match="symbolic link"):
        save_outline(root, content=changed, base_hash=text_hash(before), capability=capability)
    assert (root / "outline.md").read_text(encoding="utf-8") == before
    assert read_jsonl(root / "events/human-edits.jsonl") == []


def test_ui_rejects_symlinked_wildcard_ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "must-not-read.jsonl").write_text("not-json\n", encoding="utf-8")
    (root / "discussions").rmdir()
    (root / "discussions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectError, match="symbolic link"):
        ui_state(root)
    run_root, _record, _human2, _agent2 = _project(tmp_path / "run-case", monkeypatch)
    run = RunRepository.create(run_root, controller="human")
    outside_file = tmp_path / "outside-task.json"
    outside_file.write_text("{}\n", encoding="utf-8")
    (run_root / "runs" / run.run_id / "tasks/v1").mkdir(parents=True)
    (run_root / "runs" / run.run_id / "tasks/v1/tsk_00000000000000000000000000000000.json").symlink_to(outside_file)
    with pytest.raises(ProjectError, match="symbolic link"):
        ui_state(run_root)


def test_tmux_launcher_create_idempotence_command_collision_and_exact_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _project_record, _human, _agent = _project(tmp_path, monkeypatch)
    package = Path(__file__).parents[1]
    fake_bin = tmp_path / "bin"
    state = tmp_path / "tmux-state"
    fake_bin.mkdir()
    state.mkdir()
    fake_tmux = fake_bin / "tmux"
    fake_tmux.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FAKE_TMUX_STATE'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + '\\n')
command = args[0]
session = next((args[index + 1].lstrip('=') for index, value in enumerate(args[:-1]) if value == '-t'), None)
if command == 'has-session':
    raise SystemExit(0 if (root / ('session-' + session)).exists() else 1)
if command == 'new-session':
    session = args[args.index('-s') + 1]
    (root / ('session-' + session)).touch()
elif command == 'set-environment':
    key, value = args[-2], args[-1]
    (root / (session + '-' + key)).write_text(value, encoding='utf-8')
elif command == 'show-environment':
    key = args[-1]
    path = root / (session + '-' + key)
    if not path.exists(): raise SystemExit(1)
    print(key + '=' + path.read_text(encoding='utf-8'))
elif command == 'kill-session':
    (root / ('session-' + session)).unlink(missing_ok=True)
""",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""), "FAKE_TMUX_STATE": str(state)}
    base = [sys.executable, str(package / "scripts/launch-tmux.py"), str(root), "--session", "sole-live", "--", "worker", "--prompt", "two words"]
    first = subprocess.run(base, capture_output=True, text=True, env=env, check=False)
    second = subprocess.run(base, capture_output=True, text=True, env=env, check=False)
    collision = subprocess.run([*base[:-1], "different"], capture_output=True, text=True, env=env, check=False)
    assert first.returncode == second.returncode == 0
    assert "Existing session" in second.stdout
    assert collision.returncode != 0 and "different orchestration command" in collision.stderr
    calls = [json.loads(line) for line in (state / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    creates = [call for call in calls if call[0] == "new-session"]
    assert creates == [["new-session", "-d", "-s", "sole-live", "-c", str(root), "worker", "--prompt", "two words"]]
