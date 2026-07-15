#!/usr/bin/env python3
"""Exercise the full Soleresearch V1 workflow using synthetic local notes only."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import subprocess
import sys
import threading
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soleresearch.controller import read_controller_capability, remove_controller_capabilities
from soleresearch.indexing import read_index_snapshot
from soleresearch.project import load_project
from soleresearch.ui import create_server, ui_state


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / os.environ.get("SOLERESEARCH_DEMO_OUTPUT", "demo-output")
PROJECT = OUTPUT / "project"
FIXTURES = ROOT / "fixtures"
CONFIG = OUTPUT / "controller-config"
EXPORT_A = OUTPUT / "export-a"
EXPORT_B = OUTPUT / "export-b"
ZOTERO = OUTPUT / "zotero-review"
QUESTION = "Which observable outsole-wear features should a future running-shoe remaining-life model record before any threshold is inferred?"
EXCERPTS = (
    "The lateral heel shows a widening smooth patch while the central forefoot lugs remain sharply defined.",
    "Two shoes with the same tracked mileage show different exposed-foam area because their initial rubber coverage differs.",
    "A localized toe-off abrasion is visible without a matching smooth region at the heel.",
    "The photograph angle hides part of the medial forefoot, so apparent unworn area cannot be treated as observed tread.",
)


def run(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["sole-research", *args],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(args)}\n{completed.stderr}")
    return json.loads(completed.stdout)


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def research_view(state: dict[str, Any]) -> dict[str, Any]:
    """Exclude only the explicit derived-index presence indicator."""
    value = json.loads(json.dumps(state))
    value["staleness"].pop("index_present", None)
    return value


def http_request(port: int, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        connection.close()


def edit_and_inspect_through_http(project: Path, human_token: str, question_node_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, bool]]:
    server = create_server(project, host="127.0.0.1", port=0, edit=True, controller_token=human_token)
    thread = threading.Thread(target=server.serve_forever, name="soleresearch-demo-ui", daemon=True)
    log_capture = io.StringIO()
    shutdown_clean = False
    try:
        with redirect_stdout(log_capture):
            thread.start()
            bootstrap_status, bootstrap_headers, bootstrap_body = http_request(server.server_port, "GET", "/")
            outline_status, outline_headers, outline_body = http_request(server.server_port, "GET", "/api/v1/outline")
            outline = json.loads(outline_body)
            digest = outline["outline_hash"]
            anchor = f"<!-- soleresearch:node {question_node_id} -->"
            edited = outline["content"].replace(
                anchor,
                anchor + "\n\nHuman note: keep observable wear signals separate from any future remaining-life decision rule.",
                1,
            )
            payload = json.dumps({"schema_version": 1, "content": edited, "base_hash": digest}).encode("utf-8")
            put_status, put_headers, put_body = http_request(
                server.server_port,
                "PUT",
                "/api/v1/outline",
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-CSRF-Token": server.config.csrf_token,
                    "If-Match": f'"{digest}"',
                },
            )
            put_result = json.loads(put_body)
            state_status, _state_headers, state_body = http_request(server.server_port, "GET", "/api/v1/state")
            state = json.loads(state_body)
            server.shutdown()
            thread.join(timeout=5)
            shutdown_clean = not thread.is_alive()
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        server.server_close()
    expected_views = {"audit", "conflicts", "discussions", "edges", "evidence", "gaps", "nodes", "runs", "sources"}
    checks = {
        "ui_http_bootstrap_ok": (
            bootstrap_status == 200
            and bootstrap_headers.get("content-type", "").startswith("text/html")
            and server.config.csrf_token.encode("ascii") in bootstrap_body
        ),
        "ui_http_outline_get_etag_ok": outline_status == 200 and outline_headers.get("etag") == f'"{digest}"',
        "ui_http_outline_put_csrf_etag_ok": (
            put_status == 200
            and put_result.get("saved") is True
            and put_headers.get("etag") == f'"{put_result.get("outline_hash")}"'
        ),
        "ui_http_state_views_ok": state_status == 200 and expected_views <= set(state.get("views", {})),
        "ui_http_server_shutdown_clean": shutdown_clean,
    }
    if not all(checks.values()):
        raise SystemExit(f"served UI HTTP assertion failed: {json.dumps(checks, sort_keys=True)}")
    return put_result, state, checks


def inspect_post_run_through_http(project: Path) -> tuple[dict[str, Any], dict[str, bool]]:
    server = create_server(project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="soleresearch-demo-ui-post-run", daemon=True)
    log_capture = io.StringIO()
    shutdown_clean = False
    try:
        with redirect_stdout(log_capture):
            thread.start()
            status, _headers, body = http_request(server.server_port, "GET", "/api/v1/state")
            state = json.loads(body)
            server.shutdown()
            thread.join(timeout=5)
            shutdown_clean = not thread.is_alive()
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        server.server_close()
    runs = state.get("views", {}).get("runs", {})
    run_items = runs.get("items", []) if isinstance(runs, dict) else []
    audit = state.get("views", {}).get("audit", {})
    checks = {
        "ui_http_post_run_state_ok": (
            status == 200
            and runs.get("total") == 1
            and len(run_items) == 1
            and run_items[0].get("status") == "finished"
            and isinstance(run_items[0].get("gate"), dict)
            and isinstance(run_items[0].get("budget"), dict)
            and len(run_items[0].get("events", [])) > 0
            and audit.get("timeline", {}).get("total", 0) > 0
        ),
        "ui_http_post_run_server_shutdown_clean": shutdown_clean,
    }
    if not all(checks.values()):
        raise SystemExit(f"post-run served UI HTTP assertion failed: {json.dumps(checks, sort_keys=True)}")
    return state, checks


def main() -> int:
    # Parent-side UI/controller APIs and CLI subprocesses must resolve the same
    # external capability broker. Never inherit a conflicting caller setting.
    os.environ["SOLERESEARCH_CONFIG_HOME"] = str(CONFIG)
    if PROJECT.exists() or EXPORT_A.exists() or EXPORT_B.exists() or ZOTERO.exists() or CONFIG.exists():
        raise SystemExit("demo output already exists; refusing to overwrite project, exports, Zotero bundle, or capabilities")

    initialized = run("init", str(PROJECT), "--name", "Offline outsole-wear signal map")
    human_cap = initialized["controller_capability_paths"]["human"]
    question = run(
        "scaffold", str(PROJECT), "--question", QUESTION,
        "--actor-id", "demo-human", "--controller-token-file", human_cap,
    )

    sources: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for index, excerpt in enumerate(EXCERPTS, start=1):
        imported = run("import", str(PROJECT), "markdown", str(FIXTURES / f"source-{index}.md"))
        source_id = imported["source_ids"][0]
        source = next(item for item in run("source", str(PROJECT), "list")["sources"] if item["source_id"] == source_id)
        sources.append(source)
        run(
            "source", str(PROJECT), "quality", source["source_id"],
            "--authority", "not_applicable", "--methodology-transparency", "high",
            "--evidence-directness", "high", "--relevance", "high",
            "--publication-status", "informal",
            "--notes", "Synthetic offline workflow fixture; not scientific evidence.",
        )
        item = run(
            "evidence", str(PROJECT), "add", "--source-id", source["source_id"],
            "--locator", "section", "--section", "Observed pattern", "--excerpt", excerpt,
            "--paraphrase", f"Synthetic fixture {index} identifies one candidate observation or limitation to record.",
            "--stance", "context", "--actor-type", "human", "--actor-id", "demo-human",
            "--method", "offline synthetic fixture inspection",
        )
        evidence_id = item["evidence_id"]
        evidence.append(next(record for record in run("evidence", str(PROJECT), "list")["evidence"] if record["evidence_id"] == evidence_id))

    discussion = run(
        "discuss", str(PROJECT), "add", "--entity-type", "source",
        "--entity-id", sources[3]["source_id"],
        "--content", "Treat hidden outsole regions as missing observations, not as unworn tread.",
        "--actor-type", "human", "--actor-id", "demo-human",
    )
    run(
        "discuss", str(PROJECT), "promote", "--discussion-id", discussion["discussion_id"],
        "--entity-type", "source", "--entity-id", sources[3]["source_id"],
        "--content", "Candidate takeaway: record visibility and occlusion beside spatial wear features.",
        "--actor-type", "orchestrator", "--actor-id", "demo-orchestrator",
    )

    initial_operations = [
        {
            "op": "add", "target": "node",
            "record": {
                "node_type": "interpretation",
                "title": "Candidate observation groups",
                "body": "Record spatial wear location, material exposure, remaining tread definition, and image visibility separately. Synthetic demonstration only; scientific validation remains a gap.",
                "tags": ["synthetic-demo", "wear-observation"],
                "maturity": "exploratory", "parent_id": question["node_id"], "position": 0,
                "evidence_ids": [item["evidence_id"] for item in evidence],
            },
        }
    ]
    initial_operations_path = OUTPUT / "initial-operations.json"
    initial_operations_path.write_text(json.dumps(initial_operations, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    initial_diff = run(
        "diff", str(PROJECT), "propose", "--operations", str(initial_operations_path),
        "--actor-type", "agent", "--actor-id", "demo-synthesizer",
    )
    run(
        "diff", str(PROJECT), "apply", "--diff-id", initial_diff["diff_id"],
        "--controller-token-file", human_cap,
    )

    project_record = load_project(PROJECT)
    token = read_controller_capability(Path(human_cap))
    ui_edit, ui_http_state, ui_http_checks = edit_and_inspect_through_http(PROJECT, token, question["node_id"])
    reconciled = run("reconcile", str(PROJECT), "--controller-token-file", human_cap)

    started = run(
        "run", str(PROJECT), "start", "--controller", "human", "--cycles", "1",
        "--tasks", "2", "--deep-sources", "5", "--agents", "2", "--max-depth", "1",
        "--minutes", "15", "--provider-unit", "dollars", "--provider-ceiling", "1",
    )
    run_id = started["run_id"]
    context_path = OUTPUT / "selected-context.json"
    context_path.write_text(json.dumps({"node_ids": [question["node_id"]]}, indent=2) + "\n", encoding="utf-8")
    dispatch_args = [
        "run", str(PROJECT), "dispatch", "--run-id", run_id,
        "--controller-token-file", human_cap, "--role", "reader",
        "--subquestion", "Which spatial observations and image limitations recur across the curated fixture set?",
        "--evidence-strategy", "Use only the four existing exact-locator evidence records.",
        "--context", str(context_path), "--capability", "read_source",
        "--reserve-deep-sources", "4", "--reserve-provider-usage", "0",
    ]
    for source in sources:
        dispatch_args.extend(("--artifact-ref", f"source:{source['source_id']}"))
    task = run(*dispatch_args)

    result = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task["task_id"],
        "worker_id": task["worker_id"],
        "base_revision": task["base_revision"],
        "used_capabilities": ["read_source"],
        "used_artifact_references": [f"source:{item['source_id']}" for item in sources],
        "accessed_sources": [
            {
                "source_id": item["source_id"], "source_hash": item["content_hash"],
                "source_version": item["source_version"], "deeply_processed": True,
                "access_method": "local_synthetic_inspection", "access_url": None,
            }
            for item in sources
        ],
        "evidence": [],
        "proposed_graph_operations": [
            {
                "op": "add", "target": "node",
                "record": {
                    "node_type": "gap",
                    "title": "Scientific validation and retirement thresholds remain unresolved",
                    "body": "The synthetic fixtures cannot establish predictive validity, causal meaning, or a remaining-life threshold.",
                    "tags": ["synthetic-demo", "validation-gap"],
                    "maturity": "gap", "parent_id": question["node_id"], "position": 1,
                },
            },
        ],
        "outline_suggestions": [],
        "disagreements": [],
        "gaps": [{"summary": "No real study or measured mileage outcome was used in the offline demo."}],
        "rationale": "Four synthetic local notes support a workflow-only grouping and explicitly preserve the scientific gap.",
        "usage": {"unit": "dollars", "amount": 0},
        "completed_operations": sorted(set(task["required_result_operations"]) | {"source_provenance", "gaps"}),
        "errors": [],
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    result_path = OUTPUT / "worker-result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    imported_result = run(
        "run", str(PROJECT), "import-result", "--run-id", run_id,
        "--controller-token-file", human_cap, "--result", str(result_path),
    )
    run(
        "gate", str(PROJECT), run_id, "resolve", "--result-id", imported_result["result_id"],
        "--controller-token-file", human_cap,
    )
    finished = run(
        "run", str(PROJECT), "finish", "--run-id", run_id,
        "--controller-token-file", human_cap,
        "--reason", "Stopped early: offline mechanics demonstrated; synthetic fixtures cannot answer the scientific question.",
    )
    post_run_http_state, post_run_http_checks = inspect_post_run_through_http(PROJECT)
    ui_http_checks.update(post_run_http_checks)

    fixed_now = lambda: "2026-07-11T12:05:00Z"
    initial_rebuild = run("rebuild-index", str(PROJECT))
    state_before = ui_state(PROJECT, now=fixed_now)
    index_snapshot_before = read_index_snapshot(PROJECT)
    first_export = run("export", str(PROJECT), str(EXPORT_A))
    second_export = run("export", str(PROJECT), str(EXPORT_B))
    export_hashes_equal = tree_hash(EXPORT_A) == tree_hash(EXPORT_B)
    index_path = PROJECT / ".soleresearch/index.sqlite3"
    index_path.unlink(missing_ok=True)
    state_without_index = ui_state(PROJECT, now=fixed_now)
    rebuilt = run("rebuild-index", str(PROJECT))
    state_after_rebuild = ui_state(PROJECT, now=fixed_now)
    index_snapshot_after = read_index_snapshot(PROJECT)
    zotero = run("zotero-bundle", str(PROJECT), str(ZOTERO))
    remove_controller_capabilities(project_record["project_id"])
    for directory in (CONFIG / "projects", CONFIG):
        try:
            directory.rmdir()
        except OSError:
            pass

    audit = {
        "schema_version": 1,
        "demo_type": "offline_synthetic",
        "live_public_web_executed": False,
        "retailer_access_executed": False,
        "provider_usage": {"unit": "dollars", "amount": 0},
        "project_id": project_record["project_id"],
        "run_id": run_id,
        "source_count": len(sources),
        "evidence_count": len(evidence),
        "question_node_id": question["node_id"],
        "discussion_id": discussion["discussion_id"],
        "initial_reviewed_diff_id": initial_diff["diff_id"],
        "reviewed_result_id": imported_result["result_id"],
        "continuation_reviewed_diff_id": imported_result["graph_diff"]["diff_id"],
        "human_edit_event_id": ui_edit["event_id"],
        "ui_http_state_view_names": sorted(post_run_http_state["views"]),
        **ui_http_checks,
        "reconciliation_changed": reconciled["changed"],
        "finish_status": finished["status"],
        "exports": [first_export, second_export],
        "deterministic_export_tree_hash_equal": export_hashes_equal,
        "research_view_equal_with_index_deleted": research_view(state_before) == research_view(state_without_index),
        "research_view_equal_after_index_rebuild": research_view(state_before) == research_view(state_after_rebuild),
        "index_snapshot_equal_after_rebuild": index_snapshot_before == index_snapshot_after,
        "index_presence_indicator_toggled_as_expected": (
            state_before["staleness"]["index_present"] is True
            and state_without_index["staleness"]["index_present"] is False
            and state_after_rebuild["staleness"]["index_present"] is True
        ),
        "initial_rebuild": initial_rebuild,
        "rebuilt_index": rebuilt,
        "zotero_review_status": zotero["review_status"],
        "controller_capabilities_inside_project": False,
        "controller_capabilities_scrubbed_after_demo": not CONFIG.exists(),
    }
    (OUTPUT / "demo-audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not all((
        export_hashes_equal,
        research_view(state_before) == research_view(state_without_index),
        research_view(state_before) == research_view(state_after_rebuild),
        index_snapshot_before == index_snapshot_after,
        all(ui_http_checks.values()),
    )):
        raise SystemExit("deterministic export/index-observable-state assertion failed")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
