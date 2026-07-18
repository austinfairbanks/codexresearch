from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from soleresearch import __version__
from soleresearch.errors import SoleResearchError
from soleresearch.evidence import EvidenceRepository, locate_excerpt, locator
from soleresearch.exporting import export_project
from soleresearch.discussions import DiscussionRepository
from soleresearch.controller import capability_paths, read_controller_capability
from soleresearch.graph import GraphRepository, new_node
from soleresearch.indexing import rebuild_index
from soleresearch.integrations import approve_adapter, generate_adapter, prepare_zotero_bundle, test_adapter
from soleresearch.migrations import migrate_project
from soleresearch.orchestration import RunRepository, validate_result_with_bundle
from soleresearch.project import initialize_project, load_project, project_status, utc_now
from soleresearch.projection import build_dashboard_projection, next_dashboard_projection, publication_status, publish_dashboard_projection
from soleresearch.schemas import SCHEMA_VERSION, tool_catalog, validate_document
from soleresearch.storage import atomic_write_json
from soleresearch.sources import (
    SourceRepository,
    import_bibtex,
    import_csl_json,
    import_identifier,
    import_local_document,
    import_url,
    load_extraction,
    render_reading_queue,
)
from soleresearch.ui import serve as serve_ui


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sole-research",
        description="Local-first evidence-to-outline research workbench",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("mcp", help="serve the complete bounded workflow over stdio MCP")
    hook = commands.add_parser("hook", help="run packaged lifecycle hook helpers")
    hook.add_argument("action", choices=("turn-complete",))
    workspace = commands.add_parser("workspace", help="select or inspect the explicit MCP workspace root")
    workspace_actions = workspace.add_subparsers(dest="action", required=True)
    workspace_select = workspace_actions.add_parser("select")
    workspace_select.add_argument("path", type=Path)
    workspace_actions.add_parser("show")

    init = commands.add_parser("init", help="atomically initialize a research project")
    init.add_argument("project", type=Path)
    init.add_argument("--name")
    init.add_argument(
        "--data-policy",
        choices=("public_only", "local_private"),
        default="public_only",
    )

    doctor = commands.add_parser("doctor", help="check the runtime and optional project")
    doctor.add_argument("project", type=Path, nargs="?")

    status = commands.add_parser("status", help="report authoritative project counts")
    status.add_argument("project", type=Path)
    status.add_argument("--run-id")

    projection = commands.add_parser("projection", help="build the bounded read-only Sites projection")
    projection.add_argument("project", type=Path)
    projection.add_argument("--thread-id", default="unknown")
    projection.add_argument("--published-revision", type=int)
    projection.add_argument("--output", type=Path)

    publish = commands.add_parser("publish", help="publish one revision to a configured Sole Research Site")
    publish.add_argument("project", type=Path)
    publish.add_argument("--site-url", required=True)
    publish.add_argument("--publisher-token-file", required=True, type=Path)
    publish.add_argument("--sites-auth-token-file", type=Path)
    publish.add_argument("--thread-id", default="unknown")

    publication = commands.add_parser("publication", help="inspect Sites publication and retry state")
    publication_actions = publication.add_subparsers(dest="action", required=True)
    publication_status_parser = publication_actions.add_parser("status")
    publication_status_parser.add_argument("project", type=Path)

    serve = commands.add_parser("serve", help="serve the local outline-first web UI")
    serve.add_argument("project", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--edit", action="store_true")
    serve.add_argument("--controller-token-file", type=Path)
    serve.add_argument("--unsafe-non-loopback", action="store_true")
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--workspace-dir", type=Path, help="show immediate child projects as independent question roots")

    export = commands.add_parser("export", help="create a deterministic audit bundle")
    export.add_argument("project", type=Path)
    export.add_argument("output", type=Path)

    rebuild = commands.add_parser("rebuild-index", help="replace the disposable SQLite index")
    rebuild.add_argument("project", type=Path)

    migrate = commands.add_parser("migrate", help="explicitly migrate a historical project contract")
    migrate.add_argument("project", type=Path)

    importer = commands.add_parser("import", help="import and normalize a research source")
    importer.add_argument("project", type=Path)
    importer.add_argument(
        "kind",
        choices=("url", "doi", "arxiv", "bibtex", "csl-json", "pdf", "markdown", "outline"),
    )
    importer.add_argument("input")
    importer.add_argument("--inspect", action="store_true", help="retrieve and inspect URL content")
    importer.add_argument("--retain-copy", action="store_true")
    importer.add_argument("--private", action="store_true")

    source = commands.add_parser("source", help="inspect or update the source ledger")
    source.add_argument("project", type=Path)
    source_actions = source.add_subparsers(dest="action", required=True)
    source_actions.add_parser("list")
    source_actions.add_parser("queue")
    source_show = source_actions.add_parser("show")
    source_show.add_argument("source_id")
    source_read = source_actions.add_parser("read")
    source_read.add_argument("source_id")
    source_read.add_argument("--state", required=True, choices=("unread", "queued", "reading", "read", "skipped"))
    source_quality = source_actions.add_parser("quality")
    source_quality.add_argument("source_id")
    quality_choices = ("unknown", "low", "medium", "high", "not_applicable")
    source_quality.add_argument("--authority", choices=quality_choices)
    source_quality.add_argument("--methodology-transparency", choices=quality_choices)
    source_quality.add_argument("--evidence-directness", choices=quality_choices)
    source_quality.add_argument("--relevance", choices=quality_choices)
    source_quality.add_argument("--publication-status", choices=("unknown", "preprint", "peer_reviewed", "institutional", "commercial", "informal", "not_applicable"))
    source_quality.add_argument("--notes")

    evidence = commands.add_parser("evidence", help="persist evidence tied to inspected content")
    evidence.add_argument("project", type=Path)
    evidence_actions = evidence.add_subparsers(dest="action", required=True)
    evidence_actions.add_parser("list")
    evidence_add = evidence_actions.add_parser("add")
    evidence_add.add_argument("--source-id", required=True)
    evidence_add.add_argument("--source-hash")
    evidence_add.add_argument("--source-version")
    evidence_add.add_argument("--locator", required=True, choices=("page", "section", "paragraph", "captured_passage", "figure", "table", "timestamp"))
    evidence_add.add_argument("--page", type=int)
    evidence_add.add_argument("--section")
    evidence_add.add_argument("--paragraph", type=int)
    evidence_add.add_argument("--figure")
    evidence_add.add_argument("--table")
    evidence_add.add_argument("--timestamp")
    evidence_add.add_argument("--start-char", type=int)
    evidence_add.add_argument("--end-char", type=int)
    evidence_add.add_argument("--label", default="")
    evidence_add.add_argument("--excerpt", required=True)
    evidence_add.add_argument("--paraphrase", required=True)
    evidence_add.add_argument("--stance", required=True, choices=("supports", "contradicts", "qualifies", "context", "uncertain"))
    evidence_add.add_argument("--actor-type", choices=("human", "agent", "import"), default="human")
    evidence_add.add_argument("--actor-id", default="cli-user")
    evidence_add.add_argument("--method", default="manual-exact-locator")
    evidence_add.add_argument("--run-id")
    evidence_add.add_argument("--task-id")

    scaffold = commands.add_parser("scaffold", help="create the first anchored research question")
    scaffold.add_argument("project", type=Path)
    scaffold.add_argument("--question", required=True)
    scaffold.add_argument("--actor-id", default="cli-user")
    scaffold.add_argument("--controller-token-file", type=Path)

    discuss = commands.add_parser("discuss", help="record entity-scoped turns and takeaways")
    discuss.add_argument("project", type=Path)
    discuss_actions = discuss.add_subparsers(dest="action", required=True)
    discuss_actions.add_parser("list")
    discuss_show = discuss_actions.add_parser("show")
    discuss_show.add_argument("--discussion-id", required=True)
    for action_name in ("add", "promote"):
        discuss_write = discuss_actions.add_parser(action_name)
        discuss_write.add_argument("--discussion-id", required=action_name == "promote")
        discuss_write.add_argument("--entity-type", required=True, choices=("node", "edge", "source", "evidence"))
        discuss_write.add_argument("--entity-id", required=True)
        discuss_write.add_argument("--content", required=True)
        discuss_write.add_argument("--actor-type", choices=("human", "agent", "orchestrator"), default="human")
        discuss_write.add_argument("--actor-id", default="cli-user")
        if action_name == "promote":
            discuss_write.add_argument("--promoted-node-id")
        else:
            discuss_write.set_defaults(promoted_node_id=None)

    graph_diff = commands.add_parser("diff", help="propose, inspect, or orchestrator-apply a graph diff")
    graph_diff.add_argument("project", type=Path)
    diff_actions = graph_diff.add_subparsers(dest="action", required=True)
    for action_name in ("list", "conflicts", "events"):
        diff_actions.add_parser(action_name)
    diff_show = diff_actions.add_parser("show")
    diff_show.add_argument("--diff-id", required=True)
    diff_propose = diff_actions.add_parser("propose")
    diff_propose.add_argument("--diff-id")
    diff_propose.add_argument("--operations", type=Path, required=True, help="JSON array of graph operations")
    diff_propose.add_argument("--base-revision", type=int)
    diff_propose.add_argument("--actor-type", choices=("human", "agent", "orchestrator"), default="agent")
    diff_propose.add_argument("--actor-id", default="cli-agent")
    diff_apply = diff_actions.add_parser("apply")
    diff_apply.add_argument("--diff-id", required=True)
    diff_apply.add_argument("--controller-token-file", type=Path, required=True)

    reconcile = commands.add_parser("reconcile", help="reconcile human Markdown edits into graph state")
    reconcile.add_argument("project", type=Path)
    reconcile.add_argument("--controller-token-file", type=Path)

    run = commands.add_parser("run", help="create and operate a bounded research run")
    run.add_argument("project", type=Path)
    run_actions = run.add_subparsers(dest="action", required=True)
    run_start = run_actions.add_parser("start")
    run_start.add_argument("--controller", choices=("human", "sol"), default="human")
    run_start.add_argument("--cycles", type=int, default=3)
    run_start.add_argument("--tasks", type=int, default=10)
    run_start.add_argument("--deep-sources", type=int, default=15)
    run_start.add_argument("--agents", type=int, default=4)
    run_start.add_argument("--max-depth", type=int, default=1)
    run_start.add_argument("--minutes", type=float, default=90)
    run_start.add_argument("--provider-unit", choices=("dollars", "tokens", "credits", "rate_limit"), default="dollars")
    run_start.add_argument("--provider-ceiling", type=float, default=5.0)
    run_start.add_argument("--git-repository", type=Path)
    run_start.add_argument("--worktree-parent", type=Path)
    run_start.add_argument("--baseline-revision")
    run_start.add_argument("--allow-dirty-baseline", action="store_true")

    run_dispatch = run_actions.add_parser("dispatch")
    run_dispatch.add_argument("--run-id", required=True)
    run_dispatch.add_argument("--controller-token-file", type=Path, required=True)
    run_dispatch.add_argument("--role", required=True)
    run_dispatch.add_argument("--subquestion", required=True)
    run_dispatch.add_argument("--evidence-strategy", required=True)
    run_dispatch.add_argument("--context", type=Path, required=True)
    run_dispatch.add_argument("--artifact-ref", action="append", default=[])
    run_dispatch.add_argument("--capability", action="append", default=[])
    run_dispatch.add_argument("--domain", action="append", default=[])
    run_dispatch.add_argument("--parent-task-id")
    run_dispatch.add_argument("--reserve-deep-sources", type=int, default=0)
    run_dispatch.add_argument("--reserve-provider-usage", type=float, default=0.0)
    run_dispatch.add_argument("--lease-minutes", type=float, default=30.0)

    def run_controller_action(name: str) -> argparse.ArgumentParser:
        action_parser = run_actions.add_parser(name)
        action_parser.add_argument("--run-id", required=True)
        action_parser.add_argument("--controller-token-file", type=Path, required=True)
        return action_parser

    run_import = run_controller_action("import-result")
    run_import.add_argument("--result", type=Path, required=True)
    run_bundle = run_controller_action("bundle-task")
    run_bundle.add_argument("--task-id", required=True)
    run_bundle.add_argument("--output", type=Path, required=True)
    run_controller_action("expire-leases")
    run_cancel = run_controller_action("cancel-task")
    run_cancel.add_argument("--task-id", required=True)
    run_cancel.add_argument("--reason", required=True)
    run_requeue = run_controller_action("requeue-task")
    run_requeue.add_argument("--task-id", required=True)
    for action_name in ("pause", "finish"):
        lifecycle = run_controller_action(action_name)
        lifecycle.add_argument("--reason", required=True)
    run_checkpoint = run_controller_action("checkpoint")
    run_checkpoint.add_argument("--path", action="append", required=True)
    run_checkpoint.add_argument("--message", required=True)

    resume = commands.add_parser("resume", help="resume a paused bounded research run")
    resume.add_argument("project", type=Path)
    resume.add_argument("run_id")
    resume.add_argument("--controller-token-file", type=Path)

    budget = commands.add_parser("budget", help="inspect or explicitly extend a run budget")
    budget.add_argument("project", type=Path)
    budget.add_argument("run_id")
    budget_actions = budget.add_subparsers(dest="action", required=True)
    budget_actions.add_parser("show")
    budget_extend = budget_actions.add_parser("extend")
    budget_extend.add_argument("--actor", required=True, choices=("human", "sol"))
    budget_extend.add_argument("--reason", required=True)
    budget_extend.add_argument("--cycles", type=int)
    budget_extend.add_argument("--tasks", type=int)
    budget_extend.add_argument("--deep-sources", type=int)
    budget_extend.add_argument("--minutes", type=float)
    budget_extend.add_argument("--provider-usage", type=float)
    budget_extend.add_argument("--controller-token-file", type=Path, required=True)

    gate = commands.add_parser("gate", help="inspect decisions, hand off control, or advance a cycle")
    gate.add_argument("project", type=Path)
    gate.add_argument("run_id")
    gate_actions = gate.add_subparsers(dest="action", required=True)
    gate_actions.add_parser("show")
    gate_resolve = gate_actions.add_parser("resolve")
    gate_resolve.add_argument("--result-id", required=True)
    gate_resolve.add_argument("--reject", action="store_true")
    gate_resolve.add_argument("--controller-token-file", type=Path, required=True)
    gate_ratify = gate_actions.add_parser("ratify")
    gate_ratify.add_argument("--diff-id", required=True)
    gate_ratify.add_argument("--controller-token-file", type=Path, required=True)
    gate_switch = gate_actions.add_parser("switch-controller")
    gate_switch.add_argument("--controller", required=True, choices=("human", "sol"))
    gate_switch.add_argument("--controller-token-file", type=Path, required=True)
    gate_advance = gate_actions.add_parser("advance-cycle")
    gate_advance.add_argument("--controller-token-file", type=Path, required=True)

    tools = commands.add_parser("tools", help="inspect harness-neutral versioned tool contracts")
    tools_actions = tools.add_subparsers(dest="action", required=True)
    tools_actions.add_parser("list")
    tools_show = tools_actions.add_parser("show")
    tools_show.add_argument("--tool-id", required=True)
    tools_validate_result = tools_actions.add_parser("validate-result")
    tools_validate_result.add_argument("--file", type=Path, required=True)
    tools_validate_result.add_argument("--bundle", type=Path)

    zotero = commands.add_parser("zotero-bundle", help="prepare a reviewed manual Zotero import bundle")
    zotero.add_argument("project", type=Path)
    zotero.add_argument("output", type=Path)

    adapter = commands.add_parser("adapter", help="generate, test, or approve an isolated harness adapter")
    adapter_actions = adapter.add_subparsers(dest="action", required=True)
    adapter_generate = adapter_actions.add_parser("generate")
    adapter_generate.add_argument("staging", type=Path)
    adapter_generate.add_argument("--harness", required=True)
    adapter_test = adapter_actions.add_parser("test")
    adapter_test.add_argument("staging", type=Path)
    adapter_approve = adapter_actions.add_parser("approve")
    adapter_approve.add_argument("staging", type=Path)
    adapter_approve.add_argument("--project", type=Path, required=True)
    adapter_approve.add_argument("--reason", required=True)
    adapter_approve.add_argument("--controller-token-file", type=Path, required=True)
    return parser


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SoleResearchError(f"cannot load {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SoleResearchError(f"{label} JSON must contain an object")
    return value


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "start":
        envelope = {
            "cycles": args.cycles, "tasks": args.tasks, "deep_sources": args.deep_sources,
            "agents": args.agents, "max_depth": args.max_depth, "minutes": args.minutes,
            "provider_usage": {"unit": args.provider_unit, "ceiling": args.provider_ceiling},
        }
        repository = RunRepository.create(
            args.project, controller=args.controller, envelope=envelope,
            git_repository=args.git_repository, worktree_parent=args.worktree_parent,
            baseline_revision=args.baseline_revision, allow_dirty_baseline=args.allow_dirty_baseline,
        )
        return repository.status()
    repository = RunRepository(args.project, _require(args.run_id, "--run-id"))
    token = read_controller_capability(args.controller_token_file)
    if args.action == "dispatch":
        context = {} if args.context is None else _load_object(args.context, "context")
        return repository.dispatch(
            role=_require(args.role, "--role"), subquestion=_require(args.subquestion, "--subquestion"),
            evidence_strategy=_require(args.evidence_strategy, "--evidence-strategy"),
            selected_context=context, artifact_references=args.artifact_ref,
            allowed_capabilities=args.capability, allowed_domains=args.domain,
            parent_task_id=args.parent_task_id, controller_token=token,
            reserve_deep_sources=args.reserve_deep_sources,
            reserve_provider_usage=args.reserve_provider_usage,
            lease_minutes=args.lease_minutes,
        )
    if args.action == "import-result":
        return repository.import_result(_load_object(_require(args.result, "--result"), "result"), controller_token=token)
    if args.action == "bundle-task":
        output = _require(args.output, "--output")
        if output.exists():
            raise SoleResearchError("worker bundle output already exists; immutable bundles are never overwritten")
        bundle = repository.worker_bundle(
            _require(args.task_id, "--task-id"), controller_token=token
        )
        atomic_write_json(output, bundle)
        return {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": bundle["bundle_id"],
            "task_id": bundle["task"]["task_id"],
            "output": str(output),
        }
    if args.action == "expire-leases":
        return repository.expire_leases(controller_token=token)
    if args.action == "cancel-task":
        return repository.cancel_task(_require(args.task_id, "--task-id"), _require(args.reason, "--reason"), controller_token=token)
    if args.action == "requeue-task":
        return repository.requeue_task(_require(args.task_id, "--task-id"), controller_token=token)
    if args.action == "pause":
        return repository.pause(_require(args.reason, "--reason"), controller_token=token)
    if args.action == "finish":
        return repository.finish(_require(args.reason, "--reason"), controller_token=token)
    return repository.checkpoint(args.path, _require(args.message, "--message"), controller_token=token)


def _budget(args: argparse.Namespace) -> dict[str, Any]:
    repository = RunRepository(args.project, args.run_id)
    if args.action == "show":
        status = repository.status()
        return {"schema_version": SCHEMA_VERSION, "run_id": args.run_id, "budget": status["budget"], "remaining": status["remaining"]}
    delta = {
        key: value for key, value in {
            "cycles": args.cycles, "tasks": args.tasks, "deep_sources": args.deep_sources,
            "minutes": args.minutes, "provider_usage": args.provider_usage,
        }.items() if value is not None
    }
    return repository.extend(
        delta, actor=_require(args.actor, "--actor"), reason=_require(args.reason, "--reason"),
        controller_token=read_controller_capability(args.controller_token_file),
    )


def _gate(args: argparse.Namespace) -> dict[str, Any]:
    repository = RunRepository(args.project, args.run_id)
    if args.action == "show":
        status = repository.status()
        return {"schema_version": SCHEMA_VERSION, "run_id": args.run_id, "gate": status["gate"]}
    token = read_controller_capability(args.controller_token_file)
    if args.action == "resolve":
        return repository.resolve_result(_require(args.result_id, "--result-id"), accept=not args.reject, controller_token=token)
    if args.action == "ratify":
        return repository.ratify(_require(args.diff_id, "--diff-id"), controller_token=token)
    if args.action == "switch-controller":
        return repository.switch_controller(_require(args.controller, "--controller"), controller_token=token)
    return repository.advance_cycle(controller_token=token)


def _require(value: Any, name: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SoleResearchError(f"{name} is required")
    return value


def _tools(args: argparse.Namespace) -> dict[str, Any]:
    catalog = tool_catalog()
    if args.action == "list":
        return catalog
    if args.action == "validate-result":
        path = _require(args.file, "--file")
        result = _load_object(path, "result")
        validate_document("result", result)
        response = {
            "schema_version": SCHEMA_VERSION,
            "kind": "result",
            "valid": True,
            "file": str(path),
        }
        if args.bundle is not None:
            bundle = _load_object(args.bundle, "worker bundle")
            receipt_time = utc_now()
            validate_result_with_bundle(result, bundle, receipt_time=receipt_time)
            response.update({
                "bundle_id": bundle["bundle_id"],
                "task_id": bundle["task"]["task_id"],
                "receipt_time": receipt_time,
                "broker_admission": False,
            })
        return response
    identity = _require(args.tool_id, "--tool-id")
    match = next((item for item in catalog["tools"] if item["id"] == identity), None)
    if match is None:
        raise SoleResearchError(f"unknown tool_id: {identity}")
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": catalog["catalog_version"],
        "tool": match,
    }


def _adapter(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "generate":
        return generate_adapter(args.staging, harness=_require(args.harness, "--harness"))
    if args.action == "test":
        return test_adapter(args.staging)
    return approve_adapter(
        args.staging,
        project_path=_require(args.project, "--project"),
        controller_token=read_controller_capability(args.controller_token_file),
        reason=_require(args.reason, "--reason"),
    )


def _import(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "url":
        record, created = import_url(
            args.project,
            args.input,
            inspect=args.inspect,
            retain_copy=args.retain_copy,
        )
        records = [record]
    elif args.kind in {"doi", "arxiv"}:
        record, created = import_identifier(args.project, args.input, kind=args.kind)
        records = [record]
    elif args.kind == "bibtex":
        records = import_bibtex(args.project, Path(args.input))
        created = None
    elif args.kind == "csl-json":
        records = import_csl_json(args.project, Path(args.input))
        created = None
    else:
        record, created = import_local_document(
            args.project,
            Path(args.input),
            kind=args.kind,
            private=args.private,
            retain_copy=args.retain_copy,
        )
        records = [record]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_ids": [item["source_id"] for item in records],
        "sources": len(records),
        "created": created,
    }


def _source(args: argparse.Namespace) -> dict[str, Any]:
    repository = SourceRepository(args.project)
    if args.action == "list":
        records = repository.all()
        return {"schema_version": SCHEMA_VERSION, "sources": records}
    if args.action == "show":
        return repository.get(_require(args.source_id, "source_id"))
    if args.action == "queue":
        records = render_reading_queue(args.project)
        return {"schema_version": SCHEMA_VERSION, "source_ids": [item["source_id"] for item in records]}
    if args.action == "read":
        return repository.set_reading_state(
            _require(args.source_id, "source_id"),
            _require(args.state, "--state"),
        )
    dimensions = {
        "authority": args.authority,
        "methodology_transparency": args.methodology_transparency,
        "evidence_directness": args.evidence_directness,
        "relevance": args.relevance,
        "publication_status": args.publication_status,
        "notes": args.notes,
    }
    updates = {key: value for key, value in dimensions.items() if value is not None}
    if not updates:
        raise SoleResearchError("source quality requires at least one dimension")
    return repository.set_quality(_require(args.source_id, "source_id"), **updates)


def _evidence(args: argparse.Namespace) -> dict[str, Any]:
    repository = EvidenceRepository(args.project)
    if args.action == "list":
        return {"schema_version": SCHEMA_VERSION, "evidence": repository.all()}
    source_id = _require(args.source_id, "--source-id")
    excerpt = _require(args.excerpt, "--excerpt")
    kind = _require(args.locator, "--locator")
    if kind == "captured_passage" and args.start_char is None and args.end_char is None:
        exact_locator = locate_excerpt(
            load_extraction(args.project, source_id, args.source_hash, args.source_version), excerpt
        )
    else:
        exact_locator = locator(
            kind,
            page=args.page,
            section=args.section,
            paragraph=args.paragraph,
            figure=args.figure,
            table=args.table,
            timestamp=args.timestamp,
            start_char=args.start_char,
            end_char=args.end_char,
            label=args.label,
        )
    record, created = repository.add(
        source_id=source_id,
        locator=exact_locator,
        excerpt=excerpt,
        paraphrase=_require(args.paraphrase, "--paraphrase"),
        stance=_require(args.stance, "--stance"),
        actor_type=args.actor_type,
        actor_id=args.actor_id,
        method=args.method,
        run_id=args.run_id,
        task_id=args.task_id,
        source_hash=args.source_hash,
        source_version=args.source_version,
    )
    return {"schema_version": SCHEMA_VERSION, "evidence_id": record["evidence_id"], "created": created}


def _doctor(project: Path | None) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "python_3_12_or_newer": sys.version_info >= (3, 12),
    }
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "checks": checks}
    if project is not None:
        document = load_project(project)
        checks["project_valid"] = True
        result["project_id"] = document["project_id"]
    result["ok"] = all(checks.values())
    return result


def _scaffold(args: argparse.Namespace) -> dict[str, Any]:
    repository = GraphRepository(args.project)
    node = new_node("question", args.question, authority="human_accepted")
    diff = repository.propose(
        [{"op": "add", "target": "node", "record": node}],
        actor_type="human",
        actor_id=args.actor_id,
    )
    applied = repository.apply(
        diff["diff_id"],
        controller_token=read_controller_capability(args.controller_token_file),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": node["node_id"],
        "diff_id": applied["diff_id"],
        "revision": applied["applied_revision"],
    }


def _discuss(args: argparse.Namespace) -> dict[str, Any]:
    repository = DiscussionRepository(args.project)
    if args.action == "list":
        return {"schema_version": SCHEMA_VERSION, "discussions": repository.list()}
    if args.action == "show":
        return {
            "schema_version": SCHEMA_VERSION,
            "entries": repository.all(_require(args.discussion_id, "--discussion-id")),
        }
    record = repository.add(
        entity_type=_require(args.entity_type, "--entity-type"),
        entity_id=_require(args.entity_id, "--entity-id"),
        content=_require(args.content, "--content"),
        actor_type=args.actor_type,
        actor_id=args.actor_id,
        discussion_id=args.discussion_id,
        entry_type="promoted_takeaway" if args.action == "promote" else "turn",
        promoted_node_id=args.promoted_node_id,
    )
    return record


def _diff(args: argparse.Namespace) -> dict[str, Any]:
    repository = GraphRepository(args.project)
    if args.action == "list":
        return {"schema_version": SCHEMA_VERSION, "diffs": repository.diffs()}
    if args.action == "conflicts":
        return {"schema_version": SCHEMA_VERSION, "conflicts": repository.conflicts()}
    if args.action == "events":
        return {"schema_version": SCHEMA_VERSION, "reconciliation_events": repository.reconciliation_events()}
    if args.action == "show":
        identity = _require(args.diff_id, "--diff-id")
        match = next((item for item in repository.diffs() if item["diff_id"] == identity), None)
        if match is None:
            raise SoleResearchError(f"unknown graph diff: {identity}")
        return match
    if args.action == "apply":
        return repository.apply(
            _require(args.diff_id, "--diff-id"),
            controller_token=read_controller_capability(args.controller_token_file),
        )
    operations_path = _require(args.operations, "--operations")
    try:
        operations = json.loads(operations_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SoleResearchError(f"cannot load operations JSON: {exc}") from exc
    if not isinstance(operations, list):
        raise SoleResearchError("operations JSON must contain an array")
    return repository.propose(
        operations,
        base_revision=args.base_revision,
        actor_type=args.actor_type,
        actor_id=args.actor_id,
        diff_id=args.diff_id,
    )


def _emit(value: Any, *, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            document = initialize_project(
                args.project,
                name=args.name,
                data_policy=args.data_policy,
            )
            result = {
                "schema_version": SCHEMA_VERSION,
                "project": str(args.project.resolve()),
                "project_id": document["project_id"],
                "controller_capability_paths": {
                    name: str(path) for name, path in capability_paths(document["project_id"]).items()
                },
            }
        elif args.command == "mcp":
            from soleresearch.mcp_server import serve_mcp

            return serve_mcp(_parser)
        elif args.command == "hook":
            from soleresearch.refresh import MAX_HOOK_INPUT_BYTES, notify_turn_complete

            try:
                notify_turn_complete(sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1))
            except Exception:
                pass
            sys.stdout.write('{"continue":true}\n')
            return 0
        elif args.command == "workspace":
            from soleresearch.mcp_server import select_workspace_root, selected_workspace_root

            if args.action == "select":
                result = select_workspace_root(args.path)
            else:
                selected = selected_workspace_root()
                result = {"schema_version": SCHEMA_VERSION, "workspace_root": None if selected is None else str(selected)}
        elif args.command == "doctor":
            result = _doctor(args.project)
            if not result["ok"]:
                _emit(result)
                return 1
        elif args.command == "status":
            result = RunRepository(args.project, args.run_id).status() if args.run_id else project_status(args.project)
        elif args.command == "projection":
            result = (
                build_dashboard_projection(
                    args.project,
                    published_revision=args.published_revision,
                    thread_id=args.thread_id,
                )
                if args.published_revision is not None
                else next_dashboard_projection(args.project, thread_id=args.thread_id)
            )
            if args.output is not None:
                atomic_write_json(args.output, result)
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "output": str(args.output.resolve()),
                    "project_id": result["project_id"],
                    "project_revision": result["project_revision"],
                    "published_revision": result["published_revision"],
                }
        elif args.command == "publish":
            result = publish_dashboard_projection(
                args.project,
                site_url=args.site_url,
                publisher_token_file=args.publisher_token_file,
                sites_auth_token_file=args.sites_auth_token_file,
                thread_id=args.thread_id,
            )
        elif args.command == "publication":
            result = publication_status(args.project)
        elif args.command == "serve":
            token = read_controller_capability(args.controller_token_file) if args.edit else None
            serve_ui(
                args.project,
                host=args.host,
                port=args.port,
                controller_token=token,
                edit=args.edit,
                unsafe_non_loopback=args.unsafe_non_loopback,
                allowed_hosts=tuple(args.allowed_host),
                workspace_root=args.workspace_dir,
            )
            return 0
        elif args.command == "export":
            result = export_project(args.project, args.output)
        elif args.command == "rebuild-index":
            result = rebuild_index(args.project)
        elif args.command == "migrate":
            result = migrate_project(args.project)
        elif args.command == "import":
            result = _import(args)
        elif args.command == "source":
            result = _source(args)
        elif args.command == "evidence":
            result = _evidence(args)
        elif args.command == "scaffold":
            result = _scaffold(args)
        elif args.command == "discuss":
            result = _discuss(args)
        elif args.command == "diff":
            result = _diff(args)
        elif args.command == "reconcile":
            result = GraphRepository(args.project).reconcile(
                controller_token=read_controller_capability(args.controller_token_file),
            )
        elif args.command == "run":
            result = _run(args)
        elif args.command == "resume":
            result = RunRepository(args.project, args.run_id).resume(
                controller_token=read_controller_capability(args.controller_token_file)
            )
        elif args.command == "budget":
            result = _budget(args)
        elif args.command == "gate":
            result = _gate(args)
        elif args.command == "tools":
            result = _tools(args)
        elif args.command == "zotero-bundle":
            result = prepare_zotero_bundle(args.project, args.output)
        elif args.command == "adapter":
            result = _adapter(args)
        else:  # pragma: no cover - argparse enforces the command set.
            raise AssertionError(args.command)
        _emit(result)
        return 0
    except (SoleResearchError, OSError) as exc:
        _emit(
            {"schema_version": SCHEMA_VERSION, "error": str(exc), "command": args.command},
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
